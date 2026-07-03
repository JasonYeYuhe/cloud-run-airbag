"""Detection engine: collect the signals the enabled detectors need, run each detector, fuse.

Contract: ``detect()`` returns a dict shaped like ``analyzer.analyze`` output — at least
``{verdict, reason, rate}`` plus the 5xx CI fields — so ``state_machine._validate`` (reads
``verdict``/``reason``) and the OBSERVE branch (folds ``rate`` into the learned 5xx baseline) are
unchanged. In 5xx-only mode the returned dict is the 5xx verdict VERBATIM (key-identical to today).
Only when >1 detector is enabled is a ``signals`` breakdown added — so the default path stays byte
identical. (Latency/saturation/burn detectors land in later Phase 1 commits.)
"""
from __future__ import annotations

from dataclasses import dataclass

from .. import analyzer, config, tools


@dataclass
class SignalContext:
    """Observations the detectors read. Collected need-based: only fields the enabled detectors use
    are populated, so enabling only 5xx makes zero extra backend calls."""
    service: str
    region: str
    baseline_rate: float
    err_sample: dict | None = None          # {errs, total} — the 5xx business-path sample
    latency_windows: list | None = None     # [{slow, total}, …] per-window count of over-SLO requests
    error_windows: list | None = None       # [{errs, total}, …] per-window 5xx counts (burn-rate pooling)


def enabled_detectors() -> list[str]:
    """The detector keys selected by AIRBAG_SIGNALS. Empty/whitespace falls back to 5xx so the
    statistical gate is never left signal-less. 'all' = every shipped detector."""
    raw = (config.SIGNALS or "").strip().lower()
    if raw in ("", "5xx"):
        return ["5xx"]
    if raw == "all":
        return list(_DETECTORS.keys())
    keys = [k.strip() for k in raw.split(",") if k.strip() and k.strip() in _DETECTORS]
    return keys or ["5xx"]


def _collect(service: str, region: str, baseline_rate: float, keys: list[str]) -> SignalContext:
    ctx = SignalContext(service=service, region=region, baseline_rate=baseline_rate)
    if "5xx" in keys:
        ctx.err_sample = tools.sample_business_path(service, region, config.STAT_SAMPLE_N)
    if "latency" in keys:
        ctx.latency_windows = tools.sample_latency_windows(service, region, config.SIGNAL_WINDOWS)
    if "burn" in keys:
        ctx.error_windows = tools.sample_error_windows(service, region, config.BURN_WINDOWS,
                                                       config.BURN_PER_WINDOW)
    return ctx


# --- detectors: each takes a SignalContext, returns a verdict dict {verdict, reason, ...} ----------
def _detect_5xx(ctx: SignalContext) -> dict:
    """The v2 Wilson-CI 5xx detector, unchanged — its own CI is its anti-flap (a 4/4 outage FAILs; a
    single blip is INCONCLUSIVE). Output is analyzer.analyze verbatim."""
    s = ctx.err_sample or {"errs": 0, "total": 0}
    return analyzer.analyze(int(s.get("errs", 0)), int(s.get("total", 0)), ctx.baseline_rate,
                            z=config.STAT_Z, min_fail_errors=config.STAT_MIN_FAIL_ERRORS)


def _detect_latency(ctx: SignalContext) -> dict:
    """CI-backed latency detector: a request over the SLO is "slow"; Wilson-gate the per-window
    slow-proportion (same rigor as 5xx) vs LATENCY_SLO_TOLERANCE, and require the window to FAIL for
    DEBOUNCE_WINDOWS of the last windows (persistence = anti-flap). A degradation present in fewer
    windows collapses to PASS (NOT INCONCLUSIVE — that would page); INCONCLUSIVE only for no data.
    The SLO is baseline-relative and applied by the collector, so this reads {slow, total} per window."""
    windows = [w for w in (ctx.latency_windows or []) if int(w.get("total", 0)) > 0]
    if not windows:
        return {"verdict": "INCONCLUSIVE", "reason": "no latency samples",
                "detail": {"windows": 0}}
    fail_windows = sum(
        1 for w in windows
        if analyzer.analyze(int(w["slow"]), int(w["total"]), config.LATENCY_SLO_TOLERANCE,
                            z=config.STAT_Z, min_fail_errors=config.LATENCY_MIN_SLOW)["verdict"] == "FAIL")
    n = len(windows)
    if fail_windows >= config.SIGNAL_DEBOUNCE_WINDOWS:
        return {"verdict": "FAIL",
                "reason": f"latency: {fail_windows}/{n} recent windows confidently over the SLO",
                "detail": {"fail_windows": fail_windows, "windows": n}}
    return {"verdict": "PASS",
            "reason": (f"latency ok: {fail_windows}/{n} windows over SLO "
                       f"(< {config.SIGNAL_DEBOUNCE_WINDOWS}-window debounce)"),
            "detail": {"fail_windows": fail_windows, "windows": n}}


