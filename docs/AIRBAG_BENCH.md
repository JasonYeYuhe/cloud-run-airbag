# Airbag-Bench — the measuring stick

> A labeled incident-replay harness + scorecard for Airbag's decision quality. Built **first** in the
> v3 cycle (Phase 0.1, per the Gemini 3.1 Pro review) so Phases 1–2 are a TDD loop against real
> numbers — you cannot safely tune multi-signal fusion or a verifier without a baseline. Code:
> `agent/tests/bench/` · committed baseline: `agent/tests/bench/baseline_scorecard.json` · tests:
> `agent/tests/test_bench.py`.

## What it measures

Airbag's thesis is **Gemini diagnoses, a deterministic FSM acts**. The seam that actually decides
whether traffic gets rolled back is `analyzer.analyze` (the Wilson 5xx verdict) → a decision proposal
→ `state_machine._validate` (the gate) → the autonomy level. Airbag-Bench replays the **real**
`run_self_heal` over a corpus of labeled incident scenarios and scores the **decided action** against
ground truth.

Metrics (each ratio carries its raw `num/den`; a zero denominator renders `n/a`, never `0.0`/`NaN`,
and is excluded from any gate):

| Metric | Definition |
|---|---|
| **Rollback precision** | correct rollbacks ÷ all rollbacks decided |
| **Rollback recall** | correct rollbacks ÷ all cases that warranted a rollback |
| **False-rollback rate** | rolled back when it shouldn't (÷ all cases) |
| **False-escalation rate** | paged a human on a benign case (÷ OBSERVE-expected cases) |
| **Wasted-rollback rate** | decided rollback, it didn't clear, then escalated (÷ all cases) |
| **Target correctness** (v4) | of the ROLLBACK decisions on cases that pin an `expected_target`, how many AIMED at the ground-truth revision. **Decided-keyed — it scores the SELECTOR**: a wrong aim counts even when the causal veto stops it pre-shift (an executed-keyed metric would read a flattering 100% there) |
| **Mean stages-to-mitigate** | FSM stages emitted on a successful mitigation |
| **Accuracy** + confusion matrix | decided == ground truth |

## Honesty caveats (read before quoting a number)

1. **It scores the v2 _deterministic floor_, with the LLM OFF — a LOWER BOUND, not a measurement of
   the live agent.** For reproducibility (no API key, deterministic, CI-able) the harness forces the
   deterministic `_heuristic` to play the decider's role. The production path is Gemini via the ADK
   `SequentialAgent`, which is *stronger* than the heuristic. So every "v2 MISSES / WRONGLY ROLLS
   BACK" number is a property of the heuristic floor and a conservative lower bound. (Note: the live
   Gemini prompt's rollback threshold is `error_rate ≥ 0.5`; the heuristic's is `0.05` — so borderline
   cases can diverge live. The bench measures the floor; treat it as such.)
