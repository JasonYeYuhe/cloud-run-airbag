"""Tools façade — delegates to the active backend (mock | local | gcp).

ADK-friendly signatures + docstrings so these can also be attached to an ADK
LlmAgent as FunctionTools (see agent.py).
"""
from __future__ import annotations

from . import config
from .backends import get_backend


def list_cloud_run_revisions(service: str, region: str) -> dict:
    """List Cloud Run revisions with traffic %, readiness and creation time.

    Args:
        service (str): Cloud Run service name.
        region (str): GCP region, e.g. asia-northeast1.
    """
    return get_backend().list_cloud_run_revisions(service, region)


def query_error_rate(service: str, region: str, window_minutes: int = 5,
                     since_epoch: float | None = None) -> dict:
    """Return the 5xx error rate and request count over a recent window.

    Args:
        service (str): Cloud Run service name.
        region (str): GCP region.
        window_minutes (int): lookback window in minutes.
    """
    return get_backend().query_error_rate(service, region, window_minutes, since_epoch)


def fetch_error_logs(service: str, region: str, n: int = 10) -> list[str]:
    """Recent ERROR-level log lines (exception + stack trace) for the service — the raw evidence
    the RCA agent reasons over, instead of a hand-built 'returned HTTP 500' string.

    Args:
        service (str): Cloud Run service name.
        region (str): GCP region.
        n (int): max log entries to return.
    """
    return get_backend().fetch_error_logs(service, region, n)


def sample_business_path(service: str, region: str, n: int = 20) -> dict:
    """Actively sample the business path n times and count 5xx — the statistical decision
    analyzer (analyzer.analyze) turns {errs, total} into a FAIL/PASS/INCONCLUSIVE verdict.

    Args:
        service (str): Cloud Run service name.
        region (str): GCP region.
        n (int): number of probe requests.
    """
    return get_backend().sample_business_path(service, region, n)


def sample_latency_windows(service: str, region: str, windows: int = 4) -> list[dict]:
    """Per-window count of requests slower than the latency SLO (last-good p99 × factor), newest
    last — the latency detector (signals/) Wilson-gates each window's slow-proportion and requires
    persistence across windows. Returns [{slow, total}, …]. Only called when the latency detector is
    enabled (AIRBAG_SIGNALS includes 'latency').

    Args:
        service (str): Cloud Run service name.
        region (str): GCP region.
        windows (int): number of recent time-bucketed windows to return.
    """
    return get_backend().sample_latency_windows(service, region, windows)


def sample_error_windows(service: str, region: str, windows: int = 6, per_window: int = 50) -> list[dict]:
    """Per-window 5xx counts over `windows` bursts of the business path, newest last — the pooled-Wilson
    burn-rate detector (signals/, v5 5.1) pools these to catch a slow error-budget burn that is
    sub-threshold in any single window. Returns [{errs, total}, …]. Only called when the burn detector
    is enabled (AIRBAG_SIGNALS includes 'burn').

    Args:
        service (str): Cloud Run service name.
        region (str): GCP region.
        windows (int): number of recent time-bucketed windows to pool.
        per_window (int): samples per window.
    """
    return get_backend().sample_error_windows(service, region, windows, per_window)


def probe_revision_health(service: str, region: str, revision: str, n: int = 8) -> dict:
    """Probe a specific NON-serving revision's health directly (per-revision URL), returning
    {errs, total, slow} over n samples — the causal pre-check (causal.py) Wilson-gates errs (and,
    for a latency incident, the slow count of over-SLO successes) to decide whether the rollback
    TARGET is also degraded (external cause) before committing a rollback.
    Only called when AIRBAG_CAUSAL_CHECK is on.

    Args:
        service (str): Cloud Run service name.
        region (str): GCP region.
        revision (str): the rollback-target revision to probe.
        n (int): number of probe requests.
    """
    return get_backend().probe_revision_health(service, region, revision, n)


def synthetic_probe(service: str, path: str | None = None) -> dict:
    """Actively hit the service to confirm it is really serving the business path (zero-traffic guard +
    proves the failing endpoint recovered). Returns {ok, path, status, elapsed_ms} — elapsed_ms lets the
    latency-aware verify confirm a rollback recovered the LATENCY signal (a slow-but-200 response is not
    "recovered" when the latency detector is enabled).

    Args:
        service (str): Cloud Run service name.
        path (str): path to probe (defaults to the business path, not /healthz).
    """
    return get_backend().synthetic_probe(service, path=path or config.PROBE_PATH)


def rollback_traffic_to_revision(service: str, region: str, revision: str) -> dict:
    """Route 100% of Cloud Run traffic to one explicit (last-good) revision.

    Args:
        service (str): Cloud Run service name.
        region (str): GCP region.
        revision (str): exact target revision name (never LATEST).
    """
    return get_backend().rollback_traffic_to_revision(service, region, revision)


def set_traffic_split(service: str, region: str, splits: dict, tag_revision: str | None = None) -> dict:
    """Split traffic across explicit revisions (e.g. {fix: 10, safe: 90}) — used for the
    gradual canary when restoring traffic to the fix. `tag_revision` tags one revision so it
    gets a stable per-revision URL the canary gate can probe directly.

    Args:
        service (str): Cloud Run service name.
        region (str): GCP region.
        splits (dict): revision name -> percent (should sum to 100).
        tag_revision (str): optional revision to tag for direct probing.
    """
    return get_backend().set_traffic_split(service, region, splits, tag_revision=tag_revision)


def probe_candidate(service: str, region: str, revision: str) -> dict:
    """Probe a specific candidate revision DIRECTLY (per-revision URL), so a canary gate can
    verify the fix itself even at a low traffic percentage. Returns {ok, errors, total}.

    Args:
        service (str): Cloud Run service name.
        region (str): GCP region.
        revision (str): the candidate (fix) revision to probe directly.
    """
    return get_backend().probe_candidate(service, region, revision)


# --- demo harness (not part of the heal; drives the repeatable break/heal/reset demo) ---
def break_target(service: str, region: str, prefer: str = "bug") -> dict:
    """Put the target into the faulty state. `prefer` picks the fault: 'bug' (the KeyError the fix-PR
    repairs, default) or 'slow' (the v3 latency regression — 200s past the SLO, ~0 5xx). gcp routes
    100% to the matching bad revision; local toggles the runtime fault."""
    return get_backend().break_target(service, region, prefer)


def reset_target(service: str, region: str) -> dict:
    """Restore the target to the healthy baseline (gcp: route 100% to the healthy revision;
    local: clear the runtime fault) so the demo can be run again."""
    return get_backend().reset_target(service, region)
