"""Statistical analyzer — incl. the edge cases Gemini 3.1 Pro + 3.5 Flash flagged in review:
a low-traffic total outage must FAIL (trust the Wilson CI, don't hard-gate on sample size), a
single transient blip must NOT FAIL (min error count), and N=0 must be safe."""
from autosre.analyzer import analyze, wilson_interval


def test_clear_outage_fails():
    assert analyze(20, 20, baseline_rate=0.02)["verdict"] == "FAIL"
    assert analyze(18, 20, baseline_rate=0.02)["verdict"] == "FAIL"


def test_low_traffic_total_outage_still_fails():
    # 4/4 = 100%; Wilson lower bound ~0.44 > baseline and errs>=3 -> FAIL (don't wait for a human)
    v = analyze(4, 4, baseline_rate=0.02)
    assert v["verdict"] == "FAIL" and v["ci_low"] > 0.02


def test_single_blip_does_not_fail():
    assert analyze(1, 1, baseline_rate=0.02)["verdict"] == "INCONCLUSIVE"   # errs < min_fail_errors
    assert analyze(2, 20, baseline_rate=0.02)["verdict"] == "INCONCLUSIVE"  # few errors


def test_confidently_healthy_passes_with_enough_samples():
    assert analyze(0, 500, baseline_rate=0.02)["verdict"] == "PASS"


def test_small_clean_sample_is_inconclusive():
    # 0/20 can't confidently prove the rate is below 2% -> INCONCLUSIVE (not PASS)
    assert analyze(0, 20, baseline_rate=0.02)["verdict"] == "INCONCLUSIVE"


def test_zero_samples_is_safe():
    v = analyze(0, 0, baseline_rate=0.02)
    assert v["verdict"] == "INCONCLUSIVE" and v["rate"] == 0.0


def test_wilson_interval_bounds_and_n0():
    lo, hi = wilson_interval(0, 0)
    assert (lo, hi) == (0.0, 1.0)         # safe at N=0
    lo, hi = wilson_interval(5, 10)
    assert 0.0 <= lo < 0.5 < hi <= 1.0    # straddles the point estimate
