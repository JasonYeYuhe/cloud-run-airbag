"""v4 Phase 1.2 — rollback-target SELECTION: prefer a WITNESSED-healthy revision (serving-history
ledger) over the bare "newest ready" recency proxy, with the cold-start fallback byte-identical to
v3. The framing under test is selection-turns-an-ESCALATE-into-a-heal:

  * bad→bad deploy + empty ledger (v3 behavior): recency picks the landmine → the causal pre-check
    vetoes (COINCIDENT) → ESCALATE. Safe, but a human gets paged even though a proven-good older
    revision exists.
  * bad→bad deploy + ledger: selection proposes the witnessed-good revision → the live probe agrees
    → autonomous heal. No new bypass: a STALE ledger entry (witnessed once, degraded now) is still
    vetoed by the live probe (the ledger only PROPOSES).

conftest pins the mock backend + memory store per test; these tests patch tools.get_backend with a
per-revision world the stock mock can't express (revision-specific probe health)."""
from autosre import config, memory, tools
from autosre.state_machine import (_heuristic, _preferred_target, _rollback_pair, _validate,
                                   run_self_heal)

SERVICE = "badbad-svc"
SERVING = f"{SERVICE}-00012-bad"        # newest, serving 100%, degraded
LANDMINE = f"{SERVICE}-00011-landmine"  # newest READY 0-traffic — recency's pick, ALSO degraded
GOOD = f"{SERVICE}-00009-good"          # older, ready, genuinely healthy


def _revs(landmine_ready: bool = True) -> dict:
    return {"service": SERVICE, "revisions": [
        {"name": SERVING, "ready": True, "traffic_percent": 100,
         "create_time": "2026-07-01T12:00:00Z"},
        {"name": LANDMINE, "ready": landmine_ready, "traffic_percent": 0,
         "create_time": "2026-07-01T10:00:00Z"},
        {"name": GOOD, "ready": True, "traffic_percent": 0,
         "create_time": "2026-06-30T08:00:00Z"},
    ]}


# --- the selection seam (unit) ----------------------------------------------------------
def test_cold_start_is_v3_recency_byte_identical():
    """Empty/None ledger -> the newest ready 0-traffic revision, exactly as before v4."""
    for witnessed in (None, {}):
        serving, target, from_ledger = _rollback_pair(_revs(), witnessed)
        assert (serving, target, from_ledger) == (SERVING, LANDMINE, False)


def test_ledger_prefers_witnessed_over_newer_landmine():
    serving, target, from_ledger = _rollback_pair(_revs(), {GOOD: {"count": 3}})
    assert (serving, target, from_ledger) == (SERVING, GOOD, True)


def test_newest_witnessed_wins_when_several_are():
    _, target, _ = _rollback_pair(_revs(), {GOOD: {}, LANDMINE: {}})
    assert target == LANDMINE   # both witnessed -> recency among witnessed (newest first)


def test_witnessed_but_not_ready_is_skipped():
    _, target, from_ledger = _rollback_pair(_revs(landmine_ready=False), {LANDMINE: {}})
    assert (target, from_ledger) == (GOOD, False)   # landmine witnessed but not ready -> fallback


def test_witnessed_serving_revision_is_not_a_target():
    _, target, from_ledger = _rollback_pair(_revs(), {SERVING: {}})
    assert (target, from_ledger) == (LANDMINE, False)   # serving is never its own rollback target


def test_preferred_target_empty_candidates():
    assert _preferred_target([], {GOOD: {}}) == (None, False)


def test_heuristic_uses_ledger_and_says_so():
    d = _heuristic(_revs(), {"error_rate": 0.9}, {GOOD: {"count": 2}})
    assert d["action"] == "ROLLBACK" and d["rollback_revision"] == GOOD
    assert d["_target_source"] == "ledger" and "WITNESSED" in d["reasoning"]
    cold = _heuristic(_revs(), {"error_rate": 0.9})
    assert cold["rollback_revision"] == LANDMINE and cold["_target_source"] == "recency"


def test_validate_promotion_uses_ledger():
    stat = {"verdict": "FAIL", "reason": "18/20 5xx", "rate": 0.9}
    hedged = {"action": "OBSERVE", "confidence": 0.4, "reasoning": "llm hedged"}
    d = _validate(hedged, _revs(), stat, {GOOD: {}})
    assert d["action"] == "ROLLBACK" and d["rollback_revision"] == GOOD
    assert d["_promoted"] and d["_target_source"] == "ledger"
    cold = _validate(hedged, _revs(), stat)
    assert cold["rollback_revision"] == LANDMINE and cold["_target_source"] == "recency"


