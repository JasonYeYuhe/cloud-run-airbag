#!/usr/bin/env bash
# One-command deploy of Airbag to Cloud Run. Run `gcloud auth login` once first.
#   PROJECT=your-proj REGION=asia-northeast1 ./deploy.sh
# Captures every cross-service gotcha learned during the real deploy (see comments).
set -euo pipefail
: "${PROJECT:?set PROJECT=your-gcp-project}"
REGION="${REGION:-asia-northeast1}"
ROOT="$(cd "$(dirname "$0")" && pwd)"
SA="airbag-agent@${PROJECT}.iam.gserviceaccount.com"
PNUM="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"
COMPUTE_SA="${PNUM}-compute@developer.gserviceaccount.com"
gcloud config set project "$PROJECT" >/dev/null

echo "== enable APIs =="
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  monitoring.googleapis.com logging.googleapis.com secretmanager.googleapis.com

echo "== agent service account + least-priv roles =="
gcloud iam service-accounts create airbag-agent --display-name="Airbag self-heal agent" 2>/dev/null || true
# datastore.user: durable state store (AIRBAG_STATE=firestore). cloudtasks.enqueuer: durable work
# queue (AIRBAG_QUEUE=cloudtasks) enqueues self-heal tasks.
for R in run.admin monitoring.viewer logging.viewer secretmanager.secretAccessor datastore.user cloudtasks.enqueuer; do
  gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:${SA}" --role="roles/$R" --condition=None -q >/dev/null
done
# GOTCHA 1: new projects' default compute SA lacks build perms -> source deploys fail.
gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:${COMPUTE_SA}" \
  --role="roles/cloudbuild.builds.builder" --condition=None -q >/dev/null
# GOTCHA 2: the agent must actAs the target's runtime SA to update its traffic (rollback).
gcloud iam service-accounts add-iam-policy-binding "$COMPUTE_SA" \
  --member="serviceAccount:${SA}" --role="roles/iam.serviceAccountUser" -q >/dev/null

echo "== compute demo + webhook tokens (real secrets — never hardcode in this public repo) =="
# demo token: gates /demo/* once public (operator can trigger; public dashboard is watch-only).
# webhook token: gates /alerts/* and /internal/complete-rollback (Cloud Monitoring + the fix CI).
mk_secret() {  # name, value
  printf '%s' "$2" | gcloud secrets create "$1" --data-file=- 2>/dev/null \
    || printf '%s' "$2" | gcloud secrets versions add "$1" --data-file=-
  gcloud secrets add-iam-policy-binding "$1" --member="serviceAccount:${SA}" \
    --role=roles/secretmanager.secretAccessor -q >/dev/null
}
DEMO_TOKEN="$(grep '^AIRBAG_DEMO_TOKEN=' "$ROOT/agent/.env" 2>/dev/null | cut -d= -f2-)"
[ -z "$DEMO_TOKEN" ] && DEMO_TOKEN="$(openssl rand -hex 16)"
WEBHOOK_TOKEN="$(grep '^AIRBAG_WEBHOOK_TOKEN=' "$ROOT/agent/.env" 2>/dev/null | cut -d= -f2-)"
[ -z "$WEBHOOK_TOKEN" ] && WEBHOOK_TOKEN="$(openssl rand -hex 24)"
# dedicated credential for the Cloud-Tasks-facing /internal/run-heal worker (distinct from the webhook token)
INTERNAL_TOKEN="$(grep '^AIRBAG_INTERNAL_TOKEN=' "$ROOT/agent/.env" 2>/dev/null | cut -d= -f2-)"
[ -z "$INTERNAL_TOKEN" ] && INTERNAL_TOKEN="$(openssl rand -hex 24)"
# dedicated token for the remote MCP control plane (separate blast radius from the Cloud-Tasks token)
MCP_TOKEN="$(grep '^AIRBAG_MCP_TOKEN=' "$ROOT/agent/.env" 2>/dev/null | cut -d= -f2-)"
[ -z "$MCP_TOKEN" ] && MCP_TOKEN="$(openssl rand -hex 24)"
AURL="https://airbag-agent-${PNUM}.${REGION}.run.app"   # the agent's own URL (Cloud Tasks target)

