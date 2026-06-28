# Build plan — "delay-bomb rollback" minimal slice first

Deadline 2026-07-10. Architecture rule: **deterministic state machine executes prod actions; Gemini only diagnoses + emits a structured decision.**

## Scope discipline (honest downgrade)
Full unattended end-to-end loop is high-risk in ~12 days. Ship in this order; everything after step 6 is **stretch**:

| # | Step | Risk |
|---|---|---|
| 1 | Target demo app on Cloud Run with a togglable fault + a known-good prior revision | low |
| 2 | **Before writing code**: manually `gcloud run services update-traffic SVC --to-revisions <good>=100` and watch 5xx drop. This is the foundation — make it go red→green once by hand. | low |
| 3 | `rollback_traffic_to_revision` as a Python tool (`run_v2`: list → pick prior Ready → `update_service` 100% → `.result()`) | low |
| 4 | PromQL alert policy on `run.googleapis.com/request_count` 5xx ratio (duration = 2×interval); trigger bad revision, confirm incident `open` | medium |
| 5 | `webhook_tokenauth` channel → `/alerts` (200-then-async, token, idempotent) | medium |
| 6 | **Minimal autonomous loop**: alert `open` → rollback → persist "rolled back, pending revert". ← highest-impact, lowest-risk demo core | medium |
| 7 | Wrap in ADK `SequentialAgent` + Gemini structured decision; `adk`-on-Cloud-Run deploy, min-instances≥1 | medium |
| 8 | Close loop: on incident `closed`, re-check metrics (zero-traffic guard) → `--to-latest` revert | high |
| 9 | **Stretch**: Gemini + GitHub App fix PR + Actions CI + sandbox self-correction + human gate | high |

## ⚠️ Verified landmines (do NOT skip — see ../research-archive round 3)
- [ ] **Pin `google-adk~=1.0`.** ADK 2.0/2.3 is a breaking graph-runtime rewrite; `pip install google-adk` grabs 2.3.0 and silently ignores `_run_async_impl`. CI asserts the pin. Docs live at **adk.dev** now.
- [ ] **GitHub App PRs don't trigger `on:pull_request`** (anti-recursion). Add `on:push` fallback or `actions/create-github-app-token`. Judge CI green by check-runs `conclusion=success`.
- [ ] **Zero-traffic trap.** error-rate==0 may just mean no traffic → add `sum(rate(total)) > N` precondition + synthetic `/healthz` probe before declaring recovery / reverting.
- [ ] **Cloud Run PATCH is a long-running Operation** → `.result()` before verifying.
- [ ] **Gemini structured output**: classic `generateContent` + `responseSchema`. NOT the new Interactions API `response_format` (400s).
- [ ] Never trust `LATEST` for prod actions — pin explicit revision.
- [ ] `DatabaseSessionService` (Cloud SQL), not InMemory (multi-instance/cold-start loses session). `min-instances≥1`.
- [ ] Webhook: 200 first, heal async; HMAC/token verify; idempotency on incident id.

## Production hardening (post-demo — flagged by Codex/Gemini review)
- **Durable idempotency + queue**: replace the in-process `_seen_incidents` set + FastAPI
  `BackgroundTasks` with a Firestore/Cloud SQL atomic insert + Cloud Tasks/Pub/Sub worker
  (BackgroundTasks aren't durable — a restart drops in-flight heals; a set isn't shared
  across Cloud Run instances).
- **Webhook auth**: require a non-empty token when `BACKEND=gcp`; prefer header/HMAC over a
  URL-query token (keeps secrets out of logs).
- **gcp error-rate**: the has-errors gate is coarse; for a true rate use a Cloud Monitoring
  `request_count` 5xx ratio / log-based metric — validate all filters against the live project.

## ✅ Deployed to Cloud Run (project `airbag-hack-260628`, region `asia-northeast1`)
Reproduce with `./deploy.sh`. Real deploy gotchas hit & fixed (all encoded in deploy.sh):
- **New-project default compute SA lacks build perms** → grant `roles/cloudbuild.builds.builder` or `gcloud run deploy --source` fails with PERMISSION_DENIED on the source bucket.
- **Cross-service `actAs`** → the agent SA needs `roles/iam.serviceAccountUser` on the *target's* runtime SA, else `update_service` (rollback) → `403 iam.serviceaccounts.actAs`.
- **Background work is CPU-throttled** → deploy the agent with `--no-cpu-throttling`, else the post-202 self-heal stalls right after `TRIAGED`.
- **GFE reserves `/healthz`** → Cloud Run's frontend 404s `/healthz` before it reaches the container; use `/health`.
- **Cloud Logging ingestion lag** (~10–20s) → after generating 5xx, wait before the agent reads them; the verify window is anchored at rollback time so post-rollback it reads 0.
- **`LATEST` traffic target** has `revision=''` → resolve it to the newest revision when reading traffic %.

## Still open (stretch)
- [ ] Gemini fix-PR + GitHub Actions CI loop (the `FIX_PR` stage) — needs a target repo.
- [ ] Repeatable one-click cloud demo reset (currently `./scripts/gcp-demo.sh` re-breaks the target).
