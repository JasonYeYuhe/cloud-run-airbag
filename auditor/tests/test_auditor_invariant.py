"""The auditor's INDEPENDENCE invariant (v6 Phase 1.1 seed; extended in Phase 1.3).

The whole marquee rests on the auditor being adversarially INDEPENDENT of the agent it audits. The
crypto kernel `auditor/verify.py` proves the strongest form of that: it imports ZERO agent code and
ZERO network libraries — ONLY the Python stdlib + `cryptography`. That is a STRONGER property than the
agent's LLM-free denylist (agent/tests/test_architecture_invariant.py), so it is enforced as an import
ALLOWLIST: `verify.py` may import ONLY names on the allowlist; anything else (autosre, httpx, google,
requests, …) fails the test. AST-based — parses the real Import/ImportFrom statements, so it catches
aliased / relative / `from`-imports alike, not a substring match.

Scope note: the auditor SERVICE files added in Phase 1.2/1.3 (the poller + the KMS counter-signer)
legitimately need httpx / google-auth, so Phase 1.3 EXTENDS this file with (a) a denylist mirror for
the rest of `auditor/` and (b) a repo-level parity test asserting the auditor's forbidden set is a
superset of the agent's `_FORBIDDEN`. The kernel allowlist below stays the tightest guard.
"""
import ast
import pathlib
import sys

_AUDITOR = pathlib.Path(__file__).resolve().parent.parent

# verify.py's ENTIRE legitimate dependency surface: the stdlib + the one crypto lib. Nothing else —
# no agent import, no network client. Using sys.stdlib_module_names keeps this robust to the kernel
# reaching for another stdlib module without loosening the "no agent / no network" guarantee.
_ALLOWED_TOP = frozenset(sys.stdlib_module_names) | {"cryptography"}


def _imported_tops(tree: ast.AST) -> set[str]:
    """The top-level package of every import in the file. A relative import (`from . import x`) is
    reported as '<relative>' — always disallowed in the kernel (it would pull in a sibling module and
    void the standalone guarantee)."""
    tops: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            tops.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                tops.add("<relative>")
            if node.module:
                tops.add(node.module.split(".")[0])
    return tops


def test_verify_kernel_imports_only_stdlib_and_cryptography():
    src = (_AUDITOR / "verify.py").read_text(encoding="utf-8")
    tops = _imported_tops(ast.parse(src))
    forbidden = tops - _ALLOWED_TOP
    assert not forbidden, (
        "auditor/verify.py must import ONLY the stdlib + `cryptography` — the 'zero agent imports' "
        "independence property the whole marquee rests on. Disallowed top-level imports: "
        f"{sorted(forbidden)}")


def test_allowlist_catches_an_agent_or_network_import():
    """Guard the guard: prove the allowlist would FLAG the exact imports that void independence."""
    for src in ("from autosre import proof", "import autosre.proof", "import httpx",
                "from google.auth import default", "from . import config", "import requests"):
        tops = _imported_tops(ast.parse(src))
        assert tops - _ALLOWED_TOP, f"allowlist FAILED to flag an independence-voiding import: {src!r}"


def test_allowlist_allows_the_kernel_deps():
    for src in ("import base64, hashlib, json", "from __future__ import annotations",
                "from cryptography.hazmat.primitives import hashes, serialization",
                "from cryptography.hazmat.primitives.asymmetric import ec"):
        tops = _imported_tops(ast.parse(src))
        assert not (tops - _ALLOWED_TOP), f"allowlist false-positived on a clean kernel import: {src!r}"


# --- DENYLIST for the rest of the auditor SERVICE (Phase 1.2/1.3) -----------------------------------
# The service files (the counter-signer, and the poller/server in 1.3) legitimately need httpx /
# google-auth, so they get a DENYLIST instead of the kernel's allowlist. It forbids the agent's LLM
# tier AND the whole `autosre` package — the auditor imports ZERO agent code. The repo-level parity
# test in agent/tests asserts this set is a SUPERSET of the agent's own _FORBIDDEN (read by path,
# no import coupling), so the two can never silently drift.
_DENYLIST = frozenset({
    "gemini", "adk_brain", "genai", "generativeai", "adk",   # the agent's LLM/diagnosis tier
    "autosre",                                               # ANY agent code — the independence claim
})


def _module_strings(node: ast.AST) -> list[str]:
    """Every dotted module name a single Import/ImportFrom brings into scope (mirrors the agent's
    architecture-invariant helper) so component matching catches google.adk / from google import genai."""
    out: list[str] = []
    if isinstance(node, ast.Import):
        out += [a.name for a in node.names]
    elif isinstance(node, ast.ImportFrom):
        if node.module:
            out.append(node.module)
            out += [f"{node.module}.{a.name}" for a in node.names]
        else:
            out += [a.name for a in node.names]                # from . import gemini  (relative)
    return out


def _denylist_hits(tree: ast.AST) -> set[str]:
    hits: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for mod in _module_strings(node):
                if _DENYLIST & set(mod.split(".")):
                    hits.add(mod)
    return hits


def _service_files() -> list[pathlib.Path]:
    """Every auditor .py EXCEPT the allowlist-pure kernel (verify.py) — i.e. the network-using service
    modules the denylist guards."""
    return [p for p in sorted(_AUDITOR.glob("*.py")) if p.name != "verify.py"]


def test_auditor_service_files_never_import_agent_or_llm():
    offenders = {}
    for p in _service_files():
        bad = _denylist_hits(ast.parse(p.read_text(encoding="utf-8"), filename=str(p)))
        if bad:
            offenders[p.name] = sorted(bad)
    assert not offenders, (
        "auditor service files must import NO agent code (`autosre`) and NO LLM tier — the "
        f"independence property. Offending imports: {offenders}")


def test_denylist_flags_every_agent_and_llm_import_form():
    for src in ("from autosre import proof", "import autosre.state_machine",
                "from autosre.gemini import _client", "from google import genai",
                "import google.generativeai as g", "from google.adk.runners import InMemoryRunner",
                "from . import adk_brain"):
        assert _denylist_hits(ast.parse(src)), f"denylist FAILED to flag an agent/LLM import: {src!r}"


def test_denylist_allows_network_and_sibling_imports():
    for src in ("import httpx", "from google.auth import default",
                "from google.auth.transport.requests import Request", "import verify",
                "import base64, hashlib, json"):
        assert not _denylist_hits(ast.parse(src)), f"denylist false-positived on a clean import: {src!r}"
