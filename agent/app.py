"""Airbag agent — FastAPI webhook entrypoint (deploys to Cloud Run).

Pattern (verified): respond fast (202), heal asynchronously, verify token/HMAC,
and stay idempotent on the incident id. The actual self-heal runs in
autosre.state_machine (deterministic; Gemini only decides).
"""
from __future__ import annotations

import hashlib
import hmac
import logging

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request

from autosre import config
from autosre.state_machine import run_self_heal

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("airbag")

app = FastAPI(title="Airbag — Cloud Run self-heal agent")

# Day-0 in-process idempotency. TODO: Firestore/Cloud SQL so it survives multi-instance
# and cold starts (see docs/PLAN.md — DatabaseSessionService).
_seen_incidents: set[str] = set()


@app.get("/healthz")
def healthz():
    return {"status": "ok", "mock": config.USE_MOCK}


@app.post("/alerts/cloud-monitoring", status_code=202)
async def cloud_monitoring_alert(request: Request, background_tasks: BackgroundTasks):
    token = request.query_params.get("token") or request.headers.get("x-airbag-token", "")
    if config.WEBHOOK_TOKEN and not hmac.compare_digest(token, config.WEBHOOK_TOKEN):
        raise HTTPException(status_code=401, detail="invalid token")

    payload = await request.json()
    incident = payload.get("incident", {}) or {}
    incident_id = incident.get("incident_id") or incident.get("condition_name") or "unknown"
    state = incident.get("state", "open")
    labels = (incident.get("resource", {}) or {}).get("labels", {}) or {}
    service = labels.get("service_name") or config.TARGET_SERVICE

    # 'closed' may just mean traffic stopped, not "fixed" — handled by the verify/revert
    # path, not by kicking off another heal here.
    if state != "open":
        return {"status": "ignored", "reason": f"state={state}", "incident_id": incident_id}

    if incident_id in _seen_incidents:
        return {"status": "duplicate", "incident_id": incident_id}
    _seen_incidents.add(incident_id)

    background_tasks.add_task(run_self_heal, incident_id, service)
    return {"status": "accepted", "incident_id": incident_id, "service": service}


@app.post("/alerts/sentry", status_code=202)
async def sentry_alert(request: Request, background_tasks: BackgroundTasks):
    raw = await request.body()
    if config.SENTRY_SECRET:
        sig = request.headers.get("sentry-hook-signature", "")
        expected = hmac.new(config.SENTRY_SECRET.encode(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            raise HTTPException(status_code=401, detail="invalid signature")
    # TODO: map Sentry issue payload -> service + incident id, then run_self_heal.
    return {"status": "todo"}
