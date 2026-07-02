"""Airbag-Bench scorecard — turn replayed CaseResults into the reproducible decision-quality metrics.

Metrics are keyed on ``decided_action`` (the DECISION event) for precision/recall/false-rates, and on
terminal ``status=='mitigated'`` for mean-stages-to-mitigate. Every ratio carries its raw
numerator/denominator; a zero denominator renders as ``n/a`` (NOT 0.0, NOT NaN) and is excluded from
any pass/fail gate, so a future phase that decides no rollbacks neither crashes nor falsely passes.
"""
from __future__ import annotations

from dataclasses import dataclass, field

ACTIONS = ("ROLLBACK", "OBSERVE", "ESCALATE")


@dataclass(frozen=True)
class Ratio:
    num: int
    den: int

    @property
    def value(self) -> float | None:
        return (self.num / self.den) if self.den else None

    def fmt(self) -> str:
        return "n/a (0/0)" if not self.den else f"{self.value:.1%} ({self.num}/{self.den})"

    def as_dict(self) -> dict:
        return {"num": self.num, "den": self.den,
                "value": round(self.value, 4) if self.value is not None else None}


@dataclass
class Scorecard:
    label: str
    n: int
    rollback_precision: Ratio
    rollback_recall: Ratio
    false_rollback_rate: Ratio
    false_escalation_rate: Ratio
    wasted_rollback_rate: Ratio
    accuracy: Ratio
    target_correctness: Ratio            # v4: of the ROLLBACK DECISIONS on cases that pin an
                                         # expected_target, how many AIMED at the right revision
                                         # (decided-keyed: a wrong aim the causal veto stops
                                         # pre-shift still counts as a wrong aim — the metric
                                         # scores the SELECTOR, not the downstream safety net)
    mean_stages_to_mitigate: float | None
    confusion: dict                      # expected -> {decided -> count}
    per_case: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "label": self.label, "n": self.n,
            "rollback_precision": self.rollback_precision.as_dict(),
            "rollback_recall": self.rollback_recall.as_dict(),
            "false_rollback_rate": self.false_rollback_rate.as_dict(),
            "false_escalation_rate": self.false_escalation_rate.as_dict(),
            "wasted_rollback_rate": self.wasted_rollback_rate.as_dict(),
            "accuracy": self.accuracy.as_dict(),
            "target_correctness": self.target_correctness.as_dict(),
            "mean_stages_to_mitigate": (round(self.mean_stages_to_mitigate, 2)
                                        if self.mean_stages_to_mitigate is not None else None),
            "confusion": self.confusion,
            "per_case": self.per_case,
        }

    def to_markdown(self) -> str:
        lines = [
            f"### Airbag-Bench scorecard — {self.label}",
            "",
            f"_{self.n} labeled cases. Scores the **v2 deterministic floor (LLM off)** — a LOWER "
            "BOUND on the live Gemini/ADK path, not a measurement of it._",
            "",
            "| Metric | Value | What it means |",
            "|---|---|---|",
            f"| Rollback precision | {self.rollback_precision.fmt()} | of the rollbacks Airbag "
            "decided, how many were warranted |",
            f"| Rollback recall | {self.rollback_recall.fmt()} | of the bad deploys that warranted "
            "rollback, how many Airbag caught |",
            f"| False-rollback rate | {self.false_rollback_rate.fmt()} | rolled back when it should "
            "not have (of all cases) |",
            f"| False-escalation rate | {self.false_escalation_rate.fmt()} | paged a human on a "
            "benign case (of OBSERVE-expected cases) |",
            f"| Wasted-rollback rate | {self.wasted_rollback_rate.fmt()} | rolled back, the rollback "
            "did not clear, then escalated (of all cases) |",
            f"| Accuracy | {self.accuracy.fmt()} | decided == ground-truth action |",
            f"| Target correctness | {self.target_correctness.fmt()} | of the ROLLBACK decisions "
            "on target-pinned cases, how many AIMED at the ground-truth revision (v4: scores the "
            "SELECTOR — a wrong aim counts even when the causal veto stops it pre-shift) |",
            f"| Mean stages-to-mitigate | "
            f"{self.mean_stages_to_mitigate if self.mean_stages_to_mitigate is not None else 'n/a'} | "
            "FSM stages emitted on a successful mitigation |",
            "",
            "**Confusion matrix** (rows = ground truth, cols = decided):",
            "",
            "| expected ↓ / decided → | ROLLBACK | OBSERVE | ESCALATE |",
            "|---|---|---|---|",
        ]
        for exp in ACTIONS:
            row = self.confusion.get(exp, {})
            lines.append(f"| {exp} | {row.get('ROLLBACK', 0)} | {row.get('OBSERVE', 0)} | "
                         f"{row.get('ESCALATE', 0)} |")
        lines += ["", "**Per case:** (final = what Airbag ultimately did; scoring keys off it. "
                  "target = chosen → ✓/✗ vs the pinned expected target, with the selection source)", "",
                  "| case | category | expected | final | target | status | stages |",
                  "|---|---|---|---|---|---|---|"]
        for c in self.per_case:
            final = c.get("final", c["decided"])
            mark = "✓" if final == c["expected"] else "✗"
            note = "" if final == c["decided"] else f" (decided {c['decided']})"
            lines.append(f"| {c['name']} | {c['category']} | {c['expected']} | {final} {mark}{note} "
                         f"| {_target_cell(c)} | {c['status']} | {c['stages']} |")
        return "\n".join(lines)


