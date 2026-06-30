"""v2 agentic fix pipeline — the marquee upgrade over a single-call rewrite.

  fetch real error logs ─► RCA (structured RootCause) ─► discover the culprit file from the
  stack trace ─► patch + author a regression test (Gemini patch model) ─► SANDBOX-VERIFY the
  test FAILS on the bug and PASSES on the fix (iterate up to N) ─► hand the fix + test to the PR.

The PR self-proves: it ships a regression test the agent wrote, already shown to catch the bug
and pass on the fix in a local sandbox — no human-pre-planted oracle. Gemini still only PROPOSES
code; the deterministic state machine + CI + the production canary remain the gates that decide.

Failure is graceful: any step returning None makes the caller fall back to the simple single-call
fix (github_pr._gemini_fix), so the heal never blocks on the pipeline.
"""
from __future__ import annotations

import logging
import subprocess
import sys
import tempfile
from pathlib import Path

from . import config, gemini, tools
from .schemas import FixResult, RootCause

log = logging.getLogger("airbag.fix")

MAX_FIX_ITERS = int(__import__("os").getenv("AIRBAG_MAX_FIX_ITERS", "2"))


def available() -> bool:
    return gemini.available()


def build_fix(service: str, error_context: str, get_file) -> dict | None:
    """Run the full pipeline. `get_file(path) -> str | None` fetches repo file content (the caller
    — github_pr — provides it via the GitHub API). Returns a dict the caller commits, or None."""
    if not available():
        return None
    try:
        logs = tools.fetch_error_logs(service, config.GCP_REGION)
        rca = _rca(service, error_context, logs)
        if not rca:
            return None
        path = _discover_file(rca, get_file)
        source = get_file(path)
        if not source:
            log.warning("fix_pipeline: could not read %s", path)
            return None

        sandbox_out = ""
        for attempt in range(MAX_FIX_ITERS + 1):
            fix = _patch_and_test(service, path, source, rca, logs, prior_failure=sandbox_out)
            if not fix or fix.fixed_content.strip() == source.strip():
                if attempt == 0:
                    return None
                break
            v = _sandbox_verify(path, source, fix.fixed_content, fix.test_path, fix.test_content)
            sandbox_out = v.get("output", "")
            if v.get("ok"):
                log.info("fix_pipeline: sandbox-verified on attempt %d", attempt + 1)
                return _result(rca, path, fix, v)
            log.warning("fix_pipeline: sandbox attempt %d not verified (%s)", attempt + 1, v.get("why"))
        # exhausted iterations — still ship the last fix, flagged unverified (CI is the backstop)
        return _result(rca, path, fix, v)
    except Exception as e:  # noqa: BLE001
        log.warning("fix_pipeline.build_fix failed, caller will fall back: %s", e)
        return None


def _result(rca: RootCause, path: str, fix: FixResult, v: dict) -> dict:
    proof = ("✅ regression test fails on the bug and passes on the fix (verified in a sandbox "
             "before this PR)" if v.get("ok") else
             "⚠️ sandbox could not fully verify locally — relying on CI + the production canary")
    body = (f"{fix.pr_body}\n\n---\n**Root cause** (`{rca.error_signature}`): {rca.summary}\n\n"
            f"**Self-proving test** `{fix.test_path}` — {proof}.\n\n"
            "— opened autonomously by **Airbag** 🛟 after rolling back the bad revision. "
            "Gemini proposed this; CI + the production canary verify it before the rollback is undone.")
    return {"path": path, "fixed_content": fix.fixed_content,
            "test_path": fix.test_path, "test_content": fix.test_content,
            "pr_title": fix.pr_title, "pr_body": body, "summary": fix.summary,
            "root_cause": rca.model_dump(), "sandbox_ok": bool(v.get("ok"))}


def _rca(service: str, error_context: str, logs: list[str]) -> RootCause | None:
    from google.genai import types
    joined = "\n---\n".join(logs)[:6000] or "(no error logs available)"
    prompt = (
        f"You are an SRE doing root-cause analysis for Cloud Run service '{service}'.\n"
        f"Incident: {error_context}\n\nRecent ERROR logs / stack traces:\n```\n{joined}\n```\n\n"
        "Identify the root cause. Return the error_signature (exception + key message), the "
        "suspected_file (repo-relative path parsed from the deepest application stack frame, e.g. "
        "'target-app/main.py'), the suspected_symbol (function), and a one-paragraph summary + "
        "hypothesis for why this revision started failing.")
    try:
        resp = gemini._client().models.generate_content(
            model=config.GEMINI_PATCH_MODEL, contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json",
                                               response_schema=RootCause))
        parsed = getattr(resp, "parsed", None)
        return parsed if parsed is not None else RootCause.model_validate_json(resp.text)
    except Exception as e:  # noqa: BLE001
        log.warning("RCA failed: %s", e)
        return None


