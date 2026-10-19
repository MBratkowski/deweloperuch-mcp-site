"""The reviewer: routing after Check, the evidence job's checks (the decisive behavioural check, red
and green, the reproduction, the neighbourhood, the fence, an exact revert) and posting a verdict.

Offline: FakeGitHub stands in for the API, temporary git repositories for the pull request, junit
files and tiny pytest projects for the test sessions, and a fake lab for the browser."""

from __future__ import annotations

import base64
import errno
import json
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tests.factory.fakes import FakeGitHub
from tools.factory import fix_publish, review, review_evidence
from tools.factory.config import load_config
from tools.factory.fence import FenceReport
from tools.factory.findings import Cell, Finding, Group
from tools.factory.issues import SENTINEL
from tools.factory.review import Route, load_verdict, post, route
from tools.factory.review_evidence import (
    Behaviour,
    Evidence,
    HeadProof,
    behaviour_check,
    compare_sessions,
    exact_revert,
    gather_decisive,
    gather_evidence,
    junit_results,
    prove_head_tests,
)
from tools.factory.stress import StressResult

CONFIG = load_config()
REPO = CONFIG.site.repo
API = f"/repos/{REPO}"
APP = "factory-app[bot]"
OWNER = "MBratkowski"
TRUSTED = frozenset({OWNER, APP})
ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / ".github" / "factory" / "schemas" / "verdict.json"
BRANCH = "fix/7-the-terminal-fits"
HEAD = "c" * 40
BASE = "b" * 40
MERGED = "d" * 40
TEST_ID = "tests/e2e/test_terminal.py::test_terminal_fits"
CHECKLIST = {
    "cause_not_symptom": True,
    "evidence_green": True,
    "no_weakened_tests": True,
    "baselines_match": True,
    "fence_and_copy": True,
    "minimal_diff": True,
}


# --- GitHub scaffolding ------------------------------------------------------------------------


def pr_body(*, attempt: int = 1, head: str | None = HEAD, tests: Sequence[str] = (TEST_ID,)) -> str:
    encoded = base64.b64encode(json.dumps(list(tests)).encode()).decode()
    markers = [SENTINEL, f"<!-- factory:attempt={attempt} -->", f"<!-- factory:tests={encoded} -->"]
    if head is not None:
        markers.append(f"<!-- factory:head={head} -->")
    return "Refs #7\n\n## Cause\n\nA fixed width.\n\n" + "\n".join(markers) + "\n"


def pull_data(
    number: int = 12,
    *,
    head: str = HEAD,
    ref: str = BRANCH,
    author: str = APP,
    labels: Sequence[str] = ("factory",),
    body: str | None = None,
    repo: str = REPO,
    state: str = "open",
    draft: bool = False,
) -> dict[str, Any]:
    return {
        "number": number,
        "node_id": f"PR_{number}",
        "state": state,
        "draft": draft,
        "title": "The terminal fits",
        "user": {"login": author},
        "labels": [{"name": name} for name in labels],
        "head": {"ref": ref, "sha": head, "repo": {"full_name": repo}},
        "base": {"ref": "main", "sha": "e" * 40, "repo": {"full_name": REPO}},
        "body": pr_body() if body is None else body,
    }


def issue_data(
    labels: Sequence[str] = ("factory:found", "factory:fixing", "S3"),
    *,
    author: str = APP,
    body: str = "## Problem\n\nThe terminal overflows.\n",
    state: str = "open",
) -> dict[str, Any]:
    return {
        "number": 7,
        "title": "The terminal fits a 375 px viewport",
        "state": state,
        "user": {"login": author},
        "labels": [{"name": name} for name in labels],
        "body": body,
    }


def run_event(
    conclusion: str = "success",
    *,
    head: str = HEAD,
    event: str = "pull_request",
    repo: str = REPO,
    numbers: Sequence[int] = (12,),
) -> dict[str, Any]:
    return {
        "action": "completed",
        "workflow_run": {
            "id": 555,
            "name": "Check",
            "event": event,
            "status": "completed",
            "conclusion": conclusion,
            "head_sha": head,
            "head_repository": {"full_name": repo},
            "pull_requests": [{"number": n, "head": {"sha": head, "ref": BRANCH}} for n in numbers],
        },
    }


def github(
    pull: Mapping[str, Any] | None = None,
    issue: Mapping[str, Any] | None = None,
    *,
    comments: Sequence[Mapping[str, Any]] = (),
    files: Sequence[Mapping[str, Any]] = (),
) -> FakeGitHub:
    return FakeGitHub(
        {
            ("GET", f"{API}/pulls/12"): dict(pull or pull_data()),
            ("GET", f"{API}/issues/7"): dict(issue or issue_data()),
            ("GET", f"{API}/issues/7/comments"): list(comments),
            ("GET", f"{API}/compare/main...{HEAD}"): {"merge_base_commit": {"sha": BASE}},
            ("GET", f"{API}/actions/runs/555/jobs"): {
                "jobs": [
                    {"name": "Validate this commit / Tests (pytest, Playwright)", "conclusion": "failure"},
                    {"name": "Validate this commit / Link check", "conclusion": "success"},
                ]
            },
            ("GET", f"{API}/pulls/12/files"): list(files),
        }
    )


def writes(gh: FakeGitHub) -> list[tuple[str, str, Any]]:
    return [call for call in gh.calls if call[0] != "GET"]


def posted(gh: FakeGitHub, path: str) -> list[Any]:
    return [body for method, p, body in gh.calls if (method, p) == ("POST", path)]


def labels_added(gh: FakeGitHub, number: int) -> list[str]:
    return [name for body in posted(gh, f"{API}/issues/{number}/labels") for name in body["labels"]]


def comments_on(gh: FakeGitHub, number: int) -> list[str]:
    return [body["body"] for body in posted(gh, f"{API}/issues/{number}/comments")]


def dispatches(gh: FakeGitHub, workflow: str) -> list[Any]:
    return posted(gh, f"{API}/actions/workflows/{workflow}/dispatches")


# --- route -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "event", "pull", "expected"),
    [
        ("stranger author", run_event(), pull_data(author="someone"), ("ignore", None)),
        ("the owner is not the App", run_event(), pull_data(author=OWNER), ("ignore", None)),
        ("fork", run_event(repo="someone/fork"), pull_data(), ("ignore", None)),
        ("fork head on the pull request", run_event(), pull_data(repo="someone/fork"), ("ignore", None)),
        ("push event", run_event(event="push"), pull_data(), ("ignore", None)),
        ("stale head", run_event(head="f" * 40), pull_data(), ("ignore", None)),
        ("no factory label", run_event(), pull_data(labels=()), ("ignore", None)),
        ("a person took it over", run_event(), pull_data(labels=("factory", "factory:needs-human")), ("ignore", None)),
        ("a person took it over, Check failed", run_event("failure"),
         pull_data(labels=("factory", "factory:needs-human")), ("ignore", None)),
        ("a draft", run_event(), pull_data(draft=True), ("ignore", None)),
        ("closed", run_event(), pull_data(state="closed"), ("ignore", None)),
        ("cancelled Check", run_event("cancelled"), pull_data(), ("ignore", None)),
        ("not a fix branch", run_event(), pull_data(ref="feature/x"), ("ignore", None)),
        ("Check failure on attempt 1", run_event("failure"), pull_data(), ("fix", 2)),
        ("Check timed out on attempt 1", run_event("timed_out"), pull_data(), ("fix", 2)),
        ("Check failure on attempt 2", run_event("failure"), pull_data(body=pr_body(attempt=2)), ("needs-human", 2)),
        ("success", run_event(), pull_data(), ("review", 1)),
    ],
)
def test_route_table(name: str, event: dict[str, Any], pull: dict[str, Any], expected: tuple[str, int | None]) -> None:
    gh = github(pull)
    result = route(event, gh, CONFIG, TRUSTED)
    action, attempt = expected
    assert result.action == action, (name, result.reason)
    assert result.reason
    if action != "ignore":
        assert (result.pr, result.issue, result.head, result.attempt, result.revert) == (12, 7, HEAD, attempt, False)
    if action == "review":
        assert result.base == BASE
        assert result.pr_body == pull["body"] and "The terminal overflows." in result.issue_body
    if action == "needs-human":
        assert result.failed_jobs == ("Validate this commit / Tests (pytest, Playwright)",)
    assert writes(gh) == []


@pytest.mark.parametrize(
    "body",
    [
        pr_body(head=None),  # no factory:head marker
        pr_body(head="f" * 40),  # a marker of an earlier push: the body was not updated yet
        pr_body().replace(SENTINEL, "Quoted"),  # markers outside the block our code renders
        pr_body().replace("factory:tests=", "factory:tests=!!"),  # unreadable tests marker
    ],
)
def test_route_trusts_markers_only_for_the_head_they_name(body: str) -> None:
    result = route(run_event(), github(pull_data(body=body)), CONFIG, TRUSTED)
    assert result.action == "ignore"


def test_route_ignores_a_head_the_reviewer_already_reviewed() -> None:
    # Before any evidence job exists, in any review state; a review of an older head or by a person does not count.
    for state in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
        gh = github()
        gh.responses[("GET", f"{API}/pulls/12/reviews")] = [reviewed(HEAD, state=state)]
        result = route(run_event(), gh, CONFIG, TRUSTED)
        assert result.action == "ignore" and "already reviewed" in result.reason
        assert writes(gh) == []
    gh = github()
    gh.responses[("GET", f"{API}/pulls/12/reviews")] = [reviewed("f" * 40), reviewed(HEAD, login=OWNER)]
    assert route(run_event(), gh, CONFIG, TRUSTED).action == "review"
    # A failed Check is not a review: the fixer's next attempt is still routed.
    gh = github()
    gh.responses[("GET", f"{API}/pulls/12/reviews")] = [reviewed(HEAD)]
    assert route(run_event("failure"), gh, CONFIG, TRUSTED).action == "fix"


def test_route_never_trusts_a_body_without_the_app_slug() -> None:
    assert route(run_event(), github(), CONFIG, frozenset({OWNER})).action == "ignore"


def updated_branch(tmp_path: Path, kind: str) -> tuple[Path, str, str, str]:
    """An origin whose pull request #12 has a merge commit as its head, and a shallow clone of main
    (the route job's checkout). `kind`: "update" (GitHub's update of the marked head with main),
    "twice" (two updates in a row), "reversed" (main with the marked head merged in: the first parent
    is main's), "side" (the marked head merged with a commit that is not on main). Returns (clone,
    marked head, head, merge base of main and the head)."""
    origin = tmp_path / "origin"
    git("init", "-q", "-b", "main", str(origin), cwd=tmp_path)
    commit(origin, "Base", {"css/site.css": "a {}\n", "README.md": "one\n"})
    git("checkout", "-q", "-b", BRANCH, cwd=origin)
    marked = commit(origin, "Fix", {"css/site.css": "a { color: red; }\n"})
    git("checkout", "-q", "main", cwd=origin)
    commit(origin, "Main moves", {"README.md": "two\n"})
    if kind == "reversed":
        git("checkout", "-q", "-b", "other", cwd=origin)
        git("merge", "-q", "--no-ff", "-m", "Merge the fix into main", marked, cwd=origin)
    elif kind == "side":
        git("checkout", "-q", "-b", "side", "main~1", cwd=origin)
        side = commit(origin, "Not on main", {"js/site.js": "side\n"})
        git("checkout", "-q", BRANCH, cwd=origin)
        git("merge", "-q", "--no-ff", "-m", "Merge a side branch", side, cwd=origin)
    else:
        git("checkout", "-q", BRANCH, cwd=origin)
        git("merge", "-q", "--no-ff", "-m", "Merge branch 'main'", "main", cwd=origin)
        if kind == "twice":
            git("checkout", "-q", "main", cwd=origin)
            commit(origin, "Main moves again", {"README.md": "three\n"})
            git("checkout", "-q", BRANCH, cwd=origin)
            git("merge", "-q", "--no-ff", "-m", "Merge branch 'main' again", "main", cwd=origin)
    head = git("rev-parse", "HEAD", cwd=origin)
    git("update-ref", "refs/pull/12/head", head, cwd=origin)
    git("checkout", "-q", "main", cwd=origin)
    base = git("merge-base", "main", head, cwd=origin)
    clone = tmp_path / "main"
    git("clone", "-q", "--depth", "1", f"file://{origin}", str(clone), cwd=tmp_path)
    return clone, marked, head, base


