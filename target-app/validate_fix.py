"""CI gate for Airbag's auto-authored fix PRs (run only on `airbag/fix**` branches).

Fails if the planted KeyError is still present — i.e. if total_revenue() still raises in the
"buggy" path. This is what lets the agent's CI self-correction loop know its fix is wrong:
a red check here -> Gemini re-fixes -> re-validate. NOT run on main (main keeps the gated
demo bug on purpose), so main's CI stays green.
"""
import sys

from main import ORDERS, total_revenue

try:
    total = total_revenue(ORDERS, buggy=True)  # the path the bad revision hit
except Exception as e:  # noqa: BLE001
    print(f"FIX NOT APPLIED — total_revenue still raises in the buggy path: "
          f"{type(e).__name__}: {e}")
    sys.exit(1)

print(f"fix verified — total_revenue is safe in all modes (revenue={total})")
sys.exit(0)
