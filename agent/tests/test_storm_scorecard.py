"""v5 Phase 2 — the STORM SCORECARD ratchet: the committable proof that the storm flags turn the
honest 2026-07-02 shape into storm-safe autonomy, pinned + CI-ratcheted (the AIRBAG_BENCH pattern).

The scorecard measures OUTCOME SHAPE on a deterministic sequential replay; the CONCURRENT
transactional safety (N simultaneous deliveries -> one leader) is proven separately by the threaded
lease-contention tests in test_state_store.py. Both together are the exit criterion.
"""
import json
from pathlib import Path

from bench.storm import run_storm

_DIR = Path(__file__).resolve().parent / "bench"


def _committed(name: str) -> dict:
    return json.loads((_DIR / name).read_text(encoding="utf-8"))


# --- GOLDEN RATCHET: the replay reproduces the committed scorecards exactly ------------------------
def test_flag_off_matches_committed():
    got = run_storm(flag_on=False).to_dict()
    assert got == _committed("storm_scorecard_flagoff.json"), (
        "flag-off storm scorecard drifted — re-run `python tests/bench/run_bench.py --storm --write` "
        "and review the diff (a change to the storm shape must be intentional + reviewed).")


def test_flag_on_matches_committed():
    got = run_storm(flag_on=True).to_dict()
    assert got == _committed("storm_scorecard_flagon.json"), (
        "flag-on storm scorecard drifted — re-run `python tests/bench/run_bench.py --storm --write` "
        "and review the diff.")


# --- THE CONTRACT: flag-on is storm-safe (1/1/0/0); flag-off is the honest ugly baseline -----------
def test_flag_on_is_storm_safe():
    """The marquee claim, as a hard assertion: N distinct alerts for one outage collapse to ONE heal,
    ONE operator card, ZERO self-poisoned detections, ZERO redundant pile-up."""
    sc = run_storm(flag_on=True)
    assert sc.heals_per_outage == 1
    assert sc.approval_cards_per_outage == 1
    assert sc.self_traffic_counted_in_detection == 0
    assert sc.unattended_terminal_states == 0
    # the mechanism: one leader awaits, the rest coalesce (attach) BEFORE triage
    assert sc.statuses[0] == "awaiting_approval"
    assert sc.statuses[1:] == ["attached"] * (sc.n_deliveries - 1)


def test_flag_off_is_the_honest_storm_shape():
    """The pre-registered ugly baseline: every delivery self-amplifies (N heals, N cards), Airbag's
    own probes poison detection, and the cards pile up unattended — the 2026-07-02 pathology."""
    sc = run_storm(flag_on=False)
    n = sc.n_deliveries
    assert sc.heals_per_outage == n                       # every alert ran its own full heal
    assert sc.approval_cards_per_outage == n              # ... and filed its own approval card
    assert sc.self_traffic_counted_in_detection > 0       # Airbag's own probe 5xx counted as user 5xx
    assert sc.unattended_terminal_states == n - 1         # N-1 redundant cards pile up + expire silently
    assert sc.statuses == ["awaiting_approval"] * n       # no coalescing — every delivery gates separately


def test_flags_strictly_improve_every_metric():
    """No metric regresses flag-on vs flag-off; every one strictly improves (the ratchet direction).
    (Honesty: in this pure-awaiting storm `approval_cards` and `unattended` move together — they are
    distinct measurements, the approval store vs the run outcomes, that agree on this scenario; and
    `self_traffic` is over-determined here, zeroed by EITHER storm flag. The real self-traffic filter
    and the one-leader coalesce are each proven independently at the unit level — see
    test_probe_marking.py and test_state_store.py.)"""
    off, on = run_storm(flag_on=False), run_storm(flag_on=True)
    assert on.heals_per_outage < off.heals_per_outage
    assert on.approval_cards_per_outage < off.approval_cards_per_outage
    assert on.self_traffic_counted_in_detection < off.self_traffic_counted_in_detection
    assert on.unattended_terminal_states < off.unattended_terminal_states
