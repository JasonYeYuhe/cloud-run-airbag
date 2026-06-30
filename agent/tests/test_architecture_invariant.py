"""The load-bearing architecture invariant: Gemini DIAGNOSES, the deterministic state machine ACTS.
The prod-mutating layer (backends/ + tools.py) must never import the LLM — so a Gemini hallucination
or prompt injection can't reach a traffic mutation except through _validate's deterministic gate.

This guard is AST-based (Phase 0.2): it parses the real Import/ImportFrom statements rather than
substring-matching the source. The old substring check (`"import gemini" in src` + a brittle
`from . import` split) would MISS `from .gemini import _client`, `from autosre import gemini`, an
aliased `import google.generativeai as g`, or `from google import genai` — exactly the slips a v3
diagnosis-tier refactor could introduce. The self-tests below pin every one of those forms.
"""
import ast
import pathlib

_AUTOSRE = pathlib.Path(__file__).resolve().parent.parent / "autosre"

# Dotted-name components that mean "the LLM / diagnosis tier". The action layer must import NONE of
# them. Matching on components (not substrings) catches relative, absolute, aliased and `from`-imports
# alike, without false-positiving on comments/strings or on legitimate deps like google.cloud.run_v2.
_FORBIDDEN = frozenset({
    "gemini",        # autosre.gemini (direct Gemini)  ·  `from .gemini import ...`
    "adk_brain",     # autosre.adk_brain (the ADK SequentialAgent brain)
    "genai",         # google.genai  ·  `from google import genai`
    "generativeai",  # google.generativeai
    "adk",           # google.adk  ·  `from google.adk... import ...`
})


def _module_strings(node: ast.AST) -> list[str]:
    """Every dotted module name a single Import/ImportFrom statement brings into scope."""
    out: list[str] = []
    if isinstance(node, ast.Import):
        out += [a.name for a in node.names]                          # import a.b.c [as d]
    elif isinstance(node, ast.ImportFrom):
        if node.module:
            out.append(node.module)                                  # from a.b import x
            out += [f"{node.module}.{a.name}" for a in node.names]   # from a.b import c (c may be a module)
        else:
            out += [a.name for a in node.names]                      # from . import gemini  (relative)
    return out


def _offending(tree: ast.AST) -> list[tuple[int, str]]:
    offenders: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for mod in _module_strings(node):
                if _FORBIDDEN & set(mod.split(".")):
                    offenders.append((getattr(node, "lineno", 0), mod))
    return offenders


def _offending_in_source(src: str) -> list[str]:
    return [mod for _, mod in _offending(ast.parse(src))]


def _action_files() -> list[pathlib.Path]:
    """The prod-mutating layer: every backend (which shifts real Cloud Run traffic) + the tools façade."""
    return sorted((_AUTOSRE / "backends").glob("*.py")) + [_AUTOSRE / "tools.py"]


def test_action_layer_never_imports_the_llm():
    offenders = {}
    for p in _action_files():
        bad = _offending(ast.parse(p.read_text(encoding="utf-8"), filename=str(p)))
        if bad:
            offenders[p.name] = bad
    assert not offenders, (
        "action layer (backends/* + tools.py) must NEVER import the LLM/diagnosis tier "
        f"(FSM-acts/LLM-advises invariant). Offending imports: {offenders}")


# --- guard the guard: prove the AST check catches every form the old substring test would miss -----
def test_ast_guard_flags_llm_imports():
    must_flag = [
        "from .gemini import _client",            # old check missed dotted relative submodule import
        "from autosre import gemini",             # old check missed cross-package import
        "import google.generativeai as g",        # old check missed aliased generativeai
        "from google import genai",               # old check missed `from google import genai`
        "from google.genai import types",
        "from google.adk.runners import InMemoryRunner",
        "import autosre.adk_brain",
    ]
    for src in must_flag:
        assert _offending_in_source(src), f"AST guard FAILED to flag an LLM import: {src!r}"


def test_ast_guard_allows_legitimate_action_layer_imports():
    must_pass = [
        "from . import config",
        "from .backends import get_backend",
        "from google.cloud import run_v2",
        "from google.cloud import logging as cloud_logging",
        "import httpx",
        "import datetime, json",
        "from __future__ import annotations",
    ]
    for src in must_pass:
        assert not _offending_in_source(src), f"AST guard false-positived on a clean import: {src!r}"
