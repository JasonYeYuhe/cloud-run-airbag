#!/usr/bin/env bash
# Tear down everything deploy.sh / infra/alert-setup.sh created, to stop all spend.
#   PROJECT=airbag-hack-260628 ./teardown.sh        # prompts before deleting
#   PROJECT=airbag-hack-260628 YES=1 ./teardown.sh  # no prompt
#
# Cost note (why this exists): the agent runs with --min-instances=1 --no-cpu-throttling so
# the background self-heal isn't CPU-throttled and in-memory idempotency survives — that's
# ~1 always-on small instance (the only standing cost; everything else is per-request /
# per-build and rounds to ~$0 at demo volume on the $300 free trial + $10/mo credit).
set -euo pipefail
: "${PROJECT:?set PROJECT=your-gcp-project}"
REGION="${REGION:-asia-northeast1}"
SA="airbag-agent@${PROJECT}.iam.gserviceaccount.com"
gcloud config set project "$PROJECT" >/dev/null

if [ "${YES:-0}" != "1" ]; then
  read -r -p "Delete airbag-agent + airbag-target, secrets, alert policy/channel, and the agent SA in ${PROJECT}? [y/N] " ok
  [ "$ok" = "y" ] || { echo "aborted"; exit 1; }
fi

echo "== delete Cloud Run services =="
for S in airbag-agent airbag-target; do
  gcloud run services delete "$S" --region "$REGION" -q 2>/dev/null || true
done

echo "== delete alert policies + notification channels (airbag) =="
for P in $(gcloud alpha monitoring policies list --filter='displayName:airbag-target' \
            --format='value(name)' 2>/dev/null); do
  gcloud alpha monitoring policies delete "$P" -q 2>/dev/null || true
done
for C in $(gcloud beta monitoring channels list --filter='displayName:"airbag agent webhook"' \
            --format='value(name)' 2>/dev/null); do
  gcloud beta monitoring channels delete "$C" -q 2>/dev/null || true
done

echo "== delete secrets =="
for SEC in airbag-gemini-key airbag-github-token airbag-demo-secret; do
  gcloud secrets delete "$SEC" -q 2>/dev/null || true
done

echo "== delete agent service account =="
gcloud iam service-accounts delete "$SA" -q 2>/dev/null || true

echo
echo "Done. (Artifact Registry build images may remain — delete the 'cloud-run-source-deploy'"
echo "repo in Artifact Registry to reclaim the last few MB if you want a truly clean project.)"