def _detect_burn(ctx: SignalContext) -> dict:
    """Pooled-Wilson SLO burn-rate detector (v5 5.1). A slow error-budget burn is sub-threshold in any
    SINGLE window but POOLED over the windows the Wilson lower bound tightens and clears the baseline —
    the exact miss the single-window 5xx detector can't catch. Anti-flap: only a SUSTAINED burn (errors
    present in ≥ SIGNAL_DEBOUNCE_WINDOWS windows) FAILs; an all-in-one-window SPIKE (pooled-elevated but
    concentrated) collapses to PASS — that spike is the 5xx detector's job, not a burn. INCONCLUSIVE
    only for no data. Reuses analyzer.analyze (the same Wilson rigor + the LEARNED baseline).

    HONEST LIMIT (v5 adversarial review): the pooled LB is compared to the LEARNED baseline. A benign
    service whose normal rate is AT or below the baseline is protected (the pooled LB doesn't clear it
    — healthy_noisy at 3% vs a 2% baseline stays PASS). But on a FRESH/unlearned service whose TRUE
    normal EXCEEDS the configured STAT_BASELINE_RATE, a confident elevation above that placeholder
    baseline reads as a burn until the baseline converges — the exact reason 5.2 (AIRBAG_BASELINE_GUARD)
    ships with it and burn is OPT-IN (default AIRBAG_SIGNALS=5xx). Set STAT_BASELINE_RATE to the
    service's real SLO, or let observe_healthy learn it, before enabling burn on a noisy service."""
    windows = [w for w in (ctx.error_windows or []) if int(w.get("total", 0)) > 0]
    if not windows:
        return {"verdict": "INCONCLUSIVE", "reason": "no burn-rate samples", "detail": {"windows": 0}}
    pooled_errs = sum(int(w.get("errs", 0)) for w in windows)
    pooled_total = sum(int(w.get("total", 0)) for w in windows)
    windows_with_errors = sum(1 for w in windows if int(w.get("errs", 0)) > 0)
    n = len(windows)
    v = analyzer.analyze(pooled_errs, pooled_total, ctx.baseline_rate,
                         z=config.STAT_Z, min_fail_errors=config.BURN_MIN_ERRORS)
    if v["verdict"] == "FAIL" and windows_with_errors >= config.SIGNAL_DEBOUNCE_WINDOWS:
        return {"verdict": "FAIL",
                "reason": (f"burn: pooled {pooled_errs}/{pooled_total} over {n} windows confidently "
                           f"above baseline (95% CI lower {v['ci_low']:.1%} > {ctx.baseline_rate:.1%}); "
                           f"sustained across {windows_with_errors} windows"),
                "detail": {"pooled_errs": pooled_errs, "pooled_total": pooled_total,
                           "windows": n, "windows_with_errors": windows_with_errors,
                           "ci_low": v["ci_low"]}}
    # pooled-FAIL but errors concentrated in < debounce windows = a SPIKE, not a burn -> PASS (the 5xx
    # detector owns spikes); or pooled not confidently elevated -> PASS. INCONCLUSIVE only for no data.
    return {"verdict": "PASS",
            "reason": (f"burn ok: pooled {pooled_errs}/{pooled_total} over {n} windows "
                       f"({windows_with_errors} with errors) — "
                       + ("not a sustained burn (< debounce)" if v["verdict"] == "FAIL"
                          else "not confidently above baseline")),
            "detail": {"pooled_errs": pooled_errs, "pooled_total": pooled_total,
                       "windows": n, "windows_with_errors": windows_with_errors}}


_DETECTORS = {"5xx": _detect_5xx, "latency": _detect_latency, "burn": _detect_burn}
_CI_BACKED = {"5xx", "latency", "burn"}   # detectors with a statistical confidence bound -> may drive a rollback


# --- fusion ------------------------------------------------------------------------------------
def _fuse(verdicts: dict[str, dict], keys: list[str]) -> dict:
    active = [(k, verdicts[k]) for k in keys if k in verdicts]
    if not active:
        return {"verdict": "INCONCLUSIVE", "reason": "no detectors enabled", "rate": 0.0}
    if len(active) == 1:
        return active[0][1]   # single detector (5xx-only default) -> verbatim, key-identical to v2
    # strongest-signal over the CI-backed detectors: any confident FAIL wins (each is already
    # debounced/CI-gated, so a single FAIL is trustworthy); else INCONCLUSIVE if any is; else PASS.
    ci = [(k, verdicts[k]) for k, _ in active if k in _CI_BACKED]
    fails = [k for k, v in ci if v.get("verdict") == "FAIL"]
    inconcl = [k for k, v in ci if v.get("verdict") == "INCONCLUSIVE"]
    if fails:
        verdict, reason = "FAIL", "; ".join(f"{k} {verdicts[k].get('reason')}" for k in fails)
    elif inconcl:
        verdict, reason = "INCONCLUSIVE", "; ".join(f"{k} {verdicts[k].get('reason')}" for k in inconcl)
    else:
        verdict, reason = "PASS", "all enabled signals healthy"
    fused = {"verdict": verdict, "reason": reason,
             "signals": {k: {"verdict": v.get("verdict"), "reason": v.get("reason")} for k, v in active}}
    if "5xx" in verdicts:   # carry the 5xx rate for the learned-baseline EMA (only when 5xx ran)
        fused["rate"] = verdicts["5xx"].get("rate")
    return fused


def detect(service: str, region: str, baseline_rate: float) -> dict:
    """Run the enabled detectors and fuse into one stat-shaped verdict."""
    keys = enabled_detectors()
    ctx = _collect(service, region, baseline_rate, keys)
    verdicts = {k: _DETECTORS[k](ctx) for k in keys if k in _DETECTORS}
    return _fuse(verdicts, keys)
