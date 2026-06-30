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
   calls** whose label rationale is in `agent/tests/bench/fixtures.py` and below.

## Baseline scorecard — v2 deterministic floor (LLM off)

_11 labeled cases. Regenerate with `make bench` / `python tests/bench/run_bench.py --write`._

| Metric | Value | Reading |
|---|---|---|
| Rollback precision | **66.7% (2/3)** | 1 of 3 rollbacks is misfired — the coincident dependency outage |
| Rollback recall | **40.0% (2/5)** | misses every non-5xx regression (latency / saturation / SLO-burn) |
| False-rollback rate | 9.1% (1/11) | the dependency outage |
| False-escalation rate | 25.0% (1/4) | the 1-in-4 blip |
| Wasted-rollback rate | 9.1% (1/11) | the dependency rollback that didn't clear |
| Accuracy | 45.5% (5/11) | — |
| Mean stages-to-mitigate | 10.0 | — |

Confusion matrix (rows = ground truth, cols = decided):

| expected ↓ / decided → | ROLLBACK | OBSERVE | ESCALATE |
|---|---|---|---|
| ROLLBACK | 2 | 3 | 0 |
| OBSERVE | 0 | 3 | 1 |
| ESCALATE | 1 | 1 | 0 |

## Pre-registered gaps → the v3 phase that closes each

| # | case(s) | v2 floor does | should do | gap | closed by |
|---|---|---|---|---|---|
| 1 | `latency_regression`, `saturation_cpu`, `slo_slow_burn` | OBSERVE | ROLLBACK | **recall** — 5xx-only signal coverage | **Phase 1** multi-signal detection (latency p99 + saturation + multi-window burn-rate) |
| 2 | `coincident_dependency_outage` | ROLLBACK (wasted) | ESCALATE | **precision** — no causal grounding | **Phase 2** deploy-ledger causal pre-check |
| 3 | `low_traffic_blip` | ESCALATE | OBSERVE | **false-escalation** — thin-evidence paging | **Phase 3** verifier / better evidence (judgment call: paging policy) |
| 4 | `no_healthy_target` | OBSERVE | ESCALATE | **mishandle** — silent observe when it can't act | Phase 2/3 (judgment call) |

The recall gap is **a signal-coverage gap, not a gate bug**: with 5xx ≈ 0 the business-path sample is
`0/20`, so the Wilson verdict is INCONCLUSIVE and the heuristic sees nothing — neither detector can
fire. Multi-signal fusion (Phase 1) gives the gate something to act on.

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
cd agent && python -m pytest tests/test_bench.py -q   # the regression tests
```
