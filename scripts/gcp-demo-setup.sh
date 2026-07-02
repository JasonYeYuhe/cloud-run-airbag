#!/usr/bin/env bash
# Establish the repeatable cloud-demo baseline on the live target. v3 supports TWO scenarios
# from ONE baseline (no teardown between runs), so we keep three revisions:
#   - HEALTHY (FAULT_MODE=off, current source) — the NEWEST revision, serving 100%
#   - BAD     (FAULT_MODE=bug — the KeyError the fix-PR repairs)          — ready at 0%
#   - SLOW    (FAULT_MODE=slow — the v3 latency regression, 200s > SLO)   — ready at 0%
# every other revision is deleted, so the rollback target is unambiguous.
#
# WHY HEALTHY MUST BE NEWEST (the invariant the FSM relies on): both deterministic rollback-target
# selectors (_heuristic, _rollback_pair) pick the NEWEST ready 0-traffic revision that isn't serving.
#   - break        -> BAD serves; newest 0-traffic = HEALTHY -> rollback lands on HEALTHY. ✓
#   - break-latency -> SLOW serves; newest 0-traffic = HEALTHY -> rollback lands on HEALTHY. ✓
# If SLOW were newest, the 5xx demo would roll the bug revision ONTO the slow one (0 5xx, so the
# causal check + _verify pass) — a silent latency-regressed "recovery". Keeping HEALTHY newest makes
# every rollback land on the genuinely-good revision. Traffic shifts don't change creation order, so
# once this baseline is set you can run break / break-latency back-to-back, forever, without re-setup.
#
# Run once after ./deploy.sh. Then drive break/break-latency -> heal -> reset from the dashboard.
#   PROJECT=airbag-hack-260628 REGION=asia-northeast1 ./scripts/gcp-demo-setup.sh
set -euo pipefail
PROJECT="${PROJECT:-airbag-hack-260628}"; REGION="${REGION:-asia-northeast1}"
SVC="${TARGET_SERVICE:-airbag-target}"
SLOW_DELAY_S="${SLOW_DELAY_S:-1.2}"   # > the agent's 800ms latency SLO, with margin for jitter
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export CLOUDSDK_CORE_DISABLE_PROMPTS=1
gcloud config set project "$PROJECT" >/dev/null

echo "== build + deploy the HEALTHY image (FAULT_MODE=off, current source), serving 100% =="
# This first --source build produces the image every later revision reuses (via `services update`,
# no rebuild). It serves 100% while we stage the fault revisions at 0%, so the target is never down.
gcloud run deploy "$SVC" --source "$ROOT/target-app" --region "$REGION" \
  --allow-unauthenticated --update-env-vars FAULT_MODE=off -q
TMP="$(gcloud run services describe "$SVC" --region "$REGION" \
  --format='value(status.latestCreatedRevisionName)')"
gcloud run services update-traffic "$SVC" --region "$REGION" --to-revisions="${TMP}=100" -q >/dev/null

echo "== stage BAD (FAULT_MODE=bug) at 0% traffic, reusing the healthy image =="
gcloud run services update "$SVC" --region "$REGION" \
  --update-env-vars FAULT_MODE=bug --no-traffic -q >/dev/null
BAD="$(gcloud run services describe "$SVC" --region "$REGION" \
  --format='value(status.latestCreatedRevisionName)')"

echo "== stage SLOW (FAULT_MODE=slow, ${SLOW_DELAY_S}s > SLO) at 0% traffic =="
gcloud run services update "$SVC" --region "$REGION" \
  --update-env-vars FAULT_MODE=slow,SLOW_DELAY_S="$SLOW_DELAY_S" --no-traffic -q >/dev/null
SLOW="$(gcloud run services describe "$SVC" --region "$REGION" \
  --format='value(status.latestCreatedRevisionName)')"

echo "== re-assert HEALTHY as the NEWEST revision (FAULT_MODE=off), then route 100% to it =="
# Created LAST -> highest create_time -> the rollback target for BOTH scenarios (see header).
gcloud run services update "$SVC" --region "$REGION" \
  --update-env-vars FAULT_MODE=off --no-traffic -q >/dev/null
HEALTHY="$(gcloud run services describe "$SVC" --region "$REGION" \
  --format='value(status.latestCreatedRevisionName)')"
gcloud run services update-traffic "$SVC" --region "$REGION" --to-revisions="${HEALTHY}=100" -q >/dev/null

echo "== strip any stale traffic tags (leftover canary/causal tags create phantom 0% targets) =="
for TAG in airbagfix airbagcanary airbagcausal; do
  gcloud run services update-traffic "$SVC" --region "$REGION" --remove-tags="$TAG" -q >/dev/null 2>&1 || true
done

echo "== delete every revision except HEALTHY + BAD + SLOW (rollback target stays unambiguous) =="
for REV in $(gcloud run revisions list --service "$SVC" --region "$REGION" \
              --format='value(metadata.name)'); do
  if [ "$REV" != "$HEALTHY" ] && [ "$REV" != "$BAD" ] && [ "$REV" != "$SLOW" ]; then
    echo "  deleting $REV"
    gcloud run revisions delete "$REV" --region "$REGION" -q >/dev/null 2>&1 || true
  fi
done

echo
echo "Baseline ready (HEALTHY is newest & serving 100%):"
echo "  HEALTHY = ${HEALTHY}  (100%, FAULT_MODE=off)"
echo "  BAD     = ${BAD}  (0%, FAULT_MODE=bug  -> 5xx demo:      Break -> Heal)"
echo "  SLOW    = ${SLOW}  (0%, FAULT_MODE=slow -> latency demo:  Break-latency -> Heal)"
echo "Run break / break-latency -> Heal -> Reset back-to-back; target is always left HEALTHY."
