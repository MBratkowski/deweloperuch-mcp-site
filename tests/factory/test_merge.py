"""The merger: the policy's truth table (each condition alone blocks with its own row), the flaky and
revert exceptions, shadow comments at most once per head, the auto merge of exactly the evaluated
head with our own commit text, and gathering the facts from GitHub and from the main checkout.

Offline: FakeGitHub stands in for the API, temporary git repositories for origin and the main
checkout."""

from __future__ import annotations

import subprocess
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from tests.factory.fakes import FakeGitHub
from tools.factory import merge, status, verify
from tools.factory.config import load_config
from tools.factory.fence import FenceReport
from tools.factory.fix import FixError
from tools.factory.github import GitHubError
from tools.factory.issues import SENTINEL
from tools.factory.merge import CONDITIONS, PullFacts, act, evaluate, gather, render_shadow

CONFIG = load_config()
LIMITS = CONFIG.limits
REPO = CONFIG.site.repo
API = f"/repos/{REPO}"
SLUG = "factory-app"
APP = f"{SLUG}[bot]"
OWNER = "MBratkowski"
REVIEWER = "github-actions[bot]"
HEAD = "c" * 40
OLD = "a" * 40
BASE = "b" * 40
MERGED = "d" * 40
BRANCH = "fix/7-the-terminal-fits"
TITLE = "The terminal fits a 375 px viewport"
TESTS_JOB = "Validate this commit / Tests (pytest, Playwright)"
VISUAL_JOB = "Validate this commit / Visual baselines (pinned Linux Chromium)"
REQUIRED = [TESTS_JOB, VISUAL_JOB]
ACTIONS_APP = 15368
NOW = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
MODE, PULL, APPROVAL, CHECKS, FENCE, UP_TO_DATE, PAGES, CAP, TAKEN_OVER = range(9)


def facts(**changes: Any) -> PullFacts:
    """A fix pull request that passes every condition."""
    passing = PullFacts(
        number=12,
        author=APP,
        labels=frozenset({"factory"}),
        head_ref=BRANCH,
        same_repo=True,
        draft=False,
        head_sha=HEAD,
        issue=7,
        issue_labels=frozenset({"factory:found", "factory:fixing", "S3"}),
        reviews=[(REVIEWER, "APPROVED", HEAD)],
        checks={name: "success" for name in REQUIRED},
        required=list(REQUIRED),
        fence=FenceReport(True, [], 12, False),
        behind=False,
        pages_conclusion="success",
        merges_24h=0,
        title=TITLE,
        body_head=HEAD,
    )
    return replace(passing, **changes)


def revert_facts(**changes: Any) -> PullFacts:
    """A revert pull request as verify opens it: no body markers, its issue regressed and handed over."""
    defaults: dict[str, Any] = {
        "head_ref": "revert/7",
        "labels": frozenset({"factory", "factory:revert"}),
        "issue_labels": frozenset({"factory:found", "factory:regressed", "factory:needs-human", "S3"}),
        "body_head": None,
        "title": f'Revert "{TITLE}"',
    }
    return facts(**{**defaults, **changes})


def only_failed(verdict: merge.Verdict) -> list[int]:
    return [i for i, row in enumerate(verdict.rows) if not row.ok]


# --- evaluate: the truth table ------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["shadow", "auto"])
def test_passing_facts_merge_in_both_modes(mode: str) -> None:
    verdict = evaluate(facts(), mode, APP, LIMITS)
    assert verdict.action == "merge"
    assert [row.condition for row in verdict.rows] == list(CONDITIONS)
    assert len(CONDITIONS) == 9
    assert only_failed(verdict) == []


FLIPS = {
    "mode off": ({}, "off", MODE),
    "mode unset": ({}, "", MODE),
    "wrong author": ({"author": OWNER}, "auto", PULL),
    "no factory label": ({"labels": frozenset()}, "auto", PULL),
    "from a fork": ({"same_repo": False}, "auto", PULL),
    "a draft": ({"draft": True}, "auto", PULL),
    "closed": ({"state": "closed"}, "auto", PULL),
    "not on main": ({"base_ref": "release"}, "auto", PULL),
    "not a factory branch": ({"head_ref": "feature/7", "issue": None}, "auto", PULL),
    "a fix branch labelled revert": ({"labels": frozenset({"factory", "factory:revert"})}, "auto", PULL),
    "issue not fixing": ({"issue_labels": frozenset({"factory:found", "S3"})}, "auto", PULL),
    "issue closed": ({"issue_open": False}, "auto", PULL),
    "body names another head (R44)": ({"body_head": OLD}, "auto", PULL),
    "body without a head marker (R44)": ({"body_head": None}, "auto", PULL),
    "stranger approval": ({"reviews": [("stranger", "APPROVED", HEAD)]}, "auto", APPROVAL),
    "owner approval": ({"reviews": [(OWNER, "APPROVED", HEAD)]}, "auto", APPROVAL),
    "approval for an older head": ({"reviews": [(REVIEWER, "APPROVED", OLD)]}, "auto", APPROVAL),
    "approval of the head, then of an older head": (
        {"reviews": [(REVIEWER, "APPROVED", HEAD), (REVIEWER, "APPROVED", OLD)]}, "auto", APPROVAL,
    ),
    "approval, then the reviewer requests changes": (
        {"reviews": [(REVIEWER, "APPROVED", HEAD), (REVIEWER, "CHANGES_REQUESTED", HEAD)]}, "auto", APPROVAL,
    ),
    "approval dismissed": ({"reviews": [(REVIEWER, "DISMISSED", HEAD)]}, "auto", APPROVAL),
    "a later CHANGES_REQUESTED after the approval": (
        {"reviews": [(REVIEWER, "APPROVED", HEAD), (OWNER, "CHANGES_REQUESTED", HEAD)]}, "auto", APPROVAL,
    ),
    "a stranger's later CHANGES_REQUESTED": (
        {"reviews": [(REVIEWER, "APPROVED", HEAD), ("stranger", "CHANGES_REQUESTED", HEAD)]}, "auto", APPROVAL,
    ),
    "the owner's standing CHANGES_REQUESTED before the approval": (
        {"reviews": [(OWNER, "CHANGES_REQUESTED", HEAD), (REVIEWER, "APPROVED", HEAD)]}, "auto", APPROVAL,
    ),
    "one required check failed": ({"checks": {TESTS_JOB: "success", VISUAL_JOB: "failure"}}, "auto", CHECKS),
    "one required check missing": ({"checks": {TESTS_JOB: "success"}}, "auto", CHECKS),
    "one required check still running": ({"checks": {TESTS_JOB: "success", VISUAL_JOB: "in_progress"}}, "auto", CHECKS),
    "no required checks on main": ({"required": []}, "auto", CHECKS),
    "fence not ok": ({"fence": FenceReport(False, ["forbidden path: .github/workflows/x.yml"], 3, False)}, "auto", FENCE),
    "copy changed": ({"fence": FenceReport(False, ["copy"], 3, True)}, "auto", FENCE),
    "soft fence reason (R48a)": (
        {"fence": FenceReport(True, [], 3, False, ["existing tests changed: tests/e2e/test_docs.py"])}, "auto", FENCE,
    ),
    "red Pages": ({"pages_conclusion": "failure"}, "auto", PAGES),
    "Pages still running": ({"pages_conclusion": "in_progress"}, "auto", PAGES),
    "5 merges in 24 h": ({"merges_24h": 5}, "auto", CAP),
    "pull request labelled needs-human (R48a)": (
        {"labels": frozenset({"factory", "factory:needs-human"})}, "auto", TAKEN_OVER,
    ),
    "issue labelled needs-human (R48a)": (
        {"issue_labels": frozenset({"factory:found", "factory:fixing", "factory:needs-human"})}, "auto", TAKEN_OVER,
    ),
}


