# Architecture

## State machine (the transaction)
```
RECEIVED → TRIAGED → ANALYZED(multi-signal Wilson verdict) → ADK(triage→decide) → DECISION
   ├─ OBSERVE → DONE (witness the serving revision into the ledger on confident health)
   └─ ROLLBACK (target: WITNESSED-healthy preferred, recency fallback; FSM re-aims an unwitnessed LLM aim)
        → [REVERSIBILITY guard (v4, flag)] → [CAUSAL target-probe (live, signal-keyed)]
        → ROLLBACK_APPLIED → VERIFYING ──(signal recovered & probe ok)──→ MITIGATED (witness the target) → FIX_PR → PENDING_REVERT
             └─(guard BLOCK / probe COINCIDENT / budget exceeded)→ ESCALATED (zero or reverted shift)

   # close the transaction (complete_rollback, triggered by the fix-PR CI or the dashboard):
   COMPLETE_ROLLBACK → FIX_DEPLOYED → CANARY(10/50/100, direct fix-probe gate) ──(healthy)──→ ROLLBACK_UNDONE → CLOSED
                                          └─(fix unhealthy / none)──→ MANUAL_INTERVENTION (compensate: traffic back to safe revision)
```
- **Fast path (deterministic, reversible):** rollback Cloud Run traffic to the last-good revision. Stops the bleeding; zero data-migration risk.
- **Slow path:** Gemini writes a fix → real GitHub Actions CI → deploy → **verify the deployed revision IS the fix** → undo the temporary rollback.
- The two paths are one transaction with a **compensating action**: traffic is restored to the fix only after it's proven healthy; otherwise it routes straight back to the safe rolled-back revision. The undo is triggered by `/internal/complete-rollback` (the fix-PR's CI) or the dashboard's **Verify & Undo** button. State is held in a pluggable durable store (`state_store.py`, `AIRBAG_STATE=memory|firestore`) with a self-healing lease lock — running live on **Firestore**.

## v2 — autonomous-SRE upgrades (live)
- **Statistical gate** (`analyzer.py`) — the rollback decision is gated by a **Wilson confidence-interval** verdict (`FAIL`/`PASS`/`INCONCLUSIVE`) over a fresh business-path sample, with a per-service **learned baseline** (`memory.py`, EMA of healthy samples). Replaces the static `5xx ≥ 5%`.

