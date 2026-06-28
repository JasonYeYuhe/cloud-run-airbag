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


def restore_traffic_to_latest(service: str, region: str) -> dict:
    """Undo a temporary rollback by routing traffic back to LATEST (the fixed revision).

    Args:
        service (str): Cloud Run service name.
        region (str): GCP region.
    """
    return get_backend().restore_traffic_to_latest(service, region)


# --- demo harness (not part of the heal; drives the repeatable break/heal/reset demo) ---
def break_target(service: str, region: str) -> dict:
    """Put the target into the faulty state (gcp: route 100% to the bad revision carrying
    FAULT_MODE=bug; local: toggle the runtime KeyError fault)."""
    return get_backend().break_target(service, region)


def reset_target(service: str, region: str) -> dict:
    """Restore the target to the healthy baseline (gcp: route 100% to the healthy revision;
    local: clear the runtime fault) so the demo can be run again."""
    return get_backend().reset_target(service, region)