# AGENT_ONLY=1 redeploys just the agent (skips the target) — use it to ship an agent code change
# WITHOUT creating a new target revision, which would land ABOVE the demo baseline's HEALTHY (newest)
# revision and break the "healthy is newest" rollback-target invariant (see scripts/gcp-demo-setup.sh),
# forcing a re-run of that script.
if [ "${AGENT_ONLY:-}" != "1" ]; then
  echo "== deploy target-app (healthy baseline; FAULT_MODE=off; /__fault gated by FAULT_TOKEN) =="
  # Explicit FAULT_MODE=off: scripts/gcp-demo-setup.sh later flips the service spec to FAULT_MODE=bug
  # for the bad revision, so a plain redeploy must re-assert healthy. FAULT_TOKEN stops anyone on the
  # public internet from toggling the runtime fault (griefing the demo); the gcp demo uses revision
  # routing, not /__fault, so this never gets in the way.
  gcloud run deploy airbag-target --source "$ROOT/target-app" --region "$REGION" \
    --allow-unauthenticated --update-env-vars "FAULT_MODE=off,FAULT_TOKEN=${DEMO_TOKEN}" -q
else
  echo "== AGENT_ONLY=1 — skipping target redeploy (demo baseline preserved) =="
fi
TURL="$(gcloud run services describe airbag-target --region "$REGION" --format='value(status.url)')"

echo "== tokens -> Secret Manager =="
mk_secret airbag-demo-secret "$DEMO_TOKEN"
mk_secret airbag-webhook-secret "$WEBHOOK_TOKEN"
mk_secret airbag-internal-token "$INTERNAL_TOKEN"
mk_secret airbag-mcp-token "$MCP_TOKEN"

echo "== Cloud Tasks queue (durable work queue; only used when AIRBAG_QUEUE=cloudtasks) =="
# --max-attempts bounds retries at the infra level (belt-and-suspenders with the app's
# MAX_HEAL_ATTEMPTS circuit breaker) so a deterministically-failing heal can't loop forever.
gcloud tasks queues create airbag-heals --location="$REGION" \
  --max-attempts=5 --max-retry-duration=1800s 2>/dev/null \
  || gcloud tasks queues update airbag-heals --location="$REGION" \
       --max-attempts=5 --max-retry-duration=1800s 2>/dev/null \
  || echo "  (queue airbag-heals already exists)"

echo "== gemini key -> Secret Manager =="
if [ -f "$ROOT/agent/.env" ]; then
  KEY="$(grep '^GEMINI_API_KEY=' "$ROOT/agent/.env" | cut -d= -f2-)"
  printf '%s' "$KEY" | gcloud secrets create airbag-gemini-key --data-file=- 2>/dev/null \
    || printf '%s' "$KEY" | gcloud secrets versions add airbag-gemini-key --data-file=-
  gcloud secrets add-iam-policy-binding airbag-gemini-key --member="serviceAccount:${SA}" \
    --role=roles/secretmanager.secretAccessor -q >/dev/null
fi

# AIRBAG_SELF_URL + AIRBAG_TASKS_* let cloudtasks mode work; AIRBAG_QUEUE is left UNSET (=inproc
# default) so the standard deploy is unchanged. Flip to cloudtasks with --update-env-vars AIRBAG_QUEUE=cloudtasks.
# Multi-instance: durable state (AIRBAG_STATE=firestore) + a cross-instance event bus
# (AIRBAG_EVENTS=pubsub fan-out) make scale-out safe, so the agent runs --max-instances 3.
# AIRBAG_QUEUE + AIRBAG_MCP_HTTP are left UNSET (inproc + MCP off) — built + tested opt-in flags,
# kept off in the demo for simplicity + a small attack surface.
# --- v5 flag posture (LIVE-VERIFIED 2026-07-04 on airbag-hack-260628; signed proof in docs/proof/) ---
# Storm-safe + provenance flags ON per V5_VISION §8 Q1/Q2 (their documented default is ON *after* live
# verify, which is now done):
#   AIRBAG_STORM_COALESCE=1       — N alert deliveries for ONE outage coalesce to ONE heal
#                                   (verified live: 5 distinct-id alerts -> 1 leader + 4 attached).
#   AIRBAG_SELF_TRAFFIC_EXCLUDE=1 — the log-scan detection COUNT excludes Airbag's own probe UA
#                                   (verified live: 10 marked probe 5xx excluded, 10 user 5xx kept).
#   AIRBAG_PROOF_SIGN=1 + AIRBAG_KMS_KEY — Cloud KMS-sign the proof bundle (verified offline, incl. a
#                                   tamper negative-control). FAIL-OPEN: a missing key / KMS hiccup
#                                   degrades to digest-only, so run infra/kms-setup.sh FIRST to create
#                                   the key (a fresh clone without it simply signs nothing — never blocks).
#   AIRBAG_REVISION_DELTA=1       — attach the LLM-free "what changed" spec diff to record/report/proof.
# AIRBAG_SIGNALS is "5xx,latency" (NOT "all"): the burn detector (5.1) stays opt-in + CI-ratcheted but
# is OFF live — its 300-sample burst makes the SLOW-fault latency heal ~10min (the 5xx demo is
# unaffected). We now pass env vars with a "^@^" CUSTOM DELIMITER so the comma inside "5xx,latency" (and
# any future comma value) is not mis-split by gcloud. AIRBAG_CAUSAL_CHECK=1 probes the rollback target's
# health before committing. All of these are deterministic — the action tier never calls the LLM.
ENVS="AIRBAG_BACKEND=gcp@GOOGLE_CLOUD_PROJECT=${PROJECT}@GOOGLE_CLOUD_LOCATION=${REGION}@TARGET_SERVICE=airbag-target@TARGET_BASE_URL=${TURL}@AIRBAG_SIGNALS=5xx,latency@AIRBAG_CAUSAL_CHECK=1@AIRBAG_STORM_COALESCE=1@AIRBAG_SELF_TRAFFIC_EXCLUDE=1@AIRBAG_PROOF_SIGN=1@AIRBAG_KMS_KEY=projects/${PROJECT}/locations/${REGION}/keyRings/airbag/cryptoKeys/airbag-proof/cryptoKeyVersions/1@AIRBAG_REVISION_DELTA=1@AIRBAG_VERIFY_INTERVAL_S=4@AIRBAG_VERIFY_ATTEMPTS=8@AIRBAG_STATE=firestore@AIRBAG_EVENTS=pubsub@AIRBAG_SELF_URL=${AURL}@AIRBAG_TASKS_QUEUE=airbag-heals@AIRBAG_TASKS_LOCATION=${REGION}"
SECRETS="GEMINI_API_KEY=airbag-gemini-key:latest,AIRBAG_DEMO_TOKEN=airbag-demo-secret:latest,AIRBAG_WEBHOOK_TOKEN=airbag-webhook-secret:latest,AIRBAG_INTERNAL_TOKEN=airbag-internal-token:latest,AIRBAG_MCP_TOKEN=airbag-mcp-token:latest"

