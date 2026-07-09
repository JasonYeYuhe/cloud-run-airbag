#!/usr/bin/env bash
# v6 Phase 1.3 — deploy the Airbag Auditor: a SECOND, adversarially-independent Cloud Run service that
# READS the agent's public proof endpoints and counter-signs an independent verdict with its OWN KMS
# key. It NEVER writes to the agent (no write path) and runs under a dedicated ZERO-role SA whose only
# grant is signerVerifier on the airbag-auditor key (infra/auditor-kms-setup.sh) + outbound HTTPS.
#
#   PROJECT=<id> AGENT_URL=https://airbag-agent-...run.app [MIN_INSTANCES=0] ./infra/auditor-deploy.sh
#
# COST posture (§8 Q8): default MIN_INSTANCES=0 (scale-to-zero between demos). Pin MIN_INSTANCES=1 only
# for a recording/finals window so the background poll loop stays warm. max-instances=1 keeps the
# in-memory attestation snapshot single-sourced. Deploying the auditor is safe at ANY time (read-only).
set -euo pipefail
: "${PROJECT:?set PROJECT=your-gcp-project}"
: "${AGENT_URL:?set AGENT_URL=https://airbag-agent-...run.app (the public proof host)}"
REGION="${REGION:-asia-northeast1}"
MIN_INSTANCES="${MIN_INSTANCES:-0}"
SERVICE="${AUDITOR_SERVICE:-airbag-auditor}"
AUDITOR_SA="${AUDITOR_SA:-airbag-auditor@${PROJECT}.iam.gserviceaccount.com}"
AUDITOR_KEY="${AIRBAG_AUDITOR_KMS_KEY:-projects/${PROJECT}/locations/${REGION}/keyRings/airbag/cryptoKeys/airbag-auditor/cryptoKeyVersions/1}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
export CLOUDSDK_CORE_DISABLE_PROMPTS=1

# Stage the committed trust-anchor PEMs into the build context (gitignored; sources live in scripts/).
echo "== stage trust-anchor PEMs into auditor/keys/ =="
mkdir -p "$HERE/auditor/keys"
cp "$HERE/scripts/airbag-proof-pubkey.pem" "$HERE/auditor/keys/airbag-proof-pubkey.pem"
cp "$HERE/scripts/auditor-pubkey.pem" "$HERE/auditor/keys/auditor-pubkey.pem"

echo "== deploy ${SERVICE} (min-instances ${MIN_INSTANCES}, max 1, read-only, SA ${AUDITOR_SA}) =="
# ^@^ sets '@' as the env-var delimiter (mirrors deploy.sh) so URLs/resource names with ',' are safe.
gcloud run deploy "$SERVICE" --source "$HERE/auditor" --region "$REGION" --project "$PROJECT" \
  --service-account "$AUDITOR_SA" --allow-unauthenticated \
  --min-instances "$MIN_INSTANCES" --max-instances 1 --memory 512Mi --cpu 1 --timeout 60 \
  --set-env-vars "^@^AIRBAG_AGENT_PROOF_URL=${AGENT_URL}@AIRBAG_AUDITOR_KMS_KEY=${AUDITOR_KEY}@AIRBAG_AGENT_PUBKEY_PEM=/app/keys/airbag-proof-pubkey.pem@AIRBAG_AUDITOR_PUBKEY_PEM=/app/keys/auditor-pubkey.pem"

URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --project "$PROJECT" \
  --format='value(status.url)')"
echo
echo "Done. Auditor: ${URL}   (status page: ${URL}/  · attestations: ${URL}/attestations)"
echo "Scale to zero between demos:  gcloud run services update ${SERVICE} --region ${REGION} --min-instances 0"
