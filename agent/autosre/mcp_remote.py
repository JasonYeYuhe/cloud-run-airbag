"""Remote MCP server mounted on the Cloud Run agent (streamable-HTTP at /mcp), behind the
AIRBAG_MCP_HTTP flag. Any MCP client can point at https://<agent>/mcp/ with
`Authorization: Bearer <AIRBAG_INTERNAL_TOKEN>` and drive incident response — the same capability
as the stdio server (mcp-server/), but the deployed agent IS the MCP server (no local proxy).

Tools call the agent's functions IN-PROCESS. Read tools are synchronous + fast; the slow actions
(a heal, an L1 approval that re-runs the rollback) are spawned on a daemon thread so the MCP call
returns immediately — mirroring the webhook's fire-and-forget. The mount is Bearer-gated because the
agent is public (the dashboard is watch-only; driving Airbag needs the dedicated internal token).
"""
from __future__ import annotations

import hmac
import threading
import uuid
from contextlib import asynccontextmanager
from typing import Literal

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse

from . import autonomy, config, incidents, memory, queue
from .state_machine import apply_approval

# DNS-rebinding protection guards localhost-bound MCP servers from a malicious web page using the
# victim's browser; it rejects unknown Host headers (a Cloud Run run.app host -> HTTP 421). For a
# REMOTE, Bearer-gated server it doesn't apply (a rebinding browser has no token -> 401 at the gate),
# so we disable the Host check and rely on BearerGate for auth.
mcp = FastMCP("airbag", streamable_http_path="/",
              transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))


@mcp.tool()
def airbag_incidents(limit: int = 20) -> dict:
    """List recent Airbag incidents (self-heal runs) with their outcomes, rollback target, fix PR,
    and before/after 5xx."""
    return {"incidents": incidents.list_recent(max(1, min(limit, 200)))}


@mcp.tool()
def airbag_incident(incident_id: str) -> dict:
    """Full evidence for one incident: the ADK/Gemini decision, the signals, before/after metrics,
    and the complete thought-chain timeline."""
    return incidents.get(incident_id) or {"error": f"incident {incident_id} not found"}


@mcp.tool()
def airbag_autonomy() -> dict:
    """The target service's autonomy level (L0–L3), trust-ramp streak, and any pending approvals."""
    return {"service": autonomy.status(config.TARGET_SERVICE),
            "pending_approvals": autonomy.pending_approvals()}


@mcp.tool()
def airbag_memory() -> dict:
    """The learned per-service baseline 5xx rate + cross-incident memory (count, recent failures)."""
    return memory.summary(config.TARGET_SERVICE)


@mcp.tool()
def airbag_trigger_heal() -> dict:
    """Trigger a self-heal on the target service now (runs in the background). Returns the
    incident_id; follow it with airbag_incident."""
    iid = f"mcp-{uuid.uuid4().hex[:8]}"
    # route through the durable queue (Cloud Tasks when enabled) so the heal survives an instance
    # recycle + gets the per-incident lease/circuit-breaker — not a best-effort daemon thread.
    mode = queue.enqueue_heal(None, iid, config.TARGET_SERVICE)
    return {"status": "accepted", "incident_id": iid, "queue": mode}


@mcp.tool()
def airbag_approve(incident_id: str, approve: bool) -> dict:
    """Resolve an incident awaiting a human decision: approve=True to APPROVE the gated rollback
    (L1) or fix-PR (L2), or approve=False to DENY. `approve` is required — decide explicitly."""
    threading.Thread(target=apply_approval, args=(incident_id, approve), daemon=True).start()
    return {"status": "accepted", "incident_id": incident_id, "approve": approve}


@mcp.tool()
def airbag_set_autonomy(level: Literal["L0", "L1", "L2", "L3"]) -> dict:
    """Set the target service's autonomy level: L0 observe / L1 approve-rollback /
    L2 auto-rollback+approve-fix / L3 full."""
    return autonomy.set_level(config.TARGET_SERVICE, level)


mcp_app = mcp.streamable_http_app()


@asynccontextmanager
async def lifespan(app):
    """Run the MCP session manager for the lifetime of the host app (required for the mounted
    streamable-HTTP transport)."""
    async with mcp.session_manager.run():
        yield


class BearerGate:
    """ASGI middleware: require Authorization: Bearer <AIRBAG_MCP_TOKEN> for the mounted MCP app —
    the agent is public, so this destructive action surface must be authenticated, with its OWN
    token (not the Cloud-Tasks credential). Fail-closed if the token is unset. Gates every scope
    except lifespan, so a future non-HTTP transport (e.g. websocket) can't slip past."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "lifespan":
            headers = dict(scope.get("headers") or [])
            auth = headers.get(b"authorization", b"").decode()
            token = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
            if not config.MCP_TOKEN or not hmac.compare_digest(token, config.MCP_TOKEN):
                resp = JSONResponse(
                    {"error": "unauthorized — Authorization: Bearer <AIRBAG_MCP_TOKEN> required"},
                    status_code=401)
                await resp(scope, receive, send)
                return
        await self.app(scope, receive, send)


gated_mcp_app = BearerGate(mcp_app)
