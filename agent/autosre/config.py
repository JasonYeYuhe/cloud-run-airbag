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

GCP_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "")
GCP_REGION = os.getenv("GOOGLE_CLOUD_LOCATION", "asia-northeast1")
TARGET_SERVICE = os.getenv("TARGET_SERVICE", "airbag-target")

# local backend: where the target-app is reachable
TARGET_BASE_URL = os.getenv("TARGET_BASE_URL", "http://localhost:8081")
ERROR_SAMPLE_N = int(os.getenv("AIRBAG_ERROR_SAMPLE_N", "12"))
# probe the business path (not /healthz, which can stay 200 during a fault) so the
# "verified recovered" signal actually proves the failing endpoint is healthy again.
PROBE_PATH = os.getenv("AIRBAG_PROBE_PATH", "/api/orders")

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
