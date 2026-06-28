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


_client_singleton = None


def available() -> bool:
    return bool(config.GEMINI_API_KEY)


def _client():
    """Cached client — must hold a reference, else it's GC'd mid-request
    ('client has been closed')."""
    global _client_singleton
    if _client_singleton is None:
        from google import genai
        _client_singleton = genai.Client(api_key=config.GEMINI_API_KEY)
    return _client_singleton


def decide(service: str, revs: dict, err: dict) -> dict | None:
    """Ask Gemini for a structured remediation decision. None -> caller falls back."""
    if not available():
        return None
    try:
        from google.genai import types

        prompt = (
            "You are a Cloud Run release safety agent. A production alert fired.\n"
            f"Service: {service}\n"
            f"Error metrics: {json.dumps(err)}\n"
            f"Revisions (newest first): {json.dumps(revs.get('revisions', []))}\n\n"
            "Decide the safest action. If the revision currently serving traffic has a "
            "high 5xx error rate (error_rate >= 0.5, or many errors) AND a previous "
            "healthy (ready, 0% traffic) revision exists, choose ROLLBACK and set "
            "rollback_revision to that healthy revision's EXACT name. Rolling back is safe "
            "and reversible, so prefer it whenever the serving revision is clearly failing. "
            "Choose OBSERVE only if errors are minor or there is no healthy revision to roll "
            "back to. Be concise."
        )
        resp = _client().models.generate_content(
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
        resp = _client().models.generate_content(
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
