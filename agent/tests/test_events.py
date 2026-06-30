"""Event bus: the in-process log contract (default) + the pubsub fan-out helpers (dedup, own-skip).
conftest pins AIRBAG_EVENTS off (inproc); these don't touch real Pub/Sub."""
from autosre import config, events


def setup_function(_):
    events._events.clear()
    events._seen.clear()
    events._offset = 0


def test_inproc_publish_and_get_since(monkeypatch):
    monkeypatch.setattr(config, "EVENTS_BACKEND", "inproc")
    e1 = events.publish({"stage": "A"})
    events.publish({"stage": "B"})
    assert "_eid" in e1 and e1["_src"] == events._SELF   # tagged for fan-out/dedup
    evs, nxt = events.get_since(0)
    assert [e["stage"] for e in evs] == ["A", "B"] and nxt == 2
    evs2, _ = events.get_since(1)
    assert [e["stage"] for e in evs2] == ["B"]           # absolute-index contract intact


def test_append_local_dedups_by_eid():
    ev = {"_eid": "x1", "_src": "other", "stage": "MIRRORED"}
    events._append_local(ev)
    events._append_local(ev)                              # a Pub/Sub redelivery / duplicate
    assert len(events.get_since(0)[0]) == 1               # only once


def test_cross_instance_mirror_skips_own_eid():
    # simulate: we published locally (own _eid recorded), then the same msg comes back via Pub/Sub
    own = events.publish({"stage": "LOCAL"})
    events._append_local(dict(own))                       # fan-out echo of our own event
    assert len(get_stages()) == 1                         # not double-counted


def get_stages():
    return [e["stage"] for e in events.get_since(0)[0]]


def test_pubsub_publish_is_best_effort(monkeypatch):
    # a Pub/Sub failure must NOT break publish() (the heal must keep running)
    monkeypatch.setattr(config, "EVENTS_BACKEND", "pubsub")
    monkeypatch.setattr(events, "_pubsub", lambda: (_ for _ in ()).throw(RuntimeError("no pubsub")))
    e = events.publish({"stage": "C"})                    # must not raise
    assert e["stage"] == "C" and get_stages() == ["C"]   # still delivered locally
