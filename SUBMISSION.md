# Airbag — submission

**DevOps × AI Agent Hackathon 2026** (Google Cloud Japan / Findy) · Required stack: **Gemini + ADK + Cloud Run**

| | |
|---|---|
| **What** | An autonomous release safety net for Cloud Run: detects a bad deploy *even hours after it shipped*, rolls traffic back to the last-good revision, **proves** recovery, then has Gemini open a fix PR through real CI. |
| **Agent + glassbox dashboard** | https://airbag-agent-946577240607.asia-northeast1.run.app |
| **Target (demo app)** | https://airbag-target-946577240607.asia-northeast1.run.app |
| **Repo** | https://github.com/JasonYeYuhe/cloud-run-airbag |
| **Team** | Jason Ye ([@JasonYeYuhe](https://github.com/JasonYeYuhe)) · WANG Pei ([@WANG-Pei777](https://github.com/WANG-Pei777)) |

---

## 1. The problem
Bad deploys don't always fail at deploy time. A latent bug (a config flag, an out-of-bounds
code path, a dependency that degrades) ships green and starts erroring **hours later**, long
after the canary/rollout window closed and the deploy engineer went home. **78% of orgs report
incidents where no alert fired in time.** When something *does* fire, a human still has to wake
up, diagnose, and act.

## 2. The white space (4-source competitive research → what nobody does)
We ran a 4-model competitive sweep (archived in [`docs/research/`](docs/research/)). Every adjacent
tool stops short on at least one axis:

| | Out-of-window? | Acts on prod? | Reversible/safe? | Proves recovery? |
|---|:--:|:--:|:--:|:--:|
| Argo/Harness/LaunchDarkly auto-rollback | ❌ in-window only | ✅ | ✅ | partial |
| Datadog/PagerDuty/Cloud Monitoring | ✅ | ❌ alert only | — | ❌ |
| Gemini Cloud Assist | ✅ | ❌ **officially advisory** ("human-in-the-loop required") | — | ❌ |
| Jules / Devin (coding agents) | ✅ | ❌ write code offline | — | ❌ |
| **Airbag** | ✅ | ✅ | ✅ traffic-shift only | ✅ error-rate→0 + probe |

**Airbag owns the intersection:** an independent production alert — *hours after* the deploy —
triggers a **reversible** action on the live service and **proves** the incident is gone.

## 3. How it works
```
independent prod alert (Cloud Monitoring 5xx, even out-of-window)
  → /alerts webhook (202-then-async, token, idempotent)
  → ADK SequentialAgent: triage (calls Cloud Run + Logging tools) → decide (structured IncidentDecision)
  → deterministic state machine validates (confidence ≥ τ AND rollback_revision ∈ known-good)
  → rollback Cloud Run traffic to the last-good revision     [STOP THE BLEEDING — reversible]
  → prove error-rate == 0 (Cloud Logging) AND business-path probe ok   [PROOF OF RECOVERY]
  → Gemini opens a fix PR for the root cause → real GitHub Actions CI   [PERMANENT FIX]
  → fix deploys → verify it IS the fix → undo the temporary rollback (compensate if it fails)   [CLOSE THE TRANSACTION]
```
**Design rule (the governance story):** a deterministic FastAPI state machine executes every
production action; **Gemini only diagnoses and emits a structured decision** through ADK. The LLM
never freely touches prod. Judges see a governed control loop, not a chatbot with `run.admin`.

## 4. Required-stack story (one coherent agent)
- **Gemini** — the decision, *through ADK*. The triage `LlmAgent` calls the Cloud Run /
  Monitoring tools itself via ADK function-calling; the decision `LlmAgent` returns a structured
  `IncidentDecision` (`response_schema`). Runtime path: [`agent/autosre/adk_brain.py`](agent/autosre/adk_brain.py)
  runs the `SequentialAgent` defined in [`agent/autosre/agent.py`](agent/autosre/agent.py).
  Fail-closed: ADK → direct Gemini → heuristic, so the heal never blocks on the LLM.
- **ADK** — `google-adk` 1.36 (pinned `~=1.36.0`; CI asserts the 1.x pin — 2.x is a breaking
  graph-runtime rewrite). The `SequentialAgent(triage → decide)` runs on every heal.
- **Cloud Run** — both the **patient** (the target service we roll back) and the **runtime**
  (the agent + dashboard, `--min-instances=1 --no-cpu-throttling` so the async heal isn't
  CPU-throttled). Traffic shifts via `run_v2` (`update_service`, explicit revision, `.result()`).
- **Cloud Logging / Monitoring** — detection (the real alert path) and the proof-of-recovery gate.
- **Secret Manager + least-priv IAM** — Gemini key, GitHub token, demo token; SA scoped to
  `run.admin` / `monitoring.viewer` / `logging.viewer` / `secretmanager.secretAccessor`.

## 5. What's real vs roadmap (honest)
**Real, verified on live Cloud Run (project `airbag-hack-260628`, `asia-northeast1`):**
- Detection → **ADK/Gemini decision** → **rollback** → **verified recovery (error-rate→0 + probe)**.
- A real **Cloud Monitoring 5xx alert** auto-triggers the heal with **no human** (~3–4 min).
- Gemini opens a real **fix PR** for the planted `KeyError` → GitHub Actions **CI green**
  (e.g. [PR #3](https://github.com/JasonYeYuhe/cloud-run-airbag/pull/3)).
- One-click repeatable demo: **Break → Heal → (deploy fix) → Verify & Undo → Reset** from the dashboard.
- **Close the transaction:** verify the deployed revision **is** the fix (the CI-reported
  revision/sha, or a post-rollback healthy candidate), restore traffic to it, and CLOSE — or
  **compensate** back to the safe revision if the fix fails (`complete_rollback`).
  Triggered by `/internal/complete-rollback` (token-gated) or the dashboard's **Verify & Undo**.
- **Gradual canary on restore:** traffic rolls forward to the fix 10%→50%→100% with a health
  gate at each step (compensate on any failure) — catch a bad fix at low exposure.
- **CI self-correction:** the agent watches its own fix PR's CI (`validate-fix` runs on the
  `airbag/fix**` branch only, so `main` stays green); on red it feeds the failure to Gemini,
  commits a correction, retries, then escalates — "the agent verifies and corrects its own work".
- **Verifiable incident-report Artifact:** every run is persisted and rendered at
  `/incidents/{id}/report` (decision + signals + before/after + full timeline) — *AI isn't guessing*.
- Three execution backends (mock / local / gcp) behind one agent codebase; `/demo/*` is
  token-gated so the public dashboard is watch-only. 32 tests, CI green.

**Roadmap (P2, honestly not done):**
- **Fully-unattended CI close:** the included `.github/workflows/complete-rollback.yml` deploys
  the fix and calls `/internal/complete-rollback`, but is gated on a one-time **Workload Identity
  Federation** binding (GitHub Actions → GCP deploy). Until that's wired, the close is triggered
  by the dashboard button (or a manual `curl`/`workflow_dispatch`).
- Durable state (Firestore) instead of in-process idempotency + `--max-instances 1`; a
  Cloud Tasks/Pub-Sub worker instead of FastAPI `BackgroundTasks`.

## 6. The demo
- **Live:** open the agent URL (operator link pre-fills the demo token), click **Break → Heal →
  Reset**. Watch the thought-chain stream: `TRIAGED → ADK → DECISION(ROLLBACK) → ROLLBACK_APPLIED
  → VERIFYING → MITIGATED → FIX_PR`, the error curve hit 0, the gate turn **✓ VERIFIED RESOLVED**.
- **Local (no GCP):** `./run-local.sh` → http://localhost:8080.
- **Autonomous (no human):** `./scripts/gcp-demo.sh` breaks the target; the Cloud Monitoring
  alert self-heals it in ~3–4 min.

See [`docs/DEMO.md`](docs/DEMO.md) for the 90-second script and [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## 7. Reproduce
```bash
gcloud auth login                          # once
PROJECT=<your-proj> ./deploy.sh            # target + agent + secrets + IAM (encodes every gotcha)
PROJECT=<your-proj> ./scripts/gcp-demo-setup.sh   # bad-revision baseline (FAULT_MODE=bug)
PROJECT=<your-proj> ./infra/alert-setup.sh        # the autonomous Cloud Monitoring alert path
PROJECT=<your-proj> ./teardown.sh          # delete everything (stop spend)
```
Cost: one always-on small instance (`--min-instances=1`) is the only standing cost; everything
else is per-request/per-build (~$0 at demo volume on the trial credit).
