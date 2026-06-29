"""CI self-correction: the check-runs reducer + the fail-closed guard. The live watch/correct
loop is exercised against real GitHub PRs; here we lock the pure logic + the disabled no-op."""
from autosre import config, github_pr


def test_branch_ci_reduces_check_runs():
    assert github_pr.branch_ci({"check_runs": []}) == ("none", "")
    assert github_pr.branch_ci({"check_runs": [{"status": "in_progress"}]})[0] == "pending"
    assert github_pr.branch_ci(
        {"check_runs": [{"status": "completed", "conclusion": "success", "name": "ci"}]}
    )[0] == "success"
    # skipped/neutral don't count as failure (e.g. the gated complete-rollback job)
    assert github_pr.branch_ci(
        {"check_runs": [{"status": "completed", "conclusion": "skipped", "name": "x"},
                        {"status": "completed", "conclusion": "success", "name": "ci"}]}
    )[0] == "success"
    concl, summary = github_pr.branch_ci({"check_runs": [
        {"status": "completed", "conclusion": "success", "name": "ci"},
        {"status": "completed", "conclusion": "failure", "name": "validate-fix",
         "output": {"summary": "FIX NOT APPLIED — KeyError"}}]})
    assert concl == "failure"
    assert "validate-fix" in summary and "FIX NOT APPLIED" in summary


def test_poll_ci_bails_on_auth_error():
    class _FakeResp:
        status_code = 403
    class _FakeClient:
        def get(self, url):
            return _FakeResp()
    concl, summary = github_pr._poll_ci(_FakeClient(), "owner/repo", "airbag/fix-1")
    assert concl == "unreadable" and "Checks:read" in summary  # bails, doesn't poll to timeout


def test_self_correct_noop_when_unavailable(monkeypatch):
    monkeypatch.setattr(config, "GITHUB_TOKEN", "")  # available() -> False
    seen = []
    github_pr.self_correct_ci("airbag/fix-1", 1, "svc", "ctx", lambda *a, **k: seen.append(a))
    assert seen == []  # returns immediately, no network, no events
