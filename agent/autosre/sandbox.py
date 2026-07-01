"""Sandbox for the LLM-authored regression test — pluggable via AIRBAG_SANDBOX.

  subprocess (default) — run pytest in a bounded local subprocess inside the agent container, with the
                         GCE metadata server neutralized. Zero extra infra; used for the demo.
  cloudrun_job         — run the test in an ISOLATED, network-egress-disabled Cloud Run Job under a
                         ZERO-PERMISSION service account (provisioned by infra/sandbox-job-setup.sh).
                         This is the production posture: executing un-sandboxed LLM-authored code in
                         the prod agent's container (which holds a run.admin SA) would contradict the
                         guarded-action moat — even with the metadata server neutralized, arbitrary
                         code could read the filesystem or exfiltrate over the network.

`verify(...)` returns {ok, why, catches_bug, fix_passes, output}. This module DISPATCHES/executes the
LLM-authored TEST; it never imports the LLM (gemini/genai) — keeping the FSM-acts/LLM-advises split.
The cloudrun_job path degrades gracefully to the subprocess path on any error, so a heal never blocks.
"""
from __future__ import annotations

import base64
import json
import logging
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import config

log = logging.getLogger("airbag.sandbox")

RESULT_MARKER = "AIRBAG_SANDBOX_RESULT:"
_MAX_ENV_B64 = 24_000  # keep each base64 env override well under Cloud Run's env-size limit


def verify(path: str, original: str, fixed: str, test_path: str, test_content: str) -> dict:
    """Prove the agent-authored test FAILS on the original (buggy) file and PASSES on the fix."""
    if not test_content.strip():
        return {"ok": False, "why": "no test produced", "output": ""}
    stem = Path(path).stem
    test_name = Path(test_path).name if test_path else "test_regression_airbag.py"
    if config.SANDBOX_BACKEND == "cloudrun_job":
        try:
            return _verify_cloudrun_job(stem, test_name, original, fixed, test_content)
        except Exception as e:  # noqa: BLE001
            # Do NOT fall back to the subprocess here: once cloudrun_job isolation is selected, running
            # untrusted LLM-authored code in the prod agent's privileged container would silently break
            # the very guarantee this feature provides. Return UNVERIFIED instead — the fix still ships
            # (flagged), and CI + the production canary remain the backstops. The heal never blocks.
            log.warning("cloudrun_job sandbox unavailable (%s); reporting UNVERIFIED (not running "
                        "untrusted code in the prod container)", e)
            return {"ok": False, "why": f"cloudrun_job sandbox unavailable: {e}",
                    "catches_bug": False, "fix_passes": False, "output": ""}
    return _verify_subprocess(stem, test_name, original, fixed, test_content)


# --- subprocess backend (default) --------------------------------------------------------------
def _run_pytest(cwd: Path, test_name: str) -> dict:
    try:
        # A stripped env alone does NOT stop google.auth.default() in the LLM-authored test from
        # minting the agent's run.admin token via the metadata endpoint, so point it at an
        # unreachable host/IP and null out file creds. (The cloudrun_job backend removes this risk
        # entirely by running under a zero-permission SA with no network egress.)
        import os
        p = subprocess.run([sys.executable, "-m", "pytest", "-q", test_name],
                           cwd=str(cwd), capture_output=True, text=True, timeout=60,
                           env={"PYTHONPATH": str(cwd), "PATH": os.environ.get("PATH", ""),
                                "GCE_METADATA_HOST": "metadata.invalid", "GCE_METADATA_IP": "0.0.0.0",
                                "GOOGLE_APPLICATION_CREDENTIALS": "/dev/null"})
        return {"rc": p.returncode, "out": (p.stdout + p.stderr)[-1200:]}
    except Exception as e:  # noqa: BLE001
        return {"rc": -1, "out": f"pytest run error: {e}"}