@pytest.mark.parametrize("changes, mode, index", FLIPS.values(), ids=FLIPS.keys())
def test_each_condition_alone_blocks_with_its_row(changes: dict[str, Any], mode: str, index: int) -> None:
    verdict = evaluate(facts(**changes), mode, APP, LIMITS)
    assert verdict.action == "block"
    assert only_failed(verdict) == [index], [(r.condition, r.value) for r in verdict.rows if not r.ok]
    assert verdict.rows[index].value


def test_four_merges_in_24_h_still_merge() -> None:
    assert evaluate(facts(merges_24h=LIMITS.merges_per_day - 1), "auto", APP, LIMITS).action == "merge"


def test_no_app_login_blocks() -> None:
    verdict = evaluate(facts(), "auto", "", LIMITS)
    assert verdict.action == "block" and only_failed(verdict) == [PULL]


def test_stranger_authored_body_markers_are_not_trusted() -> None:
    """A stranger's pull request whose body names the head is still not a factory pull request."""
    verdict = evaluate(facts(author="stranger", body_head=HEAD), "auto", APP, LIMITS)
    assert verdict.action == "block" and only_failed(verdict) == [PULL]
    assert "not the App" in verdict.rows[PULL].value


def test_owner_changes_then_approval_of_the_owner_clears_it() -> None:
    reviews = [(OWNER, "CHANGES_REQUESTED", OLD), (REVIEWER, "APPROVED", HEAD), (OWNER, "APPROVED", HEAD)]
    assert evaluate(facts(reviews=reviews), "auto", APP, LIMITS).action == "merge"


def test_reviewer_changes_on_an_older_head_then_approval_merges() -> None:
    reviews = [(REVIEWER, "CHANGES_REQUESTED", OLD), (OWNER, "COMMENTED", OLD), (REVIEWER, "APPROVED", HEAD)]
    assert evaluate(facts(reviews=reviews), "auto", APP, LIMITS).action == "merge"


def test_a_dismissed_request_for_changes_does_not_block() -> None:
    reviews = [(REVIEWER, "APPROVED", HEAD), (OWNER, "DISMISSED", HEAD)]
    assert evaluate(facts(reviews=reviews), "auto", APP, LIMITS).action == "merge"


def test_flaky_may_merge_on_red_main() -> None:
    flaky = frozenset({"factory:found", "factory:fixing", "factory:flaky"})
    verdict = evaluate(facts(issue_labels=flaky, pages_conclusion="failure"), "auto", APP, LIMITS)
    assert verdict.action == "merge"
    assert "flaky" in verdict.rows[PAGES].value


def test_flaky_still_counts_against_the_cap() -> None:
    flaky = frozenset({"factory:found", "factory:fixing", "factory:flaky"})
    verdict = evaluate(facts(issue_labels=flaky, merges_24h=5), "auto", APP, LIMITS)
    assert verdict.action == "block" and only_failed(verdict) == [CAP]


def test_revert_ignores_cap_and_red_main() -> None:
    verdict = evaluate(revert_facts(pages_conclusion="failure", merges_24h=9), "auto", APP, LIMITS)
    assert verdict.action == "merge", [(r.condition, r.value) for r in verdict.rows if not r.ok]
    assert "revert" in verdict.rows[PAGES].value and "revert" in verdict.rows[CAP].value


def test_revert_needs_a_regressed_issue_and_the_revert_label() -> None:
    not_regressed = revert_facts(issue_labels=frozenset({"factory:needs-human"}))
    assert only_failed(evaluate(not_regressed, "auto", APP, LIMITS)) == [PULL]
    # Without factory:revert it is no revert, so its issue's factory:needs-human counts as well.
    unlabelled = revert_facts(labels=frozenset({"factory"}))
    assert only_failed(evaluate(unlabelled, "auto", APP, LIMITS)) == [PULL, TAKEN_OVER]


