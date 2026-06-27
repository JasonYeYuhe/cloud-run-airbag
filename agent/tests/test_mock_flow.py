"""Smoke test: the whole self-heal loop runs end-to-end in MOCK mode."""
from autosre import tools
from autosre.state_machine import run_self_heal


def setup_function(_):
    tools._MOCK["rolled_back"] = False  # reset mock world between tests


def test_mock_heal_mitigates():
    result = run_self_heal("inc-test", "airbag-target")
    assert result["status"] == "mitigated"
    assert result["rolled_back_to"].endswith("-good")
    stages = [e["stage"] for e in result["events"]]
    assert "ROLLBACK_APPLIED" in stages
    assert "MITIGATED" in stages


def test_no_error_means_noop():
    tools._MOCK["rolled_back"] = True  # error rate reads 0 -> OBSERVE, not ROLLBACK
    result = run_self_heal("inc-quiet", "airbag-target")
    assert result["status"] == "noop"
