# Airbag v2 — vision, audit & roadmap

> Output of a deep multi-agent review: a forward-looking code audit across every surface + live
> competitor research (progressive-delivery, AI-SRE, and coding agents) → a plan for a big v2 update.
> Generated 2026-06-29. Companion to [NEXT_STEPS.md](NEXT_STEPS.md) (the day-to-day plan) and
> [ARCHITECTURE.md](ARCHITECTURE.md).

## Executive summary
Airbag v1 is a genuinely differentiated, production-grade entry: an **independent** monitor that
catches an **out-of-window** 5xx regression, lets **Gemini DECIDE while a deterministic state
machine ACTS**, rolls Cloud Run traffic back to the last-good revision, **proves recovery**
(error-rate→0 + business-path probe), opens a **CI-validated fix PR** (with self-correction), then
**reversibly undoes the rollback via a compensating 10/50/100 canary** — persisting a verifiable
incident Artifact.

Competitor research confirms this is real white space. Every progressive-delivery incumbent
(Argo Rollouts, Flagger, Harness, LaunchDarkly, Kayenta, Cloud Deploy, CodeDeploy) only analyzes
*during a rollout it itself orchestrates* — no out-of-window detection, no fix authoring. The whole
AI-SRE field (Cleric, Traversal, Resolve, Rootly, incident.io, Datadog Bits, Gemini Cloud Assist,
Azure SRE Agent) is **advisory or act-only-after-approval**, and almost none author a CI-validated
fix *and* complete a reversible, verified remediation loop.

**v2 thesis:** keep the moat — *independent out-of-window detection + reversible, compensating,
verified action + autonomous proven fix* — and borrow the field's hard-won rigor to close the gaps
that block a real product: **statistical decisioning** (not a static 5xx threshold), a **real
agentic RCA + multi-agent fix pipeline** (v1's weakest part), **graduated/scoped autonomy with
ChatOps approval**, a **durable multi-service core**, **cross-incident memory**, and **composition
with — not duplication of — Gemini Cloud Assist**.

## Current state — honest assessment
**Strengths (verified in code):**
- The **governance split is the moat and it's implemented well** — `gemini.decide`/`adk_brain.decide`
  only return a structured `IncidentDecision`; `state_machine._validate` gates on `confidence ≥ 0.7`
  **AND** a known-READY rollback target, escalating on failure rather than silently no-op'ing.
- The **reversible close-the-loop** is sophisticated: `complete_rollback` selects the true fix
  revision (CI-reported or newest post-rollback READY, clock-skew tolerant), canaries 10/50/100 with
  a per-stage gate that probes the **tagged candidate directly** (so the load balancer can't mask a
  bad fix at 10%), compensates to the safe revision on any failure, and caps undo attempts.
- A clean **8-method backend interface** across mock/local/gcp (a real abstraction, not a conditional
  chain); a **fail-safe fallback chain** (ADK→Gemini→heuristic); a verifiable Artifact + SSE glassbox.

**Top gaps blocking a real product:**
1. **The fix path is the weakest link.** `github_pr._gemini_fix` is a *single* Gemini call that
   rewrites *one hardcoded file* (`FIX_FILE=target-app/main.py`) from a hand-built string — it never
   reads the actual stack trace, failing logs, or the bad-vs-good revision diff.
2. **Fix verification leans on a human-pre-planted CI check** (`target-app/validate_fix.py`). A real
   incident has no such oracle — the agent must *synthesize a regression test*.
3. **`GEMINI_PATCH_MODEL` (gemini-2.5-pro) is configured but UNUSED** — `_gemini_fix` patches with
   flash. *(One-line, high-credibility fix.)*
4. **Detection is a static `error_rate ≥ 0.05`, 5xx-only** — no latency/saturation, no baseline/
   statistical test → false rollbacks + slow detection.
5. **State is in-process** (`pending.py`, `_seen_incidents`) forcing `--max/min-instances=1`; the CI
   watcher is a daemon thread lost on restart; webhooks use `BackgroundTasks`.
6. **Single `TARGET_SERVICE`**; **no auth/RBAC/multi-tenant** (only a shared token); **no ChatOps**;
   **no cross-incident memory / topology**; the **gcp backend logic isn't exercised in CI** (now
   partly addressed — see the review work; gcp LATEST resolution is tested).

