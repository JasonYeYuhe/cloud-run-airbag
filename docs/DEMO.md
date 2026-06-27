# Demo script & judge talking points

## The 90-second live flow (what the dashboard shows)
1. **Set the scene (10s).** "A bad revision shipped hours ago. The canary window is long gone. Nobody's watching." Target app is green.
2. **Inject (5s).** Click **💣 Inject fault** → target's `/api/orders` starts returning 5xx. Error-rate curve spikes to 100%, gate shows `PENDING`.
3. **Autonomous heal (40s).** Click **🚨 Trigger incident** (or **▶ Run demo** to do both). Watch the thought-chain stream:
   `RECEIVED → TRIAGED → DECISION(ROLLBACK, conf 92%) → ROLLBACK_APPLIED → VERIFYING… → MITIGATED`.
   The revision traffic bar flips good→100%, the error-rate curve drops to 0, the gate turns green **✓ VERIFIED RESOLVED**. No human touched it.
4. **The point (15s).** "Traffic is back on the healthy revision, and we *proved* the 5xx rate hit zero — not 'metrics didn't get worse'. The fix PR is the next, human-gated step."

> Run it: `./run-local.sh` → http://localhost:8080. (The dashboard also self-plays offline if the agent isn't up.)

## Why judges should care (the differentiation, grounded)
- **Out-of-window detection.** Every auto-rollback tool (Argo/Harness/LaunchDarkly/Sedai) only acts inside the deploy/canary window. **78% of orgs have had an incident with *no* alert firing** — that's the gap we own: an independent production alert, hours later, still triggers a rollback.
- **Action layer, not diagnosis layer.** Gemini Cloud Assist is officially advisory ("don't modify… human-in-the-loop required"); Jules only writes code offline. We *act* on Cloud Run and *prove* recovery. "Cloud Assist tells you what's wrong; Jules writes code; **we fix the live incident.**"
- **Reversible by design = safe to be autonomous.** The stop-the-bleeding action is a traffic shift to an already-healthy revision — instantly reversible, zero data-migration risk. That's why default-autonomous is defensible here when nobody else dares.
- **Deterministic execution + Gemini decision.** The LLM only *decides*; a deterministic gate (confidence + known-good revision) validates, then the state machine acts. Judges see governance, not a chatbot with prod access.
- **Zero-error proof gate.** Success = Cloud Logging/Monitoring shows error-rate back to **0** + a synthetic probe passes (guards the zero-traffic trap). A binary, on-screen, verifiable criterion.

## Required-stack story (one coherent agent, not a checklist)
Gemini (structured decision) · **ADK** (the triage→decide→act→verify state machine) · **Cloud Run** (both the patient and the runtime) · Cloud Logging/Monitoring (the proof) · Secret Manager + least-priv IAM (the governance).

## Real vs stretch (be honest with judges)
- **Real now:** detection → decision → **rollback** → **verified recovery**, end-to-end, on a live target. Three execution backends (mock/local/gcp); same agent code.
- **Stretch (wired, gated):** Gemini-authored fix PR → real GitHub Actions CI → redeploy → undo the temporary rollback. Shown as the `FIX_PR` stage; the safe core stands on its own.
