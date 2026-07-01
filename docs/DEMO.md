# Demo script & judge talking points

## The 90-second live flow (what the dashboard shows)
Dashboard controls: **💣 Break** (route traffic to the bad revision) · **🚑 Heal** (trigger the
agent) · **✅ Verify & Undo** (verify the deployed fix, then undo the temporary rollback) ·
**↺ Reset** (route back to healthy) · **▶ Run demo** (Break then Heal, one click).
Repeatable: Break → Heal → Reset, as many times as you like.

1. **Set the scene (10s).** "A bad revision shipped hours ago. The canary window is long gone. Nobody's watching." Target app is green.
2. **Break (5s).** Click **💣 Break** → 100% of traffic shifts to the bad revision (`FAULT_MODE=bug`); `/api/orders` raises a `KeyError` → HTTP 500. Error-rate curve spikes to 100%, gate shows `PENDING`.
3. **Autonomous heal (40s).** Click **🚑 Heal** (or use **▶ Run demo** to do both). Watch the thought-chain stream:
   `RECEIVED → TRIAGED → ADK(triage→decide) → DECISION(ROLLBACK, conf ~0.9) → ROLLBACK_APPLIED → VERIFYING… → MITIGATED → FIX_PR`.
   The revision traffic bar flips healthy→100%, the error-rate curve drops to 0, the gate turns green **✓ VERIFIED RESOLVED**. No human touched it.
4. **The point (15s).** "Traffic is back on the healthy revision, and we *proved* the 5xx rate hit zero — not 'metrics didn't get worse'. The decision ran through the **ADK SequentialAgent** (Gemini calling Cloud Run tools itself); the deterministic state machine executed it. Then Gemini opened a **fix PR for that same `KeyError`** — rolled back *and* root-cause fixed."
5. **Close the loop (optional, ~30s).** After the fix PR merges and a fixed revision deploys,
   click **✅ Verify & Undo** (or let the fix-PR's CI call `/internal/complete-rollback`). The
   agent verifies the new revision IS the fix, restores traffic to it, and the chain reaches
   `FIX_DEPLOYED → CANARY(10→50→100) → ROLLBACK_UNDONE → CLOSED` — and if the "fix" were unhealthy it
   would **compensate** straight back to the safe revision (`MANUAL_INTERVENTION`).
6. **Reset (5s).** Click **↺ Reset** to route back to the healthy baseline and run it again.

> Run it locally: `./run-local.sh` → http://localhost:8080. (The dashboard also self-plays offline if the agent isn't up.)
> Run it on live Cloud Run: open the agent URL (operator link with `?token=` pre-fills the demo token), then Break → Heal → Reset.

**Demo-flow note (gcp):** a *rollback* goes to a **previous** good revision, so the bad revision
must be the **newest** one — which is exactly what `scripts/gcp-demo-setup.sh` sets up. Break →
Heal → Reset is infinitely repeatable (it only shifts traffic, creates no revisions). The **Verify
& Undo** step deploys a *new* fix revision (now the newest), which inverts that ordering — so after
demoing Verify & Undo, re-run `./scripts/gcp-demo-setup.sh` before the next full Break → Heal cycle.

## Why judges should care (the differentiation, grounded)
- **Out-of-window detection.** Every auto-rollback tool (Argo/Harness/LaunchDarkly/Sedai) only acts inside the deploy/canary window. **78% of orgs have had an incident with *no* alert firing** — that's the gap we own: an independent production alert, hours later, still triggers a rollback.
- **Action layer, not diagnosis layer.** Gemini Cloud Assist is officially advisory ("don't modify… human-in-the-loop required"); Jules only writes code offline. We *act* on Cloud Run and *prove* recovery. "Cloud Assist tells you what's wrong; Jules writes code; **we fix the live incident.**"
- **Reversible by design = safe to be autonomous.** The stop-the-bleeding action is a traffic shift to an already-healthy revision — instantly reversible, zero data-migration risk. That's why default-autonomous is defensible here when nobody else dares.
- **Deterministic execution + Gemini decision.** The LLM only *decides*; a deterministic gate (confidence + known-good revision) validates, then the state machine acts. Judges see governance, not a chatbot with prod access.
- **Zero-error proof gate.** Success = Cloud Logging/Monitoring shows error-rate back to **0** + a synthetic probe passes (guards the zero-traffic trap). A binary, on-screen, verifiable criterion.

## Required-stack story (one coherent agent, not a checklist)
**Gemini** decides — **through ADK**: the `SequentialAgent` (triage → decide) runs at decision
time, the triage `LlmAgent` calling the Cloud Run / Monitoring tools itself via ADK
function-calling, then the decision `LlmAgent` emits a structured `IncidentDecision`. A
deterministic state machine validates + executes it on **Cloud Run** (both the patient and the
agent's runtime). Cloud Logging/Monitoring is the proof; Secret Manager + least-priv IAM is the
governance. (ADK can be disabled with `AIRBAG_USE_ADK=false`, falling back to a direct Gemini
call then a heuristic — the heal never blocks on the LLM.)

## Real vs stretch (be honest with judges)
- **Real now:** detection → **ADK/Gemini decision** → **rollback** → **verified recovery** → **Gemini fix PR through real CI** (with **CI self-correction** on red) → **verify the fix + undo the rollback via a gradual canary** (10→50→100, compensate on failure), end-to-end on a live target. Every run is persisted as a **verifiable incident-report Artifact** (`/incidents/{id}/report`). Three execution backends (mock/local/gcp); same agent code; 166 tests.
- **Stretch / roadmap (P2):** the *fully-unattended* CI trigger (`complete-rollback.yml` deploys the fix then calls the endpoint) needs a one-time Workload Identity Federation binding; durable Firestore state; Cloud Tasks/Pub-Sub worker.
