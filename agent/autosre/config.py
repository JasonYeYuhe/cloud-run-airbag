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

# Graduated autonomy (v2): per-service trust level enforced in the state machine. See autonomy.py.
# L0 observe | L1 approve-before-rollback | L2 auto-rollback+gate-fix-PR | L3 full (default).
AUTONOMY_LEVEL = os.getenv("AIRBAG_AUTONOMY", "L3")
AUTONOMY_PROMOTE_AFTER = int(os.getenv("AIRBAG_AUTONOMY_PROMOTE_AFTER", "5"))  # advisory threshold
APPROVAL_TTL_S = float(os.getenv("AIRBAG_APPROVAL_TTL_S", "3600"))            # pending-approval window

# Durable work queue (v2): inproc (default, FastAPI BackgroundTasks) | cloudtasks. See queue.py.
QUEUE_BACKEND = os.getenv("AIRBAG_QUEUE", "inproc")
CLOUD_TASKS_QUEUE = os.getenv("AIRBAG_TASKS_QUEUE", "airbag-heals")
CLOUD_TASKS_LOCATION = os.getenv("AIRBAG_TASKS_LOCATION", GCP_REGION)
SELF_URL = os.getenv("AIRBAG_SELF_URL", "")  # the agent's own base URL (Cloud Tasks target)
# dedicated credential for the Cloud-Tasks-facing worker (NOT the webhook token — blast radius)
INTERNAL_TOKEN = os.getenv("AIRBAG_INTERNAL_TOKEN", "")
# remote MCP endpoint mounted on the agent (streamable-HTTP at /mcp). Off by default so the standard
# deploy is unchanged. Bearer-gated by its OWN dedicated token (NOT the Cloud-Tasks INTERNAL_TOKEN —
# the MCP mount is a destructive public control plane; keep its blast radius separate). See mcp_remote.py.
MCP_HTTP_ENABLED = _bool("AIRBAG_MCP_HTTP", "false")
MCP_TOKEN = os.getenv("AIRBAG_MCP_TOKEN", "")

# Event bus: inproc (default, single-instance) | pubsub (cross-instance SSE fan-out → enables
# dropping --max-instances=1). See events.py.
EVENTS_BACKEND = os.getenv("AIRBAG_EVENTS", "inproc")
EVENTS_TOPIC = os.getenv("AIRBAG_EVENTS_TOPIC", "airbag-events")
HEAL_LEASE_S = float(os.getenv("AIRBAG_HEAL_LEASE_S", "600"))  # per-incident heal lease (>= worst-case run)
MAX_HEAL_ATTEMPTS = int(os.getenv("AIRBAG_MAX_HEAL_ATTEMPTS", "5"))  # circuit breaker: stop redelivering a deterministically-failing heal

# Durable state store (v2): memory (default) | firestore. See state_store.py.
STATE_BACKEND = os.getenv("AIRBAG_STATE", "memory")
COMPLETE_LEASE_S = float(os.getenv("AIRBAG_COMPLETE_LEASE_S", "300"))  # complete-rollback lock lease
DEDUP_TTL_S = float(os.getenv("AIRBAG_DEDUP_TTL_S", "3600"))           # webhook dedup window

# Storm-safe autonomy (v5 Phase 1.1): a per-SERVICE correlation lease coalesces the N alert
# deliveries for ONE broken service (each carries a DISTINCT Monitoring incident id, so every
# per-incident dedup passes) into a SINGLE heal — the first alert is the leader (runs the heal), the
# rest ATTACH their incident id and ack before any triage (no self-amplifying diagnostic probes).
# The lease holds while the outcome is UNSETTLED (running, or held on a live approval/pending) and
# has a generous TTL as a crash backstop only. Default OFF -> byte-identical to v4. See state_store.
STORM_COALESCE = _bool("AIRBAG_STORM_COALESCE", "false")
# Crash backstop for the correlation lease — must exceed the worst-case heal wall-clock (like
# HEAL_LEASE_S) so a live leader is never taken over mid-run; a settle re-aims the clock explicitly.
SERVICE_HEAL_LEASE_S = float(os.getenv("AIRBAG_SERVICE_HEAL_LEASE_S", "900"))

