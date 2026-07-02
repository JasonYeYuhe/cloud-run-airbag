"""Forward-only / irreversible-deploy guard (v4 Phase 3) — the one gap where every other gate
GREENLIGHTS a strictly-worse action.

A deploy that performs a forward-only change (a schema migration, a one-way data backfill)
DECLARES it with a Cloud Run revision annotation (`airbag.dev/irreversible`, surfaced by the
backends as the revision dict's `irreversible` field). Rolling traffic back ACROSS such a marker
puts code that cannot read the migrated datastore in front of it: the old revision boots fine, a
GET probe returns 200 (a synthetic probe can't exercise a mutation), the causal pre-check passes,
`_verify` can pass — and every write corrupts. The rollback makes the outage strictly WORSE, so
the correct action is ESCALATE to a human.

THE CONTRACT (what a deploy declares, and what the guard honors):
  * The annotation VALUE identifies the forward-only change — ideally a unique migration id
    (e.g. `2026-07-02-orders-v2`); the literal `true` works for a one-off.
  * Cloud Run revision-template annotations are STICKY (every later deploy inherits them until
    removed), so IDENTICAL values on consecutive revisions are treated as ONE declaration, not a
    new change per revision — an inherited leftover can never freeze all future rollbacks
    (the adversarial review's second MAJOR). Consequence: CHAINED distinct migrations need
    DISTINCT values; two back-to-back migrations both labeled `true` read as one declaration
    (documented residual — use ids).
  * A marker is CROSSED only when it lies on the traffic path being reversed:
    epoch(target) < epoch(marker) <= epoch(serving). A migration declared on a staged
    --no-traffic revision NEWER than serving is not crossed by rolling serving back
    (the review's first MAJOR: traffic never reached that side).

HONESTY — what this is and is not:
  * It HONORS a declared contract. It does NOT detect migrations; an undeclared forward-only
    deploy is invisible to it (that detection is unknowable from the outside).
  * It fails OPEN: no marker → PROCEED, byte-identical to today. Unknown create_times or no
    serving revision (the crossing can't be established) → PROCEED. Guard disabled (default) →
    PROCEED.
  * It is DETERMINISTIC and LLM-free (enforced by the AST architecture invariant), and it only
    ever converts a rollback into an ESCALATE — it never selects, walks, or shifts traffic.
  * Marker readiness is deliberately ignored — a failed deploy that declared a migration may
    still have run it; a declaration is authoritative.
"""
from __future__ import annotations

import datetime
import logging

from . import config

log = logging.getLogger("airbag.reversibility")


def _epoch(iso: str | None) -> float:
    if not iso:
        return 0.0
    try:
        return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except Exception:  # noqa: BLE001 — unparseable time = unknown = fail-open
        return 0.0


def _marker_value(rev: dict | None) -> str | None:
    """The revision's declared forward-only identity: the annotation value as a normalized string
    (backends surface it in the `irreversible` field; bench fixtures may use a plain bool).
    Explicit negations ('false'/'0'/'no'/'off') mean NOT declared."""
    v = (rev or {}).get("irreversible")
    if v is True:
        return "true"
    if isinstance(v, str):
        s = v.strip().lower()
        return s if s and s not in ("false", "0", "no", "off", "none") else None
    return None


def check(revs: dict, target: str | None) -> dict:
    """Would rolling traffic to `target` cross a DECLARED irreversibility marker?
    Returns {verdict: "PROCEED"|"BLOCK", reason, ...evidence}. Fail-open on every ambiguity."""
    if not config.REVERSIBILITY_GUARD_ENABLED:
        return {"verdict": "PROCEED", "reason": "reversibility guard disabled (default)"}
    rs = (revs or {}).get("revisions", [])
    if not target:
        return {"verdict": "PROCEED", "reason": "no rollback target — nothing to guard"}
    t = next((r for r in rs if r.get("name") == target), None)
    t_epoch = _epoch((t or {}).get("create_time"))
    if t_epoch <= 0:
        return {"verdict": "PROCEED",
                "reason": f"target {target} create_time unknown — cannot establish a crossing; "
                          f"fail-open (rollback proceeds)"}
    serving = max(rs, key=lambda r: r.get("traffic_percent", 0), default=None)
    if not serving or serving.get("traffic_percent", 0) <= 0:
        return {"verdict": "PROCEED",
                "reason": "no serving revision — cannot establish which side of a marker traffic "
                          "is on; fail-open (rollback proceeds)"}
    s_epoch = _epoch(serving.get("create_time"))
    if s_epoch <= 0:
        return {"verdict": "PROCEED",
                "reason": f"serving revision {serving.get('name')} create_time unknown — cannot "
                          f"establish a crossing; fail-open (rollback proceeds)"}
    t_val = _marker_value(t)
    markers = [r for r in rs if _marker_value(r)]
    for m in markers:
        m_epoch = _epoch(m.get("create_time"))
        m_val = _marker_value(m)
        # crossed = the marker sits on the traffic path being reversed (newer than the target,
        # not newer than serving), AND it declares a change the target does not itself carry
        # (identical values = one sticky/inherited declaration, not a new change per revision).
        if 0 < m_epoch and t_epoch < m_epoch <= s_epoch and m_val != t_val:
            return {"verdict": "BLOCK", "marker_revision": m.get("name"), "target": target,
                    "marker_value": m_val,
                    "reason": (f"rollback target {target} PREDATES revision {m.get('name')}, which "
                               f"DECLARED a forward-only change ({config.IRREVERSIBLE_ANNOTATION}="
                               f"{m_val} — e.g. a schema migration) on the serving lineage. "
                               f"Rolling back across it would put code that cannot read the "
                               f"migrated datastore in front of it; escalating to a human instead "
                               f"of making the outage worse")}
    return {"verdict": "PROCEED",
            "reason": ("no declared irreversibility marker crossed"
                       if not markers else
                       "declared marker(s) are not crossed by this rollback (outside the "
                       "target→serving span, or carried identically by the target itself)")}
