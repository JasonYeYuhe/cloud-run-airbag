# Demo video script — Airbag (v4)

Target length **3:30–4:30** (requirement: 3–5 min; real GitHub Actions / Cloud Run / logs must
appear on screen). Screens to have open: ① the **glassbox dashboard** (operator link so the demo
token is pre-filled), ② **GCP Cloud Run console** (`airbag-target` revisions), ③ a **GitHub tab**
(the open `airbag/fix` PR + Actions), ④ one **incident report** tab (any recent mitigated
incident, `/incidents/{id}/report`). Baseline ready: `./scripts/gcp-demo-setup.sh` run → target
HEALTHY and **newest** (00024-style), slow + bug revisions staged at 0%. Record 1080p+.

---

### 0:00–0:30 — The gap (hook)
> "Monitoring tools only *alert*. Auto-rollback tools like Argo or LaunchDarkly only work *inside*
> the deploy window. Google's own Gemini Cloud Assist is, by its own docs, *advisory* — a human
> still has to act. But most bad deploys don't fail at deploy time — they rot, and start erroring
> **hours later** when nobody's watching."

*(On screen: the comparison table from SUBMISSION.md §2.)*

> "Airbag is the missing piece: an autonomous safety net on Cloud Run that catches the
> out-of-window failure, rolls it back to a **provably-good** revision, **proves** recovery on the
> signal that broke, and opens the fix PR through real CI."

### 0:30–0:50 — The setup
*(Cloud Run console: `airbag-target`, three revisions — healthy serving 100% (newest), a `slow`
revision and a `bug` revision at 0%. Then the dashboard: green, idle.)*
> "A live Cloud Run service. Two bad revisions sit in its history — one crashes with a real
> `KeyError`, one silently became 5× slower. Both shipped 'green'. Airbag has been **watching this
> service serve healthily** — remember that, it matters in a minute."

### 0:50–1:45 — Scenario A: the crash (💣 Break → Heal)
*(Click **💣 Break**. Target tab: `/api/orders` → HTTP 500. Dashboard error curve spikes.)*
> "Production traffic is now on the crashing revision — users are getting 500s."

*(Click **🚑 Heal**. Narrate the thought-chain as stages stream.)*
> "Watch it think. Detection is **statistical** — a Wilson confidence interval, 20 of 20 requests
> failing, not a naive threshold. The **ADK SequentialAgent** has Gemini call the Cloud Run and
> Logging tools itself and return a *structured* decision."

`ANALYZED: FAIL → ADK → DECISION: ROLLBACK → CAUSAL → ROLLBACK_APPLIED → VERIFYING → MITIGATED`
> "Before spending the rollback, it **live-probes the rollback target** — if the target were also
> broken, rolling back would be futile and it escalates instead. Target's clean → traffic shifts →
> and it **proves** recovery: error-rate zero in Cloud Logging *and* a live business-path probe.
> **VERIFIED RESOLVED. No human.**"

*(GitHub tab: the fix PR + green Actions run.)*
> "Then the slow path: Gemini reads the real stack trace, patches the bug, **authors a regression
> test that's sandbox-verified**, and opens a PR — real GitHub Actions, green. Rolled back *and*
> root-cause fixed."

### 1:45–2:45 — Scenario B: the silent killer (🐢 latency Break → Heal)
*(Click **↺ Reset** if needed, then **🐢 Break latency**. Target tab: `/api/orders` still 200 —
but visibly slow. Optionally curl with `time`.)*
> "Now the harder one. This revision doesn't error — every request is a **200**. It's just 5×
> slower than the SLO. A 5xx monitor sees **nothing**. This is the incident that pages nobody."

*(Click **🚑 Heal**. Narrate the ANALYZED line.)*
> "The 5xx detector honestly reports *inconclusive* — but the **latency detector** fails it: four
> out of four windows confidently over the SLO. One incident, more than one signal."

*(Point at the DECISION card — the 🎯 target line.)*
> "And here's v4. Look at the target: **witnessed-good, from the serving-history ledger**. Airbag
> doesn't roll back to the *newest* revision and hope — newest is how you land on a second bad
> deploy. It rolls back to a revision it has **watched serving healthily** — and the live probe
> re-checks it *on the latency axis*: a 200-but-slow target can't fix a latency incident, so it
> would be vetoed."

`ROLLBACK_APPLIED → VERIFYING (latency_ms ≪ SLO) → MITIGATED`
> "Recovery is proven **on the signal that broke** — latency back under the SLO, not just 'no
> errors'. And notice: **no fix PR this time** — there's no code bug to fix; the rollback *is* the
> remedy. The agent doesn't fabricate work."

### 2:45–3:30 — The receipts (report, proof, bench)
*(Open the incident report tab for the latency incident.)*
> "Every run persists a report a human — or a judge — can audit: the per-detector verdicts, the
> causal probe's counts on both axes, **which revision it aimed at and why**, and a
> **tamper-evident proof bundle** — a sha256 digest you can recompute yourself. Two of these live
> bundles are committed in the repo."

*(Show docs/AIRBAG_BENCH.md scorecard table briefly.)*
> "And we don't grade our own homework with vibes: a committed incident-replay **bench** scores
> every decision — including a v4 dimension for whether the rollback was aimed at the *right
> revision*. The one deliberate miss you see is the control: cold-start recency aiming at a
> landmine. The ledger heals it. During our own live testing, Gemini once hallucinated '100% 5xx'
> on this latency incident and aimed at the crashing revision — the probe vetoed it, and the FSM
> now **re-aims** to the witnessed-good target. The system caught the model."

### 3:30–4:15 — Why autonomy is defensible here + close
> "Why dare to be autonomous when Google keeps Cloud Assist advisory? Three reasons. The action is
> a **traffic shift to a witnessed-healthy revision** — reversible, no data risk; a deploy that
> *isn't* reversible can declare it, and Airbag refuses to roll back across it. The decision-maker
> never touches prod — **Gemini diagnoses, a deterministic state machine acts**, and an AST test
> enforces that boundary in CI. And autonomy is **graduated** — per-service trust levels from
> observe-only to full, with durable human approval gates."

*(Optional 5s: the complete-rollback GitHub Actions run.)*
> "When the fix merges, CI deploys it **keylessly** via Workload Identity Federation, Airbag
> verifies it, canary-restores traffic 10-50-100, and closes the incident. Detect, roll back,
> prove, fix, verify, close — one transaction, zero humans. That's **Airbag** — the release
> airbag for Cloud Run."

*(Click **↺ Reset** — back to green.)*

---
**B-roll / fallbacks:** if a live heal is slow, cut to the offline dashboard self-play or a
pre-recorded run; keep the GitHub PR + Actions tabs pre-opened; the incident-report tab can be
any recent real incident. Spoken words ≈ 640 (~4:10 at a normal pace). Must-appear-on-screen
checklist: ☐ Cloud Run console (revisions + traffic) ☐ real GitHub Actions run ☐ Cloud Logging
or the dashboard error curve ☐ the 🎯 ledger target line ☐ the incident report + proof digest.
