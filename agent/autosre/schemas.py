"""Shared pydantic schemas — no ADK/GCP deps, so any module can import them."""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class IncidentDecision(BaseModel):
    # Top-level actions the LLM may choose. The fix-PR is NOT a top-level action: it's a downstream
    # step of ROLLBACK (state_machine._mitigate -> _open_fix_pr). OPEN_FIX_PR was dropped (Phase 0.3)
    # because state_machine only branches on ROLLBACK/ESCALATE, so it silently became a no-op DONE
    # that folded a healthy sample into the learned baseline and shipped nothing.
    action: Literal["ROLLBACK", "OBSERVE", "ESCALATE"]
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