# --- v4 RE-AIM: the FSM corrects an LLM aim that has no witnessed history ----------------
# (Observed LIVE on Cloud Run: Gemini hallucinated '100% 5xx' during a latency incident and aimed
# the rollback at the KeyError landmine; the causal probe vetoed safely — but a witnessed-good
# revision existed, so the correct outcome is a re-aimed autonomous heal, not a page.)
def test_llm_wrong_aim_is_reaimed_to_witnessed(monkeypatch):
    monkeypatch.setattr(config, "CAUSAL_CHECK_ENABLED", True)   # the re-aim is licensed by the probe
    stat = {"verdict": "FAIL", "reason": "latency 4/4 windows", "rate": 0.0}
    llm = {"action": "ROLLBACK", "confidence": 1.0, "rollback_revision": LANDMINE,
           "bad_revision": SERVING, "reasoning": "llm aim", "_source": "gemini-adk"}
    d = _validate(llm, _revs(), stat, {GOOD: {"count": 2}})
    assert d["rollback_revision"] == GOOD
    assert d["_target_source"] == "ledger" and d["_target_overridden"] == LANDMINE
    assert "re-aim" in d["reasoning"]


def test_reaim_requires_the_live_probe(monkeypatch):
    """The override is only licensed when the act-time causal probe exists to gate the substituted
    target (review F1: with the probe off, an unprobed stale witness could be WORSE than the LLM's
    aim, and the recorded probe claim would be false) — probe off => the LLM's aim stands."""
    monkeypatch.setattr(config, "CAUSAL_CHECK_ENABLED", False)
    stat = {"verdict": "FAIL", "reason": "x", "rate": 0.9}
    llm = {"action": "ROLLBACK", "confidence": 1.0, "rollback_revision": LANDMINE,
           "bad_revision": SERVING, "reasoning": "llm aim"}
    d = _validate(llm, _revs(), stat, {GOOD: {}})
    assert d["rollback_revision"] == LANDMINE and "_target_overridden" not in d


def test_llm_aim_at_any_witnessed_target_stands_even_if_not_newest(monkeypatch):
    """Kills the mutant the review found surviving (F4): dropping the `target not in witnessed`
    guard would re-aim a witnessed-but-older LLM aim onto the NEWEST witnessed candidate — here
    the landmine. An aim at ANY witnessed target must stand."""
    monkeypatch.setattr(config, "CAUSAL_CHECK_ENABLED", True)
    stat = {"verdict": "FAIL", "reason": "x", "rate": 0.9}
    llm = {"action": "ROLLBACK", "confidence": 1.0, "rollback_revision": GOOD,
           "bad_revision": SERVING, "reasoning": "llm aim"}
    d = _validate(llm, _revs(), stat, {GOOD: {}, LANDMINE: {}})   # newest witnessed = LANDMINE
    assert d["rollback_revision"] == GOOD and "_target_overridden" not in d


def test_llm_aim_at_a_witnessed_target_stands(monkeypatch):
    monkeypatch.setattr(config, "CAUSAL_CHECK_ENABLED", True)
    stat = {"verdict": "FAIL", "reason": "x", "rate": 0.9}
    llm = {"action": "ROLLBACK", "confidence": 1.0, "rollback_revision": GOOD,
           "bad_revision": SERVING, "reasoning": "llm aim"}
    d = _validate(llm, _revs(), stat, {GOOD: {}})
    assert d["rollback_revision"] == GOOD and "_target_overridden" not in d


def test_llm_aim_stands_on_cold_ledger_and_without_stat(monkeypatch):
    monkeypatch.setattr(config, "CAUSAL_CHECK_ENABLED", True)
    llm = {"action": "ROLLBACK", "confidence": 1.0, "rollback_revision": LANDMINE,
           "bad_revision": SERVING, "reasoning": "llm aim"}
    stat = {"verdict": "FAIL", "reason": "x", "rate": 0.9}
    assert _validate(dict(llm), _revs(), stat, {})["rollback_revision"] == LANDMINE   # cold: v3
    assert _validate(dict(llm), _revs(), None, {GOOD: {}})["rollback_revision"] == LANDMINE  # no verdict
    # witnessed exists but contains NO eligible candidate (only the serving revision) -> unchanged
    assert _validate(dict(llm), _revs(), stat, {SERVING: {}})["rollback_revision"] == LANDMINE


# --- integration: the marquee story over a per-revision world ---------------------------
class BadBadWorld:
    """Two consecutive bad deploys: the serving revision AND the newest-ready (recency's pick) are
    both degraded; an older witnessed revision is healthy. Per-revision probes + per-target
    rollback outcomes — the stock mock can't express this."""

    def __init__(self):
        self.routed_to: list[str] = []    # every traffic shift, in order

    def _healthy_serving(self) -> bool:
        return bool(self.routed_to) and self.routed_to[-1] == GOOD

    def list_cloud_run_revisions(self, service, region):
        return _revs()

    def query_error_rate(self, service, region, window_minutes=5, since_epoch=None):
        return {"service": service, "error_rate": 0.0 if self._healthy_serving() else 0.9,
                "total_requests": 40, "window_minutes": window_minutes}

    def fetch_error_logs(self, service, region, n=10):
        return ["KeyError: 'amount'"]

    def sample_business_path(self, service, region, n=20):
        return {"errs": 0 if self._healthy_serving() else 18, "total": 20}

    def sample_latency_windows(self, service, region, windows=4):
        return [{"slow": 0, "total": 20} for _ in range(windows)]

    def probe_revision_health(self, service, region, revision, n=8):
        # PER-REVISION probe: the landmine is confidently degraded; the old revision is clean.
        return {"errs": 0 if revision == GOOD else 8, "total": 8}

    def synthetic_probe(self, service, path="/api/orders"):
        ok = self._healthy_serving()
        return {"ok": ok, "path": path, "status": 200 if ok else 500, "elapsed_ms": 12.0}

    def probe_candidate(self, service, region, revision):
        ok = revision == GOOD
        return {"ok": ok, "errors": 0 if ok else 1, "total": 1}

    def rollback_traffic_to_revision(self, service, region, revision):
        self.routed_to.append(revision)
        return {"status": "success", "service": service, "active_revision": revision}

    def set_traffic_split(self, service, region, splits, tag_revision=None):
        self.routed_to.extend(splits)
        return {"status": "success", "service": service, "traffic": dict(splits)}


