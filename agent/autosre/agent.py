"""ADK agent definition — the DIAGNOSIS brain (for `adk web` and future ADK-native runs).

Production actions execute in state_machine.py (deterministic). This agent only reads
evidence and emits a structured IncidentDecision; the state machine validates and acts.

Pinned to google-adk ~=1.0 — 2.0 is a breaking graph-runtime rewrite (see requirements.txt).
"""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from . import config, tools

try:
    from google.adk.agents import LlmAgent, SequentialAgent
except ImportError as e:  # pragma: no cover - adk only needed for the ADK path
    raise ImportError(
        "google-adk not installed. `pip install -r requirements.txt` (must be 1.x)."
    ) from e


class IncidentDecision(BaseModel):
    """Structured decision the state machine validates before acting."""
    action: Literal["ROLLBACK", "OBSERVE", "OPEN_FIX_PR", "ESCALATE"]
    bad_revision: Optional[str] = None
    rollback_revision: Optional[str] = None
    confidence: float = Field(ge=0, le=1)
    evidence: List[str] = []


triage_agent = LlmAgent(
    name="triage",
    model=config.GEMINI_DECISION_MODEL,
    instruction=(
        "You triage a Cloud Run incident. Use the tools to gather revisions and the "
        "5xx error rate. Identify which revision correlates with new errors and whether "
        "a previous healthy revision exists. Do NOT execute any rollback — only report."
    ),
    tools=[tools.list_cloud_run_revisions, tools.query_error_rate],
)

decision_agent = LlmAgent(
    name="decide",
    model=config.GEMINI_DECISION_MODEL,
    instruction=(
        "Return a structured production decision. Prefer ROLLBACK only when one revision "
        "clearly correlates with new 5xx errors AND a previous healthy revision exists. "
        "Otherwise OBSERVE. Set confidence honestly."
    ),
    output_schema=IncidentDecision,
)

# Root agent for `adk web` exploration / evaluation.
root_agent = SequentialAgent(name="airbag_brain", sub_agents=[triage_agent, decision_agent])
