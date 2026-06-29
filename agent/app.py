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
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from autosre import autonomy, config, events, incidents, report, state_store, tools
from autosre.state_machine import apply_approval, complete_rollback, run_self_heal

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("airbag")

app = FastAPI(title="Airbag — Cloud Run self-heal agent")
_DASHBOARD = (Path(__file__).parent / "static" / "dashboard.html").read_text(encoding="utf-8")


# --- dashboard --------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def dashboard():
    return _DASHBOARD


@app.get("/health")  # NB: Cloud Run's frontend reserves /healthz, so use /health
def health():
    return {"status": "ok", "backend": config.BACKEND, "gemini": bool(config.GEMINI_API_KEY)}


# --- incident reports (read-only Artifact; safe to be public) ----------------
@app.get("/incidents")
def list_incidents():
    return {"incidents": incidents.list_recent()}


@app.get("/incidents/{incident_id}")
def get_incident(incident_id: str):
    rec = incidents.get(incident_id)
    if not rec:
        raise HTTPException(status_code=404, detail="unknown incident")
    return rec


@app.get("/incidents/{incident_id}/report", response_class=HTMLResponse)
def incident_report(incident_id: str):
    rec = incidents.get(incident_id)
    if not rec:
        raise HTTPException(status_code=404, detail="unknown incident")
    return report.render(rec)


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


# --- auth -------------------------------------------------------------------
def _gate(configured: str, supplied: str, what: str) -> None:
    """Constant-time token check that FAILS CLOSED in production: if the token isn't
    configured, open only for local/mock dev — on the public gcp service refuse to serve
    (a blank secret must never silently expose the heal/PR/rollback control plane)."""
    if not configured:
        if config.BACKEND == "gcp":
            raise HTTPException(status_code=503,
                                detail=f"{what} not configured — refusing unauthenticated on gcp")
        return  # local/mock dev: open
    if not (supplied and hmac.compare_digest(supplied, configured)):
        raise HTTPException(status_code=401, detail=f"invalid or missing {what}")


def require_demo_token(request: Request) -> None:
    """Gate the /demo/* ACTION endpoints (header preferred, ?token= fallback for one-click
    deep links). The read-only dashboard + SSE + /incidents stay public."""
    supplied = request.headers.get("x-airbag-demo-token") or request.query_params.get("token", "")
    _gate(config.DEMO_TOKEN, supplied, "demo token")


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


@app.post("/demo/complete-rollback", dependencies=[Depends(require_demo_token)])
def demo_complete_rollback(background_tasks: BackgroundTasks):
    """Dashboard 'Verify & Undo Rollback' button: verify the deployed fix, restore traffic
    to it, close the transaction (or compensate). Half-automated trigger for the demo."""
    background_tasks.add_task(complete_rollback, config.TARGET_SERVICE, None, None, None)
    return {"status": "accepted", "service": config.TARGET_SERVICE}


# legacy aliases (scripts/gcp-demo.sh + older docs)
@app.post("/demo/inject", dependencies=[Depends(require_demo_token)])
def demo_inject(background_tasks: BackgroundTasks):
    return _break(background_tasks)


@app.post("/demo/trigger", dependencies=[Depends(require_demo_token)])
def demo_trigger(background_tasks: BackgroundTasks):
    return demo_heal(background_tasks)


# --- P1: CI-triggered transaction close (the fix PR's CI calls this after deploy) -------
def require_internal_token(request: Request) -> None:
    """Gate the machine-to-machine endpoint with the webhook token (CI holds it)."""
    supplied = request.headers.get("x-airbag-token") or request.query_params.get("token", "")
    _gate(config.WEBHOOK_TOKEN, supplied, "webhook token")


