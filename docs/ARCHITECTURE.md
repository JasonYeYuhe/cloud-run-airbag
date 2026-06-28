# Architecture

## State machine (the transaction)
```
RECEIVED → TRIAGED → ADK(triage→decide) → DECISION
   ├─ OBSERVE → DONE
   └─ ROLLBACK → ROLLBACK_APPLIED → VERIFYING ──(error→0 & probe ok)──→ MITIGATED → FIX_PR → PENDING_REVERT
                                       └─(budget exceeded)→ ESCALATED

   # close the transaction (complete_rollback, triggered by the fix-PR CI or the dashboard):
   COMPLETE_ROLLBACK → FIX_DEPLOYED → REVERIFYING ──(fix healthy)──→ ROLLBACK_UNDONE → CLOSED
                                          └─(fix unhealthy / none)──→ MANUAL_INTERVENTION (compensate: traffic back to safe revision)
```
- **Fast path (deterministic, reversible):** rollback Cloud Run traffic to the last-good revision. Stops the bleeding; zero data-migration risk.
- **Slow path:** Gemini writes a fix → real GitHub Actions CI → deploy → **verify the deployed revision IS the fix** → undo the temporary rollback.
- The two paths are one transaction with a **compensating action**: traffic is restored to the fix only after it's proven healthy; otherwise it routes straight back to the safe rolled-back revision. The undo is triggered by `/internal/complete-rollback` (the fix-PR's CI) or the dashboard's **Verify & Undo** button. State lives in-process (`pending.py`) + `--min-instances=1`; durable Firestore is roadmap.

## Components
| Concern | Tech | Notes |
|---|---|---|
| Webhook + orchestration | FastAPI on Cloud Run | 202-then-async; token/HMAC; idempotent |
| Decision brain | ADK 1.x `SequentialAgent` (triage→decide), runs at decision time; triage calls the Cloud Run/Monitoring tools via ADK function-calling; decide emits Gemini `responseSchema` | LLM only decides; never executes prod. Falls back to a direct Gemini call then a heuristic (`adk_brain.py`) |
| Stop-the-bleeding | `google-cloud-run` `run_v2` traffic split | explicit revision, `.result()` the op |
| Proof of recovery | Cloud Logging 5xx scan + synthetic **business-path** (`/api/orders`) probe | zero-traffic guard (probe, not `/healthz`, which can stay 200 during the fault) |
| Permanent fix | GitHub REST (httpx) + fine-grained repo-scoped token + GitHub Actions | App-PR needs `on:push` |
| State | in-process (`_seen_incidents`) + `--min-instances=1` | Firestore/Cloud SQL durable state is roadmap (P1/P2); min-instances carries the demo |
| Secrets / IAM | Secret Manager + least-priv SA | `run.admin`, `monitoring.viewer`, `logging.viewer`, `secretmanager.secretAccessor` (AI Studio key, so no `aiplatform.user`) |
| Dashboard (later) | Next.js | replays the event stream as a verifiable thought-chain Artifact |

## Decision schema (Gemini structured output)
```python
class IncidentDecision(BaseModel):
    action: Literal["ROLLBACK", "OBSERVE", "OPEN_FIX_PR", "ESCALATE"]
    bad_revision: str | None
    rollback_revision: str | None
    confidence: float
    evidence: list[str]
```
The state machine only acts when `action == ROLLBACK`, `confidence ≥ threshold`, and `rollback_revision ∈ known_good_revisions`.
