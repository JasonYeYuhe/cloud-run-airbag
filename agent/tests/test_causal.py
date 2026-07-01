"""The causal pre-check (autosre/causal.py) — Phase 2a. Only a CONFIDENT-unhealthy rollback target
blocks the rollback (COINCIDENT); everything else (healthy, flaky, no-target, probe error) resolves to
proceed, so a legitimate rollback is NEVER blocked (protecting a bad revision is the worst failure)."""
from autosre import causal, config, tools


class _Probe:
    def __init__(self, errs, total, raises=False):
        self.errs, self.total, self.raises = errs, total, raises

    def probe_revision_health(self, service, region, revision, n=8):
        if self.raises:
            raise RuntimeError("probe network error")
        return {"errs": self.errs, "total": self.total}


def _verdict(monkeypatch, errs, total, raises=False, target="svc-00001-good"):
    monkeypatch.setattr(tools, "get_backend", lambda: _Probe(errs, total, raises))
    return causal.precheck("svc", "r", target)["verdict"]


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
