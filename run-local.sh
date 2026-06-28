#!/usr/bin/env bash
# Live local demo: starts target-app (:8081) + agent+dashboard (:8080).
# The whole self-heal loop runs for real over HTTP — no GCP needed.
#   ./run-local.sh   then open http://localhost:8080  and click "Run demo".
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
export AIRBAG_BACKEND="${AIRBAG_BACKEND:-local}"
export TARGET_BASE_URL="${TARGET_BASE_URL:-http://localhost:8081}"

echo "→ setting up venvs (first run installs deps)…"
python3 -m venv "$ROOT/.venv-demo"
PY="$ROOT/.venv-demo/bin"
"$PY/pip" -q install -r "$ROOT/agent/requirements-local.txt"

echo "→ starting target-app on :8081"
( cd "$ROOT/target-app" && PORT=8081 "$PY/uvicorn" main:app --port 8081 --log-level warning ) &
TPID=$!
echo "→ starting agent + dashboard on :8080"
( cd "$ROOT/agent" && "$PY/uvicorn" app:app --port 8080 --log-level warning ) &
APID=$!
trap "kill $TPID $APID 2>/dev/null || true" EXIT

echo "→ waiting for the agent to be ready (first run installs deps, ~30-60s)…"
for _ in $(seq 1 60); do
  curl -fsS -o /dev/null http://localhost:8080/health 2>/dev/null && break || sleep 1
done
echo
echo "  ✅ Dashboard:  http://localhost:8080"
echo "     (click 'Run demo' — or: curl -XPOST localhost:8080/demo/run)"
echo "  Ctrl-C to stop."
wait
