# Architecture

## State machine (the transaction)
```
RECEIVED → LOCKED(idempotent) → TRIAGED → DECISION
   ├─ OBSERVE → DONE
   └─ ROLLBACK → ROLLBACK_APPLIED → VERIFYING ──(error→0 & probe ok)──→ MITIGATED
                                       └─(budget exceeded)→ ESCALATED
   MITIGATED → FIX_PR_OPEN → CI_GREEN → DEPLOY_FIXED → VERIFIED → REVERT_TEMP_ROLLBACK → CLOSED   (stretch)
```
- **Fast path (deterministic, reversible):** rollback Cloud Run traffic to the last-good revision. Stops the bleeding; zero data-migration risk.
- **Slow path (probabilistic, human-gated):** Gemini writes a fix → real GitHub Actions CI → deploy → verify → undo the temporary rollback.
- The two paths are one transaction with compensating actions (undo rollback only after the permanent fix is verified).

## Components
| Concern | Tech | Notes |
|---|---|---|
| Webhook + orchestration | FastAPI on Cloud Run | 200-then-async; token/HMAC; idempotent |
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
