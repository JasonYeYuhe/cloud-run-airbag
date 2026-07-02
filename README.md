# Airbag — autonomous release airbag for Cloud Run

> Detects a bad Cloud Run deploy **even hours after it shipped**, instantly rolls traffic back to the last healthy revision, **proves recovery** via Cloud Monitoring/Logging, has Gemini open a real fix PR through CI/CD — then, once the fix deploys, **verifies it and undoes the temporary rollback** to close the loop (compensating back to safety if the fix fails). Triggered by the fix-PR's CI or one click in the dashboard.

Built for the **DevOps × AI Agent Hackathon 2026** (Google Cloud Japan / Findy). Stack: **Gemini + ADK + Cloud Run** (required), FastAPI, Cloud Monitoring/Logging, GitHub App + Actions.

**Live demo:** [dashboard](https://airbag-agent-946577240607.asia-northeast1.run.app) · [target service](https://airbag-target-946577240607.asia-northeast1.run.app) · reproduce: `gcloud auth login` then `PROJECT=<id> ./deploy.sh`

## Why this exists
Monitoring tools only *alert*. Coding agents (Jules/Devin) only *write code* offline. Auto-rollback tools (Argo/Harness/LaunchDarkly) only work **inside the deploy/canary window**. **Google's own Gemini Cloud Assist is officially advisory** ("don't modify… human-in-the-loop required"). Nobody closes this exact loop:

```
independent prod alert (even out-of-window)
  → auto rollback Cloud Run traffic to last-good revision   (deterministic, reversible — STOP THE BLEEDING)  ✅ live
  → prove error-rate == 0 via Monitoring/Logging + synthetic probe   (PROOF OF RECOVERY)                    ✅ live
  → Gemini/ADK open a fix PR → real GitHub Actions CI         (PERMANENT FIX)                                ✅ live
  → on deploy + verified, undo the temporary rollback (CLOSE THE TRANSACTION)                                ✅ verify + undo + compensate
```
All four steps run on live Cloud Run. The close-the-transaction step verifies the deployed revision **is** the fix (matches the CI-reported revision/sha, or a post-rollback healthy candidate) before restoring traffic, and **compensates** back to the safe revision if the fix fails — triggered by the fix-PR's CI (`/internal/complete-rollback`) or the dashboard's **Verify & Undo** button. *(The CI path is **fully unattended** — GitHub Actions authenticates to GCP keylessly via Workload Identity Federation, deploys the fix, and calls Airbag to verify + restore, no human; verified live. Setup: [`infra/wif-setup.sh`](infra/wif-setup.sh).)*

**Design rule — the autonomy boundary:** a deterministic state machine executes every production
action; **Gemini only diagnoses and emits a structured decision** (an AST test in CI enforces that
the action tier cannot even *import* the LLM). Autonomy is **graduated per service** (L0
observe → L1 approve-first → L2/L3 auto with durable approval gates), the only automated action is
a **reversible traffic shift to a witnessed-healthy revision**, and a deploy that *isn't*
reversible can declare it (`airbag.dev/irreversible`) — Airbag escalates instead of crossing it.

## Architecture (target)
```
Cloud Monitoring alert ─webhook(token)→ /alerts  (Cloud Run: airbag-agent, FastAPI)
                                            │ 202 then async
                            ┌───────────────┴───────────────┐
                            │  ADK SequentialAgent                  │
                            │  triage → analyze(Wilson CI) → decide │
                            │    → autonomy gate → rollback         │
                            │    → verify(loop) → fix-PR            │
                            └───────────────┬───────────────────────┘
   tools: Cloud Run Admin (run_v2) · Cloud Monitoring/Logging · GitHub App
   state: durable Firestore store (AIRBAG_STATE=memory|firestore) — survives container recycles
          multi-instance (Firestore state + Pub/Sub event-bus fan-out)   secrets: Secret Manager
                                            │
   target demo app (Cloud Run) ←rollback traffic / ←fix deploy
```

## Repo layout
| Path | What |
|---|---|
| `agent/` | The self-heal agent — FastAPI webhook + ADK 1.x state machine |
| `target-app/` | The "delay-bomb" demo target deployed to Cloud Run (injectable faults) |
| `infra/` | `gcloud` setup: enable APIs, service account + min IAM, alert policy, webhook channel |
| `agent/static/dashboard.html` | live glassbox dashboard — SSE thought-chain, proof-of-recovery curve, Break/Heal/Verify&Undo, incident-report link |
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

**Dual-path heal:** after the rollback stops the bleeding, the slow path runs an **agentic fix pipeline** — Gemini reads the *real stack trace* (root-cause analysis), patches the culprit file, and **authors a regression test that's sandbox-verified to fail on the bug and pass on the fix** *before* opening a PR that commits **both the fix and the test** (a self-proving PR — see [open PRs](https://github.com/JasonYeYuhe/cloud-run-airbag/pulls)), which passes `on: push` CI. Runs **on the deployed agent** during a live heal (fine-grained token in Secret Manager); idempotent — reuses an open `airbag/fix` PR rather than spamming.

**Cloud demo:** `./scripts/gcp-demo.sh` (breaks the target), then either wait for the alert, or open the agent URL and click **🚑 Heal** for the instant path.
**Reproduce the deploy from scratch:** `gcloud auth login` once, then `PROJECT=<id> ./deploy.sh`.

## v2 — production-grade autonomy (live)
Four upgrades take Airbag from "impressive demo" toward "a thing a team would actually run." Each
was built against an adversarial review (Gemini 3.1 Pro + 3.5 Flash, and/or a multi-agent review
workflow) and verified on live Cloud Run:

| Upgrade | What it does | Where |
|---|---|---|
| **Statistical decision gate** | The rollback trigger is a **Wilson confidence-interval** verdict (`FAIL`/`PASS`/`INCONCLUSIVE`), not a static `5xx ≥ 5%`. `PASS`→withhold, `INCONCLUSIVE`→escalate (don't auto-act on weak evidence), `FAIL`→proceed. A low-traffic 4/4 outage still fires; a single blip never does. | [`analyzer.py`](agent/autosre/analyzer.py) |
| **Durable state + multi-instance** | Pending reverts, incidents, and webhook dedup live in **Firestore** (`AIRBAG_STATE=firestore`), behind one atomic `transact` with a self-healing **lease** lock. With a **Pub/Sub event-bus fan-out** (`AIRBAG_EVENTS=pubsub`) the dashboard sees a heal on *any* instance, so the agent runs **`--max-instances 3`** (verified: heal events fan out across instances). | [`state_store.py`](agent/autosre/state_store.py) · [`events.py`](agent/autosre/events.py) |
| **Graduated autonomy** | Per-service trust levels enforced **deterministically**: `L0` observe · `L1` approve-before-rollback · `L2` auto-rollback + approve-the-fix-PR · `L3` full. Durable approval gate (`/internal/approve`, dashboard Approve/Deny); advisory promotion + automatic demotion on a failed heal. | [`autonomy.py`](agent/autosre/autonomy.py) |
| **Learned baseline + memory** | The analyzer's baseline is **learned per service** (EMA of steady-state healthy samples), not hardcoded. Cross-incident memory tracks failures + flags a **recurring** incident ("the fix isn't holding"). | [`memory.py`](agent/autosre/memory.py) |

The deterministic-core / LLM-advisory rule still holds throughout: Gemini decides, the state machine
(now with a statistical gate **and** an autonomy gate) validates and acts.

## v3 — causal certainty across signals (live, on by default)
| Upgrade | What it does | Where |
|---|---|---|
| **Multi-signal detection** | A deterministic detector+fusion engine (`AIRBAG_SIGNALS`): the **latency detector** catches a 200-but-slow regression (Wilson-gated per-window slow proportion + an N-window debounce) and feeds the same FAIL/PASS/INCONCLUSIVE contract; a confident FAIL **promotes** a rollback even when the LLM hedges. Recovery is then proven **on the signal that triggered** (a slow-but-200 probe is not "recovered" for a latency incident). | [`signals/`](agent/autosre/signals/) |
| **Causal pre-check** | Before spending the rollback, **probe the rollback target directly**: if it is ALSO confidently degraded, the cause is external → **escalate without the futile traffic shift**. | [`causal.py`](agent/autosre/causal.py) |
| **Airbag-Bench** | A committed, labeled incident-replay bench + scorecards with a CI ratchet — every decision-quality claim above is a reproducible number, honestly framed as the deterministic floor. | [`docs/AIRBAG_BENCH.md`](docs/AIRBAG_BENCH.md) |

## v4 — the action is provably CORRECT and provably SAFE (live)
v3 made detection trustworthy; v4 makes the one reversible **action** trustworthy. The rollback
target used to be "the newest ready 0-traffic revision" — **recency is only a proxy** for
last-good, and a bad→bad deploy sequence defeats it.

| Upgrade | What it does | Where |
|---|---|---|
| **Serving-history ledger** (the marquee) | Airbag **witnesses** revisions it has *observed serving healthily* (confident no-op runs; `_verify`-proven mitigation targets) into a bounded per-service Firestore map, and target selection **prefers witnessed-good over merely-newest** (cold start = today's behavior). The FSM even **re-aims** an LLM-proposed target that has no witnessed history (caught live: Gemini aimed a latency rollback at the 5xx landmine). The ledger only **proposes** — the live causal probe still gates every selection. Scored: the bench's **target-correctness** dimension, with bad→bad fixtures where recency aims at the landmine and the ledger heals. | [`memory.py`](agent/autosre/memory.py) · [`state_machine.py`](agent/autosre/state_machine.py) |
| **Latency-aware target-probe** | The causal probe now matches the **incident's axis**: for a latency incident, a 200-but-confidently-slow target is vetoed (`{errs,total,slow}` + a second Wilson gate; cold-start rinse so a scaled-to-zero target's boot never counts). Causal-mode false rollbacks: **0 on both external-cause axes**. | [`causal.py`](agent/autosre/causal.py) |
| **Irreversible-deploy guard** | A deploy that performed a forward-only change (schema migration) **declares** it (`airbag.dev/irreversible=<id>` revision annotation); Airbag refuses to roll back **across** the declared marker (that "reversible" action would corrupt every write) and escalates instead. Honors a declared contract — does **not** detect migrations. Fail-open, **default OFF**. | [`reversibility.py`](agent/autosre/reversibility.py) |
| **Firestore-emulator CI gate** | The durable-state contract (transactions, leases, ordered reads, the ledger) is proven against **real** Firestore transactions in CI, not just the in-memory mimic. | [`.github/workflows/ci.yml`](.github/workflows/ci.yml) |

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
