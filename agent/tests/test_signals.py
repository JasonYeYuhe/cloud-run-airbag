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


# --- latency detector (Phase 1.2) --------------------------------------------------------------
def _ctx(latency_windows):
    return engine.SignalContext(service="s", region="r", baseline_rate=0.02,
                                latency_windows=latency_windows)


def test_latency_detector_fails_on_sustained_regression():
    v = engine._detect_latency(_ctx([{"slow": 18, "total": 20}] * 4))
    assert v["verdict"] == "FAIL" and v["detail"]["fail_windows"] == 4


def test_latency_detector_fails_on_moderate_sustained_regression():
    v = engine._detect_latency(_ctx([{"slow": 8, "total": 20}] * 4))   # ~40% over SLO
    assert v["verdict"] == "FAIL"


def test_latency_detector_passes_on_transient_spike_debounce():
    """One hot window (< debounce) must collapse to PASS, NOT INCONCLUSIVE (which would page)."""
    v = engine._detect_latency(_ctx([{"slow": 18, "total": 20}, {"slow": 0, "total": 20},
                                     {"slow": 0, "total": 20}, {"slow": 0, "total": 20}]))
    assert v["verdict"] == "PASS" and v["detail"]["fail_windows"] == 1


def test_latency_detector_passes_within_slo():
    v = engine._detect_latency(_ctx([{"slow": 1, "total": 20}] * 4))   # below LATENCY_MIN_SLOW
    assert v["verdict"] == "PASS"


def test_latency_detector_inconclusive_on_no_data():
    assert engine._detect_latency(_ctx([]))["verdict"] == "INCONCLUSIVE"
    assert engine._detect_latency(_ctx([{"slow": 0, "total": 0}]))["verdict"] == "INCONCLUSIVE"


# --- fusion (Phase 1.2) ------------------------------------------------------------------------
def test_fuse_strongest_signal_latency_fail_wins():
    fused = engine._fuse({"5xx": {"verdict": "INCONCLUSIVE", "reason": "5x", "rate": 0.0},
                          "latency": {"verdict": "FAIL", "reason": "slow"}}, ["5xx", "latency"])
    assert fused["verdict"] == "FAIL"
    assert "signals" in fused and fused["rate"] == 0.0        # carries the 5xx rate for the EMA
    assert set(fused["signals"]) == {"5xx", "latency"}


def test_fuse_all_pass_is_pass():
    fused = engine._fuse({"5xx": {"verdict": "PASS", "reason": "ok", "rate": 0.0},
                          "latency": {"verdict": "PASS", "reason": "ok"}}, ["5xx", "latency"])
    assert fused["verdict"] == "PASS"


def test_all_enables_5xx_and_latency(monkeypatch):
    monkeypatch.setattr(config, "SIGNALS", "all")
    assert set(signals.enabled_detectors()) == {"5xx", "latency"}


def test_latency_detector_boundary_at_debounce(monkeypatch):
    """Exactly SIGNAL_DEBOUNCE_WINDOWS fail windows triggers (>=); one fewer does not."""
    monkeypatch.setattr(config, "SIGNAL_DEBOUNCE_WINDOWS", 3)
    hot, cool = {"slow": 18, "total": 20}, {"slow": 0, "total": 20}
    assert engine._detect_latency(_ctx([hot, hot, hot, cool]))["verdict"] == "FAIL"   # 3 -> FAIL
    assert engine._detect_latency(_ctx([hot, hot, cool, cool]))["verdict"] == "PASS"   # 2 -> PASS


def test_latency_only_mode_has_no_rate_key(monkeypatch):
    """AIRBAG_SIGNALS=latency: detect() carries NO 5xx `rate`, so the OBSERVE-branch fold is skipped
    (never folds a fabricated 0.0 into the 5xx EMA) and nothing KeyErrors."""
    from autosre import tools

    class _LatBackend:
        def sample_latency_windows(self, service, region, windows=4):
            return [{"slow": 18, "total": 20}] * 4

    monkeypatch.setattr(config, "SIGNALS", "latency")
    monkeypatch.setattr(tools, "get_backend", lambda: _LatBackend())
    out = signals.detect("svc", "r", baseline_rate=0.02)
    assert out["verdict"] == "FAIL" and "rate" not in out    # no 5xx rate -> observe_healthy guard skips
