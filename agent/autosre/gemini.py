"""Gemini integration (AI Studio API key via google-genai).

Real structured decision when GEMINI_API_KEY is set; returns None on any failure so
the state machine falls back to a deterministic decision. Uses a Pydantic response
schema + resp.parsed (robust against markdown-wrapped JSON).
"""
from __future__ import annotations

import json
import logging

from . import config
from .schemas import IncidentDecision

log = logging.getLogger("airbag.gemini")


def available() -> bool:
    return bool(config.GEMINI_API_KEY)


def decide(service: str, revs: dict, err: dict) -> dict | None:
    """Ask Gemini for a structured remediation decision. None -> caller falls back."""
    if not available():
        return None
    try:
        from google import genai
        from google.genai import types

        prompt = (
            "You are a Cloud Run release safety agent. A production alert fired.\n"
            f"Service: {service}\n"
            f"Error metrics: {json.dumps(err)}\n"
            f"Revisions (newest first): {json.dumps(revs.get('revisions', []))}\n\n"
            "Decide the safest action. Prefer ROLLBACK only when one revision clearly "
            "correlates with new 5xx errors AND a previous healthy (ready, 0% traffic) "
            "revision exists; set rollback_revision to that healthy revision's name. "
            "Otherwise OBSERVE. Be concise and set confidence honestly."
        )
        resp = genai.Client(api_key=config.GEMINI_API_KEY).models.generate_content(
            model=config.GEMINI_DECISION_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=IncidentDecision,
            ),
        )
        parsed = getattr(resp, "parsed", None)
        data = parsed.model_dump() if parsed is not None else json.loads(resp.text)
        data["_source"] = f"gemini:{config.GEMINI_DECISION_MODEL}"
        return data
    except Exception as e:  # noqa: BLE001 - any failure -> deterministic fallback
        log.warning("gemini.decide failed, falling back: %s", e)
        return None


def explain_recovery(service: str, before: dict, after: dict) -> str | None:
    """Optional one-line human summary of the recovery (nice for the dashboard)."""
    if not available():
        return None
    try:
        from google import genai

        resp = genai.Client(api_key=config.GEMINI_API_KEY).models.generate_content(
            model=config.GEMINI_DECISION_MODEL,
            contents=(
                f"In one short sentence, confirm recovery for Cloud Run service {service}. "
                f"Before: {json.dumps(before)}. After: {json.dumps(after)}."
            ),
        )
        return (resp.text or "").strip()
    except Exception as e:  # noqa: BLE001
        log.warning("gemini.explain_recovery failed: %s", e)
        return None
