"""The v2 fix pipeline's deterministic parts: the SANDBOX verifier (the heart of "self-proving"
— a candidate test must FAIL on the bug and PASS on the fix), file discovery, and fail-closed.
The live RCA/patch (Gemini) is exercised manually."""
from autosre import config, fix_pipeline
from autosre.schemas import RootCause

_BUGGY = (
    "ORDERS = [{'price': 10}, {'price': 25}]\n"
    "def total_revenue(orders, buggy=False):\n"
    "    key = 'amount' if buggy else 'price'\n"
    "    return sum(o[key] for o in orders)\n")
_FIXED = (
    "ORDERS = [{'price': 10}, {'price': 25}]\n"
    "def total_revenue(orders, buggy=False):\n"
    "    return sum(o['price'] for o in orders)\n")
_GOOD_TEST = (
    "import main\n"
    "def test_no_keyerror_in_buggy_mode():\n"
    "    assert main.total_revenue(main.ORDERS, buggy=True) == 35\n")
_WEAK_TEST = (  # passes regardless of the bug -> must be rejected as not catching it
    "import main\n"
    "def test_healthy_path():\n"
    "    assert main.total_revenue(main.ORDERS) == 35\n")


def test_sandbox_accepts_a_real_regression_test():
    v = fix_pipeline._sandbox_verify("target-app/main.py", _BUGGY, _FIXED,
                                     "target-app/test_regression_airbag.py", _GOOD_TEST)
    assert v["ok"] is True and v["catches_bug"] and v["fix_passes"]


def test_sandbox_rejects_a_test_that_does_not_catch_the_bug():
    v = fix_pipeline._sandbox_verify("target-app/main.py", _BUGGY, _FIXED,
                                     "target-app/test_x.py", _WEAK_TEST)
    assert v["ok"] is False and v["catches_bug"] is False  # passes on the buggy file too


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