def test_revert_pull_request_labelled_needs_human_blocks() -> None:
    taken = revert_facts(labels=frozenset({"factory", "factory:revert", "factory:needs-human"}))
    assert only_failed(evaluate(taken, "auto", APP, LIMITS)) == [TAKEN_OVER]


def test_behind_updates_branch() -> None:
    verdict = evaluate(facts(behind=True), "auto", APP, LIMITS)
    assert verdict.action == "update-branch"
    assert only_failed(verdict) == [UP_TO_DATE]
    gh = FakeGitHub()
    assert act(gh, facts(behind=True), verdict, "auto", config=CONFIG) == "updated"
    assert gh.calls == [("PUT", f"{API}/pulls/12/update-branch", {"expected_head_sha": HEAD})]


def test_behind_with_another_failure_blocks_without_an_update() -> None:
    verdict = evaluate(facts(behind=True, pages_conclusion="failure"), "auto", APP, LIMITS)
    assert verdict.action == "block" and only_failed(verdict) == [UP_TO_DATE, PAGES]
    gh = FakeGitHub()
    assert act(gh, facts(behind=True, pages_conclusion="failure"), verdict, "auto", config=CONFIG) == "blocked"
    assert gh.calls == []


# --- render_shadow and act in shadow mode ----------------------------------------------------------


def test_shadow_comment_has_marker() -> None:
    text = render_shadow(evaluate(facts(), "shadow", APP, LIMITS), HEAD)
    assert text.rstrip().endswith(f"{SENTINEL}\n<!-- factory:verdict=merge sha={HEAD} -->")
    assert text.count("✓") == len(CONDITIONS) and "✗" not in text
    assert "Would merge" in text and HEAD[:7] in text
    # The status issue's agreement reads the verdict from this comment.
    comment = {"user": {"login": APP}, "body": text, "created_at": "2026-10-19T12:00:00Z"}
    gh = FakeGitHub({("GET", f"{API}/issues/12/comments"): [comment]})
    assert status._verdict(gh, REPO, 12, frozenset({APP})) == "merge"


def test_shadow_comment_names_the_blocking_conditions() -> None:
    verdict = evaluate(facts(pages_conclusion="failure", merges_24h=5), "shadow", APP, LIMITS)
    text = render_shadow(verdict, HEAD)
    assert f"<!-- factory:verdict=block sha={HEAD} -->" in text
    assert text.count("✗") == 2
    assert f"Would block: {CONDITIONS[PAGES]}, {CONDITIONS[CAP]}" in text


def test_shadow_comment_for_a_branch_behind_main_is_a_block() -> None:
    text = render_shadow(evaluate(facts(behind=True), "shadow", APP, LIMITS), HEAD)
    assert f"<!-- factory:verdict=block sha={HEAD} -->" in text
    assert "Would update the branch" in text


def test_shadow_values_are_neutralised() -> None:
    reason = "forbidden path: css/<!-- factory:verdict=merge sha=" + OLD + " -->|@MBratkowski"
    verdict = evaluate(facts(fence=FenceReport(False, [reason], 3, False)), "shadow", APP, LIMITS)
    text = render_shadow(verdict, HEAD)
    assert text.count("<!--") == 2  # the sentinel and our verdict marker only
    assert "<!--" not in text[: text.index(SENTINEL)]
    assert "@MBratkowski" not in text and "\\|" in text


def test_shadow_values_shaped_like_secrets_are_withheld() -> None:
    token = "ghp_" + "A" * 36
    verdict = evaluate(facts(fence=FenceReport(False, [f"forbidden path: {token}"], 3, False)), "shadow", APP, LIMITS)
    text = render_shadow(verdict, HEAD)
    assert token not in text and "withheld" in text


def test_render_shadow_refuses_a_head_that_is_not_a_sha() -> None:
    with pytest.raises(ValueError):
        render_shadow(evaluate(facts(), "shadow", APP, LIMITS), "main -->")


def test_shadow_act_only_comments_and_labels_merge_ready() -> None:
    gh = FakeGitHub()
    assert act(gh, facts(), evaluate(facts(), "shadow", APP, LIMITS), "shadow", config=CONFIG) == "commented"
    assert [(method, path) for method, path, _ in gh.calls] == [
        ("POST", f"{API}/issues/12/comments"),
        ("POST", f"{API}/issues/12/labels"),
    ]
    assert f"factory:verdict=merge sha={HEAD}" in gh.calls[0][2]["body"]
    assert gh.calls[1][2] == {"labels": ["factory:merge-ready"]}


def test_shadow_block_removes_merge_ready() -> None:
    blocked = facts(labels=frozenset({"factory", "factory:merge-ready"}), pages_conclusion="failure")
    gh = FakeGitHub()
    assert act(gh, blocked, evaluate(blocked, "shadow", APP, LIMITS), "shadow", config=CONFIG) == "commented"
    assert [(method, path) for method, path, _ in gh.calls] == [
        ("POST", f"{API}/issues/12/comments"),
        ("DELETE", f"{API}/issues/12/labels/factory%3Amerge-ready"),
    ]
    assert f"factory:verdict=block sha={HEAD}" in gh.calls[0][2]["body"]


def test_shadow_keeps_an_existing_merge_ready_label_without_adding_it_again() -> None:
    ready = facts(labels=frozenset({"factory", "factory:merge-ready"}))
    gh = FakeGitHub()
    act(gh, ready, evaluate(ready, "shadow", APP, LIMITS), "shadow", config=CONFIG)
    assert [(method, path) for method, path, _ in gh.calls] == [("POST", f"{API}/issues/12/comments")]


