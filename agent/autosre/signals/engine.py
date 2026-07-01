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


_DETECTORS = {"5xx": _detect_5xx, "latency": _detect_latency}
_CI_BACKED = {"5xx", "latency"}   # detectors with a statistical confidence bound -> may drive a rollback


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
