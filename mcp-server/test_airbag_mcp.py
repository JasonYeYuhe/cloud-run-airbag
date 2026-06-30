"""Contract tests for the Airbag MCP server (mocked HTTP — no live agent needed).
Run: cd mcp-server && python -m pytest test_airbag_mcp.py  (deps: mcp, httpx, pytest)."""
import asyncio
from unittest.mock import patch

import airbag_mcp as m


def test_all_tools_registered():
    names = {t.name for t in asyncio.run(m.mcp.list_tools())}
    assert names == {
        "airbag_health", "airbag_incidents", "airbag_incident", "airbag_autonomy", "airbag_memory",
        "airbag_trigger_heal", "airbag_approve", "airbag_set_autonomy", "airbag_break", "airbag_reset"}


class _Resp:
    def __init__(self, data, status_code=200, text=""):
        self._data = data
        self.status_code = status_code
        self.text = text

    def json(self):
        return self._data


def test_read_tool_calls_get_no_auth():
    with patch.object(m.httpx, "get", return_value=_Resp({"status": "ok"})) as g:
        assert m.airbag_health() == {"status": "ok"}
        assert g.call_args[0][0].endswith("/health")
        assert "headers" not in g.call_args.kwargs  # read tools send no token


def test_action_tool_requires_token(monkeypatch):
    monkeypatch.setattr(m, "DEMO_TOKEN", "")
    with patch.object(m.httpx, "post") as p:
        out = m.airbag_trigger_heal()
        assert "error" in out and p.call_count == 0   # fail closed: no request without a token


def test_action_tool_sends_token_header(monkeypatch):
    monkeypatch.setattr(m, "DEMO_TOKEN", "tok")
    with patch.object(m.httpx, "post", return_value=_Resp({"status": "accepted"})) as p:
        assert m.airbag_trigger_heal() == {"status": "accepted"}
        assert p.call_args.kwargs["headers"]["x-airbag-demo-token"] == "tok"
        assert p.call_args[0][0].endswith("/demo/heal")


def test_non_2xx_returns_sanitized_error_not_raise(monkeypatch):
    # a 500 must NOT raise (httpx exceptions embed the token-bearing request) -> structured error
    monkeypatch.setattr(m, "DEMO_TOKEN", "tok")
    with patch.object(m.httpx, "post", return_value=_Resp(None, status_code=500, text="boom")):
        out = m.airbag_trigger_heal()
        assert out["error"] == "airbag returned HTTP 500" and "tok" not in str(out)


def test_connection_error_is_handled(monkeypatch):
    with patch.object(m.httpx, "get", side_effect=m.httpx.ConnectError("down")):
        out = m.airbag_health()
        assert "could not reach airbag" in out["error"]


def test_path_segments_are_quoted(monkeypatch):
    with patch.object(m.httpx, "get", return_value=_Resp({})) as g:
        m.airbag_incident("../demo/heal")
        assert "%2F" in g.call_args[0][0] and "/incidents/..%2Fdemo%2Fheal" in g.call_args[0][0]