2. **`decided_action` comes from the DECISION event** (the gate's verdict), *independent of the
   mitigation outcome* — so a case that decides ROLLBACK and then fails verification (a coincident
   dependency outage) is correctly counted as a (false) rollback, not as an escalation.
3. **The corpus is pre-registered, not stacked.** It is deliberately balanced (the floor is correct on
   5/11 and has a named gap on 6/11). Each gap is registered below *before* v3 exists, with the phase
   that closes it — so the scorecard is a roadmap, not a deck-stacking exercise. Three cases
   (`coincident_dependency_outage`, `low_traffic_blip`, `no_healthy_target`) are explicit **judgment
   calls** whose label rationale is in `agent/tests/bench/fixtures.py` and below. The corpus grew
   with the roadmap (11 → 17 in v3, 22 in v4 with the `bad_bad` target-correctness family + the
   latency-axis causal cases); each
   section below states the corpus size its numbers were computed on.

## Baseline scorecard — 5xx-signal deterministic floor (LLM off)

_11 labeled cases. Regenerate with `make bench` / `python tests/bench/run_bench.py --write`. As of v3
Phase 1.1b the deterministic gate gained a **promotion** rule — a confident (FAIL) statistical verdict
can now drive a rollback even when the LLM/heuristic hedged. In 5xx-only mode this changed exactly one
case: `no_healthy_target` now correctly **ESCALATEs** (a confirmed outage with no rollback target)
instead of silently observing → accuracy 45.5%→54.5%. Recall/precision (the multi-signal + causal
targets) are unchanged in 5xx-only mode; the multi-signal recall lift is measured separately (§ How
Phase 1 uses this)._

| Metric | Value | Reading |
|---|---|---|
| Rollback precision | **66.7% (2/3)** | 1 of 3 rollbacks is misfired — the coincident dependency outage |
| Rollback recall | **40.0% (2/5)** | 5xx-only misses every non-5xx regression (latency / saturation / SLO-burn) |
| False-rollback rate | 9.1% (1/11) | the dependency outage |
| False-escalation rate | 25.0% (1/4) | the 1-in-4 blip |
| Wasted-rollback rate | 9.1% (1/11) | the dependency rollback that didn't clear |
| Accuracy | 54.5% (6/11) | promotion fixed `no_healthy_target` |
| Mean stages-to-mitigate | 10.0 | — |

Confusion matrix (rows = ground truth, cols = decided):

| expected ↓ / decided → | ROLLBACK | OBSERVE | ESCALATE |
|---|---|---|---|
| ROLLBACK | 2 | 3 | 0 |
| OBSERVE | 0 | 3 | 1 |
| ESCALATE | 1 | 0 | 1 |

## Pre-registered gaps → the v3 phase that closes each

| # | case(s) | 5xx floor does | should do | gap | closed by |
|---|---|---|---|---|---|
| 1 | `latency_regression` | OBSERVE | ROLLBACK | **recall** — 5xx-only signal coverage | **Phase 1.2** latency detector (multi-signal) |
| — | `saturation_cpu`, `slo_slow_burn` | OBSERVE | ROLLBACK | **recall** (deferred detectors) | saturation + burn-rate deferred (a follow-up — see below) |
| 2 | `coincident_dependency_outage` | ROLLBACK (wasted) | ESCALATE | **precision** — no causal grounding | **Phase 2** deploy-ledger causal pre-check |
| 3 | `low_traffic_blip` | ESCALATE | OBSERVE | **false-escalation** — thin-evidence paging | **Phase 3** verifier / better evidence (judgment call: paging policy) |
| ✅ | `no_healthy_target` | ~~OBSERVE~~ → **ESCALATE** | ESCALATE | ~~mishandle~~ **CLOSED** | **Phase 1.1b** deterministic promotion |

The recall gap is **a signal-coverage gap, not a gate bug**: with 5xx ≈ 0 the business-path sample is
`0/20`, so the Wilson verdict is INCONCLUSIVE and nothing fires. **Phase 1.2** adds a latency detector
(the clean, CI-backed, out-of-window signal); **saturation and SLO-burn are deferred to a follow-up** —
saturation has no confidence bound (a healthy CPU-pegged service would false-rollback) and burn-rate
can't be told from steady-state noise without the learned baseline, so shipping them now would be
gold-plating that risks a false rollback (see `docs/V3_VISION.md §6`).

## Multi-signal result — Phase 1.2 (`AIRBAG_SIGNALS=5xx,latency`)

_Regenerate: `python tests/bench/run_bench.py --signals 5xx,latency`. Committed:
`multisignal_scorecard.json`. This is the TDD proof: the latency detector lifts recall on the
out-of-window regressions WITHOUT increasing the false-rollback rate._

| Metric | 5xx-only floor | **5xx + latency** | What moved |
|---|---|---|---|
| Rollback recall | 33.3% (2/6) | **66.7% (4/6)** | catches both latency regressions (the out-of-window win) |
| Rollback precision | 66.7% (2/3) | **80.0% (4/5)** | two more correct rollbacks, no misfires added |
| **False-rollback rate** | 7.1% (1/14) | **7.1% (1/14)** | **unchanged** — the §6 guard: latency adds zero false rollbacks |
| Accuracy | 57.1% (8/14) | **71.4% (10/14)** | — |

What the latency detector does per case: `latency_regression` (14× p99) and
`latency_regression_moderate` (2.5× p99) → **ROLLBACK** (were silent OBSERVE misses);
`latency_within_slo` (mild jitter) and `latency_spike_transient` (one hot window) → **OBSERVE** (no
false trigger — the **N-window persistence gate / debounce** is load-bearing: a naive non-debounced
detector would roll back on the transient spike). `saturation_cpu` and `slo_slow_burn` are still
missed — their detectors are deferred, so this is **not** a headline "100% recall"; it's an honest
+34-point recall lift on the signal we shipped, with precision up and false-rollbacks flat.

The detector is **CI-backed** (Wilson-gates the per-window slow-request proportion, same statistical
rigor as the 5xx gate) and **deterministic** (no LLM — enforced by the AST invariant on `signals/`),
so its FAIL verdict drives a rollback through `_validate`'s deterministic promotion. Enable it in
production with `AIRBAG_SIGNALS=5xx,latency`; the demo stays 5xx-only (default), zero demo risk.

## Causal pre-check — Phase 2a (`AIRBAG_CAUSAL_CHECK=on`)

_Regenerate: `python tests/bench/run_bench.py --signals 5xx,latency --causal`. Committed:
`causal_scorecard.json` (+ `causal_5xx_scorecard.json`). Scoring keys off the FINAL action — a rollback
counts only if traffic actually shifted (`ROLLBACK_APPLIED`), so a causal escalate is honestly counted._

Before committing a rollback, Airbag **probes the rollback target's health**: if the last-good
revision is ALSO confidently degraded, the cause is external (a dependency/quota outage), not this
revision — so a rollback is futile → **ESCALATE without the wasted traffic shift**. Only a
*confident*-unhealthy target blocks (Wilson gate over N probes); a transient/flaky/errored probe →
INCONCLUSIVE → **proceed with the rollback** (a legitimate rollback is never blocked).

_(v3-era corpus, 17 cases — the v4 bad→bad cases below change the denominators and deliberately
recalibrate the "recall UNCHANGED" invariant: a veto of a rollback AIMED at a landmine is the check
working, so the safety guard is now "never block a rollback aimed at a HEALTHY target" + a committed
recall ratchet — see the v4 section.)_

| Metric | 5xx,latency (causal off) | **+ causal** | What moved |
|---|---|---|---|
| Rollback precision | 75% (6/8) | **100% (6/6)** | both external-cause outages stop rolling back |
| **False-rollback rate** | 11.8% (2/17) | **0% (0/17)** | zero wasted rollbacks |
| **Rollback recall** | 75% (6/8) | **75% (6/8) — UNCHANGED** | the causal check blocks ZERO legitimate rollbacks |
| Accuracy | 12/17 | **14/17** | — |

Per case (causal on): `coincident_dependency_outage` + `coincident_quota_exhaustion` → **ESCALATE**
(target also failing → no traffic shift); `masquerade_real_bad_deploy` (high 5xx but target HEALTHY) →
**ROLLBACK** and `intermittent_target` (target blips 2/8, below the confidence bar) → **ROLLBACK** —
the two anti-regression / safety cases prove the check never blocks a genuine bad deploy.

**Honesty:** v2 was already *safe* on the coincident case — its post-rollback `_verify` escalates when
recovery isn't proven (surfaced as `wasted_rollback_rate`). The causal pre-check's win is **narrower
and real**: it skips the futile traffic shift + the verify delay and gives an evidence-grounded
pre-action reason. It adds **no** new correctness class for stateful/data-migration bugs (a synthetic
GET probe doesn't exercise a mutation). Deterministic + LLM-free (`causal.py` under the AST invariant);
default OFF (demo unchanged). The gcp target-probe (tag-at-0% + probe + restore) is implemented,
defensive (any error → proceed), and OPT-IN — not live-verified this session.

## Target correctness — v4 Phase 1 (the serving-history ledger)

_Regenerate: `--write` in each mode. Corpus: **22 cases** (the 17 above + the `bad_bad` family +
the two v4 latency-axis causal cases)._

**Latency-axis causal veto (v4 Phase 2).** The v3 target-probe counted only 5xx, so a
200-but-confidently-slow target passed the pre-check for a *latency* incident and the futile
rollback shipped (`latency_coincident_slow_target`: multisignal causal-off = a wasted rollback;
causal-on = **COINCIDENT → ESCALATE with zero traffic shifted**). The probe now returns
`{errs,total,slow}` and, **only when the triggering signal is latency**, a second Wilson gate on
the slow-proportion (same knobs as the latency detector) can veto. Safety mirrors the 5xx axis:
`latency_target_warmup_blip` (2/8 slow, below the bar) still rolls back, and the gcp probe RINSES
the cold start (one untimed request) so a scaled-to-zero target's boot latency is never counted as
veto evidence. Honest limits: the veto sees slowness between the SLO and the probe's 10s timeout
(beyond that: INCONCLUSIVE → proceed, `_verify` backstops); with the veto on, **causal-mode false
rollbacks are 0 across BOTH external-cause axes** (5xx dependency/quota + slow dependency).

_The v4 TARGET-correctness dimension (below) uses the same corpus._
The v4 marquee claim is about the rollback **TARGET**: "roll back to a known-good revision" was
recency-as-proxy ("newest ready 0-traffic"), which a **bad→bad deploy sequence defeats** — ship
broken R12, panic-ship broken R11: recency aims at R11, the second landmine, even when an older R9
was WITNESSED serving healthily. The action-only scorecard **cannot see this miss** (the wrong-target
rollback still reads "ROLLBACK ✓"), which is why v4 adds the target dimension._

Three committed cases, one world (newest serving bad · newer-ready landmine · older witnessed-good):

| case | ledger | aims at | causal OFF outcome | causal ON outcome |
|---|---|---|---|---|
| `bad_bad_ledger_heals` | **warm** (R9 witnessed) | R9 ✓ `[ledger]` | **mitigated** | **mitigated** (live probe agrees) |
| `bad_bad_cold_ledger` | cold (the pre-v4 control) | landmine ✗ `[recency]` | **WASTED rollback** (shift → fail verify → escalate) | **vetoed pre-shift → ESCALATE** — safe, but a human is paged though a proven-good revision exists |
| `bad_deploy_newest_is_witnessed` | warm, newest IS witnessed | same as recency ✓ | mitigated | mitigated (negative control: no distortion of the common case) |

Committed numbers: target correctness **6/7 (5xx floor)** and **8/9 (multisignal)** — the single ✗
in every mode is the deliberate cold-start control; the warm ledger case converts its ESCALATE into
an autonomous heal onto the witnessed-good revision.

**Honesty:** the ledger only **PROPOSES** — the live causal pre-check still probes whatever target is
selected before any traffic shifts (a stale witness is vetoed live; pinned by
`test_stale_ledger_entry_cannot_bypass_the_live_probe`). Cold start falls back to recency,
byte-identical to v3. "Witnessed-healthy" means *observed serving healthily at witness time* (a PASS
or zero-5xx no-op run, or a `_verify`-proven mitigation target) — not a guarantee about now. The old
"recall UNCHANGED by causal" invariant was recalibrated (a veto of a landmine-AIMED rollback is
correct): the committed guard is now *never block a rollback aimed at a healthy-modeled target* plus
a recall ratchet against the committed causal scorecards.

## Storm scorecard — v5 Phase 2 (`AIRBAG_STORM_COALESCE` + `AIRBAG_SELF_TRAFFIC_EXCLUDE`)

The decision-quality scorecards above score ONE heal per fixture. The **storm scorecard** scores a
different thing: the OUTCOME SHAPE of a whole *outage* — one broken service hit by **N alert
deliveries** (distinct Cloud Monitoring incident ids), replayed sequentially through the real
`run_self_heal` seam against a `StormBackend` that models Airbag's own probe-feedback (its triage
5xx land in the log-based detection COUNT unless the self-traffic exclusion filters the probe UA).
It reproduces the real 2026-07-02 storm SHAPE and proves the storm flags fix it. Committed for BOTH
flag states, pre-registered + CI-ratcheted (`tests/test_storm_scorecard.py`).

| Metric (per outage, N=6) | flag-off (2026-07-02 shape) | flag-on (storm-safe) |
|---|---|---|
| `heals_per_outage` | **6** — every alert ran its own full heal | **1** — one leader; the rest coalesce |
| `approval_cards_per_outage` | **6** — each heal filed its own card | **1** — one operator card |
| `self_traffic_counted_in_detection` | **5** — Airbag's own probe 5xx counted as user 5xx | **0** — probe UA excluded |
| `unattended_terminal_states` | **5** — N−1 redundant cards pile up + expire silently | **0** — followers attach cleanly |
| `blind_landings` (v5 3.1) | **0** — L1 gates before any rollback | **0** — same; mechanism proven in `test_blind_landing.py` |

Flag-off is the honest ugly baseline (committed on purpose, labeled — *the storm stops being an
anecdote*); flag-on is `1/1/0/0`. **HONEST FRAMING:** the scorecard measures outcome shape on a
deterministic *sequential* replay; it does **not** claim to reproduce concurrency — the concurrent
transactional safety (N simultaneous deliveries → exactly one leader, no lost ids) is proven
separately by the threaded lease-contention tests in `test_state_store.py`. Both together are the
exit criterion. (`unattended` here = redundant pending operator items, the silent pile-up; the
single legitimate card for an outage is *attended*, not counted.) Two honesty notes on the metric
independence in this sequential replay: `approval_cards` and `unattended` move together on a
pure-awaiting storm (they measure different things — the approval store vs the run outcomes — but
agree here); and `self_traffic=0` flag-on is *over-determined* — EITHER storm flag alone zeroes it
in the model (coalescing removes the follower probes; the UA exclusion filters them). The real
self-traffic filter and the one-leader coalesce are each proven independently at the unit level
(`test_probe_marking.py`, `test_state_store.py`), so the scorecard neither invents nor over-credits
a single flag.

## How Phases 1–2 use this (the TDD loop)

1. Make a change behind its flag (e.g. `AIRBAG_SIGNALS`).
2. `make bench` — see which cases flipped.
3. If the flip is intended (e.g. `latency_regression` → ROLLBACK), re-run with `--write` and **review
   the JSON diff** in the PR; the golden ratchet test (`test_per_case_matches_committed_baseline`)
   forces every behavior change to be intentional, and the monotonic guard
   (`test_false_rates_do_not_regress_vs_baseline`) prevents a recall fix from silently regressing
   precision elsewhere.
4. The "meaning" tests assert the *current* known gaps; when a phase closes one, update its assertion
   and the baseline together.

## Running it

```bash
make bench                                  # print the scorecard
cd agent && python tests/bench/run_bench.py # same, directly
cd agent && python tests/bench/run_bench.py --write   # regenerate the committed baseline JSON
cd agent && python tests/bench/run_bench.py --storm          # print the storm scorecard (both flags)
cd agent && python tests/bench/run_bench.py --storm --write  # regenerate the committed storm scorecards
cd agent && python -m pytest tests/test_bench.py -q   # the regression tests
```
