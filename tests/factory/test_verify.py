"""Live verification of merged fixes, reverts and the stale-deploy issue (offline)."""

from __future__ import annotations

import base64
import contextlib
import io
import json
import os
import subprocess
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest
from PIL import Image
from playwright.sync_api import Playwright

from tests.factory.fakes import FakeGitHub
from tools.factory import check_drafts, verify
from tools.factory.ci_facts import deploy_stale
from tools.factory.config import load_config
from tools.factory.findings import Cell, Finding, Group
from tools.factory.github import GitHubError
from tools.factory.issues import SENTINEL
from tools.factory.verify import (
    Outcome,
    act,
    close_stale_deploy,
    is_deployed,
    load_outcomes,
    merge_marker,
    pages_changed,
    parent_worktree,
    pending,
    take_after_image,
    verify_all,
)

REPO = load_config().site.repo
API = f"/repos/{REPO}"
APP = "factory-app[bot]"
TRUSTED = frozenset({"MBratkowski", APP})
MERGE = "a" * 40
DEPLOYED = "b" * 40
PR = 41
LIVE = "https://live.test/"
PARENT = "http://parent.test/"
TEST_ID = "tests/e2e/test_docs.py::test_toc_tracks_anchor_navigation_and_history[chromium-no-preference]"
MERGED_AT = "2026-10-18T10:00:00Z"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 16


# --- builders -------------------------------------------------------------------------------


def browser_group(
    check: str = "overflow", page: str = "landing", selector: str = "div.term", width: int = 375
) -> Group:
    cell = Cell(page, "webkit", width, 900, "default")
    finding = Finding(check, cell, selector, "m", (0, 0, 1, 1))
    return Group(check, page, "default", selector, ("webkit",), (width,), (finding,))


def ci_group(check: str = "flaky", selector: str = TEST_ID) -> Group:
    return Group(check, "ci" if check == "flaky" else "site", "default", selector, (), (), (), note="n")


def body(groups: tuple[Group, ...] = (), fps: tuple[str, ...] | None = None, script: str | None = None) -> str:
    fingerprints = fps if fps is not None else tuple(g.fingerprint for g in groups) or ("f" * 12,)
    encoded = base64.b64encode(json.dumps([g.to_json() for g in groups]).encode()).decode()
    markers = [SENTINEL, *(f"<!-- factory:fp={fp} -->" for fp in fingerprints)]
    markers.append(f"<!-- factory:groups={encoded} -->")
    if script is not None:
        markers.append(f"<!-- factory:script={script} -->")
    return "## Problem\n\nSomething.\n\n" + "\n".join(markers) + "\n"


def issue(
    number: int,
    groups: tuple[Group, ...] = (),
    *,
    labels: tuple[str, ...] = ("factory:found", "factory:merged", "S3"),
    author: str = APP,
    state: str = "open",
    **body_args: Any,
) -> dict[str, Any]:
    return {
        "number": number,
        "state": state,
        "user": {"login": author},
        "labels": [{"name": name} for name in labels],
        "body": body(groups, **body_args),
    }


def marker_comment(
    sha: str = MERGE, pr: int = PR, author: str = APP, created: str = "2026-10-18T10:05:00Z"
) -> dict[str, Any]:
    return {
        "user": {"login": author},
        "created_at": created,
        "body": f"Merged.\n\n<!-- factory:merged sha={sha} pr={pr} -->",
    }


def check_responses(
    *issues: dict[str, Any], status: str = "ahead", merge: str = MERGE, files: tuple[str, ...] = ("css/site.css",)
) -> dict[tuple[str, str], Any]:
    responses: dict[tuple[str, str], Any] = {
        ("GET", f"{API}/deployments"): [{"id": 1, "sha": DEPLOYED}],
        ("GET", f"{API}/deployments/1/statuses"): [{"state": "success"}],
        ("GET", f"{API}/compare/{merge}...{DEPLOYED}"): {"status": status},
        ("GET", f"{API}/commits/{merge}"): {
            "sha": merge,
            "commit": {"committer": {"date": MERGED_AT}},
            "files": [{"filename": name} for name in files],
        },
    }
    for item in issues:
        responses[("GET", f"{API}/issues/{item['number']}/comments")] = [marker_comment(sha=merge)]
    return responses


class Reproducer:
    """A fake probe.reproduce: answers by (fingerprint, url) and records every call."""

    def __init__(self, codes: Mapping[tuple[str, str], int] | None = None, default: int = 0) -> None:
        self.codes = dict(codes or {})
        self.default = default
        self.calls: list[tuple[str, str]] = []

    def __call__(self, playwright: Any, group: Group, url: str) -> int:
        self.calls.append((group.fingerprint, url))
        return self.codes.get((group.fingerprint, url), self.default)


def no_reproduction(playwright: Any, group: Group, url: str) -> int:
    raise AssertionError("no reproduction expected")


def fake_after(playwright: Any, cell: Cell, url: str, dest: Path) -> None:
    dest.write_bytes(PNG)


@contextlib.contextmanager
def fake_parent(sha: str) -> Iterator[str]:
    yield PARENT


def no_parent(sha: str) -> contextlib.AbstractContextManager[str]:
    raise AssertionError("no parent build expected")