# Observer-safe diagnostics (v5 Phase 1.2). Every diagnostic/probe request Airbag makes to a target
# carries this marker so its OWN traffic is distinguishable from users'. On 2026-07-02 the causal
# probe's 8 requests against a broken 0%-traffic target produced 8 REAL 5xx that fired the very Cloud
# Monitoring alert being diagnosed. The UA lands in Cloud Run request logs (httpRequest.userAgent), so
# the log-scan detection COUNT + an additive log-based alert metric (infra/alert-setup-v2.sh) can
# EXCLUDE it. (_burst in app.py stays UNMARKED — it SIMULATES USERS; never mark it.) The marker
# identifies ALL of Airbag's target-bound httpx traffic (probes AND local control POSTs), so no
# backend request can be mistaken for a user's — enforced by the probe-marking guard test.
PROBE_UA = os.getenv("AIRBAG_PROBE_UA", "airbag-probe/1")
PROBE_HEADERS = {"User-Agent": PROBE_UA, "X-Airbag-Probe": "1"}
# Exclude Airbag's own marked probe traffic from the log-scan 5xx COUNT (query_error_rate). Default
# OFF so detection/demo stays byte-identical until flipped. HONESTY SCOPE (Gemini review): covers the
# DETECTION/COUNT path only — app-emitted tracebacks (fetch_error_logs) and the built-in console 5xx
# metric are OUT of scope (a probe-triggered traceback is byte-identical to a user one; no decision
# keys on trace COUNTS, and the built-in request_count metric can't filter on a header at all).
SELF_TRAFFIC_EXCLUDE = _bool("AIRBAG_SELF_TRAFFIC_EXCLUDE", "false")

# Approval coalescing + settlement (v5 Phase 1.3). One outage that repeatedly needs a human (the
# storm's step 3: a verify-failure demotes L3->L1, then every subsequent gated heal filed its OWN
# approval card, piling up + expiring silently) collapses to ONE operator card. Gated heals with the
# SAME sha256(service|kind|proposed_target|primary_signal) ATTACH to the open card + bump a count; one
# approve/deny settles ALL attached; a heal that self-resolves sweeps its now-stale card. The signal
# term is a Gemini BLOCKER fix: a 5xx card carries a fix-PR consequence, a latency card must not — so
# different incident CLASSES never merge even when they propose the same target. Also fixes the
# demotion breadcrumb: `demoted_from` + the CAUSING incident are preserved across later L1 failures
# (v2 erased them every record_outcome), cleared only by an explicit re-grant. Default OFF -> v2.
APPROVAL_COALESCE = _bool("AIRBAG_APPROVAL_COALESCE", "false")

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

# Statistical decision gate (v2): the serving revision's sampled 5xx proportion is turned into a
# FAIL/PASS/INCONCLUSIVE verdict (Wilson CI) that gates the rollback — see analyzer.py.
STAT_GATE_ENABLED = _bool("AIRBAG_STAT_GATE", "true")
STAT_SAMPLE_N = int(os.getenv("AIRBAG_STAT_SAMPLE_N", "20"))
STAT_BASELINE_RATE = float(os.getenv("AIRBAG_STAT_BASELINE_RATE", "0.02"))  # TODO: per-service learned baseline
STAT_Z = float(os.getenv("AIRBAG_STAT_Z", "1.96"))                          # 95% CI
STAT_MIN_FAIL_ERRORS = int(os.getenv("AIRBAG_STAT_MIN_FAIL_ERRORS", "3"))

# Multi-signal detection engine (v3 Phase 1). When the statistical gate is on (STAT_GATE_ENABLED),
# AIRBAG_SIGNALS selects the enabled detectors; default "5xx" = today's single-signal behavior (so the
# default path is unchanged). "all" enables every shipped detector. See signals/. STAT_GATE_ENABLED is
# the master switch — false = no stat gate at all (unchanged from v2).
SIGNALS = os.getenv("AIRBAG_SIGNALS", "5xx")
SIGNAL_WINDOWS = int(os.getenv("AIRBAG_SIGNAL_WINDOWS", "4"))               # recent windows read per detection
SIGNAL_DEBOUNCE_WINDOWS = int(os.getenv("AIRBAG_SIGNAL_DEBOUNCE_WINDOWS", "3"))  # a noisy signal must persist N windows
# Latency detector: a request slower than the SLO (last-good p99 x factor, floored at ABS_MS so a fast
# baseline isn't hair-trigger) is "slow"; the per-window slow-proportion is Wilson-gated (same rigor as
# 5xx) vs TOLERANCE, and the window must FAIL for DEBOUNCE_WINDOWS of the last WINDOWS to trigger.
LATENCY_SLO_FACTOR = float(os.getenv("AIRBAG_LATENCY_SLO_FACTOR", "3.0"))       # SLO = baseline p99 x this
LATENCY_SLO_ABS_MS = float(os.getenv("AIRBAG_LATENCY_SLO_ABS_MS", "800"))       # floor for the SLO threshold
LATENCY_SLO_TOLERANCE = float(os.getenv("AIRBAG_LATENCY_SLO_TOLERANCE", "0.05"))  # allowed slow-proportion
LATENCY_MIN_SLOW = int(os.getenv("AIRBAG_LATENCY_MIN_SLOW", "3"))               # min slow reqs/window to FAIL it

