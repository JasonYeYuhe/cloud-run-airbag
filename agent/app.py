"""Airbag agent — FastAPI service (deploys to Cloud Run).

Responsibilities:
  - webhook intake (Cloud Monitoring / Sentry): 202-then-async, token/HMAC, idempotent
  - live "glassbox" dashboard + SSE thought-chain stream
  - demo harness: inject a fault into the target, then trigger the self-heal
The actual self-heal runs in autosre.state_machine (deterministic; Gemini only decides).
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
import uuid
from pathlib import Path

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse

from autosre import config, events
from autosre.state_machine import run_self_heal

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("airbag")

app = FastAPI(title="Airbag — Cloud Run self-heal agent")
_DASHBOARD = (Path(__file__).parent / "static" / "dashboard.html").read_text(encoding="utf-8")
_seen_incidents: set[str] = set()


# --- dashboard --------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def dashboard():
    return _DASHBOARD


@app.get("/healthz")
def healthz():
    return {"status": "ok", "backend": config.BACKEND, "gemini": bool(config.GEMINI_API_KEY)}


@app.get("/events")
async def events_stream():
    async def gen():
        idx = 0
        yield f"data: {json.dumps({'stage': 'CONNECTED', 'backend': config.BACKEND})}\n\n"
        while True:
            batch, idx = events.get_since(idx)
            for ev in batch:
                yield f"data: {json.dumps(ev)}\n\n"
            await asyncio.sleep(0.25)

    return StreamingResponse(gen(), media_type="text/event-stream")


# --- demo harness -----------------------------------------------------------
def _target(path: str, method: str = "post"):
    try:
        with httpx.Client(timeout=3.0) as c:
            return c.request(method, config.TARGET_BASE_URL.rstrip("/") + path)
    except Exception as e:  # noqa: BLE001
        log.warning("target call failed: %s", e)
        return None


@app.post("/demo/inject")
def demo_inject():
    _target("/__fault/http500")
    events.publish({"stage": "FAULT_INJECTED", "msg": "bad revision now returns 5xx on /api/orders",
                    "service": config.TARGET_SERVICE})
    return {"status": "fault injected"}


@app.post("/demo/reset")
def demo_reset():
    _target("/__fault/off")
    return {"status": "fault cleared"}


@app.post("/demo/trigger")
def demo_trigger(background_tasks: BackgroundTasks):
    incident_id = f"inc-{uuid.uuid4().hex[:8]}"
    background_tasks.add_task(run_self_heal, incident_id, config.TARGET_SERVICE)
    return {"status": "accepted", "incident_id": incident_id}


@app.post("/demo/run")
def demo_run(background_tasks: BackgroundTasks):
    """One-click demo: inject a fault, then trigger the self-heal a moment later."""
    _target("/__fault/http500")
    events.publish({"stage": "FAULT_INJECTED", "msg": "bad revision now returns 5xx on /api/orders",
                    "service": config.TARGET_SERVICE})
    incident_id = f"inc-{uuid.uuid4().hex[:8]}"

    def delayed():
        time.sleep(1.2)
        run_self_heal(incident_id, config.TARGET_SERVICE)

    background_tasks.add_task(delayed)
    return {"status": "accepted", "incident_id": incident_id}


# --- production webhooks -----------------------------------------------------
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
    return {"status": "todo"}
