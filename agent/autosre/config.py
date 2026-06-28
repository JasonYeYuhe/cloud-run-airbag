"""Runtime configuration, read from environment (see .env.example)."""
import os
from pathlib import Path


def _load_dotenv() -> None:
    """Minimal .env loader (agent/.env) — no dependency; existing env wins."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()


def _bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


# Execution backend: how the agent observes/acts on the world.
#   mock  — in-memory, zero deps (CI/tests)
#   local — real HTTP against a locally-running target-app (demo without GCP)
#   gcp   — real Cloud Run via run_v2 + Cloud Monitoring (needs gcloud auth)
BACKEND = os.getenv("AIRBAG_BACKEND", "mock").strip().lower()
# Back-compat: AIRBAG_USE_MOCK=false used to mean "real".
if _bool("AIRBAG_USE_MOCK", "true") is False and BACKEND == "mock":
    BACKEND = "gcp"
USE_MOCK = BACKEND == "mock"

WEBHOOK_TOKEN = os.getenv("AIRBAG_WEBHOOK_TOKEN", "")
SENTRY_SECRET = os.getenv("SENTRY_WEBHOOK_SECRET", "")
# Shared token gating the /demo/* ACTION endpoints (inject/break/heal/trigger/run/reset).
# Empty -> demo endpoints are open (convenient for the local demo). Set in prod so the
# public dashboard can be watched read-only, but only an operator holding the token can
# trigger Gemini + GitHub actions (prevents PR/cost spam once the service is public).
DEMO_TOKEN = os.getenv("AIRBAG_DEMO_TOKEN", "")

GCP_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "")
GCP_REGION = os.getenv("GOOGLE_CLOUD_LOCATION", "asia-northeast1")
TARGET_SERVICE = os.getenv("TARGET_SERVICE", "airbag-target")

# local backend: where the target-app is reachable
TARGET_BASE_URL = os.getenv("TARGET_BASE_URL", "http://localhost:8081")
# demo harness (gcp): after 'break' shifts traffic to the bad revision, generate this many
# requests to create real 5xx in Cloud Logging, then wait this long for ingestion before
# the agent heals (the one-click /demo/run path). Tunable for the live demo.
DEMO_BURST_N = int(os.getenv("AIRBAG_DEMO_BURST_N", "30"))
# Short delay before the one-click /demo/run heals — gcp triage actively samples the business
# path, so detection no longer waits on Cloud Logging ingestion (just let the traffic shift settle).
DEMO_HEAL_DELAY_S = float(os.getenv("AIRBAG_DEMO_HEAL_DELAY_S", "8"))
ERROR_SAMPLE_N = int(os.getenv("AIRBAG_ERROR_SAMPLE_N", "12"))
# probe the business path (not /healthz, which can stay 200 during a fault) so the
# "verified recovered" signal actually proves the failing endpoint is healthy again.
PROBE_PATH = os.getenv("AIRBAG_PROBE_PATH", "/api/orders")

# Route the decision through the ADK SequentialAgent (triage->decide; Gemini calls the
# tools itself) when a Gemini key is present. Falls back to a direct Gemini decision, then
# a heuristic, on any failure. Set AIRBAG_USE_ADK=false to use the direct Gemini path.
USE_ADK = _bool("AIRBAG_USE_ADK", "true")

# Gemini (AI Studio API key). Empty -> deterministic fallback decision.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_AI_API_KEY") or ""
# GA gemini-2.5-flash is the reliable default (the 3.x/"-latest" aliases were 503/429
# on the free tier when tested 2026-06-28). Override via env to use a newer model.
GEMINI_DECISION_MODEL = os.getenv("GEMINI_DECISION_MODEL", "gemini-2.5-flash")
GEMINI_PATCH_MODEL = os.getenv("GEMINI_PATCH_MODEL", "gemini-2.5-pro")

CONFIDENCE_THRESHOLD = float(os.getenv("AIRBAG_CONFIDENCE_THRESHOLD", "0.7"))
ERROR_RATE_THRESHOLD = float(os.getenv("AIRBAG_ERROR_RATE_THRESHOLD", "0.05"))

# verify loop
VERIFY_ATTEMPTS = int(os.getenv("AIRBAG_VERIFY_ATTEMPTS", "6"))
VERIFY_INTERVAL_S = float(os.getenv("AIRBAG_VERIFY_INTERVAL_S", "2"))
# P1 close-the-transaction: cap failed undo/compensation retries (then require a human)
MAX_UNDO_ATTEMPTS = int(os.getenv("AIRBAG_MAX_UNDO_ATTEMPTS", "2"))
# Gradual canary on RESTORE: percent of traffic to the fix at each gated step. "100" = single
# flip. The stop-the-bleeding rollback stays an instant 100% flip (you want to stop bleeding fast).
CANARY_STAGES = [int(x) for x in os.getenv("AIRBAG_CANARY_STAGES", "10,50,100").split(",") if x.strip()]

# CI self-correction: after the fix PR opens, watch its CI; if red, feed the failure back to
# Gemini, re-commit a correction to the branch, retry up to N times, then escalate (PR comment).
CI_SELF_CORRECT = _bool("AIRBAG_CI_SELF_CORRECT", "true")
MAX_CI_RETRIES = int(os.getenv("AIRBAG_MAX_CI_RETRIES", "2"))
CI_POLL_INTERVAL_S = float(os.getenv("AIRBAG_CI_POLL_INTERVAL_S", "15"))
CI_POLL_TIMEOUT_S = float(os.getenv("AIRBAG_CI_POLL_TIMEOUT_S", "300"))

# fix-PR slow path (optional). Empty token -> the FIX_PR stage is a no-op note.
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO = os.getenv("GITHUB_REPO", "")          # "owner/repo"
FIX_FILE = os.getenv("AIRBAG_FIX_FILE", "target-app/main.py")
FIX_BASE = os.getenv("AIRBAG_FIX_BASE", "main")
