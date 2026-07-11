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
    """The layers that must never import the LLM:
      - the prod-mutating action layer: every backend (shifts real Cloud Run traffic) + the tools façade;
      - the DETERMINISTIC detection tier (signals/): its verdict can promote a rollback in _validate, so
        it must stay statistical, not a Gemini call, or a hallucinated verdict could drive prod.
      - memory.py (v4): its learned baseline feeds the detectors AND its serving-history ledger
        PROPOSES the rollback target — both must stay deterministic facts, never an LLM output.
      - reversibility.py (v4): its BLOCK verdict converts a rollback into an ESCALATE — a
        hallucinated block/pass would directly drive prod, so it must stay a declared-marker read.
      - revision_delta.py (v5 5.3): its deterministic spec diff rides the signed proof bundle, so it
        must stay a fact (a set diff), never an LLM output that could forge "what changed" evidence.
      - proof.py (v6, Round 2 #24): the module that BUILDS and SIGNS the tamper-evident bundle — the
        artifact the auditor independently verifies — deserves the guard MORE than the diffs that ride
        it. Its imports are clean today (config, report, httpx, google.auth, stdlib), but "clean" must
        be ENFORCED through a finals sprint of edits to exactly this file (bundle_version, DSSE, etc.).
      - dsse.py (v6 Phase 1.2): assembles the in-toto Statement + DSSE envelope that rides the signed
        proof (cosign-verifiable); like proof.py it must stay a deterministic construction, never an
        LLM output that could forge attestation subjects/predicates.
      - state_store.py (v6 Phase 2): the durable primitive under the transparency log (transact_multi)
        + the per-service heal leases — a hallucinated write here could forge/suppress a log entry or a
        lease, so it must stay LLM-free (it genuinely imports only copy/threading/time/config).
    (adk_brain.py / gemini.py / agent.py are the LLM-advisory tier — they ARE allowed to import it.)"""
    return (sorted((_AUTOSRE / "backends").glob("*.py"))
            + sorted((_AUTOSRE / "signals").glob("*.py"))
            + [_AUTOSRE / "tools.py", _AUTOSRE / "causal.py", _AUTOSRE / "memory.py",
               _AUTOSRE / "reversibility.py", _AUTOSRE / "revision_delta.py", _AUTOSRE / "proof.py",
               _AUTOSRE / "dsse.py", _AUTOSRE / "state_store.py"])


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


def test_causal_and_signals_are_in_the_scanned_set():
    """The deterministic tiers that can drive a rollback (causal.py + signals/) must be scanned, or the
    LLM-free guarantee is unenforced for them."""
    scanned = {p.name for p in _action_files()}
    assert "causal.py" in scanned
    assert "revision_delta.py" in scanned   # v5 5.3: its diff rides the signed proof bundle
    assert "proof.py" in scanned            # v6 Round 2 #24: builds+signs the bundle the auditor verifies
    assert "dsse.py" in scanned             # v6 Phase 1.2: assembles the DSSE/in-toto attestation
    assert "state_store.py" in scanned      # v6 Phase 2: the transact_multi primitive under the log
    assert any(p.parent.name == "signals" for p in _action_files())


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


def test_auditor_denylist_supersets_the_agent_forbidden_set():
    """Repo-level parity (v6 Phase 1.2/1.3): the auditor's SERVICE denylist must forbid everything the
    agent's action-tier denylist forbids — so the independent auditor is at least as LLM-isolated as
    the agent's action tier — AND additionally forbids ALL agent code (`autosre`). Read the auditor
    invariant BY PATH (exec in an isolated namespace, `__file__` injected) so there is no import
    coupling across the two test suites and the sets can never silently drift apart."""
    inv = _AUTOSRE.parent.parent / "auditor" / "tests" / "test_auditor_invariant.py"
    assert inv.exists(), f"auditor invariant not found at {inv}"
    ns: dict = {"__file__": str(inv)}
    exec(compile(inv.read_text(encoding="utf-8"), str(inv), "exec"), ns)   # noqa: S102 — trusted repo file
    auditor_denylist = ns["_DENYLIST"]
    assert _FORBIDDEN <= auditor_denylist, (
        f"auditor denylist {sorted(auditor_denylist)} must be a SUPERSET of the agent's _FORBIDDEN "
        f"{sorted(_FORBIDDEN)} — otherwise the auditor could import an LLM form the agent forbids")
    assert "autosre" in auditor_denylist, "the auditor must forbid ALL agent code (autosre)"
