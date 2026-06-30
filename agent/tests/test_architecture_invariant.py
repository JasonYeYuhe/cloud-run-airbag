"""The load-bearing architecture invariant: Gemini DIAGNOSES, the deterministic state machine ACTS.
The prod-mutating layer (backends/ + tools.py) must never import the LLM — so a Gemini hallucination
or prompt injection can't reach a traffic mutation except through _validate's deterministic gate.
This test makes that thesis regression-proof (not just a runtime key-null in conftest)."""
import pathlib

_AUTOSRE = pathlib.Path(__file__).resolve().parent.parent / "autosre"


def _imports_gemini(path: pathlib.Path) -> bool:
    src = path.read_text(encoding="utf-8")
    return ("import gemini" in src or "from . import" in src and "gemini" in src.split("from . import")[1].split("\n")[0]
            or "google.genai" in src or "google import genai" in src)


def test_action_layer_never_imports_the_llm():
    """No backend (which shifts real Cloud Run traffic) nor tools.py imports Gemini/genai."""
    action_files = list((_AUTOSRE / "backends").glob("*.py")) + [_AUTOSRE / "tools.py"]
    offenders = [p.name for p in action_files if _imports_gemini(p)]
    assert not offenders, f"action layer must not import the LLM (FSM-acts/LLM-advises invariant): {offenders}"
