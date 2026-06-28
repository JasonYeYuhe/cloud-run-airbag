# 3-minute demo video script — Airbag

Target length **≤ 3:00**. Two screens: the **glassbox dashboard** (agent URL) and a **GCP /
GitHub console** tab. Record at 1080p+. Have the baseline ready (`./scripts/gcp-demo-setup.sh`
run; target healthy). Open the dashboard via the operator link so the demo token is pre-filled.

---

### 0:00–0:25 — The gap (hook)
> "Monitoring tools only *alert*. Auto-rollback tools like Argo or LaunchDarkly only work *inside*
> the deploy window. Google's own Gemini Cloud Assist is, by its own docs, *advisory* — a human
> still has to act. But most bad deploys don't fail at deploy time — they rot, and start erroring
> **hours later** when nobody's watching. **78% of orgs** have had an incident with no alert in time."

*(On screen: the comparison table from the deck / SUBMISSION.md §2.)*

> "Airbag is the piece nobody built: an autonomous safety net that catches an out-of-window bad
> deploy, **rolls it back**, **proves** recovery, and opens the fix PR — on real Cloud Run."

### 0:25–0:45 — The setup
*(Dashboard: target green, gate PENDING/idle. Show the GCP Cloud Run console: `airbag-target`,
two revisions — healthy serving 100%, a bad revision `FAULT_MODE=bug` at 0%.)*
> "Here's a live Cloud Run service. A bad revision is sitting in history — it has a real bug, a
> `KeyError` on the orders endpoint. Imagine it shipped this morning and the canary passed."

### 0:45–1:05 — Break (the incident, out of window)
*(Click **💣 Break**.)*
> "I shift production traffic to that bad revision — a deploy that looks fine but isn't."

*(Dashboard: error-rate curve spikes to 100%, gate PENDING. Switch to the target tab: `/api/orders`
returns HTTP 500.)*
> "Users are now getting 500s. In a real incident this is the moment a pager *should* fire — and
> often doesn't."

### 1:05–2:05 — Heal (the autonomous loop — the core)
*(Click **🚑 Heal**. Let the thought-chain stream; narrate the stages as they appear.)*
> "Watch the agent think. It's not a script — the decision runs through the **ADK SequentialAgent**:
> Gemini *calls the Cloud Run and Logging tools itself* to triage…"

`TRIAGED → ADK (triage→decide) → DECISION: ROLLBACK (conf ~0.9, source gemini-adk)`
> "…and returns a *structured* decision: roll back, to this specific healthy revision, with its
> reasoning and evidence. Now the key design point — **Gemini only decides.** A deterministic state
> machine checks the confidence and that the target really is a known-good revision, then acts."

`ROLLBACK_APPLIED → VERIFYING… → MITIGATED`
> "Traffic shifts back to the healthy revision. And it doesn't just *say* it's fixed — it **proves**
> it: error-rate back to zero in Cloud Logging **and** a live probe of the business path. Gate turns
> green: **VERIFIED RESOLVED.** No human touched this."

*(Switch to the target tab: `/api/orders` is 200 again. Optionally show the Cloud Run console:
traffic back on the healthy revision.)*

### 2:05–2:35 — The fix PR (root cause, not just a band-aid)
*(Thought-chain shows `FIX_PR`. Switch to the GitHub tab → the open PR + green CI check.)*
> "Rolling back stopped the bleeding. But the bug is still in the code — so the agent's slow path
> has **Gemini open a real pull request** that fixes that exact `KeyError`, and GitHub Actions CI
> goes **green**. Rolled back *and* root-cause fixed — the same bug, both halves of one incident."

### 2:35–3:00 — Why it's safe to be autonomous + close
> "Why dare to make this autonomous when Google keeps Cloud Assist advisory? Because the action is
> a **traffic shift to an already-healthy revision** — instantly reversible, zero data-migration
> risk. That's the whole thesis: a reversible action, a deterministic gate, and a binary proof of
> recovery is exactly where autonomy is *defensible*."

*(Click **↺ Reset** — back to baseline.)*
> "Gemini decides, ADK orchestrates, Cloud Run is both patient and runtime, and it's all repeatable.
> That's Airbag — the release airbag for Cloud Run."

---
**B-roll / fallbacks:** if the live heal is slow, cut to the offline dashboard self-play (loads with
no server) or a pre-recorded run. Keep the GitHub PR tab pre-opened. Total spoken words ≈ 430 (≈ 2:50).
