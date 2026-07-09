#!/usr/bin/env bash
# v6 Phase 1.2 — create the AUDITOR's OWN Cloud KMS asymmetric-signing key (a SECOND, independent
# identity that counter-signs the auditor's attestations) and its OWN least-privilege service account.
# Idempotent. Clone of infra/kms-setup.sh with two deliberate deltas (V6_VISION Round-2 #26):
#   (1) it CREATES the auditor SA first (the agent's kms-setup.sh resolves the deployed agent SA);
#   (2) PEM_OUT is PARAMETERIZED (default scripts/auditor-pubkey.pem) so it can NEVER overwrite the
#       agent's committed scripts/airbag-proof-pubkey.pem.
#
#   PROJECT=<id> REGION=asia-northeast1 ./infra/auditor-kms-setup.sh
#
# INDEPENDENCE is load-bearing: the auditor key (airbag-auditor) is DISTINCT from the agent's proof
# key (airbag-proof), and signerVerifier is granted to the AUDITOR SA ONLY — never the agent SA, never
# reuse the proof key. By default this runs in the SAME project (administrative separation via a
# distinct SA + key). For MAXIMAL administrative-domain independence, run it in a SECOND GCP project
# (set PROJECT to that project); the auditor needs only its own KMS key + outbound HTTPS to the
# agent's public URL. HONESTY: the counter-signature proves PROVENANCE of the auditor's verdict, not
# that the heal's decisions are correct.
set -euo pipefail
: "${PROJECT:?set PROJECT=your-gcp-project}"
REGION="${REGION:-asia-northeast1}"
KEYRING="${AIRBAG_KMS_KEYRING:-airbag}"
KEY="${AIRBAG_AUDITOR_KMS_KEYNAME:-airbag-auditor}"
AUDITOR_SA_NAME="${AUDITOR_SA_NAME:-airbag-auditor}"
AUDITOR_SA="${AUDITOR_SA:-${AUDITOR_SA_NAME}@${PROJECT}.iam.gserviceaccount.com}"
# Delta (2): the PEM output path is a variable so this NEVER clobbers the agent's committed pubkey.
PEM_OUT="${AUDITOR_PEM_OUT:-$(cd "$(dirname "$0")/.." && pwd)/scripts/auditor-pubkey.pem}"
export CLOUDSDK_CORE_DISABLE_PROMPTS=1

gcloud services enable cloudkms.googleapis.com iam.googleapis.com --project "$PROJECT" >/dev/null 2>&1 || true

# Delta (1): create the auditor's OWN least-privilege SA first (precedent: infra/sandbox-job-setup.sh:27).
echo "== auditor service account '${AUDITOR_SA}' (its own identity — NEVER the agent SA) =="
gcloud iam service-accounts create "$AUDITOR_SA_NAME" --project "$PROJECT" \
  --display-name "Airbag auditor — independent verifier, signerVerifier on airbag-auditor ONLY" \
  2>/dev/null || echo "  (auditor SA exists)"

echo "== key ring '${KEYRING}' (${REGION}) =="
gcloud kms keyrings create "$KEYRING" --location "$REGION" --project "$PROJECT" 2>/dev/null \
  || echo "  (keyring exists)"

echo "== auditor asymmetric-sign key '${KEY}' (EC_SIGN_P256_SHA256) — DISTINCT from airbag-proof =="
gcloud kms keys create "$KEY" --location "$REGION" --keyring "$KEYRING" \
  --purpose asymmetric-signing --default-algorithm ec-sign-p256-sha256 \
  --project "$PROJECT" 2>/dev/null || echo "  (key exists)"

echo "== grant ${AUDITOR_SA} roles/cloudkms.signerVerifier (least privilege) on the AUDITOR key ONLY =="
gcloud kms keys add-iam-policy-binding "$KEY" --location "$REGION" --keyring "$KEYRING" \
  --member "serviceAccount:${AUDITOR_SA}" --role roles/cloudkms.signerVerifier \
  --project "$PROJECT" >/dev/null

KEY_VERSION="projects/${PROJECT}/locations/${REGION}/keyRings/${KEYRING}/cryptoKeys/${KEY}/cryptoKeyVersions/1"
echo "== export the auditor public key PEM -> ${PEM_OUT} =="
gcloud kms keys versions get-public-key 1 --key "$KEY" --keyring "$KEYRING" --location "$REGION" \
  --project "$PROJECT" --output-file "$PEM_OUT"

cat <<EOF

Done. The auditor's independent identity:
  service account : ${AUDITOR_SA}   (signerVerifier on ${KEY} ONLY)
  key version     : ${KEY_VERSION}
Deploy the auditor service (Phase 1.3) with this SA + set:
  AIRBAG_AUDITOR_KMS_KEY=${KEY_VERSION}
  AIRBAG_AGENT_PROOF_URL=https://<agent-url>          (the public GET /incidents/{id}/proof host)
Commit ${PEM_OUT#"$(cd "$(dirname "$0")/.." && pwd)/"} (the PUBLIC key — safe to commit) so anyone can
verify an auditor attestation offline against the auditor's pinned identity.
EOF
