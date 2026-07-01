"""Airbag-Bench harness — replay the REAL decision seam over a fixture-driven backend.

Design (reviewed adversarially before build — see the workflow review):
  * Replays the real ``state_machine.run_self_heal`` so the bench exercises the actual
    ``analyzer.analyze`` -> ``_heuristic`` -> ``_validate`` -> autonomy seam V3 modifies — NOT a
    reimplementation. The LLM is forced OFF, so the deterministic ``_heuristic`` floor plays the
    decider's role (reproducible, no API key, CI-able). This scores the v2 DETERMINISTIC FLOOR, a
    LOWER BOUND on the live Gemini path — stated plainly in the scorecard + docs/AIRBAG_BENCH.md.
  * ONE FixtureBackend instance per case (NOT one-per-get_backend-call): ``get_backend()`` is called
    many times inside a single run (triage, rollback, _verify, _mitigate), so post-rollback state
    must persist across those calls — exactly why backends/mock.py holds module-level state.
  * decided_action is read from the DECISION event (the gate's verdict), independent of the
    subsequent mitigation OUTCOME — so a case that decides ROLLBACK then fails _verify (a coincident
    dependency outage) still counts as a (false) rollback, not as an escalation.
"""
from __future__ import annotations

from dataclasses import dataclass

from autosre import config, state_store, tools
from autosre.state_machine import run_self_heal


class FixtureBackend:
    """A backend whose observations come from one BenchCase ``world``. Holds post-rollback state on
    the instance, so a single instance must back an entire ``run_self_heal`` (see harness note)."""

    def __init__(self, world: dict):
        self.world = world
        self.rolled_back = False

    # --- read signals --------------------------------------------------------------------------
    def list_cloud_run_revisions(self, service: str, region: str) -> dict:
        return {"service": service, "revisions": list(self.world["revisions"])}

    def query_error_rate(self, service: str, region: str, window_minutes: int = 5,
                         since_epoch: float | None = None) -> dict:
        cleared = self.rolled_back and self.world.get("rollback_clears", True)
        rate = 0.0 if cleared else float(self.world["error_rate"])
        total = int(self.world.get("sample", {}).get("total", 200)) or 200
        return {"service": service, "error_rate": rate, "total_requests": total,
                "window_minutes": window_minutes}

    def fetch_error_logs(self, service: str, region: str, n: int = 10) -> list[str]:
        return list(self.world.get("logs", []))[:n]

    def sample_latency_windows(self, service: str, region: str, windows: int = 4) -> list[dict]:
        # per-window {slow, total} from the fixture; default benign (no slow requests) so non-latency
        # cases are unaffected when the latency detector is enabled.
        w = self.world.get("latency_windows")
        if w is None:
            return [{"slow": 0, "total": 20} for _ in range(windows)]
        return [dict(x) for x in w]

    def sample_business_path(self, service: str, region: str, n: int = 20) -> dict:
        # the pinned observed sample (active probe at triage time); post-rollback it would read clean
        if self.rolled_back and self.world.get("rollback_clears", True):
            return {"errs": 0, "total": int(self.world.get("sample", {}).get("total", n))}
        s = self.world["sample"]
        return {"errs": int(s["errs"]), "total": int(s["total"])}

    def synthetic_probe(self, service: str, path: str = "/healthz") -> dict:
        ok = self.rolled_back and self.world.get("rollback_clears", True)
        return {"ok": ok, "path": path, "status": 200 if ok else 503}

    def probe_candidate(self, service: str, region: str, revision: str) -> dict:
        ok = self.rolled_back and self.world.get("rollback_clears", True)
        return {"ok": ok, "errors": 0 if ok else 1, "total": 1}

    # --- act -----------------------------------------------------------------------------------
    def rollback_traffic_to_revision(self, service: str, region: str, revision: str) -> dict:
        self.rolled_back = True
        return {"status": "success", "service": service, "active_revision": revision}

    def set_traffic_split(self, service: str, region: str, splits: dict,
                         tag_revision: str | None = None) -> dict:
        self.rolled_back = True
        return {"status": "success", "service": service, "traffic": dict(splits)}

    def break_target(self, service: str, region: str) -> dict:
        self.rolled_back = False
        return {"status": "success", "service": service}

    def reset_target(self, service: str, region: str) -> dict:
        self.rolled_back = True
        return {"status": "success", "service": service}

    def __getattr__(self, name: str):
        # Let dunder probes (copy/deepcopy/pickle) fall through as a normal missing attribute, so
        # FixtureBackend stays copy-safe; only a real missing BACKEND method fails loudly.
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        # Forward-compat: if Phase 1 adds new backend collectors (query_latency / query_saturation)
        # to the action layer, fail LOUDLY with a clear message instead of an opaque AttributeError.
        raise NotImplementedError(
            f"FixtureBackend has no backend method {name!r} — add it (reading world[...]) when "
            f"Phase 1 introduces this collector, so the bench keeps replaying the real seam.")


