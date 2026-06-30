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

## P1 — Close the transaction ✅ DONE (marquee differentiation completed)
7. ~~**Undo the temporary rollback after the fix ships.**~~ **Done.** `complete_rollback`
   (`agent/autosre/state_machine.py`) verifies the deployed revision **is** the fix (the
   CI-reported `revision`/`git_sha`, or the newest READY revision created after the rollback that
   isn't the bad/safe one), restores traffic to it, and CLOSEs — or **compensates** back to the
   safe revision and escalates (`MANUAL_INTERVENTION`, never loops). Stages:
   `PENDING_REVERT → COMPLETE_ROLLBACK → FIX_DEPLOYED → CANARY(10/50/100) → ROLLBACK_UNDONE → CLOSED`.
   Pending state is in-process (`pending.py`) + `--min-instances=1` (Firestore skipped per review).
   Triggers: the dashboard **Verify & Undo** button, or `POST /internal/complete-rollback`
   (token-gated) from the fix-PR's CI.
   - **Remaining for *fully-unattended* CI close:** wire `.github/workflows/complete-rollback.yml`
     (provided, gated on `vars.AIRBAG_AGENT_URL`). One-time Workload Identity Federation setup:
     ```bash
     gcloud iam workload-identity-pools create github --location=global
     gcloud iam workload-identity-pools providers create-oidc github \
       --workload-identity-pool=github --location=global \
       --issuer-uri=https://token.actions.githubusercontent.com \
       --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository"
     # bind the deploy SA to the repo, then set repo Actions vars/secrets (see the workflow header)
     ```

## P1.5 — Durable state (Firestore) — optional, post-submission
8. Replace in-process `_seen_incidents` + `pending.py` with Firestore (idempotent across restarts /
   multi-instance). Skipped for the hackathon per the review (min-instances=1 carries the demo).

## P2 — Depth / hardening (post-submission polish; Days 9+)
9. **Cloud Tasks / Pub/Sub** for the webhook instead of FastAPI `BackgroundTasks` (durable work).
10. ~~**Gradual canary on restore**~~ ✅ **Done** — `complete_rollback` restores traffic to the fix
    in `AIRBAG_CANARY_STAGES` steps (default 10%→50%→100%) with a probe+error-rate gate at each;
    compensates to 100% safe on any gate failure. The stop-the-bleeding rollback stays an instant flip.
11. ~~**CI self-correction**~~ ✅ **Done** — `github_pr.self_correct_ci` watches the fix PR's CI
    (`validate-fix.yml` runs on `airbag/fix**` only, so main stays green); on red it feeds the failure
    to Gemini, commits a correction, retries ≤`MAX_CI_RETRIES`, then escalates (PR comment).
12. ~~**Per-incident Artifact**~~ ✅ **Done** — `incidents.py` + `report.py`: each run persisted and
    rendered at `/incidents/{id}/report` (decision, signals, before/after, timeline); linked from the dashboard.
13. **Tests**: unit-test the gcp traffic-resolution (LATEST → newest) + a local-backend integration test;
    expand CI beyond the mock smoke test. *(118 tests; gcp backend incl. traffic-etag retry, decision gate, webhooks incl. header-only auth, Airbag-Bench, AST architecture-invariant + lint now covered.)*

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
