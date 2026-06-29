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


def synthetic_probe(service: str, path: str | None = None) -> dict:
    """Actively hit the service to confirm it is really serving the business path
    (zero-traffic guard + proves the failing endpoint recovered).

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
def break_target(service: str, region: str) -> dict:
    """Put the target into the faulty state (gcp: route 100% to the bad revision carrying
    FAULT_MODE=bug; local: toggle the runtime KeyError fault)."""
    return get_backend().break_target(service, region)


def reset_target(service: str, region: str) -> dict:
    """Restore the target to the healthy baseline (gcp: route 100% to the healthy revision;
    local: clear the runtime fault) so the demo can be run again."""
    return get_backend().reset_target(service, region)