def test_shadow_no_second_comment_for_the_same_head() -> None:
    for posted in (facts(verdict_posted=True), facts(verdict_posted=True, pages_conclusion="failure")):
        gh = FakeGitHub()
        assert act(gh, posted, evaluate(posted, "shadow", APP, LIMITS), "shadow", config=CONFIG) == "unchanged"
        assert gh.calls == []


def test_shadow_says_nothing_before_the_reviewer_reviewed_the_head() -> None:
    """An earlier sweep must not use up the head's one comment before the approval arrives."""
    for reviews in ([], [(REVIEWER, "APPROVED", OLD)], [(OWNER, "APPROVED", HEAD)]):
        waiting = facts(reviews=reviews)
        gh = FakeGitHub()
        assert act(gh, waiting, evaluate(waiting, "shadow", APP, LIMITS), "shadow", config=CONFIG) == "waiting"
        assert gh.calls == []


def test_shadow_removes_a_stale_merge_ready_label_while_waiting() -> None:
    waiting = facts(reviews=[(REVIEWER, "APPROVED", OLD)], labels=frozenset({"factory", "factory:merge-ready"}))
    gh = FakeGitHub()
    assert act(gh, waiting, evaluate(waiting, "shadow", APP, LIMITS), "shadow", config=CONFIG) == "waiting"
    assert gh.calls == [("DELETE", f"{API}/issues/12/labels/factory%3Amerge-ready", None)]


def test_shadow_comments_once_the_reviewer_requested_changes() -> None:
    rejected = facts(reviews=[(REVIEWER, "CHANGES_REQUESTED", HEAD)])
    gh = FakeGitHub()
    assert act(gh, rejected, evaluate(rejected, "shadow", APP, LIMITS), "shadow", config=CONFIG) == "commented"
    assert f"factory:verdict=block sha={HEAD}" in gh.calls[0][2]["body"]


def test_shadow_never_updates_the_branch() -> None:
    behind = facts(behind=True)
    gh = FakeGitHub()
    assert act(gh, behind, evaluate(behind, "shadow", APP, LIMITS), "shadow", config=CONFIG) == "commented"
    assert [method for method, _, _ in gh.calls] == ["POST"]


@pytest.mark.parametrize("mode", ["off", "", "manual"])
def test_act_does_nothing_unless_shadow_or_auto(mode: str) -> None:
    gh = FakeGitHub()
    assert act(gh, facts(), evaluate(facts(), mode, APP, LIMITS), mode, config=CONFIG) == "off"
    assert gh.calls == []


# --- act in auto mode ---------------------------------------------------------------------------


def auto_github(merged: Any = None) -> FakeGitHub:
    return FakeGitHub({
        ("PUT", f"{API}/pulls/12/merge"): merged or {"sha": MERGED, "merged": True, "message": "merged"},
        ("GET", f"{API}/issues"): [
            {"number": 30, "state": "open", "labels": [{"name": "factory:found"}, {"name": "S2"}], "created_at": "2026-10-10T00:00:00Z"},
        ],
        ("GET", f"{API}/actions/workflows/pages.yml/runs"): {"workflow_runs": [{"conclusion": "success"}]},
    })


def test_auto_merge_passes_head_sha() -> None:
    gh = auto_github()
    assert act(gh, facts(), evaluate(facts(), "auto", APP, LIMITS), "auto", config=CONFIG) == "merged"
    method, path, body = gh.calls[0]
    assert (method, path) == ("PUT", f"{API}/pulls/12/merge")
    assert body == {"merge_method": "squash", "sha": HEAD, "commit_title": TITLE, "commit_message": "Refs #7"}


def test_auto_merge_labels_the_issue_marks_the_merge_deletes_the_branch_and_promotes() -> None:
    gh = auto_github()
    act(gh, facts(), evaluate(facts(), "auto", APP, LIMITS), "auto", config=CONFIG)
    calls = [(method, path) for method, path, _ in gh.calls]
    assert calls == [
        ("PUT", f"{API}/pulls/12/merge"),
        ("POST", f"{API}/issues/7/labels"),
        ("DELETE", f"{API}/issues/7/labels/factory%3Afixing"),
        ("POST", f"{API}/issues/7/comments"),
        ("DELETE", f"{API}/git/refs/heads/fix/7-the-terminal-fits"),
        ("GET", f"{API}/issues"),
        ("GET", f"{API}/actions/workflows/pages.yml/runs"),
        ("POST", f"{API}/issues/30/labels"),
    ]
    assert gh.calls[1][2] == {"labels": ["factory:merged"]}
    assert gh.calls[7][2] == {"labels": ["factory:ready"]}
    # verify reads the merge commit and the pull request from the App's comment.
    comment = {"user": {"login": APP}, "body": gh.calls[3][2]["body"], "created_at": "2026-10-19T12:00:00Z"}
    assert verify.merge_marker([comment], frozenset({APP})) == (MERGED, 12)


def test_auto_merge_title_is_made_safe_and_the_message_is_only_refs() -> None:
    unsafe = facts(title="Fixes #8 and pings @someone")
    gh = auto_github()
    act(gh, unsafe, evaluate(unsafe, "auto", APP, LIMITS), "auto", config=CONFIG)
    body = gh.calls[0][2]
    assert body["commit_title"] == "Fix for issue 7"
    assert body["commit_message"] == "Refs #7"


def test_auto_merge_of_a_revert_leaves_the_issue_alone() -> None:
    revert = revert_facts(pages_conclusion="failure", merges_24h=5)
    gh = auto_github()
    assert act(gh, revert, evaluate(revert, "auto", APP, LIMITS), "auto", config=CONFIG) == "merged"
    assert [(method, path) for method, path, _ in gh.calls] == [
        ("PUT", f"{API}/pulls/12/merge"),
        ("DELETE", f"{API}/git/refs/heads/revert/7"),
    ]
    assert gh.calls[0][2] == {
        "merge_method": "squash", "sha": HEAD, "commit_title": f'Revert "{TITLE}"', "commit_message": "Refs #7",
    }


