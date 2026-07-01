# Airbag MCP server

Exposes the [Airbag](../README.md) autonomous Cloud Run rollback agent as **MCP tools**, so any
MCP client — Claude Desktop, Cursor, or another agent — can observe and drive incident response.
On theme for an AI-agent hackathon: *Airbag isn't just an agent, it's a tool other agents compose with.*

It's a standalone **stdio** MCP server that proxies Airbag's HTTP API, so it works against the live
Cloud Run agent or a local `./run-local.sh` instance — nothing about the deployed agent changes.

## Tools
| Tool | Auth | What |
|---|:--:|---|
| `airbag_health` | — | backend + Gemini status |
| `airbag_incidents(limit)` | — | recent self-heal runs + outcomes |
| `airbag_incident(incident_id)` | — | full decision + signals + thought-chain timeline |
| `airbag_incident_proof(incident_id)` | — | tamper-evident proof bundle (sha256 digest) for A2A/audit |
| `airbag_autonomy` | — | autonomy level (L0–L3), trust streak, pending approvals |
| `airbag_memory` | — | learned per-service baseline + incident memory |
| `airbag_trigger_heal` | token | run a self-heal now → returns `incident_id` |
| `airbag_approve(incident_id, approve)` | token | approve/deny a gated rollback (L1) or fix-PR (L2) |
| `airbag_set_autonomy(level)` | token | set L0 / L1 / L2 / L3 |
| `airbag_break` / `airbag_reset` | token | demo fault injection / reset |

Read tools are watch-only (no token). Action tools need `AIRBAG_DEMO_TOKEN`.

## Run / install
```bash
pip install -r mcp-server/requirements.txt
# smoke it (lists tools over stdio):
AIRBAG_URL=https://airbag-agent-…run.app python mcp-server/airbag_mcp.py   # Ctrl-C to stop
```

Add to Claude Desktop (`~/Library/Application Support/Claude/claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "airbag": {
      "command": "python",
      "args": ["/abs/path/to/cloud-run-airbag/mcp-server/airbag_mcp.py"],
      "env": {
        "AIRBAG_URL": "https://airbag-agent-946577240607.asia-northeast1.run.app",
        "AIRBAG_DEMO_TOKEN": "<demo token>"
      }
    }
  }
}
```

Then ask Claude things like *"use airbag to show recent incidents,"* *"what's the autonomy level?,"*
*"set airbag-target to L1, break it, then approve the rollback."*

## Config
| env | default |
|---|---|
| `AIRBAG_URL` | the deployed agent URL |
| `AIRBAG_DEMO_TOKEN` | *(unset — required for action tools)* |
| `AIRBAG_MCP_TIMEOUT` | `30` (seconds) |

> **Note:** this is a client-side stdio server. A *remote* MCP endpoint mounted on the Cloud Run
> agent (streamable-HTTP) is a possible enhancement; stdio keeps it simple, standard, and safe.
