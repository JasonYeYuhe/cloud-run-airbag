#!/usr/bin/env bash
# Wire a real Cloud Monitoring 5xx alert -> webhook -> the agent, so heals fire with NO
# manual trigger. Run after deploy.sh.
#   PROJECT=<id> REGION=asia-northeast1 ./infra/alert-setup.sh
set -euo pipefail
: "${PROJECT:?set PROJECT=your-gcp-project}"
REGION="${REGION:-asia-northeast1}"
SERVICE="${TARGET_SERVICE:-airbag-target}"
# Use the real webhook token (Secret Manager), NOT a hardcoded literal — this token gates the heal
# trigger on a public service. Sent via the Authorization header (webhook_basicauth), NOT in the URL:
# a `?token=` query string persists the secret in Cloud Run/LB request logs + GCP audit logs (P0#2).
TOKEN="${AIRBAG_WEBHOOK_TOKEN:-$(gcloud secrets versions access latest --secret=airbag-webhook-secret --project "$PROJECT" 2>/dev/null)}"
: "${TOKEN:?no webhook token — run ./deploy.sh first (creates Secret Manager airbag-webhook-secret)}"
export CLOUDSDK_CORE_DISABLE_PROMPTS=1
gcloud components install beta -q >/dev/null 2>&1 || true

AURL="$(gcloud run services describe airbag-agent --region "$REGION" --project "$PROJECT" --format='value(status.url)')"

echo "== webhook notification channel -> ${AURL}/alerts/cloud-monitoring (Basic auth, token in header) =="
# webhook_basicauth -> Cloud Monitoring sends `Authorization: Basic base64(airbag:TOKEN)`; the agent
# verifies the password against AIRBAG_WEBHOOK_TOKEN (app._alert_token). No secret in the URL.
CH=$(gcloud beta monitoring channels create \
  --display-name="airbag agent webhook" --type=webhook_basicauth \
  --channel-labels="url=${AURL}/alerts/cloud-monitoring,username=airbag,password=${TOKEN}" \
  --project "$PROJECT" --format="value(name)")
echo "  channel: $CH"

echo "== alert policy: any 5xx on ${SERVICE} =="
TMP="$(mktemp)"
cat > "$TMP" <<JSON
{
  "displayName": "${SERVICE} 5xx spike",
  "combiner": "OR",
  "conditions": [{
    "displayName": "5xx requests on ${SERVICE}",
    "conditionThreshold": {
      "filter": "metric.type=\"run.googleapis.com/request_count\" resource.type=\"cloud_run_revision\" resource.label.\"service_name\"=\"${SERVICE}\" metric.label.\"response_code_class\"=\"5xx\"",
      "comparison": "COMPARISON_GT", "thresholdValue": 0, "duration": "0s",
      "trigger": {"count": 1},
      "aggregations": [{"alignmentPeriod": "60s", "perSeriesAligner": "ALIGN_RATE"}]
    }
  }],
  "notificationChannels": ["${CH}"],
  "alertStrategy": {"autoClose": "1800s"}
}
JSON
gcloud beta monitoring policies create --policy-from-file="$TMP" --project "$PROJECT" --format="value(name)"
rm -f "$TMP"
echo
echo "Done. Break the target (./scripts/gcp-demo.sh) and the alert self-heals it in ~2-4 min,"
echo "no manual trigger. (Cloud Monitoring evaluation latency is the wait.)"