def test_auto_merge_tolerates_a_branch_already_deleted() -> None:
    gh = auto_github()
    gh.responses[("DELETE", f"{API}/git/refs/heads/{BRANCH}")] = GitHubError(422, "Reference does not exist")
    assert act(gh, facts(), evaluate(facts(), "auto", APP, LIMITS), "auto", config=CONFIG) == "merged"


def test_auto_merge_that_github_does_not_confirm_stops() -> None:
    gh = auto_github({"merged": False, "message": "Head branch was modified"})
    with pytest.raises(GitHubError):
        act(gh, facts(), evaluate(facts(), "auto", APP, LIMITS), "auto", config=CONFIG)
    assert [method for method, _, _ in gh.calls] == ["PUT"]


def test_auto_block_writes_nothing() -> None:
    blocked = facts(pages_conclusion="failure")
    gh = FakeGitHub()
    assert act(gh, blocked, evaluate(blocked, "auto", APP, LIMITS), "auto", config=CONFIG) == "blocked"
    assert gh.calls == []


def test_auto_does_not_comment_in_shadow_style() -> None:
    gh = auto_github()
    act(gh, facts(), evaluate(facts(), "auto", APP, LIMITS), "auto", config=CONFIG)
    assert not any("factory:verdict" in str(body) for _, _, body in gh.calls)


# --- gather: GitHub and the main checkout ------------------------------------------------------------

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
    "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
}


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=GIT_ENV
    ).stdout.strip()


def write(root: Path, files: dict[str, str]) -> None:
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


BASE_FILES = {
    "css/site.css": ".terminal { width: 400px; }\n",
    "tests/e2e/test_terminal.py": "def test_terminal():\n    # the terminal\n    assert True\n",
}


def origin_and_main(tmp_path: Path, head_files: dict[str, str], number: int = 12) -> tuple[Path, str, str]:
    """An origin with main and a pull request head under refs/pull/<n>/head, and a clone of main."""
    origin = tmp_path / "origin"
    origin.mkdir()
    git("init", "-q", "-b", "main", cwd=origin)
    write(origin, BASE_FILES)
    git("add", "-A", cwd=origin)
    git("commit", "-qm", "base", cwd=origin)
    base = git("rev-parse", "HEAD", cwd=origin)
    git("checkout", "-qb", BRANCH, cwd=origin)
    write(origin, head_files)
    git("add", "-A", cwd=origin)
    git("commit", "-qm", "head", cwd=origin)
    head = git("rev-parse", "HEAD", cwd=origin)
    git("update-ref", f"refs/pull/{number}/head", head, cwd=origin)
    git("checkout", "-q", "main", cwd=origin)
    clone = tmp_path / "main"
    git("clone", "-q", str(origin), str(clone), cwd=tmp_path)
    return clone, base, head


def pull_json(head: str, **changes: Any) -> dict[str, Any]:
    data = {
        "number": 12,
        "state": "open",
        "draft": False,
        "title": TITLE,
        "user": {"login": APP},
        "labels": [{"name": "factory"}],
        "head": {"ref": BRANCH, "sha": head, "repo": {"full_name": REPO}},
        "base": {"ref": "main", "sha": BASE, "repo": {"full_name": REPO}},
        "body": f"Refs #7\n\n{SENTINEL}\n<!-- factory:attempt=1 -->\n<!-- factory:head={head} -->\n",
    }
    data.update(changes)
    return data


def check_run(name: str, conclusion: str = "success", app: int = ACTIONS_APP, status: str = "completed") -> dict[str, Any]:
    return {"name": name, "status": status, "conclusion": conclusion if status == "completed" else None, "app": {"id": app}}


def gather_github(head: str, base: str, overrides: dict[tuple[str, str], Any] | None = None) -> FakeGitHub:
    responses: dict[tuple[str, str], Any] = {
        ("GET", f"{API}/pulls/12"): pull_json(head),
        ("GET", f"{API}/issues/7"): {
            "number": 7, "state": "open", "labels": [{"name": "factory:found"}, {"name": "factory:fixing"}],
        },
        ("GET", f"{API}/pulls/12/reviews"): [
            {"id": 2, "user": {"login": REVIEWER}, "state": "APPROVED", "commit_id": head, "submitted_at": "2026-10-19T10:00:00Z"},
            {"id": 1, "user": {"login": REVIEWER}, "state": "CHANGES_REQUESTED", "commit_id": OLD, "submitted_at": "2026-10-18T10:00:00Z"},
        ],
        ("GET", f"{API}/rules/branches/main"): [
            {"type": "pull_request", "parameters": {"required_approving_review_count": 1}},
            {
                "type": "required_status_checks",
                "parameters": {
                    "strict_required_status_checks_policy": True,
                    "required_status_checks": [
                        {"context": TESTS_JOB, "integration_id": ACTIONS_APP},
                        {"context": VISUAL_JOB, "integration_id": ACTIONS_APP},
                    ],
                },
            },
        ],
        ("GET", f"{API}/commits/{head}/check-runs"): {
            "total_count": 4,
            "check_runs": [
                check_run(TESTS_JOB), check_run(TESTS_JOB), check_run(VISUAL_JOB), check_run("Lint", "failure"),
            ],
        },
        ("GET", f"{API}/compare/main...{head}"): {"behind_by": 0, "ahead_by": 1, "merge_base_commit": {"sha": base}},
        ("GET", f"{API}/actions/workflows/pages.yml/runs"): {"workflow_runs": [{"status": "completed", "conclusion": "success"}]},
        ("GET", f"{API}/issues"): [],
        ("GET", f"{API}/issues/12/comments"): [],
    }
    responses.update(overrides or {})
    return FakeGitHub(responses)


