# Architecture

## State machine (the transaction)
```
RECEIVED → TRIAGED → ADK(triage→decide) → DECISION
   ├─ OBSERVE → DONE
   └─ ROLLBACK → ROLLBACK_APPLIED → VERIFYING ──(error→0 & probe ok)──→ MITIGATED → FIX_PR → PENDING_REVERT
                                       └─(budget exceeded)→ ESCALATED

   # close the transaction (complete_rollback, triggered by the fix-PR CI or the dashboard):
   COMPLETE_ROLLBACK → FIX_DEPLOYED → CANARY(10/50/100, direct fix-probe gate) ──(healthy)──→ ROLLBACK_UNDONE → CLOSED
                                          └─(fix unhealthy / none)──→ MANUAL_INTERVENTION (compensate: traffic back to safe revision)
```
- **Fast path (deterministic, reversible):** rollback Cloud Run traffic to the last-good revision. Stops the bleeding; zero data-migration risk.
- **Slow path:** Gemini writes a fix → real GitHub Actions CI → deploy → **verify the deployed revision IS the fix** → undo the temporary rollback.
- The two paths are one transaction with a **compensating action**: traffic is restored to the fix only after it's proven healthy; otherwise it routes straight back to the safe rolled-back revision. The undo is triggered by `/internal/complete-rollback` (the fix-PR's CI) or the dashboard's **Verify & Undo** button. State is held in a pluggable durable store (`state_store.py`, `AIRBAG_STATE=memory|firestore`) with a self-healing lease lock — running live on **Firestore**.

## v2 — autonomous-SRE upgrades (live)
- **Statistical gate** (`analyzer.py`) — the rollback decision is gated by a **Wilson confidence-interval** verdict (`FAIL`/`PASS`/`INCONCLUSIVE`) over a fresh business-path sample, with a per-service **learned baseline** (`memory.py`, EMA of healthy samples). Replaces the static `5xx ≥ 5%`.
- **Durable state + multi-instance** (`state_store.py`, `events.py`) — pending reverts / incidents / webhook-dedup behind one atomic `transact`; Firestore transaction for the exactly-once completion **lease** (self-healing if a heal crashes). With a **Pub/Sub event-bus fan-out** (`AIRBAG_EVENTS=pubsub`) every instance mirrors every instance's events, so the dashboard stream is instance-agnostic and the agent runs `--max-instances 3`.
- **Graduated autonomy** (`autonomy.py`) — per-service `L0` observe / `L1` approve-before-rollback / `L2` auto-rollback + approve-fix / `L3` full, enforced in the deterministic state machine; durable approval gate (`/internal/approve`); advisory promotion + automatic demotion.
- **Cross-incident memory** (`memory.py`) — incident history + **recurrence** detection (advisory "the fix isn't holding" signal).

## Components
| Concern | Tech | Notes |
|---|---|---|
| Webhook + orchestration | FastAPI on Cloud Run | 202-then-async; token/HMAC; idempotent |
| Decision brain | ADK 1.x `SequentialAgent` (triage→decide), runs at decision time; triage calls the Cloud Run/Monitoring tools via ADK function-calling; decide emits Gemini `responseSchema` | LLM only decides; never executes prod. Falls back to a direct Gemini call then a heuristic (`adk_brain.py`) |
| Stop-the-bleeding | `google-cloud-run` `run_v2` traffic split | explicit revision, `.result()` the op |
| Proof of recovery | Cloud Logging 5xx scan + synthetic **business-path** (`/api/orders`) probe | zero-traffic guard (probe, not `/healthz`, which can stay 200 during the fault) |
| Permanent fix (v2 pipeline) | `fix_pipeline`: RCA from the real stack trace → discover the culprit file → patch + author a regression test (Gemini patch model) → **sandbox-verify** (test fails on the bug, passes on the fix) → PR commits the fix **and** the test via GitHub REST (httpx) | self-proving PR (no pre-planted oracle); App-PR needs `on:push`; falls back to a single-call fix |
| Sandbox for the LLM-authored test | `sandbox.py`, `AIRBAG_SANDBOX=subprocess` (default) \| `cloudrun_job` | **subprocess**: bounded local subprocess, metadata server neutralized (used for the demo). **cloudrun_job** (production posture, `infra/sandbox-job-setup.sh`): runs the test in an **egress-disabled Cloud Run Job** under a **zero-permission SA** — no LLM code runs in the prod agent's privileged container. Falls back to subprocess on error |
| State + events | `state_store` (memory \| **Firestore**) via atomic `transact` + self-healing lease; **`events` Pub/Sub fan-out** for cross-instance SSE | live on Firestore + Pub/Sub at **`--max-instances 3`** (durable + multi-instance). Cloud Tasks durable *work* queue + remote MCP are built + tested but opt-in/off in the demo |
| Autonomy + memory | `autonomy.py` (L0–L3 + durable approval gate, trust ramp) · `memory.py` (learned baseline + recurrence) | levels enforced deterministically; baseline learned per service |
| Secrets / IAM | Secret Manager + least-priv SA | `run.admin`, `monitoring.viewer`, `logging.viewer`, `secretmanager.secretAccessor` (AI Studio key, so no `aiplatform.user`) |
| Dashboard | `agent/static/dashboard.html` (vanilla JS + SSE) | replays the event stream as a verifiable thought-chain; links the per-incident report Artifact |

## Decision schema (Gemini structured output)
```python
class IncidentDecision(BaseModel):
    action: Literal["ROLLBACK", "OBSERVE", "ESCALATE"]
    bad_revision: str | None = None
    rollback_revision: str | None = None
    confidence: float        # 0..1
    reasoning: str = ""      # shown on the dashboard + incident report
    evidence: list[str] = []
```
The executor handles `ROLLBACK` (act), `ESCALATE` (surface to a human), and `OBSERVE` (no-op). The
fix PR is **not** a top-level action — it's a downstream step of `ROLLBACK` (opened by the
deterministic slow path), so it is not in the enum (the old `OPEN_FIX_PR` value silently became a
no-op `DONE` that polluted the learned baseline — dropped in v3 Phase 0.3). The state machine only
rolls back when `action == ROLLBACK`, `confidence ≥ threshold`, and
`rollback_revision ∈ known_good_revisions`.