def updated_github(marked: str, head: str, base: str) -> FakeGitHub:
    gh = github(pull_data(head=head, body=pr_body(head=marked)))
    gh.responses[("GET", f"{API}/compare/main...{head}")] = {"merge_base_commit": {"sha": base}}
    return gh


@pytest.mark.parametrize("kind", ["update", "twice"])
def test_route_accepts_githubs_update_of_the_marked_head(tmp_path: Path, kind: str) -> None:
    """R49(1): "Update branch" makes a merge commit the head; the body still names the head before it."""
    clone, marked, head, base = updated_branch(tmp_path, kind)
    assert git("rev-parse", "--is-shallow-repository", cwd=clone) == "true"
    gh = updated_github(marked, head, base)
    result = route(run_event(head=head), gh, CONFIG, TRUSTED, repo=clone)
    assert (result.action, result.head, result.base, result.attempt) == ("review", head, base, 1), result.reason
    # Read with git only: the head sits in a private ref, main is still checked out and the clone was deepened.
    assert git("rev-parse", "refs/factory/updated-head", cwd=clone) == head
    assert git("rev-parse", "--abbrev-ref", "HEAD", cwd=clone) == "main"
    assert git("rev-parse", "--is-shallow-repository", cwd=clone) == "false"
    assert writes(gh) == []


@pytest.mark.parametrize("kind", ["reversed", "side"])
def test_route_rejects_a_merge_that_does_not_update_the_marked_head(tmp_path: Path, kind: str) -> None:
    clone, marked, head, base = updated_branch(tmp_path, kind)
    result = route(run_event(head=head), updated_github(marked, head, base), CONFIG, TRUSTED, repo=clone)
    assert result.action == "ignore" and "R44" in result.reason


def test_route_without_a_checkout_accepts_only_the_marked_head(tmp_path: Path) -> None:
    clone, marked, head, base = updated_branch(tmp_path, "update")
    assert route(run_event(head=head), updated_github(marked, head, base), CONFIG, TRUSTED).action == "ignore"


def test_branch_updated_refuses_a_head_that_moved(tmp_path: Path) -> None:
    clone, marked, head, _ = updated_branch(tmp_path, "update")
    assert fix_publish.branch_updated(clone, 12, head, marked) is True
    assert fix_publish.branch_updated(clone, 12, "f" * 40, marked) is False  # refs/pull/12/head is another commit
    assert fix_publish.branch_updated(clone, 12, head, None) is False
    assert fix_publish.branch_updated(clone, 12, marked, marked) is False  # the marked head itself is no update


@pytest.mark.parametrize(
    "issue",
    [
        issue_data(("factory:found", "factory:needs-human")),
        issue_data(state="closed"),
        {**issue_data(), "pull_request": {}},
    ],
)
def test_route_leaves_an_issue_a_person_took_over_alone(issue: dict[str, Any]) -> None:
    assert route(run_event("failure"), github(issue=issue), CONFIG, TRUSTED).action == "ignore"


def test_route_drops_the_body_of_an_untrusted_issue() -> None:
    result = route(run_event(), github(issue=issue_data(author="someone")), CONFIG, TRUSTED)
    assert result.action == "review" and result.issue_body == ""


def test_route_marks_a_flaky_issue() -> None:
    issue = issue_data(("factory:found", "factory:fixing", "factory:flaky"))
    assert route(run_event(), github(issue=issue), CONFIG, TRUSTED).flaky is True


def test_workflow_dispatch_routes_on_the_latest_check_run_of_the_head() -> None:
    gh = github()
    runs = {
        "workflow_runs": [
            {"id": 556, "event": "pull_request", "status": "completed", "conclusion": "success",
             "head_sha": HEAD, "head_repository": {"full_name": REPO}},
            {"id": 555, "event": "pull_request", "status": "completed", "conclusion": "failure",
             "head_sha": HEAD, "head_repository": {"full_name": REPO}},
        ]
    }
    gh.responses[("GET", f"{API}/actions/workflows/check.yml/runs")] = runs
    result = route({"inputs": {"pr": "12"}}, gh, CONFIG, TRUSTED)
    assert (result.action, result.pr, result.check_run, result.base) == ("review", 12, 556, BASE)
    query = next(b for m, p, b in gh.calls if p == f"{API}/actions/workflows/check.yml/runs")
    assert query["head_sha"] == HEAD and query["event"] == "pull_request"


@pytest.mark.parametrize("runs", [[], [{"id": 9, "event": "pull_request", "status": "in_progress", "conclusion": None,
                                        "head_sha": HEAD, "head_repository": {"full_name": REPO}}]])
def test_workflow_dispatch_waits_for_check(runs: list[dict[str, Any]]) -> None:
    gh = github()
    gh.responses[("GET", f"{API}/actions/workflows/check.yml/runs")] = {"workflow_runs": runs}
    assert route({"inputs": {"pr": "12"}}, gh, CONFIG, TRUSTED).action == "ignore"


def merged_marker(sha: str = MERGED, author: str = APP) -> dict[str, Any]:
    return {"user": {"login": author}, "created_at": "2026-10-18T10:00:00Z",
            "body": f"Merged.\n\n<!-- factory:merged sha={sha} pr=11 -->\n"}


def revert_pull(**changes: Any) -> dict[str, Any]:
    body = f"Refs #7\n\nReverts {MERGED} (#11): the fix did not hold on the live site.\n"
    values: dict[str, Any] = {"ref": "revert/7", "labels": ("factory", "factory:revert"), "body": body, **changes}
    return pull_data(**values)


def test_route_reviews_a_revert_against_the_trusted_merge_marker() -> None:
    issue = issue_data(("factory:regressed", "factory:needs-human"))
    gh = github(revert_pull(), issue, comments=[merged_marker("a" * 40, author="someone"), merged_marker()])
    result = route(run_event(), gh, CONFIG, TRUSTED)
    assert (result.action, result.revert, result.issue, result.merged, result.attempt) == ("review", True, 7, MERGED, 0)


def test_a_failed_check_on_a_revert_goes_to_a_person() -> None:
    issue = issue_data(("factory:regressed", "factory:needs-human"))
    gh = github(revert_pull(), issue, comments=[merged_marker()])
    assert route(run_event("failure"), gh, CONFIG, TRUSTED).action == "needs-human"


def test_route_leaves_a_revert_a_person_took_over_alone() -> None:
    issue = issue_data(("factory:regressed", "factory:needs-human"))
    pull = revert_pull(labels=("factory", "factory:revert", "factory:needs-human"))
    gh = github(pull, issue, comments=[merged_marker()])
    for conclusion in ("success", "failure"):
        result = route(run_event(conclusion), gh, CONFIG, TRUSTED)
        assert result.action == "ignore" and "factory:needs-human" in result.reason
    assert writes(gh) == []


@pytest.mark.parametrize("pull", [revert_pull(labels=("factory",)), pull_data(labels=("factory", "factory:revert"))])
def test_a_revert_needs_both_the_label_and_the_branch(pull: dict[str, Any]) -> None:
    gh = github(pull, issue_data(("factory:regressed",)), comments=[merged_marker()])
    assert route(run_event(), gh, CONFIG, TRUSTED).action == "ignore"


# --- act: a failed Check -------------------------------------------------------------------------


def routed(action: str = "review", **changes: Any) -> Route:
    values: dict[str, Any] = dict(
        action=action, pr=12, issue=7, base=BASE, head=HEAD, attempt=1, revert=False, reason="routed", check_run=555
    )
    values.update(changes)
    return Route(**values)


def test_act_dispatches_the_next_attempt_after_a_failed_check() -> None:
    gh = github()
    assert review.act(gh, routed("fix", attempt=2, failed_jobs=("Tests",)), CONFIG) == "fix"
    assert dispatches(gh, "factory-fix.yml") == [{"ref": "main", "inputs": {"issue": "7", "attempt": "2"}}]
    (comment,) = comments_on(gh, 12)
    assert "attempt 2 of 2" in comment and "Tests" in comment and "factory:attempt" not in comment
    assert labels_added(gh, 7) == []


def test_act_hands_the_issue_to_a_person_after_the_last_attempt() -> None:
    gh = github()
    assert review.act(gh, routed("needs-human", attempt=2, failed_jobs=("Tests",)), CONFIG) == "needs-human"
    assert labels_added(gh, 7) == ["factory:needs-human"]
    assert ("DELETE", f"{API}/issues/7/labels/factory%3Afixing", None) in gh.calls
    assert ("POST", "/graphql", {"id": "PR_12"}) in gh.calls
    (summary,) = comments_on(gh, 12)
    assert "draft" in summary and "#7" in summary and "Tests" in summary
    assert comments_on(gh, 7) and dispatches(gh, "factory-fix.yml") == []


def test_act_hands_a_failed_revert_to_a_person() -> None:
    gh = github()
    assert review.act(gh, routed("needs-human", attempt=0, revert=True, merged=MERGED), CONFIG) == "needs-human"
    (summary,) = comments_on(gh, 12)
    assert "revert pull request #12" in summary and dispatches(gh, "factory-fix.yml") == []
    assert labels_added(gh, 7) == ["factory:needs-human"]


def test_act_does_nothing_for_a_review_or_ignore_route() -> None:
    gh = github()
    assert review.act(gh, routed("ignore"), CONFIG) == "ignore"
    assert review.act(gh, routed("review"), CONFIG) == "ignore"
    assert writes(gh) == []


# --- post --------------------------------------------------------------------------------------


def evidence(green: bool = True, **changes: Any) -> Evidence:
    data = Evidence(
        green=green,
        red_green={"flaky": False, "red": {TEST_ID: "failed: 1 failed"}, "green": {TEST_ID: "passed: 1 passed"}, "reasons": []},
        original=0,
        new_groups=[],
        fence=FenceReport(True, [], 4, False, []),
        exact_revert=None,
        pr=12,
        head=HEAD,
        base=BASE,
        revert=False,
        reasons=() if green else ("failed on the head site: tests.e2e.test_docs::test_toc",),
    )
    return replace(data, **changes)


def verdict(kind: str = "approve", **changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "sha": HEAD,
        "verdict": kind,
        "summary": "The fix replaces the fixed width with a max-width; the evidence is green.",
        "checklist": dict(CHECKLIST),
        "comments": [],
    }
    data.update(changes)
    return data


