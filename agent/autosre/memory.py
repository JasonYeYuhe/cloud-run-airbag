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
    return max(config.STAT_BASELINE_FLOOR, b)


def observe_healthy(service: str, rate: float) -> float:
    """Fold a STEADY-STATE healthy 5xx rate (the service running normally at real traffic — i.e. an
    OBSERVE decision, or a PASS verdict) into the per-service EMA baseline.

    Do NOT feed the immediate post-rollback 0.0 here: a momentarily-zero window is not evidence the
    service's steady-state rate is zero, and repeatedly folding 0 would collapse a genuinely-noisy
    service's baseline to the floor — the opposite of the point. The floor is applied at READ time
    (baseline_for) only; the stored EMA stays unclamped so the running statistic isn't corrupted."""
    a = config.BASELINE_ALPHA

    def _m(cur):
        cur = cur or {"service": service}
        prev = cur.get("baseline_rate", config.STAT_BASELINE_RATE)  # seed from the global default
        cur["baseline_rate"] = a * rate + (1 - a) * prev   # unclamped running EMA
        cur["baseline_samples"] = cur.get("baseline_samples", 0) + 1
        cur["updated_at"] = time.time()
        return cur, cur["baseline_rate"]
    return state_store.transact(_COLL, service, _m)


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