def _verify_subprocess(stem: str, test_name: str, original: str, fixed: str, test_content: str) -> dict:
    try:
        with tempfile.TemporaryDirectory() as d:
            tdir = Path(d)
            module = tdir / f"{stem}.py"
            tf = tdir / test_name
            tf.write_text(test_content)
            module.write_text(fixed)               # the fix must PASS
            fixed_run = _run_pytest(tdir, test_name)
            module.write_text(original)            # the original must FAIL (test catches the bug)
            orig_run = _run_pytest(tdir, test_name)
        return _verdict(fixed_run["rc"], orig_run["rc"], fixed_run["out"] + "\n" + orig_run["out"])
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "why": f"sandbox error: {e}", "output": str(e)}


def _verdict(fixed_rc: int, orig_rc: int, output: str) -> dict:
    catches_bug = orig_rc != 0
    fix_passes = fixed_rc == 0
    ok = catches_bug and fix_passes
    return {"ok": ok, "why": "verified" if ok else f"catches_bug={catches_bug} fix_passes={fix_passes}",
            "catches_bug": catches_bug, "fix_passes": fix_passes, "output": output[-1500:]}


# --- cloudrun_job backend (egress-disabled, zero-permission SA) ---------------------------------
def _b64(s: str) -> str:
    return base64.b64encode(s.encode("utf-8")).decode("ascii")


def _verify_cloudrun_job(stem: str, test_name: str, original: str, fixed: str, test_content: str) -> dict:
    env = {"AIRBAG_STEM": stem, "AIRBAG_TEST_NAME": test_name,
           "AIRBAG_ORIGINAL_B64": _b64(original), "AIRBAG_FIXED_B64": _b64(fixed),
           "AIRBAG_TEST_B64": _b64(test_content)}
    too_big = [k for k, v in env.items() if len(v) > _MAX_ENV_B64]
    if too_big:
        raise ValueError(f"inputs too large for env override ({too_big}); use the subprocess sandbox")

    from google.cloud import run_v2
    client = run_v2.JobsClient()
    job = client.job_path(config.GCP_PROJECT, config.SANDBOX_JOB_REGION, config.SANDBOX_JOB_NAME)
    overrides = run_v2.RunJobRequest.Overrides(
        container_overrides=[run_v2.RunJobRequest.Overrides.ContainerOverride(
            env=[run_v2.EnvVar(name=k, value=v) for k, v in env.items()])],
        task_count=1)
    op = client.run_job(request=run_v2.RunJobRequest(name=job, overrides=overrides))
    execution = op.result(timeout=config.SANDBOX_JOB_TIMEOUT_S)   # runner always exits 0 -> completes
    exec_name = (execution.name or "").split("/")[-1]
    verdict = _read_job_verdict(exec_name)
    if verdict is not None:
        log.info("cloudrun_job sandbox verdict for %s: ok=%s", exec_name, verdict.get("ok"))
        return verdict
    # The runner always exits 0 and logs the verdict marker; if we can't read it back (log ingestion
    # lag / permissions), treat as UNVERIFIED — never claim a fix is verified without the proof.
    return {"ok": False, "why": "sandbox job ran but the verdict was unreadable from logs",
            "catches_bug": False, "fix_passes": False, "output": ""}


def _read_job_verdict(exec_name: str, attempts: int = 6, interval_s: float = 5.0) -> dict | None:
    """Read the RESULT_MARKER line the runner logged (stdout is captured by the control plane even
    though the container has no network egress). Polls for Cloud Logging ingestion lag."""
    from google.cloud import logging as cloud_logging
    flt = (f'resource.type="cloud_run_job" '
           f'resource.labels.job_name="{config.SANDBOX_JOB_NAME}" '
           f'labels."run.googleapis.com/execution_name"="{exec_name}" '
           f'textPayload:"{RESULT_MARKER}"')
    client = cloud_logging.Client(project=config.GCP_PROJECT)
    for _ in range(attempts):
        for e in client.list_entries(filter_=flt, resource_names=[f"projects/{config.GCP_PROJECT}"],
                                     order_by=cloud_logging.DESCENDING, max_results=5):
            payload = getattr(e, "payload", "") or ""
            if isinstance(payload, str) and RESULT_MARKER in payload:
                try:
                    return json.loads(payload.split(RESULT_MARKER, 1)[1].strip())
                except Exception:  # noqa: BLE001
                    continue
        time.sleep(interval_s)
    return None
