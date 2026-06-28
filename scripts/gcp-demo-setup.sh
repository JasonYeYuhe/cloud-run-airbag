#!/usr/bin/env bash
# Establish the repeatable cloud-demo baseline on the live target:
#   - a HEALTHY revision (FAULT_MODE=off, current source) serving 100%
#   - a BAD revision (FAULT_MODE=bug — the KeyError the fix-PR repairs) ready at 0% traffic
#   - every other revision deleted, so the agent's rollback target is unambiguous
# Run once after ./deploy.sh. Then drive break -> heal -> reset from the dashboard, repeatably.
#   PROJECT=airbag-hack-260628 REGION=asia-northeast1 ./scripts/gcp-demo-setup.sh
set -euo pipefail
PROJECT="${PROJECT:-airbag-hack-260628}"; REGION="${REGION:-asia-northeast1}"
SVC="${TARGET_SERVICE:-airbag-target}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export CLOUDSDK_CORE_DISABLE_PROMPTS=1
gcloud config set project "$PROJECT" >/dev/null

echo "== deploy the HEALTHY baseline (FAULT_MODE=off, current source) =="
# --update-env-vars FAULT_MODE=off re-asserts healthy even if a prior run left the spec on bug.
gcloud run deploy "$SVC" --source "$ROOT/target-app" --region "$REGION" \
  --allow-unauthenticated --update-env-vars FAULT_MODE=off -q
HEALTHY="$(gcloud run services describe "$SVC" --region "$REGION" \
  --format='value(status.latestCreatedRevisionName)')"
echo "  healthy revision: $HEALTHY"
gcloud run services update-traffic "$SVC" --region "$REGION" --to-revisions="${HEALTHY}=100" -q >/dev/null

echo "== create the BAD revision (FAULT_MODE=bug) at 0% traffic, reusing the healthy image =="
# `services update` (no --source) reuses the just-built current-source image -> the bug is real;
# --no-traffic keeps the healthy revision serving.
gcloud run services update "$SVC" --region "$REGION" \
  --update-env-vars FAULT_MODE=bug --no-traffic -q >/dev/null
BAD="$(gcloud run services describe "$SVC" --region "$REGION" \
  --format='value(status.latestCreatedRevisionName)')"
echo "  bad revision: $BAD"
# belt-and-suspenders: ensure 100% is still on the healthy revision
gcloud run services update-traffic "$SVC" --region "$REGION" --to-revisions="${HEALTHY}=100" -q >/dev/null

echo "== delete every other revision (keep only healthy + bad) so rollback is unambiguous =="
for REV in $(gcloud run revisions list --service "$SVC" --region "$REGION" \
              --format='value(metadata.name)'); do
  if [ "$REV" != "$HEALTHY" ] && [ "$REV" != "$BAD" ]; then
    echo "  deleting $REV"
    gcloud run revisions delete "$REV" --region "$REGION" -q >/dev/null 2>&1 || true
  fi
done

echo
echo "Baseline ready: healthy=${HEALTHY} (100%), bad=${BAD} (0%, FAULT_MODE=bug)."
echo "Drive it from the dashboard: Break -> Heal -> Reset (repeatable). Target left HEALTHY."
