# Airbag sandbox job — egress-disabled verification of LLM-authored tests

The fix pipeline (`agent/autosre/fix_pipeline.py`) has Gemini author a regression test, then
**self-proves** it: the test must FAIL on the buggy file and PASS on the fix. That means running
**LLM-authored code**. Running it inside the prod agent's container — which holds a `run.admin`
service account — would contradict Airbag's guarded-action moat: even with the metadata server
neutralized, arbitrary code could read the filesystem or exfiltrate over the network.

So the production posture (Phase 0.5) runs that verification **here**, in an isolated
**Cloud Run Job**:

- **Zero-permission service account.** The job runs as `airbag-sandbox@…`, which has **no IAM role
  bindings** — so even if code reached the metadata server, the token it mints can do nothing.
- **No network egress.** The job attaches to a locked-down custom VPC with Direct VPC egress
  (`--vpc-egress=all-traffic`) and a **DENY-ALL egress firewall** scoped to the sandbox SA. The
  container can reach nothing — not the metadata server, not GCP APIs, not the internet.
- **No baked-in code or secrets.** The image is just `python + pytest + runner.py`. The three file
  contents (original / fixed / test) arrive **per-execution** as base64 env-var overrides
  (`RunJobRequest.overrides`). Nothing sensitive is in the image or the job spec.
- **Verdict read-back.** `runner.py` prints one `AIRBAG_SANDBOX_RESULT:{…}` line to stdout — captured
  by the Cloud Run control plane (not the container's blocked network) — and **always exits 0**. The
  agent (`autosre/sandbox.py`) reads that marker back from Cloud Logging. If it can't, the fix is
  treated as **unverified** (never falsely "verified"); CI remains the backstop.

## Wiring

- Provision: `PROJECT=<id> REGION=<r> ./infra/sandbox-job-setup.sh` (idempotent).
- Enable on the agent: `AIRBAG_SANDBOX=cloudrun_job` (default is `subprocess`, used for the demo).
- The subprocess fallback (bounded local subprocess with the metadata server neutralized) still runs
  if the Job path errors, so a heal never blocks on the sandbox.

## Smoke test (direct execution)

```bash
b64() { printf '%s' "$1" | base64; }
ORIG=$(b64 "ORDERS=[{'price':10},{'price':25}]
def total_revenue(orders, buggy=False):
    key='amount' if buggy else 'price'
    return sum(o[key] for o in orders)")
FIXED=$(b64 "ORDERS=[{'price':10},{'price':25}]
def total_revenue(orders, buggy=False):
    return sum(o['price'] for o in orders)")
TEST=$(b64 "import main
def test_no_keyerror(): assert main.total_revenue(main.ORDERS, buggy=True)==35")
gcloud run jobs execute airbag-sandbox --region asia-northeast1 --wait \
  --update-env-vars "AIRBAG_STEM=main,AIRBAG_TEST_NAME=test_r.py,AIRBAG_ORIGINAL_B64=$ORIG,AIRBAG_FIXED_B64=$FIXED,AIRBAG_TEST_B64=$TEST"
# then read the verdict:
gcloud logging read 'resource.type="cloud_run_job" resource.labels.job_name="airbag-sandbox" textPayload:"AIRBAG_SANDBOX_RESULT"' --limit 1 --freshness 5m
```