def run_verify(gh: FakeGitHub, issues: list[dict], **kwargs: Any) -> list[Outcome]:
    kwargs.setdefault("trusted", TRUSTED)
    kwargs.setdefault("reproduce_fn", no_reproduction)
    kwargs.setdefault("take_after", fake_after)
    kwargs.setdefault("parent_site", no_parent)
    return verify_all(gh, None, issues, LIVE, **kwargs)  # type: ignore[arg-type]


def only_reads(gh: FakeGitHub) -> bool:
    return all(method == "GET" for method, _, _ in gh.calls)


def writes(gh: FakeGitHub) -> list[tuple[str, str, Any]]:
    return [call for call in gh.calls if call[0] != "GET"]


def never_publish(files: Mapping[str, Path]) -> dict[str, str]:
    raise AssertionError("no evidence expected")


def act_responses(*issues: dict[str, Any], sha: str = MERGE) -> dict[tuple[str, str], Any]:
    responses: dict[tuple[str, str], Any] = {}
    for item in issues:
        responses[("GET", f"{API}/issues/{item['number']}")] = item
        responses[("GET", f"{API}/issues/{item['number']}/comments")] = [marker_comment(sha=sha)]
    return responses


# --- pending, marker, deployment ------------------------------------------------------------


def test_pending_selects_open_issues_labelled_merged() -> None:
    issues = [
        issue(1),
        issue(2, state="closed"),
        issue(3, labels=("factory:found",)),
        {**issue(4), "pull_request": {"url": "x"}},
    ]
    assert [i["number"] for i in pending(issues)] == [1]


def test_merge_marker_comes_from_the_latest_trusted_comment() -> None:
    comments = [
        marker_comment("1" * 40, 5, created="2026-10-18T09:00:00Z"),
        marker_comment("2" * 40, 6, author="mbratkowski", created="2026-10-18T10:00:00Z"),
        marker_comment("3" * 40, 7, author="stranger", created="2026-10-18T11:00:00Z"),
        {"user": {"login": APP}, "created_at": "2026-10-18T12:00:00Z", "body": "No marker here."},
    ]
    assert merge_marker(comments, TRUSTED) == ("2" * 40, 6)
    assert merge_marker(comments[2:], TRUSTED) is None
    short = {**marker_comment(), "body": "<!-- factory:merged sha=abc pr=1 -->"}
    assert merge_marker([short], TRUSTED) is None


@pytest.mark.parametrize(
    "status, expected",
    [("identical", True), ("ahead", True), ("behind", False), ("diverged", False)],
)
def test_is_deployed_when_the_deployed_commit_contains_the_merge(status: str, expected: bool) -> None:
    gh = FakeGitHub({("GET", f"{API}/compare/{MERGE}...{DEPLOYED}"): {"status": status}})
    assert is_deployed(gh, MERGE, DEPLOYED) is expected
    assert gh.calls == [("GET", f"{API}/compare/{MERGE}...{DEPLOYED}", None)]


def test_not_deployed_is_skipped(tmp_path: Path) -> None:
    item = issue(1, (browser_group(),))
    gh = FakeGitHub(check_responses(item, status="behind"))
    outcomes = run_verify(gh, [item], out_dir=tmp_path)
    assert [(o.issue, o.result, o.merge_sha) for o in outcomes] == [(1, "not-deployed", MERGE)]
    assert outcomes[0].after is None
    assert only_reads(gh)
    acting = FakeGitHub()
    act(outcomes, acting, tmp_path, never_publish, trusted=TRUSTED, remote="unused")
    assert acting.calls == []


def test_missing_marker_or_untrusted_author_is_an_error() -> None:
    no_marker = issue(1, (browser_group(),))
    stranger = issue(2, (browser_group(),), author="stranger")
    responses = check_responses(stranger)
    responses[("GET", f"{API}/issues/1/comments")] = [marker_comment(author="stranger")]
    gh = FakeGitHub(responses)
    outcomes = run_verify(gh, [no_marker, stranger])
    assert [(o.issue, o.result) for o in outcomes] == [(1, "error"), (2, "error")]
    assert "factory:merged marker" in outcomes[0].reason
    assert "trusted" in outcomes[1].reason
    assert only_reads(gh)


# --- browser groups: fixed and reproduces ---------------------------------------------------


def test_fixed_closes_issue(tmp_path: Path) -> None:
    group = browser_group()
    item = issue(1, (group,))
    gh = FakeGitHub(check_responses(item))
    reproducer = Reproducer({(group.fingerprint, LIVE): 0})
    outcomes = run_verify(gh, [item], reproduce_fn=reproducer, out_dir=tmp_path)
    (outcome,) = outcomes
    assert (outcome.issue, outcome.result, outcome.merge_sha, outcome.pr) == (1, "fixed", MERGE, PR)
    assert outcome.fingerprint == group.fingerprint
    assert outcome.after == tmp_path / "after" / f"1-{group.fingerprint}.png"
    assert outcome.after.read_bytes() == PNG
    assert reproducer.calls == [(group.fingerprint, LIVE)]
    assert only_reads(gh)

    published: list[dict[str, Path]] = []

    def publish(files: Mapping[str, Path]) -> dict[str, str]:
        published.append(dict(files))
        return {path: f"https://raw.example/{path}" for path in files}

    acting = FakeGitHub(act_responses(item))
    act(outcomes, acting, tmp_path, publish, trusted=TRUSTED, run_id="99", remote="unused")
    path = f"issues/{group.fingerprint}/99/after.png"
    assert published == [{path: outcome.after}]
    assert [(m, p) for m, p, _ in writes(acting)] == [
        ("POST", f"{API}/issues/1/comments"),
        ("PATCH", f"{API}/issues/1"),
        ("DELETE", f"{API}/issues/1/labels/factory%3Amerged"),
    ]
    comment = writes(acting)[0][2]["body"]
    assert f"![after](https://raw.example/{path})" in comment
    assert MERGE[:7] in comment and "<!--" not in comment
    assert writes(acting)[1][2] == {"state": "closed", "state_reason": "completed"}


