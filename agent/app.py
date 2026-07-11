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
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from autosre import autonomy, config, events, incidents, memory, queue, report, state_store, tools
from autosre.state_machine import apply_approval, complete_rollback, run_self_heal

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("airbag")

# Remote MCP (streamable-HTTP at /mcp) is opt-in (AIRBAG_MCP_HTTP); when on, the agent itself is an
# MCP server. Wrapped in try/except: a broken MCP layer must NOT take down the dashboard / core agent.
_mcp_remote = None
if config.MCP_HTTP_ENABLED:
    try:
        from autosre import mcp_remote as _mcp_remote
    except Exception:  # noqa: BLE001
        logging.getLogger("airbag").exception("remote MCP failed to load — serving without /mcp")
        _mcp_remote = None


@asynccontextmanager
async def _lifespan(_app):
    events.start_subscriber()  # cross-instance event fan-out (no-op unless AIRBAG_EVENTS=pubsub)
    if _mcp_remote is not None:
        async with _mcp_remote.mcp.session_manager.run():  # MCP streamable-HTTP needs this running
            yield
    else:
        yield


app = FastAPI(title="Airbag — Cloud Run self-heal agent", lifespan=_lifespan)
if _mcp_remote is not None:
    app.mount("/mcp", _mcp_remote.gated_mcp_app)  # Bearer AIRBAG_MCP_TOKEN
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
def list_incidents(limit: int = 50):
    return {"incidents": incidents.list_recent(max(1, min(limit, 200)))}


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


@app.get("/incidents/{incident_id}/proof")
def incident_proof(incident_id: str):
    """Tamper-evident proof bundle: a canonical stitch of the incident evidence + a sha256 content
    digest an auditor / another agent can verify. See autosre.proof (integrity, not a signature)."""
    from autosre import proof
    rec = incidents.get(incident_id)
    if not rec:
        raise HTTPException(status_code=404, detail="unknown incident")
    # v5 4.2: serve the SIGNED snapshot persisted at MITIGATED/CLOSED (signed once at the decision
    # moment; the record mutates later). Falls back to a live digest-only build for pre-4.2 incidents.
    if rec.get("proof"):
        return rec["proof"]
    return proof.build(rec)


# v6 Phase 2 — the read-only transparency-log seam the auditor walks. The auditor is HTTPS-only with NO
# Firestore access (independence), and the log lives in the agent's Firestore, so it CANNOT walk the
# chain without these routes. Both are additive + read-only -> the recorded demo stays byte-identical.
@app.get("/transparency/head")
def transparency_head():
    """The log's head pointer. The auditor uses `seq` to know how far to walk and `prev_entry_hash` to
    confirm the last entry hashes to it. An empty log reports seq 0 (never 404). The internal
    idempotency cache (recent_pairs) is NOT exposed — only what the auditor walks."""
    from autosre import transparency
    h = transparency.head()
    if not h:
        return {"seq": 0, "prev_entry_hash": transparency.GENESIS, "empty": True}
    return {"seq": h.get("seq"), "prev_entry_hash": h.get("prev_entry_hash"),
            "updated_at": h.get("updated_at")}


