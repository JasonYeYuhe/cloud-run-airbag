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

echo "== deploy target-app (healthy baseline; FAULT_MODE=off; /__fault gated by FAULT_TOKEN) =="
# Explicit FAULT_MODE=off: scripts/gcp-demo-setup.sh later flips the service spec to FAULT_MODE=bug
# for the bad revision, so a plain redeploy must re-assert healthy. FAULT_TOKEN stops anyone on the
# public internet from toggling the runtime fault (griefing the demo); the gcp demo uses revision
# routing, not /__fault, so this never gets in the way.
gcloud run deploy airbag-target --source "$ROOT/target-app" --region "$REGION" \
  --allow-unauthenticated --update-env-vars "FAULT_MODE=off,FAULT_TOKEN=${DEMO_TOKEN}" -q
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
# AIRBAG_STATE=firestore (durable state — survives recycles; single-instance, the SSE bus is
# in-process). AIRBAG_QUEUE + AIRBAG_MCP_HTTP are left UNSET (inproc + MCP off) — both are built +
# tested opt-in flags, kept off in the demo for simplicity + a small attack surface.
ENVS="AIRBAG_BACKEND=gcp,GOOGLE_CLOUD_PROJECT=${PROJECT},GOOGLE_CLOUD_LOCATION=${REGION},TARGET_SERVICE=airbag-target,TARGET_BASE_URL=${TURL},AIRBAG_VERIFY_INTERVAL_S=4,AIRBAG_VERIFY_ATTEMPTS=8,AIRBAG_STATE=firestore,AIRBAG_SELF_URL=${AURL},AIRBAG_TASKS_QUEUE=airbag-heals,AIRBAG_TASKS_LOCATION=${REGION}"
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
  ENVS="${ENVS},GITHUB_REPO=$(grep '^GITHUB_REPO=' "$ROOT/agent/.env" | cut -d= -f2-)"
  SECRETS="${SECRETS},GITHUB_TOKEN=airbag-github-token:latest"
fi

echo "== deploy agent =="
# GOTCHA 3: the background self-heal needs CPU always allocated (--no-cpu-throttling),
# else CPU is throttled after the 202 response and the heal stalls.
# GOTCHA 4: the agent keeps idempotency + pending-revert + incident state IN-PROCESS, so it must
# stay a single instance. --min-instances 1 keeps it warm; --max-instances 1 keeps the in-process
# stores authoritative (no scale-out splitting state). Concurrency default is fine (the dashboard
# needs concurrent SSE + clicks); single-instance is about instance count, not request count.
# --timeout 3600: long-lived streamable-HTTP MCP sessions (when AIRBAG_MCP_HTTP=on) + dashboard SSE.
# To enable the remote MCP control plane: --update-env-vars AIRBAG_MCP_HTTP=on (it stays a public,
# token-gated, destructive surface, so --max-instances 1 is REQUIRED — sessions are in-process).
gcloud run deploy airbag-agent --source "$ROOT/agent" --region "$REGION" \
  --service-account "$SA" --allow-unauthenticated --min-instances 1 --max-instances 1 \
  --no-cpu-throttling --timeout 3600 --set-env-vars "$ENVS" --update-secrets "$SECRETS" -q

AURL="$(gcloud run services describe airbag-agent --region "$REGION" --format='value(status.url)')"
echo
echo "Agent:           $AURL"
echo "Target:          $TURL"
echo "Dashboard:       open $AURL and paste the demo token (in agent/.env, or Secret Manager"
echo "                 airbag-demo-secret) into the token field — or use ${AURL}/?token=<demo-token>"
# (token intentionally not printed here so it doesn't land in shared CI/terminal logs)
