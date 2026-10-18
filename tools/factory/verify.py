"""Verify merged fixes on the live site: close the fixed issues, revert the regressions.

`check` reads GitHub with a read-only token and reruns every `factory:merged` issue's reproduction
on the live site; it writes outcomes.json and the "after" images. `act` holds the App token and
runs no reproduction: it re-reads every issue it touches, closes the fixed ones, and opens a revert
pull request for each regression. outcomes.json comes from a job that ran agent scripts, so `act`
treats it as data: an issue must still be an open `factory:merged` issue whose trusted merge marker
names the same commit, the "after" image must be a PNG inside the outcomes directory, and reasons
are stripped of comment markers before they reach GitHub.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from tools.factory import check_drafts, ci_facts
from tools.factory import evidence as evidence_module
from tools.factory.ci_facts import deploy_stale, deployed_sha, failed_tests
from tools.factory.config import load_config
from tools.factory.dispatch import _only_deploy_stale
from tools.factory.evidence import EvidenceError, redact
from tools.factory.findings import Cell, Group
from tools.factory.github import GitHub, GitHubError, _NoRedirect
from tools.factory.issues import FINGERPRINT_PATTERN, dedup, fingerprints_in, groups_in, script_in

ROOT = Path(__file__).resolve().parents[2]
MERGED = "factory:merged"
REGRESSED = "factory:regressed"
NEEDS_HUMAN = "factory:needs-human"
REVERT_LABELS = ("factory", "factory:revert")
RESULTS = ("fixed", "reproduces", "not-deployed", "error")
SERIOUS = frozenset({"S1", "S2"})
# Checks that are facts about CI, not about a page (probe.NOT_REPRODUCIBLE, without importing the browser).
CI_CHECKS = frozenset({"flaky", "deploy-stale"})
SHARED_PREFIXES = ("css/", "js/", "assets/")
CHECK_WORKFLOW, PAGES_WORKFLOW = ci_facts.WORKFLOWS
IDENTITY = ("factory-revert", "factory@users.noreply.github.com")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_SCRIPT_BYTES = 1_000_000
DOWNLOAD_TIMEOUT_SECONDS = 30
MAX_REASON = 500

NO_MARKER = "no factory:merged marker from a trusted login"
UNTRUSTED = "the issue is not authored by a trusted login; verify it by hand"
SIMULATED = "simulated failed verification (--simulate-failed-verify)"
STALE_CLOSED = (
    "The live site is no longer stale: the deployed commit matches `main` again, "
    "or the last Pages run on `main` did not fail. Closing."
)

_MARKER = re.compile(r"<!-- factory:merged sha=([0-9a-f]{40}) pr=([0-9]+) -->")
_SHA = re.compile(r"[0-9a-f]{40}")
_PATH_PART = re.compile(r"[A-Za-z0-9._-]+")

Result = Literal["fixed", "reproduces", "not-deployed", "error"]
ReproduceFn = Callable[[Any, Group, str], int]
RunScriptFn = Callable[[str, str], int]
FetchScript = Callable[[str], bytes]
TakeAfter = Callable[[Any, Cell, str, Path], None]
ParentSite = Callable[[str], AbstractContextManager[str]]
PublishEvidence = Callable[[Mapping[str, Path]], dict[str, str]]


class GitError(RuntimeError):
    """A git command failed. The message never contains credentials."""


class RevertConflict(GitError):
    """The merge cannot be reverted cleanly on top of main."""


@dataclass(frozen=True)
class Outcome:
    issue: int
    result: Result
    merge_sha: str
    reason: str = ""
    after: Path | None = None  # the live "after" image of a fixed browser issue
    fingerprint: str = ""
    pr: int | None = None


# --- reading issues -------------------------------------------------------------------------


def _labels(item: dict) -> set[str]:
    return {label["name"] for label in item.get("labels", [])}


def _trusted(item: dict, trusted: frozenset[str]) -> bool:
    login = ((item.get("user") or {}).get("login") or "").lower()
    return login in {t.lower() for t in trusted}


def pending(issues: Sequence[dict]) -> list[dict]:
    """Open issues labelled `factory:merged` (pull requests excluded)."""
    return [
        issue
        for issue in issues
        if issue.get("state", "open") == "open" and "pull_request" not in issue and MERGED in _labels(issue)
    ]


def merge_marker(comments: Sequence[dict], trusted: frozenset[str]) -> tuple[str, int] | None:
    """(merge SHA, pull request) from the latest trusted comment carrying a merge marker."""
    for comment in sorted(comments, key=lambda c: c.get("created_at") or "", reverse=True):
        if not _trusted(comment, trusted):
            continue
        match = _MARKER.search(comment.get("body") or "")
        if match:
            return match.group(1), int(match.group(2))
    return None


def is_deployed(gh: Any, merge_sha: str, deployed: str) -> bool:
    """True when the deployed commit contains the merge (compare base merge_sha, head deployed)."""
    data = gh.request("GET", f"{ci_facts._api(gh)}/compare/{merge_sha}...{deployed}")
    return (data or {}).get("status") in ("identical", "ahead")


def pages_changed(files: Sequence[str]) -> set[str]:
    """The pages a set of changed files affects: a page's own HTML, or every page for shared assets."""
    pages = load_config().site.pages
    by_file = {f"{path}index.html": name for name, path in pages.items()}
    changed: set[str] = set()
    for name in files:
        if name in by_file:
            changed.add(by_file[name])
        elif name.startswith(SHARED_PREFIXES):
            changed |= set(pages)
    return changed