@app.get("/transparency/log")
def transparency_log(from_seq: int = Query(1, alias="from"), to_seq: int | None = Query(None, alias="to")):
    """The immutable log entries in [from, to] (ascending), each with its full chained core + entry_hash
    for the auditor to recompute the links. Page size is capped so a huge range can't wedge a response;
    the auditor paginates. Read-only, LLM-free."""
    from autosre import transparency
    lo = max(1, from_seq)
    hi = lo + 999 if to_seq is None else min(to_seq, lo + 999)   # <=1000 entries/page
    return {"entries": transparency.entries(lo, hi)}


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
    requests a real incident would produce.

    v5 Phase 1.2 PIN: this client is DELIBERATELY UNMARKED — it SIMULATES USERS, not Airbag's
    diagnostics. Marking it with config.PROBE_HEADERS would make the self-traffic exclusion hide the
    very outage the demo is creating. The probe-marking guard test asserts _burst stays unmarked."""
    base = config.TARGET_BASE_URL.rstrip("/")
    with httpx.Client(timeout=5.0) as c:  # v5 1.2: UNMARKED on purpose — simulates USERS (see docstring)
        for _ in range(n):
            try:
                c.get(base + path)
            except Exception:  # noqa: BLE001
                pass


def _break(background_tasks: BackgroundTasks, prefer: str = "bug") -> dict:
    """Route the target to a bad revision. prefer='bug' (KeyError → 5xx, default) or 'slow' (the v3
    LATENCY regression: 200s past the SLO, ~0 5xx — a 5xx-only monitor misses it)."""
    res = tools.break_target(config.TARGET_SERVICE, config.GCP_REGION, prefer)
    if res.get("status") != "success":
        events.publish({"stage": "ESCALATED", "msg": f"break failed: {res.get('error')}",
                        "service": config.TARGET_SERVICE})
        return res
    fault = res.get("fault", prefer)
    if fault == "slow":
        msg = (f"bad revision {res.get('active_revision')} serving 100% — LATENCY regression: "
               f"{config.PROBE_PATH} returns 200 but slowly (> SLO), ~0 5xx")
    else:
        msg = (f"bad revision {res.get('active_revision')} serving 100% — "
               f"KeyError on {config.PROBE_PATH} → HTTP 500")
    events.publish({"stage": "FAULT_INJECTED", "msg": msg,
                    "service": config.TARGET_SERVICE})
    # 5xx needs real failing traffic to be visible; the latency detector actively samples the
    # serving revision itself (sample_latency_windows), so a slow-request burst is unnecessary
    # (and would serialize at SLOW_DELAY_S each).
    if config.BACKEND == "gcp" and fault != "slow":
        background_tasks.add_task(_burst, config.PROBE_PATH, config.DEMO_BURST_N)
    return res


@app.post("/demo/break", dependencies=[Depends(require_demo_token)])
def demo_break(background_tasks: BackgroundTasks):
    return _break(background_tasks)


@app.post("/demo/break-latency", dependencies=[Depends(require_demo_token)])
def demo_break_latency(background_tasks: BackgroundTasks):
    """v3 latency-regression demo: route to the `slow` revision (200s past the SLO, ~0 5xx) so the
    multi-signal latency detector — not a 5xx monitor — is what catches and heals it."""
    return _break(background_tasks, prefer="slow")


@app.post("/demo/heal", dependencies=[Depends(require_demo_token)])
def demo_heal(background_tasks: BackgroundTasks):
    incident_id = f"inc-{uuid.uuid4().hex[:8]}"
    queue.enqueue_heal(background_tasks, incident_id, config.TARGET_SERVICE)
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
        # A flake here (run_self_heal re-raises on transient failure by design, and Starlette's
        # BackgroundTasks SWALLOWS it) would otherwise leave the marquee demo frozen on
        # FAULT_INJECTED with the target still broken. Emit ESCALATED + restore the target so the
        # dashboard always reaches a terminal state and the target is left healthy.
        try:
            if is_gcp:
                _burst(config.PROBE_PATH, config.DEMO_BURST_N)
            time.sleep(config.DEMO_HEAL_DELAY_S if is_gcp else 1.2)
            run_self_heal(incident_id, config.TARGET_SERVICE)
        except Exception as e:  # noqa: BLE001 — never let the one-click demo strand the target
            log.exception("demo/run break_then_heal failed for %s", incident_id)
            events.publish({"stage": "ESCALATED", "incident_id": incident_id,
                            "service": config.TARGET_SERVICE,
                            "msg": f"one-click demo failed ({e}) — restoring the target to healthy"})
            try:
                tools.reset_target(config.TARGET_SERVICE, config.GCP_REGION)
            except Exception:  # noqa: BLE001 — best effort; a background task must never raise
                log.exception("demo/run reset_target failed for %s", incident_id)

    background_tasks.add_task(break_then_heal)
    return {"status": "accepted", "incident_id": incident_id,
            "broke_to": res.get("active_revision")}


@app.post("/demo/run-latency", dependencies=[Depends(require_demo_token)])
def demo_run_latency(background_tasks: BackgroundTasks):
    """One-click v3 LATENCY demo: route to the `slow` revision (200s past the SLO, ~0 5xx), then heal.
    No 5xx burst — the latency detector actively samples the serving revision; the multi-signal engine
    (not a 5xx monitor) is what catches it, and the rollback to the healthy revision IS the remedy."""
    res = tools.break_target(config.TARGET_SERVICE, config.GCP_REGION, "slow")
    if res.get("status") != "success":
        events.publish({"stage": "ESCALATED", "msg": f"break failed: {res.get('error')}",
                        "service": config.TARGET_SERVICE})
        return res
    events.publish({"stage": "FAULT_INJECTED",
                    "msg": f"bad revision {res.get('active_revision')} serving 100% — LATENCY regression: "
                           f"{config.PROBE_PATH} returns 200 but slowly (> SLO), ~0 5xx",
                    "service": config.TARGET_SERVICE})
    incident_id = f"inc-{uuid.uuid4().hex[:8]}"

    def break_then_heal():
        # mirror /demo/run's safety net: always reach a terminal state + leave the target healthy
        try:
            time.sleep(2.0)  # brief settle; the latency detector samples the serving revision live
            run_self_heal(incident_id, config.TARGET_SERVICE)
        except Exception as e:  # noqa: BLE001 — never let the one-click demo strand the target
            log.exception("demo/run-latency break_then_heal failed for %s", incident_id)
            events.publish({"stage": "ESCALATED", "incident_id": incident_id,
                            "service": config.TARGET_SERVICE,
                            "msg": f"one-click latency demo failed ({e}) — restoring the target to healthy"})
            try:
                tools.reset_target(config.TARGET_SERVICE, config.GCP_REGION)
            except Exception:  # noqa: BLE001 — best effort; a background task must never raise
                log.exception("demo/run-latency reset_target failed for %s", incident_id)

    background_tasks.add_task(break_then_heal)
    return {"status": "accepted", "incident_id": incident_id,
            "broke_to": res.get("active_revision"), "scenario": "latency"}


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
    """Gate the machine-to-machine endpoint with the webhook token (CI holds it). HEADER-ONLY
    (Phase 0.6): a `?token=` query string persists the secret in Cloud Run/LB request logs + GCP
    audit logs. CI already sends the header (complete-rollback.yml)."""
    _gate(config.WEBHOOK_TOKEN, request.headers.get("x-airbag-token", ""), "webhook token")


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


# --- Cloud Tasks worker: the durable-queue target that actually runs the heal -----------
def require_run_token(request: Request) -> None:
    """Gate the Cloud-Tasks-facing worker with a DEDICATED token (not the webhook token — keeps the
    blast radius of the heal-trigger credential separate). HEADER-ONLY (Phase 0.6): no `?token=` in
    logs; Cloud Tasks already sends the header (queue.py). OIDC is the production hardening."""
    _gate(config.INTERNAL_TOKEN, request.headers.get("x-airbag-internal-token", ""), "internal token")


@app.post("/internal/run-heal", dependencies=[Depends(require_run_token)])
async def internal_run_heal(request: Request, response: Response):
    """Cloud Tasks delivers a heal here (at-least-once). Runs run_self_heal synchronously, which is
    idempotent per incident_id (a redelivery while running, or after done, returns a cheap no-op).
    Returns 2xx for ANY terminal decision (a fresh delivery shouldn't redeliver); only a genuine
    transient failure returns 5xx so Cloud Tasks retries."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    incident_id = body.get("incident_id") or ""
    service = body.get("service") or config.TARGET_SERVICE
    if not incident_id:
        raise HTTPException(status_code=400, detail="incident_id required")
    if service != config.TARGET_SERVICE:  # allowlist — never let an arbitrary service be mutated
        raise HTTPException(status_code=400, detail=f"service {service!r} not allowed")
    try:
        return await run_in_threadpool(run_self_heal, incident_id, service)
    except Exception as e:  # noqa: BLE001
        log.exception("run-heal failed for %s", incident_id)
        response.status_code = 500  # transient -> redeliver (the heal lease was released)
        return {"status": "error", "error": str(e), "incident_id": incident_id}


# --- graduated autonomy: per-service trust level + the L1/L2 approval gate ---------------
@app.get("/autonomy")
def autonomy_status():
    """Watch-only: the target service's autonomy level, trust-ramp streak, and any pending
    approvals waiting on a human."""
    return {"default_level": config.AUTONOMY_LEVEL,
            "service": autonomy.status(config.TARGET_SERVICE),
            "pending_approvals": autonomy.pending_approvals()}


@app.get("/memory")
def memory_status():
    """Watch-only: the per-service learned baseline + cross-incident memory (count, recent
    failures, recurrence)."""
    return memory.summary(config.TARGET_SERVICE)


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
def _alert_token(request: Request) -> str:
    """The webhook token for the alert channel, preferring a HEADER (Phase 0.6 — no secret in URL
    logs). Cloud Monitoring `webhook_basicauth` sends `Authorization: Basic base64(user:token)`; we
    also accept `x-airbag-token`. `?token=` is a DEPRECATED fallback (logged) so a pre-existing
    `webhook_tokenauth` channel keeps working until it's re-created via infra/alert-setup.sh."""
    auth = request.headers.get("authorization", "")
    if auth[:6].lower() == "basic ":
        import base64
        try:
            decoded = base64.b64decode(auth[6:].strip()).decode("utf-8")
        except Exception:  # noqa: BLE001
            decoded = ""
        return decoded.split(":", 1)[1] if ":" in decoded else decoded
    header = request.headers.get("x-airbag-token", "")
    if header:
        return header
    legacy = request.query_params.get("token", "")
    if legacy:
        log.warning("alert webhook used the DEPRECATED ?token= query param — re-run "
                    "infra/alert-setup.sh to switch the channel to webhook_basicauth (header auth)")
    return legacy


@app.post("/alerts/cloud-monitoring", status_code=202)
async def cloud_monitoring_alert(request: Request, background_tasks: BackgroundTasks):
    try:
        _gate(config.WEBHOOK_TOKEN, _alert_token(request), "webhook token")
    except HTTPException as e:  # RFC2617 challenge so a webhook_basicauth channel negotiates cleanly
        if e.status_code == 401:
            raise HTTPException(status_code=401, detail=e.detail,
                                headers={"WWW-Authenticate": 'Basic realm="airbag"'}) from None
        raise

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

    queue.enqueue_heal(background_tasks, incident_id, service)
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
    queue.enqueue_heal(background_tasks, incident_id, config.TARGET_SERVICE)
    return {"status": "accepted", "incident_id": incident_id}