## v3 — multi-signal detection (Phase 1)
- **Multi-signal engine** (`signals/`) — a pluggable, **deterministic** (LLM-free, invariant-guarded) `Detector` + fusion engine behind `AIRBAG_SIGNALS` (default `5xx` = v2 behavior verbatim). Adds a **latency detector** (Wilson-gates the per-window over-SLO proportion — same rigor as 5xx — with an **N-window persistence/debounce** gate so a momentary spike can't trigger). Fuses N detectors into the *same* FAIL/PASS/INCONCLUSIVE contract the FSM gate consumes. Enable with `AIRBAG_SIGNALS=5xx,latency`. On Airbag-Bench this lifts rollback recall 33%→67% with the false-rollback rate flat (saturation/burn-rate deferred). See `docs/AIRBAG_BENCH.md`.
- **Deterministic promotion** (`state_machine._validate`) — a confident (FAIL) **statistical** verdict now drives a rollback even when the LLM/heuristic hedged to OBSERVE (picking a known-good target deterministically, or ESCALATE if none). The verdict is deterministic — the LLM still never touches prod — so this is what makes multi-signal detection *act*, not just observe. Only on FAIL (never INCONCLUSIVE) → no alert fatigue.
- **Causal pre-check** (`causal.py`, Phase 2a, `AIRBAG_CAUSAL_CHECK`) — before committing a rollback, **probe the rollback target's health**: if the last-good revision is ALSO confidently degraded, the cause is external (dependency/quota), not this revision → **ESCALATE without the wasted traffic shift**. Only a *confident*-unhealthy target blocks (Wilson gate); transient/flaky/errored → proceed (never blocks a legit rollback). Sits at the top of `_mitigate` (covers L2/L3 + the L1-approved resume). On Airbag-Bench: rollback **precision 75%→100%, false-rollback 11.8%→0%, recall unchanged** (zero legit rollbacks blocked). Deterministic + LLM-free (under the AST invariant); default off. See `docs/AIRBAG_BENCH.md`.
- **Durable state + multi-instance** (`state_store.py`, `events.py`) — pending reverts / incidents / webhook-dedup behind one atomic `transact`; Firestore transaction for the exactly-once completion **lease** (self-healing if a heal crashes). With a **Pub/Sub event-bus fan-out** (`AIRBAG_EVENTS=pubsub`) every instance mirrors every instance's events, so the dashboard stream is instance-agnostic and the agent runs `--max-instances 3`.
- **Graduated autonomy** (`autonomy.py`) — per-service `L0` observe / `L1` approve-before-rollback / `L2` auto-rollback + approve-fix / `L3` full, enforced in the deterministic state machine; durable approval gate (`/internal/approve`); advisory promotion + automatic demotion.
- **Cross-incident memory** (`memory.py`) — incident history + **recurrence** detection (advisory "the fix isn't holding" signal).

## v4 — provably-correct, provably-safe ACTION (live)
v3 made detection + diagnosis trustworthy; v4 makes the ONE reversible action — the rollback —
provably aimed at the right revision and provably safe to take. No new detectors (the bottleneck
was action-target correctness, not detection breadth). All deterministic + LLM-free (AST-guarded).

- **Serving-history ledger** (`memory.witness_serving`/`witnessed_healthy` — the marquee). The
  rollback target was the *newest ready 0-traffic revision*: recency as a **proxy** for last-good,
  which a bad→bad deploy sequence defeats. Airbag now **witnesses** revisions it has *observed
  serving healthily* (a PASS / zero-5xx no-op run, the L1-approval PASS re-check, or a
  `_verify`-proven mitigation target — never an unverified shift, never a flaky window) into a
  bounded per-service map on the same Firestore doc as the learned baseline. Target selection
  (`_preferred_target`, wired into the heuristic + `_validate`'s promotion) **prefers the newest
  witnessed candidate**; cold start falls back to recency byte-identical. The FSM also **re-aims**
  an LLM-proposed target that has no witnessed history when a witnessed candidate exists (observed
  live: Gemini aimed a latency rollback at the 5xx landmine; a single deterministic substitution —
  not a candidate-walk). HONEST LIMITS: the ledger only **PROPOSES** — the live causal probe still
  gates whatever is selected (a stale witness can never bypass it), and "witnessed-healthy" is a
  fact about *witness time*, not now. Scored on the bench's v4 **target-correctness** dimension:
  the bad→bad fixtures show cold-start recency aiming at the landmine (wasted rollback, or a
  causal veto that pages a human) vs the ledger healing autonomously onto witnessed-good.
- **Latency-aware causal target-probe** (`causal.py` + `probe_revision_health` → `{errs,total,slow}`).
  The v3 probe counted only 5xx, so a 200-but-slow target passed the pre-check for a *latency*
  incident. The probe now times each request; for a latency-triggered incident a second Wilson gate
  (the latency detector's own knobs) vetoes a confidently-slow target → COINCIDENT → escalate with
  zero shift. The gcp probe **rinses the cold start** (one untimed request) so a scaled-to-zero
  target's boot latency is never counted as veto evidence, and drops unreachable samples (the veto
  honestly covers the SLO→10s band; beyond it `_verify` remains the backstop). 5xx-incident
  behavior unchanged; VETO-only; ships ON in prod (rides `AIRBAG_CAUSAL_CHECK`). Causal-mode false
  rollbacks: **0 across both external-cause axes** on the bench.
- **Forward-only / irreversible-deploy guard** (`reversibility.py`, `AIRBAG_REVERSIBILITY_GUARD`,
  **default OFF**, fail-OPEN). The one gap every other gate greenlights: a rollback across a deploy
  that performed a forward-only change (schema migration) puts pre-migration code in front of the
  migrated datastore — the target boots, probes 200, `_verify` can pass, and every write corrupts.
  A deploy **declares** the change with the revision annotation `airbag.dev/irreversible=<id>`
  (revision-template annotations via service YAML / Terraform; the value is ideally a migration id).
  The guard blocks only a rollback that **crosses** a declared marker on the traffic path
  (`epoch(target) < epoch(marker) ≤ epoch(serving)`) declaring a change the target doesn't itself
  carry — so a staged `--no-traffic` marker doesn't block, and Cloud Run's **sticky** template
  annotations (inherited by every later revision) read as ONE declaration, never freezing all
  future rollbacks. HONEST LIMITS: it **honors a declared contract**; it does NOT detect
  migrations; undeclared forward-only deploys are invisible to it.
- **Firestore-emulator CI gate** (+ `firestore.indexes.json`). Prod runs `AIRBAG_STATE=firestore`
  but the suite pinned the memory mimic — now the state-critical suite (store contract, ordered
  reads, the ledger, approvals) runs against real google-cloud-firestore transactions in CI, with
  the backend divergence pinned (Firestore `order_by` silently omits docs missing the order field;
  every writer always stamps it). The indexes file is deliberately empty: every query is a
  single-field `order_by` (auto-indexed) and the ledger is a by-id doc read.

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
