"""The v2 fix pipeline's deterministic parts: file discovery + fail-closed. The sandbox verifier
lives in autosre/sandbox.py now (see test_sandbox.py); the live RCA/patch (Gemini) is manual."""
from autosre import config, fix_pipeline
from autosre.schemas import RootCause


def test_discover_file_prefers_stack_trace_then_falls_back(monkeypatch):
    monkeypatch.setattr(config, "FIX_FILE", "target-app/main.py")
    present = {"target-app/main.py": "x"}
    get = lambda p: present.get(p)  # noqa: E731
    assert fix_pipeline._discover_file(RootCause(summary="", error_signature="",
                                                 suspected_file="target-app/main.py"), get) == "target-app/main.py"
    # unknown suspected file -> fall back to FIX_FILE
    assert fix_pipeline._discover_file(RootCause(summary="", error_signature="",
                                                 suspected_file="nope/ghost.py"), get) == "target-app/main.py"


def test_build_fix_fails_closed_without_key(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    assert fix_pipeline.build_fix("svc", "ctx", lambda p: "src") is None
