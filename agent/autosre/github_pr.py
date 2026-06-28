"""Fix-PR slow path: Gemini reads the buggy file + the incident, writes a corrected
file, and opens a real PR via the GitHub REST API. Returns the PR URL, or None if
unconfigured / no change. This is the "root-cause fix" half of the dual-path heal
(the rollback already stopped the bleeding)."""
from __future__ import annotations

import base64
import json
import logging
import threading
import time

import httpx

from . import config, gemini
from .schemas import FixResult

log = logging.getLogger("airbag.pr")
_API = "https://api.github.com"

# Only one CI watcher per branch at a time — repeated heals reuse the same open fix PR, so
# without this two watchers could race to commit corrections (the loser 409s + false-escalates).
_watch_lock = threading.Lock()
_watching: set[str] = set()


def available() -> bool:
    return bool(config.GITHUB_TOKEN and config.GITHUB_REPO and gemini.available())


def _headers() -> dict:
    return {"Authorization": f"token {config.GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json"}


def open_fix_pr(service: str, error_context: str) -> dict | None:
    if not available():
        return None
    repo, path, base = config.GITHUB_REPO, config.FIX_FILE, config.FIX_BASE
    try:
        with httpx.Client(timeout=30.0, headers=_headers()) as c:
            # Idempotency: if Airbag already has an open fix PR, reuse it instead of opening
            # a new one. Without this, every heal (incl. repeated demo runs) spams duplicate PRs.
            opens = c.get(f"{_API}/repos/{repo}/pulls", params={"state": "open", "per_page": 100})
            if opens.status_code == 200:
                for pr in opens.json():
                    if (pr.get("head", {}).get("ref", "") or "").startswith("airbag/fix"):
                        log.info("fix PR already open, reusing: %s", pr["html_url"])
                        return {"pr_url": pr["html_url"], "branch": pr["head"]["ref"],
                                "number": pr["number"],
                                "summary": "existing open fix PR (reused — not re-opened)"}

            meta = c.get(f"{_API}/repos/{repo}/contents/{path}", params={"ref": base})
            meta.raise_for_status()
            meta = meta.json()
            source = base64.b64decode(meta["content"]).decode()

            fix = _gemini_fix(service, path, source, error_context)
            if not fix or fix["fixed_content"].strip() == source.strip():
                log.warning("no usable fix produced; skipping PR")
                return None

            ref = c.get(f"{_API}/repos/{repo}/git/ref/heads/{base}")
            ref.raise_for_status()
            base_sha = ref.json()["object"]["sha"]
            branch = f"airbag/fix-{int(time.time())}"
            c.post(f"{_API}/repos/{repo}/git/refs",
                   json={"ref": f"refs/heads/{branch}", "sha": base_sha}).raise_for_status()
            c.put(f"{_API}/repos/{repo}/contents/{path}", json={
                "message": fix["pr_title"], "branch": branch,
                "content": base64.b64encode(fix["fixed_content"].encode()).decode(),
                "sha": meta["sha"],
            }).raise_for_status()
            pr = c.post(f"{_API}/repos/{repo}/pulls", json={
                "title": fix["pr_title"], "head": branch, "base": base,
                "body": fix["pr_body"] + "\n\n— opened autonomously by **Airbag** 🛟 after rolling back the bad revision.",
            })
            pr.raise_for_status()
            url = pr.json()["html_url"]
            log.info("opened fix PR: %s", url)
            return {"pr_url": url, "branch": branch, "number": pr.json()["number"],
                    "summary": fix["summary"]}
    except Exception as e:  # noqa: BLE001
        log.warning("open_fix_pr failed: %s", e)
        return None


def branch_ci(ref_runs: dict) -> tuple[str, str]:
    """Reduce a GitHub check-runs payload to (conclusion, failure_summary).
    conclusion ∈ {success, failure, pending, none}. Pure -> easy to unit-test."""
    runs = ref_runs.get("check_runs", []) if ref_runs else []
    if not runs:
        return "none", ""
    if any(cr.get("status") != "completed" for cr in runs):
        return "pending", ""
    failed = [cr for cr in runs
              if cr.get("conclusion") not in ("success", "neutral", "skipped")]
    if failed:
        summary = "; ".join(
            f"{cr.get('name')}: {((cr.get('output') or {}).get('summary') or cr.get('conclusion') or 'failed')[:200]}"
            for cr in failed)
        return "failure", summary
    return "success", ""