PATCH = "@@ -1,3 +1,3 @@\n .term {\n-  width: 640px;\n+  max-width: 100%;\n }\n@@ -20,0 +21,2 @@\n+.a {}\n+.b {}"


def run_post(gh: FakeGitHub, data: Any, found: Evidence, route_: Route | None = None) -> str:
    return post(gh, 12, HEAD, data, found, route=route_ or routed(), config=CONFIG)


def test_post_dispatches_merge_after_approval() -> None:
    gh = github(files=[{"filename": "css/site.css", "patch": PATCH}])
    assert run_post(gh, verdict(), evidence()) == "approved"
    (created,) = posted(gh, f"{API}/pulls/12/reviews")
    assert (created["commit_id"], created["event"]) == (HEAD, "APPROVE")
    assert "max-width" in created["body"]
    assert dispatches(gh, "factory-merge.yml") == [{"ref": "main", "inputs": {"pr": "12"}}]
    assert dispatches(gh, "factory-fix.yml") == [] and labels_added(gh, 7) == []


def test_post_refuses_approval_without_green_evidence() -> None:
    gh = github()
    assert run_post(gh, verdict(), evidence(green=False)) == "refused"
    assert posted(gh, f"{API}/pulls/12/reviews") == []
    assert dispatches(gh, "factory-merge.yml") == []
    assert labels_added(gh, 7) == ["factory:needs-human"]
    assert any("not green" in text for text in comments_on(gh, 12))


def test_post_refuses_wrong_sha() -> None:
    gh = github()
    assert run_post(gh, verdict(sha="f" * 40), evidence()) == "refused"
    assert posted(gh, f"{API}/pulls/12/reviews") == [] and dispatches(gh, "factory-merge.yml") == []


@pytest.mark.parametrize("item", sorted(CHECKLIST))
def test_post_refuses_an_approval_with_a_false_checklist_item(item: str) -> None:
    gh = github()
    data = verdict(checklist={**CHECKLIST, item: False})
    assert run_post(gh, data, evidence()) == "refused"
    assert posted(gh, f"{API}/pulls/12/reviews") == []


def test_post_refuses_when_the_head_moved_or_the_evidence_is_for_another_commit() -> None:
    gh = github()
    assert run_post(gh, verdict(), evidence(), routed(head="f" * 40)) == "refused"
    assert run_post(gh, verdict(), evidence(head="f" * 40)) == "refused"
    assert run_post(gh, verdict(), evidence(), routed("ignore")) == "refused"
    assert writes(gh) == []  # stale: a later event reviews the new head


def test_post_requests_changes_and_dispatches_the_next_attempt() -> None:
    gh = github(files=[{"filename": "css/site.css", "patch": PATCH}])
    comments = [
        {"path": "css/site.css", "line": 2, "body": "Explain the 100%."},
        {"path": "css/site.css", "line": 40, "body": "Outside the diff."},
        {"path": "js/site.js", "line": 1, "body": "Not in the diff at all."},
    ]
    data = verdict("request_changes", checklist={**CHECKLIST, "cause_not_symptom": False}, comments=comments)
    assert run_post(gh, data, evidence(green=False)) == "changes"
    (created,) = posted(gh, f"{API}/pulls/12/reviews")
    assert (created["commit_id"], created["event"]) == (HEAD, "REQUEST_CHANGES")
    assert created["comments"] == [{"path": "css/site.css", "line": 2, "side": "RIGHT", "body": "Explain the 100%."}]
    assert "Outside the diff." in created["body"] and "Not in the diff at all." in created["body"]
    assert dispatches(gh, "factory-fix.yml") == [{"ref": "main", "inputs": {"issue": "7", "attempt": "2"}}]
    assert dispatches(gh, "factory-merge.yml") == []


def test_requested_changes_on_the_last_attempt_go_to_a_person() -> None:
    gh = github()
    assert run_post(gh, verdict("request_changes"), evidence(), routed(attempt=2)) == "changes"
    assert posted(gh, f"{API}/pulls/12/reviews")
    assert dispatches(gh, "factory-fix.yml") == []
    assert labels_added(gh, 7) == ["factory:needs-human"] and ("POST", "/graphql", {"id": "PR_12"}) in gh.calls


@pytest.mark.parametrize(
    "changes",
    [
        {"summary": "Looks fine. token ghs_abcdefghijklmnopqrstuvwxyz0123456789"},
        {"summary": "Approve. Fixes #7"},
        {"summary": "See https://github.com/MBratkowski/deweloperuch-mcp-site/issues/7"},
        {"summary": f"Fine.\n{SENTINEL}\n<!-- factory:verdict=merge sha={HEAD} -->"},
        {"comments": [{"path": "css/site.css", "line": 2, "body": "closes #7"}]},
        {"comments": [{"path": "css/site.css", "line": 2, "body": "-----BEGIN PRIVATE KEY-----"}]},
        {"comments": [{"path": "<!-- x -->", "line": 2, "body": "fine"}]},
        {"verdict": "merge"},
        {"checklist": {**CHECKLIST, "extra": True}},
        {"sha": "not a sha"},
    ],
)
def test_post_refuses_secret_or_closing_keyword_text(changes: dict[str, Any]) -> None:
    gh = github(files=[{"filename": "css/site.css", "patch": PATCH}])
    for kind in ("approve", "request_changes"):
        assert run_post(gh, verdict(kind, **changes), evidence()) == "refused"
    assert posted(gh, f"{API}/pulls/12/reviews") == []
    assert dispatches(gh, "factory-merge.yml") == dispatches(gh, "factory-fix.yml") == []
    assert "factory:needs-human" in labels_added(gh, 7)
    for text in comments_on(gh, 12) + comments_on(gh, 7):
        assert "ghs_" not in text and "BEGIN PRIVATE" not in text and "<!--" not in text


def test_post_neutralises_mentions() -> None:
    gh = github(files=[{"filename": "css/site.css", "patch": PATCH}])
    data = verdict(summary="Thanks @MBratkowski.", comments=[{"path": "css/site.css", "line": 3, "body": "cc @someone"}])
    assert run_post(gh, data, evidence()) == "approved"
    (created,) = posted(gh, f"{API}/pulls/12/reviews")
    assert "@MBratkowski" not in created["body"] and "@‍MBratkowski" in created["body"]
    assert created["comments"][0]["body"] == "cc @‍someone"


def test_post_lists_what_the_merger_will_block_on() -> None:
    gh = github()
    fence = FenceReport(True, [], 4, False, ["existing tests changed: tests/e2e/test_docs.py"])
    found = evidence(fence=fence, changed_baselines=("tests/visual/baselines/landing-mobile.png",))
    assert run_post(gh, verdict(), found) == "approved"
    (created,) = posted(gh, f"{API}/pulls/12/reviews")
    assert "- existing tests changed: `tests/e2e/test_docs.py`" in created["body"]
    assert "- `tests/visual/baselines/landing-mobile.png`" in created["body"]


def test_post_puts_head_chosen_names_into_code_spans() -> None:
    # Names the head chose (a baseline, a path the fence lists) and detail from the artifacts never
    # render as markdown, a link or a mention in the review.
    gh = github()
    link = "[click](https://example.com/x)"
    fence = FenceReport(True, [], 4, False, [f"existing tests changed: tests/e2e/{link}.py"])
    found = evidence(
        fence=fence,
        changed_baselines=("tests/visual/baselines/`@owner` a.png",),
        notes=("flaked once: tests.e2e.test_docs::test_toc[chromium]",),
    )
    assert run_post(gh, verdict(), found) == "approved"
    (created,) = posted(gh, f"{API}/pulls/12/reviews")
    body = created["body"]
    assert f"- existing tests changed: `tests/e2e/{link}.py`" in body
    assert "- ``tests/visual/baselines/`@‍owner` a.png``" in body
    assert "- flaked once: `tests.e2e.test_docs::test_toc[chromium]`" in body
    gh = github()
    reasons = (f"failed on the head site and failed on the rerun: tests.e2e.{link}::test_x",)
    assert run_post(gh, verdict(), evidence(green=False, reasons=reasons)) == "refused"
    assert any(f"failed on the head site and failed on the rerun: `tests.e2e.{link}::test_x`" in text
               for text in comments_on(gh, 12))


def reviewed(commit: str, login: str = "github-actions[bot]", state: str = "APPROVED") -> dict[str, Any]:
    return {"id": 1, "user": {"login": login}, "commit_id": commit, "state": state}


@pytest.mark.parametrize("kind", ["approve", "request_changes"])
def test_post_refuses_a_second_review_of_the_same_head(kind: str) -> None:
    gh = github(files=[{"filename": "css/site.css", "patch": PATCH}])
    gh.responses[("GET", f"{API}/pulls/12/reviews")] = [reviewed(HEAD, state="DISMISSED")]
    assert run_post(gh, verdict(kind), evidence()) == "refused"
    assert writes(gh) == []  # no review, no dispatch of the merger or the fixer, no hand-over


def test_post_reviews_a_head_reviewed_only_by_others_or_before() -> None:
    gh = github(files=[{"filename": "css/site.css", "patch": PATCH}])
    gh.responses[("GET", f"{API}/pulls/12/reviews")] = [reviewed("f" * 40), reviewed(HEAD, login=OWNER)]
    assert run_post(gh, verdict(), evidence()) == "approved"
    assert dispatches(gh, "factory-merge.yml") == [{"ref": "main", "inputs": {"pr": "12"}}]


def test_a_revert_is_approved_or_handed_to_a_person() -> None:
    found = evidence(revert=True, exact_revert=True, red_green={}, original=0)
    revert_route = routed(revert=True, attempt=0, merged=MERGED)
    gh = github()
    assert run_post(gh, verdict(), found, revert_route) == "approved"
    assert dispatches(gh, "factory-merge.yml") == [{"ref": "main", "inputs": {"pr": "12"}}]
    gh = github()
    assert run_post(gh, verdict("request_changes"), replace(found, green=False, exact_revert=False), revert_route) == "changes"
    assert dispatches(gh, "factory-fix.yml") == [] and labels_added(gh, 7) == ["factory:needs-human"]


def test_load_verdict_round_trips() -> None:
    loaded = load_verdict(verdict(comments=[{"path": "css/site.css", "line": 2, "body": "ok"}]))
    assert load_verdict(loaded.to_json()) == loaded
    assert loaded.comments[0].line == 2 and loaded.checklist == CHECKLIST


def test_verdict_schema_matches_the_code() -> None:
    text = SCHEMA.read_text(encoding="utf-8")
    schema = json.loads(text)
    assert tuple(schema["required"]) == review.VERDICT_FIELDS and schema["additionalProperties"] is False
    assert set(schema["properties"]) == set(review.VERDICT_FIELDS)
    assert tuple(schema["properties"]["verdict"]["enum"]) == review.VERDICTS
    checklist = schema["properties"]["checklist"]
    assert tuple(checklist["required"]) == review.CHECKLIST and checklist["additionalProperties"] is False
    assert all(item == {"type": "boolean"} for item in checklist["properties"].values())
    item = schema["properties"]["comments"]["items"]
    assert tuple(item["required"]) == review.COMMENT_FIELDS and item["additionalProperties"] is False
    assert schema["properties"]["sha"]["pattern"] == "^[0-9a-f]{40}$"
    # claude_args carries the schema inline, inside single quotes.
    assert "'" not in text


