"""Tools façade — delegates to the active backend (mock | local | gcp).

ADK-friendly signatures + docstrings so these can also be attached to an ADK
LlmAgent as FunctionTools (see agent.py).
"""
from __future__ import annotations

from .backends import get_backend


def list_cloud_run_revisions(service: str, region: str) -> dict:
    """List Cloud Run revisions with traffic %, readiness and creation time.

    Args:
        service (str): Cloud Run service name.
        region (str): GCP region, e.g. asia-northeast1.
    """
    return get_backend().list_cloud_run_revisions(service, region)


def query_error_rate(service: str, region: str, window_minutes: int = 5) -> dict:
    """Return the 5xx error rate and request count over a recent window.

    Args:
        service (str): Cloud Run service name.
        region (str): GCP region.
        window_minutes (int): lookback window in minutes.
    """
    return get_backend().query_error_rate(service, region, window_minutes)


def synthetic_probe(service: str, path: str = "/healthz") -> dict:
    """Actively hit the service to confirm it is really serving (zero-traffic guard).

    Args:
        service (str): Cloud Run service name.
        path (str): health path to probe.
    """
    return get_backend().synthetic_probe(service, path=path)


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