def _run(monkeypatch, world: BadBadWorld, causal: bool):
    monkeypatch.setattr(tools, "get_backend", lambda: world)
    monkeypatch.setattr(config, "CAUSAL_CHECK_ENABLED", causal)
    monkeypatch.setattr(config, "VERIFY_ATTEMPTS", 2)
    monkeypatch.setattr(config, "VERIFY_INTERVAL_S", 0.0)
    return run_self_heal(f"inc-badbad-{causal}-{len(world.routed_to)}", SERVICE)


def test_bad_bad_without_ledger_escalates_v3_behavior(monkeypatch):
    """OLD behavior (cold start): recency proposes the landmine; the causal pre-check catches it
    live -> COINCIDENT -> ESCALATE with ZERO traffic shifted. Safe — but a human is paged even
    though a proven-good revision exists. This is the gap the ledger closes."""
    world = BadBadWorld()
    res = _run(monkeypatch, world, causal=True)
    assert res["status"] == "escalated"
    assert world.routed_to == []                       # veto fired BEFORE any shift


def test_bad_bad_without_ledger_and_without_causal_wastes_the_rollback(monkeypatch):
    """OLD behavior with the causal check off: the landmine is rolled onto, verify fails, escalate
    — the reversible action was WASTED on a bad target."""
    world = BadBadWorld()
    res = _run(monkeypatch, world, causal=False)
    assert res["status"] == "escalated"
    assert world.routed_to == [LANDMINE]               # shifted onto the landmine, then failed


def test_bad_bad_with_ledger_heals_autonomously(monkeypatch):
    """THE MARQUEE: the ledger proposes the witnessed-good older revision; the live probe agrees;
    the rollback lands on it and verifies -> autonomous heal instead of an escalation. The landmine
    never receives traffic."""
    memory.witness_serving(SERVICE, GOOD)              # Airbag once watched GOOD serve healthily
    world = BadBadWorld()
    res = _run(monkeypatch, world, causal=True)
    assert res["status"] == "mitigated"
    assert res["rolled_back_to"] == GOOD
    assert LANDMINE not in world.routed_to
    decision = next(e for e in res["events"] if e.get("stage") == "DECISION")
    assert decision.get("_target_source") == "ledger"  # the selection is attributable in the events


def test_llm_wrong_aim_heals_end_to_end_via_reaim(monkeypatch):
    """The live-incident replay: the LLM proposes the landmine during a confident FAIL; the FSM
    re-aims at the witnessed-good revision; the live probe agrees; the heal completes — the page
    that happened live becomes an autonomous heal."""
    from autosre import gemini, state_machine
    memory.witness_serving(SERVICE, GOOD)
    world = BadBadWorld()
    monkeypatch.setattr(state_machine.adk_brain, "decide", lambda *a, **k: None)
    monkeypatch.setattr(gemini, "decide", lambda *a, **k: {
        "action": "ROLLBACK", "confidence": 1.0, "rollback_revision": LANDMINE,
        "bad_revision": SERVING, "reasoning": "hallucinated aim at the landmine",
        "_source": "gemini-test"})
    res = _run(monkeypatch, world, causal=True)
    assert res["status"] == "mitigated"
    assert res["rolled_back_to"] == GOOD
    assert LANDMINE not in world.routed_to
    decision = next(e for e in res["events"] if e.get("stage") == "DECISION")
    assert decision.get("_target_overridden") == LANDMINE


def test_stale_ledger_entry_cannot_bypass_the_live_probe(monkeypatch):
    """GUARDRAIL: the ledger witnessed the landmine long ago (it was healthy then, degraded now).
    Selection proposes it — and the MANDATORY live causal probe vetoes: ESCALATE, zero shift. The
    ledger only ever PROPOSES; it can never launder a stale witness past the act-time probe."""
    memory.witness_serving(SERVICE, LANDMINE)          # stale witness of the now-bad revision
    world = BadBadWorld()
    res = _run(monkeypatch, world, causal=True)
    assert res["status"] == "escalated"
    assert world.routed_to == []