def test_gather_reads_github_and_fences_from_the_main_checkout(tmp_path: Path) -> None:
    repo, base, head = origin_and_main(tmp_path, {"css/site.css": ".terminal { max-width: 100%; }\n"})
    gh = gather_github(head, base)
    found = gather(gh, repo, 12, CONFIG, app_slug=SLUG, now=NOW)
    assert found == PullFacts(
        number=12,
        author=APP,
        labels=frozenset({"factory"}),
        head_ref=BRANCH,
        same_repo=True,
        draft=False,
        head_sha=head,
        issue=7,
        issue_labels=frozenset({"factory:found", "factory:fixing"}),
        reviews=[(REVIEWER, "CHANGES_REQUESTED", OLD), (REVIEWER, "APPROVED", head)],
        checks={TESTS_JOB: "success", VISUAL_JOB: "success"},
        required=[TESTS_JOB, VISUAL_JOB],
        fence=FenceReport(True, [], 2, False),
        behind=False,
        pages_conclusion="success",
        merges_24h=0,
        title=TITLE,
        body_head=head,
        state="open",
        base_ref="main",
        issue_open=True,
        verdict_posted=False,
    )
    # The head was fetched as git objects into a private ref; main stays checked out.
    assert git("rev-parse", "refs/factory/merge-head", cwd=repo) == head
    assert git("rev-parse", "HEAD", cwd=repo) == base
    assert evaluate(found, "auto", APP, LIMITS).action == "merge"
    # Only reads: nothing was written to GitHub.
    assert {method for method, _, _ in gh.calls} == {"GET"}


def test_gather_then_merge_never_uses_the_description(tmp_path: Path) -> None:
    repo, base, head = origin_and_main(tmp_path, {"css/site.css": ".terminal { max-width: 100%; }\n"})
    body = f"Fixes #7\n\nCloses #8 for @someone\n\n{SENTINEL}\n<!-- factory:head={head} -->\n"
    gh = gather_github(head, base, {
        ("GET", f"{API}/pulls/12"): pull_json(head, body=body),
        ("PUT", f"{API}/pulls/12/merge"): {"sha": MERGED, "merged": True},
    })
    found = gather(gh, repo, 12, CONFIG, app_slug=SLUG, now=NOW)
    verdict = evaluate(found, "auto", APP, LIMITS)
    assert act(gh, found, verdict, "auto", config=CONFIG) == "merged"
    (merge_call,) = [body for method, path, body in gh.calls if method == "PUT"]
    assert merge_call["commit_message"] == "Refs #7"
    assert merge_call["commit_title"] == TITLE
    assert "Fixes" not in str(merge_call) and "Closes" not in str(merge_call)


def test_gather_recomputes_soft_fence_reasons(tmp_path: Path) -> None:
    changed = {"tests/e2e/test_terminal.py": "def test_terminal():\n    # the whole terminal\n    assert True\n"}
    repo, base, head = origin_and_main(tmp_path, changed)
    found = gather(gather_github(head, base), repo, 12, CONFIG, app_slug=SLUG, now=NOW)
    assert found.fence.ok and found.fence.review
    verdict = evaluate(found, "auto", APP, LIMITS)
    assert verdict.action == "block" and only_failed(verdict) == [FENCE]
    assert "existing tests changed" in verdict.rows[FENCE].value


def test_gather_recomputes_hard_fence_reasons(tmp_path: Path) -> None:
    repo, base, head = origin_and_main(tmp_path, {".github/workflows/x.yml": "on: push\n"})
    found = gather(gather_github(head, base), repo, 12, CONFIG, app_slug=SLUG, now=NOW)
    assert not found.fence.ok
    assert "forbidden path: .github/workflows/x.yml" in evaluate(found, "auto", APP, LIMITS).rows[FENCE].value


def test_gather_refuses_a_head_that_moved(tmp_path: Path) -> None:
    repo, base, head = origin_and_main(tmp_path, {"css/site.css": "x\n"})
    gh = gather_github(OLD, base, {("GET", f"{API}/compare/main...{OLD}"): {"behind_by": 0, "merge_base_commit": {"sha": base}}})
    with pytest.raises(FixError, match="moved"):
        gather(gh, repo, 12, CONFIG, app_slug=SLUG, now=NOW)


def test_gather_reads_behind_and_the_merge_base(tmp_path: Path) -> None:
    repo, base, head = origin_and_main(tmp_path, {"css/site.css": "x\n"})
    gh = gather_github(head, base, {("GET", f"{API}/compare/main...{head}"): {"behind_by": 2, "merge_base_commit": {"sha": base}}})
    assert gather(gh, repo, 12, CONFIG, app_slug=SLUG, now=NOW).behind


def test_gather_trusts_body_markers_only_in_the_marker_block(tmp_path: Path) -> None:
    repo, base, head = origin_and_main(tmp_path, {"css/site.css": "x\n"})
    body = f"<!-- factory:head={head} -->\n\nRefs #7\n"
    gh = gather_github(head, base, {("GET", f"{API}/pulls/12"): pull_json(head, body=body)})
    found = gather(gh, repo, 12, CONFIG, app_slug=SLUG, now=NOW)
    assert found.body_head is None
    assert only_failed(evaluate(found, "auto", APP, LIMITS)) == [PULL]


def test_gather_revert_links_its_issue_from_the_branch(tmp_path: Path) -> None:
    repo, base, head = origin_and_main(tmp_path, {"css/site.css": "x\n"})
    pull = pull_json(head, body="Refs #7\n\nReverts it.\n", labels=[{"name": "factory"}, {"name": "factory:revert"}])
    pull["head"]["ref"] = "revert/7"
    gh = gather_github(head, base, {
        ("GET", f"{API}/pulls/12"): pull,
        ("GET", f"{API}/issues/7"): {"number": 7, "state": "open", "labels": [{"name": "factory:regressed"}, {"name": "factory:needs-human"}]},
    })
    found = gather(gh, repo, 12, CONFIG, app_slug=SLUG, now=NOW)
    assert (found.issue, found.head_ref, found.body_head) == (7, "revert/7", None)
    assert evaluate(found, "auto", APP, LIMITS).action == "merge"


