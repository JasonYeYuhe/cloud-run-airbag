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
| Decision brain | ADK 1.x `SequentialAgent` (triage→decide) + Gemini `responseSchema` | LLM only decides; never executes prod |
| Stop-the-bleeding | `google-cloud-run` `run_v2` traffic split | explicit revision, `.result()` the op |
| Proof of recovery | Cloud Monitoring PromQL 5xx ratio + synthetic `/healthz` probe | zero-traffic guard |
| Permanent fix | GitHub App + Octokit/PyGithub + GitHub Actions | App-PR needs `on:push` |
| State | Cloud SQL via ADK `DatabaseSessionService` | not InMemory |
| Secrets / IAM | Secret Manager + least-priv SA | `run.developer`, `monitoring.viewer`, `logging.viewer`, `secretmanager.secretAccessor`, `aiplatform.user` |
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
