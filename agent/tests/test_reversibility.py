"""v4 Phase 3 — the forward-only / irreversible-deploy guard (autosre/reversibility.py).

The one gap where every other gate GREENLIGHTS a strictly-worse action: a rollback across a deploy
that DECLARED a forward-only change (schema migration) puts pre-migration code in front of the
migrated datastore. The guard HONORS the declared contract (it does NOT detect migrations), fails
OPEN on every ambiguity, ships default-OFF, and only ever converts a rollback into an ESCALATE.
conftest pins the mock backend + resets stores per test."""
from autosre import config, reversibility
from autosre.backends import mock
from autosre.state_machine import run_self_heal

T0, T1, T2 = "2026-06-27T22:00:00Z", "2026-06-28T00:00:00Z", "2026-06-29T00:00:00Z"


def _revs(*rev):
    return {"revisions": list(rev)}


def _r(name, ts, irreversible=False, traffic=0, ready=True):
    return {"name": name, "ready": ready, "traffic_percent": traffic,
            "create_time": ts, "irreversible": irreversible}


# --- the unit contract -----------------------------------------------------------------
def test_guard_disabled_is_a_noop_even_with_a_marker(monkeypatch):
    # pin the flag (env-overridable at import) so an exported AIRBAG_REVERSIBILITY_GUARD=1 in a
    # dev shell can't flip the suite's only default-posture tests (review MINOR)
    monkeypatch.setattr(config, "REVERSIBILITY_GUARD_ENABLED", False)
    revs = _revs(_r("r2", T2, irreversible=True, traffic=100), _r("r1", T0))
    assert reversibility.check(revs, "r1")["verdict"] == "PROCEED"   # default-OFF posture


def test_no_marker_proceeds(monkeypatch):
    monkeypatch.setattr(config, "REVERSIBILITY_GUARD_ENABLED", True)
    revs = _revs(_r("r2", T2, traffic=100), _r("r1", T0))
    v = reversibility.check(revs, "r1")
    assert v["verdict"] == "PROCEED" and "no declared" in v["reason"]


def test_target_predating_a_marker_blocks(monkeypatch):
    monkeypatch.setattr(config, "REVERSIBILITY_GUARD_ENABLED", True)
    revs = _revs(_r("r2", T2, irreversible=True, traffic=100), _r("r1", T0))
    v = reversibility.check(revs, "r1")
    assert v["verdict"] == "BLOCK" and v["marker_revision"] == "r2"


def test_marker_on_an_intermediate_revision_blocks(monkeypatch):
    """R1(good) → R2(migration, declared) → R3(bad serving): rolling R3→R1 crosses R2's marker
    even though the SERVING revision carries none."""
    monkeypatch.setattr(config, "REVERSIBILITY_GUARD_ENABLED", True)
    revs = _revs(_r("r3", T2, traffic=100), _r("r2", T1, irreversible=True), _r("r1", T0))
    assert reversibility.check(revs, "r1")["verdict"] == "BLOCK"


def test_marker_older_than_target_is_not_crossed(monkeypatch):
    """The migration predates the target — the target can read the migrated store; proceed."""
    monkeypatch.setattr(config, "REVERSIBILITY_GUARD_ENABLED", True)
    revs = _revs(_r("r3", T2, traffic=100), _r("r2", T1), _r("r1", T0, irreversible=True))
    assert reversibility.check(revs, "r2")["verdict"] == "PROCEED"


def test_marker_on_the_target_itself_is_not_crossed(monkeypatch):
    monkeypatch.setattr(config, "REVERSIBILITY_GUARD_ENABLED", True)
    revs = _revs(_r("r2", T2, traffic=100), _r("r1", T0, irreversible=True))
    assert reversibility.check(revs, "r1")["verdict"] == "PROCEED"


def test_unknown_times_fail_open(monkeypatch):
    monkeypatch.setattr(config, "REVERSIBILITY_GUARD_ENABLED", True)
    # target time unknown -> can't establish a crossing -> proceed
    revs = _revs(_r("r2", T2, irreversible=True, traffic=100), _r("r1", None))
    assert reversibility.check(revs, "r1")["verdict"] == "PROCEED"
    # serving/marker time unknown -> can't establish a crossing -> proceed
    revs = _revs(_r("r2", None, irreversible=True, traffic=100), _r("r1", T0))
    assert reversibility.check(revs, "r1")["verdict"] == "PROCEED"
    # marker (non-serving) time unknown -> that marker can't establish a crossing -> proceed
    revs = _revs(_r("r3", T2, traffic=100), _r("r2", None, irreversible=True), _r("r1", T0))
    assert reversibility.check(revs, "r1")["verdict"] == "PROCEED"


def test_staged_marker_newer_than_serving_is_not_crossed(monkeypatch):
    """Review MAJOR 1: a migration declared on a staged --no-traffic revision NEWER than serving
    is NOT crossed by rolling serving back — traffic never reached that side. Blocking here would
    suppress a legitimate heal (the worst failure)."""
    monkeypatch.setattr(config, "REVERSIBILITY_GUARD_ENABLED", True)
    revs = _revs(_r("r3-staged", T2, irreversible=True),          # newest, 0% traffic, declared
                 _r("r2-bad", T1, traffic=100),                   # serving the outage
                 _r("r1-good", T0))
    assert reversibility.check(revs, "r1-good")["verdict"] == "PROCEED"


