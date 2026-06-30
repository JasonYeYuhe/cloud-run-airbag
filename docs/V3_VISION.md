# Airbag v3 — Vision, Audit & Development Plan

> Lead-architect synthesis for the next dev cycle: a code-grounded audit of every surface + verified competitor research → the single biggest v3 bet, the fixes that precede it, and a phased plan a fresh dev session can execute. Companion to `docs/V2_VISION.md`, `docs/ARCHITECTURE.md`, `docs/NEXT_STEPS.md`. Save as `docs/V3_VISION.md`.

---

## 1. Executive summary

**Where Airbag is.** v2 shipped the moat and it holds up in code: an *independent, out-of-window* monitor where **Gemini diagnoses and a deterministic FSM acts** (`adk_brain.decide`/`gemini.decide` only return an `IncidentDecision`; `state_machine._validate` and the action layer never import the LLM — guarded by `test_architecture_invariant.py`). The reversible loop is genuinely sophisticated: Wilson-CI statistical rollback gate (`analyzer.analyze`), durable Firestore state, graduated autonomy L0–L3 with a durable approval gate (`autonomy.py`), learned per-service baseline + cross-incident memory (`memory.py`), a self-proving fix pipeline that sandbox-verifies an agent-authored regression test (`fix_pipeline.py`), keyless WIF close, and a 10/50/100 canary-restore that probes the tagged candidate directly (`state_machine.complete_rollback`, lines 356–386). 102 tests. This is ahead of where most of the AI-SRE field actually ships.

