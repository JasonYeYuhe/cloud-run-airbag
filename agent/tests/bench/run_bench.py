"""Airbag-Bench CLI — replay the corpus, print the scorecard, and (with --write) commit the
baseline JSON + the scorecard section of docs/AIRBAG_BENCH.md.

Run from the agent/ dir:   python tests/bench/run_bench.py
Write the committed baseline: python tests/bench/run_bench.py --write
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# --- path bootstrap (works under direct execution; pytest handles its own path) ---
_HERE = Path(__file__).resolve()
_AGENT = _HERE.parents[2]          # .../agent  (for `import autosre`)
_TESTS = _HERE.parents[1]          # .../agent/tests  (so `bench` resolves as a package)
for p in (str(_AGENT), str(_TESTS)):
    if p not in sys.path:
        sys.path.insert(0, p)

from bench.harness import run_bench          # noqa: E402
from bench.scorecard import score            # noqa: E402

def _arg(flag: str, default=None):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        return sys.argv[i + 1] if i + 1 < len(sys.argv) else default
    return default


def _scorecard_path(signals: str | None, causal: bool):
    """The committed scorecard file for a (signals, causal) config."""
    multi = bool(signals) and signals != "5xx"
    name = ({(False, False): "baseline_scorecard.json",
             (True, False): "multisignal_scorecard.json",
             (False, True): "causal_5xx_scorecard.json",
             (True, True): "causal_scorecard.json"})[(multi, causal)]
    return _HERE.parent / name


def main() -> int:
    write = "--write" in sys.argv
    signals = _arg("--signals")                      # e.g. --signals 5xx,latency
    causal = "--causal" in sys.argv
    label = (f"signals={signals or '5xx'}" + (" +causal" if causal else "")) or "5xx floor (LLM off)"
    card = score(run_bench(signals=signals, causal=causal), label=label)
    print(card.to_markdown())
    print()
    if write:
        out = _scorecard_path(signals, causal)
        out.write_text(json.dumps(card.to_dict(), indent=2) + "\n", encoding="utf-8")
        print(f"[wrote] {out}")
    else:
        tip = " ".join(filter(None, [f"--signals {signals}" if signals else "", "--causal" if causal else "", "--write"]))
        print(f"(run with `{tip}` to update the committed scorecard)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
