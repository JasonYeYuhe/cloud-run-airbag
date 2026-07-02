# v4 kickoff — paste this into a fresh session to start Airbag v4 seamlessly

> Self-contained handoff. The new session has no prior context; everything it needs is below or in
> the repo. (This file is the artifact; the fenced block is what you paste.)

---

```
You are continuing development of "Airbag" — an autonomous release safety net for Google Cloud Run,
built for the DevOps × AI Agent Hackathon 2026 (required stack: Gemini + ADK + Cloud Run). v1, v2, and
v3 are complete, LIVE, and verified; your job is to build v4.

ORIENT FIRST (read these before doing anything):
- The repo is at: /Users/jason/Documents/AI Agent Hackathon/cloud-run-airbag  (branch main)
- Read: README.md, docs/ARCHITECTURE.md, and especially docs/V4_VISION.md — that is your plan of record
  (a 6-lens first-principles ideation → adversarial-critique → synthesis, then Gemini-3.1-Pro-reviewed:
  GO, all decisions locked). docs/V3_VISION.md is the prior stage for context.
- Your auto-memory (loaded each session) has the full project history; trust it but VERIFY file/line
  refs still hold before acting (the codebase moved a lot in v3).

WHAT AIRBAG DOES (the thesis — do NOT break it):
Detect a bad Cloud Run deploy (even hours later, across MORE THAN ONE SIGNAL: 5xx + latency) → ADK/Gemini
DIAGNOSES and emits a structured IncidentDecision → a DETERMINISTIC state machine VALIDATES (confidence +
known-good target + a Wilson-CI multi-signal statistical gate, with a deterministic PROMOTION so a
confident statistical FAIL rolls back even when the LLM hedges) → a CAUSAL pre-check probes the rollback
TARGET's health (a futile rollback onto an also-degraded target ESCALATEs instead) → ACTS (rolls traffic
to the last-good revision) → proves recovery ON THE TRIGGERING SIGNAL (5xx→0 + business-path probe; for a
latency incident, latency back under SLO) → for a code-bug incident opens a self-proving fix PR
(RCA + an agent-authored regression test, sandbox-verified in an egress-disabled Cloud Run Job) → CI
deploys the fix KEYLESSLY (Workload Identity Federation) → Airbag verifies + canary-restores traffic
(10/50/100) → CLOSE. THE INVARIANT: Gemini diagnoses, the FSM acts — the ACTION TIER
(agent/autosre/backends/*, signals/*, tools.py, causal.py, and the _validate/_verify logic) NEVER imports
the LLM. agent/tests/test_architecture_invariant.py (AST-based) guards this; any NEW action-tier module
you add (e.g. the v4 reversibility guard) MUST be added to its _action_files() set.

CURRENT STATE (live + verified, as of the v3 wrap):
- 175 tests (167 agent + 8 mcp-server); ruff (E9,F) clean; CI green.
- Live on Cloud Run, project airbag-hack-260628, region asia-northeast1:
  agent+dashboard https://airbag-agent-946577240607.asia-northeast1.run.app (rev 00032) ·
  target https://airbag-target-946577240607.asia-northeast1.run.app
- v3 is ON BY DEFAULT in prod: AIRBAG_SIGNALS=all (5xx + latency), AIRBAG_CAUSAL_CHECK=1 (deploy.sh).
- Live config: gcp backend + Firestore durable state + Pub/Sub event bus + --max-instances 3 +
  in-process queue + remote-MCP OFF. Cloud Tasks + remote MCP are built+tested OPT-IN flags, off in the demo.
- Demo baseline (clean 3-revision, HEALTHY is NEWEST): airbag-target-00024 (FAULT_MODE=off, healthy,
  serving 100%, NEWEST) + airbag-target-00023 (FAULT_MODE=slow, latency regression, 0%) +
  airbag-target-00022 (FAULT_MODE=bug, KeyError→500, 0%). Two scenarios from one baseline: 💣 Break→Heal
  (5xx) and 🐢 Break-latency→Heal (latency). Because the rollback target is the newest ready 0-traffic
  revision, HEALTHY-NEWEST makes BOTH scenarios roll back onto the good revision, back-to-back, no re-setup.
  ALWAYS leave the target HEALTHY. After a real Verify&Undo (a fix deploy makes a new newest revision),
  re-run scripts/gcp-demo-setup.sh to restore the healthy-newest baseline.
- gcloud is authed; gh is authed (JasonYeYuhe); `agy` (Antigravity / Gemini 3.x CLI) is available for
  cross-model review (it can be flaky/rate-limited — a multi-agent Workflow review is the robust
  substitute). Secrets live in Secret Manager + agent/.env (gitignored). NEVER print or commit secrets.

YOUR MISSION — build v4 per docs/V4_VISION.md. THE THESIS: v3 made DETECTION and DIAGNOSIS trustworthy;
v4 makes the ONE reversible ACTION provably correct and provably safe. Today the rollback TARGET is the
"newest ready 0-traffic revision" (recency is a PROXY for last-good that a bad→bad deploy defeats), and
nothing guards a forward-only/irreversible deploy where a rollback makes the outage strictly WORSE. Do
NOT add more detectors — v4's bottleneck is action-target correctness, not detection breadth.

LOCKED DECISIONS (Gemini 3.1 Pro confirmed all defaults; see V4_VISION.md §0):
- Ship the bench TARGET-correctness dimension (the honest, committable marquee proof).
- Irreversibility marker = a Cloud Run revision ANNOTATION `airbag.dev/irreversible=true`.
- Irreversibility guard ships default-OFF behind a flag (demo unchanged); the latency-veto extension of
  the causal probe ships ON in prod (causal is already on).
- Descope ladder if the 8 days bind (in order): (1) drop the Phase-4 live-proof artifact, (2) drop Phase 2
  (latency-veto), (3) descope Phase 3 to fixture-only, (4) descope Phase 1 bench target-scoring. The
  marquee ledger + its Firestore-emulator CI gate is the non-negotiable FLOOR.

START WITH PHASE 1 (the marquee — see V4_VISION.md §3). In order:
  1. Stamp the witnessed-healthy SERVING revision into a thin per-service ledger on every OBSERVE/PASS
     (state_machine.py ~112-114, beside memory.observe_healthy) and at successful MITIGATE (after _verify,
     ~199-203). Reuse memory.observe_healthy's exact state_store.transact(_COLL, service, _m) idempotent
     pattern — a new field/collection on the SAME per-service doc, NOT a topology/dependency graph. Never
     stamp the immediate post-rollback 0.0-window revision (mirror the observe_healthy rule).
  2. In _rollback_pair / _heuristic / the _validate promotion, PREFER the newest ready 0-traffic revision
     that is WITNESSED-healthy in the ledger; on cold start (no ledger entry) fall back to today's
     newest-ready behavior UNCHANGED. GUARDRAIL: the ledger only PROPOSES — the chosen target MUST still
     flow through causal.precheck's LIVE probe in _mitigate before any traffic shifts (a stale entry can
     never bypass the live probe). Frame + test this as target SELECTION that turns an ESCALATE into an
     autonomous heal — NOT as "prevent a bad target" (that's redundant with the existing gates).
  3. Add a TARGET-correctness dimension to the bench (harness.py CaseResult → capture chosen target;
     expected_target in the corpus + scorecard.py) + bad→bad fixtures where recency picks the landmine and
     the ledger picks witnessed-good (plus a matched negative control). Commit updated golden scorecards +
     the CI ratchet. The committed proof must show OLD code escalating vs the ledger healing.
  4. Phase-1 ACCEPTANCE GATE (Phase 4.1): the ledger is a new durable ORDERED Firestore collection, and
     state_store.list_recent's order_by DESCENDING silently omits docs missing the order field — so add a
     firestore-emulator CI job (run the state_store transact/list_recent/lease suite under
     FIRESTORE_EMULATOR_HOST) + firestore.indexes.json, and always write the order field. (Closes the
     confirmed gap that prod runs AIRBAG_STATE=firestore but conftest pins memory.)
Then Phase 2 (latency-aware causal target-probe: extend probe_revision_health to {errs,total,slow} across
gcp+mock+local + a second Wilson gate keyed on the triggering signal — VETO ONLY, the candidate-walk is
CUT), then Phase 3 (forward-only/irreversible-deploy guard: a new LLM-free reversibility.py at the top of
_mitigate, fail-OPEN, default-OFF, refuse-to-rollback across a declared marker). Full detail + file:line
refs + the CUT list + risks in docs/V4_VISION.md.

CORRECTED FACTS (the review caught these against the code — do NOT repeat the wrong versions):
- probe_revision_health does NOT already return elapsed_ms (that's synthetic_probe) — Phase 2 timing is real work.
- the causal probe is NOT 5xx-blind — it COUNTS 5xx; it is LATENCY-blind. Phase 2 adds the latency axis.
- the irreversibility marker does NOT ride an already-parsed env surface — the FSM revision dict is
  {name, ready, traffic_percent, create_time}; you must add ONE new field in list_revisions.

HOW TO WORK (the established cadence — keep it):
- Commit + push to main as you go; keep CI green; keep google-adk pinned to 1.x (CI asserts it); keep ONE
  consistent test count across docs.
- TDD each item: write the unit/bench test alongside the code; run pytest + ruff (E9,F) before every
  commit; the Airbag-Bench CI ratchet (test_bench.py + committed scorecards) must stay green. Run
  test_architecture_invariant.py on every commit touching state_machine/causal/backends and add any new
  action module to _action_files().
- Verify on real Cloud Run where it matters (deploy + a live Break→Heal AND Break-latency→Heal); leave the
  target HEALTHY and the demo baseline intact. Ship anything touching the live demo behind a flag (default
  = current behavior) — the irreversibility guard is default-OFF for exactly this reason.
- REVIEW each substantial design + implementation before committing: `agy` (Gemini 3.1 Pro + 3.5 Flash)
  and/or a multi-agent Workflow (find → adversarially-verify-refute-by-default → synthesize). Apply their
  fixes. Special attention to the NON-REDUNDANCY rule: does this bet add value a deterministic gate doesn't
  already enforce? (The v3 Phase-2b lesson: a second LLM verifier was CUT as theater; the v4 candidate-walk
  was CUT for the same reason.)
- Be HONEST in docs — no overclaiming. The ledger PROPOSES (the live probe still gates); the guard HONORS a
  declared contract (it does NOT detect migrations); a latency incident's target is probe-eligible, not
  "latency-proven" unless the Phase-2 probe ran. Respect the DO-NOT list in V4_VISION.md §4 (no GKE, no RL,
  no blame engine, no fleet UI, no new detectors, no second LLM pass, no heavy ledger, no candidate-walk).

Start by reading docs/V4_VISION.md end to end, then begin Phase 1 item 1 (the serving-history ledger).
The six open questions are already answered (V4_VISION.md §0/§8) — only ask Jason if you hit a NEW decision
the code/docs can't resolve. Otherwise, proceed.
```
