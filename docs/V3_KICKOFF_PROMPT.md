# v3 kickoff — paste this into a fresh session to start Airbag v3 seamlessly

> Self-contained handoff. The new session has no prior context; everything it needs is below or in
> the repo. (This file is the artifact; the fenced block is what you paste.)

---

```
You are continuing development of "Airbag" — an autonomous release safety net for Google Cloud Run,
built for the DevOps × AI Agent Hackathon 2026 (required stack: Gemini + ADK + Cloud Run). v2 is
complete, live, and verified; your job is to build v3.

ORIENT FIRST (read these before doing anything):
- The repo is at: /Users/jason/Documents/AI Agent Hackathon/cloud-run-airbag  (branch main)
- Read: README.md, docs/ARCHITECTURE.md, and especially docs/V3_VISION.md — that is your plan of
  record (audit + verified competitor research + a Gemini-3.1-Pro-reviewed phased dev plan).
- Your auto-memory (loaded each session) has the full project history; trust it but verify file/line
  refs still hold before acting.

WHAT AIRBAG DOES (the thesis — do not break it):
Detect a bad Cloud Run deploy (even hours later) → ADK/Gemini DIAGNOSES and emits a structured
IncidentDecision → a DETERMINISTIC state machine VALIDATES (confidence + known-good target + a
Wilson-CI statistical gate) and ACTS (rolls traffic back to the last-good revision) → proves recovery
(5xx→0 + business-path probe) → opens a self-proving fix PR (RCA + an agent-authored regression test,
sandbox-verified) → CI deploys the fix KEYLESSLY (Workload Identity Federation) → Airbag verifies +
canary-restores traffic (10/50/100) → CLOSE. THE INVARIANT: Gemini diagnoses, the FSM acts — the
action layer (agent/autosre/backends/* and tools.py) NEVER imports the LLM. test_architecture_invariant
guards this; Phase 0 makes that guard AST-based. Never let the verifier/causal layer leak into the
action layer.

CURRENT STATE (live + verified):
- 102 tests green (95 agent + 7 mcp-server); ruff (E9,F) clean; CI green (4 jobs).
- Live on Cloud Run, project airbag-hack-260628, region asia-northeast1:
  agent+dashboard https://airbag-agent-946577240607.asia-northeast1.run.app (rev 00027) ·
  target https://airbag-target-946577240607.asia-northeast1.run.app
- Live config: gcp backend + Firestore durable state (AIRBAG_STATE=firestore) + Pub/Sub event bus
  (AIRBAG_EVENTS=pubsub) + --max-instances 3 (multi-instance) + in-process queue + remote-MCP OFF.
  Cloud Tasks queue + remote MCP are built+tested OPT-IN flags, off in the demo.
- Demo baseline: target revision airbag-target-00014 (FAULT_MODE=bug, NEWEST, 0% traffic) +
  airbag-target-00013 (healthy, serving). Break→Heal→Reset is repeatable. ALWAYS leave the target
  HEALTHY. After a real Verify&Undo (which deploys a new healthy revision), re-run
  scripts/gcp-demo-setup.sh so the bug revision is newest again.
- gcloud is authed; gh is authed (JasonYeYuhe); `agy` (Antigravity / Gemini 3.x CLI) is available for
  cross-model review. Secrets live in Secret Manager + agent/.env (gitignored). NEVER print or commit
  secrets/tokens.

YOUR MISSION — build v3 per docs/V3_VISION.md. The single biggest bet: make Airbag CAUSALLY CERTAIN
before it acts, across MORE THAN ONE SIGNAL (today detection is single-signal 5xx, which undercuts the
out-of-window moat). All new intelligence lives in the DIAGNOSIS tier; the FSM stays LLM-free.

START WITH PHASE 0 (harden + build the measuring stick — see V3_VISION.md §5; re-sequenced per the
Gemini review). In order:
  1. Build Airbag-Bench FIRST (labeled incident-replay harness + scorecard: precision/recall on
     rollback, false-rollback rate, mean-stages-to-mitigate) and BASELINE the current v2 5xx-only impl
     — so Phases 1–2 are a TDD loop against real numbers.
  2. Make test_architecture_invariant AST-based (parse Import/ImportFrom; catch gemini / google.genai /
     google.generativeai / adk in the action layer) — it currently uses fragile substring matching.
  3. Drop OPEN_FIX_PR from the IncidentDecision Literal enum (schemas.py) — it silently becomes a
     no-op DONE today and pollutes the learned baseline.
  4. Add etag/optimistic-concurrency (or a per-service traffic lease) to gcp._set_traffic — a latent
     prod-correctness bug under --max-instances 3.
  5. Harden the fix_pipeline sandbox: move the LLM-authored test run to a network-egress-disabled
     Cloud Run Job (per the Gemini review — NOT a v4 defer; it contradicts the guarded-action moat).
  6. /demo/run error handling (wrap break_then_heal in try/except → emit ESCALATED + reset_target) +
     switch the alert webhook to a header token (drop ?token= from machine endpoints).
  7. Generic CI-retry prompt in github_pr._gemini_fix (it hardcodes the demo bug's oracle today).
Then Phase 1 (multi-signal detection — WITH debounce/hysteresis), Phase 2 (causal deploy-ledger +
graded-confidence verifier + confidence→autonomy wiring), Phase 3 (Airbag-Bench CI gate, MCP action
tools, signed proof bundle, dashboard). Full detail + file:line refs in docs/V3_VISION.md.

HOW TO WORK (the established cadence — keep it):
- Commit + push to main as you go; keep CI green; keep google-adk pinned to 1.x (CI asserts it).
- Test every change (pytest + ruff E9,F) AND verify on real Cloud Run (deploy + a live Break→Heal);
  leave the target HEALTHY and the demo baseline intact.
- Build each substantial change behind a flag where it touches the live demo (default = current
  behavior, zero demo risk), like the v2 themes did.
- REVIEW each substantial design + implementation before committing: use `agy` (Gemini 3.1 Pro + 3.5
  Flash) and/or a multi-agent Workflow (find → adversarially-verify-refute-by-default → synthesize).
  If agy times out (it was flaky), the Workflow review is the robust substitute. Apply their fixes.
- Be HONEST in docs — no overclaiming; if something is opt-in/off, say so; keep one consistent test
  count. Avoid the over-engineering trap: docs/V3_VISION.md §6 has an explicit "do NOT" list
  (no GKE/Cloud-Functions backends, no RL autonomy policy, no multi-service blame engine, no fleet UI
  in v3) — respect it.

Start by reading docs/V3_VISION.md end to end, then begin Phase 0 item 1 (Airbag-Bench). Ask me only
if you hit a real decision the code/docs can't resolve (e.g., you need a Slack webhook for the
deferred ChatOps feature). Otherwise, proceed.
```
