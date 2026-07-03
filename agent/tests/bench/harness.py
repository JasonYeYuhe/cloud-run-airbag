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
    the instance, so a single instance must back an entire ``run_self_heal`` (see harness note).

    v4 (target-correctness) per-revision fields, all optional so existing worlds are unchanged:
      * ``clears_on``: [revision, …] — the rollback clears ONLY when traffic landed on one of these
        (a bad→bad world clears on the witnessed-good revision, not the landmine). When absent, the
        original ``rollback_clears`` bool applies to any rollback.
      * ``target_probes``: {revision: {errs,total}} — PER-REVISION causal-probe results (the
        landmine probes degraded, the good one clean). Falls back to ``target_probe`` (one result
        for any revision), then to the healthy default."""

    def __init__(self, world: dict):
        self.world = world
        self.rolled_back = False
        self.rolled_to: str | None = None   # the revision the last shift landed on

    def _cleared(self) -> bool:
        """Did the (simulated) rollback actually fix the world? Per-revision when clears_on is
        given; else the original whole-world rollback_clears bool."""
        if not self.rolled_back:
            return False
        clears_on = self.world.get("clears_on")
        if clears_on is not None:
            return self.rolled_to in clears_on
        return self.world.get("rollback_clears", True)

    # --- read signals --------------------------------------------------------------------------
    def list_cloud_run_revisions(self, service: str, region: str) -> dict:
        return {"service": service, "revisions": list(self.world["revisions"])}

    def query_error_rate(self, service: str, region: str, window_minutes: int = 5,
                         since_epoch: float | None = None) -> dict:
        rate = 0.0 if self._cleared() else float(self.world["error_rate"])
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

    def sample_error_windows(self, service: str, region: str, windows: int = 6,
                             per_window: int = 50) -> list[dict]:
        # v5 5.1 burn-rate pooling: per-window {errs, total} from the fixture; default benign (no burn)
        # so non-burn cases are unaffected when the burn detector is enabled. Cleared post-rollback.
        if self._cleared():
            return [{"errs": 0, "total": per_window} for _ in range(windows)]
        w = self.world.get("error_windows")
        if w is None:
            return [{"errs": 0, "total": per_window} for _ in range(windows)]
        return [dict(x) for x in w]

    def sample_business_path(self, service: str, region: str, n: int = 20) -> dict:
        # the pinned observed sample (active probe at triage time); post-rollback it would read clean
        if self._cleared():
            return {"errs": 0, "total": int(self.world.get("sample", {}).get("total", n))}
        s = self.world["sample"]
        return {"errs": int(s["errs"]), "total": int(s["total"])}

    def synthetic_probe(self, service: str, path: str = "/healthz") -> dict:
        ok = self._cleared()
        return {"ok": ok, "path": path, "status": 200 if ok else 503}

    def probe_revision_health(self, service: str, region: str, revision: str, n: int = 8) -> dict:
        # the fixture's probe MODEL {errs,total,slow} — the causal Wilson math runs for real
        # in-bench (slow feeds the v4 latency axis; omitted = fast target). PER-REVISION first
        # (target_probes — a bad→bad world's landmine probes degraded while the witnessed-good
        # probes clean), then the whole-world target_probe (an external cause breaks EVERY
        # revision), then the healthy default (a bad DEPLOY's last-good target is fine).
        p = self.world.get("target_probes", {}).get(revision) or self.world.get("target_probe")
        if p is None:
            return {"errs": 0, "total": n, "slow": 0}
        return {"errs": int(p["errs"]), "total": int(p["total"]), "slow": int(p.get("slow", 0))}

    def probe_candidate(self, service: str, region: str, revision: str) -> dict:
        ok = self._cleared()
        return {"ok": ok, "errors": 0 if ok else 1, "total": 1}

    # --- act -----------------------------------------------------------------------------------
    def rollback_traffic_to_revision(self, service: str, region: str, revision: str) -> dict:
        self.rolled_back = True
        self.rolled_to = revision
        return {"status": "success", "service": service, "active_revision": revision}

    def set_traffic_split(self, service: str, region: str, splits: dict,
                         tag_revision: str | None = None) -> dict:
        # The heal path under bench replay never splits traffic (rollback is a 100% flip); the
        # fixture world has no per-split error model, so a partial canary would read the WHOLE
        # world's error rate and mislead — fail loudly if a future phase exercises it here.
        if splits and max(int(p) for p in splits.values()) < 100:
            raise NotImplementedError(
                "FixtureBackend does not simulate partial traffic splits — extend the world model "
                "(per-split error rates) before benching a canary path.")
        self.rolled_back = True
        self.rolled_to = max(splits, key=splits.get) if splits else self.rolled_to
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
    decided_action: str          # from the DECISION event — the gate's verdict (what Airbag DECIDED)
    final_action: str            # what Airbag ULTIMATELY did (ROLLBACK iff traffic actually shifted) —
                                 # scoring keys off this so a causal pre-check ESCALATE is observable
    rolled_back: bool            # was ROLLBACK_APPLIED emitted (traffic actually shifted)?
    status: str                  # terminal run_self_heal status (mitigated/escalated/noop/...)
    expected_target: str | None  # ground-truth rollback target (None when the case pins no target)
    chosen_target: str | None    # the DECISION's rollback_revision — WHICH revision Airbag aimed at
    target_source: str | None    # 'ledger' | 'recency' (deterministic selectors) | None (no rollback)
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
    "CAUSAL_CHECK_ENABLED": False,  # PINNED off for the baseline; run_bench(causal=True) exercises it.
    "REVERSIBILITY_GUARD_ENABLED": False,  # PINNED off (default posture); run_case(reversibility=True)
                                           # exercises the guard on the dedicated fixtures.
    "TARGET_EVIDENCE": False,   # v5 3.1 PINNED off so the corpus baseline is hermetic (a stray
                                # AIRBAG_TARGET_EVIDENCE=1 must not add BLIND_LANDING stages/drift).
    "VERIFY_ATTEMPTS": 2,        # keep the verify loop short
    "VERIFY_INTERVAL_S": 0.0,    # no wall-clock sleeps
    "CI_SELF_CORRECT": False,    # no background CI-watch thread
    "GITHUB_TOKEN": "",          # FIX_PR slow path is a no-op note
    "GITHUB_REPO": "",
}


SERVICE = "airbag-target"   # the service the corpus revisions belong to (run_self_heal target)


def _decision_event(events: list[dict]) -> dict:
    return next((ev for ev in events if ev.get("stage") == "DECISION"), {})


def run_case(case, signals: str | None = None, causal: bool = False,
             reversibility: bool = False) -> CaseResult:
    """Replay one fixture through the real run_self_heal. Self-contained: snapshots + restores the
    pinned config and the patched backend resolver, so it's safe under pytest and the CLI alike.
    `signals` overrides AIRBAG_SIGNALS (default the pinned '5xx'); `causal` enables the causal
    pre-check (AIRBAG_CAUSAL_CHECK, default off) to exercise the precision path; `reversibility`
    enables the v4 irreversible-deploy guard for its dedicated fixtures."""
    saved_cfg = {k: getattr(config, k) for k in _PINNED}
    saved_get_backend = tools.get_backend
    fb = FixtureBackend(case.world)
    try:
        for k, v in _PINNED.items():
            setattr(config, k, v)
        if signals is not None:
            config.SIGNALS = signals
        if causal:
            config.CAUSAL_CHECK_ENABLED = True
        if reversibility:
            config.REVERSIBILITY_GUARD_ENABLED = True
        tools.get_backend = lambda: fb            # tools binds the NAME at import -> patch on tools
        state_store.reset_memory()                # isolate durable state per case
        # sanity: per-case isolation is load-bearing (memory.observe_healthy folds samples keyed on
        # the SERVICE) — assert the learned baseline for the real service starts at the config
        # default after the reset, so a prior case can't poison this one.
        from autosre import memory
        assert memory.baseline_for(SERVICE) == config.STAT_BASELINE_RATE, "state bled across cases"
        # v4: seed the serving-history ledger from the fixture (the revisions Airbag is said to
        # have witnessed serving healthily BEFORE this incident). Omitted = cold start (recency).
        for rev in case.world.get("witnessed", []):
            memory.witness_serving(SERVICE, rev)
        result = run_self_heal(f"bench-{case.name}", SERVICE)
    finally:
        tools.get_backend = saved_get_backend
        for k, v in saved_cfg.items():
            setattr(config, k, v)
    events = result.get("events", [])
    status = result.get("status", "unknown")
    rolled_back = any(e.get("stage") == "ROLLBACK_APPLIED" for e in events)
    decision = _decision_event(events)
    decided = decision.get("action", "OBSERVE")   # no DECISION emitted -> treat as a no-op
    # what Airbag ULTIMATELY did: a rollback iff traffic actually shifted (so a causal pre-check that
    # escalates BEFORE the shift is scored ESCALATE, not ROLLBACK); else the terminal outcome.
    if rolled_back:
        final = "ROLLBACK"
    elif status == "escalated":
        final = "ESCALATE"
    elif status in ("noop", "observed"):
        final = "OBSERVE"
    else:
        final = decided
    return CaseResult(
        name=case.name, category=case.category, expected_action=case.expected_action,
        is_bad_deploy=case.is_bad_deploy, decided_action=decided, final_action=final,
        rolled_back=rolled_back, status=status, stages=len(events),
        cleared=fb._cleared(),   # the world's ACTUAL end state (per-revision worlds: depends on
                                 # WHICH revision traffic landed on, not just whether it shifted)
        expected_target=getattr(case, "expected_target", None),
        chosen_target=decision.get("rollback_revision") if decided == "ROLLBACK" else None,
        target_source=decision.get("_target_source") if decided == "ROLLBACK" else None)


def run_bench(cases=None, signals: str | None = None, causal: bool = False) -> list[CaseResult]:
    from bench.fixtures import CASES
    cases = CASES if cases is None else cases
    return [run_case(c, signals=signals, causal=causal) for c in cases]
