"""Airbag Auditor — FastAPI service (deploys to Cloud Run as a SECOND, independent service).

READ-ONLY / out-of-band: a background loop polls the agent's PUBLIC proof endpoints, independently
verifies each heal against a PINNED signer identity, and counter-signs the verdict with the auditor's
OWN KMS key. It NEVER writes to the agent — structurally it cannot block a heal. The status page is
the money-shot's second-browser surface: the tri-state flips on camera within one poll beat.

Service tier (denylist-guarded): fastapi / uvicorn / httpx + the sibling kernel / counter-signer /
poller — never agent code, never the LLM. Counter-signing is fail-open: with no auditor KMS key
configured (dev / pre-mint), attestations are emitted UNSIGNED rather than blocking the loop.
"""
from __future__ import annotations

import asyncio
import html
import logging
import time
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

import attestation
import config
import poller

_HERE = Path(__file__).resolve().parent

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("airbag.auditor")

# In-memory latest snapshot (the FLOOR is stateless-ish; Phase 2 adds AUDITOR-owned durable checkpoint
# storage). Keyed by incident_id -> counter-signed attestation envelope.
_STATE: dict = {"attestations": {}, "last_poll_at": None, "error": None}
# Persistent attestation cache across poll cycles ({incident_id: (raw_fetched_digest, env)}) — a
# published proof is immutable, so an unchanged incident reuses its counter-signature instead of a
# fresh KMS sign every cycle (keeps the poll fast + cheap; a tamper changes the bytes -> re-audit).
_CACHE: dict = {}


def _signer():
    """The auditor's REUSABLE counter-signer: KMS when configured (credentials created once + reused),
    else a fail-open no-op (UNSIGNED attestations in dev / before the airbag-auditor key is minted)."""
    key = config.AUDITOR_KMS_KEY
    if not key:
        return lambda digest: None
    return attestation.kms_signer(key)


async def _poll_loop():
    """Poll -> verify -> counter-sign, forever. Runs the blocking poll in a threadpool so it never
    stalls the event loop; swallows every error so a transient agent outage can't kill the loop. ALL
    setup lives INSIDE the guarded loop (an unreadable pinned PEM or a client-construction error must
    degrade + retry, never silently terminate the task while /health still reports ok)."""
    fetch = None
    signer = None
    while True:
        try:
            if fetch is None:
                fetch = poller.httpx_fetch(config.HTTP_TIMEOUT_S, max_bytes=config.MAX_BODY_BYTES,
                                           deadline_s=config.FETCH_DEADLINE_S)
            if signer is None:
                signer = _signer()
            pem = config.agent_pubkey_pem()   # re-read each cycle: a fixed-permission anchor self-heals
            res = await run_in_threadpool(
                poller.poll_once, fetch=fetch, agent_url=config.AGENT_PROOF_URL, expected_pem=pem,
                expected_key=config.EXPECTED_AGENT_KEY, signer=signer, now=time.time,
                limit=config.MAX_INCIDENTS, signed_not_before=config.SIGNED_NOT_BEFORE, cache=_CACHE)
            for stale in [k for k in _CACHE if k not in res]:   # bound the cache to the current window
                del _CACHE[stale]
            _STATE.update(attestations=res, last_poll_at=time.time(), error=None)
        except Exception as e:  # noqa: BLE001 — the out-of-band loop must NEVER die
            _STATE["error"] = str(e)
            log.exception("auditor poll loop error")
        await asyncio.sleep(config.POLL_INTERVAL_S)


@asynccontextmanager
async def _lifespan(_app):
    # Only poll when an agent URL is configured (so tests / a bare boot don't spin a network loop).
    task = asyncio.create_task(_poll_loop()) if config.AGENT_PROOF_URL else None
    try:
        yield
    finally:
        if task:
            task.cancel()


app = FastAPI(title="Airbag Auditor — independent heal attestation", lifespan=_lifespan)

# Serve the offline Proof Explorer (Phase 4) + the committed proof fixtures live from the auditor, so a
# judge clicks from an attestation card straight to a client-side "verify it yourself". Staged into the
# image at deploy (infra/auditor-deploy.sh copies docs/explorer + docs/proof); mounted only when present
# so tests / a bare boot don't require them. The Explorer is byte-identical to docs/explorer (a copy).
for _sub, _at in (("explorer", "/explorer"), ("proof", "/proof")):
    if (_HERE / _sub).is_dir():
        app.mount(_at, StaticFiles(directory=str(_HERE / _sub), html=True), name=_sub)
EXPLORER_SERVED = (_HERE / "explorer").is_dir()


@app.get("/health")
def health():
    return {"ok": True, "role": "auditor", "agent_url": config.AGENT_PROOF_URL or None,
            "expected_agent_key": config.EXPECTED_AGENT_KEY,
            "counter_signing": bool(config.AUDITOR_KMS_KEY),
            "last_poll_at": _STATE["last_poll_at"], "attested": len(_STATE["attestations"])}


@app.get("/attestations")
def attestations():
    return {"last_poll_at": _STATE["last_poll_at"], "error": _STATE["error"],
            "count": len(_STATE["attestations"]),
            "attestations": list(_STATE["attestations"].values())}


