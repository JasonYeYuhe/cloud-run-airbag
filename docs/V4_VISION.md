# Airbag v4 — Vision & Development Plan

> Plan of record for the next stage. Produced by a 6-lens first-principles ideation workflow
> (action-safety · detection-breadth · causal-frontier · honesty/proof · competitive · irreducible-bet)
> → dedup + scoring → adversarial refute-by-default critique → synthesis, all grounded by reading the
> actual repo. Then reviewed by Gemini 3.1 Pro (see §0). Window: **~8 days to submission (2026-07-10).**

## §0. Gemini 3.1 Pro review (incorporated)
Reviewed against the actual code. Verdict: **GO** — *"a phenomenal, rigorous plan; it correctly shifts
focus from 'more detection' to 'provably correct/safe actions' — exactly the right leverage point for v4.
Converting the recency proxy into a literally-true 'known-good' target is a massive upgrade for the core
value proposition."* Fully aligned on the phased approach, the LLM-free/no-redundant-check/fail-open
constraints, and the descope ladder. **All six open questions (§8) confirmed at their recommended
defaults** — so the decisions below are LOCKED for the fresh session (Jason may still override): (1) ship
the bench target-correctness dimension; (2) marker = Cloud Run annotation `airbag.dev/irreversible=true`;
(3) irreversibility guard default-OFF, latency-veto ON in prod; (4) descope ladder as written (protect
Phase 3 over Phase 2); (5) live-proof capture best-effort/drop-first (the emulator CI test matters more);
(6) state the corrected facts plainly.

## 1. Executive summary
After v3 (live by default, agent rev 00032), Airbag reliably **DETECTS** an incident (multi-signal
5xx + latency, Wilson-gated, debounced) and **ACTS** reversibly via a deterministic FSM: statistical-FAIL
promotion, a causal target-probe that vetoes a futile rollback, signal-aware verify/remediation, and a
tamper-evident proof bundle scored on a 17-case bench with a CI ratchet. The invariant holds and is
AST-guarded (Gemini diagnoses, the FSM acts; the action tier never imports the LLM).

**v4's leverage is NOT more detection — it is making the ONE reversible action provably correct and
provably safe.** Today the rollback *target* is the "newest ready 0-traffic revision"
(`state_machine._rollback_pair`/`_heuristic`): recency is a **proxy** for last-good that a bad→bad deploy
sequence defeats (**gap a**), and nothing detects a **forward-only / irreversible** deploy where a
traffic rollback makes the outage strictly worse (**gap b**). v4 replaces recency with a thin
serving-history ledger of witnessed-healthy revisions (the marquee), tightens the pre-flight target-probe
to cover the **latency** signal it is currently blind to, adds a fail-open irreversibility-contract
guard, and pays down the confirmed Firestore-durability test gap (**gap e**) that the new ledger needs
anyway. Every bet is **LLM-free in the action tier**, provable offline on the existing coupled bench +
CI ratchet, and leaves the live demo target HEALTHY.

## The single biggest v4 bet
**Make the rollback TARGET provably last-good instead of merely newest.** A thin serving-history ledger
stamps the currently-serving revision as the service's *witnessed-healthy* revision on every OBSERVE/PASS
and at MITIGATE (reusing the exact per-service `state_store.transact` pattern `memory.observe_healthy`
already uses), and `_rollback_pair`/`_heuristic`/`_validate` then **prefer** a ready revision Airbag has
*witnessed serving healthily*, falling back to recency only on cold start.

The framed thesis is target **SELECTION**, not target veto: on a bad→bad sequence today's veto-only gates
(`causal.precheck` + `_verify`) already prevent *landing on* a broken target — but they do so by
**ESCALATING to a human even when a proven-good older revision exists**. The ledger converts that ESCALATE
into an autonomous heal by *selecting* the witnessed-good revision. This upgrades the most load-bearing
sentence in the pitch — *"we roll back to a KNOWN-good revision"* — from a recency proxy to **literally
true**, with no LLM intelligence and no DO-NOT-list violation.

> **Mandatory guardrail:** the ledger only PROPOSES a target; the existing causal pre-check MUST still
> re-probe it live at act-time, so a stale ledger entry (healthy an hour ago, dead now) can never bypass
> the live probe. Cold start honestly falls back to recency (no-worse than today).

## 2. The three gaps v4 closes (grounded in code)
- **(a) Recency ≠ last-good.** `_rollback_pair` (`state_machine.py`) picks the newest ready 0-traffic
  revision by list order. A bad→bad deploy (ship broken R10, panic-ship broken R11) makes the target R10 —
  another broken revision. v3's gates then *escalate* (safe, but not autonomous) even if a proven-good R9
  exists. → **Phase 1 (marquee).**
