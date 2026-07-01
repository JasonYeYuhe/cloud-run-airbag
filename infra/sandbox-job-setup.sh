#!/usr/bin/env bash
# Provision the egress-disabled Cloud Run Job sandbox (v3 Phase 0.5). The LLM-authored regression
# test from the fix pipeline runs HERE — isolated from the prod agent — under a ZERO-PERMISSION
# service account with NO network egress. Executing un-sandboxed LLM-authored code in the prod
# agent's container (which holds a run.admin SA) would contradict the guarded-action moat.
#
# Run once (idempotent):  PROJECT=<id> REGION=asia-northeast1 ./infra/sandbox-job-setup.sh
# Then enable it on the agent with AIRBAG_SANDBOX=cloudrun_job (deploy.sh) — default stays subprocess.
set -euo pipefail
: "${PROJECT:?set PROJECT=your-gcp-project}"
REGION="${REGION:-asia-northeast1}"
JOB="${AIRBAG_SANDBOX_JOB:-airbag-sandbox}"
SANDBOX_SA="airbag-sandbox@${PROJECT}.iam.gserviceaccount.com"
AGENT_SA="airbag-agent@${PROJECT}.iam.gserviceaccount.com"
NET="airbag-sandbox-net"
SUBNET="airbag-sandbox-subnet"
REPO="airbag-sandbox"
IMG="${REGION}-docker.pkg.dev/${PROJECT}/${REPO}/runner:latest"
HERE="$(cd "$(dirname "$0")/.." && pwd)"   # repo root
export CLOUDSDK_CORE_DISABLE_PROMPTS=1

echo "== enable APIs (compute is needed for the isolation VPC) =="
gcloud services enable run.googleapis.com compute.googleapis.com \
  artifactregistry.googleapis.com cloudbuild.googleapis.com --project "$PROJECT"

echo "== zero-permission service account (deliberately NO role bindings) =="
gcloud iam service-accounts create airbag-sandbox --project "$PROJECT" \
  --display-name "Airbag sandbox — runs untrusted LLM-authored tests, NO permissions" 2>/dev/null || true

echo "== isolation network: custom VPC, /26 subnet, DENY-ALL egress firewall on the sandbox SA =="
gcloud compute networks create "$NET" --project "$PROJECT" --subnet-mode=custom 2>/dev/null || true
gcloud compute networks subnets create "$SUBNET" --project "$PROJECT" --network "$NET" \
  --region "$REGION" --range 10.8.0.0/26 2>/dev/null || true
# Belt-and-suspenders on top of the no-Cloud-NAT network: an explicit high-priority egress DENY for
# ALL traffic from any workload running as the sandbox SA. The job can reach NOTHING (metadata
# server, GCP APIs, the internet). stdout still reaches Cloud Logging (captured by the control plane,
# not the container's blocked network), so the agent can read the verdict back.
gcloud compute firewall-rules create airbag-sandbox-deny-egress --project "$PROJECT" \
  --network "$NET" --direction EGRESS --action DENY --rules all \
  --destination-ranges 0.0.0.0/0 --target-service-accounts "$SANDBOX_SA" --priority 100 2>/dev/null || true

echo "== build + push the minimal sandbox image =="
gcloud artifacts repositories create "$REPO" --project "$PROJECT" --location "$REGION" \
  --repository-format docker 2>/dev/null || true
gcloud builds submit "${HERE}/sandbox-job" --project "$PROJECT" --tag "$IMG"

echo "== create/update the egress-disabled Cloud Run Job =="
# Direct VPC egress (--vpc-egress=all-traffic) routes ALL of the job's traffic through the locked-down
# VPC, where the DENY firewall drops it. Runs as the zero-permission SA. max-retries 0 = one attempt.
gcloud run jobs deploy "$JOB" --project "$PROJECT" --region "$REGION" --image "$IMG" \
  --service-account "$SANDBOX_SA" \
  --network "$NET" --subnet "$SUBNET" --vpc-egress all-traffic \
  --max-retries 0 --task-timeout 120s --memory 512Mi --cpu 1

# The agent SA runs the job (run.jobs.run) — already covered by its existing project-level run.admin,
# and reads the verdict via its logging.viewer. No extra binding is required.
echo
echo "Done. The sandbox job '$JOB' is egress-disabled (SA has zero roles, VPC denies all egress)."
echo "Enable it on the agent:  gcloud run services update airbag-agent --region $REGION \\"
echo "  --update-env-vars AIRBAG_SANDBOX=cloudrun_job   (default stays 'subprocess')."
echo "Smoke-test the job directly (should log AIRBAG_SANDBOX_RESULT with ok=true):"
echo "  see infra/README or docs/ARCHITECTURE.md for the base64 env-override example."