def test_gather_without_a_factory_branch_reads_no_issue(tmp_path: Path) -> None:
    repo, base, head = origin_and_main(tmp_path, {"css/site.css": "x\n"})
    pull = pull_json(head)
    pull["head"]["ref"] = "feature/x"
    gh = gather_github(head, base, {("GET", f"{API}/pulls/12"): pull})
    found = gather(gh, repo, 12, CONFIG, app_slug=SLUG, now=NOW)
    assert (found.issue, found.issue_labels, found.issue_open) == (None, frozenset(), False)
    assert ("GET", f"{API}/issues/7", None) not in gh.calls


def test_gather_a_pull_request_number_as_issue_is_not_an_open_issue(tmp_path: Path) -> None:
    repo, base, head = origin_and_main(tmp_path, {"css/site.css": "x\n"})
    gh = gather_github(head, base, {
        ("GET", f"{API}/issues/7"): {"number": 7, "state": "open", "pull_request": {}, "labels": [{"name": "factory:fixing"}]},
    })
    assert not gather(gh, repo, 12, CONFIG, app_slug=SLUG, now=NOW).issue_open


def test_required_checks_come_from_the_rules_of_main() -> None:
    gh = FakeGitHub({
        ("GET", f"{API}/rules/branches/main"): [
            {"type": "required_status_checks", "parameters": {"required_status_checks": [{"context": "A", "integration_id": ACTIONS_APP}]}},
            {"type": "required_status_checks", "parameters": {"required_status_checks": [{"context": "B"}, {"context": "A", "integration_id": ACTIONS_APP}]}},
            {"type": "deletion"},
        ],
    })
    assert merge.required_checks(gh, API) == [("A", ACTIONS_APP), ("B", None)]


def test_check_conclusions_need_every_run_of_a_required_check_to_succeed() -> None:
    runs = [
        check_run("A"), check_run("A", "failure"),           # one of two suites failed
        check_run("B"), check_run("B", "failure", app=99),   # another app's run of the same name does not count
        check_run("C", status="in_progress"),
        check_run("D", "skipped"),
    ]
    gh = FakeGitHub({("GET", f"{API}/commits/{HEAD}/check-runs"): {"check_runs": runs}})
    required = [("A", ACTIONS_APP), ("B", ACTIONS_APP), ("C", ACTIONS_APP), ("D", None), ("E", None)]
    assert merge.check_conclusions(gh, API, HEAD, required) == {
        "A": "failure", "B": "success", "C": "in_progress", "D": "skipped", "E": "missing",
    }
    (call,) = gh.calls
    assert call[2]["filter"] == "latest" and call[2]["per_page"] == 100


def test_merges_in_the_window_count_only_the_apps_factory_merges() -> None:
    def item(number: int, merged_at: str | None, *labels: str) -> dict[str, Any]:
        return {"number": number, "labels": [{"name": n} for n in ("factory", *labels)], "pull_request": {"merged_at": merged_at}}

    def pull(number: int, merged_at: str | None, by: str, *labels: str) -> dict[str, Any]:
        return {"number": number, "merged_at": merged_at, "merged_by": {"login": by}, "labels": [{"name": n} for n in ("factory", *labels)]}

    recent, old = "2026-10-19T08:00:00Z", "2026-10-18T08:00:00Z"
    gh = FakeGitHub({
        ("GET", f"{API}/issues"): [
            item(1, recent), item(2, recent), item(3, old), item(4, None), item(5, recent, "factory:revert"),
            {"number": 6, "labels": [{"name": "factory"}]},  # an issue, not a pull request
        ],
        ("GET", f"{API}/pulls/1"): pull(1, recent, APP),
        ("GET", f"{API}/pulls/2"): pull(2, recent, OWNER),
    })
    assert merge.merges_in_window(gh, API, APP, NOW) == 1
    params = gh.calls[0][2]
    assert params == {"state": "closed", "labels": "factory", "since": "2026-10-18T12:00:00Z", "per_page": 100}
    assert ("GET", f"{API}/pulls/3", None) not in gh.calls and ("GET", f"{API}/pulls/5", None) not in gh.calls


def test_verdict_posted_needs_a_trusted_marker_for_this_head() -> None:
    def comment(login: str, body: str) -> dict[str, Any]:
        return {"user": {"login": login}, "body": body}

    ours = f"Table\n\n{SENTINEL}\n<!-- factory:verdict=block sha={HEAD} -->\n"
    trusted = frozenset({APP, OWNER})
    cases = {
        "a stranger's marker": ([comment("stranger", ours)], False),
        "a marker for an older head": ([comment(APP, ours.replace(HEAD, OLD))], False),
        "a marker outside the block": ([comment(APP, f"<!-- factory:verdict=merge sha={HEAD} -->\n")], False),
        "the App's marker for this head": ([comment("stranger", "x"), comment(APP, ours)], True),
    }
    for name, (comments, expected) in cases.items():
        gh = FakeGitHub({("GET", f"{API}/issues/12/comments"): comments})
        assert merge.verdict_posted(gh, API, 12, HEAD, trusted) is expected, name


def test_pages_conclusion_reads_the_last_run_on_main() -> None:
    path = ("GET", f"{API}/actions/workflows/pages.yml/runs")
    assert merge.pages_conclusion(FakeGitHub({path: {"workflow_runs": [{"status": "completed", "conclusion": "failure"}]}})) == "failure"
    assert merge.pages_conclusion(FakeGitHub({path: {"workflow_runs": [{"status": "in_progress", "conclusion": None}]}})) == "in_progress"
    assert merge.pages_conclusion(FakeGitHub({path: {"workflow_runs": []}})) == "none"


