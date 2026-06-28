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
for R in run.admin monitoring.viewer logging.viewer secretmanager.secretAccessor; do
  gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:${SA}" --role="roles/$R" --condition=None -q >/dev/null
done
# GOTCHA 1: new projects' default compute SA lacks build perms -> source deploys fail.
gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:${COMPUTE_SA}" \
  --role="roles/cloudbuild.builds.builder" --condition=None -q >/dev/null
# GOTCHA 2: the agent must actAs the target's runtime SA to update its traffic (rollback).
gcloud iam service-accounts add-iam-policy-binding "$COMPUTE_SA" \
  --member="serviceAccount:${SA}" --role="roles/iam.serviceAccountUser" -q >/dev/null

echo "== deploy target-app (healthy baseline; FAULT_MODE=off so a redeploy is never faulty) =="
# Explicit FAULT_MODE=off: scripts/gcp-demo-setup.sh later flips the service spec to
# FAULT_MODE=bug for the bad revision, so a plain redeploy must re-assert healthy.
gcloud run deploy airbag-target --source "$ROOT/target-app" --region "$REGION" \
  --allow-unauthenticated --update-env-vars FAULT_MODE=off -q
TURL="$(gcloud run services describe airbag-target --region "$REGION" --format='value(status.url)')"

echo "== demo + webhook tokens -> Secret Manager (never hardcode in this public repo) =="
# demo token: gates /demo/* once public (operator can trigger; public dashboard is watch-only).
# webhook token: gates /alerts/* and /internal/complete-rollback (Cloud Monitoring + the fix CI).
# Both are real secrets — reuse agent/.env values or generate, store in Secret Manager.
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
mk_secret airbag-demo-secret "$DEMO_TOKEN"
mk_secret airbag-webhook-secret "$WEBHOOK_TOKEN"

echo "== gemini key -> Secret Manager =="
if [ -f "$ROOT/agent/.env" ]; then
  KEY="$(grep '^GEMINI_API_KEY=' "$ROOT/agent/.env" | cut -d= -f2-)"
  printf '%s' "$KEY" | gcloud secrets create airbag-gemini-key --data-file=- 2>/dev/null \
    || printf '%s' "$KEY" | gcloud secrets versions add airbag-gemini-key --data-file=-
  gcloud secrets add-iam-policy-binding airbag-gemini-key --member="serviceAccount:${SA}" \
    --role=roles/secretmanager.secretAccessor -q >/dev/null
fi

ENVS="AIRBAG_BACKEND=gcp,GOOGLE_CLOUD_PROJECT=${PROJECT},GOOGLE_CLOUD_LOCATION=${REGION},TARGET_SERVICE=airbag-target,TARGET_BASE_URL=${TURL},AIRBAG_VERIFY_INTERVAL_S=4,AIRBAG_VERIFY_ATTEMPTS=8"
SECRETS="GEMINI_API_KEY=airbag-gemini-key:latest,AIRBAG_DEMO_TOKEN=airbag-demo-secret:latest,AIRBAG_WEBHOOK_TOKEN=airbag-webhook-secret:latest"

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
gcloud run deploy airbag-agent --source "$ROOT/agent" --region "$REGION" \
  --service-account "$SA" --allow-unauthenticated --min-instances 1 --no-cpu-throttling \
  --set-env-vars "$ENVS" --update-secrets "$SECRETS" -q

AURL="$(gcloud run services describe airbag-agent --region "$REGION" --format='value(status.url)')"
echo
echo "Agent:           $AURL"
echo "Target:          $TURL"
echo "Dashboard:       open $AURL and paste the demo token (in agent/.env, or Secret Manager"
echo "                 airbag-demo-secret) into the token field — or use ${AURL}/?token=<demo-token>"
# (token intentionally not printed here so it doesn't land in shared CI/terminal logs)
