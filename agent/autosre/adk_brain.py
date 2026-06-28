"""The REQUIRED-STACK decision path: Gemini reasons *through the ADK SequentialAgent*.

`agent.py` defines the brain (triage -> decide). Here we actually RUN it: the triage
LlmAgent calls the Cloud Run / Monitoring tools itself via ADK function-calling, then the
decision LlmAgent emits a structured IncidentDecision. The deterministic state_machine
validates and executes it — Gemini never touches prod directly.

Returns None on ANY failure (no key, ADK import, tool error, parse) so the caller falls
back to a direct Gemini decision and then a heuristic; the heal never blocks on ADK.
"""
from __future__ import annotations

import asyncio
import logging
import os

from . import config
from .schemas import IncidentDecision

log = logging.getLogger("airbag.adk")


def available() -> bool:
    return config.USE_ADK and bool(config.GEMINI_API_KEY)


def decide(service: str, region: str | None = None) -> dict | None:
    """Run the ADK brain synchronously (state_machine runs in a worker thread → no loop)."""
    if not available():
        return None
    try:
        return asyncio.run(_run(service, region or config.GCP_REGION))
    except Exception as e:  # noqa: BLE001 - any failure -> caller falls back
        log.warning("adk_brain.decide failed, falling back: %s", e)
        return None


async def _run(service: str, region: str) -> dict | None:
    # ADK on the AI Studio backend reads GOOGLE_API_KEY (not GEMINI_API_KEY) + VERTEXAI flag.
    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "FALSE")
    os.environ["GOOGLE_API_KEY"] = config.GEMINI_API_KEY

    from google.adk.runners import InMemoryRunner
    from google.genai import types

    from .agent import decision_agent, root_agent

    runner = InMemoryRunner(agent=root_agent, app_name="airbag")
    await runner.session_service.create_session(
        app_name="airbag", user_id="airbag", session_id=service)
    prompt = (
        f"A production alert fired for Cloud Run service '{service}' in region {region}. "
        "Use your tools to gather the revisions and the 5xx error rate, then decide the "
        "safest action and return the structured decision."
    )
    finals: dict[str, str] = {}
    tool_calls: list[str] = []
    async for ev in runner.run_async(
            user_id="airbag", session_id=service,
            new_message=types.Content(role="user", parts=[types.Part(text=prompt)])):
        for p in (ev.content.parts if ev.content and ev.content.parts else []):
            if getattr(p, "function_call", None):
                tool_calls.append(p.function_call.name)
        if ev.is_final_response() and ev.content and ev.content.parts:
            txt = "".join(p.text or "" for p in ev.content.parts)
            if txt.strip():
                finals[ev.author] = txt

    raw = finals.get(decision_agent.name) or (list(finals.values())[-1] if finals else None)
    if not raw:
        log.warning("adk produced no final decision text")
        return None
    data = IncidentDecision.model_validate_json(raw).model_dump()
    data["_source"] = f"gemini-adk:{config.GEMINI_DECISION_MODEL}"
    data["_adk_tools"] = tool_calls
    return data
