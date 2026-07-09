"""Phase 1.3 — config env parsing must be FAIL-OPEN: a deploy-time typo in a numeric env var LOGS +
defaults rather than raising at import and crash-looping the independent watchdog (confirmed review
finding). The one nuance: a malformed AIRBAG_SIGNED_NOT_BEFORE disables the DEGRADED strip-detection
signal, but keeping the auditor UP (verifying SIGNED-VERIFIED/INTEGRITY-ONLY/FAIL) beats taking the
whole watchdog offline — and the fallback is logged loudly, not silent.
"""
import config


def test_env_float_defaults_on_missing_and_garbage(monkeypatch):
    assert config._env_float("X_ABSENT", 8.0) == 8.0
    monkeypatch.setenv("X_BAD", "8s")
    assert config._env_float("X_BAD", 8.0) == 8.0          # malformed -> default, never raises
    monkeypatch.setenv("X_OK", "3.5")
    assert config._env_float("X_OK", 8.0) == 3.5


def test_env_int_defaults_on_garbage(monkeypatch):
    monkeypatch.setenv("Y_BAD", "twenty-five")
    assert config._env_int("Y_BAD", 25) == 25
    monkeypatch.setenv("Y_OK", "7")
    assert config._env_int("Y_OK", 25) == 7


def test_signed_not_before_iso_date_does_not_crash(monkeypatch):
    """The exact confirmed input: an ISO date where an epoch float is expected must NOT raise (it would
    crash-loop the container at import); it degrades to None (DEGRADED disabled) instead."""
    monkeypatch.setenv("AIRBAG_SIGNED_NOT_BEFORE", "2026-01-01")
    assert config._env_float("AIRBAG_SIGNED_NOT_BEFORE", None) is None