> **v2 build status:** Theme 2 (the marquee **agentic self-proving fix loop**) has **landed** —
> `agent/autosre/fix_pipeline.py`: RCA from the real stack trace → stack-trace file discovery →
> patch + an agent-authored regression test (Gemini patch model) → **sandbox verification** (the
> test must fail on the bug and pass on the fix) → a PR that commits the fix *and* the test.
> Verified live against gemini-2.5-pro. Remaining themes (statistical decisioning, graduated
> autonomy + ChatOps, durable core, memory, Cloud Assist composition) are next.

## Strategic themes (the v2 shape)

### 1 · Decide smarter — statistical, baseline-aware, topology-aware triage
Replace the bare `error_rate ≥ 0.05` gate with real analysis. Borrow **LaunchDarkly's sequential
significance testing** and **Kayenta's Mann-Whitney distribution scoring**: compare the serving vs
last-good revision into a composite canary score; learn a per-service baseline; add latency/
saturation/trace signals; let Gemini classify business-criticality; ingest topology (Cloud Asset
Inventory + App Hub) for blast-radius; return **Pass / Fail-and-act / Inconclusive-and-pause**.
*Edge: incumbents do this only during their own rollout; Airbag does it on an independent
out-of-window monitor and then acts reversibly — the score becomes evidence in a real remediation.*

### 2 · Fix smarter — agentic RCA + multi-agent patch with self-proving tests  ⭐ marquee
v1's biggest upside. Every leading coding agent (Jules, Devin, Seer, Sweep, Copilot Autofix)
converges on **RCA → plan → multi-file patch → self-critique → sandbox-test-and-iterate BEFORE the
PR**, with an **agent-authored regression test**. Airbag's incident is the ideal bounded, machine-
verifiable task (it already owns the oracle: error-rate→0, canary-healthy, CI-green). Build:
- A real **ADK RCA stage** with tools over Cloud Logging (exception + stack trace), Cloud Trace, and
  the bad-vs-last-good revision diff → a structured `RootCause`.
- **Semantic code discovery** (stack-trace frames + ripgrep/embeddings) to replace the hardcoded
  `FIX_FILE` — fix the real culprit in any file.
- A **Plan→Patch→Critique→Test** multi-agent pipeline on the existing `SequentialAgent`.
- An **agent-authored regression test** committed *with* the fix (the PR self-proves; no pre-planted
  oracle), validated in a **firewalled pre-PR sandbox** (Cloud Run Job / gVisor) so it iterates in
  seconds, not multi-minute CI round-trips.
- Use the already-configured **`GEMINI_PATCH_MODEL`**; make the coder **pluggable** (delegate to
  Jules / Claude Code).
*Edge: incident.io/Datadog/Seer stop at proposing a PR; Airbag proves the fix on a real production
canary and reversibly completes the loop.*

### 3 · Act with graduated, scoped trust + ChatOps
The #1 objection to an agent touching prod. Borrow **Komodor's active guardrails / scoped autonomy
levels** and **Azure/Sedai Review→Autonomous run modes** with a *graduate-after-N-successes* ramp.
Enforce as **hard guardrails in the deterministic state machine**: per-service `L0 observe → L1
rollback-on-approval → L2 auto-rollback+gated recovery → L3 full-auto`; a pre-flight safety gate;
**Slack/Teams ChatOps** (per-incident thread, step-by-step Gemini reasoning, Approve/Deny → a new
`/internal/approve` transition); PagerDuty/Opsgenie fan-out; policy-as-code transitions + freeze
windows + blast-radius caps. *Edge: Airbag's actions stay deterministic, compensating, and verified
— "graduated autonomy you can actually trust."*

### 4 · Durable, multi-service, multi-instance core
Turn the brittle parts product-grade. **Firestore** for incidents + pending-reversions (transactional
CAS on the `_completing` lock) + webhook dedup with TTL → exactly-once, multi-instance safe.
**Cloud Tasks/Pub-Sub** for durable webhook work + a durable PR-watch worker (replacing the daemon
thread). **Multi-service** via the alert-label `service_name` (the webhook already reads it) + per-
service doc locking; drop `--max-instances=1`. Structured logging + **OpenTelemetry** across every
stage. Backend **contract tests + Cloud Run emulator** so the gcp path is covered in CI.
*Edge: Flagger/Argo need K8s + a service mesh; Airbag stays **serverless-native** (Cloud Run traffic
split, no mesh/sidecar/CRDs).*

