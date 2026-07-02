#!/usr/bin/env bash
# v5 Phase 1.2 — observer-safe alerting (ADDITIVE; does NOT touch the v1 policy).
#
# The v1 alert (infra/alert-setup.sh) fires on the BUILT-IN run.googleapis.com/request_count 5xx
# metric, which CANNOT filter on a request header — so Airbag's OWN diagnostic probes (which carry
# User-Agent: airbag-probe/1) count toward the alert and can fire the very incident being diagnosed
# (observed live 2026-07-02). This script creates a LOG-BASED counter metric that EXCLUDES the probe
# UA (httpRequest.userAgent is present in Cloud Run request logs) plus a policy on it, so the alert
# reflects USER 5xx only.
#
# ADDITIVE + REVERSIBLE by design: it adds a new metric + policy alongside v1 and prints how to
# cut over (disable v1) and roll back. Do NOT cut over live until the storm scorecard + a live probe
# verify the exclusion works AND the demo video is recorded (the alert path is demo-critical).
# HONESTY SCOPE: this covers the DETECTION/COUNT path only. App-emitted tracebacks (fetch_error_logs)
# and the built-in console metric are out of scope — a probe-triggered traceback is byte-identical to
# a user one and no decision keys on trace COUNTS.
#
#   PROJECT=<id> REGION=asia-northeast1 ./infra/alert-setup-v2.sh
set -euo pipefail
: "${PROJECT:?set PROJECT=your-gcp-project}"
REGION="${REGION:-asia-northeast1}"
SERVICE="${TARGET_SERVICE:-airbag-target}"
PROBE_UA="${AIRBAG_PROBE_UA:-airbag-probe/1}"
METRIC="${AIRBAG_LOG_METRIC:-airbag_user_5xx}"
export CLOUDSDK_CORE_DISABLE_PROMPTS=1
gcloud components install beta -q >/dev/null 2>&1 || true

TOKEN="${AIRBAG_WEBHOOK_TOKEN:-$(gcloud secrets versions access latest --secret=airbag-webhook-secret --project "$PROJECT" 2>/dev/null)}"
: "${TOKEN:?no webhook token — run ./deploy.sh first (creates Secret Manager airbag-webhook-secret)}"
AURL="$(gcloud run services describe airbag-agent --region "$REGION" --project "$PROJECT" --format='value(status.url)')"

# The log filter: Cloud Run request logs for the target's 5xx, EXCLUDING Airbag's marked probe UA.
# `NOT httpRequest.userAgent="..."` (not `!=`) so a request log missing userAgent is KEPT — we only
# ever drop the exact probe UA, never a real user 5xx. Mirrors gcp._error_rate_filter exactly.
LOG_FILTER="resource.type=\"cloud_run_revision\" resource.labels.service_name=\"${SERVICE}\" resource.labels.location=\"${REGION}\" httpRequest.status>=500 NOT httpRequest.userAgent=\"${PROBE_UA}\""

echo "== log-based counter metric '${METRIC}' (user 5xx only; probe UA excluded) =="
if gcloud logging metrics describe "$METRIC" --project "$PROJECT" >/dev/null 2>&1; then
  gcloud logging metrics update "$METRIC" --project "$PROJECT" \
    --description="Airbag v5 1.2: ${SERVICE} 5xx EXCLUDING airbag-probe traffic" \
    --log-filter="$LOG_FILTER"
else
  gcloud logging metrics create "$METRIC" --project "$PROJECT" \
    --description="Airbag v5 1.2: ${SERVICE} 5xx EXCLUDING airbag-probe traffic" \
    --log-filter="$LOG_FILTER"
fi

echo "== webhook notification channel -> ${AURL}/alerts/cloud-monitoring (Basic auth, token in header) =="
CH=$(gcloud beta monitoring channels create \
  --display-name="airbag agent webhook (v2)" --type=webhook_basicauth \
  --channel-labels="url=${AURL}/alerts/cloud-monitoring,username=airbag,password=${TOKEN}" \
  --project "$PROJECT" --format="value(name)")
echo "  channel: $CH"

echo "== alert policy on the log-based metric (observer-safe) =="
TMP="$(mktemp)"
cat > "$TMP" <<JSON
{
  "displayName": "${SERVICE} user 5xx spike (observer-safe, v2)",
  "combiner": "OR",
  "conditions": [{
    "displayName": "user 5xx on ${SERVICE} (probe UA excluded)",
    "conditionThreshold": {
      "filter": "metric.type=\"logging.googleapis.com/user/${METRIC}\" resource.type=\"cloud_run_revision\"",
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

cat <<EOF

Done (ADDITIVE — v1 policy untouched). Verify the exclusion is live before cutting over:
  1) With AIRBAG_SELF_TRAFFIC_EXCLUDE=1 on the agent, run a heal whose causal probe hits a broken
     target; confirm the '${METRIC}' metric does NOT move for the probe UA (Logs Explorer:
     $LOG_FILTER).
  2) Confirm a real USER 5xx DOES move it.
Cut over (only AFTER the demo video is recorded — coordinate with Jason):
  - disable the v1 policy:  gcloud alpha monitoring policies update <v1-policy-id> --no-enabled
Roll back:
  - disable this v2 policy + delete the metric:  gcloud logging metrics delete ${METRIC}
EOF
