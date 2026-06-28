# Airbag — autonomous release airbag for Cloud Run

> Detects a bad Cloud Run deploy **even hours after it shipped**, instantly rolls traffic back to the last healthy revision, **proves recovery** via Cloud Monitoring/Logging, then has Gemini open a real fix PR through CI/CD. *(Closing the loop — auto-undoing the temporary rollback once the fix is verified — is the next milestone; see [Roadmap](docs/NEXT_STEPS.md). Today the rollback is held until the fix ships.)*

Built for the **DevOps × AI Agent Hackathon 2026** (Google Cloud Japan / Findy). Stack: **Gemini + ADK + Cloud Run** (required), FastAPI, Cloud Monitoring/Logging, GitHub App + Actions.

## Why this exists
Monitoring tools only *alert*. Coding agents (Jules/Devin) only *write code* offline. Auto-rollback tools (Argo/Harness/LaunchDarkly) only work **inside the deploy/canary window**. **Google's own Gemini Cloud Assist is officially advisory** ("don't modify… human-in-the-loop required"). Nobody closes this exact loop:

```
independent prod alert (even out-of-window)
  → auto rollback Cloud Run traffic to last-good revision   (deterministic, reversible — STOP THE BLEEDING)  ✅ live
  → prove error-rate == 0 via Monitoring/Logging + synthetic probe   (PROOF OF RECOVERY)                    ✅ live
  → Gemini/ADK open a fix PR → real GitHub Actions CI         (PERMANENT FIX)                                ✅ live
  → on green + deploy + verified, undo the temporary rollback (CLOSE THE TRANSACTION)                        🚧 roadmap (P1)
```
The first three steps run unattended on live Cloud Run today; closing the transaction (the last step) is the next milestone.

**Design rule:** a deterministic state machine executes production actions; **Gemini only diagnoses and emits a structured decision** — the LLM never freely touches prod.

## Architecture (target)
```
Cloud Monitoring alert ─webhook(token)→ /alerts  (Cloud Run: airbag-agent, FastAPI)
                                            │ 202 then async
                            ┌───────────────┴───────────────┐
                            │  ADK SequentialAgent           │
                            │  triage → decide → rollback    │
                            │        → verify(loop) → fix-PR  │
                            └───────────────┬───────────────┘
   tools: Cloud Run Admin (run_v2) · Cloud Monitoring/Logging · GitHub App
   state: Cloud SQL (DatabaseSessionService)   secrets: Secret Manager
                                            │
   target demo app (Cloud Run) ←rollback traffic / ←fix deploy
```

## Repo layout
| Path | What |
|---|---|
| `agent/` | The self-heal agent — FastAPI webhook + ADK 1.x state machine |
| `target-app/` | The "delay-bomb" demo target deployed to Cloud Run (injectable faults) |
| `infra/` | `gcloud` setup: enable APIs, service account + min IAM, alert policy, webhook channel |
| `dashboard/` | (later) Next.js thought-chain / proof-of-recovery dashboard |
| `docs/` | `PLAN.md` (roadmap + minimal slice), `ARCHITECTURE.md` |
| `.github/workflows/` | CI |

## Status — 🟢 LIVE on Google Cloud Run
The **deployed agent autonomously heals the deployed target** on real Cloud Run, decided by real Gemini. Verified end-to-end: bad revision serving 500s (a planted `KeyError` on `/api/orders`) → agent detects → Gemini decides `ROLLBACK` (conf 1.0) → real traffic shifts to the healthy revision → error rate proven `0%`. The slow path then opens a fix PR for **that same `KeyError`** — rolled back *and* root-cause fixed.

| | URL |
|---|---|
| **Agent + dashboard** | https://airbag-agent-946577240607.asia-northeast1.run.app |
| **Target (demo app)** | https://airbag-target-946577240607.asia-northeast1.run.app |

![Airbag glassbox dashboard — the agent's thought-chain, proof-of-recovery curve, and the ADK/Gemini rollback decision](docs/dashboard.png)
*The glassbox dashboard: the agent's thought-chain (detect → **ADK triage→decide** → rollback → verify → fix-PR), the 5xx error-rate dropping to 0 with the **✓ VERIFIED RESOLVED** gate, and the structured `gemini-adk` decision.*

**Fully autonomous:** a real **Cloud Monitoring 5xx alert** fires on its own and triggers the heal with **no human in the loop** (verified — target rolled back ~3 min after the alert, triggered by Cloud Monitoring incident, not a button). Wire it with `./infra/alert-setup.sh`.

**Dual-path heal:** after the rollback stops the bleeding, the slow path has **Gemini open a real fix PR** (root-cause) that passes CI — e.g. [PR #1](https://github.com/JasonYeYuhe/cloud-run-airbag/pull/1) fixed the planted `KeyError` (`amount`→`price`), `on: push` CI green. (Validated locally; on the deployed agent, enable with a fine-grained, repo-scoped token.)

**Cloud demo:** `./scripts/gcp-demo.sh` (breaks the target), then either wait for the alert, or open the agent URL and click **🚨 Trigger incident** for the instant path.
**Reproduce the deploy from scratch:** `gcloud auth login` once, then `PROJECT=<id> ./deploy.sh`.

It also runs fully **locally with no GCP** (see below). See [docs/PLAN.md](docs/PLAN.md) and [docs/DEMO.md](docs/DEMO.md).

## Run the live demo (no GCP, ~1 min)
```bash
./run-local.sh            # boots target-app (:8081) + agent+dashboard (:8080)
# open http://localhost:8080  →  click "▶ Run demo"
```
You'll watch the agent detect the injected fault, decide, roll Cloud Run traffic back to
the healthy revision, and prove the 5xx rate hits zero — streamed live as a thought-chain.

## Execution backends (`AIRBAG_BACKEND`)
| value | what it does | needs |
|---|---|---|
| `mock` | in-memory (CI/tests) | nothing |
| `local` | **real HTTP** against the local target-app; rollback = shift traffic off the faulty revision | nothing (default for the demo) |
| `gcp` | **real Cloud Run** via `run_v2` + Cloud Monitoring | `gcloud auth` + billing-enabled project |

**Gemini:** set `GEMINI_API_KEY` (AI Studio) to use a real Gemini structured decision;
without it the agent falls back to a deterministic decision so the demo always runs.

## License
MIT © 2026 Jason Ye
