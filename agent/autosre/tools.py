"""Tools the agent uses to observe and act on Cloud Run.

Each tool runs in MOCK mode (AIRBAG_USE_MOCK=true) so the whole loop is runnable
with zero GCP. Real implementations are stubbed with the verified API shapes —
see docs/PLAN.md for the wiring order. Docstrings/type-hints are kept ADK-friendly
so these can also be attached to an ADK LlmAgent as FunctionTools.
"""
from __future__ import annotations

import logging

from . import config

log = logging.getLogger("airbag.tools")

# --- tiny in-memory mock world (Day 0 only) -------------------------------
_MOCK = {"rolled_back": False}


def list_cloud_run_revisions(service: str, region: str) -> dict:
    """List Cloud Run revisions with traffic %, readiness and creation time.

    Args:
        service (str): Cloud Run service name.
        region (str): GCP region, e.g. asia-northeast1.
    """
    if config.USE_MOCK:
        rb = _MOCK["rolled_back"]
        return {"service": service, "revisions": [
            {"name": f"{service}-00002-bad", "ready": True,
             "traffic_percent": 0 if rb else 100, "create_time": "2026-06-28T00:00:00Z"},
            {"name": f"{service}-00001-good", "ready": True,
             "traffic_percent": 100 if rb else 0, "create_time": "2026-06-27T22:00:00Z"},
        ]}
    # TODO(real): run_v2.ServicesClient().get_service(name).traffic + list_revisions()
    raise NotImplementedError("wire google-cloud-run run_v2 — see docs/PLAN.md step 3")


def query_error_rate(service: str, region: str, window_minutes: int = 5) -> dict:
    """Return the 5xx error rate and total request count over a recent window.

    Args:
        service (str): Cloud Run service name.
        region (str): GCP region.
        window_minutes (int): lookback window in minutes.
    """
    if config.USE_MOCK:
        rate = 0.0 if _MOCK["rolled_back"] else 0.12
        return {"service": service, "error_rate": rate, "total_requests": 200,
                "window_minutes": window_minutes}
    # TODO(real): Cloud Monitoring PromQL on run.googleapis.com/request_count,
    # response_code_class="5xx" ratio; guard zero-traffic with sum(rate(total)) > N.
    raise NotImplementedError("wire google-cloud-monitoring — see docs/PLAN.md step 4")


def synthetic_probe(service: str, path: str = "/healthz") -> dict:
    """Actively hit the service to confirm it is really serving (zero-traffic guard).

    Args:
        service (str): Cloud Run service name.
        path (str): health path to probe.
    """
    if config.USE_MOCK:
        ok = _MOCK["rolled_back"]
        return {"ok": ok, "path": path, "status": 200 if ok else 503}
    # TODO(real): httpx.get(f"{service_url}{path}", timeout=5) -> status == 200
    raise NotImplementedError("wire httpx probe — see docs/PLAN.md step 8")


def rollback_traffic_to_revision(service: str, region: str, revision: str) -> dict:
    """Route 100% of Cloud Run traffic to one explicit (last-good) revision.

    Args:
        service (str): Cloud Run service name.
        region (str): GCP region.
        revision (str): exact target revision name (never LATEST).
    """
    if config.USE_MOCK:
        _MOCK["rolled_back"] = True
        return {"status": "success", "service": service, "active_revision": revision}
    # TODO(real): run_v2 update_service(traffic=[TrafficTarget(
    #   type_=TrafficTargetAllocationType.TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION,
    #   revision=revision, percent=100)]).result()   # block on the long-running op
    raise NotImplementedError("wire run_v2 update_service — see docs/PLAN.md step 3")


def restore_traffic_to_latest(service: str, region: str) -> dict:
    """Undo a temporary rollback by routing traffic back to LATEST (the fixed revision).

    Args:
        service (str): Cloud Run service name.
        region (str): GCP region.
    """
    if config.USE_MOCK:
        _MOCK["rolled_back"] = False
        return {"status": "success", "service": service, "active_revision": "LATEST"}
    raise NotImplementedError("wire run_v2 update_service --to-latest — see docs/PLAN.md step 8")


def open_fix_pr(service: str, evidence: list[str]) -> dict:
    """Open a Gemini-authored fix PR (slow path / stretch).

    Args:
        service (str): Cloud Run service name.
        evidence (list[str]): root-cause evidence to include in the PR body.
    """
    if config.USE_MOCK:
        return {"status": "stub", "pr_url": "https://github.com/JasonYeYuhe/airbag-target-app/pull/0"}
    # TODO(real): GitHub App installation token -> Octokit/PyGithub create branch + PR.
    raise NotImplementedError("wire GitHub App — see docs/PLAN.md step 9")
