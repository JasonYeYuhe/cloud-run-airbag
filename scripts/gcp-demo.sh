#!/usr/bin/env bash
# Cloud demo: simulate a bad deploy on the live target, then let the live agent self-heal.
#   ./scripts/gcp-demo.sh   then open the agent dashboard and click "Trigger incident".
set -euo pipefail
PROJECT="${PROJECT:-airbag-hack-260628}"; REGION="${REGION:-asia-northeast1}"
TURL="$(gcloud run services describe airbag-target --region "$REGION" --project "$PROJECT" --format='value(status.url)')"
AURL="$(gcloud run services describe airbag-agent  --region "$REGION" --project "$PROJECT" --format='value(status.url)')"
# The bad revision carries FAULT_MODE=http500; route 100% traffic to it.
BAD="$(gcloud run revisions list --service airbag-target --region "$REGION" --project "$PROJECT" \
        --filter='spec.containers[0].env.name=FAULT_MODE' --format='value(metadata.name)' --limit 1)"
echo "routing target -> bad revision: ${BAD:-airbag-target-00002-hv7}"
gcloud run services update-traffic airbag-target --to-revisions="${BAD:-airbag-target-00002-hv7}=100" \
  --region "$REGION" --project "$PROJECT" -q >/dev/null
echo "generating 5xx (and waiting for Cloud Logging to index)..."
for r in 1 2 3 4; do for i in $(seq 1 8); do curl -s -o /dev/null "$TURL/api/orders"; done; sleep 4; done
sleep 12
echo
echo "  Target is now failing (500s)."
echo "  Dashboard: $AURL   ->  click 'Trigger incident'"
echo "  or:        curl -XPOST $AURL/demo/trigger"