def test_any_group_still_present_reproduces() -> None:
    gone, still = browser_group(selector="div.a"), browser_group(selector="div.b")
    item = issue(1, (gone, still))
    reproducer = Reproducer({(still.fingerprint, LIVE): 1})
    outcomes = run_verify(FakeGitHub(check_responses(item)), [item], reproduce_fn=reproducer)
    assert [(o.result, o.after) for o in outcomes] == [("reproduces", None)]
    assert "div.b" in outcomes[0].reason


def test_a_reproduction_error_is_an_error() -> None:
    group = browser_group()
    item = issue(1, (group,))
    outcomes = run_verify(FakeGitHub(check_responses(item)), [item], reproduce_fn=Reproducer(default=2))
    assert outcomes[0].result == "error"


def test_simulated_failure_forces_reproduces() -> None:
    simulated, other = browser_group(selector="div.sim"), browser_group(selector="div.other")
    first, second = issue(1, (simulated,)), issue(2, (other,))
    responses = check_responses(first, second, status="behind")  # even before the deployment
    other_merge = "c" * 40
    responses[("GET", f"{API}/issues/2/comments")] = [marker_comment(sha=other_merge)]
    responses[("GET", f"{API}/compare/{other_merge}...{DEPLOYED}")] = {"status": "ahead"}
    responses[("GET", f"{API}/commits/{other_merge}")] = {"files": []}
    reproducer = Reproducer()
    gh = FakeGitHub(responses)
    outcomes = run_verify(gh, [first, second], reproduce_fn=reproducer, simulate=simulated.fingerprint)
    assert [(o.issue, o.result, o.merge_sha) for o in outcomes] == [
        (1, "reproduces", MERGE),
        (2, "fixed", other_merge),
    ]
    assert "simulated" in outcomes[0].reason
    assert reproducer.calls == [(other.fingerprint, LIVE)]
    assert only_reads(gh)


# --- reverts --------------------------------------------------------------------------------


