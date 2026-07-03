"""v5 Phase 4.1 — fix-path write hardening (a HARD GATE, not a flag). The fix-PR pipeline commits
LLM-CHOSEN file paths; a prompt-injected stack trace could target `.github/workflows/*.yml`, which
EXECUTES with repo secrets on push to the airbag/fix* branch. These lock the allowlist gate, the
graceful-degrade discovery, the reuse-keying, and the self-correction path threading."""
from autosre import config, fix_pipeline, github_pr
from autosre.schemas import RootCause


# --- the HARD GATE: config.fix_path_allowed --------------------------------------------------------
def test_allowlist_accepts_paths_inside_and_rejects_outside(monkeypatch):
    monkeypatch.setattr(config, "FIX_ALLOWLIST", ["target-app"])
    for ok in ("target-app/main.py", "target-app/sub/util.py", "target-app/test_regression_airbag.py"):
        assert config.fix_path_allowed(ok), ok
    for bad in (".github/workflows/x.yml", ".github/actions/a.yml", "src/foo.py", "main.py",
                "../etc/passwd", "/etc/passwd", "target-app/../.github/workflows/x.yml",
                "target-app/../../secrets", "", None, "target-app/../src/x.py", "\x00evil"):
        assert not config.fix_path_allowed(bad), bad


def test_allowlist_is_configurable(monkeypatch):
    monkeypatch.setattr(config, "FIX_ALLOWLIST", ["src", "lib"])
    assert config.fix_path_allowed("src/app.py")
    assert config.fix_path_allowed("lib/util.py")
    assert not config.fix_path_allowed("target-app/main.py")   # not in the configured allowlist


def test_github_rejected_even_if_allowlist_includes_it(monkeypatch):
    """`.github/` is rejected UNCONDITIONALLY — a misconfigured allowlist can't re-enable the vuln."""
    monkeypatch.setattr(config, "FIX_ALLOWLIST", [".github", "target-app"])
    assert not config.fix_path_allowed(".github/workflows/deploy.yml")
    assert not config.fix_path_allowed(".github/x.txt")
    assert config.fix_path_allowed("target-app/main.py")       # the legit entry still works


def test_allowlist_root_allows_any_safe_relative_path(monkeypatch):
    monkeypatch.setattr(config, "FIX_ALLOWLIST", ["."])
    assert config.fix_path_allowed("anywhere/x.py") and config.fix_path_allowed("main.py")
    assert not config.fix_path_allowed(".github/workflows/x.yml")   # .github still rejected
    assert not config.fix_path_allowed("../escape.py")             # traversal still rejected


# --- discovery degrades gracefully (defense in depth) ---------------------------------------------
def test_discover_file_rejects_disallowed_suspected_file(monkeypatch):
    monkeypatch.setattr(config, "FIX_FILE", "target-app/main.py")
    monkeypatch.setattr(config, "FIX_ALLOWLIST", ["target-app"])
    present = {"target-app/main.py": "x", ".github/workflows/ci.yml": "on: push"}
    get = lambda p: present.get(p)  # noqa: E731
    # a prompt-injected .github path IS readable, but the allowlist rejects it -> fall back to FIX_FILE
    rca = RootCause(summary="", error_signature="", suspected_file=".github/workflows/ci.yml")
    assert fix_pipeline._discover_file(rca, get) == "target-app/main.py"


# --- the write gate in open_fix_pr (mocked GitHub API): a .github path is NEVER committed ----------
class _Resp:
    def __init__(self, payload, code=200):
        self._p, self.status_code = payload, code

    def json(self):
        return self._p

    def raise_for_status(self):
        pass


class _FakeClient:
    def __init__(self):
        self.puts = []
        self.posts = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, params=None):
        if "/pulls" in url:
            return _Resp([])                                     # no open PR to reuse
        if "/git/ref/heads/" in url:
            return _Resp({"object": {"sha": "base-sha"}})
        if "/contents/" in url:
            return _Resp({}, code=404)                           # file absent on the new branch
        return _Resp({})

    def post(self, url, json=None):
        self.posts.append(url)
        if "/pulls" in url:
            return _Resp({"html_url": "http://pr/7", "number": 7})
        return _Resp({})                                         # create-ref

    def put(self, url, json=None):
        self.puts.append(url)
        return _Resp({})


def _wire(monkeypatch, build_fix_ret):
    monkeypatch.setattr(config, "GITHUB_TOKEN", "t")
    monkeypatch.setattr(config, "GITHUB_REPO", "o/r")
    monkeypatch.setattr(config, "FIX_FILE", "target-app/main.py")
    monkeypatch.setattr(config, "FIX_ALLOWLIST", ["target-app"])
    monkeypatch.setattr(github_pr.gemini, "available", lambda: True)
    monkeypatch.setattr(fix_pipeline, "build_fix", lambda *a, **k: build_fix_ret)
    client = _FakeClient()
    monkeypatch.setattr(github_pr.httpx, "Client", lambda *a, **k: client)
    return client


