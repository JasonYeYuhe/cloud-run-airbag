"""Airbag STORM scorecard (v5 Phase 2) — the committable proof of storm-safe autonomy.

A SCENARIO layer over the bench harness. A scenario replays the 2026-07-02 storm SHAPE: N alert
deliveries (distinct Cloud Monitoring incident ids, ONE broken service) driving the REAL
``state_machine.run_self_heal`` seam against a ``StormBackend`` that models probe-feedback — Airbag's
OWN triage probes hit the broken service and their 5xx land in the log-based detection COUNT UNLESS
the self-traffic exclusion (v5 1.2) filters the probe UA. SEQUENTIALLY scripted, so it is fully
deterministic (no thread-race flake). HONEST FRAMING (Gemini review): this measures the storm's
OUTCOME SHAPE on a deterministic replay; the CONCURRENT transactional safety (N simultaneous
deliveries -> exactly one leader) is proven SEPARATELY by the threaded lease-contention tests in
tests/test_state_store.py — both proofs together are the exit criterion.

Metrics per outage:
  * heals_per_outage                  — full heal runs (ran triage), NOT coalesced attaches
  * approval_cards_per_outage         — distinct operator approval cards the storm created
  * self_traffic_counted_in_detection — Airbag's OWN probe 5xx that a detection COUNT included
  * unattended_terminal_states        — redundant pending operator items (the silent pile-up)

Committed for BOTH flag states: flag-off (the honest 2026-07-02 shape) and flag-on (storm-safe),
pre-registered + CI-ratcheted, exactly the AIRBAG_BENCH.md pattern.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from autosre import autonomy, config, incidents, state_store, tools
from autosre.state_machine import run_self_heal

SERVICE = "airbag-target"
_BAD = f"{SERVICE}-00002-bad"
_GOOD = f"{SERVICE}-00001-good"


class StormBackend:
    """Models ONE broken service (a bad revision serving 5xx, a witnessed-good rollback target) and
    the observer-effect feedback loop: Airbag's own triage probes hit the broken service, and their
    5xx land in the log-based detection COUNT (query_error_rate) unless SELF_TRAFFIC_EXCLUDE filters
    the probe UA. State persists across the whole storm (one instance backs every run_self_heal)."""

    def __init__(self):
        self.rolled_back = False
        self.rolled_to: str | None = None
        self._probe_5xx_pending = 0        # Airbag probe 5xx generated since the last detection window
        self.self_5xx_in_detection = 0     # cumulative: probe 5xx a detection COUNT actually included

    # --- read signals -------------------------------------------------------------------------
    def list_cloud_run_revisions(self, service, region):
        return {"service": service, "revisions": [
            {"name": _BAD, "ready": True, "irreversible": False,
             "traffic_percent": 0 if self.rolled_back else 100, "create_time": "2026-07-02T00:00:00Z"},
            {"name": _GOOD, "ready": True, "irreversible": False,
             "traffic_percent": 100 if self.rolled_back else 0, "create_time": "2026-07-01T00:00:00Z"},
        ]}

    def query_error_rate(self, service, region, window_minutes=5, since_epoch=None):
        # The log-scan detection. Real USER 5xx from the broken service, PLUS Airbag's own probe 5xx
        # that landed in the logs since the last window — UNLESS the self-traffic exclusion filters the
        # probe UA (v5 1.2). This is the self-poisoning of 2026-07-02: heal A's probes inflate heal B's
        # triage COUNT. `since_epoch` set = the post-rollback verify window (log-based, user only).
        user_5xx = 0 if self.rolled_back else 1
        probe_5xx = 0 if config.SELF_TRAFFIC_EXCLUDE else self._probe_5xx_pending
        if probe_5xx:
            self.self_5xx_in_detection += probe_5xx   # these were Airbag's own, counted as if user 5xx
        self._probe_5xx_pending = 0                    # this window consumes the pending probe log entries
        errs = user_5xx + probe_5xx
        return {"service": service, "error_rate": 1.0 if errs else 0.0, "total_requests": 200,
                "errors": errs, "window_minutes": window_minutes}

    def sample_business_path(self, service, region, n=20):
        # Airbag actively probes the broken business path at triage. These requests 5xx (the service
        # is broken) and their log entries become self-traffic the NEXT heal's detection may count.
        errs = 0 if self.rolled_back else n
        if not self.rolled_back:
            self._probe_5xx_pending += 1   # one probe-generated log entry per triage burst (deduped)
        return {"errs": errs, "total": n}

    def sample_latency_windows(self, service, region, windows=4):
        return [{"slow": 0, "total": 20} for _ in range(windows)]   # 5xx storm, not latency

    def probe_revision_health(self, service, region, revision, n=8):
        # the causal probe of the rollback TARGET (the witnessed-good revision) — it is healthy, so
        # the causal verdict is PROCEED and these probes do not 5xx.
        return {"errs": 0, "total": n, "slow": 0}

    def synthetic_probe(self, service, path="/healthz"):
        ok = self.rolled_back
        return {"ok": ok, "path": path, "status": 200 if ok else 503, "elapsed_ms": 10.0 if ok else 0.0}

    def fetch_error_logs(self, service, region, n=10):
        return ["Traceback (most recent call last):\nKeyError: 'amount'"]

    def probe_candidate(self, service, region, revision):
        ok = self.rolled_back
        return {"ok": ok, "errors": 0 if ok else 1, "total": 1}

    # --- act ---------------------------------------------------------------------------------
    def rollback_traffic_to_revision(self, service, region, revision):
        self.rolled_back = True
        self.rolled_to = revision
        return {"status": "success", "service": service, "active_revision": revision}

    def set_traffic_split(self, service, region, splits, tag_revision=None):
        self.rolled_back = True
        self.rolled_to = max(splits, key=splits.get) if splits else self.rolled_to
        return {"status": "success", "service": service, "traffic": dict(splits)}

    def reset_target(self, service, region):
        self.rolled_back = True
        return {"status": "success", "service": service}

    def break_target(self, service, region):
        self.rolled_back = False
        return {"status": "success", "service": service}

    def __getattr__(self, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        raise NotImplementedError(f"StormBackend has no backend method {name!r}")


@dataclass(frozen=True)
class StormScorecard:
    label: str
    flag_on: bool
    n_deliveries: int
    heals_per_outage: int
    approval_cards_per_outage: int
    self_traffic_counted_in_detection: int
    unattended_terminal_states: int
    blind_landings: int  # rollbacks onto an unverifiable-evidence target (v5 3.1; 0 in this L1 scenario)
    statuses: list  # the terminal status of each delivery, in order (the shape, for auditing)

    def to_dict(self) -> dict:
        return asdict(self)


# config pinned so the storm is deterministic + offline, independent of the ambient env/.env
_PINNED = {
    "GEMINI_API_KEY": "", "STATE_BACKEND": "memory", "EVENTS_BACKEND": "inproc",
    "QUEUE_BACKEND": "inproc", "STAT_GATE_ENABLED": True, "SIGNALS": "5xx",
    "CAUSAL_CHECK_ENABLED": False, "REVERSIBILITY_GUARD_ENABLED": False,
    "VERIFY_ATTEMPTS": 2, "VERIFY_INTERVAL_S": 0.0, "CI_SELF_CORRECT": False,
    "GITHUB_TOKEN": "", "GITHUB_REPO": "",
    # the storm flags — set per run (below)
    "STORM_COALESCE": False, "SELF_TRAFFIC_EXCLUDE": False, "APPROVAL_COALESCE": False,
}


def run_storm(flag_on: bool, n_deliveries: int = 6, level: str = "L1") -> StormScorecard:
    """Replay ONE outage as N sequential alert deliveries (distinct incident ids) for one broken
    service at autonomy `level` (L1 = the cautious approve-before-rollback posture where a storm
    piles up approval cards). flag_on toggles the three storm flags together (the prod posture).
    Deterministic: no threads, no wall-clock waits."""
    saved_cfg = {k: getattr(config, k) for k in _PINNED}
    saved_get_backend = tools.get_backend
    fb = StormBackend()
    try:
        for k, v in _PINNED.items():
            setattr(config, k, v)
        if flag_on:
            config.STORM_COALESCE = True
            config.SELF_TRAFFIC_EXCLUDE = True
            config.APPROVAL_COALESCE = True
        tools.get_backend = lambda: fb
        state_store.reset_memory()
        autonomy.set_level(SERVICE, level)
        # seed the ledger so the rollback aims at a WITNESSED-good target (not a cold-start landmine)
        from autosre import memory
        memory.witness_serving(SERVICE, _GOOD)

        statuses = []
        for i in range(n_deliveries):
            res = run_self_heal(f"storm-inc-{i}", SERVICE)   # distinct incident id per alert delivery
            statuses.append(res.get("status", "unknown"))

        cards = autonomy.pending_approvals()
        heals = sum(1 for s in statuses if s not in ("attached", "duplicate"))
        n_cards = len(cards)
        # unattended = the redundant pile-up. Measured INDEPENDENTLY from the terminal STATUSES (not
        # derived from the card count): count the deliveries that ended needing a human — a separate
        # operator item — BEYOND the single legitimate one the outage warrants. A coalesced follower
        # ends "attached" (cleanly resolved, needs no attention); the duplicates that pile up + expire
        # silently are the 2026-07-02 pathology. (cards and this move together in a pure-awaiting storm,
        # but they measure different things — the approval store vs the run outcomes — and an escalate
        # that arms a pending needs a human without being an approval card, so they CAN diverge.)
        needs_human = sum(1 for s in statuses
                          if s in ("awaiting_approval", "awaiting_fix_approval", "escalated"))
        unattended = max(0, needs_human - 1)
        # v5 3.1: blind landings (a rollback onto a target the causal probe couldn't assess). This L1
        # scenario gates before any rollback, so it produces none; the metric is measured here so the
        # scorecard genuinely counts them, and the mechanism is proven in test_blind_landing.py.
        blind_landings = sum(1 for i in range(n_deliveries)
                             if (incidents.get(f"storm-inc-{i}") or {}).get("blind_landing"))
    finally:
        tools.get_backend = saved_get_backend
        for k, v in saved_cfg.items():
            setattr(config, k, v)

    label = ("flag-on (storm-safe: coalesce + self-exclude)" if flag_on
             else "flag-off (the honest 2026-07-02 shape)")
    return StormScorecard(
        label=label, flag_on=flag_on, n_deliveries=n_deliveries,
        heals_per_outage=heals, approval_cards_per_outage=n_cards,
        self_traffic_counted_in_detection=fb.self_5xx_in_detection,
        unattended_terminal_states=unattended, blind_landings=blind_landings, statuses=statuses)
