"""v5 Phase 1.2 — observer-safe diagnostics guard (spirit of the AST architecture invariant).

Airbag's OWN diagnostic/probe traffic must be self-identifying so it can't (a) fire the very alert
it is diagnosing or (b) poison another heal's log-scan 5xx count — the self-amplifying storm of
2026-07-02. This guard PINS that:
  * every httpx.Client(...) in the action-tier backends/ carries config.PROBE_HEADERS, and
  * _burst in app.py stays UNMARKED (it SIMULATES USERS — marking it would hide the demo's outage).
AST-based (not substring) so it survives reformatting and catches a NEW backend probe added unmarked.
Plus filter-construction tests for the self-traffic exclusion in the log-scan 5xx COUNT.
"""
import ast
import datetime
import pathlib

from autosre import config
from autosre.backends import gcp

_AGENT = pathlib.Path(__file__).resolve().parent.parent
_BACKENDS = _AGENT / "autosre" / "backends"
_APP = _AGENT / "app.py"


def _is_httpx_client(node: ast.AST) -> bool:
    """A call to httpx.Client(...)  (the dotted Attribute form we use everywhere)."""
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "Client" and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "httpx")


def _has_probe_headers(call: ast.Call) -> bool:
    """The call passes headers=config.PROBE_HEADERS (the one shared marker — not just any headers)."""
    for kw in call.keywords:
        if kw.arg == "headers":
            v = kw.value
            return (isinstance(v, ast.Attribute) and v.attr == "PROBE_HEADERS"
                    and isinstance(v.value, ast.Name) and v.value.id == "config")
    return False


def _httpx_clients(tree: ast.AST) -> list[ast.Call]:
    return [n for n in ast.walk(tree) if _is_httpx_client(n)]


def _backend_files() -> list[pathlib.Path]:
    return sorted(_BACKENDS.glob("*.py"))


def _tree(p: pathlib.Path) -> ast.AST:
    return ast.parse(p.read_text(encoding="utf-8"), filename=str(p))


def test_every_backend_httpx_client_is_marked():
    """Every diagnostic/control httpx.Client in backends/ carries config.PROBE_HEADERS — so NO
    Airbag-originated request to a target can be mistaken for a user's (the observer-effect fix)."""
    offenders = {}
    for p in _backend_files():
        unmarked = [c.lineno for c in _httpx_clients(_tree(p)) if not _has_probe_headers(c)]
        if unmarked:
            offenders[p.name] = unmarked
    assert not offenders, (
        "every httpx.Client in backends/ must pass headers=config.PROBE_HEADERS "
        f"(v5 Phase 1.2 observer-safe invariant). Unmarked at: {offenders}")


def test_backends_actually_have_clients_scanned():
    """Guard the guard: prove the scan SEES clients (gcp + local), so a silent parse/glob miss can't
    make the marker test pass vacuously."""
    counts = {p.name: len(_httpx_clients(_tree(p))) for p in _backend_files()}
    assert counts.get("gcp.py", 0) >= 5      # the five diagnostic clients (§3 1.2)
    assert counts.get("local.py", 0) >= 3    # local diagnostic + control clients


def test_burst_stays_unmarked():
    """_burst SIMULATES USERS — it must NOT carry the probe marker, or the self-traffic exclusion
    would hide the very outage the demo creates. The inverse of the backends invariant."""
    burst = next((n for n in ast.walk(_tree(_APP))
                  if isinstance(n, ast.FunctionDef) and n.name == "_burst"), None)
    assert burst is not None, "_burst not found in app.py"
    clients = _httpx_clients(burst)
    assert clients, "_burst should still create an httpx client (it simulates user traffic)"
    assert all(not _has_probe_headers(c) for c in clients), \
        "_burst must stay UNMARKED — it simulates USERS, not Airbag's diagnostics (v5 Phase 1.2 pin)"


# --- guard-the-guard: the AST checks catch exactly what they claim ---------------------------------
def test_marker_check_flags_unmarked_and_passes_marked():
    marked = _httpx_clients(ast.parse("httpx.Client(timeout=5.0, headers=config.PROBE_HEADERS)"))
    unmarked = _httpx_clients(ast.parse("httpx.Client(timeout=5.0)"))
    wrong = _httpx_clients(ast.parse("httpx.Client(headers={'User-Agent': 'x'})"))
    assert marked and _has_probe_headers(marked[0])
    assert unmarked and not _has_probe_headers(unmarked[0])
    assert wrong and not _has_probe_headers(wrong[0])   # a non-PROBE_HEADERS dict is NOT the marker


# --- filter-construction: the self-traffic exclusion in the 5xx COUNT (v5 Phase 1.2) --------------
def test_error_rate_filter_excludes_probe_ua_only_when_enabled(monkeypatch):
    start = datetime.datetime(2026, 7, 2, tzinfo=datetime.timezone.utc)

    monkeypatch.setattr(config, "SELF_TRAFFIC_EXCLUDE", False)
    off = gcp._error_rate_filter("svc", "asia-northeast1", start)
    assert "userAgent" not in off and 'httpRequest.status>=500' in off   # default: byte-identical to v4

    monkeypatch.setattr(config, "SELF_TRAFFIC_EXCLUDE", True)
    on = gcp._error_rate_filter("svc", "asia-northeast1", start)
    assert f'NOT httpRequest.userAgent="{config.PROBE_UA}"' in on         # excludes ONLY Airbag's marked UA
    assert on.startswith(off)                                             # additive — no user-5xx clause dropped
