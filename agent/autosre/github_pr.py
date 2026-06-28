"""Fix-PR slow path: Gemini reads the buggy file + the incident, writes a corrected
file, and opens a real PR via the GitHub REST API. Returns the PR URL, or None if
unconfigured / no change. This is the "root-cause fix" half of the dual-path heal
(the rollback already stopped the bleeding)."""
from __future__ import annotations

import base64
import json
import logging
import time

import httpx

from . import config, gemini
from .schemas import FixResult

log = logging.getLogger("airbag.pr")
_API = "https://api.github.com"


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
            meta = c.get(f"{_API}/repos/{repo}/contents/{path}", params={"ref": base})
            meta.raise_for_status()
            meta = meta.json()
            source = base64.b64decode(meta["content"]).decode()

            fix = _gemini_fix(service, path, source, error_context)
            if not fix or fix["fixed_content"].strip() == source.strip():
                log.warning("no usable fix produced; skipping PR")
                return None

            base_sha = c.get(f"{_API}/repos/{repo}/git/ref/heads/{base}").json()["object"]["sha"]
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
            return {"pr_url": url, "branch": branch, "summary": fix["summary"]}
    except Exception as e:  # noqa: BLE001
        log.warning("open_fix_pr failed: %s", e)
        return None


def _gemini_fix(service: str, path: str, source: str, error_context: str) -> dict | None:
    try:
        from google.genai import types

        prompt = (
            f"A Cloud Run service '{service}' shipped a bad revision that returns HTTP 500.\n"
            f"Incident: {error_context}\n\n"
            f"Here is `{path}`:\n```\n{source}\n```\n\n"
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
