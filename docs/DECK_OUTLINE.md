# Pitch deck outline — Airbag (10 slides, v4)

Story arc: *the 3am gap → why only an agent closes it → the loop → why autonomy is safe here →
the v4 proof → receipts → stack → impact*. One idea per slide; the dashboard mid-heal screenshot
is the hero. Speaker notes in italics.

---

**1 · Title.** 🛟 **Airbag — the autonomous release safety net for Cloud Run.**
"Detects a bad deploy hours after it shipped, rolls back to a **provably-good** revision, proves
recovery **on the signal that broke**, and fixes the root cause through real CI."
DevOps × AI Agent Hackathon 2026 · Jason Ye, WANG Pei. *Live URLs + QR to the video.*

**2 · Why this must be an AGENT — not a script or a rules engine.** *(the judges' question,
answered head-on)* A rules engine can threshold 5xx. It cannot:
- **diagnose** — fuse multiple signals with statistical confidence (a 200-but-5×-slower service
  trips no 5xx rule; our latency detector Wilson-gates it, debounced);
- **choose** — pick the *right* rollback target from serving history + live evidence (newest ≠
  good: a bad→bad deploy sequence defeats every "roll back to previous" script);
- **adapt the remedy** — a latency regression gets a rollback and *no* fix-PR; a code bug gets an
  RCA'd, **sandbox-verified, self-proving** patch + regression test through real CI;
- **explain** — a structured decision with reasoning, evidence, and an auditable thought-chain.
And the honest flip: where a rule IS stronger, we use one — **the LLM never executes; a
deterministic FSM gates every action.** The agent is the diagnosis; the machine is the hand.
*This hybrid is the whole design.*

**3 · The white space (nobody closes this loop).** The 4-axis table:
out-of-window · acts-on-prod · reversible/safe · proves-recovery.
Monitoring (Datadog/PagerDuty): alert only. Auto-rollback (Argo/Harness/LaunchDarkly): in-window
only. **Gemini Cloud Assist: officially advisory** — human required. Jules/Devin: code, offline.
**Airbag owns the intersection.** *The money slide.*

**4 · The loop (architecture).** alert (even hours later) → ADK SequentialAgent triage→decide
(Gemini calls the tools) → **deterministic gates** (Wilson verdict → confidence + target checks →
irreversibility guard → live causal target-probe) → rollback → **prove on the triggering signal**
→ fix-PR (sandbox-verified) → CI deploys keylessly (WIF) → verify → canary-restore → **CLOSED**.
*One transaction with a compensating action; every stage streamed + persisted.*

**5 · Why autonomy is defensible here (governance).**
- The action is a **traffic shift to a witnessed-healthy revision** — reversible, zero data risk;
  a deploy that *isn't* reversible **declares it** and Airbag refuses to cross the marker.
- **Gemini diagnoses, the FSM acts** — enforced by an AST test in CI (the action tier cannot even
  *import* the LLM).
- **Graduated autonomy** L0→L3 per service, durable approval gates, auto-demotion on a failed
  heal. *"A governed control loop, not a chatbot with run.admin."*

**6 · v4 — the rollback target is provably RIGHT (the marquee).**
"Roll back to the last good revision" — every tool says it; recency is how they all pick it; a
bad→bad deploy sequence defeats it. Airbag **witnesses** revisions serving healthily into a
per-service ledger and aims there — and the live probe re-checks the aim **on the incident's
axis**. *Tell the live story: Gemini once hallucinated '100% 5xx' on a latency incident and aimed
at the crashing revision — the probe vetoed it, and the FSM now re-aims to the witnessed-good
target. **The system caught the model.** That's the thesis in one incident.*

**7 · Demo (hero).** Dashboard mid-heal screenshot: thought-chain
(`ANALYZED FAIL → ADK → DECISION 🎯 witnessed-good → CAUSAL → ROLLBACK_APPLIED → VERIFYING →
MITIGATED`), error curve → 0, **✓ VERIFIED RESOLVED**, ⚡ alert→verified-recovery time.
*"Two scenarios, one click each, infinitely repeatable: the crash and the silent latency
regression a 5xx monitor can't see."*

**8 · Receipts (it's real, and measured).** Live on Cloud Run (`airbag-hack-260628`): autonomous
Cloud-Monitoring-alert heal, no human (~3 min) · fix PRs with **green real CI** · **keyless WIF
close** (deploy→verify→canary→CLOSED, zero humans, verified live) · **committed tamper-evident
proof bundles** of two real heals (recompute the sha256 yourself) · **Airbag-Bench**: a committed
incident-replay bench + CI ratchet — precision 67%→**100%**, false rollbacks →**0** on both
external-cause axes, plus a v4 **target-correctness** dimension · **249 tests**, 5 CI jobs incl.
a Firestore-emulator gate. *Screenshot: green Actions run + a scorecard table.*

**9 · Required stack, used for real.** **Gemini** — the decision, via ADK function-calling +
structured output (and RCA + patch + test authoring). **ADK 1.x** `SequentialAgent` on every heal
(pinned; CI-asserted). **Cloud Run** — patient *and* runtime (+ an egress-disabled Cloud Run Job
sandbox for LLM-authored tests). **Logging/Monitoring** — detection + proof. **Firestore +
Pub/Sub + Cloud Tasks** — durable state, multi-instance events, work queue. **Secret Manager +
least-priv IAM + WIF** — governance.

**10 · Impact / ask.** Out-of-window incidents are the 3am, multi-team, expensive kind. Airbag:
detect→mitigate→prove in **~1–3 minutes, zero humans**, on an action that's safe to automate —
and honest enough to escalate when it isn't. Cost: ~one small always-on instance. *"The release
airbag every Cloud Run service should ship with." Repo + live demo + video QR.*

---
**ROI anchors:** MTTR hours→minutes on the incidents that page nobody; zero data-migration risk
(pure traffic shift, guarded where it isn't); every claim reproducible (bench, proofs, live URLs).