- **(b) Irreversible deploys.** A forward-only deploy (DB/schema migration) leaves the old revision unable
  to read the migrated datastore; a traffic rollback onto it deepens the outage. A GET-probe can't exercise
  a mutation, so the old code boots fine and **every existing gate greenlights the harmful action.** → **Phase 3.**
- **(e) Firestore untested.** `deploy.sh` runs `AIRBAG_STATE=firestore` in prod, yet `conftest.py` pins
  `AIRBAG_STATE=memory` and CI has zero emulator coverage — so the durability + per-incident-lease story is
  untested on the backend it actually uses. The marquee ledger adds a new ordered collection that *needs*
  this. → **Phase 4 (Phase-1 acceptance gate).**

## 3. Phased development plan (hand to a fresh dev session)
Sequenced so the highest-value work lands first, each phase LLM-free in the action tier, each provable on
the existing bench + CI ratchet, each leaving the demo target HEALTHY.

### Phase 1 — Marquee: proven-last-good rollback target — *~3.5 days*
Replace recency-as-proxy with a witnessed-healthy serving history, framed as target SELECTION (upgrades an
ESCALATE into an autonomous heal), and make it PROVABLE by teaching the bench to score *which* target was
chosen (not just the action). Ledger only proposes; the live causal probe still gates whatever it picks.
1. **Stamp witnessed-healthy serving revision into a per-service ledger** *(1d)* — on every OBSERVE/PASS
   (`state_machine.py` ~112-114, beside `memory.observe_healthy`) and at successful MITIGATE (after
   `_verify` passes, ~199-203), record the serving revision name + timestamp into a bounded per-service
   ledger. Reuse `memory.observe_healthy`'s exact `state_store.transact(_COLL, service, _m)` idempotent
   pattern — a new field/collection on the same per-service doc, **not** a topology/dependency graph.
   *Proof:* unit tests — stamps on OBSERVE/PASS/MITIGATE, bounded, idempotent under transact retry, and
   never stamps the immediate post-rollback 0.0-window revision (mirrors the `observe_healthy` rule).
2. **Prefer a witnessed-good target, recency fallback on cold start** *(1d)* — wire a ledger read into
   `_rollback_pair` (~486), `_heuristic` (~472), and the `_validate` promotion (~528): among ready
   0-traffic non-serving revisions, prefer the newest that appears witnessed-healthy; if none (cold start),
   fall back to today's newest-ready behavior **unchanged**. The selected target still flows into
   `_mitigate` where `causal.precheck` live-probes it before any shift.
   *Proof:* `_rollback_pair` unit tests over bad→bad lists (ledger → witnessed-good; empty ledger → newest
   exactly as today) + an integration test that the chosen target still passes `causal.precheck`.
3. **Add a TARGET-correctness dimension to the bench** *(1.5d)* — extend `harness.py` `CaseResult` to
   capture the chosen target, add `expected_target`/target-correctness to the corpus + `scorecard.py`, add
   bad→bad fixtures where recency picks the landmine and the ledger picks witnessed-good, plus a matched
   negative control (healthy-newest IS witnessed-good → agree, no regression). Commit updated golden
   scorecards + CI ratchet. *This is the honest, committable marquee proof — without a target dimension the
   headline claim can't be scored.*

**Exit:** ledger stamps on OBSERVE/PASS/MITIGATE; `_rollback_pair` prefers witnessed-good with an unchanged
recency fallback; a committed bench fixture proves *recency-lands-on-landmine vs ledger-lands-on-witnessed-good*;
the chosen target still passes `causal.precheck` live; all 175+ tests + CI green; reviewed then pushed.

