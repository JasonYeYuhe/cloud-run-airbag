"""Airbag MCP server — exposes the autonomous Cloud Run rollback agent as MCP tools so ANY MCP
client (Claude Desktop, Cursor, another agent) can observe and drive incident response.

Transport: stdio (the standard local MCP transport). It proxies Airbag's HTTP API, so it works
against the live Cloud Run agent or a local `./run-local.sh` instance — set:

    AIRBAG_URL          base URL of the agent (default = the deployed agent)
    AIRBAG_DEMO_TOKEN   required only for the ACTION tools (read tools are watch-only / public)

Add to Claude Desktop (claude_desktop_config.json):

    "airbag": {
      "command": "python",
      "args": ["/abs/path/to/cloud-run-airbag/mcp-server/airbag_mcp.py"],
      "env": {"AIRBAG_URL": "https://airbag-agent-...run.app", "AIRBAG_DEMO_TOKEN": "<token>"}
    }

Read tools (safe, no token): airbag_health, airbag_incidents, airbag_incident,
airbag_incident_proof, airbag_autonomy, airbag_memory. Action tools (need AIRBAG_DEMO_TOKEN):
airbag_trigger_heal, airbag_approve, airbag_set_autonomy, airbag_break, airbag_reset.
"""
from __future__ import annotations

import os
from typing import Literal
from urllib.parse import quote

import httpx
from mcp.server.fastmcp import FastMCP

AIRBAG_URL = os.getenv("AIRBAG_URL", "https://airbag-agent-946577240607.asia-northeast1.run.app").rstrip("/")
DEMO_TOKEN = os.getenv("AIRBAG_DEMO_TOKEN", "")
TIMEOUT = float(os.getenv("AIRBAG_MCP_TIMEOUT", "30"))

mcp = FastMCP("airbag")


def _err(r) -> dict | None:
    """Sanitized error for a non-2xx — NEVER raise (httpx exceptions embed the request, incl. the
    token-bearing header) and never echo our request, only the agent's own response body."""
    if r.status_code == 401:
        return {"error": "unauthorized — check AIRBAG_DEMO_TOKEN"}
    if r.status_code >= 400:
        return {"error": f"airbag returned HTTP {r.status_code}", "detail": r.text[:500]}
    return None


def _get(path: str, params: dict | None = None):
    try:
        r = httpx.get(f"{AIRBAG_URL}{path}", params=params, timeout=TIMEOUT)
    except httpx.RequestError as e:
        return {"error": f"could not reach airbag at {AIRBAG_URL}", "detail": type(e).__name__}
    return _err(r) or r.json()


def _post(path: str, *, params: dict | None = None, json: dict | None = None):
    if not DEMO_TOKEN:
        return {"error": "AIRBAG_DEMO_TOKEN is not set — required for action tools"}
    try:
        r = httpx.post(f"{AIRBAG_URL}{path}", params=params, json=json,
                       headers={"x-airbag-demo-token": DEMO_TOKEN}, timeout=TIMEOUT)
    except httpx.RequestError as e:
        return {"error": f"could not reach airbag at {AIRBAG_URL}", "detail": type(e).__name__}
    return _err(r) or r.json()


# --- read tools (watch-only) ---------------------------------------------------------
@mcp.tool()
def airbag_health() -> dict:
    """Airbag agent health: backend (gcp/local/mock) + whether Gemini is configured."""
    return _get("/health")


@mcp.tool()
def airbag_incidents(limit: int = 20) -> dict:
    """List recent Airbag incidents (self-heal runs) with their outcomes (mitigated / escalated /
    awaiting_approval / observed), the revision rolled back to, the fix PR, and before/after 5xx."""
    return _get("/incidents", params={"limit": max(1, min(limit, 200))})


@mcp.tool()
def airbag_incident(incident_id: str) -> dict:
    """Full evidence for one incident: the ADK/Gemini decision, the signals it acted on, the
    before/after error rate, and the complete stage-by-stage thought-chain timeline."""
    return _get(f"/incidents/{quote(incident_id, safe='')}")


@mcp.tool()
def airbag_incident_proof(incident_id: str) -> dict:
    """The tamper-evident PROOF BUNDLE for one incident (A2A-consumable): a canonical stitch of the
    decision, detection signals, causal pre-check, recovery proof (incl. Alert→Verified-Recovery
    seconds), fix PR, and the FSM transition log, plus a sha256 content DIGEST. Verify integrity by
    recomputing sha256 over the canonical bundle and comparing — it is NOT a cryptographic signature."""
    return _get(f"/incidents/{quote(incident_id, safe='')}/proof")


@mcp.tool()
def airbag_autonomy() -> dict:
    """The target service's autonomy level (L0 observe / L1 approve-rollback / L2 gate-fix /
    L3 full), the trust-ramp streak, and any approvals currently awaiting a human."""
    return _get("/autonomy")


@mcp.tool()
def airbag_memory() -> dict:
    """Cross-incident memory for the target service: the learned per-service baseline 5xx rate,
    how many incidents are remembered, and the recent failure history (for recurrence)."""
    return _get("/memory")


# --- action tools (require AIRBAG_DEMO_TOKEN) ----------------------------------------
@mcp.tool()
def airbag_trigger_heal() -> dict:
    """Trigger a self-heal on the target service now (detect → decide → roll back → verify → fix-PR).
    Returns the incident_id; follow its progress with airbag_incident."""
    return _post("/demo/heal")


@mcp.tool()
def airbag_approve(incident_id: str, approve: bool) -> dict:
    """Resolve an incident awaiting a human decision: pass approve=True to APPROVE the gated
    rollback (autonomy L1) or fix-PR (L2) so the agent acts, or approve=False to DENY it (no
    action). `approve` is required — decide explicitly. Use airbag_autonomy to see what's pending."""
    return _post("/demo/approve", params={"incident_id": incident_id, "approve": approve})


@mcp.tool()
def airbag_set_autonomy(level: Literal["L0", "L1", "L2", "L3"],
                        service: str = "airbag-target") -> dict:
    """Set a service's autonomy level: L0 (observe only), L1 (approve before rollback),
    L2 (auto-rollback, approve the fix-PR), or L3 (fully autonomous)."""
    return _post(f"/autonomy/{quote(service, safe='')}", params={"level": level})


@mcp.tool()
def airbag_break() -> dict:
    """DEMO: route the target's traffic to a known-bad revision (inject a fault) so a heal can run."""
    return _post("/demo/break")


@mcp.tool()
def airbag_reset() -> dict:
    """DEMO: reset the target back to the healthy revision serving 100%."""
    return _post("/demo/reset")


if __name__ == "__main__":
    mcp.run()  # stdio transport
