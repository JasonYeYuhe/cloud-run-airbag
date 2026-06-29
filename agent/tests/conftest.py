"""Pin the mock backend + no Gemini key for the whole suite, regardless of a local agent/.env
(which sets AIRBAG_BACKEND=local). Keeps tests deterministic and offline everywhere."""
import pytest

from autosre import config, state_store


@pytest.fixture(autouse=True)
def _hermetic_backend(monkeypatch):
    monkeypatch.setattr(config, "BACKEND", "mock")
    monkeypatch.setattr(config, "USE_MOCK", True)
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")  # no live ADK/Gemini/GitHub calls
    monkeypatch.setattr(config, "STATE_BACKEND", "memory")
    state_store.reset_memory()  # isolate the durable store (pending/incidents/dedup) per test
