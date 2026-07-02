"""Pin the mock backend + no Gemini key for the whole suite, regardless of a local agent/.env
(which sets AIRBAG_BACKEND=local). Keeps tests deterministic and offline everywhere.

FIRESTORE-EMULATOR MODE (v4 Phase 4.1 — closes the gap that prod runs AIRBAG_STATE=firestore while
tests pinned memory): set AIRBAG_TEST_FIRESTORE=1 with FIRESTORE_EMULATOR_HOST pointing at a
running emulator, and the durable store runs against REAL google-cloud-firestore transactions
(per-test isolation via the emulator's wipe endpoint). Refuses to run without the emulator host so
this mode can never touch real Firestore. CI runs the state-critical suite this way (see
.github/workflows/ci.yml `firestore-emulator`)."""
import os

import pytest

from autosre import config, state_store

FIRESTORE_TEST = os.environ.get("AIRBAG_TEST_FIRESTORE", "").strip().lower() in ("1", "true", "yes")
if FIRESTORE_TEST and not os.environ.get("FIRESTORE_EMULATOR_HOST"):
    raise RuntimeError(
        "AIRBAG_TEST_FIRESTORE=1 requires FIRESTORE_EMULATOR_HOST (a running emulator) — "
        "refusing to run the test suite against real Firestore.")


def _wipe_emulator() -> None:
    """Per-test isolation on the real backend: the emulator's documents-wipe endpoint."""
    import httpx
    host = os.environ["FIRESTORE_EMULATOR_HOST"]
    proj = config.GCP_PROJECT or "airbag-test"
    httpx.delete(f"http://{host}/emulator/v1/projects/{proj}/databases/(default)/documents",
                 timeout=10.0)


@pytest.fixture(autouse=True)
def _hermetic_backend(monkeypatch):
    monkeypatch.setattr(config, "BACKEND", "mock")
    monkeypatch.setattr(config, "USE_MOCK", True)
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")  # no live ADK/Gemini/GitHub calls
    if FIRESTORE_TEST:
        monkeypatch.setattr(config, "STATE_BACKEND", "firestore")
        monkeypatch.setattr(config, "GCP_PROJECT", config.GCP_PROJECT or "airbag-test")
        _wipe_emulator()  # isolate the durable store per test, same as reset_memory below
    else:
        monkeypatch.setattr(config, "STATE_BACKEND", "memory")
    state_store.reset_memory()  # isolate the durable store (pending/incidents/dedup) per test