def test_sticky_inherited_marker_does_not_block_forever(monkeypatch):
    """Review MAJOR 2: Cloud Run revision-template annotations are STICKY — after one declared
    migration, later revisions inherit the identical value. Identical values = ONE declaration:
    rollbacks WITHIN the inherited plateau proceed; only crossing the ORIGINAL boundary blocks."""
    monkeypatch.setattr(config, "REVERSIBILITY_GUARD_ENABLED", True)
    revs = _revs(_r("r3-bad", T2, irreversible="true", traffic=100),   # inherited marker, serving
                 _r("r2-good", T1, irreversible="true"),               # inherited marker
                 _r("r0-pre", "2026-06-27T00:00:00Z"))                 # predates the migration
    # within the plateau: target carries the SAME declaration -> not a new crossing -> heal
    assert reversibility.check(revs, "r2-good")["verdict"] == "PROCEED"
    # across the original boundary: the pre-migration revision must still be protected
    assert reversibility.check(revs, "r0-pre")["verdict"] == "BLOCK"


def test_chained_distinct_migrations_block_each_boundary(monkeypatch):
    """Chained migrations with DISTINCT ids: rolling from m2 to m1 crosses m2's own change."""
    monkeypatch.setattr(config, "REVERSIBILITY_GUARD_ENABLED", True)
    revs = _revs(_r("m2", T2, irreversible="orders-v2", traffic=100),
                 _r("m1", T1, irreversible="orders-v1"),
                 _r("r0", T0))
    assert reversibility.check(revs, "m1")["verdict"] == "BLOCK"
    v = reversibility.check(revs, "r0")
    assert v["verdict"] == "BLOCK" and v["marker_revision"] == "m2"


def test_explicit_false_annotation_is_not_a_declaration(monkeypatch):
    monkeypatch.setattr(config, "REVERSIBILITY_GUARD_ENABLED", True)
    revs = _revs(_r("r2", T2, irreversible="false", traffic=100), _r("r1", T0))
    assert reversibility.check(revs, "r1")["verdict"] == "PROCEED"


def test_no_target_and_empty_revs_fail_open(monkeypatch):
    monkeypatch.setattr(config, "REVERSIBILITY_GUARD_ENABLED", True)
    assert reversibility.check(_revs(), None)["verdict"] == "PROCEED"
    assert reversibility.check({}, "r1")["verdict"] == "PROCEED"


def test_not_ready_marker_still_blocks(monkeypatch):
    """A failed deploy that DECLARED a migration may still have run it — a declaration is
    authoritative regardless of readiness."""
    monkeypatch.setattr(config, "REVERSIBILITY_GUARD_ENABLED", True)
    revs = _revs(_r("r3", T2, traffic=100), _r("r2", T1, irreversible=True, ready=False),
                 _r("r1", T0))
    assert reversibility.check(revs, "r1")["verdict"] == "BLOCK"


# --- wired into the heal (mock backend) --------------------------------------------------
def test_declared_marker_escalates_with_zero_traffic_shift(monkeypatch):
    monkeypatch.setattr(config, "REVERSIBILITY_GUARD_ENABLED", True)
    mock.reset()
    mock.declare_irreversible()                      # the serving bad deploy declared a migration
    res = run_self_heal("inc-rev1", "airbag-target")
    assert res["status"] == "escalated"
    stages = [e.get("stage") for e in res["events"]]
    assert "REVERSIBILITY" in stages
    assert "ROLLBACK_APPLIED" not in stages          # zero traffic shifted
    from autosre import incidents
    assert incidents.get("inc-rev1")["reversibility"]["verdict"] == "BLOCK"


def test_no_marker_heals_exactly_as_today(monkeypatch):
    monkeypatch.setattr(config, "REVERSIBILITY_GUARD_ENABLED", True)
    mock.reset()                                     # no declaration anywhere
    res = run_self_heal("inc-rev2", "airbag-target")
    assert res["status"] == "mitigated"              # fail-open: the guard is invisible


def test_flag_off_ignores_the_marker(monkeypatch):
    monkeypatch.setattr(config, "REVERSIBILITY_GUARD_ENABLED", False)   # pin the default posture
    mock.reset()
    mock.declare_irreversible()
    res = run_self_heal("inc-rev3", "airbag-target")  # default posture: guard off
    assert res["status"] == "mitigated"              # demo/live behavior unchanged
    assert "REVERSIBILITY" not in [e.get("stage") for e in res["events"]]


def test_l1_approved_resume_is_also_guarded(monkeypatch):
    """The guard sits in _mitigate, so an L1 rollback approved AFTER a migration shipped is
    re-checked at resume time with FRESH revisions — the approval can't launder the crossing."""
    from autosre import autonomy
    from autosre.state_machine import apply_approval
    monkeypatch.setattr(config, "REVERSIBILITY_GUARD_ENABLED", True)
    autonomy.set_level("airbag-target", "L1")
    mock.reset()
    res = run_self_heal("inc-rev4", "airbag-target")
    assert res["status"] == "awaiting_approval"      # gated before any action
    mock.declare_irreversible()                      # the migration ships WHILE awaiting approval
    out = apply_approval("inc-rev4", approve=True)
    assert out["status"] == "escalated"
    assert "ROLLBACK_APPLIED" not in [e.get("stage") for e in out["events"]]


# --- the committed bench fixtures (guard on vs off — no fifth scorecard mode) ------------
def test_bench_fixture_marker_blocks_and_absence_heals():
    from bench.fixtures import case_by_name
    from bench.harness import run_case
    marked = case_by_name("irreversible_marker_blocks_rollback")
    absent = case_by_name("irreversible_marker_absent_rolls_back")
    r = run_case(marked, reversibility=True)
    assert r.final_action == "ESCALATE" and not r.rolled_back      # blocked pre-shift
    r = run_case(absent, reversibility=True)
    assert r.final_action == "ROLLBACK" and r.status == "mitigated"  # fail-open heal
    r = run_case(marked, reversibility=False)
    assert r.status == "mitigated"                   # guard off: today's behavior, marker ignored
