"""Graduated autonomy — per-service trust levels enforced deterministically in the state machine
(NOT by the LLM), so an operator dials how much Airbag may do on its own. Reviewed by Gemini 3.1
Pro + 3.5 Flash, who prescribed 4 levels, ADVISORY promotion (never silent) + automatic demotion,
and durable approval state (built on state_store, so it survives Cloud Run container recycles).

  L0 OBSERVE          — never touch prod; decide + report only ("would roll back to X").
  L1 MANUAL_MITIGATE  — gate BEFORE the rollback (approve-to-act); strict "don't touch prod".
  L2 AUTO_MITIGATE    — auto-rollback (stop the bleeding — reversible), but gate the forward fix-PR.
  L3 FULL             — fully autonomous: rollback + fix-PR (the original behavior; default).

Trust ramp: promotion is ADVISORY (after N verified heals we SUGGEST promoting — a human clicks),
demotion is AUTOMATIC (a failed/compensated heal drops an auto level back to L1, fail-safe).
"""
from __future__ import annotations

import time

from . import config, state_store

LEVELS = ("L0", "L1", "L2", "L3")
_COLL = "autonomy"
_APPR = "approvals"


def level_for(service: str) -> str:
    doc = state_store.get(_COLL, service)
    lvl = (doc or {}).get("level")
    return lvl if lvl in LEVELS else config.AUTONOMY_LEVEL


def set_level(service: str, level: str) -> dict:
    if level not in LEVELS:
        raise ValueError(f"invalid autonomy level {level!r}; expected one of {LEVELS}")

    def _m(cur):
        cur = cur or {"service": service, "streak": 0}
        cur["level"] = level
        cur["advise_promote"] = False  # an explicit set clears any standing suggestion
        cur.pop("demoted_from", None)          # a human re-grant clears the demotion breadcrumb (v5 1.3)
        cur.pop("demoted_by_incident", None)   # (harmless when flag off — demoted_from was ephemeral there)
        return cur, dict(cur)
    return state_store.transact(_COLL, service, _m)


def record_outcome(service: str, success: bool, incident_id: str | None = None) -> dict:
    """Update the per-service trust ramp after a heal. On success bump the streak (and SUGGEST a
    promotion at the threshold — advisory only). On failure reset the streak and AUTO-DEMOTE an
    autonomous level back to L1 (fail-safe). Returns the new autonomy record.

    v5 Phase 1.3 (AIRBAG_APPROVAL_COALESCE): the demotion breadcrumb — `demoted_from` and the CAUSING
    `demoted_by_incident` — is preserved across subsequent L1 failures (v2 erased it on EVERY call,
    losing 'why am I at L1' the moment a second storm heal failed), and cleared only by an explicit
    re-grant (set_level). Flag OFF -> byte-identical v2 (breadcrumb erased every call)."""
    coalesce = config.APPROVAL_COALESCE
    def _m(cur):
        cur = cur or {"service": service, "level": config.AUTONOMY_LEVEL, "streak": 0}
        if not coalesce:
            cur.pop("demoted_from", None)   # v2: erased every call (the ergonomics bug 1.3 fixes)
        current_lvl = cur.get("level", config.AUTONOMY_LEVEL)  # may be absent -> read cleanly
        if success:
            cur["streak"] = cur.get("streak", 0) + 1
            cur["advise_promote"] = (cur["streak"] >= config.AUTONOMY_PROMOTE_AFTER
                                     and current_lvl in ("L0", "L1", "L2"))  # advise up to L3
        else:
            cur["streak"] = 0
            cur["advise_promote"] = False
            if current_lvl in ("L2", "L3"):   # a REAL demotion (a repeat failure already at L1 is not)
                cur["demoted_from"] = current_lvl
                cur["level"] = "L1"  # a bad heal revokes autonomy until a human re-grants it
                if coalesce and incident_id:
                    cur["demoted_by_incident"] = incident_id  # the CAUSING incident, recorded once
        return cur, dict(cur)
    return state_store.transact(_COLL, service, _m)


def status(service: str) -> dict:
    doc = state_store.get(_COLL, service) or {}
    return {"service": service, "level": level_for(service),
            "streak": doc.get("streak", 0), "advise_promote": bool(doc.get("advise_promote")),
            "demoted_from": doc.get("demoted_from"),
            "demoted_by_incident": doc.get("demoted_by_incident"),  # v5 1.3: the causing incident
            "promote_after": config.AUTONOMY_PROMOTE_AFTER}


# --- durable approval queue (L1 rollback / L2 fix-PR gates) -----------------------------
def save_approval(incident_id: str, data: dict) -> None:
    state_store.put(_APPR, incident_id, {**data, "incident_id": incident_id,
                                         "created_at": time.time(),
                                         "expires_at": time.time() + config.APPROVAL_TTL_S})


def get_approval(incident_id: str) -> dict | None:
    a = state_store.get(_APPR, incident_id)
    if not a or a.get("expires_at", 0) < time.time():
        return None  # absent or expired (lazy check; native TTL only reclaims storage)
    return a


def claim_approval(incident_id: str) -> dict | None:
    """Atomically read-AND-delete the approval, so a double-clicked Approve can't resume the same
    incident twice (no double rollback / duplicate fix-PRs). Returns it once, then None."""
    now = time.time()

    def _m(cur):
        if not cur or cur.get("expires_at", 0) < now:
            return state_store.KEEP, None   # absent or expired -> nothing to claim
        return None, dict(cur)              # delete + hand it to the single winner
    return state_store.transact(_APPR, incident_id, _m)


def clear_approval(incident_id: str) -> None:
    state_store.delete(_APPR, incident_id)


def pending_approvals() -> list[dict]:
    now = time.time()
    return [a for a in state_store.list_recent(_APPR, 100, "created_at")
            if a.get("expires_at", 0) >= now]