def test_open_fix_pr_refuses_to_commit_a_github_workflow_path(monkeypatch):
    """The marquee security test: a prompt-injected `.github/workflows/*.yml` fix path is REFUSED —
    the branch is never created, nothing is PUT, and the heal degrades to no PR (not an exploit)."""
    client = _wire(monkeypatch, {
        "path": ".github/workflows/pwn.yml", "fixed_content": "on: push\njobs: {pwn: {}}",
        "pr_title": "t", "pr_body": "b", "summary": "s"})
    assert github_pr.open_fix_pr("svc", "ctx") is None      # refused
    assert client.puts == []                                # NOTHING committed
    assert client.posts == []                               # no branch ref created either


def test_open_fix_pr_refuses_a_smuggled_disallowed_test_path(monkeypatch):
    """Second-file smuggling: the FIX path is allowlisted but the separately-LLM-chosen test_path
    (files[1]) targets `.github/`. The gate iterates EVERY committed file, so it's refused too."""
    client = _wire(monkeypatch, {
        "path": "target-app/main.py", "fixed_content": "print('fixed')",
        "test_path": ".github/workflows/pwn.yml", "test_content": "on: push\njobs: {pwn: {}}",
        "pr_title": "t", "pr_body": "b", "summary": "s"})
    assert github_pr.open_fix_pr("svc", "ctx") is None      # refused despite a clean files[0]
    assert client.puts == [] and client.posts == []         # nothing committed, no branch


def test_open_fix_pr_commits_an_allowed_path_and_returns_it(monkeypatch):
    client = _wire(monkeypatch, {
        "path": "target-app/main.py", "fixed_content": "print('fixed')",
        "test_path": "target-app/test_regression_airbag.py", "test_content": "def test(): pass",
        "pr_title": "t", "pr_body": "b", "summary": "s"})
    res = github_pr.open_fix_pr("svc", "ctx")
    assert res is not None and res["path"] == "target-app/main.py"
    assert any("/contents/target-app/main.py" in u for u in client.puts)          # fix committed
    assert any("test_regression_airbag.py" in u for u in client.puts)             # test committed
    assert not any("/contents/.github" in u for u in client.puts)                 # no workflow write


def test_open_fix_pr_reuse_is_keyed_on_incident_class(monkeypatch):
    """An open fix PR is reused ONLY for the same incident class (RCA signature), not any fix branch."""
    sig = github_pr._fix_signature("svc", "ctx", None)
    monkeypatch.setattr(config, "GITHUB_TOKEN", "t")
    monkeypatch.setattr(config, "GITHUB_REPO", "o/r")
    monkeypatch.setattr(github_pr.gemini, "available", lambda: True)

    for ref, expect_reuse in ((f"airbag/fix-{sig}-999", True),
                              ("airbag/fix-DIFFERENTCLASS-999", False)):
        class _C(_FakeClient):
            def get(self, url, params=None):
                if "/pulls" in url:
                    return _Resp([{"head": {"ref": ref}, "html_url": "http://pr/9", "number": 9}])
                return super().get(url, params)
        c = _C()
        monkeypatch.setattr(github_pr.httpx, "Client", lambda *a, **k: c)
        # a same-class open PR short-circuits (reused, no build); a different-class one does not.
        monkeypatch.setattr(fix_pipeline, "build_fix", lambda *a, **k: None)  # force fallback if not reused
        monkeypatch.setattr(github_pr, "_gemini_fix", lambda *a, **k: None)   # fallback yields nothing
        res = github_pr.open_fix_pr("svc", "ctx")
        if expect_reuse:
            assert res is not None and res["number"] == 9 and "reused" in res["summary"]
        else:
            assert res is None   # not reused, and the (mocked) build/fallback produced no fix


# --- CI self-correction refuses a disallowed path -------------------------------------------------
def test_self_correct_refuses_disallowed_path(monkeypatch):
    monkeypatch.setattr(config, "GITHUB_TOKEN", "t")
    monkeypatch.setattr(config, "GITHUB_REPO", "o/r")
    monkeypatch.setattr(config, "CI_SELF_CORRECT", True)
    monkeypatch.setattr(config, "FIX_ALLOWLIST", ["target-app"])
    monkeypatch.setattr(github_pr.gemini, "available", lambda: True)
    seen = []
    github_pr.self_correct_ci("airbag/fix-x-1", 1, "svc", "ctx",
                              lambda s, m, **k: seen.append(s), path=".github/workflows/x.yml")
    assert seen == ["CI_ESCALATED"]   # refused before any network / watch registration
