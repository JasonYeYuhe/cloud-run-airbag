"""Statistical decision analyzer — turns the serving revision's sampled 5xx observations into a
three-state verdict (FAIL / PASS / INCONCLUSIVE), so the rollback decision is statistically
defensible instead of a static `error_rate >= 0.05` threshold.

Uses the closed-form Wilson score interval (no scipy) for a proportion. Reviewed by Gemini 3.1 Pro
+ 3.5 Flash, which flagged: trust the CI on small samples (don't hard-gate on min_samples — a 4/4
outage is confidently bad) but require a minimum error COUNT so a single blip can't trip a
rollback; handle N=0 safely; treat INCONCLUSIVE as a constraint, not an alarm.

baseline_rate is a PARAMETER (the caller passes it). For now the caller passes a config floor; the
immediate follow-up is to set it from the last-good revision's historical rate (the learned-baseline
theme) so a service that's normally noisy isn't constantly flagged.
"""
from __future__ import annotations

import math


def wilson_interval(errs: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """95%-by-default Wilson score interval for the proportion errs/total. Safe at total<=0."""
    if total <= 0:
        return 0.0, 1.0
    p = errs / total
    denom = 1.0 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    margin = (z / denom) * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    return max(0.0, center - margin), min(1.0, center + margin)


def analyze(errs: int, total: int, baseline_rate: float = 0.02, *,
            z: float = 1.96, min_fail_errors: int = 3) -> dict:
    """Three-state verdict comparing the serving 5xx proportion to baseline_rate.

    FAIL  — confidently elevated (Wilson lower bound > baseline) AND at least min_fail_errors
            observed errors (so one transient failure can't trigger an auto-rollback, but a
            low-traffic 4/4 total outage — Wilson lower bound ~0.44 — still does).
    PASS  — confidently fine (Wilson upper bound < baseline).
    INCONCLUSIVE — the interval straddles baseline / not enough errors / no samples.
    """
    if total <= 0:
        return {"verdict": "INCONCLUSIVE", "rate": 0.0, "ci_low": 0.0, "ci_high": 1.0,
                "errs": 0, "total": 0, "baseline_rate": baseline_rate,
                "reason": "no samples collected"}
    rate = errs / total
    lo, hi = wilson_interval(errs, total, z)
    if lo > baseline_rate and errs >= min_fail_errors:
        verdict, reason = "FAIL", (f"5xx rate {rate:.1%} ({errs}/{total}); 95% CI lower bound "
                                   f"{lo:.1%} > baseline {baseline_rate:.1%} — confidently elevated")
    elif hi < baseline_rate:
        verdict, reason = "PASS", (f"5xx rate {rate:.1%} ({errs}/{total}); 95% CI upper bound "
                                   f"{hi:.1%} < baseline {baseline_rate:.1%} — confidently healthy")
    else:
        verdict, reason = "INCONCLUSIVE", (f"5xx rate {rate:.1%} ({errs}/{total}); 95% CI "
                                           f"[{lo:.1%},{hi:.1%}] straddles baseline {baseline_rate:.1%} "
                                           f"or too few errors (<{min_fail_errors})")
    return {"verdict": verdict, "rate": round(rate, 3), "ci_low": round(lo, 3),
            "ci_high": round(hi, 3), "errs": errs, "total": total,
            "baseline_rate": baseline_rate, "reason": reason}
