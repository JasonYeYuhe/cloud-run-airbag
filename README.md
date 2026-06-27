# Airbag — autonomous release airbag for Cloud Run

> Detects a bad Cloud Run deploy **even hours after it shipped**, instantly rolls traffic back to the last healthy revision, **proves recovery** via Cloud Monitoring, then opens a Gemini-written fix PR through real CI/CD — and undoes the temporary rollback once the fix is verified.

Built for the **DevOps × AI Agent Hackathon 2026** (Google Cloud Japan / Findy). Stack: **Gemini + ADK + Cloud Run** (required), FastAPI, Cloud Monitoring/Logging, GitHub App + Actions.

## Why this exists
Monitoring tools only *alert*. Coding agents (Jules/Devin) only *write code* offline. Auto-rollback tools (Argo/Harness/LaunchDarkly) only work **inside the deploy/canary window**. **Google's own Gemini Cloud Assist is officially advisory** ("don't modify… human-in-the-loop required"). Nobody closes this exact loop:

```
independent prod alert (even out-of-window)
  → auto rollback Cloud Run traffic to last-good revision   (deterministic, reversible — STOP THE BLEEDING)
  → prove error-rate == 0 via Monitoring/Logging + synthetic probe   (PROOF OF RECOVERY)
  → Gemini/ADK open a fix PR → real GitHub Actions CI         (PERMANENT FIX)
  → on green + deploy + verified, undo the temporary rollback (CLOSE THE TRANSACTION)
```

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

## Status
✅ **Working local demo** — the full loop (detect → decide → rollback → prove recovery) runs end-to-end over real HTTP with a live dashboard, **no GCP required**. Real Cloud Run path is wired behind a flag. See [docs/PLAN.md](docs/PLAN.md).

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
