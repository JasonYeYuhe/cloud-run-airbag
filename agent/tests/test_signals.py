"""The multi-signal detection engine (autosre/signals). Phase 1.1a: the 5xx-only default must be the
v2 Wilson verdict VERBATIM (key-identical), so the existing heal + bench baseline are unchanged."""
from autosre import analyzer, config, signals
from autosre.signals import engine


def _fake_backend(monkeypatch, errs, total):
    from autosre import tools
    monkeypatch.setattr(tools, "get_backend", lambda: _Backend(errs, total))


class _Backend:
    def __init__(self, errs, total):
        self.errs, self.total = errs, total

    def sample_business_path(self, service, region, n=20):
        return {"errs": self.errs, "total": self.total}


def test_enabled_defaults_to_5xx(monkeypatch):
    monkeypatch.setattr(config, "SIGNALS", "5xx")
    assert signals.enabled_detectors() == ["5xx"]
    monkeypatch.setattr(config, "SIGNALS", "")        # empty falls back to 5xx (never signal-less)
    assert signals.enabled_detectors() == ["5xx"]
    monkeypatch.setattr(config, "SIGNALS", "bogus")   # unknown key falls back to 5xx
    assert signals.enabled_detectors() == ["5xx"]


def test_detect_5xx_is_analyzer_verbatim(monkeypatch):
    """5xx-only detect() == analyzer.analyze(...) key-for-key — the byte-identical default path."""
    monkeypatch.setattr(config, "SIGNALS", "5xx")
    _fake_backend(monkeypatch, errs=14, total=20)
    got = signals.detect("svc", "r", baseline_rate=0.02)
    expected = analyzer.analyze(14, 20, 0.02, z=config.STAT_Z,
                                min_fail_errors=config.STAT_MIN_FAIL_ERRORS)
    assert got == expected                    # identical dict, same keys, same values
    assert "signals" not in got               # no extra key in the default path
    assert got["verdict"] == "FAIL"


def test_detect_5xx_healthy_is_verbatim(monkeypatch):
    monkeypatch.setattr(config, "SIGNALS", "5xx")
    _fake_backend(monkeypatch, errs=0, total=20)
    got = signals.detect("svc", "r", baseline_rate=0.02)
    assert got == analyzer.analyze(0, 20, 0.02, z=config.STAT_Z,
                                   min_fail_errors=config.STAT_MIN_FAIL_ERRORS)


def test_fuse_single_detector_is_verbatim():
    v = {"verdict": "FAIL", "reason": "x", "rate": 0.7}
    assert engine._fuse({"5xx": v}, ["5xx"]) is v   # single detector returned unchanged (identity)
