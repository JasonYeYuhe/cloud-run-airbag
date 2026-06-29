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


class RootCause(BaseModel):
    """Structured root-cause from the actual error logs (not just 'returned HTTP 500')."""
    summary: str                       # one-paragraph root cause
    error_signature: str               # exception class + key message, e.g. "KeyError: 'amount'"
    suspected_file: str = ""           # repo-relative path parsed from the stack trace (best effort)
    suspected_symbol: str = ""         # function/method implicated, if known
    hypothesis: str = ""               # why this revision started failing


class FixResult(BaseModel):
    fixed_content: str  # the full corrected file
    pr_title: str
    pr_body: str
    summary: str        # one-line root cause + fix
    # an agent-authored regression test that FAILS on the bug and PASSES on the fix — so the PR
    # self-proves, with no human-pre-planted oracle.
    test_path: str = ""      # repo-relative path, e.g. target-app/tests/test_regression_airbag.py
    test_content: str = ""   # full pytest module; imports the fixed module by name