def script_url_ok(url: str, repo: str) -> bool:
    """Only plain https://raw.githubusercontent.com/<repo>/<ref>/<path>.py URLs are downloaded."""
    prefix = f"https://raw.githubusercontent.com/{repo}/"
    if not url.startswith(prefix) or not url.endswith(".py"):
        return False
    parts = url[len(prefix) :].split("/")
    return len(parts) >= 2 and all(_PATH_PART.fullmatch(p) and p not in (".", "..") for p in parts)


def download_script(url: str) -> bytes:
    """Fetch a reproduction script without following redirects and without credentials."""
    opener = urllib.request.build_opener(_NoRedirect)
    request = urllib.request.Request(url, headers={"User-Agent": "deweloperuch-mcp-site-factory"})
    with opener.open(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
        data = response.read(MAX_SCRIPT_BYTES + 1)
    if len(data) > MAX_SCRIPT_BYTES:
        raise ValueError(f"the script is larger than {MAX_SCRIPT_BYTES} bytes")
    return data


def load_probe_groups(directory: Path) -> list[Group]:
    """Groups of this run's live probe: every findings.json under `directory` except main and ci."""
    groups: list[Group] = []
    for path in sorted(directory.rglob("findings.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("target") in ("main", "ci"):
            continue
        groups += [Group.from_json(item) for item in data.get("groups", [])]
    return groups


# --- flaky groups (R4) ----------------------------------------------------------------------


def _parse_time(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def runs_since_merge(
    gh: Any, workflow: str, contains: Callable[[str], bool], since: datetime | None = None
) -> list[dict]:
    """Completed runs of `workflow` on main whose commit contains the merge."""
    selected = []
    for run in ci_facts._runs(gh, workflow, branch="main"):
        if run.get("status") != "completed" or run.get("head_branch", "main") != "main":
            continue
        if since is not None and _parse_time(run["created_at"]) < since:
            continue  # created before the merge commit existed: it cannot contain it
        if contains(run["head_sha"]):
            selected.append(run)
    return selected


def failed_again(gh: Any, run: dict, selector: str) -> bool:
    """True when a failed job of the run failed the test id (or is the job, for a job-name selector)."""
    for job in ci_facts._jobs(gh, run["id"]):
        if job.get("conclusion") != "failure":
            continue
        if not selector.startswith("tests/"):
            if job.get("name") == selector:
                return True
            continue
        log = gh.download(f"{ci_facts._api(gh)}/actions/jobs/{job['id']}/logs")
        if selector in failed_tests(log.decode("utf-8", errors="replace")):
            return True
    return False


def verify_flaky(
    gh: Any,
    group: Group,
    merge_sha: str,
    contains: Callable[[str], bool] | None = None,
    since: datetime | None = None,
) -> tuple[Result, str]:
    """Fixed once a Pages run containing the merge succeeded and no run since failed the test again."""
    contains = contains or (lambda head: is_deployed(gh, merge_sha, head))
    pages = runs_since_merge(gh, PAGES_WORKFLOW, contains, since)
    checks = runs_since_merge(gh, CHECK_WORKFLOW, contains, since)
    for run in sorted(pages + checks, key=lambda r: r["created_at"]):
        if run.get("conclusion") == "failure" and failed_again(gh, run, group.selector):
            return "reproduces", f"{group.selector} failed again in {run['html_url']} on {run['head_sha'][:7]}"
    passed = [run for run in pages if run.get("conclusion") == "success"]
    if not passed:
        return "not-deployed", "no Pages run on a commit containing the merge has succeeded yet"
    return "fixed", (
        f"Pages passed in {passed[0]['html_url']} and no Check or Pages run on main "
        f"since the merge failed {group.selector}"
    )


# --- verification (no write token) ----------------------------------------------------------


def take_after_image(playwright: Any, cell: Cell, live_url: str, dest: Path) -> None:
    """A full-page screenshot of the cell on the live site."""
    from tools.factory.probe import Target, _browsers, _page_path, open_cell

    with _browsers(playwright, [cell.engine]) as browsers:
        run = open_cell(browsers[cell.engine], Target("live", live_url), cell, _page_path(cell.page))
        try:
            dest.write_bytes(run.page.screenshot(full_page=True))
        finally:
            run.close()


def _describe(group: Group) -> str:
    return f"{group.check} {group.selector or '(page)'} on {group.page} ({group.fingerprint})"


def _combine(parts: Sequence[tuple[Result, str]]) -> tuple[Result, str]:
    """Any part still present → reproduces; else an error → error; else waiting → not-deployed."""
    for wanted in ("reproduces", "error", "not-deployed"):
        for result, reason in parts:
            if result == wanted:
                return result, reason
    return "fixed", "; ".join(reason for _, reason in parts)


@dataclass
class _Verifier:
    gh: Any
    playwright: Any
    issues: Sequence[dict]
    live_url: str
    simulate: str | None
    trusted: frozenset[str]
    out_dir: Path | None
    probe_groups: Sequence[Group]
    reproduce: ReproduceFn
    run_script: RunScriptFn
    fetch_script: FetchScript
    take_after: TakeAfter
    parent_site: ParentSite
    _deployed: str | None = None
    _contains: dict[tuple[str, str], bool] = field(default_factory=dict)
    _commits: dict[str, dict] = field(default_factory=dict)
    _live: dict[str, int] = field(default_factory=dict)

    @property
    def api(self) -> str:
        return ci_facts._api(self.gh)

    def deployed(self) -> str:
        if self._deployed is None:
            self._deployed = deployed_sha(self.gh)
        return self._deployed

    def contains(self, merge_sha: str, head: str) -> bool:
        key = (merge_sha, head)
        if key not in self._contains:
            self._contains[key] = is_deployed(self.gh, merge_sha, head)
        return self._contains[key]

    def commit(self, sha: str) -> dict:
        if sha not in self._commits:
            self._commits[sha] = self.gh.request("GET", f"{self.api}/commits/{sha}") or {}
        return self._commits[sha]

    def merged_at(self, sha: str) -> datetime | None:
        stamp = ((self.commit(sha).get("commit") or {}).get("committer") or {}).get("date")
        return _parse_time(stamp) if stamp else None

    def verify(self, issue: dict) -> Outcome:
        number = issue["number"]
        comments = self.gh.paginate(f"{self.api}/issues/{number}/comments")
        marker = merge_marker(comments, self.trusted)
        if marker is None:
            return Outcome(number, "error", "", NO_MARKER)
        sha, pr = marker
        if not _trusted(issue, self.trusted):
            return Outcome(number, "error", sha, UNTRUSTED, pr=pr)
        text = issue.get("body") or ""
        fingerprints = sorted(fingerprints_in(text))
        fp = fingerprints[0] if fingerprints else ""

        def outcome(result: Result, reason: str, after: Path | None = None) -> Outcome:
            return Outcome(number, result, sha, reason, after, fp, pr)

        if self.simulate is not None and self.simulate in fingerprints:
            return outcome("reproduces", SIMULATED)
        try:
            if not self.contains(sha, self.deployed()):
                return outcome(
                    "not-deployed",
                    f"the deployed commit {self.deployed()[:7]} does not contain the merge {sha[:7]} yet",
                )
        except GitHubError as error:
            if error.status not in (404, 422):
                raise
            return outcome("error", f"cannot compare {sha[:7]} with the deployed commit: {error.message}")
        try:
            groups = groups_in(text)
        except (ValueError, KeyError, TypeError) as error:
            return outcome("error", f"unreadable groups marker: {error}")
        script = script_in(text)
        parts = [self.group(group, sha) for group in groups]
        if script is not None:
            parts.append(self.script(script, fp or f"issue-{number}"))
        if not parts:
            return outcome("error", "the issue carries no reproduction")
        result, reason = _combine(parts)
        if result == "fixed":
            regression = self.new_regression(sha)
            if regression is not None:
                result, reason = regression
        browser = [g for g in groups if g.check not in CI_CHECKS and g.findings]
        if result != "fixed" or self.out_dir is None or not browser:
            return outcome(result, reason)
        try:
            after = self.after(number, fp, browser[0])
        except _AfterFailed as error:
            return outcome("error", f"fixed, but the after image failed: {error}")
        return outcome(result, reason, after)

    def group(self, group: Group, sha: str) -> tuple[Result, str]:
        if group.check == "flaky":
            return verify_flaky(
                self.gh, group, sha, contains=lambda head: self.contains(sha, head), since=self.merged_at(sha)
            )
        if group.check in CI_CHECKS:
            return "error", f"{group.check} findings are not verified after a merge"
        try:
            code = self.reproduce(self.playwright, group, self.live_url)
        except ValueError as error:
            return "error", f"cannot reproduce {group.fingerprint}: {error}"
        if code == 1:
            return "reproduces", f"{_describe(group)} still reproduces on the live site"
        if code == 0:
            return "fixed", f"{_describe(group)} no longer reproduces on the live site"
        return "error", f"the reproduction of {_describe(group)} failed on the live site"

    def script(self, url: str, name: str) -> tuple[Result, str]:
        repo = ci_facts._repo(self.gh)
        if not script_url_ok(url, repo):
            return "error", f"the reproduction script is not a https://raw.githubusercontent.com/{repo}/ file"
        try:
            source = self.fetch_script(url)
        except (OSError, ValueError, http.client.HTTPException) as error:
            return "error", f"cannot download the reproduction script: {error}"
        relative = f"factory-out/repros/{name}.py"
        path = check_drafts.ROOT / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(source)
        code = self.run_script(relative, self.live_url)
        if code == 1:
            return "reproduces", "the reproduction script still reports the defect on the live site"
        if code == 0:
            return "fixed", "the reproduction script reports NOT REPRODUCED on the live site"
        return "error", "the reproduction script failed (no verdict line, a traceback, or a timeout)"

    def live_code(self, group: Group) -> int:
        if group.fingerprint not in self._live:
            self._live[group.fingerprint] = self.reproduce(self.playwright, group, self.live_url)
        return self._live[group.fingerprint]

    def new_regression(self, sha: str) -> tuple[Result, str] | None:
        """R27: a new S1/S2 finding on a page the merge changed, live but not before the merge."""
        if not self.probe_groups:
            return None
        candidates = [
            g
            for g in self.probe_groups
            if g.severity in SERIOUS
            and g.check not in CI_CHECKS
            and dedup({g.fingerprint}, self.issues, self.trusted).decision == "new"
        ]
        if not candidates:
            return None
        files = [item.get("filename", "") for item in self.commit(sha).get("files") or []]
        pages = pages_changed(files)
        candidates = [g for g in candidates if g.page in pages]
        live: list[Group] = []
        for group in candidates:
            code = self.live_code(group)
            if code == 2:
                return "error", f"cannot tell whether the new finding {_describe(group)} is on the live site"
            if code == 1:
                live.append(group)
        if not live:
            return None
        try:
            with self.parent_site(sha) as parent_url:
                codes = [(group, self.reproduce(self.playwright, group, parent_url)) for group in live]
        except (GitError, OSError, ValueError) as error:
            return "error", f"cannot build the commit before {sha[:7]}: {redact(str(error))}"
        for group, code in codes:
            if code == 0:
                return "reproduces", (
                    f"new {group.severity} finding {_describe(group)} reproduces on the live site "
                    f"but not on {sha[:7]}^, the commit before the merge"
                )
        if any(code == 2 for _, code in codes):
            return "error", f"cannot reproduce the new findings on the commit before {sha[:7]}"
        return None

    def after(self, number: int, fp: str, group: Group) -> Path:
        from playwright.sync_api import Error as PlaywrightError

        from tools.factory.probe import ProbeError

        assert self.out_dir is not None
        dest = self.out_dir / "after" / f"{number}-{fp or 'issue'}.png"
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            self.take_after(self.playwright, group.findings[0].cell, self.live_url, dest)
        except (ProbeError, PlaywrightError, OSError) as error:
            raise _AfterFailed(str(error)) from error
        return dest


class _AfterFailed(Exception):
    pass


@contextmanager
def parent_worktree(repo: Path, sha: str) -> Iterator[Path]:
    """A temporary detached worktree of `<sha>^`, removed afterwards."""
    if not _SHA.fullmatch(sha):
        raise ValueError(f"not a commit SHA: {sha!r}")
    holder = Path(tempfile.mkdtemp(prefix="factory-parent-"))
    tree = holder / "tree"
    try:
        _git(["worktree", "add", "--detach", str(tree), f"{sha}^"], repo)
        try:
            yield tree
        finally:
            _git_run(["worktree", "remove", "--force", str(tree)], repo)
    finally:
        shutil.rmtree(holder, ignore_errors=True)
        _git_run(["worktree", "prune"], repo)


@contextmanager
def serve_parent(sha: str) -> Iterator[str]:
    """The commit before the merge, packaged and served on 127.0.0.1."""
    from tools.factory.probe import local_site

    with parent_worktree(ROOT, sha) as tree, local_site(tree) as url:
        yield url


def verify_all(
    gh: Any,
    playwright: Any,
    issues: Sequence[dict],
    live_url: str,
    simulate: str | None = None,
    *,
    trusted: frozenset[str] | None = None,
    out_dir: Path | None = None,
    probe_groups: Sequence[Group] = (),
    reproduce_fn: ReproduceFn | None = None,
    run_script_fn: RunScriptFn | None = None,
    fetch_script: FetchScript | None = None,
    take_after: TakeAfter | None = None,
    parent_site: ParentSite | None = None,
) -> list[Outcome]:
    """One outcome per pending issue; `issues` is every issue (dedup for new findings needs them all).

    Only GET requests: this runs in the job without a write token.
    """
    if reproduce_fn is None:
        from tools.factory.probe import reproduce as reproduce_fn
    verifier = _Verifier(
        gh=gh,
        playwright=playwright,
        issues=issues,
        live_url=live_url,
        simulate=simulate,
        trusted=trusted if trusted is not None else load_config().trusted(os.environ.get("FACTORY_APP_SLUG")),
        out_dir=out_dir,
        probe_groups=probe_groups,
        reproduce=reproduce_fn,
        run_script=run_script_fn or (lambda script, url: check_drafts.run_script(script, url)),
        fetch_script=fetch_script or download_script,
        take_after=take_after or take_after_image,
        parent_site=parent_site or serve_parent,
    )
    return [verifier.verify(issue) for issue in pending(issues)]


# --- outcomes file --------------------------------------------------------------------------


def write_outcomes(path: Path, outcomes: Sequence[Outcome]) -> None:
    base = path.parent.resolve()
    items = []
    for outcome in outcomes:
        after = None
        if outcome.after is not None:
            after = outcome.after.resolve().relative_to(base).as_posix()
        items.append(
            {
                "issue": outcome.issue,
                "result": outcome.result,
                "merge_sha": outcome.merge_sha,
                "reason": outcome.reason,
                "after": after,
                "fingerprint": outcome.fingerprint,
                "pr": outcome.pr,
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps({"outcomes": items}, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _outcome(data: Any, base: Path) -> Outcome:
    if not isinstance(data, dict):
        raise ValueError("an outcome must be an object")
    issue, result, sha = data.get("issue"), data.get("result"), data.get("merge_sha")
    reason, fp, pr, after = data.get("reason"), data.get("fingerprint"), data.get("pr"), data.get("after")
    if not _integer(issue):
        raise ValueError(f"bad issue number {issue!r}")
    if result not in RESULTS:
        raise ValueError(f"bad result {result!r}")
    if not isinstance(sha, str) or not (_SHA.fullmatch(sha) or (sha == "" and result == "error")):
        raise ValueError(f"bad merge SHA {sha!r}")
    if not isinstance(reason, str):
        raise ValueError("the reason must be a string")
    if not isinstance(fp, str) or (fp and not FINGERPRINT_PATTERN.fullmatch(fp)):
        raise ValueError(f"bad fingerprint {fp!r}")
    if pr is not None and not _integer(pr):
        raise ValueError(f"bad pull request number {pr!r}")
    image = None
    if after is not None:
        if not isinstance(after, str):
            raise ValueError("after must be a path")
        resolved = (base / after).resolve()
        if not resolved.is_relative_to(base.resolve()) or not resolved.is_file():
            raise ValueError(f"after image {after!r} is not a file inside {base}")
        image = base / after
    return Outcome(issue, result, sha, reason, image, fp, pr)


def load_outcomes(path: Path) -> list[Outcome]:
    """Read and validate outcomes.json; it comes from a job that ran agent scripts."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("outcomes"), list):
        raise ValueError(f"{path}: expected {{'outcomes': [...]}}")
    return [_outcome(item, path.parent) for item in data["outcomes"]]


# --- acting (App token, no reproduction) ----------------------------------------------------


def _git_run(args: list[str], cwd: Path, identity: bool = False) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    if identity:  # for this command only; never written to any git config
        env |= {
            "GIT_AUTHOR_NAME": IDENTITY[0],
            "GIT_AUTHOR_EMAIL": IDENTITY[1],
            "GIT_COMMITTER_NAME": IDENTITY[0],
            "GIT_COMMITTER_EMAIL": IDENTITY[1],
        }
    return subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args], cwd=cwd, env=env, capture_output=True, text=True
    )


def _git_error(result: subprocess.CompletedProcess[str], verb: str, remote: str | None) -> GitError:
    detail = result.stderr or result.stdout
    if remote:
        detail = detail.replace(remote, "<remote>")
    return GitError(f"git {verb} failed: {redact(detail).strip()}")


def _git(args: list[str], cwd: Path, remote: str | None = None, identity: bool = False) -> str:
    result = _git_run(args, cwd, identity)
    if result.returncode != 0:
        raise _git_error(result, args[0], remote)
    return result.stdout.strip()


def revert_branch(repo: Path, remote: str, merge_sha: str, branch: str) -> str:
    """Push `branch` = origin/main plus `git revert --no-edit <merge_sha>`; returns the commit title.

    Works in a temporary worktree, so the checkout in `repo` is left as it was.
    """
    if not _SHA.fullmatch(merge_sha):
        raise ValueError(f"not a commit SHA: {merge_sha!r}")
    _git(["fetch", "--no-tags", remote, "+refs/heads/main:refs/remotes/origin/main"], repo, remote)
    holder = Path(tempfile.mkdtemp(prefix="factory-revert-"))
    tree = holder / "tree"
    try:
        _git(["worktree", "add", "--detach", str(tree), "refs/remotes/origin/main"], repo, remote)
        try:
            reverted = _git_run(["revert", "--no-edit", merge_sha], tree, identity=True)
            if reverted.returncode != 0:
                conflicts = _git_run(["diff", "--name-only", "--diff-filter=U"], tree).stdout.split()
                _git_run(["revert", "--abort"], tree)
                if conflicts:
                    raise RevertConflict(f"the revert of {merge_sha[:7]} conflicts in {', '.join(conflicts)}")
                raise _git_error(reverted, "revert", remote)
            title = _git(["log", "-1", "--format=%s"], tree, remote)
            _git(["push", "--force", remote, f"HEAD:refs/heads/{branch}"], tree, remote)
            return title
        finally:
            _git_run(["worktree", "remove", "--force", str(tree)], repo)
    finally:
        shutil.rmtree(holder, ignore_errors=True)
        _git_run(["worktree", "prune"], repo)


def _plain(text: str) -> str:
    """Agent-adjacent text on one line, without anything that could open or close a hidden marker."""
    text = " ".join(text.split())
    while "<!--" in text or "-->" in text:
        text = text.replace("<!--", "").replace("-->", "")
    return text[:MAX_REASON]


def _label_path(api: str, number: int, label: str) -> str:
    return f"{api}/issues/{number}/labels/{urllib.parse.quote(label, safe='')}"


def _remove_label(gh: Any, api: str, number: int, label: str) -> None:
    try:
        gh.request("DELETE", _label_path(api, number, label))
    except GitHubError as error:
        if error.status != 404:
            raise


def _comment(gh: Any, api: str, number: int, text: str) -> None:
    gh.request("POST", f"{api}/issues/{number}/comments", {"body": text})


def _close(gh: Any, api: str, number: int) -> None:
    gh.request("PATCH", f"{api}/issues/{number}", {"state": "closed", "state_reason": "completed"})


def _current(gh: Any, api: str, outcome: Outcome, trusted: frozenset[str]) -> tuple[dict, int] | None:
    """The issue and its pull request when it is still pending with the same trusted merge marker."""
    number = outcome.issue
    issue = gh.request("GET", f"{api}/issues/{number}")
    if not issue or not pending([issue]) or not _trusted(issue, trusted):
        print(f"#{number}: no longer an open {MERGED} issue by a trusted author; skipped")
        return None
    marker = merge_marker(gh.paginate(f"{api}/issues/{number}/comments"), trusted)
    if marker is None or marker[0] != outcome.merge_sha:
        print(f"#{number}: the merge marker does not name {outcome.merge_sha[:7]}; skipped")
        return None
    return issue, marker[1]


def _close_fixed(
    gh: Any, api: str, outcome: Outcome, issue: dict, pr: int, publish_evidence: PublishEvidence, run_id: str
) -> None:
    number = outcome.issue
    lines = [
        "Verified on the live site: the reproduction no longer finds the defect.",
        "",
        f"- Merge: `{outcome.merge_sha[:7]}` (#{pr})",
        f"- Result: {_plain(outcome.reason)}",
    ]
    if outcome.after is not None:
        if not outcome.after.read_bytes().startswith(PNG_SIGNATURE):
            print(f"#{number}: the after image is not a PNG; skipped")
            return
        fingerprints = fingerprints_in(issue.get("body") or "")
        key = outcome.fingerprint if outcome.fingerprint in fingerprints else min(fingerprints, default=str(number))
        path = f"issues/{key}/{run_id}/after.png"
        url = publish_evidence({path: outcome.after})[path]
        lines += ["", f"![after]({url})"]
    _comment(gh, api, number, "\n".join(lines) + "\n")
    _close(gh, api, number)
    _remove_label(gh, api, number, MERGED)
    print(f"#{number}: fixed on the live site, closed")


def _open_revert_pr(gh: Any, api: str, repo_name: str, branch: str) -> int | None:
    owner = repo_name.split("/")[0]
    pulls = gh.paginate(f"{api}/pulls", {"state": "open", "head": f"{owner}:{branch}", "per_page": 100})
    for pull in pulls:
        head = pull.get("head") or {}
        if head.get("ref") == branch and (head.get("repo") or {}).get("full_name") == repo_name:
            return pull["number"]
    return None


def _revert(gh: Any, api: str, repo_name: str, outcome: Outcome, pr: int, repo: Path, remote: str) -> None:
    number, sha = outcome.issue, outcome.merge_sha
    branch = f"revert/{number}"
    reason = _plain(outcome.reason)
    failed = f"Live verification failed for the merge `{sha[:7]}` (#{pr}): {reason}"
    pull = _open_revert_pr(gh, api, repo_name, branch)
    if pull is None:
        try:
            title = revert_branch(repo, remote, sha, branch)
        except RevertConflict as error:
            _comment(gh, api, number, f"{failed}\n\nThe automatic revert did not apply ({error}); revert it by hand.\n")
            gh.request("POST", f"{api}/issues/{number}/labels", {"labels": [REGRESSED, NEEDS_HUMAN]})
            _remove_label(gh, api, number, MERGED)
            print(f"#{number}: regressed; the revert conflicts, left to a person")
            return
        body = (
            f"Refs #{number}\n\n"
            f"Reverts {sha} (#{pr}): the fix did not hold on the live site.\n\n"
            f"Reason: {reason}\n"
        )
        created = gh.request(
            "POST", f"{api}/pulls", {"title": title, "head": branch, "base": "main", "body": body}
        )
        pull = created["number"]
        gh.request("POST", f"{api}/issues/{pull}/labels", {"labels": list(REVERT_LABELS)})
    _comment(
        gh,
        api,
        number,
        f"{failed}\n\nRevert pull request: #{pull}. The factory stops here; a person decides the next step.\n",
    )
    gh.request("POST", f"{api}/issues/{number}/labels", {"labels": [REGRESSED, NEEDS_HUMAN]})
    _remove_label(gh, api, number, MERGED)
    print(f"#{number}: regressed, revert pull request #{pull}")


def _token_remote(repo_name: str) -> str:
    token = os.environ.get("GH_TOKEN", "")
    return f"https://x-access-token:{token}@github.com/{repo_name}.git"


def act(
    outcomes: Sequence[Outcome],
    gh: Any,
    repo: Path,
    publish_evidence: PublishEvidence,
    *,
    remote: str | None = None,
    trusted: frozenset[str] | None = None,
    run_id: str | None = None,
) -> None:
    """Close fixed issues and revert regressions. Never reruns a reproduction."""
    trusted = trusted if trusted is not None else load_config().trusted(os.environ.get("FACTORY_APP_SLUG"))
    run_id = run_id or os.environ.get("GITHUB_RUN_ID") or "local"
    api, repo_name = ci_facts._api(gh), ci_facts._repo(gh)
    for outcome in outcomes:
        if outcome.result == "not-deployed":
            print(f"#{outcome.issue}: not deployed yet ({_plain(outcome.reason)})")
            continue
        if outcome.result == "error":
            print(f"#{outcome.issue}: verification error: {_plain(outcome.reason)}")
            continue
        current = _current(gh, api, outcome, trusted)
        if current is None:
            continue
        issue, pr = current
        if outcome.result == "fixed":
            _close_fixed(gh, api, outcome, issue, pr, publish_evidence, run_id)
        else:
            _revert(gh, api, repo_name, outcome, pr, repo, remote or _token_remote(repo_name))


def close_stale_deploy(gh: Any, stale: Group | None, *, trusted: frozenset[str] | None = None) -> list[int]:
    """Once deploy_stale reports nothing, close the open trusted issues that are only deploy-stale."""
    if stale is not None:
        return []
    trusted = trusted if trusted is not None else load_config().trusted(os.environ.get("FACTORY_APP_SLUG"))
    api = ci_facts._api(gh)
    closed = []
    for issue in gh.paginate(f"{api}/issues", {"state": "open", "per_page": 100}):
        if (
            issue.get("state", "open") != "open"
            or "pull_request" in issue
            or not _trusted(issue, trusted)
            or not _only_deploy_stale(issue)
        ):
            continue
        _comment(gh, api, issue["number"], STALE_CLOSED + "\n")
        _close(gh, api, issue["number"])
        closed.append(issue["number"])
    return closed


# --- command line ---------------------------------------------------------------------------


def _fingerprint_arg(value: str) -> str:
    if not FINGERPRINT_PATTERN.fullmatch(value):
        raise argparse.ArgumentTypeError("must be 12 lowercase hex characters")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tools.factory.verify")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="rerun reproductions on the live site (read-only token)")
    check.add_argument("--out", type=Path, required=True, help="outcomes.json to write")
    check.add_argument("--simulate-failed-verify", type=_fingerprint_arg, metavar="FP")
    check.add_argument("--probe-out", type=Path, help="this run's probe output (new-finding rule)")
    run = commands.add_parser("act", help="close fixed issues, open reverts (App token)")
    run.add_argument("--outcomes", type=Path, required=True, help="outcomes.json from check")
    return parser


@contextmanager
def _without_tokens() -> Iterator[None]:
    """Agent reproduction scripts inherit this process's environment: keep the token out of it."""
    saved = {name: os.environ.pop(name) for name in ("GH_TOKEN", "GITHUB_TOKEN") if name in os.environ}
    try:
        yield
    finally:
        os.environ.update(saved)


def _playwright() -> AbstractContextManager[Any]:
    from playwright.sync_api import sync_playwright

    return sync_playwright()


def _check(args: argparse.Namespace) -> int:
    config = load_config()
    repo = config.site.repo
    trusted = config.trusted(os.environ.get("FACTORY_APP_SLUG"))
    gh = GitHub(repo)
    issues = gh.paginate(f"/repos/{repo}/issues", {"state": "all", "per_page": 100})
    probe_groups = load_probe_groups(args.probe_out) if args.probe_out is not None else []
    outcomes: list[Outcome] = []
    if pending(issues):
        with _without_tokens(), _playwright() as playwright:
            outcomes = verify_all(
                gh,
                playwright,
                issues,
                config.site.live_url,
                args.simulate_failed_verify,
                trusted=trusted,
                out_dir=args.out.parent,
                probe_groups=probe_groups,
            )
    if args.simulate_failed_verify and not any(o.reason == SIMULATED for o in outcomes):
        print(f"warning: no merged issue carries {args.simulate_failed_verify}", file=sys.stderr)
    write_outcomes(args.out, outcomes)
    for outcome in outcomes:
        print(f"#{outcome.issue} {outcome.result}: {outcome.reason}")
    print(f"{len(outcomes)} merged issues verified -> {args.out}")
    return 0


def _act(args: argparse.Namespace) -> int:
    config = load_config()
    repo = config.site.repo
    token = os.environ.get("GH_TOKEN", "")
    outcomes = load_outcomes(args.outcomes)
    gh = GitHub(repo, token or None)

    def publish_evidence(files: Mapping[str, Path]) -> dict[str, str]:
        run_id = os.environ.get("GITHUB_RUN_ID") or "local"
        with tempfile.TemporaryDirectory() as workdir:
            return evidence_module.publish(
                _token_remote(repo),
                files,
                f"Live verification for run {run_id}",
                f"https://raw.githubusercontent.com/{repo}",
                Path(workdir),
            )

    act(outcomes, gh, ROOT, publish_evidence)
    closed = close_stale_deploy(gh, deploy_stale(gh))
    if closed:
        print("closed deploy-stale " + " ".join(f"#{n}" for n in closed))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return _check(args) if args.command == "check" else _act(args)
    except (GitHubError, EvidenceError, GitError, OSError, ValueError) as error:
        print(f"verify failed: {redact(str(error))}", file=sys.stderr)
        return 2
    except Exception as error:  # ProbeError, a browser failure: infrastructure, never a verdict
        print(f"verify failed: {type(error).__name__}: {redact(str(error))}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
