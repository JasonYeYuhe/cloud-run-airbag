"""Smoke test: the whole self-heal loop runs end-to-end in the mock backend."""
from autosre import config
from autosre.backends import mock
from autosre.state_machine import run_self_heal


def setup_function(_):
    mock.reset()
    config.GEMINI_API_KEY = ""  # force the deterministic heuristic (offline, fast, no key)


def test_mock_heal_mitigates():
    result = run_self_heal("inc-test", "airbag-target")
    assert result["status"] == "mitigated"
    assert result["rolled_back_to"].endswith("-good")
    stages = [e["stage"] for e in result["events"]]
    assert "ROLLBACK_APPLIED" in stages
    assert "MITIGATED" in stages


def test_no_error_means_noop():
    mock._STATE["rolled_back"] = True  # error rate reads 0 -> OBSERVE, not ROLLBACK
    result = run_self_heal("inc-quiet", "airbag-target")
    assert result["status"] == "noop"
