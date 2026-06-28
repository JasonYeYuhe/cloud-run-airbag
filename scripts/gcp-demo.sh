#!/usr/bin/env bash
# Cloud demo: simulate a bad deploy on the live target, then let the live agent self-heal.
#   ./scripts/gcp-demo.sh   then open the agent dashboard and click "Heal".
set -euo pipefail
PROJECT="${PROJECT:-airbag-hack-260628}"; REGION="${REGION:-asia-northeast1}"
TURL="$(gcloud run services describe airbag-target --region "$REGION" --project "$PROJECT" --format='value(status.url)')"
AURL="$(gcloud run services describe airbag-agent  --region "$REGION" --project "$PROJECT" --format='value(status.url)')"
# The bad revision carries FAULT_MODE=bug (the KeyError the fix-PR repairs); route to it.
# Run scripts/gcp-demo-setup.sh first if it doesn't exist yet.
BAD="$(gcloud run revisions list --service airbag-target --region "$REGION" --project "$PROJECT" \
        --filter='spec.containers[0].env.value=bug' --sort-by='~metadata.creationTimestamp' \
        --format='value(metadata.name)' --limit 1)"
: "${BAD:?no FAULT_MODE=bug revision found — run ./scripts/gcp-demo-setup.sh first}"
echo "routing target -> bad revision: ${BAD}"
gcloud run services update-traffic airbag-target --to-revisions="${BAD}=100" \
  --region "$REGION" --project "$PROJECT" -q >/dev/null
echo "generating 5xx (and waiting for Cloud Logging to index)..."
for r in 1 2 3 4; do for i in $(seq 1 8); do curl -s -o /dev/null "$TURL/api/orders"; done; sleep 4; done
sleep 12
echo
echo "  Target is now failing (500s)."
echo "  Dashboard: $AURL   ->  click 'Heal'"
echo "  or:        curl -XPOST $AURL/demo/heal"