@app.get("/attestations/{incident_id}")
def attestation_for(incident_id: str):
    env = _STATE["attestations"].get(incident_id)
    if not env:
        raise HTTPException(status_code=404, detail="no attestation for that incident")
    return env


@app.get("/", response_class=HTMLResponse)
def status_page():
    return _status_html(_STATE)


# --- status page (self-contained; server-rendered + meta-refresh so a flip lands in one camera beat) -
_TRI = {
    "SIGNED-VERIFIED": ("#0b8457", "#e6f6ef", "provenance + integrity confirmed against the pinned key"),
    "INTEGRITY-ONLY": ("#2b6cb0", "#e9f0fb", "unsigned (pre-signing) — integrity confirmed, no provenance"),
    "DEGRADED": ("#b7791f", "#fdf3e0", "a signature was EXPECTED but is absent — strip or KMS hiccup"),
    "FAIL": ("#c53030", "#fbe8e8", "integrity broken, wrong signer, or wrong incident"),
}


def _card(env: dict) -> str:
    b = env.get("bundle", {}) if isinstance(env, dict) else {}
    tri = b.get("tri_state", "FAIL")
    fg, bg, blurb = _TRI.get(tri, ("#444", "#eee", ""))
    fetch = b.get("fetch", {}) or {}
    signed = "🖊 counter-signed" if "signature" in env else "⚠ unsigned (unattested)"
    esc = html.escape
    return f"""
    <div class="card" style="border-left:6px solid {fg}">
      <div class="row"><span class="iid">{esc(str(b.get('incident_id')))}</span>
        <span class="badge" style="color:{fg};background:{bg}">{esc(tri)}</span></div>
      <div class="blurb">{esc(blurb)}</div>
      <table>
        <tr><td>integrity</td><td>{b.get('integrity_ok')}</td>
            <td>signature</td><td>{b.get('signature_ok')}</td>
            <td>signer pinned</td><td>{b.get('signer_pinned')}</td></tr>
        <tr><td>verified signer</td><td colspan="5" class="mono">{esc(str(b.get('verified_signer')))}</td></tr>
        <tr><td>subject digest</td><td colspan="5" class="mono">{esc(str(b.get('subject_digest')))}</td></tr>
        <tr><td>fetch</td><td colspan="5" class="mono">{esc(str(fetch.get('agent_url')))} · id-match
            {fetch.get('incident_id_match')} · HTTP {esc(str(fetch.get('http_status')))}</td></tr>
      </table>
      <div class="sig">{signed}</div>
    </div>"""


def _status_html(state: dict) -> str:
    cards = "".join(_card(e) for e in state.get("attestations", {}).values()) \
        or '<div class="empty">no attestations yet — waiting for the first poll…</div>'
    last = state.get("last_poll_at")
    last_s = time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime(last)) if last else "never"
    err = state.get("error")
    err_html = f'<div class="err">poll error: {html.escape(str(err))}</div>' if err else ""
    refresh = max(2, int(config.POLL_INTERVAL_S) - 2)
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="{refresh}">
<title>Airbag Auditor</title><style>
 body{{font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;margin:0;background:#0f1720;color:#e6edf3}}
 header{{padding:18px 24px;background:#111d2b;border-bottom:1px solid #22303f}}
 h1{{margin:0;font-size:18px}} .sub{{color:#9fb3c8;font-size:12px;margin-top:4px}}
 .mono{{font-family:ui-monospace,Menlo,monospace;font-size:11px;color:#9fb3c8;word-break:break-all}}
 main{{padding:18px 24px;display:grid;gap:14px;max-width:900px}}
 .card{{background:#111d2b;border:1px solid #22303f;border-radius:8px;padding:14px 16px}}
 .row{{display:flex;justify-content:space-between;align-items:center}}
 .iid{{font-weight:600;font-size:15px}}
 .badge{{font-weight:700;padding:4px 10px;border-radius:999px;font-size:13px}}
 .blurb{{color:#9fb3c8;font-size:12px;margin:6px 0 10px}}
 table{{width:100%;border-collapse:collapse}} td{{padding:2px 8px 2px 0;font-size:12px;color:#c6d2de}}
 .sig{{margin-top:8px;font-size:12px;color:#9fb3c8}}
 .empty{{color:#9fb3c8;padding:24px;text-align:center}}
 .err{{color:#f6a5a5;font-size:12px;margin-top:6px}}
</style></head><body>
<header><h1>🛡 Airbag Auditor — independent heal attestation</h1>
 <div class="sub">pinned agent signer <span class="mono">{html.escape(config.EXPECTED_AGENT_KEY)}</span></div>
 <div class="sub">counter-signing: {"ON (auditor KMS key)" if config.AUDITOR_KMS_KEY else "OFF (unsigned — dev)"}
   · last poll {last_s} · auditing {html.escape(config.AGENT_PROOF_URL or "(no agent configured)")}
   {'· <a href="/explorer" style="color:#58a6ff">🔎 verify these yourself (zero-network, in-browser)</a>' if EXPLORER_SERVED else ''}</div>
 {err_html}
</header><main>{cards}</main></body></html>"""