def _target_cell(c: dict) -> str:
    """Markdown cell for the per-case target column: '—' when no target was pinned/chosen."""
    chosen, expected = c.get("chosen_target"), c.get("expected_target")
    if not expected and not chosen:
        return "—"
    if not chosen:
        return f"— (expected {expected})"
    mark = "" if not expected else (" ✓" if chosen == expected else f" ✗ (expected {expected})")
    src = f" [{c['target_source']}]" if c.get("target_source") else ""
    return f"{chosen}{mark}{src}"


def score(results, label: str = "v2 deterministic floor (LLM off)") -> Scorecard:
    # Score off final_action (what Airbag ULTIMATELY did — a ROLLBACK only if traffic actually
    # shifted), so a causal pre-check that escalates BEFORE the shift is honestly counted as an
    # ESCALATE, not a rollback. Equivalent to decided_action whenever the causal check is off.
    n = len(results)
    confusion: dict = {e: {d: 0 for d in ACTIONS} for e in ACTIONS}
    for r in results:
        confusion.setdefault(r.expected_action, {a: 0 for a in ACTIONS})
        confusion[r.expected_action][r.final_action] = \
            confusion[r.expected_action].get(r.final_action, 0) + 1

    rolled_back = [r for r in results if r.final_action == "ROLLBACK"]
    expected_rb = [r for r in results if r.expected_action == "ROLLBACK"]
    expected_obs = [r for r in results if r.expected_action == "OBSERVE"]
    correct_rb = [r for r in rolled_back if r.expected_action == "ROLLBACK"]
    false_rb = [r for r in rolled_back if r.expected_action != "ROLLBACK"]
    false_esc = [r for r in expected_obs if r.final_action == "ESCALATE"]
    wasted_rb = [r for r in rolled_back if r.status == "escalated" and not r.cleared]
    correct = [r for r in results if r.final_action == r.expected_action]
    mitigated = [r for r in results if r.status == "mitigated"]
    mean_stages = (sum(r.stages for r in mitigated) / len(mitigated)) if mitigated else None
    # v4 TARGET correctness — DECIDED-keyed (the Gemini review caught the executed-keyed version
    # reading 100% in causal mode purely because the veto stopped the wrong aim pre-shift): of the
    # ROLLBACK decisions on cases that PIN an expected target, how many AIMED at the ground-truth
    # revision. This scores the SELECTOR; whether the aim was then executed, vetoed, or wasted is
    # the action metrics' job.
    aimed = [r for r in results if r.decided_action == "ROLLBACK" and r.expected_target]
    aimed_right = [r for r in aimed if r.chosen_target == r.expected_target]

    per_case = [{"name": r.name, "category": r.category, "expected": r.expected_action,
                 "decided": r.decided_action, "final": r.final_action, "status": r.status,
                 "stages": r.stages, "expected_target": r.expected_target,
                 "chosen_target": r.chosen_target, "target_source": r.target_source}
                for r in results]

    return Scorecard(
        label=label, n=n,
        rollback_precision=Ratio(len(correct_rb), len(rolled_back)),
        rollback_recall=Ratio(len(correct_rb), len(expected_rb)),
        false_rollback_rate=Ratio(len(false_rb), n),
        false_escalation_rate=Ratio(len(false_esc), len(expected_obs)),
        wasted_rollback_rate=Ratio(len(wasted_rb), n),
        accuracy=Ratio(len(correct), n),
        target_correctness=Ratio(len(aimed_right), len(aimed)),
        mean_stages_to_mitigate=mean_stages,
        confusion=confusion, per_case=per_case)