@app.post("/internal/complete-rollback", dependencies=[Depends(require_internal_token)])
async def internal_complete_rollback(request: Request, response: Response):
    """Called by the fix PR's GitHub Action after it deploys the fix (--no-traffic). Body (all
    optional): {service, revision, git_sha, pr_url}. Verifies the fix is healthy BEFORE shifting
    production traffic to it; compensates back to the safe revision on failure. Runs synchronously
    so CI sees the real outcome: 2xx = CLOSED (or idempotent no-op); 422 = compensated / manual
    intervention. Idempotent — a duplicate call while one is running, or after close, is a no-op."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    service = body.get("service") or config.TARGET_SERVICE
    result = await run_in_threadpool(complete_rollback, service, body.get("revision"),
                                     body.get("git_sha"), body.get("pr_url"))
    response.status_code = {"closed": 200, "noop": 200,
                            "compensated": 422, "manual_intervention": 422}.get(
                                result.get("status"), 200)
    return result


# --- graduated autonomy: per-service trust level + the L1/L2 approval gate ---------------
@app.get("/autonomy")
def autonomy_status():
    """Watch-only: the target service's autonomy level, trust-ramp streak, and any pending
    approvals waiting on a human."""
    return {"default_level": config.AUTONOMY_LEVEL,
            "service": autonomy.status(config.TARGET_SERVICE),
            "pending_approvals": autonomy.pending_approvals()}


@app.post("/autonomy/{service}", dependencies=[Depends(require_demo_token)])
async def autonomy_set(service: str, request: Request):
    """Set a service's autonomy level (L0 observe / L1 approve-rollback / L2 gate-fix / L3 full)."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    level = (body.get("level") or request.query_params.get("level") or "").upper()
    try:
        rec = autonomy.set_level(service, level)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    events.publish({"stage": "AUTONOMY", "msg": f"{service} autonomy set to {level}",
                    "service": service})
    return {"status": "ok", "autonomy": rec}


@app.post("/demo/approve", dependencies=[Depends(require_demo_token)])
def demo_approve(background_tasks: BackgroundTasks, incident_id: str, approve: bool = True):
    """Dashboard Approve/Deny for a gated rollback (L1) or fix-PR (L2)."""
    background_tasks.add_task(apply_approval, incident_id, approve)
    return {"status": "accepted", "incident_id": incident_id, "approve": approve}


@app.post("/internal/approve", dependencies=[Depends(require_internal_token)])
async def internal_approve(request: Request, response: Response):
    """Machine approval (e.g. a ChatOps bot). Body: {incident_id, approve}. Runs synchronously so
    the caller sees the real outcome."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    incident_id = body.get("incident_id") or request.query_params.get("incident_id", "")
    approve = bool(body.get("approve", True))
    result = await run_in_threadpool(apply_approval, incident_id, approve)
    # noop = the approval expired or was never queued -> 409 so the caller knows nothing happened
    # (don't let an expired L1 approval read as success while the bad revision keeps serving).
    response.status_code = {"mitigated": 200, "denied": 200, "escalated": 422,
                            "noop": 409}.get(result.get("status"), 200)
    return result


# --- production webhooks -----------------------------------------------------
@app.post("/alerts/cloud-monitoring", status_code=202)
async def cloud_monitoring_alert(request: Request, background_tasks: BackgroundTasks):
    token = request.query_params.get("token") or request.headers.get("x-airbag-token", "")
    _gate(config.WEBHOOK_TOKEN, token, "webhook token")

    payload = await request.json()
    incident = payload.get("incident", {}) or {}
    incident_id = incident.get("incident_id") or incident.get("condition_name") or "unknown"
    state = incident.get("state", "open")
    labels = (incident.get("resource", {}) or {}).get("labels", {}) or {}
    service = labels.get("service_name") or config.TARGET_SERVICE

    if state != "open":
        return {"status": "ignored", "reason": f"state={state}", "incident_id": incident_id}
    # exactly-once dedup via the durable store (lazy expires_at; multi-instance safe)
    if state_store.seen_and_mark("dedup", incident_id, config.DEDUP_TTL_S):
        return {"status": "duplicate", "incident_id": incident_id}

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
