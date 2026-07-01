"""The sandbox that verifies the LLM-authored regression test (autosre/sandbox.py).

Two backends:
  * subprocess (default) — the self-proving heart: a candidate test must FAIL on the bug and PASS on
    the fix. Exercised for real here (offline, fast).
  * cloudrun_job — the egress-disabled Cloud Run Job path: dispatch + verdict parsing are unit-tested
    with the run_v2/logging clients mocked (the live round-trip is verified against the deployed job).
"""
from types import SimpleNamespace

from autosre import config, sandbox

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


# --- subprocess backend (default) --------------------------------------------------------------
def test_subprocess_sandbox_accepts_a_real_regression_test():
    v = sandbox.verify("target-app/main.py", _BUGGY, _FIXED,
                       "target-app/test_regression_airbag.py", _GOOD_TEST)
    assert v["ok"] is True and v["catches_bug"] and v["fix_passes"]


def test_subprocess_sandbox_rejects_a_test_that_does_not_catch_the_bug():
    v = sandbox.verify("target-app/main.py", _BUGGY, _FIXED, "target-app/test_x.py", _WEAK_TEST)
    assert v["ok"] is False and v["catches_bug"] is False  # passes on the buggy file too


def test_no_test_is_not_verified():
    assert sandbox.verify("target-app/main.py", _BUGGY, _FIXED, "t.py", "")["ok"] is False


# --- cloudrun_job backend (mocked run_v2 + logging) --------------------------------------------
def test_cloudrun_job_reads_verdict_from_logs(monkeypatch):
    monkeypatch.setattr(config, "SANDBOX_BACKEND", "cloudrun_job")
    captured = {}

    def fake_job(stem, test_name, original, fixed, test_content):
        captured["env"] = (stem, test_name, original, fixed, test_content)
        # simulate the runner logging a verified verdict
        return {"ok": True, "why": "verified", "catches_bug": True, "fix_passes": True, "output": "ok"}

    monkeypatch.setattr(sandbox, "_verify_cloudrun_job", fake_job)
    v = sandbox.verify("target-app/main.py", _BUGGY, _FIXED, "target-app/t.py", _GOOD_TEST)
    assert v["ok"] is True
    assert captured["env"][0] == "main" and captured["env"][1] == "t.py"


def test_cloudrun_job_failure_reports_unverified_not_subprocess(monkeypatch):
    """Once cloudrun_job isolation is selected, a Job failure must NOT fall back to running untrusted
    code in the prod container. It reports UNVERIFIED (the fix still ships flagged; CI backstops)."""
    monkeypatch.setattr(config, "SANDBOX_BACKEND", "cloudrun_job")

    def boom(*a, **k):
        raise RuntimeError("job API unreachable")

    monkeypatch.setattr(sandbox, "_verify_cloudrun_job", boom)
    v = sandbox.verify("target-app/main.py", _BUGGY, _FIXED, "target-app/t.py", _GOOD_TEST)
    assert v["ok"] is False and "unavailable" in v["why"]  # did NOT run the subprocess in prod


def test_cloudrun_job_rejects_oversized_inputs(monkeypatch):
    """Inputs too large for an env override raise (caught by verify -> subprocess fallback)."""
    monkeypatch.setattr(config, "SANDBOX_JOB_NAME", "airbag-sandbox")
    huge = "x" * (sandbox._MAX_ENV_B64 * 2)
    try:
        sandbox._verify_cloudrun_job("main", "t.py", huge, huge, _GOOD_TEST)
        raised = False
    except ValueError:
        raised = True
    assert raised


def test_read_job_verdict_parses_marker(monkeypatch):
    """The verdict is extracted from the RESULT_MARKER line the runner logs (control-plane stdout)."""
    import pytest
    pytest.importorskip("google.cloud.logging")
    line = sandbox.RESULT_MARKER + '{"ok": true, "catches_bug": true, "fix_passes": true, "output": "x"}'

    class _FakeLogClient:
        def __init__(self, project=None):
            pass

        def list_entries(self, **k):
            return [SimpleNamespace(payload=line)]

    import google.cloud.logging as gcl
    monkeypatch.setattr(gcl, "Client", _FakeLogClient)
    v = sandbox._read_job_verdict("exec-abc", attempts=1, interval_s=0)
    assert v is not None and v["ok"] is True


def test_job_runner_verifies_offline():
    """The standalone runner.py (what actually runs inside the isolated job) proves the same
    self-proving check: FAIL on the bug, PASS on the fix."""
    import importlib.util
    import pathlib
    rp = pathlib.Path(__file__).resolve().parents[2] / "sandbox-job" / "runner.py"
    spec = importlib.util.spec_from_file_location("airbag_sandbox_runner", rp)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    assert runner.verify("main", "test_r.py", _BUGGY, _FIXED, _GOOD_TEST)["ok"] is True
    assert runner.verify("main", "test_r.py", _BUGGY, _FIXED, _WEAK_TEST)["ok"] is False
