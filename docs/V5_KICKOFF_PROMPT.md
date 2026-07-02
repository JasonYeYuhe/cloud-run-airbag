# v5 kickoff prompt — paste the fenced block below into a fresh dev session

Plan of record: `docs/V5_VISION.md` (Gemini 3.1 Pro reviewed twice: CHANGES-NEEDED → all 7
findings fixed → **GO**). This prompt is the self-contained handoff.

```
You are continuing development of "Airbag" — an autonomous release safety net for Google Cloud
Run, built for the DevOps × AI Agent Hackathon 2026 (required stack: Gemini + ADK + Cloud Run;
SUBMISSION DEADLINE 2026-07-10). v2/v3/v4 are complete, LIVE, and verified; your job is v5.

ORIENT FIRST (read before doing anything):
- Repo: /Users/jason/Documents/AI Agent Hackathon/cloud-run-airbag (branch main)
- Read docs/V5_VISION.md END TO END — the plan of record (25-agent grounded planning workflow →
  Gemini 3.1 Pro review round 1 CHANGES-NEEDED → all findings incorporated (§0) → confirm round
  GO). docs/V4_VISION.md is the prior stage. docs/AIRBAG_BENCH.md is the measuring stick.
- Your auto-memory has the full history; trust it but VERIFY file/line refs before acting.
- PYTHON ENV: use the repo-root venv — run tests as
  `cd agent && ../.venv-demo/bin/python -m pytest -q`. Anaconda is NOT the project env (its
  fastapi is ancient); never pip-install into it.

WHAT AIRBAG DOES (the thesis — do NOT break it): detect a bad Cloud Run deploy (even hours later,
multi-signal 5xx+latency, Wilson-gated) → ADK/Gemini DIAGNOSES → a DETERMINISTIC FSM VALIDATES
(confidence + target gates + statistical promotion + v4: witnessed-healthy LEDGER target
selection, FSM re-aim of unwitnessed LLM aims, latency-axis causal probe, irreversibility guard)
→ ACTS (traffic rollback) → proves recovery ON THE TRIGGERING SIGNAL → fix PR (sandbox-verified)
→ keyless WIF CI close (verify → canary 10/50/100 → CLOSED). THE INVARIANT: Gemini diagnoses,
the FSM acts — the action tier (backends/*, signals/*, tools.py, causal.py, memory.py,
reversibility.py) NEVER imports the LLM; agent/tests/test_architecture_invariant.py (AST-based)
guards it; add any NEW action-tier module to its _action_files().

THE v5 THESIS (from docs/V5_VISION.md — grounded in a REAL live storm on 2026-07-02):
v3 made detection trustworthy, v4 made the action provably correct; v5 makes THE AGENT ITSELF
production-safe — "storm-safe autonomy". The live storm (first-hand evidence, all designed
behaviors composing into an undesigned outcome): (1) the causal probe's 8 requests against a
broken 0%-traffic tag target produced 8 REAL 5xx that FIRED the very Cloud Monitoring alert being
diagnosed; (2) every dedup keys on the Monitoring incident id (seen_and_mark state_store.py:138,
claim_heal :151), so N alert deliveries for ONE broken service spawned N full heal runs; (3) one
verify-failure demoted the service L3→L1 (designed trust ramp) and every subsequent storm heal
filed its own approval card — piling up, expiring silently; (4) a human untangled it manually.
The July-2026 market sweep (in V5_VISION §1) confirms NO vendor even names this failure class.

BUILD ORDER (details + file:line in V5_VISION §3 — follow it; all flags default OFF, demo
byte-identical until flipped):
  Phase 1.1 — service-level correlation lease: claim_service_heal in state_store.py beside
    claim_heal (mirror pending.try_begin_complete, pending.py:39 — the codebase's proven
    per-service lease). Leader runs; followers ATTACH incident_ids (transactional append +
    ATTACHED event) and ack. LIFECYCLE (Gemini BLOCKER fix — spec'd, don't improvise): lease
    holds while the outcome is UNSETTLED (running, or escalated/awaiting with a live
    approval/pending state), TTL only as crash backstop; released on mitigated/noop; re-fires
    beyond the lease coalesce onto the 1.3 approval card; a follower finding a TERMINALLY-failed
    leader claims a FRESH lease. Flag AIRBAG_STORM_COALESCE. Proof: THREADED lease-contention
    tests (N concurrent claims → exactly 1 leader; the test_state_store.py pattern) on memory +
    emulator, dead-leader takeover, hold-while-unsettled; live N-alerts→1-heal.
  Phase 1.2 — observer-safe diagnostics: stamp User-Agent airbag-probe/1 + X-Airbag-Probe on the
    FIVE diagnostic httpx clients in backends/gcp.py (lines ~109/164/211/258/339); _burst in
    app.py STAYS UNMARKED (it simulates USERS — pin with comment + test). query_error_rate's log
    filter excludes the probe UA behind AIRBAG_SELF_TRAFFIC_EXCLUDE (httpRequest.userAgent IS in
    Cloud Run request logs). Guard test (spirit of the AST invariant): every diagnostic client in
    backends/ carries the marker. infra/alert-setup-v2.sh = ADDITIVE log-based 5xx metric
    excluding the probe UA + policy; cut over live only after verification. HONESTY SCOPE
    (Gemini fix): the exclusion covers DETECTION/COUNT paths only — app-emitted tracebacks
    (fetch_error_logs) and the built-in console metric are explicitly out of scope (state it).
  Phase 1.3 — approval coalescing + settlement: approvals keyed
    sha256(service|kind|proposed_target|primary_signal) (the signal term is a Gemini BLOCKER fix
    — a 5xx card carries a fix-PR consequence, a latency card must not); second gated heal
    attaches + bumps a count on the SAME card; one decision settles ALL attached (each gets its
    terminal event/record); heal-settle sweeps stale cards (audited, never silent). Demotion:
    KEEP the trust ramp — only fix bookkeeping (stop erasing demoted_from on later L1 failures,
    record the causing incident, ONE operator card). Flag AIRBAG_APPROVAL_COALESCE.
  Phase 2 — the STORM SCORECARD (the committable proof): a scenario layer over the bench harness
    replaying the 2026-07-02 shape (N alert deliveries, distinct ids, one service + probe-feedback
    injection + a scripted verify-failure → demotion), driving the REAL seams sequentially
    (deterministic — no thread flake). Metrics: heals_per_outage, approval_cards_per_outage,
    self_traffic_counted_in_detection, unattended_terminal_states, blind_landings. COMMIT BOTH
    scorecards: flag-off (the honest ugly baseline) AND flag-on (1/1/0/0) — pre-registered, CI
    ratcheted. HONEST FRAMING (Gemini fix): the scorecard measures OUTCOME SHAPE deterministically;
    concurrency safety is proven by 1.1's threaded lease tests — both together are the exit.
  Phase 3.1 — witness-freshness horizon (WITNESS_FRESH_S default 7d; last_witnessed_at is already
    stored, memory.py:88, today used only for eviction) + BLIND-LANDING VISIBILITY: causal.py
    returns a machine-readable probe_errored flag; on probe-error + unwitnessed target _mitigate
    does ONE bounded retry then PROCEEDS FAIL-OPEN with a first-class blind_landing marker
    (Gemini MAJOR fix: the original ESCALATE contradicted the locked "never block a legit
    rollback" posture — measure it, never block). Flag AIRBAG_TARGET_EVIDENCE (documented no-op
    unless AIRBAG_CAUSAL_CHECK is also on).
  Phase 3.2 — close-time settlement (flag AIRBAG_CLOSE_SETTLEMENT): CLOSED witnesses the fix
    revision + credits the trust ramp WITHOUT double-counting (persist outcome_counted on the
    pending record; mitigate-time record_outcome already counted this incident).
  Phase 4.1 — fix-path write hardening (the path check is a HARD GATE, not a flag — it's an
    active prompt-injection→workflow-write vulnerability): reject LLM-chosen path/test_path
    outside the CONFIGURABLE allowlist AIRBAG_FIX_ALLOWLIST (default = the directory of the
    existing AIRBAG_FIX_FILE config; normalized, no '..', .github/ rejected unconditionally)
    (github_pr.py commits unvalidated paths at :62-64/:82-88 and reuses ANY open airbag/fix* PR
    at :47 — key reuse on the RCA error signature instead); loop-exit truthfulness; thread the
    discovered path/test_path into CI self-correction (today corrections hardcode
    config.FIX_FILE). NOTE: the full live CI-self-correct revival needs Jason to grant the GitHub
    token Checks:read — ship the mechanical fixes regardless.
  Phase 4.2 — KMS-signed proof bundle (flag AIRBAG_PROOF_SIGN, fail-open → degrade to digest):
    persist the canonical bundle SNAPSHOT on the record at MITIGATED/CLOSED (the record mutates
    later — sign the snapshot, never a rebuild), sign via Cloud KMS asymmetricSign
    (EC_SIGN_P256_SHA256) over httpx+ADC (zero new deps), commit the public PEM +
    scripts/verify-proof.py, infra/kms-setup.sh (SA gets signerVerifier only). Honesty: proves
    provenance, NOT decision correctness.
  Build-only-if-ahead (in order; V5_VISION §3): 5.1 pooled-Wilson SLO burn-rate detector +
    5.2 baseline integrity guard (must ship together), 5.3 revision-delta evidence.

THE FLOOR (non-negotiable if time binds): 1.1 + 1.2 + Phase-2 scorecard + the 4.1 path allowlist.
Descope ladder (V5_VISION §6): cut 5.x → 4.2 → 3.2 → descope 1.3 to coalescing-only → descope
Phase 2 to the flag-off baseline (pre-registered, labeled "mitigations pending").

CORRECTED FACTS (verified against code during planning — do not re-derive wrongly):
- The ONLY per-service lease today is pending.try_begin_complete; both heal dedups key on the
  Monitoring incident id. save_approval keys strictly on incident_id (autonomy.py:73-76).
- The alert policy uses the BUILT-IN request_count metric (infra/alert-setup.sh:37) — header
  filtering is impossible there; observer-exclusion needs the ADDITIVE log-based metric.
- Gated (L1) heals still emit triage/detection probes BEFORE the autonomy gate
  (state_machine.py:~66-101) — 1.1's attach path returning before triage is the main damper.
- Five unmarked diagnostic clients in backends/gcp.py; zero traffic-marking exists anywhere.
- complete_rollback's CLOSED branch neither witnesses the fix nor credits the trust ramp, while
  its canary-fail path DOES demote (the asymmetry 3.2 fixes).
- Nominal effort P1-P4 ≈ 9d vs ~4-5 real days — the same over-provision ratio v4 shipped with.

CURRENT LIVE STATE: agent rev 00035 (project airbag-hack-260628, asia-northeast1;
AIRBAG_SIGNALS=all, AIRBAG_CAUSAL_CHECK=1 ON; reversibility guard OFF; state=firestore,
events=pubsub, max-instances 3). Demo baseline: airbag-target-00024 (healthy, serving 100%,
NEWEST) + 00023 (slow, 0%) + 00022 (bug, 0%) — ALWAYS leave it that way; after a real
Verify&Undo re-run scripts/gcp-demo-setup.sh. Deploy agent-only: PROJECT=airbag-hack-260628
AGENT_ONLY=1 ./deploy.sh. 254 tests (246 agent + 8 mcp-server), 5 CI jobs green (incl.
firestore-emulator). Live proof bundles: docs/proof/. gcloud + gh authed; agy (Gemini CLI)
available but flaky — run models sequentially, check ~/.gemini/antigravity-cli/brain/ artifacts
on timeout; a multi-agent Workflow review is the robust substitute. Secrets in Secret Manager +
agent/.env (gitignored) — NEVER print or commit secrets.

HOW TO WORK (the established cadence): TDD each item (write tests alongside; full suite + ruff
E9,F before every commit); run the bench ratchet + architecture-invariant test on every commit
touching state_machine/state_store/autonomy/causal/backends/signals/memory; adversarial review
(agy Gemini 3.1 Pro and/or a multi-agent workflow, refute-by-default) BEFORE each substantial
commit and APPLY the fixes; live-verify on real Cloud Run where it matters; flip the storm flags
ON in deploy.sh only AFTER live verification (alert-policy v2 cutover only after the demo video
is recorded — coordinate with Jason); keep ONE consistent test count across docs; commit+push to
main as you go; keep google-adk pinned 1.x. NON-REDUNDANCY RULE: never build a check a
deterministic gate already enforces. Respect V5_VISION §4's cut list (auditor agent + MCP
governance deferred to finals week; no fleet UI, no second LLM pass, don't delete the trust ramp
to fix its ergonomics, never mark _burst as probe traffic).

The §8 open questions are answered at their defaults (storm flags ON after live verify; KMS on;
burn-rate only if ahead; Checks:read is Jason's task; auditor deferred; allowlist hard-gated).
Only ask Jason if you hit a NEW decision the code/docs can't resolve. Start by reading
docs/V5_VISION.md end to end, then begin Phase 1.1 (the correlation lease).
```
