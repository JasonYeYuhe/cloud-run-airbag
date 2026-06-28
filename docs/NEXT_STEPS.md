# Next-stage development plan — Airbag

Deadline: **2026-07-10** (project submission). Today: 2026-06-28 (~12 days).
The core product is **done and deployed** (detect → Gemini decide → rollback → prove recovery →
Gemini fix-PR; alert-driven; secure scoped token). This plan covers what's left.

## State of the world (already shipped)
- Live on Cloud Run, project `airbag-hack-260628` / `asia-northeast1`. Agent + dashboard, target app.
- Full dual-path verified end-to-end on the **deployed** agent (rollback + fix-PR #3, CI green).
- Real Cloud Monitoring 5xx alert auto-triggers the heal (no human; ~3–4 min latency).
- `deploy.sh` / `infra/alert-setup.sh` reproducible; 6 research docs archived; CI green.
- Gemini on **Tier-1** key (Secret Manager `airbag-gemini-key`); GitHub via **fine-grained** token
  `airbag-cloud-fix-pr` (Secret Manager `airbag-github-token`, expires 2026-07-28).

---

## P0 — Submission readiness (must-do; target ~Days 1–4)
1. **Repeatable one-click demo.** `/demo/run` is local-backend only. Add a gcp-backend demo flow:
   dashboard buttons **Break** (route target → bad revision) / **Heal** (trigger) / **Reset** (route → healthy),
   so a judge can run break→heal→reset reliably, repeatedly, from the dashboard. Keep the instant
   `/demo/trigger` path for live demos (alert path is real but ~3–4 min).
2. **Demo video (≤3 min)** — script in `docs/DEMO.md`; record: out-of-window break → autonomous heal
   (rollback + error-rate→0 on the glassbox dashboard) → fix-PR #N → CI green. Show real GCP consoles.
3. **`SUBMISSION.md`** — problem, the 4-source research white space (out-of-window / action-layer /
   reversible / proof-of-recovery), architecture diagram, live URLs, "real vs stretch", required-stack
   story (Gemini + ADK + Cloud Run), team.
4. **Pitch deck outline** (8–10 slides) from `docs/DEMO.md` talking points + ROI anchors.
5. **Go public**: flip repo to public before submission; README pass; add a dashboard screenshot/GIF;
   confirm LICENSE. Verify the registration/submission form requirements on the Findy site.
6. **Cost & teardown**: document min-instances cost; a `teardown.sh` to delete services/secrets/policy.

## P1 — Close the transaction (marquee differentiation completion; Days 5–8)
7. **Undo the temporary rollback after the fix ships.** Today the rollback is permanent until manual.
   Complete the "one transaction, two compensating actions" story: when the fix PR merges and a new
   healthy revision deploys, the agent verifies it and shifts traffic back to the fixed revision
   (`--to-latest`). Trigger via a **GitHub merge/deploy webhook** (or a bounded poll on the PR + new
   revision health). New stages: `FIX_DEPLOYED → REVERIFIED → ROLLBACK_UNDONE → CLOSED`.
8. **Durable state (Firestore).** Replace in-process `_seen_incidents` + the "pending-revert" state
   with Firestore (idempotent across restarts / multi-instance). Removes the biggest production caveat.

## P2 — Depth / hardening (post-submission polish; Days 9+)
9. **Cloud Tasks / Pub/Sub** for the webhook instead of FastAPI `BackgroundTasks` (durable work).
10. **Gradual canary on restore** (10%→50%→100% with a metric gate) instead of a 100% flip.
11. **CI self-correction**: if the fix PR's CI goes red, feed the failure back to Gemini, retry ≤2,
    then escalate. (Strong "agent verifies its own work" story.)
12. **Per-incident Artifact**: persist each run's evidence (decision, signals, before/after curves) and
    render a downloadable "incident report" in the dashboard — the verifiable thought-chain Artifact.
13. **Tests**: unit-test the gcp traffic-resolution (LATEST → newest) + a local-backend integration test;
    expand CI beyond the mock smoke test.

## Risks / watch-items
- Fine-grained GitHub token **expires 2026-07-28** — fine for the hackathon, note for after.
- Cloud Logging ingestion lag (~15s) and alert latency (~3–4 min) — use the instant trigger live.
- Free-tier Gemini quota (now on Tier-1, fine) — keep an eye on spend on the $10/mo credit.
- Don't over-scope P2 before the submission is locked. P0 first, always demoable.

---

## Review consensus (Codex + Gemini 3.1 Pro) — apply these
**Promote to P0 (do these first — both reviewers flagged them):**
- **Secure the public endpoints.** Add a shared-header-token check to `/demo/*` (esp. `/demo/trigger`) **before going public** — today it's `--allow-unauthenticated` and triggers Gemini + GitHub actions ⇒ PR/cost-spam risk.
- **Fix the doc overclaim.** README's intro implies "undo the temporary rollback" already works; it's stretch. Unify README / `docs/DEMO.md` / `SUBMISSION.md` on what's real vs Future Work.
- **Unify the demo fault.** Rollback demo uses `FAULT_MODE=http500`, but the fix-PR fixes the `KeyError` (`FAULT_MODE=bug`) — two mechanisms. Make the *demonstrated* fault the same code bug the fix-PR fixes, so "rolled back **and** fixed the cause" is coherent.
- **ADK honesty.** Runtime calls Gemini directly; the ADK `SequentialAgent` in `agent.py` isn't actually run. Either route the decision through the ADK agent, or state accurately in `SUBMISSION.md` where ADK is used (matters for "required stack" judging).
- **Tests up from P2.** Unit-test gcp `LATEST→newest` resolution + rollback/restore primitives; CI only covers the mock backend today.

**P1 — close the transaction, the safe way (both agree):**
- **Never blindly `--to-latest`.** Before restoring, verify the new revision *is the fix* (match PR/git-sha, or candidate created-after-rollback + business-path probe healthy). Mismatch ⇒ `MANUAL_INTERVENTION`, never loop.
- **Trigger via CI, not webhooks.** Simplest: the fix-PR's GitHub Action, after deploy, `curl`s the agent `/internal/complete-rollback`. Bounded; compensating action on failure (route back to the rollback revision).
- **Skip Firestore for the hackathon** (Gemini): `min-instances=1` + in-memory carries the demo; durable state is post-submission. (Codex prefers Firestore-first — decide by time left.)
- **Time-short fallback:** a Dashboard **"Verify & Undo Rollback"** button (half-automated) beats a buggy full-auto loop.

**Also:** promote the per-incident **evidence/Artifact UI** to P1 ("AI isn't guessing" demos far better than a silent fix). **Code-freeze the last ~5 days** — only video/docs/rehearsal; if P1 isn't done, demote it to "Future Work" in the deck.
