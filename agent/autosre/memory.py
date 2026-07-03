"""Cross-incident memory + a LEARNED per-service baseline (v2), on the durable store.

Two jobs, both per-service (collection "service_memory", doc id = service):

1. Learned baseline — the Theme-1 follow-up Gemini 3.1 Pro + 3.5 Flash both flagged: the rollback
   analyzer should NOT compare against a hardcoded global 5xx rate. We fold STEADY-STATE healthy
   observations — an OBSERVE decision or a PASS verdict (the service running normally at real
   traffic) — into an EMA (floored at read), so a normally-noisy service isn't perpetually flagged.
   We deliberately do NOT fold the momentary post-rollback 0.0 (it would collapse the baseline).
   analyzer.baseline_rate then comes from here.

2. Incident memory — count + a bounded recent history of failures per service, with recurrence
   detection ("this is the 3rd 5xx on /api/orders this hour — the fix isn't holding"). Surfaced as
   an advisory RECURRING signal (it does not, by itself, change the action).

3. Serving-history ledger (v4 Phase 1) — a bounded map of revisions Airbag has WITNESSED serving
   healthily (a PASS/clean-OBSERVE no-op run, or a verified mitigation target). The rollback
   selector PREFERS a witnessed-healthy target over the bare "newest ready" recency proxy, which a
   bad→bad deploy sequence defeats. The ledger only PROPOSES: whatever it picks still flows through
   the live causal pre-check before any traffic shifts, so a stale entry can never bypass the probe.
"""
from __future__ import annotations

import time

from . import config, state_store

_COLL = "service_memory"


# --- learned baseline ----------------------------------------------------------------
def baseline_for(service: str) -> float:
    """The analyzer's per-service baseline: the learned EMA (floored), or the config default if
    this service has no healthy history yet."""
    m = state_store.get(_COLL, service) or {}
    b = m.get("baseline_rate")
    if b is None:
        return config.STAT_BASELINE_RATE
    try:  # defensive: a non-float from store drift / a hand-edited doc must not crash triage
        b = float(b)
    except (TypeError, ValueError):
        return config.STAT_BASELINE_RATE
    return max(config.STAT_BASELINE_FLOOR, b)


def observe_healthy(service: str, rate: float) -> float:
    """Fold a STEADY-STATE healthy 5xx rate (the service running normally at real traffic — i.e. an
    OBSERVE decision, or a PASS verdict) into the per-service EMA baseline.

    Do NOT feed the immediate post-rollback 0.0 here: a momentarily-zero window is not evidence the
    service's steady-state rate is zero, and repeatedly folding 0 would collapse a genuinely-noisy
    service's baseline to the floor — the opposite of the point. The floor is applied at READ time
    (baseline_for) only; the stored EMA stays unclamped so the running statistic isn't corrupted."""
    a = config.BASELINE_ALPHA
    rate = min(1.0, max(0.0, float(rate)))   # a rate is a proportion in [0,1]; never corrupt the EMA

    def _m(cur):
        cur = cur or {"service": service}
        prev = cur.get("baseline_rate", config.STAT_BASELINE_RATE)  # seed from the global default
        new = a * rate + (1 - a) * prev                    # running EMA
        if config.BASELINE_GUARD:   # v5 5.2: clamp per-fold drift so one anomalous sample can't jerk it
            drift = config.BASELINE_MAX_FOLD_DRIFT
            new = max(prev - drift, min(prev + drift, new))
        cur["baseline_rate"] = new
        cur["baseline_samples"] = cur.get("baseline_samples", 0) + 1
        cur["updated_at"] = time.time()
        return cur, cur["baseline_rate"]
    return state_store.transact(_COLL, service, _m)


# --- serving-history ledger (v4): witnessed-healthy revisions -------------------------
def witness_serving(service: str, revision: str) -> dict | None:
    """Stamp `revision` as WITNESSED serving healthily (name + timestamp + a witness count), on the
    same per-service doc as the learned baseline (one durable doc, one transact pattern).

    Callers stamp only on CONFIDENT evidence: a PASS verdict / a zero-error OBSERVE no-op run, or a
    verified mitigation target (after `_verify` proves recovery on the triggering signal). A flaky
    sub-threshold window or an unverified post-rollback shift must NOT certify a revision — the
    rollback selector will later trust this map to PROPOSE a target (the live causal pre-check
    still re-probes whatever it proposes).

    Bounded at WITNESS_MAX revisions per service, evicting the least-recently-witnessed. The
    mutator is pure (a Firestore transaction retry re-reads and re-runs it side-effect-free)."""
    if not revision:
        return None

    def _m(cur):
        now = time.time()   # inside the mutator: a transaction retry restamps with fresh time
        cur = cur or {"service": service}
        w = dict(cur.get("witnessed") or {})
        ent = dict(w.get(revision) or {})
        ent["last_witnessed_at"] = now
        ent["count"] = ent.get("count", 0) + 1
        w[revision] = ent
        while len(w) > config.WITNESS_MAX:   # bounded: drop the least-recently-witnessed
            del w[min(w, key=lambda k: w[k].get("last_witnessed_at", 0))]
        cur["witnessed"] = w
        cur["updated_at"] = now
        return cur, dict(w.get(revision) or {})
    return state_store.transact(_COLL, service, _m)


def witnessed_healthy(service: str) -> dict:
    """The witnessed-healthy map {revision: {last_witnessed_at, count}} for this service — read by
    the rollback target selector. Empty on cold start (the selector then falls back to recency)."""
    m = state_store.get(_COLL, service) or {}
    w = m.get("witnessed")
    return dict(w) if isinstance(w, dict) else {}


# --- incident memory + recurrence ----------------------------------------------------
def record_incident(service: str, signature: str, status: str,
                    rolled_back_to: str | None = None) -> dict:
    """Remember an incident outcome (bounded recent history) for recurrence detection + the report."""
    now = time.time()

    def _m(cur):
        cur = cur or {"service": service}
        cur["incident_count"] = cur.get("incident_count", 0) + 1
        cur["last_incident_at"] = now
        cur["last_signature"] = signature
        recent = list(cur.get("recent", []))  # copy: never mutate cur's list in a retryable callback
        recent.append({"ts": now, "signature": signature, "status": status,
                       "rolled_back_to": rolled_back_to})
        cur["recent"] = recent[-config.MEMORY_RECENT_MAX:]
        return cur, dict(cur)
    return state_store.transact(_COLL, service, _m)


def recurrence(service: str, signature: str, window_s: float | None = None) -> int:
    """How many incidents with this signature happened within the look-back window (incl. priors)."""
    window_s = window_s if window_s is not None else config.RECUR_WINDOW_S
    now = time.time()
    m = state_store.get(_COLL, service) or {}
    return sum(1 for r in m.get("recent", [])
               if r.get("signature") == signature and (now - r.get("ts", 0)) <= window_s)


def summary(service: str) -> dict:
    m = state_store.get(_COLL, service) or {}
    b = m.get("baseline_rate")
    baseline = max(config.STAT_BASELINE_FLOOR, b) if b is not None else config.STAT_BASELINE_RATE
    return {"service": service, "baseline_rate": round(baseline, 4),  # reuse m (one read)
            "baseline_samples": m.get("baseline_samples", 0),
            "incident_count": m.get("incident_count", 0),
            "last_incident_at": m.get("last_incident_at"),
            "last_signature": m.get("last_signature"), "recent": m.get("recent", [])}