### 5 · Learn and prove across incidents
A **Firestore + Vertex Vector Search** incident/fix memory keyed by `service+error-signature` →
repeat incidents skip to the known-good remediation at higher autonomy. **Calibrated confidence**
gating aggressiveness. A **RAG Context Connector** (point Airbag at the team's runbooks/postmortems
to ground RCA + reduce hallucinated fixes). **Cross-incident analytics** (MTTR, detection lag, fix-PR
merge/verified rate, rollback frequency). A **postmortem workflow** generated from the Artifact.
*Edge: advisory tools remember to summarize; Airbag remembers to act faster + more autonomously next
time, with a verifiable proof trail.*

### 6 · Compose with the GCP-native stack (don't reinvent it)
**Trigger/consume a Gemini Cloud Assist Investigation** for richer RCA + topology Observations, then
ACT on the result in the state machine. Express transitions as **Cloud Deploy-style policy-as-code**
advance/rollback/repair rules. Expose Airbag's actions over an **MCP server** so other agents/IDEs
can invoke "safe rollback" as a tool. Ship an **eval harness** (replayable recorded incident
scenarios) to quantify rollback-correctness + recovery-success. *Judge-friendly one-liner: "Cloud
Assist diagnoses; Airbag safely remediates, verifies, and reverses."*

## The "big update" — headline v2 features
| Feature | Impact / Effort | Borrows from |
|---|---|---|
| **Agentic RCA + multi-agent self-proving fix pipeline** ⭐ | high / high | Sentry Seer (RCA-as-context), Jules (Plan/Patch/Critique/Test), Sweep/Devin (pre-PR sandbox + agent-authored test) |
| **Statistical decision engine** (sequential + distribution scoring + learned baseline) | high / medium | LaunchDarkly Guarded Releases, Kayenta (Mann-Whitney), Harness ML baselining |
| **Graduated autonomy + Slack ChatOps approval** | high / medium | Komodor (scoped autonomy), Azure/Sedai (run modes + trust ramp), Rootly/incident.io (Slack approve) |
| **Durable multi-service core** (Firestore + Cloud Tasks) | high / high | v1's own roadmap + the durability pattern every mature tool uses |
| **Cross-incident memory + RAG runbook grounding** | medium / medium | Cleric (operational memory + confidence-gated silence), PagerDuty/Azure (reusable playbooks) |
| **Compose with Gemini Cloud Assist + ship an MCP server + eval harness** | high / medium | Gemini Cloud Assist (RCA+topology), Cloud Deploy (policy-as-code), New Relic/incident.io (MCP) |

