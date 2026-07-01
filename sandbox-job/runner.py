"""Airbag sandbox-job runner — runs the LLM-authored regression test in an ISOLATED, egress-disabled
Cloud Run Job (not in the prod agent's container).

It reproduces the fix-pipeline's self-proving check: the agent-authored test must FAIL against the
original (buggy) file and PASS against the fixed file. Inputs arrive as base64-encoded env vars (set
per-execution via RunJobRequest overrides), so no code or secret is baked into the image. The verdict
is printed as a single marker line the agent reads back from Cloud Logging. The process ALWAYS exits 0
(the verdict lives in the marker, not the exit code) so the execution reliably completes and the
agent's read-back is never confounded by a failed-execution long-running-operation.

Standalone by design: this runs in a minimal python+pytest image with NO autosre on the path and NO
network egress, so a Gemini-authored test cannot reach the metadata server, GCP APIs, or the internet.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

RESULT_MARKER = "AIRBAG_SANDBOX_RESULT:"


def _b64(name: str) -> str:
    raw = os.environ.get(name, "")
    if not raw:
        return ""
    try:
        return base64.b64decode(raw).decode("utf-8")
    except Exception:  # noqa: BLE001
        return ""


def _run_pytest(cwd: Path, test_name: str) -> dict:
    try:
        p = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", test_name],
            cwd=str(cwd), capture_output=True, text=True, timeout=60,
            # minimal env; the container itself is egress-disabled, but keep the metadata server
            # neutralized as defense-in-depth in case the image is ever run without the VPC lock.
            env={"PYTHONPATH": str(cwd), "PATH": os.environ.get("PATH", ""),
                 "GCE_METADATA_HOST": "metadata.invalid", "GCE_METADATA_IP": "0.0.0.0",
                 "GOOGLE_APPLICATION_CREDENTIALS": "/dev/null"})
        return {"rc": p.returncode, "out": (p.stdout + p.stderr)[-1200:]}
    except Exception as e:  # noqa: BLE001
        return {"rc": -1, "out": f"pytest run error: {e}"}


def verify(stem: str, test_name: str, original: str, fixed: str, test_content: str) -> dict:
    if not test_content.strip():
        return {"ok": False, "why": "no test produced", "output": ""}
    with tempfile.TemporaryDirectory() as d:
        tdir = Path(d)
        module = tdir / f"{stem}.py"
        tf = tdir / test_name
        tf.write_text(test_content)
        module.write_text(fixed)               # the fix must PASS
        fixed_run = _run_pytest(tdir, test_name)
        module.write_text(original)            # the original must FAIL (the test catches the bug)
        orig_run = _run_pytest(tdir, test_name)
    catches_bug = orig_run["rc"] != 0
    fix_passes = fixed_run["rc"] == 0
    ok = catches_bug and fix_passes
    return {"ok": ok, "why": "verified" if ok else f"catches_bug={catches_bug} fix_passes={fix_passes}",
            "catches_bug": catches_bug, "fix_passes": fix_passes,
            "output": (fixed_run["out"] + "\n" + orig_run["out"])[-1500:]}


def main() -> int:
    stem = os.environ.get("AIRBAG_STEM", "main")
    test_name = os.environ.get("AIRBAG_TEST_NAME", "test_regression_airbag.py")
    result = verify(stem, test_name,
                    original=_b64("AIRBAG_ORIGINAL_B64"),
                    fixed=_b64("AIRBAG_FIXED_B64"),
                    test_content=_b64("AIRBAG_TEST_B64"))
    # single-line marker the agent greps out of Cloud Logging (stdout is captured by the control
    # plane, not the container's — blocked — network). Always exit 0: the verdict is the marker.
    print(RESULT_MARKER + json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