# Causal pre-check (v3 Phase 2a; v4 adds the latency axis). Before committing a rollback, probe the
# rollback TARGET's health directly: if the target is ALSO confidently degraded (an external
# dependency/quota outage breaking every revision — or the target itself being broken), landing on
# it is futile → ESCALATE instead of wasting the reversible action. Only a CONFIDENT-unhealthy
# target blocks (Wilson gate over CAUSAL_PROBE_N); transient / ambiguous / probe-error → proceed
# with the rollback (never block a legit rollback). See causal.py.
# Default OFF → the demo is unchanged. STAT_GATE_ENABLED still gates detection separately.
CAUSAL_CHECK_ENABLED = _bool("AIRBAG_CAUSAL_CHECK", "false")
CAUSAL_PROBE_N = int(os.getenv("AIRBAG_CAUSAL_PROBE_N", "8"))               # target-probe samples
CAUSAL_TOLERANCE = float(os.getenv("AIRBAG_CAUSAL_TOLERANCE", "0.05"))      # target error-proportion baseline
CAUSAL_MIN_ERRORS = int(os.getenv("AIRBAG_CAUSAL_MIN_ERRORS", "3"))         # min errors to call it unhealthy

# Forward-only / irreversible-deploy guard (v4 Phase 3). A deploy that performed a forward-only
# change (e.g. a schema migration) DECLARES it with the Cloud Run revision annotation
# `airbag.dev/irreversible=true`; rolling traffic back ACROSS a declared marker would put code that
# can't read the migrated datastore in front of it — strictly worse than the outage. The guard
# HONORS the declared contract (it does NOT detect migrations), fails OPEN (no marker → rollback
# proceeds unchanged), and ships default-OFF so the demo is unchanged. See reversibility.py.
REVERSIBILITY_GUARD_ENABLED = _bool("AIRBAG_REVERSIBILITY_GUARD", "false")
IRREVERSIBLE_ANNOTATION = os.getenv("AIRBAG_IRREVERSIBLE_ANNOTATION", "airbag.dev/irreversible")

# Cross-incident memory + learned per-service baseline (v2). See memory.py.
BASELINE_ALPHA = float(os.getenv("AIRBAG_BASELINE_ALPHA", "0.2"))          # EMA weight for new healthy samples
STAT_BASELINE_FLOOR = float(os.getenv("AIRBAG_STAT_BASELINE_FLOOR", "0.01"))  # learned baseline never below this
RECUR_WINDOW_S = float(os.getenv("AIRBAG_RECUR_WINDOW_S", "3600"))          # recurrence look-back window
RECUR_THRESHOLD = int(os.getenv("AIRBAG_RECUR_THRESHOLD", "5"))             # N incidents in window -> RECURRING
MEMORY_RECENT_MAX = int(os.getenv("AIRBAG_MEMORY_RECENT_MAX", "20"))        # bounded recent-incident history
# Serving-history ledger (v4 Phase 1): max witnessed-healthy revisions kept per service. The
# rollback selector PREFERS a witnessed revision over bare recency; see memory.witness_serving.
WITNESS_MAX = int(os.getenv("AIRBAG_WITNESS_MAX", "10"))

# Witness-freshness horizon + blind-landing visibility (v5 Phase 3.1). Behind AIRBAG_TARGET_EVIDENCE
# (default OFF, and a documented NO-OP unless AIRBAG_CAUSAL_CHECK is also on): (i) a witnessed-healthy
# revision older than WITNESS_FRESH_S is treated as COLD in target selection (last_witnessed_at is
# already stored) — a witness from arbitrarily long ago is not evidence about NOW, so it falls back to
# recency (+ the live causal probe still gates whatever is picked); (ii) on a causal PROBE-ERROR
# against an UNWITNESSED target, _mitigate makes ONE bounded probe retry then PROCEEDS fail-open with
# a first-class blind_landing marker — MEASURED + surfaced, NEVER blocking (the locked v3 "never block
# a legit rollback" posture: a network blip must not abandon users mid-outage — Gemini-review MAJOR fix).
TARGET_EVIDENCE = _bool("AIRBAG_TARGET_EVIDENCE", "false")
WITNESS_FRESH_S = float(os.getenv("AIRBAG_WITNESS_FRESH_S", str(7 * 24 * 3600)))   # 7 days