def git(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def commit(repo: Path, message: str, files: Mapping[str, str | None]) -> str:
    for name, content in files.items():
        path = repo / name
        if content is None:
            git("rm", "-q", name, cwd=repo)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        git("add", name, cwd=repo)
    git("commit", "-q", "-m", message, cwd=repo)
    return git("rev-parse", "HEAD", cwd=repo)


def history(tmp_path: Path, later: Mapping[str, str | None]) -> tuple[str, Path, str, str]:
    """A bare remote whose main has a base, a squash-merge-like commit and one later commit."""
    bare = tmp_path / "remote.git"
    git("init", "-q", "--bare", "-b", "main", str(bare))
    seed = tmp_path / "seed"
    git("init", "-q", "-b", "main", str(seed))
    commit(seed, "Base", {"css/site.css": "a\nb\nc\n", "js/old.js": "old\n", "README.md": "r\n"})
    merge = commit(
        seed,
        "Keep the terminal inside the viewport (#41)",
        {"css/site.css": "a\nB\nc\n", "css/new.css": "new\n", "js/old.js": None},
    )
    tip = commit(seed, "Later change", later)
    git("push", "-q", str(bare), "main", cwd=seed)
    clone = tmp_path / "clone"
    git("clone", "-q", str(bare), str(clone))
    return f"file://{bare}", clone, merge, tip


def test_reproduces_opens_revert(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    global_config = tmp_path / "global.gitconfig"
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    remote, clone, merge, tip = history(tmp_path, {"README.md": "r2\n"})
    head_before = git("rev-parse", "HEAD", cwd=clone)
    group = browser_group()
    item = issue(5, (group,))
    outcome = Outcome(5, "reproduces", merge, "overflow div.term still reproduces", None, group.fingerprint, PR)
    responses = act_responses(item, sha=merge)
    responses[("GET", f"{API}/pulls")] = []
    responses[("POST", f"{API}/pulls")] = {"number": 77}
    gh = FakeGitHub(responses)
    act([outcome], gh, clone, never_publish, trusted=TRUSTED, remote=remote)

    bare = remote.removeprefix("file://")
    revert = git("rev-parse", "refs/heads/revert/5", cwd=Path(bare))
    assert git("rev-parse", f"{revert}^", cwd=Path(bare)) == tip
    # the revert commit is exactly the inverse of the merge
    assert git("diff", tip, revert, cwd=Path(bare)) == git("diff", merge, f"{merge}^", cwd=Path(bare))
    subject = git("log", "-1", "--format=%s", revert, cwd=Path(bare))
    assert subject == 'Revert "Keep the terminal inside the viewport (#41)"'

    calls = writes(gh)
    pull = next(b for m, p, b in calls if (m, p) == ("POST", f"{API}/pulls"))
    assert pull["head"] == "revert/5" and pull["base"] == "main" and pull["title"] == subject
    assert pull["body"].startswith("Refs #5\n") and "overflow div.term still reproduces" in pull["body"]
    assert merge in pull["body"] and "Fixes" not in pull["body"] and "<!--" not in pull["body"]
    assert ("POST", f"{API}/issues/77/labels", {"labels": ["factory", "factory:revert"]}) in calls
    assert ("POST", f"{API}/issues/5/labels", {"labels": ["factory:regressed", "factory:needs-human"]}) in calls
    assert ("DELETE", f"{API}/issues/5/labels/factory%3Amerged", None) in calls
    comment = next(b for m, p, b in calls if p == f"{API}/issues/5/comments")
    assert "#77" in comment["body"]
    assert not any(p == f"{API}/issues/5" and m == "PATCH" for m, p, _ in calls)
    # the checkout is untouched and no worktree is left behind
    assert git("rev-parse", "HEAD", cwd=clone) == head_before
    assert len(git("worktree", "list", cwd=clone).splitlines()) == 1
    # the committer identity is set for the command only, never in the global or local config
    assert not global_config.exists()
    assert subprocess.run(["git", "config", "--local", "user.name"], cwd=clone, capture_output=True).returncode == 1
    assert git("log", "-1", "--format=%cn <%ce>", revert, cwd=Path(bare)) == "factory-revert <factory@users.noreply.github.com>"


def test_existing_revert_pull_request_is_reused(tmp_path: Path) -> None:
    group = browser_group()
    item = issue(5, (group,))
    outcome = Outcome(5, "reproduces", MERGE, "still there", None, group.fingerprint, PR)
    responses = act_responses(item)
    responses[("GET", f"{API}/pulls")] = [
        {"number": 70, "head": {"ref": "revert/5", "repo": {"full_name": "stranger/fork"}}},
        {"number": 77, "head": {"ref": "revert/5", "repo": {"full_name": REPO}}},
    ]
    gh = FakeGitHub(responses)
    act([outcome], gh, tmp_path / "no-repo", never_publish, trusted=TRUSTED, remote="unused")
    paths = [(m, p) for m, p, _ in writes(gh)]
    assert ("POST", f"{API}/pulls") not in paths
    assert ("POST", f"{API}/issues/5/labels") in paths


def test_revert_conflict_goes_to_needs_human(tmp_path: Path) -> None:
    remote, clone, merge, _ = history(tmp_path, {"css/site.css": "a\nB2\nc\n"})
    group = browser_group()
    item = issue(5, (group,))
    outcome = Outcome(5, "reproduces", merge, "still there", None, group.fingerprint, PR)
    responses = act_responses(item, sha=merge)
    responses[("GET", f"{API}/pulls")] = []
    gh = FakeGitHub(responses)
    act([outcome], gh, clone, never_publish, trusted=TRUSTED, remote=remote)
    calls = writes(gh)
    assert ("POST", f"{API}/pulls") not in [(m, p) for m, p, _ in calls]
    assert ("POST", f"{API}/issues/5/labels", {"labels": ["factory:regressed", "factory:needs-human"]}) in calls
    assert ("DELETE", f"{API}/issues/5/labels/factory%3Amerged", None) in calls
    comment = next(b for m, p, b in calls if p == f"{API}/issues/5/comments")
    assert "by hand" in comment["body"]
    bare = Path(remote.removeprefix("file://"))
    assert git("branch", "--list", "revert/5", cwd=bare) == ""
    assert len(git("worktree", "list", cwd=clone).splitlines()) == 1


@pytest.mark.parametrize("change", ["closed", "unlabelled", "other-sha", "stranger"])
def test_act_ignores_outcomes_that_no_longer_match(tmp_path: Path, change: str) -> None:
    group = browser_group()
    item = issue(1, (group,))
    responses = act_responses(item)
    if change == "closed":
        responses[("GET", f"{API}/issues/1")] = {**item, "state": "closed"}
    elif change == "unlabelled":
        responses[("GET", f"{API}/issues/1")] = {**item, "labels": [{"name": "factory:found"}]}
    elif change == "other-sha":
        responses[("GET", f"{API}/issues/1/comments")] = [marker_comment(sha="c" * 40)]
    else:
        responses[("GET", f"{API}/issues/1")] = {**item, "user": {"login": "stranger"}}
    after = tmp_path / "after.png"
    after.write_bytes(PNG)
    outcomes = [
        Outcome(1, "fixed", MERGE, "gone", after, group.fingerprint, PR),
        Outcome(1, "reproduces", MERGE, "still", None, group.fingerprint, PR),
    ]
    gh = FakeGitHub(responses)
    act(outcomes, gh, tmp_path, never_publish, trusted=TRUSTED, remote="unused")
    assert writes(gh) == []


def test_act_refuses_an_after_image_that_is_not_a_png(tmp_path: Path) -> None:
    group = browser_group()
    item = issue(1, (group,))
    after = tmp_path / "after.png"
    after.write_bytes(b"#!/bin/sh\n")
    gh = FakeGitHub(act_responses(item))
    act([Outcome(1, "fixed", MERGE, "gone", after, group.fingerprint, PR)], gh, tmp_path, never_publish,
        trusted=TRUSTED, remote="unused")
    assert writes(gh) == []


def test_act_runs_no_reproduction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.factory import probe

    def forbidden(*args: Any, **kwargs: Any) -> int:
        raise AssertionError("act must not reproduce")

    monkeypatch.setattr(probe, "reproduce", forbidden)
    monkeypatch.setattr(check_drafts, "run_script", forbidden)
    group = browser_group()
    item = issue(1, (group,))
    gh = FakeGitHub(act_responses(item))
    act([Outcome(1, "fixed", MERGE, "gone", None, group.fingerprint, PR)], gh, tmp_path, never_publish,
        trusted=TRUSTED, remote="unused")
    assert [(m, p) for m, p, _ in writes(gh)][1] == ("PATCH", f"{API}/issues/1")


# --- pages changed and new findings (R27) ---------------------------------------------------


def test_pages_changed() -> None:
    both = {"landing", "docs"}
    assert pages_changed(["index.html"]) == {"landing"}
    assert pages_changed(["docs/index.html"]) == {"docs"}
    assert pages_changed(["css/site.css"]) == both
    assert pages_changed(["js/site.js"]) == both
    assert pages_changed(["assets/images.json"]) == both
    assert pages_changed(["index.html", "docs/index.html"]) == both
    assert pages_changed(["tests/e2e/test_x.py", "tools/templates/x.html", "README.md"]) == set()
    assert pages_changed(["cssx/a.css", "docs/other.html", "docs/index.html.bak"]) == set()
    assert pages_changed([]) == set()


NEW = browser_group("console", "landing", "body", width=1440)  # S1


def regression_case(change: str | None) -> tuple[list[dict], dict, Reproducer, list[Group]]:
    own = browser_group()
    item = issue(1, (own,))
    new = browser_group("overflow", "landing", "pre.wide", 1440) if change == "severity" else NEW
    files = ("tests/e2e/test_x.py",) if change == "page" else ("css/site.css",)
    responses = check_responses(item, files=files)
    issues = [item]
    if change == "known":
        issues.append(issue(9, (new,), labels=("factory:found", "S1")))
    codes = {
        (own.fingerprint, LIVE): 0,
        (new.fingerprint, LIVE): 0 if change == "live" else 1,
        (new.fingerprint, PARENT): 1 if change == "parent" else 0,
    }
    return issues, responses, Reproducer(codes), [new]


def test_new_serious_finding_on_a_changed_page_regresses(tmp_path: Path) -> None:
    issues, responses, reproducer, probe_groups = regression_case(None)
    builds: list[str] = []

    @contextlib.contextmanager
    def parent(sha: str) -> Iterator[str]:
        builds.append(sha)
        yield PARENT

    gh = FakeGitHub(responses)
    outcomes = run_verify(
        gh, issues, reproduce_fn=reproducer, probe_groups=probe_groups, parent_site=parent, out_dir=tmp_path
    )
    assert [(o.issue, o.result, o.after) for o in outcomes] == [(1, "reproduces", None)]
    assert NEW.fingerprint in outcomes[0].reason and "S1" in outcomes[0].reason
    assert builds == [MERGE]
    assert (NEW.fingerprint, PARENT) in reproducer.calls
    assert only_reads(gh)


@pytest.mark.parametrize("change", ["known", "page", "severity", "live", "parent"])
def test_new_finding_rule_needs_every_condition(change: str, tmp_path: Path) -> None:
    issues, responses, reproducer, probe_groups = regression_case(change)
    outcomes = run_verify(
        FakeGitHub(responses),
        issues,
        reproduce_fn=reproducer,
        probe_groups=probe_groups,
        parent_site=fake_parent,
        out_dir=tmp_path,
    )
    assert [(o.issue, o.result) for o in outcomes] == [(1, "fixed")]
    if change in ("known", "page", "severity"):
        assert all(fp != probe_groups[0].fingerprint for fp, _ in reproducer.calls)


def test_new_finding_rule_is_skipped_without_probe_output() -> None:
    own = browser_group()
    item = issue(1, (own,))
    gh = FakeGitHub(check_responses(item))
    run_verify(gh, [item], reproduce_fn=Reproducer({(own.fingerprint, LIVE): 0}))
    assert ("GET", f"{API}/commits/{MERGE}", None) not in gh.calls


def test_parent_worktree_checks_out_the_commit_before_the_merge(tmp_path: Path) -> None:
    _, clone, merge, tip = history(tmp_path, {"README.md": "r2\n"})
    with parent_worktree(clone, merge) as tree:
        assert (tree / "css" / "site.css").read_text() == "a\nb\nc\n"
        assert (tree / "js" / "old.js").is_file() and not (tree / "css" / "new.css").exists()
        assert git("rev-parse", "HEAD", cwd=tree) == git("rev-parse", f"{merge}^", cwd=clone)
    assert not tree.exists()
    assert len(git("worktree", "list", cwd=clone).splitlines()) == 1
    with pytest.raises(ValueError):
        with parent_worktree(clone, "--upload-pack=x"):
            pass


# --- flaky groups ---------------------------------------------------------------------------


def run(run_id: int, sha: str, conclusion: str, created: str, branch: str = "main") -> dict[str, Any]:
    return {
        "id": run_id,
        "head_sha": sha,
        "head_branch": branch,
        "status": "completed",
        "conclusion": conclusion,
        "created_at": created,
        "html_url": f"https://github.com/{REPO}/actions/runs/{run_id}",
    }


def flaky_responses(item: dict, failing_log: bytes) -> dict[tuple[str, str], Any]:
    later, older = "d" * 40, "e" * 40
    responses = check_responses(item, files=("tests/e2e/test_docs.py", "js/site.js"))
    responses[("GET", f"{API}/actions/workflows/pages.yml/runs")] = {
        "workflow_runs": [
            run(1, older, "failure", "2026-10-17T10:00:00Z"),  # before the merge: ignored
            run(2, MERGE, "success", "2026-10-18T10:01:00Z"),
        ]
    }
    responses[("GET", f"{API}/actions/workflows/check.yml/runs")] = {
        "workflow_runs": [
            run(3, later, "failure", "2026-10-18T11:00:00Z"),
            run(4, older, "failure", "2026-10-18T12:00:00Z"),  # an old commit: ignored
            run(5, later, "failure", "2026-10-18T12:30:00Z", branch="fix/9-x"),  # not main: ignored
        ]
    }
    responses[("GET", f"{API}/compare/{MERGE}...{MERGE}")] = {"status": "identical"}
    responses[("GET", f"{API}/compare/{MERGE}...{later}")] = {"status": "ahead"}
    responses[("GET", f"{API}/compare/{MERGE}...{older}")] = {"status": "behind"}
    for run_id in (1, 3, 4, 5):
        responses[("GET", f"{API}/actions/runs/{run_id}/jobs")] = {
            "jobs": [
                {"id": run_id * 10, "name": "Validate this commit / Tests (chromium)", "conclusion": "failure"},
                {"id": run_id * 10 + 1, "name": "Validate this commit / Links", "conclusion": "success"},
            ]
        }
    flaky_line = f"2026-10-18T11:01:00Z FAILED {TEST_ID} - AssertionError\n".encode()
    for run_id in (1, 4, 5):  # runs that must be ignored still fail the test
        responses[("GET", f"{API}/actions/jobs/{run_id * 10}/logs")] = flaky_line
    responses[("GET", f"{API}/actions/jobs/30/logs")] = failing_log
    return responses


def test_flaky_fixed_after_clean_runs() -> None:
    item = issue(1, (ci_group(),), labels=("factory:found", "factory:flaky", "factory:merged", "S1"))
    other = b"2026-10-18T11:01:00Z FAILED tests/e2e/test_pages.py::test_other[chromium] - boom\n"
    gh = FakeGitHub(flaky_responses(item, other))
    outcomes = run_verify(gh, [item], out_dir=Path("/nonexistent"))
    assert [(o.result, o.after) for o in outcomes] == [("fixed", None)]
    assert "actions/runs/2" in outcomes[0].reason
    assert ("GET", f"{API}/actions/jobs/40/logs", None) not in gh.calls
    assert only_reads(gh)


def test_flaky_reproduces_when_test_failed_again() -> None:
    item = issue(1, (ci_group(),), labels=("factory:found", "factory:flaky", "factory:merged", "S1"))
    failed = f"2026-10-18T11:01:00Z FAILED {TEST_ID} - AssertionError\n".encode()
    outcomes = run_verify(FakeGitHub(flaky_responses(item, failed)), [item])
    assert outcomes[0].result == "reproduces"
    assert "actions/runs/3" in outcomes[0].reason and TEST_ID in outcomes[0].reason


def test_flaky_job_selector_reproduces_when_the_job_failed_again() -> None:
    selector = "Validate this commit / Tests (chromium)"
    item = issue(1, (ci_group(selector=selector),))
    outcomes = run_verify(FakeGitHub(flaky_responses(item, b"no test ids\n")), [item])
    assert outcomes[0].result == "reproduces"
    assert "actions/runs/3" in outcomes[0].reason


def test_flaky_waits_for_a_successful_pages_run() -> None:
    item = issue(1, (ci_group(),))
    responses = flaky_responses(item, b"clean\n")
    responses[("GET", f"{API}/actions/workflows/pages.yml/runs")] = {"workflow_runs": []}
    outcomes = run_verify(FakeGitHub(responses), [item])
    assert outcomes[0].result == "not-deployed"


# --- script reproductions -------------------------------------------------------------------

SCRIPT_URL = f"https://raw.githubusercontent.com/{REPO}/{'c' * 40}/repros/{'f' * 12}.py"
FOUND = b"import sys\nprint('REPRODUCED: 5px past the edge')\nsys.exit(1)\n"
GONE = b"print('NOT REPRODUCED')\n"


@pytest.mark.parametrize("source, result", [(FOUND, "reproduces"), (GONE, "fixed")], ids=["found", "gone"])
def test_script_reproduction_runs_the_downloaded_script(
    source: bytes, result: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(check_drafts, "ROOT", tmp_path)
    item = issue(1, (), fps=("f" * 12,), script=SCRIPT_URL)
    fetched: list[str] = []

    def fetch(url: str) -> bytes:
        fetched.append(url)
        return source

    outcomes = run_verify(FakeGitHub(check_responses(item)), [item], fetch_script=fetch, out_dir=tmp_path)
    assert fetched == [SCRIPT_URL]
    assert (tmp_path / "factory-out" / "repros" / f"{'f' * 12}.py").read_bytes() == source
    assert [(o.result, o.after) for o in outcomes] == [(result, None)]


@pytest.mark.parametrize(
    "url",
    [
        f"https://raw.githubusercontent.com/stranger/{REPO.split('/')[1]}/{'c' * 40}/repros/x.py",
        f"http://raw.githubusercontent.com/{REPO}/{'c' * 40}/repros/x.py",
        f"https://raw.githubusercontent.com/{REPO}/{'c' * 40}/../../other/repo/x.py",
        f"https://raw.githubusercontent.com/{REPO}/{'c' * 40}/repros/x.py?token=1",
        f"https://raw.githubusercontent.com/{REPO}/{'c' * 40}/repros/x.sh",
        f"https://github.com/{REPO}/raw/{'c' * 40}/repros/x.py",
        f"https://raw.githubusercontent.com/{REPO}.evil/{'c' * 40}/repros/x.py",
    ],
)
def test_script_url_outside_the_repository_is_an_error(url: str) -> None:
    item = issue(1, (), fps=("f" * 12,), script=url)

    def fetch(url: str) -> bytes:
        raise AssertionError("no download expected")

    def scripts(script: str, url: str) -> int:
        raise AssertionError("no script expected")

    outcomes = run_verify(FakeGitHub(check_responses(item)), [item], fetch_script=fetch, run_script_fn=scripts)
    assert outcomes[0].result == "error" and "raw.githubusercontent.com" in outcomes[0].reason


# --- the stale-deploy issue -----------------------------------------------------------------


def test_stale_deploy_issue_closes_when_shas_match() -> None:
    stale = issue(10, (ci_group("deploy-stale", ""),), labels=("factory:found", "S1"))
    mixed = issue(11, (ci_group("deploy-stale", ""), browser_group()), labels=("factory:found",))
    other = issue(12, (browser_group(),), labels=("factory:found",))
    forged = issue(13, (ci_group("deploy-stale", ""),), author="stranger", labels=())
    responses = {
        ("GET", f"{API}/deployments"): [{"id": 1, "sha": DEPLOYED}],
        ("GET", f"{API}/deployments/1/statuses"): [{"state": "success"}],
        ("GET", f"{API}/commits/main"): {"sha": DEPLOYED},
        ("GET", f"{API}/issues"): [stale, mixed, other, forged],
    }
    gh = FakeGitHub(responses)
    assert close_stale_deploy(gh, deploy_stale(gh), trusted=TRUSTED) == [10]
    assert [(m, p) for m, p, _ in writes(gh)] == [
        ("POST", f"{API}/issues/10/comments"),
        ("PATCH", f"{API}/issues/10"),
    ]
    assert writes(gh)[1][2] == {"state": "closed", "state_reason": "completed"}

    still = FakeGitHub(responses)
    assert close_stale_deploy(still, ci_group("deploy-stale", ""), trusted=TRUSTED) == []
    assert still.calls == []


# --- outcomes file and command line ---------------------------------------------------------


def test_outcomes_round_trip_and_validation(tmp_path: Path) -> None:
    after = tmp_path / "after" / "1-x.png"
    after.parent.mkdir()
    after.write_bytes(PNG)
    outcomes = [
        Outcome(1, "fixed", MERGE, "gone", after, "f" * 12, PR),
        Outcome(2, "error", "", "no marker"),
    ]
    path = tmp_path / "outcomes.json"
    verify.write_outcomes(path, outcomes)
    data = json.loads(path.read_text())
    assert data["outcomes"][0]["after"] == "after/1-x.png"
    assert load_outcomes(path) == outcomes

    def broken(**change: Any) -> Path:
        item = {**data["outcomes"][0], **change}
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps({"outcomes": [item]}))
        return bad

    outside = tmp_path.parent / "outside.png"
    outside.write_bytes(PNG)
    for change in (
        {"after": "../outside.png"},
        {"after": str(outside)},
        {"after": "after/missing.png"},
        {"result": "merged"},
        {"merge_sha": "abc"},
        {"issue": "1"},
        {"fingerprint": "zz"},
    ):
        with pytest.raises(ValueError):
            load_outcomes(broken(**change))


def test_cli_check_writes_outcomes_without_a_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gh = FakeGitHub({("GET", f"{API}/issues"): [issue(1, labels=("factory:found",))]})
    monkeypatch.setattr(verify, "GitHub", lambda repo, *a, **k: gh)

    def no_browser() -> Any:
        raise AssertionError("no browser expected without merged issues")

    monkeypatch.setattr(verify, "_playwright", no_browser)
    out = tmp_path / "verify" / "outcomes.json"
    assert verify.main(["check", "--out", str(out)]) == 0
    assert json.loads(out.read_text())["outcomes"] == []
    assert only_reads(gh)


def test_cli_check_passes_simulate_and_probe_groups(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    item = issue(1, (browser_group(),))
    gh = FakeGitHub({("GET", f"{API}/issues"): [item]})
    monkeypatch.setattr(verify, "GitHub", lambda repo, *a, **k: gh)
    monkeypatch.setattr(verify, "_playwright", contextlib.nullcontext)
    probe_out = tmp_path / "probe"
    for name, target in (("live", LIVE), ("main", "main"), ("ci", "ci")):
        (probe_out / name).mkdir(parents=True)
        (probe_out / name / "findings.json").write_text(
            json.dumps({"target": target, "groups": [browser_group(selector=f"div.{name}").to_json()]})
        )
    seen: dict[str, Any] = {}

    def fake_verify_all(gh: Any, playwright: Any, issues: Any, live_url: str, simulate: Any = None, **kw: Any):
        seen.update(issues=issues, live_url=live_url, simulate=simulate, **kw)
        # agent reproduction scripts run in this process's environment: no token may be there
        seen["tokens"] = [os.environ.get(name) for name in ("GH_TOKEN", "GITHUB_TOKEN")]
        return [Outcome(1, "reproduces", MERGE, "simulated", None, "f" * 12, PR)]

    monkeypatch.setattr(verify, "verify_all", fake_verify_all)
    monkeypatch.setenv("GH_TOKEN", "secret-token")
    monkeypatch.setenv("GITHUB_TOKEN", "secret-token")
    out = tmp_path / "outcomes.json"
    args = ["check", "--out", str(out), "--simulate-failed-verify", "f" * 12, "--probe-out", str(probe_out)]
    assert verify.main(args) == 0
    assert seen["simulate"] == "f" * 12 and seen["issues"] == [item]
    assert seen["live_url"] == load_config().site.live_url
    assert [g.selector for g in seen["probe_groups"]] == ["div.live"]
    assert seen["out_dir"] == tmp_path
    assert seen["tokens"] == [None, None]
    assert os.environ["GH_TOKEN"] == "secret-token"  # restored once the reproductions are done
    assert load_outcomes(out)[0].result == "reproduces"
    with pytest.raises(SystemExit):
        verify.main(["check", "--out", str(out), "--simulate-failed-verify", "not-a-fp"])


def test_cli_exits_2_with_a_redacted_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    error = GitHubError(401, "rejected https://x-access-token:secret-token@github.com/x.git")
    monkeypatch.setattr(verify, "GitHub", lambda repo, *a, **k: FakeGitHub({("GET", f"{API}/issues"): error}))
    assert verify.main(["check", "--out", str(tmp_path / "o.json")]) == 2
    err = capsys.readouterr().err
    assert "secret-token" not in err and "verify failed" in err
    assert not (tmp_path / "o.json").exists()

    outcomes = tmp_path / "outcomes.json"
    verify.write_outcomes(outcomes, [Outcome(1, "not-deployed", MERGE, "later")])
    stale_error = FakeGitHub({("GET", f"{API}/deployments"): error})
    monkeypatch.setattr(verify, "GitHub", lambda repo, *a, **k: stale_error)
    assert verify.main(["act", "--outcomes", str(outcomes)]) == 2
    assert "secret-token" not in capsys.readouterr().err


def test_cli_act_closes_the_stale_deploy_issue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stale = issue(10, (ci_group("deploy-stale", ""),), labels=("factory:found", "S1"), author="MBratkowski")
    gh = FakeGitHub(
        {
            ("GET", f"{API}/deployments"): [{"id": 1, "sha": DEPLOYED}],
            ("GET", f"{API}/deployments/1/statuses"): [{"state": "success"}],
            ("GET", f"{API}/commits/main"): {"sha": DEPLOYED},
            ("GET", f"{API}/issues"): [stale],
        }
    )
    monkeypatch.setattr(verify, "GitHub", lambda repo, *a, **k: gh)
    outcomes = tmp_path / "outcomes.json"
    verify.write_outcomes(outcomes, [Outcome(1, "not-deployed", MERGE, "later"), Outcome(2, "error", "", "x")])
    assert verify.main(["act", "--outcomes", str(outcomes)]) == 0
    assert [(m, p) for m, p, _ in writes(gh)] == [
        ("POST", f"{API}/issues/10/comments"),
        ("PATCH", f"{API}/issues/10"),
    ]
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"outcomes": [{"issue": 1, "result": "fixed", "merge_sha": MERGE,
                                             "reason": "", "after": "../x.png", "fingerprint": "", "pr": 1}]}))
    assert verify.main(["act", "--outcomes", str(bad)]) == 2


# --- the after image in a real browser ------------------------------------------------------


def test_after_image_is_a_full_page_screenshot(playwright: Playwright, fixtures_url: str, tmp_path: Path) -> None:
    cell = Cell("canary/overflow.html", "chromium", 375, 900, "default")
    dest = tmp_path / "after.png"
    take_after_image(playwright, cell, fixtures_url, dest)
    data = dest.read_bytes()
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = Image.open(io.BytesIO(data)).size
    assert width >= 375 and height >= 900
