"""Revision-delta evidence (v5 Phase 5.3) — an LLM-free, deterministic spec diff.

The honest "what changed" FORWARD story for a rollback: how did the bad (currently-serving) revision
differ from the revision we rolled traffic back to? For a latency regression there is no HTTP-500
code bug for a forward fix-PR to repair — the remedy IS the rollback — so Airbag fabricates no PR;
but an operator still deserves to see the concrete spec difference. This module computes exactly
that, from two spec dicts (``backends.*.revision_spec`` / ``tools.revision_spec``):

    spec = {"image": <image ref/digest>, "env_names": [names…], "limits": {cpu, memory, …}}

ACTION-TIER (LLM-free) INVARIANT: this diff can ride the signed proof bundle, so — like causal.py /
reversibility.py — it must stay a deterministic fact, never an LLM output. It imports nothing but the
standard library and is in test_architecture_invariant._action_files(). Env VALUES are deliberately
never read here (revision_spec never returns them): a var NAME is metadata worth surfacing, a value
can be a secret. Behind AIRBAG_REVISION_DELTA (default OFF); flag off -> diff() is never called.
"""
from __future__ import annotations


def diff(bad_spec: dict | None, target_spec: dict | None) -> dict:
    """Deterministic spec diff of the bad (serving) revision vs the rollback target.

    ``bad_spec`` is the currently-serving (degraded) revision; ``target_spec`` is where traffic is
    rolled back TO. So ``env_added`` is what the bad deploy INTRODUCED (present in bad, absent in
    target) and ``env_removed`` is what it DROPPED — the forward "what changed" framing. Tolerant of
    None / partial specs (a spec-fetch that failed reads as empty -> no change), always returns the
    same key set, and every value is JSON-serialisable + order-stable (sorted) so the same inputs
    yield a byte-identical proof digest."""
    bad = bad_spec or {}
    tgt = target_spec or {}
    bad_img, tgt_img = bad.get("image"), tgt.get("image")
    bad_env = {str(n) for n in (bad.get("env_names") or [])}
    tgt_env = {str(n) for n in (tgt.get("env_names") or [])}
    bad_lim = {str(k): str(v) for k, v in (bad.get("limits") or {}).items()}
    tgt_lim = {str(k): str(v) for k, v in (tgt.get("limits") or {}).items()}
    return {
        # the four contract flags (V5_VISION §3 5.3)
        "image_changed": bad_img != tgt_img,
        "env_added": sorted(bad_env - tgt_env),      # in the bad revision, not in the target
        "env_removed": sorted(tgt_env - bad_env),    # in the target, dropped by the bad revision
        "limits_changed": bad_lim != tgt_lim,
        # concrete before/after so the report/proof can render the actual change, not just a bool
        "image_bad": bad_img,
        "image_target": tgt_img,
        "limits_bad": dict(sorted(bad_lim.items())),
        "limits_target": dict(sorted(tgt_lim.items())),
    }