**The honest gap.** The hyperscalers (Azure SRE Agent GA, AWS DevOps Agent) have now validated Airbag's exact "alert → verified recovery" arc — so "we do detect→act→prove→fix" is no longer differentiating *on its own*. And the category's frontier moved to two things Airbag does not yet have: **causal grounding** (Causely, Traversal — "is the localized cause, not a downstream symptom") and **graded-confidence, verified diagnosis** (Cleric, Resolve.ai's separate verifier model). Meanwhile Airbag's detection is still **single-signal** — the only trigger is a binomial 5xx proportion (`analyzer.analyze(errs, total, ...)`, fed by `tools.sample_business_path`). A latency regression, saturation, or quiet SLO-budget burn — the canonical *out-of-window* bad deploy — passes straight through. That is the contradiction at the heart of the pitch: the moat is "we catch what canary-window tools miss," but today Airbag mostly catches crashes.

**The single biggest v3 bet:**

> **Make Airbag *causally certain* before it acts, across *more than one signal*.** Concretely: (1) a **multi-signal detection engine** (latency p99 + saturation + multi-window SLO burn-rate, fused into the same FAIL/PASS/INCONCLUSIVE verdict the Wilson gate already produces), and (2) a **causal pre-check + graded-confidence verifier** that binds the symptom to *the specific revision that introduced it* (a durable deploy ledger) and forces approval when confidence is low — wiring confidence directly into the L0–L3 gate.

This is one coherent bet — "detect more, and prove causality before the FSM fires" — and it is the only set of features that simultaneously (a) makes the out-of-window moat real, (b) closes the gap to Causely/Traversal/Cleric, and (c) preserves the thesis verbatim: the verifier and causal check live in the **diagnosis tier**; the action layer stays LLM-free.

---

## §0. Gemini 3.1 Pro review (incorporated)

Gemini 3.1 Pro reviewed this plan — verdict **CHANGES-NEEDED**, "the bet is brilliant." Three
adjustments, all folded into the sections below:
1. **The bet is right; the sequencing was backwards.** **Airbag-Bench moves to Phase 0** — you can't
   safely tune multi-signal fusion / the verifier without a baseline. Build the measuring stick
   first, baseline the v2 5xx-only impl, then TDD Phases 1–2 against it.
2. **The `fix_pipeline` sandbox can't be a v4 defer.** Un-sandboxed LLM-generated code in the prod
   agent directly contradicts the "guarded action layer" moat — **harden it (egress-disabled Cloud
   Run Job) in Phase 0/1**, not later.
3. **Multi-signal needs anti-flapping.** Fusing latency/saturation/burn-rate risks false positives
   from momentary spikes — **Feature 1 must include debounce/hysteresis** (a signal persists N
   windows before it can trigger), not just per-detector confidence bounds.

---

## 2. Problems / improvements to fix first

These are pre-work. The first three are correctness/security bugs that must land before any v3 feature touches `state_machine.py` or the schema. Cited to file:line.

### P0 — must fix before any decision-layer work

1. **`OPEN_FIX_PR` decision silently becomes a no-op `DONE`.** `schemas.py:10` lets the model emit `OPEN_FIX_PR`, but `state_machine._heal_body` only branches on `ROLLBACK`/`ESCALATE`; everything else hits `emit("DONE", "no rollback needed")` + `memory.observe_healthy(...)` (lines 102–114). So a plausible "don't roll back, just open a fix PR" decision records a noop, **folds a healthy sample into the learned baseline**, and ships nothing. `_validate` early-returns for non-ROLLBACK (line 441) so it's never normalized. **Fix: drop `OPEN_FIX_PR` from the `Literal` enum** — the fix-PR is a downstream step of ROLLBACK (`_mitigate`/`_open_fix_pr`), not a top-level action. Lower-risk than mapping it, and matches control flow. *(verified; small)*

2. **Webhook/demo tokens travel in URL query strings.** `infra/alert-setup.sh:21` wires the Cloud Monitoring channel as `...?token=${TOKEN}`, persisting the secret in channel config and emitting it in every Cloud Run/LB request log + GCP audit log. The `?token=` fallback is also read on four gates (`app.py:129,234,262,352`). The header path already exists for all of them. **Fix: switch `alert-setup.sh` to send the token via an auth header; deprecate `?token=` on the machine-to-machine endpoints**, keeping it only for the human one-click deep-link (already stripped client-side at `dashboard.html:318`). *(verified; small)*

3. **`/demo/run` background task has no error handling.** `demo_run` breaks the target then schedules `break_then_heal()` (`app.py:201–207`), which calls `run_self_heal` — and that **re-raises on transient failure by design** (`state_machine.py:46–48`). Starlette's `BackgroundTasks` swallows it: no event, no traffic restoration, dashboard frozen on `FAULT_INJECTED`. On the marquee demo path a flake looks like total failure. **Fix: wrap `break_then_heal` in try/except that emits `ESCALATED` and calls `reset_target`**, mirroring `_break` (`app.py:151–154`). *(verified; small)* — do this before any demo at a judging session.

### P1 — correctness under the multi-instance config you already ship

4. **Cloud Run traffic mutation is a non-atomic read-modify-write with no etag.** `gcp._set_traffic` does `_get_service()` then a *separate* `ServicesClient().update_service(update_mask=["traffic"])` with no optimistic concurrency (`backends/gcp.py:150–155`). Production runs `--max-instances 3` with `AIRBAG_QUEUE` left at `inproc`, so two instances can each be mid-traffic-write on the same service; the heal lease (`claim_heal`) does *not* serialize a heal against a `complete_rollback` (separate lease, `pending.try_begin_complete`). Last-writer-wins can strand a partial canary or undo a fresh rollback. **Fix: thread `svc.etag` through `update_service` and retry on the aborted/precondition error, or take a per-service traffic lease around every `set_traffic` call.** *(medium)* — this is the one *latent prod-correctness* bug; do it in Phase 0 because the multi-signal canary will add more `set_traffic` callers.

5. **`_gemini_fix` hardcodes the demo bug's oracle into the retry prompt.** On CI-failure retry, `github_pr.py:201–203` injects the literal `"The check runs total_revenue(ORDERS, buggy=True) and it must NOT raise"`. That is the planted demo bug's exact assertion baked into the agent — it contradicts the self-proving thesis and means CI self-correction only works for *this one bug*. **Fix: feed the real `summary`/CI-failure text generically** (the pipeline path already does this via `prior_failure`, `fix_pipeline.py:122`). *(small)* — must fix before claiming "works on any service."

### P2 — robustness / coverage (do alongside features)

6. **SSE replays the whole event log on reconnect; client de-dupes nothing.** `/events` always sets `idx = 0` and ignores `Last-Event-ID` (`app.py:94–95`); the dashboard uses a bare `EventSource` that appends without dedup. Cloud Run idle-timeout reconnects re-render the entire timeline. `events.get_since` already uses absolute `_offset` indices, so resumability is one cursor away. **Fix: honor `Last-Event-ID`/`?since=` and dedupe by `_eid`.** *(medium)* — fix *before* the fleet console (Feature 5) multiplies the event volume.

7. **`events._seen` dedup set is cleared wholesale at the bound** (`events.py:42–43`): right after `_seen.clear()`, a Pub/Sub redelivery re-appends a duplicate row. **Fix: bounded FIFO (`OrderedDict`/`deque`) so eviction is per-oldest.** *(small)*

8. **Firestore `order_by` queries need field presence that isn't provisioned or tested.** `state_store.list_recent` orders by `updated_at`/`rollback_at_epoch`/`created_at` (`state_store.py:109–118`); a doc missing the field is *silently omitted*. `conftest.py` pins `AIRBAG_STATE=memory`, so the Firestore paths are unexercised by the 102 tests. **Fix: add `firestore.indexes.json` (or document the auto-index assumption) + one Firestore-emulator integration test.** *(medium)* — required before "multi-service fleet" is credible.

9. **Architecture-invariant test uses fragile substring matching.** `test_architecture_invariant.py:10–13` checks `"import gemini" in src` and a brittle `from . import` split — it would **miss** `from .gemini import _client`, `from autosre import gemini`, aliases, or `import google.generativeai`. This guards the load-bearing thesis. **Fix: parse the AST (`ast.walk` for Import/ImportFrom) and assert no action-layer module imports `gemini`/`google.genai`/`google.generativeai`/`adk`.** *(small)* — do this **first in Phase 0**, because v3 adds a verifier + causal layer and you need the invariant guard to be real before you grow the diagnosis tier.

10. **Backend probe defaults to `/healthz`, not the business path.** `local.py:70`/`mock.py:51` default `path="/healthz"`; `tools.synthetic_probe` overrides with `PROBE_PATH` (`tools.py`) so the heal loop is safe today — but any future caller bypassing `tools.py` would probe `/healthz`, which stays 200 during a fault (`target-app/main.py:44–46`) → false "recovered." **Fix: default the backend probe to `config.PROBE_PATH`.** *(small)*

11. **`complete-rollback` CI gate fails forever on terminal `manual_intervention`.** `app.py:252–254` maps both `compensated` (retryable) and `manual_intervention` (terminal, `state_machine.py:330–337`) to HTTP 422; `complete-rollback.yml:57` `curl -fsS` goes red on both. The rich `terminal` flag is discarded. **Fix: return 200 + a "needs human" body for terminal `manual_intervention`** so CI-red means "a retry could help." *(low; small)*

---

## 3. What to learn from competitors (verified) → the specific Airbag adaptation

| Competitor (verified) | What they ship | Airbag v3 adaptation (preserves "FSM acts, LLM advises") |
|---|---|---|
| **Cleric + Resolve.ai** | Multi-hypothesis parallel diagnosis with graded confidence; Resolve.ai builds a *separate verifier model* for investigation quality. | `adk_brain.decide` emits **N ranked hypotheses each with explicit confidence**; add a lightweight Gemini **verifier pass** that adversarially scores evidence-vs-hypothesis *before* `_validate` lets the FSM act. **Tie confidence to the L0–L3 gate**: low confidence forces approval. Verifier lives in the diagnosis tier; the FSM stays LLM-free. |
| **Causely** | Codebook + Causality Graph: distinguish *localized cause* from *downstream manifestation* (100% on their own vendor benchmark — self-reported, treat the number accordingly). | **Causal pre-check before the FSM commits to rollback**: correlate 5xx/latency onset against the revision-deploy timestamp *and* dependency/upstream signals, so Airbag confirms the revision is the cause, not a coincident DB/quota incident — and doesn't waste its one reversible action. |
| **Traversal** ($48M, Amex, **32% MTTR** — *correct the "40%" figure*) | Persistent "Production World Model" + Causal Search Engine that walks topology to bind symptom→root cause. | **Durable per-service deploy ledger + topology snapshot in Firestore** (revisions, config diffs, traffic splits, dependency edges). When a 5xx surfaces *hours later*, Airbag walks the ledger to bind it to the specific revision — turning "out-of-window detect" from a time-correlation heuristic into a **structured causal lookup**. |
| **Lightrun** (Industry-first AI SRE, Feb 2026; *44% of AI-SRE investigations fail for lack of execution-level data at the right moment*) | Runtime-validated fixes + on-demand "ground-truth" capture; a Runtime-Aware PR Verifier. | **Elevate the existing sandbox to capture concrete ground-truth**: snapshot the *actual failing request/response* from the bad revision *before* rollback and embed it in the RCA PR as proof the regression test reproduces the real failure — not a guessed one. |
| **Datadog Bits + Rootly** | Graduated autonomy as a *learning loop* — "learns from every approved change to expand what it can resolve." | **Close the autonomy loop**: when an operator approves a rollback for service X / signature Y (`autonomy.save_approval` → `apply_approval`), record it so the next identical signature auto-promotes toward higher autonomy. Turns the static toggle into a self-expanding moat; FSM unchanged. |
| **Komodor Klaudia + PagerDuty** | Agent-to-agent interop is consolidating (Klaudia universal multi-agent via MCP/OpenAPI; PagerDuty native cloud-agent comms). | Airbag already ships an MCP server — **position it as the "reversible-action + proof" agent other SRE agents call.** Today `mcp_remote.py` exposes only read/trigger/approve/set-autonomy. **Add first-class MCP tools `rollback_to_last_good`, `prove_recovery`, `open_self_proving_fix_pr` with structured proof outputs** other agents can consume. Diagnosis agents are crowded; a deterministic, auditable reversible-action tool is rare — *pressure-test this niche claim, since Komodor already does remediation*. |
| **Azure SRE Agent + AWS DevOps Agent** | Validated the loop; both keep humans in the loop for complex failures and frame rollback as a *suggestion*. | **Lean into the wedge: "we don't suggest rollback — we execute-and-prove it, keylessly."** Make **"Alert-to-Verified-Recovery time"** (not MTTR) the headline metric on the dashboard and PR, since Airbag *empirically proves* recovery (5xx→0 + probe). |
| **incident.io** | Auto-drafted post-mortems with timeline; auditability is table stakes (Gartner). | At keyless-close, **emit one signed "incident proof bundle"** stitching detection evidence + chosen hypothesis/confidence + the FSM transition log + recovery proof + regression-test result + WIF-deployed fix — a tamper-evident timeline. Low cost; Airbag's proof is *empirical*, not LLM-narrated. |

---

## 4. v3 feature roadmap — the big update

Four marquee features. Features 1–3 are the core bet (detect more / prove causally / verify confidence); 4 makes it legible and extensible. Effort is relative to a hackathon cadence.

### Feature 1 — Multi-signal detection engine (latency p99 + saturation + SLO burn-rate)
- **What.** A pluggable `SignalProvider` abstraction emitting a normalized `DegradationVerdict` from detectors run in parallel: (a) **latency regression** — serving vs last-good revision `request_latencies` (two-sample / CUSUM, not binomial); (b) **saturation** — CPU/memory + Cloud Run instance/concurrency pressure from `run.googleapis.com` metrics; (c) **multi-window multi-burn-rate SLO** (fast 1h/5m + slow 6h/30m, per the Google SRE workbook). A weighted/strongest-signal fusion turns N verdicts into one **FAIL/PASS/INCONCLUSIVE**. **Fusion MUST include debounce/hysteresis** (per the Gemini review): a signal persists N consecutive windows before it can trigger, so a momentary CPU/latency spike can't cause a false rollback — anti-flapping on top of each detector's own confidence bound.
- **Why.** The single highest-leverage leap: it moves Airbag from "catches crashes" to "catches regressions" — exactly where the out-of-window moat lives. A latency regression hours after deploy is the canonical thing every canary-window competitor misses.
- **Builds on.** Generalizes `analyzer.py` (the Wilson verdict becomes *one detector of several*) and `tools.py` + `backends/gcp.py` `query_error_rate`/`sample_business_path` (add latency/saturation collectors). **The FAIL/PASS/INCONCLUSIVE contract consumed by `state_machine._validate` (lines 446–455) is unchanged** — the gate wiring is reused verbatim, so the FSM doesn't change.
- **Effort:** large.

### Feature 2 — Causal grounding: deploy ledger + causal pre-check (Traversal/Causely)
- **What.** A durable per-service **deploy ledger** in Firestore (revision SHAs, config diffs, traffic splits, declared/discovered dependency edges). Before the FSM commits to rollback, a **causal pre-check** binds the symptom to the revision that introduced it (temporal + topological) and screens out coincident dependency/quota incidents where rollback would be the wrong action.
- **Why.** Closes the gap to Causely/Traversal and *sharpens the out-of-window story*: hours later, you especially need to prove causality to the deploy, not just temporal coincidence. Protects the one reversible action from being spent on a downstream symptom.
- **Builds on.** New `ledger.py` + extends `memory.py` (already per-service durable records on `state_store`). Feeds a new field into the decision context consumed by `adk_brain`/`gemini`. Reuses `tools.list_cloud_run_revisions`. The action remains "route 100% to last-good identity" — unchanged.
- **Effort:** large (medium if dependency edges are config-declared first, discovered later).

### Feature 3 — Graded-confidence verifier + confidence→autonomy wiring (Cleric/Resolve.ai)
- **What.** `adk_brain.decide` emits **N ranked hypotheses with explicit confidence**; a lightweight Gemini **verifier pass** adversarially scores whether the evidence supports the top hypothesis. The verified confidence is threaded into `_validate`, and **low confidence forces an approval gate** regardless of the service's standing autonomy level.
- **Why.** This is the "explainability/auditability winners need" (Gartner). The deterministic action layer only fires on a hypothesis that survived a second adversarial look — a real moat-deepener that pure-rules competitors can't claim.
- **Builds on.** Extends `schemas.py` (`IncidentDecision` → ranked hypotheses + `verifier_confidence`) and `adk_brain.py`. The confidence→gate tie reuses the existing `CONFIDENCE_THRESHOLD` check in `_validate` (line 460) and the L1 approval path (`state_machine.py:133–143`). **Verifier sits in the diagnosis tier — the action layer never imports it** (enforced by the *fixed* invariant test, P2#9).
- **Effort:** medium.

### Feature 4 — Airbag as an MCP "reversible-action + proof" agent + Airbag-Bench
- **What.** Two complementary pieces: **(4a)** expose `rollback_to_last_good`, `prove_recovery`, `open_self_proving_fix_pr` as first-class MCP tools with **structured proof outputs** other SRE agents can consume (Komodor/PagerDuty A2A direction). **(4b) Airbag-Bench** — a labeled incident-replay harness reporting precision/recall on rollback, false-rollback rate, mean-stages-to-mitigate, false-escalation rate, seeded from `memory.py`/`incidents.py` recorded incidents.
- **Why.** 4a is the defensible A2A niche (action-with-proof is rare). 4b converts "we think Gemini decides well" into a reproducible number on a slide — the rigor that wins a DevOps-judged hackathon and de-risks raising autonomy. It is also the **regression guard** for Features 1–3.
- **Builds on.** 4a extends `mcp_remote.py` (today only read/trigger/approve/set-autonomy) — wrap the existing `state_machine` entrypoints. 4b builds on `backends/mock.py` + `tests/test_mock_flow.py` style, replaying through `adk_brain.decide` → `state_machine._validate` (the existing decision+gate seam). Pure additive harness, no prod surface.
- **Effort:** medium.

*(Deliberately deferred to v4: multi-platform GKE/Cloud Functions backends, dependency-aware multi-service blame, learned bandit/RL autonomy policy, cost ledger, full fleet control-plane UI, pre-deploy admission gate. See §6 — these are the over-engineering trap.)*

---

## 5. Phased development plan (hand to a fresh dev session)

Sequencing is dependency-driven: harden the seams the new diagnosis tier will lean on, **then** grow the diagnosis tier, **then** make it legible.

### Phase 0 — Harden the foundation + build the measuring stick — *~1 week*
Land before touching the schema or decision layer. **(Re-sequenced per the Gemini 3.1 Pro review — see §0.)**
1. **Build Airbag-Bench FIRST** (was Feature 4b): the labeled incident-replay harness + scorecard
   (precision/recall on rollback, false-rollback rate, mean-stages-to-mitigate). **Baseline the v2
   5xx-only implementation now** so Phases 1–2 are a TDD loop against real numbers — you cannot
   safely tune multi-signal fusion / CUSUM thresholds / the verifier without it.
2. **Fix the invariant guard** (P2#9, AST-based) — *prerequisite for everything in the diagnosis tier.*
3. **Drop `OPEN_FIX_PR` from the enum** (P0#1) — *prerequisite for any schema change in Phase 2.*
4. **Etag/lease on traffic mutation** (P1#4) — *prerequisite for Feature 1's extra `set_traffic` callers.*
5. **Harden the `fix_pipeline` sandbox** (was deferred to v4 — the review is right it can't wait):
   move the LLM-authored test execution to a **network-egress-disabled Cloud Run Job**. Un-sandboxed
   LLM code in the prod agent contradicts the "guarded action layer" moat; a judge will spot it.
   (May slip to early Phase 1 if the Job plumbing is large, but no later.)
6. **`/demo/run` error handling** (P0#3) + **header-only alert token** (P0#2) — demo + security.
7. **Generic CI-retry prompt** (P1#5).
- **Exit:** all tests green (110 after Airbag-Bench: 103 agent + 7 mcp-server) + an Airbag-Bench
  baseline scorecard for the v2 impl (`docs/AIRBAG_BENCH.md`) + tests for the invariant AST check and
  the etag retry path; the sandbox runs egress-disabled.

### Phase 1 — Multi-signal detection engine (Feature 1) — *~1–1.5 weeks*
Depends on: Phase 0 (etag).
1. Define `DegradationVerdict` (reuse FAIL/PASS/INCONCLUSIVE) and a `SignalProvider` protocol.
2. Refactor `analyzer.analyze` into a `Detector` (Wilson 5xx becomes detector #1) behind the protocol — **keep its output identical** so `_validate` is untouched.
3. Add latency + saturation collectors to `tools.py`/`backends/gcp.py` (and `mock.py` for tests) using `run.googleapis.com` metrics; add the multi-window burn-rate detector.
4. Add a fusion step; gate it behind `AIRBAG_SIGNALS` (default 5xx-only) for safe rollout.
- **Exit:** mock-backend tests prove each detector fires independently and fusion yields one verdict; the existing heal flow is unchanged when only the 5xx detector is enabled.
- **Parallelizable:** P2#6 (SSE resumability) and P2#7 (FIFO dedup) — independent, no shared files.

### Phase 2 — Causal grounding + graded-confidence verifier (Features 2 & 3) — *~1.5–2 weeks*
Depends on: Phase 0 (enum + invariant), Phase 1 (signals feed the causal correlation).
1. **Deploy ledger** (`ledger.py` on `state_store`): record each revision's SHA/config/traffic on deploy; backfill on first sighting. Add the Firestore index file + emulator test (P2#8) here — the ledger is the first new ordered collection.
2. **Causal pre-check**: correlate the (now multi-signal) onset against the ledger's deploy timestamps + declared dependency edges; output a `causal_confidence` into the decision context.
3. **Graded-confidence verifier**: extend `IncidentDecision` to ranked hypotheses + `verifier_confidence`; add the verifier pass in `adk_brain.py`; thread confidence into `_validate` and force the L1 approval gate when low.
4. **Close the autonomy loop** (Datadog/Rootly borrow): on approval of signature Y for service X, persist it so the next identical signature auto-promotes (`autonomy.record_outcome` + a signature map).
- **Exit:** a replayed coincident-dependency-outage fixture yields OBSERVE/ESCALATE (not a wasted rollback); a low-confidence fixture forces approval even at L3.

### Phase 3 — Make it legible & extensible (Feature 4) — *~1 week*
Depends on: Phases 1–2 (so the bench scores the new logic; MCP tools wrap the matured entrypoints).
1. **Airbag-Bench → CI gate** (the harness itself was built in **Phase 0**; here you promote it to a blocking CI gate and add fixtures for the new multi-signal + causal logic, so Phases 1–2 stay regression-protected and the scorecard goes on the slide).
2. **MCP action tools** (4a): `rollback_to_last_good`/`prove_recovery`/`open_self_proving_fix_pr` in `mcp_remote.py` with structured proof outputs (Bearer-gated like the existing tools).
3. **Signed incident proof bundle** (incident.io borrow) at keyless-close, from existing Firestore state.
4. **Dashboard**: surface ranked hypotheses + confidence + the multi-signal verdict + the headline **"Alert-to-Verified-Recovery time"** metric (Azure/AWS borrow). Fold in the ground-truth captured request/response (Lightrun borrow) into the PR body.
- **Exit:** Airbag-Bench produces a scorecard number; the MCP action surface drives a full heal from an external client; the demo shows a *latency* regression caught + causally attributed + proven recovered.

**Critical-path dependency chain:** P0 invariant+enum → P1 signals → P2 ledger+verifier → P3 bench+MCP. Phases 1 and the P2-bucket fixes (SSE, dedup, Firestore index) can run in parallel by a second dev; the rest is serial because each phase extends the seam the next one consumes.

---

## 6. Risks + what NOT to do (avoid the over-engineering trap)

**The v2-cycle pattern to avoid:** v2 already shipped a *lot* of durable infrastructure (Firestore, Cloud Tasks, Pub/Sub multi-instance, WIF, MCP stdio+remote). The temptation in v3 is to keep widening the platform — more backends, more services, an RL policy, a cost ledger, a full control-plane UI. **Resist it.** The judging axis and the moat both reward *depth of the core loop* (detect→prove→act→fix), not breadth of surface.

**Specific "do NOT" list:**
- **Do NOT build multi-platform GKE/Cloud Functions backends in v3.** It's "large," widens the test matrix, and adds zero to the causal/multi-signal story. The Cloud Run loop is the demo. (v4.)
- **Do NOT build the learned bandit/RL autonomy policy.** It's the flashiest idea and the worst ROI now: it needs an outcome corpus you don't have yet (Airbag-Bench is the prerequisite), and it risks the deterministic-floor thesis. Ship the *learning loop* (Datadog borrow — approvals raise confidence) which is honest and cheap; defer RL.
- **Do NOT build the dependency-aware multi-service *blame* engine yet.** The deploy ledger (Feature 2) is the 80% that makes the causal story real; cross-service blame is a large v4 follow-on built *on* the ledger, not before it.
- **Do NOT build the full fleet control-plane UI or the pre-deploy admission gate.** Both are "make it a product" surfaces, not "win the round" depth. The dashboard work in Phase 3 is intentionally scoped to *surfacing the new diagnosis*, not a fleet console.
- **Do NOT let the verifier/causal layer leak into the action layer.** The entire bet's credibility rests on "FSM acts, LLM advises." Fix the AST invariant guard *first* (Phase 0) so a Phase-2 import slip fails CI loudly.

**Technical risks to watch:**
- **Multi-signal false positives.** Latency/saturation detectors are noisier than a 5xx count. Mitigation: ship them behind `AIRBAG_SIGNALS`, default-off, and require the *same* statistical confidence (CI lower bound > baseline) the Wilson gate already enforces — don't let a single noisy detector trip a rollback.
- **`fix_pipeline` sandbox is still arbitrary LLM-driven code execution** in the prod agent (`fix_pipeline.py:176–188`; only the metadata server is neutralized). It is the single largest security gap and — per the Gemini review — **it is now Phase-0/1 work, not a v4 defer**: run the LLM-authored test in a **network-egress-disabled Cloud Run Job**. Until then, call it out explicitly in the deck; do not leave it half-done while claiming a "guarded action layer."
- **Vendor-number hygiene.** When citing competitors externally: Traversal is **32% MTTR** (not 40%); Causely's 100% is a **self-reported** benchmark; the Causely remediation quote is from the **arXiv paper**, not Businesswire. Don't repeat the inflated/mis-sourced versions on a slide.

---

*Files of record for v3 work: `agent/autosre/{analyzer,state_machine,schemas,adk_brain,memory,autonomy,mcp_remote,events,tools}.py`, `agent/autosre/backends/gcp.py`, `agent/app.py`, `infra/alert-setup.sh`, `agent/tests/test_architecture_invariant.py`, `.github/workflows/complete-rollback.yml`, `agent/static/dashboard.html`. New: `agent/autosre/ledger.py`, `agent/autosre/signals/`, `agent/tests/bench/` (Airbag-Bench), `firestore.indexes.json`.*