def _poll_ci(c: httpx.Client, repo: str, ref: str) -> tuple[str, str]:
    """Poll the branch head's checks until terminal (success/failure) or timeout."""
    deadline = time.time() + config.CI_POLL_TIMEOUT_S
    while True:
        r = c.get(f"{_API}/repos/{repo}/commits/{ref}/check-runs")
        concl, summary = branch_ci(r.json() if r.status_code == 200 else {})
        if concl in ("success", "failure"):
            return concl, summary
        if time.time() >= deadline:
            return "timeout", ""
        time.sleep(config.CI_POLL_INTERVAL_S)


def self_correct_ci(branch: str, pr_number: int, service: str, error_context: str, emit) -> None:
    """Watch the fix PR's CI; on red, feed the failure back to Gemini, commit a correction to
    the branch, retry up to MAX_CI_RETRIES, then escalate (PR comment). Runs in a background
    thread — emits CI_GREEN / CI_RED / CI_CORRECTED / CI_ESCALATED to the thought-chain."""
    if not (available() and config.CI_SELF_CORRECT):
        return
    with _watch_lock:  # one watcher per branch (repeated heals reuse the same fix PR)
        if branch in _watching:
            return
        _watching.add(branch)
    repo, path = config.GITHUB_REPO, config.FIX_FILE
    try:
        with httpx.Client(timeout=30.0, headers=_headers()) as c:
            for attempt in range(config.MAX_CI_RETRIES + 1):
                concl, summary = _poll_ci(c, repo, branch)
                if concl == "success":
                    emit("CI_GREEN", "fix PR CI is green")
                    return
                if concl == "none":
                    emit("CI_GREEN", "no CI checks to correct")
                    return
                if concl == "timeout":
                    emit("CI_WATCH", "CI did not reach a terminal state within the poll window "
                                     "— stopping watch (not marking green)")
                    return
                if attempt >= config.MAX_CI_RETRIES:
                    _comment(c, repo, pr_number,
                             f"🛟 Airbag: CI still red after {attempt} self-correction "
                             f"attempt(s) — needs a human. Last failure: {summary}")
                    emit("CI_ESCALATED", f"CI still red after {attempt} attempts — escalated")
                    return
                emit("CI_RED", f"fix PR CI failed (attempt {attempt + 1}): {summary}")
                meta = c.get(f"{_API}/repos/{repo}/contents/{path}", params={"ref": branch}).json()
                source = base64.b64decode(meta["content"]).decode()
                fix = _gemini_fix(service, path, source, error_context, ci_failure=summary)
                if not fix or fix["fixed_content"].strip() == source.strip():
                    emit("CI_ESCALATED", "Gemini produced no new correction — escalated")
                    return
                c.put(f"{_API}/repos/{repo}/contents/{path}", json={
                    "message": f"airbag: CI self-correction #{attempt + 1}", "branch": branch,
                    "sha": meta["sha"],
                    "content": base64.b64encode(fix["fixed_content"].encode()).decode(),
                }).raise_for_status()
                emit("CI_CORRECTED", f"pushed correction #{attempt + 1} to {branch}; re-running CI")
    except Exception as e:  # noqa: BLE001
        emit("CI_ESCALATED", f"CI self-correction error: {e}")
    finally:
        with _watch_lock:
            _watching.discard(branch)


def _comment(c: httpx.Client, repo: str, pr_number: int, body: str) -> None:
    try:
        c.post(f"{_API}/repos/{repo}/issues/{pr_number}/comments", json={"body": body})
    except Exception as e:  # noqa: BLE001
        log.warning("PR comment failed: %s", e)


def _gemini_fix(service: str, path: str, source: str, error_context: str,
                ci_failure: str | None = None) -> dict | None:
    try:
        from google.genai import types

        retry = (f"\n\nYour PREVIOUS fix did NOT pass CI. The CI failure was:\n{ci_failure}\n"
                 "The check runs `total_revenue(ORDERS, buggy=True)` and it must NOT raise. "
                 "Correct the file so the business path never throws in any mode.\n"
                 if ci_failure else "")
        prompt = (
            f"A Cloud Run service '{service}' shipped a bad revision that returns HTTP 500.\n"
            f"Incident: {error_context}\n\n"
            f"Here is `{path}`:\n```\n{source}\n```\n"
            f"{retry}\n"
            "Find the bug that causes the 500 and return the FULL corrected file content "
            "(no markdown fences), plus a concise PR title and body explaining the root "
            "cause and fix. Make the minimal change that fixes it."
        )
        resp = gemini._client().models.generate_content(
            model=config.GEMINI_DECISION_MODEL,  # flash is reliable + enough for a small fix
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json", response_schema=FixResult),
        )
        parsed = getattr(resp, "parsed", None)
        return parsed.model_dump() if parsed is not None else json.loads(resp.text)
    except Exception as e:  # noqa: BLE001
        log.warning("gemini fix failed: %s", e)
        return None