# Close-time settlement (v5 Phase 3.2, behind AIRBAG_CLOSE_SETTLEMENT, default OFF). A fix that
# survived direct-probed canary 10/50/100 is the STRONGEST evidence Airbag collects, yet CLOSED
# neither witnessed the fix revision nor credited the trust ramp — while the canary-FAIL path DID
# demote (a trust asymmetry). Fixed: CLOSED witnesses the fix revision (memory.witness_serving) and
# credits the trust ramp, WITHOUT double-counting — the mitigate-time record_outcome already counted
# a SUCCESS (persisted as outcome_counted on the pending record), so CLOSED credits only when unset
# (e.g. a verify-fail mitigate that then got fixed — the recovery credit). Canary-fail semantics
# unchanged. Flag OFF -> byte-identical v4.
CLOSE_SETTLEMENT = _bool("AIRBAG_CLOSE_SETTLEMENT", "false")

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

# Sandbox for the LLM-authored regression test (fix_pipeline). See sandbox.py.
#   subprocess (default) — bounded local subprocess in the agent container, metadata server neutralized.
#   cloudrun_job         — isolated, network-egress-disabled Cloud Run Job under a zero-permission SA
#                          (infra/sandbox-job-setup.sh) — the production posture (no LLM code runs in
#                          the prod agent's privileged container). Falls back to subprocess on error.
SANDBOX_BACKEND = os.getenv("AIRBAG_SANDBOX", "subprocess")
SANDBOX_JOB_NAME = os.getenv("AIRBAG_SANDBOX_JOB", "airbag-sandbox")
SANDBOX_JOB_REGION = os.getenv("AIRBAG_SANDBOX_REGION", GCP_REGION)
SANDBOX_JOB_TIMEOUT_S = float(os.getenv("AIRBAG_SANDBOX_TIMEOUT_S", "180"))

# KMS-signed proof bundle (v5 Phase 4.2, behind AIRBAG_PROOF_SIGN, default OFF, FAIL-OPEN: a signing
# failure degrades to today's digest-only bundle, never blocks a heal). At MITIGATED/CLOSED the
# canonical bundle SNAPSHOT is signed via Cloud KMS asymmetricSign (EC_SIGN_P256_SHA256) over
# httpx+ADC (zero new deps — the PyGithub-to-REST precedent). HONESTY: the signature proves
# PROVENANCE (the bundle was produced by the holder of Airbag's KMS identity), NOT the correctness of
# the decisions inside. Offline verifier: scripts/verify-proof.py; infra: infra/kms-setup.sh.
PROOF_SIGN = _bool("AIRBAG_PROOF_SIGN", "false")
# the full KMS key-VERSION resource name to sign with (.../cryptoKeyVersions/N). Empty -> can't sign.
KMS_KEY = os.getenv("AIRBAG_KMS_KEY", "")

# fix-PR slow path (optional). Empty token -> the FIX_PR stage is a no-op note.
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO = os.getenv("GITHUB_REPO", "")          # "owner/repo"
FIX_FILE = os.getenv("AIRBAG_FIX_FILE", "target-app/main.py")
FIX_BASE = os.getenv("AIRBAG_FIX_BASE", "main")

# Fix-path write allowlist (v5 Phase 4.1 — a HARD GATE, not a flag: it mitigates an ACTIVE
# prompt-injection -> workflow-write vulnerability). The fix-PR pipeline commits LLM-CHOSEN file
# paths (the RCA's suspected_file + the regression test_path). A prompt-injected stack trace could
# make Gemini target `.github/workflows/*.yml`, which EXECUTES with repo secrets on push to the very
# airbag/fix* branch being written. Any committed path MUST be inside this allowlist (comma-separated
# directory prefixes; default = the directory of AIRBAG_FIX_FILE). Configurable so a real repo can
# point it at `src/`; hard-enforced (normalized, no `..`, `.github/` rejected UNCONDITIONALLY).
FIX_ALLOWLIST = [d.strip() for d in os.getenv(
    "AIRBAG_FIX_ALLOWLIST", os.path.dirname(FIX_FILE) or ".").split(",") if d.strip()]


def fix_path_allowed(path: str | None) -> bool:
    """HARD GATE (v5 Phase 4.1): may the fix-PR pipeline commit this LLM-chosen path? Rejects absolute
    paths, parent traversal, `.github/` (workflow files execute with repo secrets on push), and
    anything outside FIX_ALLOWLIST. Normalized FIRST so `target-app/../.github/x` can't slip past a
    naive prefix check. Fails CLOSED (unset/non-str/NUL -> rejected)."""
    if not path or not isinstance(path, str) or "\x00" in path or os.path.isabs(path):
        return False
    norm = os.path.normpath(path)
    parts = norm.split(os.sep)
    if norm == ".." or parts[0] == ".." or ".." in parts or ".github" in parts:
        return False
    for allowed in FIX_ALLOWLIST:
        a = os.path.normpath(allowed)
        if a in (".", ""):                                   # allowlist = repo root -> any safe path
            return True
        if norm == a or norm.startswith(a + os.sep):
            return True
    return False
