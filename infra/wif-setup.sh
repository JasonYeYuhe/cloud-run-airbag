#!/usr/bin/env bash
# One-time Workload Identity Federation setup so the fix-PR's CI can deploy the fix + ask Airbag to
# close the rollback with NO long-lived key (keyless GitHub OIDC -> a short-lived GCP token).
# After this, set the repo vars/secret (printed at the end) and
# .github/workflows/complete-rollback.yml runs the FULLY-UNATTENDED close (no human, no dashboard).
#
#   PROJECT=airbag-hack-260628 REPO=JasonYeYuhe/cloud-run-airbag ./infra/wif-setup.sh
set -euo pipefail
PROJECT="${PROJECT:?set PROJECT=<gcp project id>}"
REPO="${REPO:-JasonYeYuhe/cloud-run-airbag}"
REGION="${REGION:-asia-northeast1}"
PNUM="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"
CISA="airbag-ci-deploy@${PROJECT}.iam.gserviceaccount.com"
COMPUTE="${PNUM}-compute@developer.gserviceaccount.com"

echo "== enable STS + IAM Credentials =="
gcloud services enable iamcredentials.googleapis.com sts.googleapis.com --project "$PROJECT" -q

echo "== WIF pool + OIDC provider (restricted to ${REPO}) =="
gcloud iam workload-identity-pools create github-pool --location=global --project "$PROJECT" \
  --display-name="GitHub Actions" 2>/dev/null || echo "  (pool exists)"
gcloud iam workload-identity-pools providers create-oidc github-provider \
  --location=global --workload-identity-pool=github-pool --project "$PROJECT" \
  --display-name="GitHub OIDC" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition="assertion.repository=='${REPO}'" \
  --issuer-uri="https://token.actions.githubusercontent.com" 2>/dev/null || echo "  (provider exists)"

echo "== dedicated CI deploy SA + least-priv roles =="
gcloud iam service-accounts create airbag-ci-deploy --display-name="Airbag CI deploy (WIF)" \
  --project "$PROJECT" 2>/dev/null || echo "  (SA exists)"
# run.admin: deploy; cloudbuild+storage+artifactregistry.writer: `gcloud run deploy --source` build+push
for R in run.admin cloudbuild.builds.editor storage.admin artifactregistry.writer; do
  gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:${CISA}" \
    --role="roles/$R" --condition=None -q >/dev/null
done
# actAs the target's runtime SA (the default compute SA) so the deploy can set the service identity
gcloud iam service-accounts add-iam-policy-binding "$COMPUTE" --member="serviceAccount:${CISA}" \
  --role=roles/iam.serviceAccountUser --project "$PROJECT" -q >/dev/null
# only THIS repo's OIDC may impersonate the deploy SA
gcloud iam service-accounts add-iam-policy-binding "$CISA" --project "$PROJECT" \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/${PNUM}/locations/global/workloadIdentityPools/github-pool/attribute.repository/${REPO}" \
  -q >/dev/null

cat <<EOF

✅ WIF wired (keyless). Now set the repo config:
  gh variable set GCP_WIF_PROVIDER --repo ${REPO} --body "projects/${PNUM}/locations/global/workloadIdentityPools/github-pool/providers/github-provider"
  gh variable set GCP_DEPLOY_SA    --repo ${REPO} --body "${CISA}"
  gh variable set AIRBAG_AGENT_URL --repo ${REPO} --body "https://<agent>.run.app"
  gh variable set TARGET_SERVICE   --repo ${REPO} --body "airbag-target"
  gh variable set GCP_REGION       --repo ${REPO} --body "${REGION}"
  gh secret set   AIRBAG_WEBHOOK_TOKEN --repo ${REPO}   # paste the webhook token
Then a merged fix to target-app/** (or workflow_dispatch) runs the unattended close.
EOF
