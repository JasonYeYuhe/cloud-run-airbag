"""Multi-signal detection engine (v3 Phase 1).

Pluggable Detectors each emit a FAIL/PASS/INCONCLUSIVE verdict; a fusion step combines them into ONE
verdict shaped exactly like ``analyzer.analyze``'s output, which ``state_machine._validate`` consumes.
When only the 5xx detector is enabled (``AIRBAG_SIGNALS=5xx``, the default), ``detect()`` returns the
5xx Wilson verdict verbatim — the single-signal behavior is unchanged.

DETERMINISTIC + LLM-FREE by design (detection is statistical, not a Gemini call) — enforced by
``test_architecture_invariant`` (signals/ must not import the LLM). That is what lets a confident
verdict here deterministically drive a rollback via ``_validate``'s promotion, without the LLM ever
touching prod: the FSM acts on a *statistical* signal, the LLM only advises.
"""
from .engine import SignalContext, detect, enabled_detectors  # noqa: F401
