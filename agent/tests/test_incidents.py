"""Each run is persisted as a verifiable incident Artifact; the read-only endpoints serve it."""
from fastapi.testclient import TestClient

import app as appmod
from autosre import incidents
from autosre.backends import mock
from autosre.state_machine import complete_rollback, run_self_heal

SVC = "airbag-target"


def test_heal_then_undo_records_a_full_artifact():
    mock.reset()
    run_self_heal("inc-art", SVC)
    rec = incidents.get("inc-art")
    assert rec and rec["status"] == "mitigated"
    assert rec["decision"]["action"] == "ROLLBACK"
    assert rec["error_before"] and rec["error_after"] == 0.0
    n_after_heal = len(rec["events"])
    assert n_after_heal >= 6

    mock.deploy_fix()
    complete_rollback(SVC, fix_revision=f"{SVC}-00003-fix")
    rec = incidents.get("inc-art")
    assert rec["status"] == "closed"
    assert rec["restored_to"] == f"{SVC}-00003-fix"
    assert len(rec["events"]) > n_after_heal  # transaction events merged into the same record


def test_get_returns_isolated_snapshot():
    from autosre import incidents as inc
    inc.record("iso-1", {"events": [{"ts": 1, "stage": "A"}]})
    snap = inc.get("iso-1")
    inc.record("iso-1", {"events": [{"ts": 2, "stage": "B"}]})  # mutate the live record after
    assert len(snap["events"]) == 1            # the snapshot must NOT have grown
    assert len(inc.get("iso-1")["events"]) == 2


def test_incident_endpoints():
    mock.reset()
    run_self_heal("inc-ep", SVC)
    c = TestClient(appmod.app)
    assert any(i["incident_id"] == "inc-ep" for i in c.get("/incidents").json()["incidents"])
    assert c.get("/incidents/inc-ep").json()["status"] == "mitigated"
    r = c.get("/incidents/inc-ep/report")
    assert r.status_code == 200 and "thought-chain" in r.text.lower() and "ROLLBACK" in r.text
    assert c.get("/incidents/does-not-exist").status_code == 404
