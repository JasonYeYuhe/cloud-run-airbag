# Pitch deck outline — Airbag (8–10 slides)

Story arc: *gap nobody fills → our loop → it's real & safe → required-stack → ask*. One idea per
slide; the dashboard screenshot is the hero. Speaker notes in italics.

---

**1 · Title.** 🛟 **Airbag — the autonomous release safety net for Cloud Run.**
"Detect a bad deploy hours after it shipped, roll it back, prove recovery, open the fix PR."
DevOps × AI Agent Hackathon 2026. Team: Jason Ye, WANG Pei. *Live URLs on the slide.*

**2 · The problem.** Bad deploys rot. The bug ships green and erupts **hours later**, out of the
canary window, when nobody's watching. **78% of orgs**: an incident with no timely alert.
*One stat, one line. Let it land.*

**3 · Why nobody fixes it (the white space).** The 4-quadrant table:
out-of-window · acts-on-prod · reversible · proves-recovery.
- Monitoring (Datadog/PagerDuty): alert only.
- Auto-rollback (Argo/Harness/LaunchDarkly): in-window only.
- Gemini Cloud Assist: **officially advisory** — human-in-the-loop required.
- Jules/Devin: write code offline.
- **Airbag: the intersection.** *This is the money slide — competitive moat.*

**4 · The loop (architecture).** The diagram:
alert → ADK triage→decide → deterministic gate → rollback → prove (error→0 + probe) → Gemini fix-PR
→ (roadmap) undo rollback. *Call out the design rule below.*

**5 · The governance rule.** **Deterministic state machine executes; Gemini only decides — through
ADK.** Confidence gate + known-good-revision check before any prod action. "Judges see a governed
control loop, not a chatbot with `run.admin`." *This is what makes autonomy defensible.*

**6 · Demo (hero).** Dashboard screenshot mid-heal: thought-chain on the left
(`ADK → DECISION ROLLBACK → ROLLBACK_APPLIED → VERIFYING → MITIGATED`), error curve dropping to 0,
gate **✓ VERIFIED RESOLVED**, traffic bar flipped to healthy. *"This is live, repeatable: Break →
Heal → Reset." Link to the 3-min video.*

**7 · It's real (proof).** Verified on live Cloud Run (`airbag-hack-260628`):
autonomous Cloud Monitoring alert → heal, no human (~3–4 min); Gemini fix-PR → **CI green** (PR #3);
3 backends, one codebase; `/demo/*` token-gated. *Screenshot the green CI check.*

**8 · Required stack, used for real.** Gemini (decision via ADK function-calling) · ADK 1.x
`SequentialAgent` runs every heal (pinned, CI-asserted) · Cloud Run = patient **and** runtime ·
Logging/Monitoring = proof · Secret Manager + least-priv IAM = governance.

**9 · Honest roadmap.** P1: close the transaction (auto-undo rollback once the fix verifies, via
CI → `/internal/complete-rollback`, with a compensating action). P2: Firestore durable state,
Cloud Tasks worker, canary restore, CI self-correction. *"The safe core stands alone today."*

**10 · Ask / impact.** MTTR for out-of-window incidents: hours of human toil → **~3 minutes, no
human**. Reversible by design. "The release airbag every Cloud Run service should ship with."
*Repo + live demo URLs; QR to the video.*

---
**ROI anchors:** out-of-window incidents = the expensive, 3am, multi-team kind; Airbag collapses
detect→mitigate→prove to minutes with zero human in the loop, and the mitigation carries zero
data-migration risk (pure traffic shift). Cost to run: ~1 always-on small instance.