@dataclass(frozen=True)
class CaseResult:
    name: str
    category: str
    expected_action: str
    is_bad_deploy: bool
    decided_action: str          # from the DECISION event — the gate's verdict
    status: str                  # terminal run_self_heal status (mitigated/escalated/noop/...)
    stages: int                  # number of emitted events (mean-stages metric uses status==mitigated)
    cleared: bool                # did the (simulated) rollback actually clear the errors?


# config attributes we pin so the bench is deterministic + offline under BOTH pytest and the CLI
_PINNED = {
    "GEMINI_API_KEY": "",        # force the deterministic _heuristic floor (no live LLM/ADK/Gemini)
    "STATE_BACKEND": "memory",   # durable store in-memory + reset per case
    "EVENTS_BACKEND": "inproc",  # no Pub/Sub fan-out attempts
    "QUEUE_BACKEND": "inproc",
    "AUTONOMY_LEVEL": "L3",      # full autonomy: a ROLLBACK decision flows straight to mitigation
    "STAT_GATE_ENABLED": True,   # the v2 Wilson gate is part of the seam under test
    "SIGNALS": "5xx",            # PINNED so the baseline is deterministic + env-independent; run_bench
                                 # overrides it to exercise the multi-signal path explicitly.
    "VERIFY_ATTEMPTS": 2,        # keep the verify loop short
    "VERIFY_INTERVAL_S": 0.0,    # no wall-clock sleeps
    "CI_SELF_CORRECT": False,    # no background CI-watch thread
    "GITHUB_TOKEN": "",          # FIX_PR slow path is a no-op note
    "GITHUB_REPO": "",
}


SERVICE = "airbag-target"   # the service the corpus revisions belong to (run_self_heal target)


def _decided_action(events: list[dict]) -> str:
    for ev in events:
        if ev.get("stage") == "DECISION":
            return ev.get("action", "OBSERVE")
    return "OBSERVE"   # no DECISION emitted (shouldn't happen) -> treat as a no-op


def run_case(case, signals: str | None = None) -> CaseResult:
    """Replay one fixture through the real run_self_heal. Self-contained: snapshots + restores the
    pinned config and the patched backend resolver, so it's safe under pytest and the CLI alike.
    `signals` overrides AIRBAG_SIGNALS (default the pinned '5xx') to exercise the multi-signal path."""
    saved_cfg = {k: getattr(config, k) for k in _PINNED}
    saved_get_backend = tools.get_backend
    fb = FixtureBackend(case.world)
    try:
        for k, v in _PINNED.items():
            setattr(config, k, v)
        if signals is not None:
            config.SIGNALS = signals
        tools.get_backend = lambda: fb            # tools binds the NAME at import -> patch on tools
        state_store.reset_memory()                # isolate durable state per case
        # sanity: per-case isolation is load-bearing (memory.observe_healthy folds samples keyed on
        # the SERVICE) — assert the learned baseline for the real service starts at the config
        # default after the reset, so a prior case can't poison this one.
        from autosre import memory
        assert memory.baseline_for(SERVICE) == config.STAT_BASELINE_RATE, "state bled across cases"
        result = run_self_heal(f"bench-{case.name}", SERVICE)
    finally:
        tools.get_backend = saved_get_backend
        for k, v in saved_cfg.items():
            setattr(config, k, v)
    events = result.get("events", [])
    return CaseResult(
        name=case.name, category=case.category, expected_action=case.expected_action,
        is_bad_deploy=case.is_bad_deploy, decided_action=_decided_action(events),
        status=result.get("status", "unknown"), stages=len(events),
        cleared=bool(case.world.get("rollback_clears", True)))


def run_bench(cases=None, signals: str | None = None) -> list[CaseResult]:
    from bench.fixtures import CASES
    cases = CASES if cases is None else cases
    return [run_case(c, signals=signals) for c in cases]
