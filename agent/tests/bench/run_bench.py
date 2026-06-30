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

BASELINE_JSON = _HERE.parent / "baseline_scorecard.json"


def main() -> int:
    write = "--write" in sys.argv
    results = run_bench()
    card = score(results)
    md = card.to_markdown()
    print(md)
    print()
    if write:
        BASELINE_JSON.write_text(json.dumps(card.to_dict(), indent=2) + "\n", encoding="utf-8")
        print(f"[wrote] {BASELINE_JSON}")
    else:
        print("(run with --write to update the committed baseline_scorecard.json)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