def test_gather_marks_a_verdict_already_posted(tmp_path: Path) -> None:
    repo, base, head = origin_and_main(tmp_path, {"css/site.css": "x\n"})
    comment = {"user": {"login": APP}, "body": f"{SENTINEL}\n<!-- factory:verdict=merge sha={head} -->\n"}
    gh = gather_github(head, base, {("GET", f"{API}/issues/12/comments"): [comment]})
    found = gather(gh, repo, 12, CONFIG, app_slug=SLUG, now=NOW)
    assert found.verdict_posted
    assert act(gh, found, evaluate(found, "shadow", APP, LIMITS), "shadow", config=CONFIG) == "unchanged"


# --- command line ---------------------------------------------------------------------------------


def test_main_does_nothing_when_the_factory_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FACTORY_MODE", "off")
    monkeypatch.setenv("FACTORY_APP_SLUG", SLUG)
    monkeypatch.setattr(merge, "GitHub", lambda *a, **k: pytest.fail("GitHub was contacted"))
    assert merge.main(["--sweep"]) == 0


def test_main_needs_the_app_slug(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("FACTORY_MODE", "shadow")
    monkeypatch.delenv("FACTORY_APP_SLUG", raising=False)
    monkeypatch.setattr(merge, "GitHub", lambda *a, **k: pytest.fail("GitHub was contacted"))
    assert merge.main(["--pr", "12"]) == 2
    assert "FACTORY_APP_SLUG" in capsys.readouterr().err


def test_main_requires_pr_or_sweep() -> None:
    with pytest.raises(SystemExit):
        merge.main([])
    with pytest.raises(SystemExit):
        merge.main(["--pr", "12", "--sweep"])
    with pytest.raises(SystemExit):
        merge.main(["--pr", "0"])


def test_main_sweep_evaluates_every_open_factory_pull_request(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("FACTORY_MODE", "shadow")
    monkeypatch.setenv("FACTORY_APP_SLUG", SLUG)
    gh = FakeGitHub({
        ("GET", f"{API}/pulls"): [
            {"number": 14, "labels": [{"name": "factory"}]},
            {"number": 9, "labels": [{"name": "bug"}]},
            {"number": 12, "labels": [{"name": "factory"}, {"name": "factory:revert"}]},
        ],
    })
    monkeypatch.setattr(merge, "GitHub", lambda *a, **k: gh)
    seen: list[tuple[int, str]] = []

    def fake_gather(gh_: Any, repo: Path, pr: int, config: Any, *, app_slug: str | None = None, now: Any = None) -> PullFacts:
        return facts(number=pr, verdict_posted=pr == 14)

    def fake_act(gh_: Any, found: PullFacts, verdict: merge.Verdict, mode: str, *, config: Any = None) -> str:
        seen.append((found.number, mode))
        return "commented"

    monkeypatch.setattr(merge, "gather", fake_gather)
    monkeypatch.setattr(merge, "act", fake_act)
    assert merge.main(["--sweep"]) == 0
    assert seen == [(12, "shadow"), (14, "shadow")]
    assert gh.calls[0] == ("GET", f"{API}/pulls", {"state": "open", "per_page": 100})
    out = capsys.readouterr().out
    assert "#12: merge" in out and "#14: merge" in out


def test_main_carries_on_after_one_pull_request_fails_and_redacts(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("FACTORY_MODE", "auto")
    monkeypatch.setenv("FACTORY_APP_SLUG", SLUG)
    gh = FakeGitHub({("GET", f"{API}/pulls"): [{"number": 12, "labels": [{"name": "factory"}]}, {"number": 14, "labels": [{"name": "factory"}]}]})
    monkeypatch.setattr(merge, "GitHub", lambda *a, **k: gh)
    acted: list[int] = []

    def fake_gather(gh_: Any, repo: Path, pr: int, config: Any, *, app_slug: str | None = None, now: Any = None) -> PullFacts:
        if pr == 12:
            raise GitHubError(500, "fetch https://x-access-token:ghs_secret@github.com failed")
        return facts(number=pr)

    monkeypatch.setattr(merge, "gather", fake_gather)
    monkeypatch.setattr(merge, "act", lambda gh_, found, verdict, mode, *, config=None: acted.append(found.number) or "merged")
    assert merge.main(["--sweep"]) == 2
    assert acted == [14]
    err = capsys.readouterr().err
    assert "ghs_secret" not in err and "#12" in err


def test_main_returns_2_on_a_github_error_listing_pull_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FACTORY_MODE", "shadow")
    monkeypatch.setenv("FACTORY_APP_SLUG", SLUG)
    gh = FakeGitHub({("GET", f"{API}/pulls"): GitHubError(502, "bad gateway")})
    monkeypatch.setattr(merge, "GitHub", lambda *a, **k: gh)
    assert merge.main(["--sweep"]) == 2


def test_main_pr_runs_one_pull_request_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    repo, base, head = origin_and_main(tmp_path, {"css/site.css": ".terminal { max-width: 100%; }\n"})
    gh = gather_github(head, base)
    monkeypatch.setenv("FACTORY_MODE", "shadow")
    monkeypatch.setenv("FACTORY_APP_SLUG", SLUG)
    monkeypatch.setattr(merge, "GitHub", lambda *a, **k: gh)
    monkeypatch.chdir(repo)
    assert merge.main(["--pr", "12"]) == 0
    writes = [(method, path) for method, path, _ in gh.calls if method != "GET"]
    assert writes == [("POST", f"{API}/issues/12/comments"), ("POST", f"{API}/issues/12/labels")]
    assert "#12: commented" in capsys.readouterr().out
