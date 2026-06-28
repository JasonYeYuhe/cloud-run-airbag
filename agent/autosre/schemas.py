"""Shared pydantic schemas — no ADK/GCP deps, so any module can import them."""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class IncidentDecision(BaseModel):
    action: Literal["ROLLBACK", "OBSERVE", "OPEN_FIX_PR", "ESCALATE"]
    bad_revision: Optional[str] = None
    rollback_revision: Optional[str] = None
    confidence: float = Field(ge=0, le=1)
    reasoning: str = ""
    evidence: List[str] = []


class FixResult(BaseModel):
    fixed_content: str  # the full corrected file
    pr_title: str
    pr_body: str
    summary: str        # one-line root cause + fix
