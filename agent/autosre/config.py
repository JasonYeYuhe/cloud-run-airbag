"""Runtime configuration, read from environment (see .env.example)."""
import os


def _bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


USE_MOCK = _bool("AIRBAG_USE_MOCK", "true")
WEBHOOK_TOKEN = os.getenv("AIRBAG_WEBHOOK_TOKEN", "")
SENTRY_SECRET = os.getenv("SENTRY_WEBHOOK_SECRET", "")

GCP_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "")
GCP_REGION = os.getenv("GOOGLE_CLOUD_LOCATION", "asia-northeast1")
TARGET_SERVICE = os.getenv("TARGET_SERVICE", "airbag-target")

GEMINI_DECISION_MODEL = os.getenv("GEMINI_DECISION_MODEL", "gemini-2.5-flash")
GEMINI_PATCH_MODEL = os.getenv("GEMINI_PATCH_MODEL", "gemini-2.5-pro")

# Rollback decision gate
CONFIDENCE_THRESHOLD = float(os.getenv("AIRBAG_CONFIDENCE_THRESHOLD", "0.7"))
ERROR_RATE_THRESHOLD = float(os.getenv("AIRBAG_ERROR_RATE_THRESHOLD", "0.05"))
