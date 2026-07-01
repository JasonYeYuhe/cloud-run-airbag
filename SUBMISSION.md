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
- Gemini opens a real **fix PR** for the planted `KeyError` via an **agentic pipeline** (RCA from
  the real stack trace → patch → an agent-authored regression test **sandbox-verified to fail on the
  bug and pass on the fix** → PR commits the fix *and* the test) → GitHub Actions **CI green**
  ([open PRs](https://github.com/JasonYeYuhe/cloud-run-airbag/pulls)).
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
  token-gated so the public dashboard is watch-only. **172 tests (165 agent + 7 mcp-server), CI green.**

**v2 — production-grade autonomy (real, verified on live Cloud Run):**
- **Statistical decision gate** — the rollback trigger is a **Wilson confidence-interval** verdict
  (`FAIL`/`PASS`/`INCONCLUSIVE`), not a static threshold: `PASS`→withhold, `INCONCLUSIVE`→escalate,
  `FAIL`→proceed. Verified live (`ANALYZED: FAIL — CI lower 83.9% > baseline 2%`).
  [`analyzer.py`](agent/autosre/analyzer.py).
- **Durable Firestore state + multi-instance** — pending reverts / incidents / dedup behind one
  atomic `transact` with a self-healing **lease** lock (survives container recycles). Paired with a
  **Pub/Sub event-bus fan-out** (`AIRBAG_EVENTS=pubsub`) so a dashboard on any instance sees a heal
  that ran on any other, the agent runs live at **`--max-instances 3`** — verified: a heal's events
  fan out across instances. [`state_store.py`](agent/autosre/state_store.py) ·
  [`events.py`](agent/autosre/events.py).
- **Graduated autonomy** — per-service `L0/L1/L2/L3` enforced in the state machine, with a
  **durable approval gate** (`/internal/approve`, dashboard Approve/Deny) + advisory promotion /
  automatic demotion. Verified live: L1 held the rollback at 500 until approved, then recovered.
  [`autonomy.py`](agent/autosre/autonomy.py).
- **Learned baseline + cross-incident memory** — the analyzer's baseline is learned per service
  (EMA of healthy samples); memory flags a **recurring** incident. [`memory.py`](agent/autosre/memory.py).
- **Fully-unattended CI close (Workload Identity Federation):** a GitHub Actions run authenticates
  to GCP **keylessly** (OIDC → WIF, no stored key), deploys the fix `--no-traffic`, and calls
  `/internal/complete-rollback`; Airbag verifies the fix, canary-restores traffic (10→50→100), and
  CLOSES — **with no human in the path**. Verified live end-to-end (incident →
  `COMPLETE_ROLLBACK → FIX_DEPLOYED → CANARY×3 → ROLLBACK_UNDONE → CLOSED`). Setup:
  [`infra/wif-setup.sh`](infra/wif-setup.sh) + [`complete-rollback.yml`](.github/workflows/complete-rollback.yml).
- Each v2 upgrade was built against an adversarial review (Gemini 3.1 Pro + 3.5 Flash, and/or a
  multi-agent review workflow with refute-by-default verification) and the findings fixed.
- **Implemented + tested but OFF in the live demo** (opt-in flags — kept off to keep the demo simple
  and the attack surface small): a **Cloud Tasks** durable work queue (`AIRBAG_QUEUE=cloudtasks`,
  redelivers a heal across an instance recycle) and an **MCP server** — a local stdio server
  ([`mcp-server/`](mcp-server/)) plus a flag-gated remote endpoint (`AIRBAG_MCP_HTTP`) — so other
  agents (Claude, Cursor) can drive Airbag. Both were live-verified, then turned off for the demo.

> Framing: the irreducible core is ~4 files — **one atomic state primitive (`transact`) + one
> deterministic transaction (`state_machine`)**; every durability/governance feature above is a thin
> policy on top. That's design discipline, not feature-stacking.

**v3 — causal certainty across more than one signal (real, on live Cloud Run rev 00029):**
The v2 moat ("catch a bad deploy out-of-window and act reversibly") was single-signal (5xx) and
un-measured. v3 makes Airbag *causally certain before it acts, across more than one signal* — and
builds a measuring stick to prove it. All new intelligence is **deterministic + LLM-free** (guarded
by an AST architecture-invariant test); the FSM still acts, the LLM only advises.
- **Airbag-Bench** ([`docs/AIRBAG_BENCH.md`](docs/AIRBAG_BENCH.md)) — a labeled incident-replay harness
  that scores rollback precision/recall, false-rollback rate, and Alert-to-Verified-Recovery over a
  17-case corpus; committed scorecards + a golden-ratchet CI gate make it a real TDD loop.
- **Multi-signal detection** (`signals/`) — a latency-regression detector (Wilson-gated slow-request
  proportion, N-window debounce) fused into the same FAIL/PASS/INCONCLUSIVE verdict the gate consumes,
  + a deterministic **promotion** so a confident statistical FAIL drives a rollback even when the LLM
  hedged. Bench: rollback **recall 50%→75%** (catches the out-of-window latency regression 5xx misses),
  false-rollback rate flat. Behind `AIRBAG_SIGNALS` (default 5xx → demo unchanged).
- **Causal pre-check** (`causal.py`) — before spending the one reversible action, **probe the rollback
  target's health**: if the last-good revision is *also* degraded, the cause is external
  (dependency/quota), not this revision → ESCALATE without a futile rollback. Only a *confident*-unhealthy
  target blocks; a transient/flaky/errored probe proceeds (never blocks a legit rollback). Bench (the
  causal step, on the 5xx+latency config): **precision 75%→100%, false-rollback rate 2/17→0, recall
  held** — zero legitimate rollbacks blocked. (Cumulative 5xx-floor → full v3: precision 67%→100%.)
- **Legibility & audit** — the incident report + glassbox dashboard surface the multi-signal per-detector
  verdict, the causal pre-check, and the headline **⚡ Alert→Verified-Recovery time**; every incident has
  a tamper-evident **proof bundle** (`/incidents/{id}/proof`, sha256 content digest).
- **Sandbox hardening** — the LLM-authored regression test now runs in an **egress-disabled Cloud Run
  Job** under a zero-permission SA (`AIRBAG_SANDBOX=cloudrun_job`), live-verified (network egress
  empirically blocked) — no untrusted code executes in the prod agent's privileged container.
- **Honest scope (first-principles):** a graded-confidence *verifier* (a second Gemini gating pass) was
  **cut** after review showed it was redundant with the deterministic gates for safety and its ratchet
  would force a human on a confident real outage; saturation + SLO-burn detectors are **deferred**
  (false-positive-prone / not yet provable). Multi-signal + causal are opt-in (off in the demo).

**Roadmap (P2, honestly not done):**
- ChatOps (Slack approvals on top of the autonomy gate); Cloud Assist composition.
- Enable multi-signal + causal in the live demo (a latency-fault scenario); saturation/burn-rate
  detectors; a WIF/KMS-signed (not just digest) proof bundle; MCP action tools for A2A.

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
