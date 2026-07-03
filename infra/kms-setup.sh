#!/usr/bin/env bash
# v5 Phase 4.2 — create the Cloud KMS asymmetric-signing key Airbag uses to SIGN its proof bundles,
# and grant the agent's service account signer/verifier on it (least privilege — signerVerifier
# only, NOT admin). Idempotent. Prints the AIRBAG_KMS_KEY value + exports the public key PEM the
# offline verifier (scripts/verify-proof.py) checks against.
#
#   PROJECT=<id> REGION=asia-northeast1 [AGENT_SA=<sa-email>] ./infra/kms-setup.sh
#
# HONESTY: the signature proves PROVENANCE (produced by the holder of this KMS identity), not that
# the decisions inside the bundle are correct. After this runs, set AIRBAG_KMS_KEY (printed below)
# + AIRBAG_PROOF_SIGN=1 on the agent to sign live (fail-open — a KMS hiccup degrades to digest-only).
set -euo pipefail
: "${PROJECT:?set PROJECT=your-gcp-project}"
REGION="${REGION:-asia-northeast1}"
KEYRING="${AIRBAG_KMS_KEYRING:-airbag}"
KEY="${AIRBAG_KMS_KEYNAME:-airbag-proof}"
export CLOUDSDK_CORE_DISABLE_PROMPTS=1

gcloud services enable cloudkms.googleapis.com --project "$PROJECT" >/dev/null 2>&1 || true

echo "== key ring '${KEYRING}' (${REGION}) =="
gcloud kms keyrings create "$KEYRING" --location "$REGION" --project "$PROJECT" 2>/dev/null \
  || echo "  (keyring exists)"

echo "== asymmetric-sign key '${KEY}' (EC_SIGN_P256_SHA256) =="
gcloud kms keys create "$KEY" --location "$REGION" --keyring "$KEYRING" \
  --purpose asymmetric-signing --default-algorithm ec-sign-p256-sha256 \
  --project "$PROJECT" 2>/dev/null || echo "  (key exists)"

# the agent's runtime service account — default to the deployed agent's SA if not supplied
if [[ -z "${AGENT_SA:-}" ]]; then
  AGENT_SA="$(gcloud run services describe airbag-agent --region "$REGION" --project "$PROJECT" \
    --format='value(spec.template.spec.serviceAccountName)' 2>/dev/null || true)"
fi
: "${AGENT_SA:?could not resolve the agent service account — pass AGENT_SA=<sa-email>}"

echo "== grant ${AGENT_SA} roles/cloudkms.signerVerifier (least privilege) on the key =="
gcloud kms keys add-iam-policy-binding "$KEY" --location "$REGION" --keyring "$KEYRING" \
  --member "serviceAccount:${AGENT_SA}" --role roles/cloudkms.signerVerifier \
  --project "$PROJECT" >/dev/null

KEY_VERSION="projects/${PROJECT}/locations/${REGION}/keyRings/${KEYRING}/cryptoKeys/${KEY}/cryptoKeyVersions/1"
PEM_OUT="$(cd "$(dirname "$0")/.." && pwd)/scripts/airbag-proof-pubkey.pem"
echo "== export the public key PEM -> ${PEM_OUT} =="
gcloud kms keys versions get-public-key 1 --key "$KEY" --keyring "$KEYRING" --location "$REGION" \
  --project "$PROJECT" --output-file "$PEM_OUT"

cat <<EOF

Done. To sign live, set on the agent (fail-open — a KMS failure degrades to digest-only):
  AIRBAG_PROOF_SIGN=1
  AIRBAG_KMS_KEY=${KEY_VERSION}
Commit ${PEM_OUT#"$(cd "$(dirname "$0")/.." && pwd)/"} (the PUBLIC key — safe to commit) so anyone can
verify a proof offline:  python scripts/verify-proof.py docs/proof/<incident>.json
EOF
