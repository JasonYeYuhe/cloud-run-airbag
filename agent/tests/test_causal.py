"""The causal pre-check (autosre/causal.py) — v3 Phase 2a + the v4 Phase 2 latency axis. Only a
CONFIDENT-unhealthy rollback target blocks the rollback (COINCIDENT); everything else (healthy,
flaky, no-target, probe error) resolves to proceed, so a legitimate rollback is NEVER blocked
(protecting a bad revision is the worst failure). For a LATENCY incident the probe is additionally
gated on the slow-proportion — a 200-but-confidently-slow target is an equally futile rollback."""
from autosre import causal, config, tools


class _Probe:
    def __init__(self, errs, total, raises=False, slow=0):
        self.errs, self.total, self.raises, self.slow = errs, total, raises, slow

    def probe_revision_health(self, service, region, revision, n=8):
        if self.raises:
            raise RuntimeError("probe network error")
        return {"errs": self.errs, "total": self.total, "slow": self.slow}


def _verdict(monkeypatch, errs, total, raises=False, target="svc-00001-good",
             slow=0, primary_signal="5xx"):
    monkeypatch.setattr(tools, "get_backend", lambda: _Probe(errs, total, raises, slow))
    return causal.precheck("svc", "r", target, primary_signal=primary_signal)["verdict"]


def test_confident_unhealthy_target_is_coincident(monkeypatch):
    # the last-good target ALSO fails confidently -> external cause -> do NOT roll back
    assert _verdict(monkeypatch, errs=8, total=8) == "COINCIDENT"


def test_healthy_target_proceeds(monkeypatch):
    # target healthy -> the current revision is the cause -> roll back (CAUSAL or INCONCLUSIVE, not blocked)
    assert _verdict(monkeypatch, errs=0, total=8) != "COINCIDENT"


def test_flaky_target_below_confidence_proceeds(monkeypatch):
    # 2/8 blips (< CAUSAL_MIN_ERRORS) must NOT block a legitimate rollback
    assert _verdict(monkeypatch, errs=2, total=8) == "INCONCLUSIVE"


def test_no_target_proceeds(monkeypatch):
    assert _verdict(monkeypatch, errs=0, total=8, target=None) == "INCONCLUSIVE"


def test_probe_error_proceeds(monkeypatch):
    # a probe exception must never block a rollback
    assert _verdict(monkeypatch, errs=0, total=0, raises=True) == "INCONCLUSIVE"


def test_zero_samples_proceeds(monkeypatch):
    assert _verdict(monkeypatch, errs=0, total=0) == "INCONCLUSIVE"


def test_confident_unhealthy_requires_min_errors(monkeypatch):
    """Just below the min-errors bar stays INCONCLUSIVE (proceed); at/above with a confident CI blocks."""
    monkeypatch.setattr(config, "CAUSAL_MIN_ERRORS", 3)
    assert _verdict(monkeypatch, errs=2, total=8) == "INCONCLUSIVE"   # 2 < 3 -> proceed
    assert _verdict(monkeypatch, errs=8, total=8) == "COINCIDENT"     # confident -> block


# --- v4 Phase 2: the latency axis, keyed on the triggering signal (veto-only) ----------------------
def _pin_latency_knobs(monkeypatch):
    """Pin the gate knobs (they are env-overridable at import) so an exported AIRBAG_LATENCY_* in a
    dev shell can't flip these tests — mirrors test_confident_unhealthy_requires_min_errors."""
    monkeypatch.setattr(config, "LATENCY_SLO_TOLERANCE", 0.05)
    monkeypatch.setattr(config, "LATENCY_MIN_SLOW", 3)


def test_latency_incident_vetoes_confidently_slow_target(monkeypatch):
    # a 200-but-slow target cannot remedy a LATENCY incident -> COINCIDENT (external slowness)
    _pin_latency_knobs(monkeypatch)
    assert _verdict(monkeypatch, errs=0, total=8, slow=8, primary_signal="latency") == "COINCIDENT"


def test_latency_incident_proceeds_on_fast_target(monkeypatch):
    _pin_latency_knobs(monkeypatch)
    assert _verdict(monkeypatch, errs=0, total=8, slow=0, primary_signal="latency") == "INCONCLUSIVE"


def test_latency_incident_tolerates_warmup_blip(monkeypatch):
    # a scaled-to-zero target's cold-start wobble (below LATENCY_MIN_SLOW) must NOT block the
    # rollback — same never-block-a-legit-rollback posture as the 5xx flaky case
    _pin_latency_knobs(monkeypatch)
    assert _verdict(monkeypatch, errs=0, total=8, slow=2, primary_signal="latency") == "INCONCLUSIVE"


def test_5xx_incident_ignores_slow_target(monkeypatch):
    """BYTE-IDENTICAL 5xx path: a slow target is irrelevant to a 5xx incident — the rollback still
    remedies the 5xx (the latency gate must not fire for primary_signal='5xx')."""
    assert _verdict(monkeypatch, errs=0, total=8, slow=8, primary_signal="5xx") == "INCONCLUSIVE"
    assert _verdict(monkeypatch, errs=0, total=8, slow=8) == "INCONCLUSIVE"   # default signal


def test_latency_incident_still_vetoes_5xx_broken_target(monkeypatch):
    """The 5xx gate runs for EVERY incident: a 5xx-broken target fails _verify on any signal, so
    vetoing it pre-shift is right for a latency incident too (this was v3 behavior, unchanged)."""
    assert _verdict(monkeypatch, errs=8, total=8, slow=0, primary_signal="latency") == "COINCIDENT"


def test_latency_probe_error_and_no_samples_proceed(monkeypatch):
    assert _verdict(monkeypatch, errs=0, total=0, raises=True, primary_signal="latency") == "INCONCLUSIVE"
    assert _verdict(monkeypatch, errs=0, total=0, slow=0, primary_signal="latency") == "INCONCLUSIVE"


def test_legacy_probe_without_slow_key_proceeds(monkeypatch):
    """A backend that still returns {errs,total} (no slow key) must default slow=0 — the latency
    gate reads it as no evidence and proceeds (forward-compat with out-of-tree backends)."""
    class _Legacy:
        def probe_revision_health(self, service, region, revision, n=8):
            return {"errs": 0, "total": 8}
    monkeypatch.setattr(tools, "get_backend", lambda: _Legacy())
    assert causal.precheck("svc", "r", "t", primary_signal="latency")["verdict"] == "INCONCLUSIVE"
