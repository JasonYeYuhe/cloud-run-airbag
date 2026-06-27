#!/usr/bin/env bash
# One-time GCP setup for Airbag. Requires gcloud + a billing-enabled project.
#   PROJECT=my-proj REGION=asia-northeast1 ./infra/setup.sh
set -euo pipefail

: "${PROJECT:?set PROJECT=your-gcp-project}"
REGION="${REGION:-asia-northeast1}"
SA="airbag-agent@${PROJECT}.iam.gserviceaccount.com"

gcloud config set project "$PROJECT"

echo "== enable APIs =="
gcloud services enable \
  run.googleapis.com monitoring.googleapis.com logging.googleapis.com \
  cloudbuild.googleapis.com secretmanager.googleapis.com \
  sqladmin.googleapis.com aiplatform.googleapis.com

echo "== service account + least-privilege IAM =="
gcloud iam service-accounts create airbag-agent \
  --display-name="Airbag self-heal agent" || true
for ROLE in roles/run.developer roles/monitoring.viewer roles/logging.viewer \
            roles/secretmanager.secretAccessor roles/aiplatform.user; do
  gcloud projects add-iam-policy-binding "$PROJECT" \
    --member="serviceAccount:${SA}" --role="$ROLE" --condition=None >/dev/null
  echo "  bound $ROLE"
done

echo
echo "Done. Next:"
echo "  1) deploy target-app:  gcloud run deploy airbag-target --source target-app --region $REGION --allow-unauthenticated"
echo "  2) deploy agent:       gcloud run deploy airbag-agent --source agent --region $REGION --service-account $SA --no-allow-unauthenticated --min-instances 1"
echo "  3) alert policy + webhook channel — see infra/alert-policy.json.tmpl and docs/PLAN.md"
