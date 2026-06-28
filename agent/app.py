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
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse

from autosre import config, events, tools
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


@app.get("/health")  # NB: Cloud Run's frontend reserves /healthz, so use /health
def health():
    return {"status": "ok", "backend": config.BACKEND, "gemini": bool(config.GEMINI_API_KEY)}


@app.get("/events")
async def events_stream(request: Request):
    async def gen():
        idx = 0
        last_ping = time.time()
        yield f"data: {json.dumps({'stage': 'CONNECTED', 'backend': config.BACKEND})}\n\n"
        while True:
            if await request.is_disconnected():
                break
            batch, idx = events.get_since(idx)
            for ev in batch:
                yield f"data: {json.dumps(ev)}\n\n"
            if time.time() - last_ping > 15:
                last_ping = time.time()
                yield ": ping\n\n"
            await asyncio.sleep(0.25)

    return StreamingResponse(gen(), media_type="text/event-stream")


# --- demo auth --------------------------------------------------------------
def require_demo_token(request: Request) -> None:
    """Gate the /demo/* ACTION endpoints behind a shared token (header preferred,
    ?token= fallback for one-click deep links). When AIRBAG_DEMO_TOKEN is unset the
    endpoints are open (local demo); the read-only dashboard + SSE are always public."""
    if not config.DEMO_TOKEN:
        return
    supplied = request.headers.get("x-airbag-demo-token") or request.query_params.get("token", "")
    if not (supplied and hmac.compare_digest(supplied, config.DEMO_TOKEN)):
        raise HTTPException(status_code=401, detail="invalid or missing demo token")


# --- demo harness: repeatable Break → Heal → Reset (works on local + gcp) -----------
def _burst(path: str, n: int) -> None:
    """Generate real traffic (→ real 5xx) against the target so Cloud Logging/Monitoring
    detect the fault. On gcp 'break' only shifts traffic; these are the failing user
    requests a real incident would produce."""
    base = config.TARGET_BASE_URL.rstrip("/")
    with httpx.Client(timeout=5.0) as c:
        for _ in range(n):
            try:
                c.get(base + path)
            except Exception:  # noqa: BLE001
                pass


def _break(background_tasks: BackgroundTasks) -> dict:
    """Route the target to the bad revision (gcp) / toggle the KeyError (local), then make
    the 5xx visible to monitoring."""
    res = tools.break_target(config.TARGET_SERVICE, config.GCP_REGION)
    if res.get("status") != "success":
        events.publish({"stage": "ESCALATED", "msg": f"break failed: {res.get('error')}",
                        "service": config.TARGET_SERVICE})
        return res
    events.publish({"stage": "FAULT_INJECTED",
                    "msg": f"bad revision {res.get('active_revision')} serving 100% — "
                           f"KeyError on {config.PROBE_PATH} → HTTP 500",
                    "service": config.TARGET_SERVICE})
    if config.BACKEND == "gcp":
        background_tasks.add_task(_burst, config.PROBE_PATH, config.DEMO_BURST_N)
    return res


@app.post("/demo/break", dependencies=[Depends(require_demo_token)])
def demo_break(background_tasks: BackgroundTasks):
    return _break(background_tasks)


@app.post("/demo/heal", dependencies=[Depends(require_demo_token)])
def demo_heal(background_tasks: BackgroundTasks):
    incident_id = f"inc-{uuid.uuid4().hex[:8]}"
    background_tasks.add_task(run_self_heal, incident_id, config.TARGET_SERVICE)
    return {"status": "accepted", "incident_id": incident_id}


@app.post("/demo/reset", dependencies=[Depends(require_demo_token)])
def demo_reset():
    res = tools.reset_target(config.TARGET_SERVICE, config.GCP_REGION)
    events.publish({"stage": "DONE",
                    "msg": f"reset → healthy revision {res.get('active_revision')} serving 100%",
                    "service": config.TARGET_SERVICE})
    return res


@app.post("/demo/run", dependencies=[Depends(require_demo_token)])
def demo_run(background_tasks: BackgroundTasks):
    """One-click demo: break (route to bad revision + generate 5xx), then heal after a
    short delay — gcp needs ~log-ingestion time before the agent can detect the 5xx."""
    res = tools.break_target(config.TARGET_SERVICE, config.GCP_REGION)
    if res.get("status") != "success":
        events.publish({"stage": "ESCALATED", "msg": f"break failed: {res.get('error')}",
                        "service": config.TARGET_SERVICE})
        return res
    events.publish({"stage": "FAULT_INJECTED",
                    "msg": f"bad revision {res.get('active_revision')} serving 100% — "
                           f"KeyError on {config.PROBE_PATH} → HTTP 500",
                    "service": config.TARGET_SERVICE})
    incident_id = f"inc-{uuid.uuid4().hex[:8]}"
    is_gcp = config.BACKEND == "gcp"

    def break_then_heal():
        if is_gcp:
            _burst(config.PROBE_PATH, config.DEMO_BURST_N)
        time.sleep(config.DEMO_HEAL_DELAY_S if is_gcp else 1.2)
        run_self_heal(incident_id, config.TARGET_SERVICE)

    background_tasks.add_task(break_then_heal)
    return {"status": "accepted", "incident_id": incident_id,
            "broke_to": res.get("active_revision")}


# legacy aliases (scripts/gcp-demo.sh + older docs)
@app.post("/demo/inject", dependencies=[Depends(require_demo_token)])
def demo_inject(background_tasks: BackgroundTasks):
    return _break(background_tasks)


@app.post("/demo/trigger", dependencies=[Depends(require_demo_token)])
def demo_trigger(background_tasks: BackgroundTasks):
    return demo_heal(background_tasks)


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
    if len(_seen_incidents) > 1000:  # bound memory (Day-0; replace with TTLCache/Firestore)
        _seen_incidents.clear()
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
    incident_id = f"sentry-{uuid.uuid4().hex[:8]}"
    background_tasks.add_task(run_self_heal, incident_id, config.TARGET_SERVICE)
    return {"status": "accepted", "incident_id": incident_id}