def _discover_file(rca: RootCause, get_file) -> str:
    """Use the file the stack trace implicates (validated by trying to read it); else fall back to
    the configured FIX_FILE. Kills the v1 hardcoded-single-file assumption."""
    cand = (rca.suspected_file or "").strip().lstrip("/")
    if cand and get_file(cand):
        return cand
    base = cand.split("/")[-1] if cand else ""
    if base and base != cand and get_file(f"target-app/{base}"):
        return f"target-app/{base}"
    return config.FIX_FILE


def _patch_and_test(service: str, path: str, source: str, rca: RootCause,
                    logs: list[str], prior_failure: str = "") -> FixResult | None:
    from google.genai import types
    stem = Path(path).stem
    retry = (f"\n\nA PREVIOUS attempt did not pass the sandbox. Sandbox output:\n{prior_failure[:1500]}\n"
             "Fix BOTH the code and/or the test so the test fails on the original bug and passes "
             "on your fix.\n" if prior_failure else "")
    prompt = (
        f"Root cause for '{service}': {rca.error_signature} — {rca.summary}\n"
        f"Here is `{path}`:\n```\n{source}\n```\n{retry}\n"
        "Return: (1) fixed_content — the FULL corrected file (no markdown fences), the minimal "
        "change that fixes the root cause; (2) a regression test — test_path like "
        f"'target-app/test_regression_airbag.py' and test_content: a self-contained pytest "
        f"module that imports the module under test as `{stem}` (it will be importable by that name) "
        "and ASSERTS the previously-broken behaviour now works (it must FAIL against the original "
        "buggy file and PASS against your fix); (3) pr_title, pr_body, summary.")
    try:
        resp = gemini._client().models.generate_content(
            model=config.GEMINI_PATCH_MODEL, contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json",
                                               response_schema=FixResult))
        parsed = getattr(resp, "parsed", None)
        fix = parsed if parsed is not None else FixResult.model_validate_json(resp.text)
        if not fix.test_path:
            fix.test_path = "target-app/test_regression_airbag.py"
        return fix
    except Exception as e:  # noqa: BLE001
        log.warning("patch+test failed: %s", e)
        return None


def _sandbox_verify(path: str, original: str, fixed: str, test_path: str, test_content: str) -> dict:
    """Run the agent-authored test against the ORIGINAL (expect fail) and the FIXED (expect pass)
    file in an isolated temp dir. A real product would use gVisor/a Cloud Run Job; this bounded
    subprocess is enough to self-prove the demo fix before the PR."""
    if not test_content.strip():
        return {"ok": False, "why": "no test produced", "output": ""}
    stem = Path(path).stem
    try:
        with tempfile.TemporaryDirectory() as d:
            tdir = Path(d)
            (tdir / f"{stem}.py").write_text(fixed)
            tf = tdir / Path(test_path).name
            tf.write_text(test_content)
            fixed_run = _run_pytest(tdir, tf)
            (tdir / f"{stem}.py").write_text(original)  # now prove the test CATCHES the bug
            orig_run = _run_pytest(tdir, tf)
        catches_bug = orig_run["rc"] != 0
        fix_passes = fixed_run["rc"] == 0
        ok = catches_bug and fix_passes
        why = ("verified" if ok else
               f"catches_bug={catches_bug} fix_passes={fix_passes}")
        return {"ok": ok, "why": why, "catches_bug": catches_bug, "fix_passes": fix_passes,
                "output": (fixed_run["out"] + "\n" + orig_run["out"])[-1500:]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "why": f"sandbox error: {e}", "output": str(e)}


def _run_pytest(cwd: Path, test_file: Path) -> dict:
    try:
        # Minimal env + neutralize the GCE metadata server: a stripped env alone does NOT stop
        # google.auth.default() in the LLM-authored test from minting the agent's run.admin token via
        # the metadata endpoint, so point it at an unreachable host/IP and null out file creds.
        p = subprocess.run([sys.executable, "-m", "pytest", "-q", str(test_file.name)],
                           cwd=str(cwd), capture_output=True, text=True, timeout=60,
                           env={"PYTHONPATH": str(cwd), "PATH": __import__("os").environ.get("PATH", ""),
                                "GCE_METADATA_HOST": "metadata.invalid", "GCE_METADATA_IP": "0.0.0.0",
                                "GOOGLE_APPLICATION_CREDENTIALS": "/dev/null"})
        return {"rc": p.returncode, "out": (p.stdout + p.stderr)[-1200:]}
    except Exception as e:  # noqa: BLE001
        return {"rc": -1, "out": f"pytest run error: {e}"}