## Roadmap
**Phase 0 — Submission hardening (now → 2026-07-10).** *Largely complete this cycle:* secured
`/demo/*`, unified the fault, ADK genuinely runs, dual-path + canary + Artifact live, repo hygiene,
102 tests + lint/CI. **Outstanding quick win:** use `GEMINI_PATCH_MODEL` (gemini-2.5-pro) for the
patch step (it's configured but unused); record the ≤3-min video; flip the repo public.

**Phase 1 — Trustworthy core (weeks 1–4).** Firestore (transactional CAS) + Cloud Tasks (webhooks +
PR watcher), drop `--max-instances=1`; multi-service via alert label + per-service locking; backend
contract + emulator tests in CI; the **statistical decision engine** behind the existing `_validate`
gate; structured logging + OpenTelemetry spans.

**Phase 2 — The marquee v2 fix loop (weeks 4–9).** Agentic RCA over logs/trace + revision diff →
`RootCause`; semantic code discovery (kill the hardcoded `FIX_FILE`); Plan→Patch→Critique→Test on
ADK; agent-authored regression test + firewalled pre-PR sandbox; pluggable coder; compose with Cloud
Assist; eval harness publishing rollback-correctness / recovery-success.

**Phase 3 — Trust surface & autonomy (weeks 9–14).** Graduated autonomy + run modes in the state
machine + pre-flight gate + graduate-after-N ramp; Slack/Teams ChatOps + Approve/Deny →
`/internal/approve`; PagerDuty/Opsgenie fan-out; cross-incident memory (Vertex Vector Search) + RAG
runbooks; policy-as-code; postmortems + analytics dashboard.

**Phase 4 — Product & reach (weeks 14+).** Auth/RBAC + multi-tenant (if SaaS) + audit log; MCP
server; Terraform module / one-click install; multi-region; more backend plugins (ECS/Lambda/K8s)
behind the 8-method interface; cost controls (per-incident Gemini budgeting, prompt caching, model
fallback).

## Positioning (how v2 stays differentiated)
Airbag v2 is **"the autonomous, reversible release safety net for Cloud Run — out-of-window
detection, deterministic compensating action, and a proven, self-tested fix."** It **composes** with
the GCP stack rather than competing: Gemini Cloud Assist and Cloud Deploy diagnose and deploy within
a window they control; **Airbag is the independent monitor that catches the regression hours after
promotion, ACTS reversibly, and proves it.** vs Argo/Flagger/Cloud Deploy: they only analyze during a
rollout they orchestrate, and need K8s + a mesh — Airbag is serverless-native. vs the AI-SRE field:
they're advisory or propose-then-approve; only Resolve/Komodor/Azure truly act, via LLM/permission-
driven execution — Airbag keeps **Gemini DECIDES, a deterministic state machine ACTS** with verified,
compensating, reversible canary recovery. **The defensible one-liner: the only system that detects
out-of-window, rolls back safely, ships a CI-validated self-proving fix, verifies it on a real
production canary, and can prove AND reverse every action.**

## Risks & watch-items
- **Agent-authored fixes are blind to production realities** (a 2025 study: critical vulns rose 37%
  after five AI refinement rounds) → keep the strict DECIDE-vs-ACT split; require the fix to pass an
  agent-authored regression test in a sandbox **and** a real production canary before the rollback is
  undone; the patch agent must never touch prod traffic.
- **Executing model-written code** adds a code-execution attack surface → the pre-PR sandbox must be
  firewalled / least-privilege (gVisor / Cloud Run Job).
- **Scope creep** — six themes; don't start v2 before Phase 0 is locked (the review's code-freeze
  warning). Durability + the new fix loop are post-submission.
- **The Firestore/Cloud Tasks/multi-service refactor touches the exactly-once + compensation logic
  that makes v1 safe** → land the contract-test + emulator harness *before* the durability migration.
- **Statistical detection needs traffic** — on low-traffic services tests will be Inconclusive often,
  so the three-state "pause for human" path + the synthetic-probe fallback must stay first-class.
- **Composing with Cloud Assist (preview)** adds a dependency on a moving Google surface → keep
  Airbag's RCA self-sufficient as a fallback (enrichment, not hard dependency).
- **Cost + blast radius rise with autonomy + multi-service** → per-incident Gemini budgeting, blast-
  radius caps, freeze windows, and the autonomy guardrails ship *alongside* autonomy, not after.
- **Operational:** the fine-grained GitHub token expires **2026-07-28**; `--min-instances=1` is an
  ongoing cost.

---
*Appendix: competitor scan (2025–2026).* **Progressive delivery:** Argo Rollouts (the
AnalysisTemplate/metric-provider data model to adopt), Flagger (webhook lifecycle hooks), Harness
(AI/ML continuous verification — closest philosophical peer), LaunchDarkly (sequential testing),
Kayenta (Mann-Whitney distribution scoring), Sedai (graduated autonomy). **AI-SRE:** Cleric
(confidence-gated silence + operational memory), Traversal (causal dependency-graph RCA), Resolve
(acts — closest to Airbag's thesis), Rootly/incident.io (advisory + Slack-gated, multi-agent search,
role-aware ChatOps), Komodor/Klaudia (scoped-autonomy guardrails), Datadog Bits / Gemini Cloud Assist
/ Azure SRE Agent. **Coding agents:** Jules (multi-agent plan/patch/test), Devin (bounded verifiable
tasks), Copilot Autofix (CodeQL-grounded), Sentry Seer (RCA-first, fixability score), Sweep
(embeddings code discovery + sandbox test loop). Full per-item findings in the workflow transcript.