# --- the decisive behavioural check (R43) --------------------------------------------------------


JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="{count}" failures="0" errors="0" skipped="0">{cases}</testsuite></testsuites>
"""


def junit(path: Path, cases: Mapping[str, str]) -> Path:
    parts = []
    for test_id, status in cases.items():
        classname, name = test_id.split("::")
        child = {"passed": "", "failed": "<failure message='x'/>", "error": "<error message='x'/>",
                 "skipped": "<skipped message='x'/>"}[status]
        parts.append(f'<testcase classname="{classname}" name="{name}" time="0.1">{child}</testcase>')
    path.write_text(JUNIT.format(count=len(cases), cases="".join(parts)), encoding="utf-8")
    return path


def test_junit_results_read_every_testcase(tmp_path: Path) -> None:
    cases = {"tests.e2e.test_a::test_one[chromium]": "passed", "tests.e2e.test_a::test_two": "failed",
             "tests.e2e.test_b::test_three": "error", "tests.e2e.test_b::test_four": "skipped"}
    assert junit_results(junit(tmp_path / "j.xml", cases)) == cases
    assert junit_results(tmp_path / "missing.xml") is None
    (tmp_path / "broken.xml").write_text("<testsuites", encoding="utf-8")
    assert junit_results(tmp_path / "broken.xml") is None


def test_compare_sessions() -> None:
    base = {"t::a": "passed", "t::b": "passed", "t::c": "passed", "t::flaky": "failed", "t::net": "skipped"}
    head = {"t::a": "passed", "t::b": "failed", "t::flaky": "passed", "t::new": "failed"}
    reasons = compare_sessions(base, head)
    assert reasons == ["failed on the head site: t::b", "not collected on the head site: t::c"]
    # A test only the head session has is not part of the check; a base failure is not required.
    assert compare_sessions({"t::a": "passed"}, {"t::a": "passed", "t::new": "failed"}) == []
    # R46: a lost test that passes on the rerun is accepted; one that does not stays a reason.
    assert compare_sessions(base, head, {"t::b": "passed", "t::c": "error"}) == [
        "not collected on the head site and errored on the rerun: t::c"
    ]
    assert compare_sessions(base, head, {}) == [
        "failed on the head site and not collected on the rerun: t::b",
        "not collected on the head site and not collected on the rerun: t::c",
    ]


def test_node_id_turns_a_junit_id_into_a_pytest_node(tmp_path: Path) -> None:
    (tmp_path / "tests" / "e2e").mkdir(parents=True)
    (tmp_path / "tests" / "e2e" / "test_docs.py").write_text("", encoding="utf-8")
    node_id = review_evidence.node_id
    assert node_id(tmp_path, "tests.e2e.test_docs::test_toc[chromium-no-preference]") == (
        "tests/e2e/test_docs.py::test_toc[chromium-no-preference]"
    )
    assert node_id(tmp_path, "tests.e2e.test_docs.TestToc::test_one") == "tests/e2e/test_docs.py::TestToc::test_one"
    for unknown in ("tests.e2e.test_other::test_x", "test_x", "..tests::x", "tests/e2e.test_docs::x"):
        assert node_id(tmp_path, unknown) is None, unknown


def test_compare_sessions_lists_a_bounded_number_of_reasons() -> None:
    base = {f"t::test_{i}": "passed" for i in range(50)}
    reasons = compare_sessions(base, {})
    assert len(reasons) == review_evidence.MAX_REASONS + 1 and reasons[-1].startswith("and ")


def git(*args: str, cwd: Path) -> str:
    done = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
    )
    return done.stdout.strip()


def commit(repo: Path, message: str, files: Mapping[str, str | bytes | None]) -> str:
    for name, content in files.items():
        path = repo / name
        if content is None:
            path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", message, cwd=repo)
    return git("rev-parse", "HEAD", cwd=repo)


PYPROJECT = '[tool.pytest.ini_options]\ntestpaths = ["tests"]\npythonpath = ["."]\n'
SITE_TEST = (
    "from pathlib import Path\n\nROOT = Path(__file__).resolve().parents[2]\n\n\n"
    "def test_title() -> None:\n    assert 'Hello' in (ROOT / 'index.html').read_text()\n\n\n"
    "def test_stylesheet() -> None:\n    assert 'color' in (ROOT / 'css' / 'site.css').read_text()\n"
)
SITE = {
    "pyproject.toml": PYPROJECT,
    "index.html": "<h1>Hello</h1>\n",
    "docs/index.html": "<h1>Docs</h1>\n",
    "css/site.css": "h1 { color: red; }\n",
    "js/site.js": "\n",
    "assets/images.json": "{}\n",
    "favicon.png": b"\x89PNG\r\n\x1a\nfavicon",
    "apple-touch-icon.png": b"\x89PNG\r\n\x1a\nicon",
    "tests/e2e/test_site.py": SITE_TEST,
    "tests/visual/baselines/renderer.json": "{}\n",
    "tests/visual/baselines/landing-desktop.png": b"\x89PNG\r\n\x1a\nbase",
}


def site_repo(tmp_path: Path, head_files: Mapping[str, str | bytes | None]) -> tuple[Path, str, str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    repo = tmp_path / "project"
    git("init", "-q", "-b", "main", str(repo), cwd=tmp_path)
    base = commit(repo, "Base", SITE)
    git("checkout", "-q", "-b", BRANCH, cwd=repo)
    head = commit(repo, "Fix", head_files)
    git("checkout", "-q", "main", cwd=repo)
    return repo, base, head


def test_behaviour_check_runs_the_base_tests_against_the_head_site(tmp_path: Path) -> None:
    repo, base, head = site_repo(tmp_path, {"css/site.css": "h1 { color: blue; }\n"})
    result = behaviour_check(repo, base, head, tmp_path / "out")
    assert result.green is True and result.reasons == ()
    assert result.base_passed == 2 and result.head_passed == 2
    assert junit_results(tmp_path / "out" / "head-site-e2e.xml") == {
        "tests.e2e.test_site::test_title": "passed",
        "tests.e2e.test_site::test_stylesheet": "passed",
    }
    assert len(git("worktree", "list", cwd=repo).splitlines()) == 1


def test_behaviour_check_ignores_head_test_code(tmp_path: Path) -> None:
    # The head breaks the page and weakens the test that would notice: the base's test still runs.
    weakened = "def test_title() -> None:\n    assert True\n\n\ndef test_stylesheet() -> None:\n    assert True\n"
    repo, base, head = site_repo(
        tmp_path,
        {
            "index.html": "<h1>Goodbye</h1>\n",
            "tests/e2e/test_site.py": weakened,
            "tests/e2e/test_new.py": "def test_new() -> None:\n    assert False\n",
            "tests/e2e/conftest.py": "import pytest\n\n\n@pytest.fixture(autouse=True)\ndef nothing() -> None:\n    pass\n",
        },
    )
    result = behaviour_check(repo, base, head, tmp_path / "out")
    assert result.green is False and result.flaked == ()
    assert result.reasons == ("failed on the head site and failed on the rerun: tests.e2e.test_site::test_title",)
    head_session = junit_results(tmp_path / "out" / "head-site-e2e.xml")
    assert head_session is not None and "tests.e2e.test_new::test_new" not in head_session
    # The rerun ran only the lost test, with the base's test code, against the head site.
    assert junit_results(tmp_path / "out" / "head-site-rerun.xml") == {"tests.e2e.test_site::test_title": "failed"}


def test_behaviour_check_uses_the_head_site_files_and_baselines(tmp_path: Path) -> None:
    removed_css = {"css/site.css": None, "css/other.css": "h1 { font-weight: 700; }\n"}
    repo, base, head = site_repo(
        tmp_path, {**removed_css, "tests/visual/baselines/landing-desktop.png": b"\x89PNG\r\n\x1a\nhead"}
    )
    result = behaviour_check(repo, base, head, tmp_path / "out")
    assert result.green is False
    assert result.reasons == ("failed on the head site and failed on the rerun: tests.e2e.test_site::test_stylesheet",)
    assert result.changed_baselines == ("tests/visual/baselines/landing-desktop.png",)


FLAKY_SITE_TEST = (
    "import os\nfrom pathlib import Path\n\n\n"
    "def test_flaky() -> None:\n"
    "    counter = Path(os.environ['FLAKE_COUNTER'])\n"
    "    runs = int(counter.read_text()) + 1 if counter.exists() else 1\n"
    "    counter.write_text(str(runs))\n"
    "    assert runs != 2  # the second run of the whole check: the head-site session\n"
)


def test_behaviour_check_accepts_a_test_that_flakes_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Run 1 on the pure base passes, run 2 in the head-site session fails, run 3 in the fresh rerun
    # session passes: accepted and recorded as flaked once (R46).
    monkeypatch.setenv("FLAKE_COUNTER", str(tmp_path / "counter"))
    repo, base, head = site_repo(tmp_path, {"css/site.css": "h1 { color: blue; }\n"})
    flaky = commit(repo, "A flaky test", {"tests/e2e/test_flaky.py": FLAKY_SITE_TEST})
    git("checkout", "-q", BRANCH, cwd=repo)
    git("rebase", "-q", "main", cwd=repo)
    head = git("rev-parse", "HEAD", cwd=repo)
    result = behaviour_check(repo, flaky, head, tmp_path / "out")
    assert result.green is True and result.reasons == ()
    assert result.flaked == ("tests.e2e.test_flaky::test_flaky",)
    assert (result.base_passed, result.head_passed) == (3, 3)
    assert (tmp_path / "counter").read_text() == "3"


def recording_rerun(results: Mapping[str, str]):
    calls: list[list[str]] = []

    def rerun(tree: Path, nodes: Sequence[str], report: Path) -> int:
        calls.append(list(nodes))
        junit(report, {test_id: status for test_id, status in results.items()})
        return 0

    return rerun, calls


def test_behaviour_check_reruns_only_the_lost_tests_once(tmp_path: Path) -> None:
    repo, base, head = site_repo(tmp_path, {"css/site.css": "h1 { color: blue; }\n"})
    title, sheet = "tests.e2e.test_site::test_title", "tests.e2e.test_site::test_stylesheet"
    sessions = fake_sessions({title: "passed", sheet: "passed"}, {title: "failed"})
    rerun, calls = recording_rerun({title: "passed", sheet: "failed"})
    result = behaviour_check(repo, base, head, tmp_path / "out", run=sessions, rerun=rerun)
    assert calls == [["tests/e2e/test_site.py::test_title", "tests/e2e/test_site.py::test_stylesheet"]]
    assert result.flaked == (title,) and result.green is False
    assert result.reasons == (f"not collected on the head site and failed on the rerun: {sheet}",)
    # Nothing lost: no rerun at all.
    rerun, calls = recording_rerun({})
    assert behaviour_check(repo, base, head, tmp_path / "b", run=fake_sessions({title: "passed"}, {title: "passed"}),
                           rerun=rerun).green is True
    assert calls == []


def test_behaviour_check_reruns_nothing_when_the_base_did_not_run_cleanly(tmp_path: Path) -> None:
    repo, base, head = site_repo(tmp_path, {"css/site.css": "h1 { color: blue; }\n"})
    title = "tests.e2e.test_site::test_title"
    rerun, calls = recording_rerun({title: "passed"})
    result = behaviour_check(repo, base, head, tmp_path / "out", run=fake_sessions({title: "passed"}, {}, codes=(2, 1)),
                             rerun=rerun)
    assert result.green is False and calls == [] and result.flaked == ()


def test_install_head_site_copies_only_the_published_files(tmp_path: Path) -> None:
    repo, base, head = site_repo(
        tmp_path,
        {
            "index.html": "<h1>Hello again</h1>\n",
            "css/site.css": None,
            "css/new.css": "a {}\n",
            "tests/e2e/test_site.py": "def test_title() -> None:\n    assert True\n",
            "tools/evil.py": "raise SystemExit(1)\n",
            "tests/visual/baselines/landing-desktop.png": b"\x89PNG\r\n\x1a\nhead",
            "tests/visual/baselines/renderer.json": '{"head": true}\n',
        },
    )
    tree = tmp_path / "tree"
    git("worktree", "add", "--detach", str(tree), base, cwd=repo)
    review_evidence.install_head_site(repo, head, tree)
    assert (tree / "index.html").read_text() == "<h1>Hello again</h1>\n"
    assert not (tree / "css" / "site.css").exists() and (tree / "css" / "new.css").exists()
    assert (tree / "tests" / "e2e" / "test_site.py").read_text() == SITE_TEST
    assert not (tree / "tools").exists()
    assert (tree / "tests/visual/baselines/landing-desktop.png").read_bytes().endswith(b"head")
    assert (tree / "tests/visual/baselines/renderer.json").read_text() == "{}\n"


def fake_sessions(base: Mapping[str, str], head: Mapping[str, str], codes: tuple[int, int] = (1, 1)):
    def run(tree: Path, suite: str, report: Path) -> int:
        is_head = report.name.startswith("head-site")
        if suite == "e2e":
            junit(report, head if is_head else base)
            return codes[1] if is_head else codes[0]
        junit(report, {})
        return 5

    return run


def test_behaviour_check_needs_a_clean_base_session(tmp_path: Path) -> None:
    repo, base, head = site_repo(tmp_path, {"css/site.css": "h1 { color: blue; }\n"})
    broken = fake_sessions({"t::a": "passed"}, {"t::a": "passed"}, codes=(2, 0))
    assert behaviour_check(repo, base, head, tmp_path / "a", run=broken).green is False
    nothing = fake_sessions({}, {}, codes=(5, 5))
    result = behaviour_check(repo, base, head, tmp_path / "b", run=nothing)
    assert result.green is False and "no test passed on the base" in result.reasons[0]


# --- head's own tests ------------------------------------------------------------------------------


CALC_PYPROJECT = PYPROJECT
CALC_TEST = "from calc import add\n\n\ndef test_zero() -> None:\n    assert add(0, 0) == 0\n"


def calc_repo(tmp_path: Path, head_files: Mapping[str, str]) -> tuple[Path, str, str]:
    repo = tmp_path / "calc"
    git("init", "-q", "-b", "main", str(repo), cwd=tmp_path)
    base = commit(repo, "Base", {"pyproject.toml": CALC_PYPROJECT, "calc.py": "def add(a, b):\n    return a - b\n",
                                 "tests/test_calc.py": CALC_TEST})
    head = commit(repo, "Fix", head_files)
    return repo, base, head


NEW_TEST = "from calc import add\n\n\ndef test_add() -> None:\n    assert add(2, 2) == 4\n"


def test_head_tests_must_fail_on_the_base_and_pass_on_the_head(tmp_path: Path) -> None:
    repo, base, head = calc_repo(tmp_path, {"calc.py": "def add(a, b):\n    return a + b\n", "tests/test_add.py": NEW_TEST})
    proof = prove_head_tests(repo, base, head, ["tests/test_add.py::test_add"], flaky=False)
    assert proof.ok is True and proof.reasons == ()
    assert proof.red["tests/test_add.py::test_add"].startswith("failed")
    assert proof.green["tests/test_add.py::test_add"].startswith("passed")


def test_a_head_test_that_passes_on_the_base_is_not_green(tmp_path: Path) -> None:
    passing = "from calc import add\n\n\ndef test_one() -> None:\n    assert add(1, 0) == 1\n"
    repo, base, head = calc_repo(tmp_path, {"tests/test_more.py": passing})
    proof = prove_head_tests(repo, base, head, ["tests/test_more.py::test_one"], flaky=False)
    assert proof.ok is False and "not red on the base" in proof.reasons[0]


def test_no_named_test_is_not_green(tmp_path: Path) -> None:
    repo, base, head = calc_repo(tmp_path, {"calc.py": "def add(a, b):\n    return a + b\n"})
    proof = prove_head_tests(repo, base, head, [], flaky=False)
    assert proof.ok is False and "names no test" in proof.reasons[0]


def test_flaky_selectors_are_the_issues_flaky_test_ids() -> None:
    cell = Cell("ci", "chromium", 0, 0, "default")
    flaky = Group("flaky", "ci", "default", TEST_ID, (), (), (Finding("flaky", cell, TEST_ID, "failed and passed", None),))
    job = replace(flaky, selector="Validate this commit / Tests (pytest, Playwright)")
    assert review_evidence.flaky_selectors(groups_body([flaky, job, overflow_group()])) == [TEST_ID]


@pytest.mark.parametrize(("base_failures", "head_failures", "ok"), [(3, 0, True), (0, 0, False), (3, 1, False)])
def test_a_flaky_fix_is_proven_statistically(
    base_failures: int, head_failures: int, ok: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, base, head = calc_repo(tmp_path, {"calc.py": "def add(a, b):\n    return a + b\n"})
    calls: list[tuple[str, int, int]] = []

    def fake_stress(test_id: str, runs: int, throttle: int, cwd: Path) -> StressResult:
        calls.append((test_id, runs, throttle))
        fixed = "a + b" in (Path(cwd) / "calc.py").read_text()
        failures = head_failures if fixed else base_failures
        return StressResult(runs, failures, "boom" if failures else "")

    monkeypatch.setenv("GH_TOKEN", "ghs_not_for_tests")
    proof = prove_head_tests(repo, base, head, ["tests/test_calc.py::test_zero"], flaky=True, stress_fn=fake_stress)
    assert proof.ok is ok and proof.flaky
    assert {(runs, throttle) for _, runs, throttle in calls} == {(50, 4)}


# --- the evidence job ------------------------------------------------------------------------------


def overflow_group(width: int = 375, selector: str = "div.term", page: str = "landing") -> Group:
    cell = Cell(page, "chromium", width, 900, "default")
    finding = Finding("overflow", cell, selector, "scrollWidth 640 > clientWidth 375", (0, 0, 600, 40))
    return Group("overflow", page, "default", selector, ("chromium",), (width,), (finding,))


def groups_body(groups: Sequence[Group] = (), script: str | None = None) -> str:
    encoded = base64.b64encode(json.dumps([g.to_json() for g in groups]).encode()).decode()
    markers = [SENTINEL, f"<!-- factory:groups={encoded} -->"]
    if script:
        markers.append(f"<!-- factory:script={script} -->")
    return "## Problem\n\nThe terminal overflows.\n\n" + "\n".join(markers) + "\n"


class FakeLab:
    """The browser side of the evidence job: sites by commit, reproductions and probe runs."""

    def __init__(self, codes: Mapping[str, int], probes: Mapping[str, Sequence[Group]]) -> None:
        self.codes = codes
        self.probes = probes
        self.calls: list[tuple[str, ...]] = []

    def site(self, sha: str) -> str:
        return f"http://127.0.0.1/{sha}/"

    def reproduce(self, group: Group, url: str) -> int:
        self.calls.append(("reproduce", group.fingerprint, url))
        return self.codes.get(url, 0)

    def probe(self, cells: Sequence[Cell], url: str, out: Path) -> list[Group]:
        self.calls.append(("probe", url, ",".join(sorted({c.engine for c in cells})), ",".join(sorted({c.page for c in cells}))))
        return list(self.probes.get(url, ()))


def site_route(base: str, head: str, **changes: Any) -> Route:
    return routed(base=base, head=head, **changes)


GREEN_SESSIONS = fake_sessions({"t::a": "passed"}, {"t::a": "passed"})


def test_gather_decisive_is_green_when_every_check_holds(tmp_path: Path) -> None:
    repo, base, head = site_repo(tmp_path, {"css/site.css": "h1 { color: blue; }\n"})
    lab = FakeLab({}, {})
    found = gather_decisive(repo, site_route(base, head), groups_body([overflow_group()]), CONFIG, tmp_path / "out",
                            lab=lab, run=GREEN_SESSIONS)
    assert found.green is True and found.reasons == ()
    assert found.original == 0 and found.new_groups == () and found.fence is not None and found.fence.ok
    head_url, base_url = lab.site(head), lab.site(base)
    assert ("reproduce", overflow_group().fingerprint, head_url) in lab.calls
    # The neighbourhood: the issue's page on every width of its engines, on the head and the base.
    assert ("probe", head_url, "chromium", "landing") in lab.calls and ("probe", base_url, "chromium", "landing") in lab.calls


def test_the_original_reproduction_must_be_gone_on_the_head(tmp_path: Path) -> None:
    repo, base, head = site_repo(tmp_path, {"css/site.css": "h1 { color: blue; }\n"})
    lab = FakeLab({f"http://127.0.0.1/{head}/": 1}, {})
    found = gather_decisive(repo, site_route(base, head), groups_body([overflow_group()]), CONFIG, tmp_path / "out",
                            lab=lab, run=GREEN_SESSIONS)
    assert found.green is False and found.original == 1
    assert any("still reproduces" in reason for reason in found.reasons)


def test_a_new_group_in_the_neighbourhood_is_not_green(tmp_path: Path) -> None:
    repo, base, head = site_repo(tmp_path, {"css/site.css": "h1 { color: blue; }\n"})
    known, new = overflow_group(1440, "header"), overflow_group(320, "nav a")
    lab = FakeLab({}, {f"http://127.0.0.1/{base}/": [known], f"http://127.0.0.1/{head}/": [known, new]})
    found = gather_decisive(repo, site_route(base, head), groups_body([overflow_group()]), CONFIG, tmp_path / "out",
                            lab=lab, run=GREEN_SESSIONS)
    assert found.green is False
    assert [g["fingerprint"] for g in found.new_groups] == [new.fingerprint]


def test_a_failing_behavioural_check_is_not_green(tmp_path: Path) -> None:
    repo, base, head = site_repo(tmp_path, {"css/site.css": "h1 { color: blue; }\n"})
    title = "tests.e2e.test_site::test_title"
    failing = fake_sessions({title: "passed"}, {title: "failed"})
    rerun, calls = recording_rerun({title: "failed"})
    found = gather_decisive(repo, site_route(base, head), groups_body([overflow_group()]), CONFIG, tmp_path / "out",
                            lab=FakeLab({}, {}), run=failing, rerun=rerun)
    assert found.green is False and f"failed on the head site and failed on the rerun: {title}" in found.reasons
    assert calls == [["tests/e2e/test_site.py::test_title"]]
    flaked = gather_decisive(repo, site_route(base, head), groups_body([overflow_group()]), CONFIG, tmp_path / "out2",
                             lab=FakeLab({}, {}), run=failing, rerun=recording_rerun({title: "passed"})[0])
    assert flaked.green is True and flaked.behaviour is not None and flaked.behaviour.flaked == (title,)


def test_hard_fence_reasons_are_not_green_and_review_reasons_are_listed(tmp_path: Path) -> None:
    repo, base, head = site_repo(tmp_path, {"css/site.css": "h1 { color: blue; }\n", "pyproject.toml": PYPROJECT + "\n"})
    found = gather_decisive(repo, site_route(base, head), groups_body([overflow_group()]), CONFIG, tmp_path / "out",
                            lab=FakeLab({}, {}), run=GREEN_SESSIONS)
    assert found.green is False and "fence: forbidden path: pyproject.toml" in found.reasons
    changed = SITE_TEST + "\n\ndef test_more() -> None:\n    assert True\n"
    repo2, base2, head2 = site_repo(tmp_path / "soft", {"tests/e2e/test_site.py": changed.replace("'Hello'", "'Hel' 'lo'")})
    soft = gather_decisive(repo2, site_route(base2, head2), groups_body([overflow_group()]), CONFIG, tmp_path / "out2",
                           lab=FakeLab({}, {}), run=GREEN_SESSIONS)
    assert soft.fence is not None and soft.fence.ok and soft.fence.review
    assert soft.green is True


SCRIPT_URL = f"https://raw.githubusercontent.com/{REPO}/0123abcd/issues/abc/1/terminal.py"


def test_the_decisive_phase_never_runs_the_reproduction_script(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # R45: the script is code an agent wrote; it runs only after the decisive evidence is uploaded.
    def never(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the decisive phase ran or fetched the script")

    monkeypatch.setattr(review_evidence, "download_script", never)
    monkeypatch.setattr(review_evidence.check_drafts, "run_script", never)
    monkeypatch.setattr(review_evidence, "_script_code", never)
    repo, base, head = site_repo(tmp_path, {"css/site.css": "h1 { color: blue; }\n"})
    found = gather_decisive(repo, site_route(base, head), groups_body(script=SCRIPT_URL), CONFIG, tmp_path / "out",
                            lab=FakeLab({}, {}), run=GREEN_SESSIONS)
    assert found.green is True and found.original == 0 and found.script is True
    assert review_evidence.Decisive.from_json(json.loads(json.dumps(found.to_json()))).script is True
    both = gather_decisive(repo, site_route(base, head), groups_body([overflow_group()], script=SCRIPT_URL), CONFIG,
                           tmp_path / "out2", lab=FakeLab({}, {}), run=GREEN_SESSIONS)
    assert both.script is True and both.original == 0


def test_the_script_phase_runs_the_reproduction_script_on_the_head(tmp_path: Path) -> None:
    repo, base, head = site_repo(tmp_path, {"css/site.css": "h1 { color: blue; }\n"})
    ran: list[tuple[str, str]] = []

    def run_script(relative: str, site: str) -> int:
        ran.append((relative, site))
        return 0

    hooks: dict[str, Any] = {"lab": FakeLab({}, {}), "fetch_script": lambda u: b"print('x')\n",
                             "run_script": run_script, "script_root": tmp_path / "scripts"}
    result = review_evidence.gather_script(repo, site_route(base, head), groups_body(script=SCRIPT_URL), CONFIG, **hooks)
    assert (result.ran, result.code, result.head, result.base, result.pr) == (True, 0, head, base, 12)
    assert ran == [("factory-out/repros/review-7.py", f"http://127.0.0.1/{head}/")]
    assert (tmp_path / "scripts" / "factory-out/repros/review-7.py").read_bytes() == b"print('x')\n"
    assert review_evidence.ScriptResult.from_json(json.loads(json.dumps(result.to_json()))) == result
    refused = review_evidence.gather_script(repo, site_route(base, head), groups_body(script="https://example.com/x.py"),
                                            CONFIG, **hooks)
    assert (refused.ran, refused.code) == (True, 2) and len(ran) == 1
    still = review_evidence.gather_script(repo, site_route(base, head), groups_body(script=SCRIPT_URL), CONFIG,
                                          **{**hooks, "run_script": lambda relative, site: 1})
    assert still.code == 1 and "still finds" in still.reason
    none = review_evidence.gather_script(repo, site_route(base, head), groups_body([overflow_group()]), CONFIG, **hooks)
    assert (none.ran, none.code) == (False, 0) and len(ran) == 1


def script_result(code: int = 0, *, ran: bool = True, head: str = HEAD) -> review_evidence.ScriptResult:
    return review_evidence.ScriptResult(pr=12, issue=7, base=BASE, head=head, ran=ran, code=code, reason="r")


def decisive_record(*, script: bool = True, flaked: tuple[str, ...] = ()) -> review_evidence.Decisive:
    return review_evidence.Decisive(
        pr=12, issue=7, base=BASE, head=HEAD, revert=False, fence=FenceReport(True, [], 4, False, []),
        exact_revert=None, behaviour=Behaviour(True, (), 10, 10, (), flaked), original=0, original_reason="gone",
        new_groups=(), reasons=(), script=script,
    )


GOOD_PROOF = HeadProof(head=HEAD, base=BASE, flaky=False, red={TEST_ID: "failed: x"}, green={TEST_ID: "passed: x"},
                       reasons=(), logs={})


@pytest.mark.parametrize(
    ("script", "green", "original"),
    [
        (script_result(0), True, 0),
        (script_result(1), False, 1),
        (script_result(2), False, 2),
        (script_result(0, ran=False), False, 2),
        (script_result(0, head="f" * 40), False, 2),
        (None, False, 2),
    ],
)
def test_evidence_needs_the_script_result_when_the_issue_has_a_script(
    script: review_evidence.ScriptResult | None, green: bool, original: int
) -> None:
    found = review_evidence.evaluate(decisive_record(), GOOD_PROOF, revert=False, tests=[TEST_ID], flaky=False,
                                     script=script)
    assert (found.green, found.original) == (green, original), found.reasons


def test_the_trusted_issue_can_require_the_script_result_on_its_own() -> None:
    # The decisive file says no script, but the issue post routed again carries one: still required.
    args: dict[str, Any] = {"revert": False, "tests": [TEST_ID], "flaky": False}
    assert review_evidence.evaluate(decisive_record(script=False), GOOD_PROOF, **args).green is True
    found = review_evidence.evaluate(decisive_record(script=False), GOOD_PROOF, script_required=True, **args)
    assert found.green is False and "no result of the reproduction script" in found.reasons


def test_a_flake_is_accepted_with_a_note() -> None:
    found = review_evidence.evaluate(decisive_record(script=False, flaked=("t::a", "t::b")), GOOD_PROOF,
                                     revert=False, tests=[TEST_ID], flaky=False)
    assert found.green is True and found.notes == ("flaked once: t::a, t::b",)


def test_the_proofs_own_reasons_count_but_their_text_never_reaches_a_review() -> None:
    proof = replace(GOOD_PROOF, reasons=("@owner see [this](https://example.com)",))
    found = review_evidence.evaluate(decisive_record(script=False), proof, revert=False, tests=[TEST_ID], flaky=False)
    assert found.green is False
    assert found.reasons == ("the proof of the head's tests reports further problems (see proof.json)",)


def test_an_issue_without_a_reproduction_is_not_green(tmp_path: Path) -> None:
    repo, base, head = site_repo(tmp_path, {"css/site.css": "h1 { color: blue; }\n"})
    found = gather_decisive(repo, site_route(base, head), "No markers.\n", CONFIG, tmp_path / "out",
                            lab=FakeLab({}, {}), run=GREEN_SESSIONS)
    assert found.green is False and found.original == 2


def test_gather_evidence_combines_the_decisive_check_and_the_head_tests(tmp_path: Path) -> None:
    head_files = {"css/site.css": "h1 { color: blue; }\n",
                  "tests/e2e/test_terminal.py": "def test_terminal_fits() -> None:\n    assert 'blue' in open('css/site.css').read()\n"}
    repo, base, head = site_repo(tmp_path, head_files)
    found = gather_evidence(repo, site_route(base, head), groups_body([overflow_group()]), pr_body(head=head), CONFIG,
                            tmp_path / "out", lab=FakeLab({}, {}), run=GREEN_SESSIONS)
    assert isinstance(found, Evidence)
    assert found.green is True, found.reasons
    assert found.red_green["red"][TEST_ID].startswith("failed") and found.red_green["green"][TEST_ID].startswith("passed")
    assert (found.head, found.base, found.pr) == (head, base, 12)


def test_evidence_files_round_trip_and_reject_bad_data(tmp_path: Path) -> None:
    repo, base, head = site_repo(tmp_path, {"css/site.css": "h1 { color: blue; }\n"})
    decisive = gather_decisive(repo, site_route(base, head), groups_body([overflow_group()]), CONFIG, tmp_path / "out",
                               lab=FakeLab({}, {}), run=GREEN_SESSIONS)
    proof = HeadProof(head=head, base=base, flaky=False, red={TEST_ID: "failed: x"}, green={TEST_ID: "passed: x"},
                      reasons=(), logs={})
    decisive_path, proof_path = tmp_path / "decisive.json", tmp_path / "proof.json"
    decisive_path.write_text(json.dumps(decisive.to_json()), encoding="utf-8")

    def load(data: Any, **changes: Any) -> Evidence:
        proof_path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
        options: dict[str, Any] = {"revert": False, "tests": [TEST_ID], "flaky": False, **changes}
        return review_evidence.load_evidence(decisive_path, proof_path, **options)

    loaded = load(proof.to_json())
    assert loaded.green is True and loaded.head == head and loaded.fence == decisive.fence
    assert loaded.red_green["red"] == {TEST_ID: "failed: x"}
    # The proof comes from the job that ran the head's code: its verdicts are read from the data.
    for broken in (replace(proof, head=base), replace(proof, red={TEST_ID: "passed: x"}), replace(proof, red={}, green={})):
        assert load(broken.to_json()).green is False
    assert load(proof.to_json(), tests=[TEST_ID, "tests/e2e/test_more.py::test_more"]).green is False
    assert load(proof.to_json(), flaky=True).green is False
    # A flaky issue's pull request must name the issue's flaky test, whatever the proof says.
    flaky_proof = replace(proof, flaky=True, red={TEST_ID: "failed: 3 of 50 throttled runs failed"},
                          green={TEST_ID: "passed: 0 of 50 throttled runs failed"})
    assert load(flaky_proof.to_json(), flaky=True, flaky_tests=[TEST_ID]).green is True
    other = "tests/e2e/test_docs.py::test_toc_tracks_anchor_navigation_and_history"
    unnamed = load(flaky_proof.to_json(), flaky=True, flaky_tests=[other])
    assert unnamed.green is False and f"does not name the flaky test {other}" in unnamed.reasons[0]
    unreadable = load('{"green": true}')
    assert unreadable.green is False and unreadable.reasons
    # A revert needs no proof at all.
    assert load('{"green": false}', revert=True).green is (decisive.exact_revert is True)
    missing = review_evidence.load_evidence(tmp_path / "none.json", proof_path, revert=False, tests=[TEST_ID], flaky=False)
    assert missing.green is False and missing.head == ""


# --- an exact revert -------------------------------------------------------------------------------


def revert_repo(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "site"
    git("init", "-q", "-b", "main", str(repo), cwd=tmp_path)
    lines = "".join(f"line {i}\n" for i in range(40))
    commit(repo, "Base", {"css/site.css": lines, "README.md": "readme\n", "assets/logo.png": b"\x89PNG\r\n\x1a\nold"})
    merged = commit(
        repo,
        "The fix",
        {
            "css/site.css": lines.replace("line 3\n", "line three\n"),
            "tests/e2e/test_terminal.py": "def test_terminal() -> None:\n    assert True\n",
            "assets/logo.png": b"\x89PNG\r\n\x1a\nnew",
        },
    )
    # Main moves on: an unrelated file and a far-away line of the same stylesheet.
    css = (repo / "css" / "site.css").read_text().replace("line 30\n", "line thirty\n")
    base = commit(repo, "Later", {"README.md": "readme 2\n", "css/site.css": css})
    return repo, base, merged


def test_exact_revert(tmp_path: Path) -> None:
    repo, base, merged = revert_repo(tmp_path)
    git("checkout", "-q", "-b", "revert/7", cwd=repo)
    git("revert", "--no-edit", merged, cwd=repo)
    head = git("rev-parse", "HEAD", cwd=repo)
    assert exact_revert(repo, base, head, merged) == (True, "")

    extra = commit(repo, "And more", {"js/site.js": "console.log(1)\n"})
    ok, reason = exact_revert(repo, base, extra, merged)
    assert ok is False and "js/site.js" in reason

    git("checkout", "-q", "-b", "partial", base, cwd=repo)
    partial = commit(repo, "Half a revert", {"tests/e2e/test_terminal.py": None})
    ok, reason = exact_revert(repo, base, partial, merged)
    assert ok is False and "css/site.css" in reason

    # The same files, but not the same lines or bytes back.
    css = (repo / "css" / "site.css").read_text()
    for name, changes in (
        ("other-lines", {"css/site.css": css.replace("line three\n", "line THREE\n"), "assets/logo.png": b"\x89PNG\r\n\x1a\nold"}),
        ("other-bytes", {"css/site.css": css.replace("line three\n", "line 3\n"), "assets/logo.png": b"\x89PNG\r\n\x1a\nnewer"}),
    ):
        git("checkout", "-q", "-b", name, base, cwd=repo)
        lookalike = commit(repo, "A lookalike", {**changes, "tests/e2e/test_terminal.py": None})
        assert set(review_evidence._changes(repo, base, lookalike)) == set(review_evidence._changes(repo, merged, f"{merged}^"))
        ok, reason = exact_revert(repo, base, lookalike, merged)
        assert ok is False, name

    assert exact_revert(repo, base, head, None)[0] is False
    assert exact_revert(repo, base, head, base)[0] is False  # the inverse of another commit


def test_gather_decisive_for_a_revert_checks_only_the_exact_revert(tmp_path: Path) -> None:
    repo, base, merged = revert_repo(tmp_path)
    git("checkout", "-q", "-b", "revert/7", cwd=repo)
    git("revert", "--no-edit", merged, cwd=repo)
    head = git("rev-parse", "HEAD", cwd=repo)
    lab = FakeLab({}, {})

    def no_sessions(tree: Path, suite: str, report: Path) -> int:
        raise AssertionError("a revert runs no test session")

    found = gather_decisive(repo, site_route(base, head, revert=True, merged=merged, attempt=0), "", CONFIG,
                            tmp_path / "out", lab=lab, run=no_sessions)
    assert found.green is True and found.exact_revert is True and lab.calls == []
    wrong = gather_decisive(repo, site_route(base, head, revert=True, merged=base, attempt=0), "", CONFIG,
                            tmp_path / "out2", lab=lab, run=no_sessions)
    assert wrong.green is False and wrong.exact_revert is False


# --- the command line ------------------------------------------------------------------------------


def test_cli_route_prints_outputs_and_writes_the_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                       capsys: pytest.CaptureFixture[str]) -> None:
    gh = github()
    monkeypatch.setattr(review, "GitHub", lambda *args, **kwargs: gh)
    monkeypatch.setenv("FACTORY_APP_SLUG", "factory-app")
    event = tmp_path / "event.json"
    event.write_text(json.dumps(run_event()), encoding="utf-8")
    assert review.main(["route", "--event", str(event), "--out", str(tmp_path / "route")]) == 0
    lines = dict(line.split("=", 1) for line in capsys.readouterr().out.splitlines())
    assert lines["action"] == "review" and lines["pr"] == "12" and lines["head"] == HEAD and lines["check_run"] == "555"
    saved = Route.from_json(json.loads((tmp_path / "route" / "route.json").read_text(encoding="utf-8")))
    assert saved.action == "review" and saved.base == BASE and saved.pr_body == pull_data()["body"]
    context = (tmp_path / "route" / "context.md").read_text(encoding="utf-8")
    assert "The terminal overflows." in context and "BEGIN ISSUE BODY" in context and HEAD in context


def write_evidence_files(tmp_path: Path) -> tuple[Path, Path]:
    decisive = review_evidence.Decisive(
        pr=12, issue=7, base=BASE, head=HEAD, revert=False, fence=FenceReport(True, [], 4, False, []),
        exact_revert=None, behaviour=Behaviour(True, (), 10, 10, ()), original=0, original_reason="gone",
        new_groups=(), reasons=(),
    )
    proof = HeadProof(head=HEAD, base=BASE, flaky=False, red={TEST_ID: "failed: x"}, green={TEST_ID: "passed: x"},
                      reasons=(), logs={})
    (tmp_path / "decisive").mkdir()
    (tmp_path / "decisive" / "decisive.json").write_text(json.dumps(decisive.to_json()), encoding="utf-8")
    (tmp_path / "proof" / "evidence-proof").mkdir(parents=True)  # download-artifact by id nests by name
    (tmp_path / "proof" / "evidence-proof" / "proof.json").write_text(json.dumps(proof.to_json()), encoding="utf-8")
    return tmp_path / "decisive", tmp_path / "proof"


def cli_post(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gh: FakeGitHub, verdict_file: Path) -> int:
    monkeypatch.setattr(review, "GitHub", lambda *args, **kwargs: gh)
    monkeypatch.setenv("FACTORY_APP_SLUG", "factory-app")
    event = tmp_path / "event.json"
    event.write_text(json.dumps(run_event()), encoding="utf-8")
    decisive, proof = write_evidence_files(tmp_path)
    return review.main(["post", "--event", str(event), "--verdict", str(verdict_file),
                        "--decisive", str(decisive), "--script", str(tmp_path / "script"), "--proof", str(proof)])


def test_cli_post_routes_again_and_posts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gh = github(files=[{"filename": "css/site.css", "patch": PATCH}])
    (tmp_path / "verdict.json").write_text(json.dumps(verdict()), encoding="utf-8")
    assert cli_post(tmp_path, monkeypatch, gh, tmp_path / "verdict.json") == 0
    (created,) = posted(gh, f"{API}/pulls/12/reviews")
    assert (created["commit_id"], created["event"]) == (HEAD, "APPROVE")
    assert dispatches(gh, "factory-merge.yml") == [{"ref": "main", "inputs": {"pr": "12"}}]


def test_cli_post_needs_the_script_result_of_an_issue_with_a_script(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The decisive file says nothing about a script, but the issue post routes again carries one.
    issue = issue_data(body=groups_body(script=SCRIPT_URL))
    (tmp_path / "verdict.json").write_text(json.dumps(verdict()), encoding="utf-8")
    gh = github(issue=issue, files=[{"filename": "css/site.css", "patch": PATCH}])
    assert cli_post(tmp_path, monkeypatch, gh, tmp_path / "verdict.json") == 0
    assert posted(gh, f"{API}/pulls/12/reviews") == [] and dispatches(gh, "factory-merge.yml") == []
    assert any("reproduction script" in text for text in comments_on(gh, 12))
    # With the script's result for this head, the approval goes through.
    other = tmp_path / "second"
    other.mkdir()
    (other / "script").mkdir()
    (other / "script" / "script.json").write_text(json.dumps(script_result(0).to_json()), encoding="utf-8")
    (other / "verdict.json").write_text(json.dumps(verdict()), encoding="utf-8")
    gh = github(issue=issue, files=[{"filename": "css/site.css", "patch": PATCH}])
    assert cli_post(other, monkeypatch, gh, other / "verdict.json") == 0
    assert dispatches(gh, "factory-merge.yml") == [{"ref": "main", "inputs": {"pr": "12"}}]


@pytest.mark.parametrize("content", [None, "not json", json.dumps(["a list"])])
def test_cli_post_refuses_a_missing_or_broken_verdict(
    content: str | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gh = github()
    path = tmp_path / "verdict.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    assert cli_post(tmp_path, monkeypatch, gh, path) == 0
    assert posted(gh, f"{API}/pulls/12/reviews") == [] and "factory:needs-human" in labels_added(gh, 7)


# --- the change, written out for the reviewer agent (R45) ----------------------------------------------


def test_write_change_writes_the_diff_the_log_and_the_heads_files_as_data(tmp_path: Path) -> None:
    repo, base, head = site_repo(
        tmp_path,
        {"css/site.css": "h1 { color: blue; }\n", "css/new.css": "a {}\n", "js/site.js": None,
         "tests/visual/baselines/landing-desktop.png": b"\x89PNG\r\n\x1a\nhead"},
    )
    git("checkout", "-q", BRANCH, cwd=repo)
    (repo / "link").symlink_to("css/site.css")
    git("add", "link", cwd=repo)
    git("commit", "-q", "-m", "A link", cwd=repo)
    head = git("rev-parse", "HEAD", cwd=repo)
    # A diff driver and a textconv filter in the repository's config never run.
    marker = tmp_path / "ran"
    git("config", "diff.external", f"touch {marker}", cwd=repo)
    (repo / ".gitattributes").write_text("*.css diff=evil\n", encoding="utf-8")
    git("config", "diff.evil.textconv", f"touch {marker} &&", cwd=repo)
    out = tmp_path / "review"
    changed = review.write_change(repo, base, head, out)
    assert not marker.exists()
    by_path = {item["path"]: item for item in changed}
    assert set(by_path) == {"css/site.css", "css/new.css", "js/site.js", "link", "tests/visual/baselines/landing-desktop.png"}
    assert (by_path["css/site.css"]["status"], by_path["css/site.css"]["file"]) == ("M", "files/css/site.css")
    assert (out / "files" / "css" / "site.css").read_text() == "h1 { color: blue; }\n"
    assert (out / "files" / "css" / "new.css").read_text() == "a {}\n"
    assert (out / "files/tests/visual/baselines/landing-desktop.png").read_bytes().endswith(b"head")
    assert by_path["js/site.js"]["file"] is None and "deleted" in by_path["js/site.js"]["note"]
    assert by_path["link"]["file"] is None and "symbolic link" in by_path["link"]["note"]
    assert not (out / "files" / "link").exists() and not (out / "files" / "js").exists()
    diff = (out / "diff.patch").read_bytes().decode(errors="replace")  # the fake PNG has no NUL byte
    assert "-h1 { color: red; }" in diff and "+h1 { color: blue; }" in diff and "deleted file mode" in diff
    log = (out / "log.txt").read_text()
    assert "A link" in log and "Fix" in log and "css/site.css" in log
    assert json.loads((out / "changed.json").read_text()) == changed


def test_write_change_writes_the_base_versions_too(tmp_path: Path) -> None:
    repo, base, head = site_repo(tmp_path, {"css/site.css": "h1 { color: blue; }\n", "css/new.css": "a {}\n", "js/site.js": None})
    out = tmp_path / "review"
    by_path = {item["path"]: item for item in review.write_change(repo, base, head, out)}
    assert by_path["css/site.css"]["base_file"] == "base/css/site.css"
    assert "red" in (out / "base" / "css" / "site.css").read_text()
    assert (out / "files" / "css" / "site.css").read_text() == "h1 { color: blue; }\n"
    assert by_path["js/site.js"]["base_file"] == "base/js/site.js" and (out / "base" / "js" / "site.js").is_file()
    assert by_path["css/new.css"]["base_file"] is None and "added" in by_path["css/new.css"]["base_note"]
    assert not (out / "base" / "css" / "new.css").exists() and not (out / "files" / "js").exists()


HARNESS_PATHS = ["CLAUDE.md", "css/claude.local.md", "js/Agents.MD", ".claude/settings.json", "css/.Claude/x.css", "docs/AGENTS.md/a.txt"]


def test_write_change_never_writes_a_harness_loaded_name(tmp_path: Path) -> None:
    files: dict[str, str | bytes | None] = {path: "x\n" for path in HARNESS_PATHS}
    repo, base, head = site_repo(tmp_path, {**files, "css/site.css": "h1 { color: blue; }\n"})
    out = tmp_path / "review"
    changed = review.write_change(repo, base, head, out)
    by_path = {item["path"]: item for item in changed}
    for path in HARNESS_PATHS:
        item = by_path[path]
        assert item["file"] is None and item["note"] == "not written: harness-loaded name", path
        assert item["base_note"] == "added: not on the base" or "harness" in item["base_note"]
    written = {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()}
    assert not any(part.lower() in ("claude.md", "claude.local.md", "agents.md", ".claude") for w in written for part in w.split("/"))
    assert by_path["css/site.css"]["file"] == "files/css/site.css"


def test_write_change_lists_a_deleted_harness_file_without_writing_its_base(tmp_path: Path) -> None:
    repo = tmp_path / "project"
    git("init", "-q", "-b", "main", str(repo), cwd=tmp_path)
    base = commit(repo, "Base", {"CLAUDE.md": "old\n", "css/a.css": "a\n"})
    head = commit(repo, "Fix", {"CLAUDE.md": None})
    out = tmp_path / "review"
    (item,) = review.write_change(repo, base, head, out)
    assert item["status"] == "D" and item["base_file"] is None and item["base_note"] == "not written: harness-loaded name"
    assert not (out / "base" / "CLAUDE.md").exists()


def test_write_change_records_an_os_error_per_file_and_goes_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, base, head = site_repo(tmp_path, {"css/toolong.css": "a {}\n", "css/site.css": "h1 { color: blue; }\n"})
    real = Path.write_bytes

    def write_bytes(self: Path, data: bytes) -> int:
        if self.name == "toolong.css":
            raise OSError(errno.ENAMETOOLONG, "File name too long")
        return real(self, data)

    monkeypatch.setattr(Path, "write_bytes", write_bytes)
    out = tmp_path / "review"
    by_path = {item["path"]: item for item in review.write_change(repo, base, head, out)}
    assert by_path["css/toolong.css"]["file"] is None
    assert by_path["css/toolong.css"]["note"] == "not written: ENAMETOOLONG"
    assert by_path["css/site.css"]["file"] == "files/css/site.css"
    assert json.loads((out / "changed.json").read_text())


def test_write_change_feeds_git_over_stdin_so_many_paths_cannot_overflow_argv(tmp_path: Path) -> None:
    files: dict[str, str | bytes | None] = {f"css/dir{n % 50}/file-{n:05d}-{'x' * 60}.css": f"a{n} {{}}\n" for n in range(4000)}
    repo, base, head = site_repo(tmp_path, files)
    seen: list[tuple[str, ...]] = []
    real = review._git_bytes

    def spy(repo_: Path, *args: str, **kwargs: Any) -> bytes:
        seen.append(args)
        return real(repo_, *args, **kwargs)

    review._git_bytes, original = spy, review._git_bytes  # type: ignore[assignment]
    try:
        changed = review.write_change(repo, base, head, tmp_path / "review")
    finally:
        review._git_bytes = original  # type: ignore[assignment]
    assert len(changed) == 4000 and all(item["file"] for item in changed)
    assert max(sum(len(a) for a in args) for args in seen) < 1000  # no git call lists the paths
    assert (tmp_path / "review" / "files" / "css" / "dir7" / f"file-00007-{'x' * 60}.css").read_text() == "a7 {}\n"


def test_write_change_does_not_write_a_blob_over_the_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(review, "MAX_REVIEW_BYTES", 20)
    repo, base, head = site_repo(tmp_path, {"css/big.css": "a" * 200, "css/small.css": "a {}\n"})
    by_path = {item["path"]: item for item in review.write_change(repo, base, head, tmp_path / "review")}
    assert by_path["css/big.css"]["file"] is None and by_path["css/big.css"]["note"] == "not written: 200 bytes"
    assert by_path["css/small.css"]["file"] == "files/css/small.css"


def test_prune_harness_names_removes_and_lists_what_the_harness_loads(tmp_path: Path) -> None:
    root = tmp_path / "visual-results"
    for name in ("visual/a/actual.png", "CLAUDE.md", "visual/Agents.md", "visual/.claude/settings.json", "visual/b/claude.local.md"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    (root / "visual" / "AGENTS.MD-link").symlink_to("a")
    skipped = review.prune_harness_names(root)
    assert skipped == ["CLAUDE.md", "visual/.claude", "visual/Agents.md", "visual/b/claude.local.md"]
    assert (root / "visual/a/actual.png").is_file() and (root / "visual" / "AGENTS.MD-link").is_symlink()
    assert not (root / "CLAUDE.md").exists() and not (root / "visual" / ".claude").exists()
    assert review.prune_harness_names(tmp_path / "absent") == []


def test_write_change_skips_unsafe_paths_and_refuses_bad_commits(tmp_path: Path) -> None:
    assert review._unsafe("../x") and review._unsafe("a/.git/config") and review._unsafe("a//b") and review._unsafe("a\\b")
    assert not review._unsafe("css/site.css")
    with pytest.raises(ValueError):
        review.write_change(tmp_path, "HEAD", HEAD, tmp_path / "out")


def test_cli_prepare_writes_the_change_of_the_routed_head(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, base, head = site_repo(tmp_path, {"css/site.css": "h1 { color: blue; }\n"})
    route_file = tmp_path / "route.json"
    route_file.write_text(json.dumps(routed(base=base, head=head).to_json()), encoding="utf-8")
    monkeypatch.chdir(repo)
    visual = tmp_path / "visual-results"
    (visual / ".claude").mkdir(parents=True)
    (visual / ".claude" / "x").write_text("x", encoding="utf-8")
    (visual / "ok.png").write_bytes(b"png")
    assert review.main(["prepare", "--route", str(route_file), "--out", str(tmp_path / "review"), "--visual", str(visual)]) == 0
    assert (tmp_path / "review" / "files" / "css" / "site.css").read_text() == "h1 { color: blue; }\n"
    assert (tmp_path / "review" / "base" / "css" / "site.css").read_text() == "h1 { color: red; }\n"
    assert json.loads((tmp_path / "review" / "visual-skipped.json").read_text()) == [".claude"]
    assert not (visual / ".claude").exists() and (visual / "ok.png").is_file()
    route_file.write_text(json.dumps(routed("ignore", base=base, head=head).to_json()), encoding="utf-8")
    assert review.main(["prepare", "--route", str(route_file), "--out", str(tmp_path / "other")]) == 2


# --- the hand-over after a failed job (R46) -------------------------------------------------------------


def test_report_failure_comments_once_with_the_failed_jobs_and_the_run() -> None:
    gh = github()
    results = {"evidence": "success", "review": "failure", "post": "skipped"}
    assert review.report_failure(gh, routed(), HEAD, results, 4242, CONFIG) is True
    (comment,) = comments_on(gh, 12)
    assert "`review` (failure)" in comment and "`evidence`" not in comment and "`post`" not in comment
    assert f"https://github.com/{REPO}/actions/runs/4242" in comment and HEAD[:7] in comment
    assert "gh workflow run factory-review.yml --ref main -f pr=12" in comment
    # One comment and nothing else: no label, no draft, no dispatch.
    assert [call for call in writes(gh)] == [("POST", f"{API}/issues/12/comments", {"body": comment})]


@pytest.mark.parametrize(
    ("fresh", "results"),
    [
        (routed(head="f" * 40), {"evidence": "failure"}),  # the pull request moved: a later event reviews it
        (routed("ignore"), {"evidence": "failure"}),  # a person took it over, or it closed
        (routed(), {"evidence": "success", "review": "success", "post": "success"}),
        (routed(), {"evidence": "success", "review": "skipped", "post": "skipped"}),
    ],
)
def test_report_failure_says_nothing_when_stale_or_nothing_failed(fresh: Route, results: dict[str, str]) -> None:
    gh = github()
    assert review.report_failure(gh, fresh, HEAD, results, 4242, CONFIG) is False
    assert writes(gh) == []


def test_report_failure_counts_a_cancelled_job_as_stopped() -> None:
    # A job that ran out of its timeout may be reported as cancelled.
    gh = github()
    results = {"evidence": "cancelled", "review": "skipped", "post": "skipped"}
    assert review.report_failure(gh, routed(), HEAD, results, 4242, CONFIG) is True
    (comment,) = comments_on(gh, 12)
    assert "`evidence` (cancelled)" in comment


def test_cli_hand_over_routes_again_and_comments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gh = github()
    monkeypatch.setattr(review, "GitHub", lambda *args, **kwargs: gh)
    monkeypatch.setenv("FACTORY_APP_SLUG", "factory-app")
    event = tmp_path / "event.json"
    event.write_text(json.dumps(run_event()), encoding="utf-8")
    args = ["hand-over", "--event", str(event), "--head", HEAD, "--run-id", "4242",
            "--job", "evidence=failure", "--job", "review=skipped", "--job", "post=skipped"]
    assert review.main(args) == 0
    (comment,) = comments_on(gh, 12)
    assert "`evidence`" in comment
    for bad in (["--job", "evidence=maybe"], ["--job", "deploy=failure"], ["--run-id", "x"], ["--head", "HEAD"]):
        gh.calls.clear()
        changed = list(args)
        flag, value = bad
        changed[changed.index(flag) + 1] = value
        assert review.main(changed) == 2, bad
        assert writes(gh) == []


def test_cli_evidence_script_phase_writes_its_own_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, base, head = site_repo(tmp_path, {"css/site.css": "h1 { color: blue; }\n"})
    route_file = tmp_path / "route.json"
    route_file.write_text(json.dumps(routed(base=base, head=head, issue_body=groups_body([overflow_group()])).to_json()),
                          encoding="utf-8")
    monkeypatch.chdir(repo)
    out = tmp_path / "script"
    assert review.main(["evidence", "--route", str(route_file), "--phase", "script", "--out", str(out)]) == 0
    result = review_evidence.ScriptResult.from_json(json.loads((out / "script.json").read_text()))
    assert (result.ran, result.code, result.head) == (False, 0, head)
