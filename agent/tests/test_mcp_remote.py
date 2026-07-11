"""Remote MCP (streamable-HTTP mounted on the agent): tool registration + the Bearer-gate ASGI
middleware. The full transport handshake is smoke-tested live separately; this locks the contract
without a server. (mcp_remote is import-safe regardless of the AIRBAG_MCP_HTTP flag.)"""
import asyncio

from autosre import config, mcp_remote


def test_remote_mcp_registers_tools():
    names = {t.name for t in asyncio.run(mcp_remote.mcp.list_tools())}
    assert names == {"airbag_incidents", "airbag_incident", "airbag_incident_proof", "airbag_autonomy",
                     "airbag_memory", "airbag_trigger_heal", "airbag_approve", "airbag_set_autonomy"}
    # v6 Phase 3: airbag_incident_proof (7 -> 8) makes the remote MCP a first-class A2A proof peer
    assert len(names) == 8


def test_airbag_incident_proof_serves_the_stored_snapshot():
    from autosre import incidents
    incidents.record("inc-mcp", {"incident_id": "inc-mcp", "service": "svc", "status": "mitigated",
                                 "events": [], "proof": {"digest": "sha256:abc", "bundle": {"x": 1}}})
    assert mcp_remote.airbag_incident_proof("inc-mcp")["digest"] == "sha256:abc"   # verbatim snapshot
    assert mcp_remote.airbag_incident_proof("nope")["error"].startswith("incident nope")


def _drive_gate(headers):
    """Run BearerGate against a fake ASGI http scope; return (passed_through, sent_messages)."""
    passed, sent = [], []

    async def downstream(scope, receive, send):
        passed.append(True)

    async def receive():
        return {}

    async def send(msg):
        sent.append(msg)

    gate = mcp_remote.BearerGate(downstream)
    asyncio.run(gate({"type": "http", "headers": headers}, receive, send))
    return passed, sent


def test_bearer_gate_blocks_without_token(monkeypatch):
    monkeypatch.setattr(config, "MCP_TOKEN", "tok")
    passed, sent = _drive_gate([])
    assert not passed  # never reached the MCP app
    assert any(m.get("type") == "http.response.start" and m.get("status") == 401 for m in sent)


def test_bearer_gate_blocks_wrong_token(monkeypatch):
    monkeypatch.setattr(config, "MCP_TOKEN", "tok")
    passed, _ = _drive_gate([(b"authorization", b"Bearer nope")])
    assert not passed


def test_bearer_gate_allows_correct_token(monkeypatch):
    monkeypatch.setattr(config, "MCP_TOKEN", "tok")
    passed, _ = _drive_gate([(b"authorization", b"Bearer tok")])
    assert passed


def test_bearer_gate_fail_closed_when_token_unset(monkeypatch):
    monkeypatch.setattr(config, "MCP_TOKEN", "")
    passed, sent = _drive_gate([(b"authorization", b"Bearer anything")])
    assert not passed  # no token configured -> deny everything
    assert any(m.get("status") == 401 for m in sent if m.get("type") == "http.response.start")