# Optional fix-PR slow path: use a FINE-GRAINED, repo-scoped GitHub token (Contents +
# Pull requests: write). Never put a broad classic token in this public service.
if grep -q '^GITHUB_TOKEN=..' "$ROOT/agent/.env" 2>/dev/null; then
  echo "== github token -> Secret Manager =="
  GHT="$(grep '^GITHUB_TOKEN=' "$ROOT/agent/.env" | cut -d= -f2-)"
  printf '%s' "$GHT" | gcloud secrets create airbag-github-token --data-file=- 2>/dev/null \
    || printf '%s' "$GHT" | gcloud secrets versions add airbag-github-token --data-file=-
  gcloud secrets add-iam-policy-binding airbag-github-token --member="serviceAccount:${SA}" \
    --role=roles/secretmanager.secretAccessor -q >/dev/null
  ENVS="${ENVS}@GITHUB_REPO=$(grep '^GITHUB_REPO=' "$ROOT/agent/.env" | cut -d= -f2-)"  # @ = the ^@^ delimiter
  SECRETS="${SECRETS},GITHUB_TOKEN=airbag-github-token:latest"
fi

echo "== deploy agent =="
# GOTCHA 3: the background self-heal needs CPU always allocated (--no-cpu-throttling),
# else CPU is throttled after the 202 response and the heal stalls.
# Multi-instance is safe now: state is durable (Firestore) + idempotent (per-incident leases) and
# events fan out cross-instance (Pub/Sub), so --max-instances 3 scales out without splitting state
# or the dashboard stream. --min-instances 1 keeps it warm. --timeout 3600 for long-lived SSE.
# NB: the (off-by-default) remote MCP control plane keeps its session manager IN-PROCESS, so if you
# enable AIRBAG_MCP_HTTP=on, pin --max-instances 1.
gcloud run deploy airbag-agent --source "$ROOT/agent" --region "$REGION" \
  --service-account "$SA" --allow-unauthenticated --min-instances 1 --max-instances 3 \
  --no-cpu-throttling --timeout 3600 --set-env-vars "^@^$ENVS" --update-secrets "$SECRETS" -q

AURL="$(gcloud run services describe airbag-agent --region "$REGION" --format='value(status.url)')"
echo
echo "Agent:           $AURL"
echo "Target:          $TURL"
echo "Dashboard:       open $AURL and paste the demo token (in agent/.env, or Secret Manager"
echo "                 airbag-demo-secret) into the token field — or use ${AURL}/?token=<demo-token>"
# (token intentionally not printed here so it doesn't land in shared CI/terminal logs)