### Phase 2 — Latency-aware causal target-probe (veto-only) — *~2 days*  ·  *candidate-walk DROPPED*
Close the causal probe's genuine blind spot: it counts only **5xx** on the target, so a **latency-regressed**
target passes the pre-check for a *latency*-triggered incident. Add slow-sample aggregation + a second Wilson
gate keyed on the triggering signal. **Do NOT re-implement selection here** (that's the Phase-1 ledger).
1. **Extend `probe_revision_health` → `{errs, total, slow}` across gcp+mock+local** *(1d)* — add per-request
   timing and count requests over the latency threshold. **Corrected fact:** `probe_revision_health` does
   NOT already return `elapsed_ms` (that's `synthetic_probe`, a different function) — this is real work.
2. **Second Wilson gate on slow-proportion in `causal.precheck`, keyed on the triggering signal** *(1d)* —
   thread `primary_signal` from `_mitigate` into `precheck`; when latency, a confidently-slow target returns
   COINCIDENT instead of false CAUSAL/INCONCLUSIVE. 5xx path byte-identical. Fail-safe unchanged
   (scaled-to-zero/unreachable → INCONCLUSIVE → PROCEED = "not confidently bad", never "proven good").
   **Candidate-walk cut** — it duplicates the ledger's selection (the v3 Phase-2b theater trap) and each gcp
   walk step is a live traffic mutation, hard to prove safe.

**Exit:** probe returns `{errs,total,slow}` in all three backends; a latency-regressed target yields
COINCIDENT for a latency incident; 5xx behavior + fail-open posture unchanged; committed latency-bad-target
+ fast-target fixtures + updated causal scorecards; CI green; reviewed then pushed.

### Phase 3 — Forward-only / irreversible-deploy guard — *~2.5 days*
Cover the one gap where every existing gate greenlights a strictly-worse action (**gap b**). Honor an
irreversibility **contract** (a declared marker); do NOT claim to *detect* migrations.
1. **Surface ONE irreversibility-marker field on the revision dict across all three backends** *(1d)* —
   **Corrected fact:** the marker does NOT ride an already-parsed surface — the FSM's revision dict is
   `{name, ready, traffic_percent, create_time}` (`gcp.py:53-55`); `_revision_env` is gcp-only + demo-harness
   only. Add exactly one new field: gcp reads a Cloud Run revision **annotation** (`airbag.dev/irreversible=true`);
   mock/local read a static fixture field. Do NOT go down the MIGRATION_ID/env-diff cross-revision path.
2. **Deterministic guard at the top of `_mitigate`, fail-OPEN, flag-gated** *(1d)* — new LLM-free module
   (`reversibility.py`, added to `test_architecture_invariant._action_files()`) hooked beside
   `causal.precheck`: if the rollback target predates a declared irreversibility marker on the serving
   revision, ESCALATE (reuse the `incidents.record` + `emit('ESCALATED')` plumbing) instead of shifting
   traffic. Place it AFTER the L0/L1 autonomy gate. **MUST fail-open** (marker absent → rolls back → demos
   unchanged); gate behind a config flag **default-OFF**, like `CAUSAL_CHECK_ENABLED`.
3. **Honest docs framing + descope fallback** *(0.5d)* — document that this HONORS a *declared contract*,
   not that it detects migrations. Fallback if time-bound: mock/local fixture + unit test proving
   marker-set → ESCALATE + zero shift, marker-absent → rolls back, without the gcp annotation read.

**Exit:** with the flag on + a marker set, `_mitigate` ESCALATEs and shifts NO traffic; with no marker,
rollback proceeds and the demo stays healthy; the guard module is in `_action_files()` and the invariant
test passes; committed marker-set + marker-absent fixtures; CI green; reviewed then pushed.

### Phase 4 — Honesty ribbon (interleaved days 3–8, buffer-absorbing)
1. **Firestore-emulator CI test + `firestore.indexes.json`** *(1d — a hard Phase-1 acceptance gate)* — add a
   firestore-emulator service to CI, run the existing `state_store` transact/`list_recent`/lease paths under
   `FIRESTORE_EMULATOR_HOST`, and add an index for the ledger's order field. `state_store.list_recent` uses
   `order_by DESCENDING` which **silently omits docs missing the order field** — so the ledger needs the
   index + an emulator test proving ordered reads. Closes gap (e); prerequisite for the durable ledger claim.
2. **Committed live-heal proof artifact + one-command bench verify** *(0.5d — DROP-FIRST nice-to-have)* —
   capture one real incident's proof bundle into `docs/proof/` with a field-by-field recompute walkthrough
   (`run_bench.py --write` already *is* the one-command verify — just add a `make` alias). First to drop.

**Exit:** CI shows a green firestore-emulator job exercising the state_store contract suite + the ledger's
ordered read; `firestore.indexes.json` committed. If time remains, `docs/proof/` carries one recomputable
live-heal bundle; else dropped + documented as deferred.

## 4. What NOT to build (the discipline — cut with reasons)
Carried forward from V3 §6 **plus** what this v4 review explicitly rejected:
- **NO GKE, NO RL/bandit threshold tuning, NO blame/ownership engine, NO multi-service fleet UI** (V3 traps).
- **NO second same-model LLM verifier pass**, and more generally **no feature whose only job is to re-check
  what a deterministic gate already enforces** (the v3 Phase-2b non-redundancy rule — this is why the
  **candidate-walk was cut**).
- **The action tier stays LLM-free**: `backends/*`, `signals/*`, `tools.py`, `causal.py`, the new
  `reversibility.py`, and the `_validate`/`_verify` logic must never import the LLM; any new action-tier
  module MUST be added to `test_architecture_invariant._action_files()`.
- **The ledger is a THIN read-time preference** on the existing per-service memory doc — not a topology /
  dependency / service graph. It only PROPOSES a target and must **never bypass the live causal pre-check**.
- **The irreversibility guard is a declared-MARKER read only** — no DB/schema inspection, no cross-revision
  env/MIGRATION_ID diff, no pre-deploy admission gate. Fail-OPEN, default-OFF.
- **The latency-aware probe is VETO-only** — no target selection, no live per-step traffic mutations.
- Also cut (see the workflow record): elevated-4xx / multi-window-burn-rate / dependency-latency / 429 /
  saturation-as-latency detectors (v4's bottleneck is action-target correctness, not detection breadth);
  post-heal background watcher (overlaps `complete_rollback`); WIF/KMS-signed proof (hardens *provenance*,
  not *correctness* — top v5 candidate); config/env-diff + deploy-onset correlation (RCA evidence that
  changes no decision); torn-split re-read (rare multi-instance race, low judge payoff); gated canary on the
  *initial* rollback (leaves 90% on the broken revision during an active outage).
- Keep google-adk pinned **1.x**; keep CI green; the live demo must ALWAYS leave the target HEALTHY; be
  honest in docs (no overclaiming); commit + push to main as you go; review each substantial change first.

## 5. Corrected facts (the review caught these against the code — state them plainly)
- `probe_revision_health` does **NOT** already return `elapsed_ms` (that is `synthetic_probe`). Phase 2's
  timing work is real, not free.
- The causal probe is **NOT 5xx-blind** — it counts 5xx; it is **LATENCY-blind**. Phase 2 adds the latency axis.
- The irreversibility marker does **NOT** ride an already-parsed env surface — the FSM revision dict is
  `{name, ready, traffic_percent, create_time}`; a new field must be added in `list_revisions`.

## 6. Risks + the pre-agreed descope ladder
Total nominal effort ~9.5d against 8d; Phases 1–3 alone are ~8d with no slack. **Descope ladder** (in order):
(1) drop the Phase-4 live-proof artifact; (2) drop Phase 2 (latency-veto) — the least load-bearing action
bet; (3) descope Phase 3 to a mock/local fixture-only proof (no gcp annotation read); (4) descope Phase 1's
bench target-scoring to `_rollback_pair` unit tests. **The marquee ledger + its emulator gate is the
non-negotiable floor.** Other risks: the ledger read as redundant with the causal probe (mitigate: frame +
test strictly as *selection that turns an ESCALATE into a heal*, with the bad→bad fixture showing OLD code
escalating); stale ledger entry (mitigate: live re-probe is mandatory, add a stale-but-selected test); the
`{errs,total}`→`{errs,total,slow}` contract change regressing the 5xx path (mitigate: 5xx path byte-identical
+ anti-regression fixtures); the guard suppressing a legit rollback (mitigate: fail-open + default-off +
marker-absent anti-regression); gcp annotation read untestable in CI (mitigate: mock-substituted parse test,
document the live-verify limitation); firestore-emulator setup eating >1d (mitigate: interleave early; can
land as a follow-up commit before submission).

## 7. Test + review cadence
Per item: write the unit/bench test alongside the code; run the full 175+ suite locally before every commit;
the bench CI ratchet (`test_bench.py` + committed scorecards) must stay green — a regression in action,
target-correctness, or `false_rollback_rate` fails CI. Per phase: run `test_architecture_invariant.py`
(confirm no action-tier LLM import crept in and the new guard module is in `_action_files()`); confirm the
live demo still leaves the target HEALTHY. **Review gate:** each substantial design AND impl gets a Gemini
review via `agy` (or a multi-agent workflow) BEFORE committing, with special attention to the
non-redundancy rule and honest docs. Commit + push to main incrementally. The Phase-4 emulator job must be
green before submission (2026-07-10).

## 8. Open questions for Jason (defaults chosen; override before/at kickoff)
1. **Phase-1 bench target-scoring (~1.5d):** ship the target-correctness scorecard dimension (stronger judge
   artifact) or fall back to `_rollback_pair` unit tests (buys ~1 day)? **Default: ship the dimension.**
2. **Irreversibility marker mechanism:** Cloud Run revision **annotation** `airbag.dev/irreversible=true`
   (recommended) vs a label / env var? **Default: annotation.**
3. **Flag posture:** ship the irreversibility guard + latency-veto **default-OFF behind flags** (demo
   unchanged) — or turn the latency-veto ON in prod since causal is already on there? **Default: guard OFF;
   latency-veto ON in prod (extends the already-on causal path).**
4. **Descope priority if 8 days bind:** the ladder in §6. **Default as listed** (protect Phase 3
   defensibility over Phase 2). Confirm or swap.
5. **Live proof capture:** is one real break→heal run on rev 00032+ feasible for `docs/proof/`, or treat it
   as best-effort/deferred from the start? **Default: best-effort, drop-first.**
6. **Corrected facts:** confirm V4_VISION.md should state the corrected facts (§5) plainly. **Default: yes.**
