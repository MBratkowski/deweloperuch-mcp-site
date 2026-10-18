"""Publish one fixer attempt with the App token: fence, secret checks, push, pull request, evidence.

Runs in factory-fix.yml's publish job, a fresh checkout of main that executes nothing from the fix.
The agent's commits arrive as a git bundle and are never pushed: they are fetched into a private
ref, and this module builds one commit of its own from the fix's tree (`git commit-tree`, on the
base or on the pull request's current head), with the factory as author and committer and a message
rendered from the validated report. Only that commit is fenced, scanned (every blob that differs
from its parent, text and binary, and its message) and pushed, so nothing the agent wrote into
intermediate commits, author fields or messages leaves the job. The report, the proof and the
images come from the fix job, which ran the agent's code, so they are data: the report is validated
again, proof output is neutralised, images must be PNG files, and everything is scanned for secret
shapes before anything reaches GitHub. Hidden markers are rendered by this module after the sentinel
line; agent text can never contain one, close an issue or mention anyone.

Outcomes, each with a comment carrying `<!-- factory:attempt=K -->` on the issue:

- the fixer reports `not_reproducible` or `needs_human`, its new test passes on the base, anything
  carries secret-shaped text (here or in the fix job's own scan), or a flaky test never fails on
  the base: `factory:needs-human`, and an open pull request goes back to draft with a summary;
- the fix job failed, no valid report or change, a hard fence reason or a proof that is not green:
  the next attempt is dispatched, or after the last attempt the same as above;
- otherwise the commit is pushed and the pull request opened or updated, labelled `factory` (and
  `factory:needs-human` when the fence found review reasons, listed under "Needs a human"); the
  issue moves from `factory:ready` to `factory:fixing`. If a step after the push fails, the attempt
  is still recorded on the issue before the error surfaces.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import urllib.parse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tools.factory import evidence as evidence_module
from tools.factory.config import Config, load_config
from tools.factory.evidence import EvidenceError, redact
from tools.factory.fence import FenceReport, GitError, check_fence
from tools.factory.fix import (
    BASELINE_DIR,
    CLOSING_REFERENCE,
    FIXING,
    FLAKY,
    FLAKY_RUNS,
    FLAKY_THROTTLE,
    ISSUE_URL,
    NEEDS_HUMAN,
    READY,
    SUITE_COMMAND,
    FixError,
    FixReport,
    Proof,
    _git_bytes,
    branch_pattern,
    check_test_id,
    check_text,
    find_pull,
    git,
    is_ancestor,
    read_report,
)
from tools.factory.github import GitHub, GitHubError
from tools.factory.issues import SENTINEL, DraftError, _fenced, _marker_block, groups_in
from tools.factory.secret_scan import secret_shape

PR_LABEL = "factory"
WORKFLOW = "factory-fix.yml"
HEAD_REF = "refs/factory/fix-head"
PR_HEAD_REF = "refs/factory/pr-head"
IDENTITY = ("deweloperuch-factory", "factory@users.noreply.github.com")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_IMAGE_BYTES = 5_000_000
MAX_REASON = 500
MAX_TITLE = 256
MAX_SUBJECT = 72
LOG_LINES = 20
MAX_LOG_LINE = 300
# Output blocks for this many tests at most; the whole body stays below GitHub's 65536 characters.
MAX_LOGGED_TESTS = 3
MAX_BODY = 60_000
MAX_LISTED = 200
MAX_BULLETS = 30
NO_IMAGES = (
    "No before and after images: the issue has no probe cells to render "
    "(an AI-review script, a flaky test or a CI finding)."
)
LOGS_LEFT_OUT = "The output is left out to keep this description within GitHub's limit; it is in the fix job's artifact."
CUT = "\n\n(The rest of this description was cut to stay within GitHub's limit.)"
WITHHELD = "(withheld: the text is shaped like a secret)"
CONVERT_TO_DRAFT = (
    "mutation($id: ID!) { convertPullRequestToDraft(input: {pullRequestId: $id}) { pullRequest { isDraft } } }"
)
_IMAGE_KEY = re.compile(r"(before|after|diff)/[A-Za-z0-9._-]+\.png")
_TESTS = re.compile(r"^<!-- factory:tests=([A-Za-z0-9+/=]*) -->$", re.MULTILINE)
_ATTEMPT = re.compile(r"^<!-- factory:attempt=([0-9]+) -->$", re.MULTILINE)
_SHA = re.compile(r"[0-9a-f]{40}")
_URL = re.compile(r"https://[^\s()<>\[\]]+")
_BACKTICKS = re.compile(r"`+")
_MENTION = re.compile(r"@(?=[A-Za-z0-9_-])")

Dispatch = Callable[[int, int], None]
PublishEvidence = Callable[[Mapping[str, Path]], dict[str, str]]


# --- markers ---------------------------------------------------------------------------------


def marked_test_ids(body: str) -> list[str]:
    """The test ids of a factory pull request's `factory:tests` marker (empty without one)."""
    match = _TESTS.search(_marker_block(body))
    if match is None:
        return []
    data = json.loads(base64.b64decode(match.group(1), validate=True))
    if not isinstance(data, list):
        raise ValueError("factory:tests must hold a JSON list")
    return [check_test_id(item, "factory:tests") for item in data]


def attempt_in(body: str) -> int | None:
    match = _ATTEMPT.search(_marker_block(body))
    return int(match.group(1)) if match else None


# --- text ------------------------------------------------------------------------------------


def _neutral(text: str) -> str:
    """Text that can neither open nor close an HTML comment (so no hidden marker can come of it)
    and mentions nobody (a zero-width joiner after each @)."""
    while "<!--" in text or "-->" in text:
        text = text.replace("<!--", "").replace("-->", "")
    return _MENTION.sub("@\u200d", text)


def _line(text: str, limit: int = MAX_REASON) -> str:
    text = _neutral(" ".join(str(text).split()))
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _code(text: str) -> str:
    text = _line(text)
    ticks = "`" * (max((len(run) for run in _BACKTICKS.findall(text)), default=0) + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{ticks}{pad}{text}{pad}{ticks}"


def _cell(text: str) -> str:
    return _line(text).replace("|", "\\|")


def _short(sha: str) -> str:
    return sha[:7] if _SHA.fullmatch(sha) else _line(sha, 12)


def _bullets(items: Sequence[str]) -> str:
    shown = [f"- {WITHHELD if secret_shape(item) else _line(item)}" for item in items[:MAX_BULLETS]]
    if len(items) > MAX_BULLETS:
        shown.append(f"- and {len(items) - MAX_BULLETS} more")
    return "\n".join(shown)


def _last_lines(text: str) -> str:
    lines = _neutral(text).rstrip().splitlines()[-LOG_LINES:]
    return "\n".join(line if len(line) <= MAX_LOG_LINE else line[: MAX_LOG_LINE - 1] + "…" for line in lines)


def safe_title(title: str, issue: int) -> str:
    """The issue title as a pull request title or commit subject: one line, mentioning nobody, and
    never a closing reference (the merger turns the title into the squash commit's subject)."""
    text = _line(title, MAX_TITLE)
    if not text or CLOSING_REFERENCE.search(text) or ISSUE_URL.search(text):
        return f"Fix for issue {issue}"
    return text


# --- the pull request body -------------------------------------------------------------------


def render_pr_body(
    issue: int,
    report: FixReport,
    proof: Proof,
    evidence: Mapping[str, str],
    fence: FenceReport,
    attempt: int,
    *,
    changed_files: Sequence[str] = (),
    limit: int | None = None,
    review: Sequence[str] | None = None,
    images_note: str | None = None,
) -> str:
    """`Refs #N`, the cause, the change, the proof, the images, the baselines, the files, what needs
    a human and the attempt; then the sentinel and the `factory:attempt` and `factory:tests` markers.

    The report's text is checked again (it may not come from load_report): no marker, no secret
    shape, no closing reference, no issue link; mentions are neutralised. Tool output is neutralised
    and kept inside code blocks and spans. The text stays within MAX_BODY characters: the output is
    left out first, then the rest is cut.
    """
    limit = load_config().limits.attempts_per_issue if limit is None else limit
    for name in ("cause", "change", "reason"):
        check_text(getattr(report, name), name)
    for i, baseline in enumerate(report.baselines):
        check_text(baseline.name, f"baselines[{i}].name")
        check_text(baseline.reason, f"baselines[{i}].reason")
    test_ids = [check_test_id(t, f"test_ids[{i}]") for i, t in enumerate(report.test_ids)]
    review = list(fence.review if review is None else review)

    def sections(with_logs: bool) -> str:
        baselines = [f"- {_code(b.name)}: {_line(b.reason)}" for b in report.baselines]
        parts = [
            ("Cause", _neutral(report.cause.strip())),
            ("Change", _neutral(report.change.strip())),
            ("Proof", _proof_section(test_ids, proof, with_logs)),
            ("Before and after", _images_section(evidence, images_note)),
            ("Changed baselines", "\n".join(baselines) or "None."),
            ("Changed files", _files_section(changed_files, fence)),
        ]
        if review:
            parts.append(
                ("Needs a human", "The fence found changes a person must review before this merges:\n\n" + _bullets(review))
            )
        parts.append(("Attempt", f"Attempt {attempt} of {limit}."))
        return f"Refs #{issue}\n\n" + "\n\n".join(f"## {name}\n\n{text}" for name, text in parts)

    body = sections(with_logs=True)
    if len(body) > MAX_BODY:
        body = sections(with_logs=False)
    if len(body) > MAX_BODY:
        body = body[: MAX_BODY - len(CUT)].rstrip() + CUT
    encoded = base64.b64encode(json.dumps(test_ids).encode()).decode()
    markers = [SENTINEL, f"<!-- factory:attempt={attempt} -->", f"<!-- factory:tests={encoded} -->"]
    return body + "\n\n" + "\n".join(markers) + "\n"


def _proof_section(test_ids: Sequence[str], proof: Proof, with_logs: bool) -> str:
    base, head = _short(proof.base_sha), _short(proof.head_sha)
    if proof.flaky:
        intro = (
            f"A flaky test: it ran {FLAKY_RUNS} times on each commit with Chromium's CPU throttled "
            f"{FLAKY_THROTTLE}x. It must fail at least once on the base and never on the head."
        )
        columns = f"Base `{base}` (must fail at least once) | Head `{head}` (must never fail)"
    else:
        intro = (
            "Each new test ran on a worktree of the base with only its test file taken from the head, "
            "then on a worktree of the head."
        )
        columns = f"Base `{base}` (must fail) | Head `{head}` (must pass)"
    rows = [f"| Test | {columns} |", "|---|---|---|"]
    for test_id in test_ids:
        red = proof.red.get(test_id, "not run")
        green = proof.green.get(test_id, "not run")
        rows.append(f"| {_code(test_id)} | {_cell(red)} | {_cell(green)} |")
    outcome = "passed" if proof.suite_passed else "failed"
    parts = [
        intro,
        "\n".join(rows),
        f"Full suite on the head (`{SUITE_COMMAND}`): {outcome}, {_code(proof.suite_summary or 'no summary')}.",
    ]
    logs = [
        (f"{side.capitalize()}, {_code(test_id)}", proof.logs.get(f"{side} {test_id}", ""))
        for test_id in test_ids[:MAX_LOGGED_TESTS]
        for side in ("base", "head")
    ]
    shown = [(title, text) for title, text in logs if text.strip()]
    if shown and with_logs:
        blocks = "\n\n".join(f"{title}\n\n{_fenced('text', _last_lines(text))}" for title, text in shown)
        parts.append(f"<details>\n<summary>Output</summary>\n\n{blocks}\n\n</details>")
    elif shown:
        parts.append(LOGS_LEFT_OUT)
    return "\n\n".join(parts)


def _images_section(evidence: Mapping[str, str], note: str | None) -> str:
    names = sorted({key.split("/", 1)[1] for key, url in evidence.items() if _IMAGE_KEY.fullmatch(key)})
    if not names:
        return _line(note) if note else NO_IMAGES
    rows = ["| Cell | Before | After | Diff |", "|---|---|---|---|"]
    for name in names:
        cells = []
        for side in ("before", "after", "diff"):
            url = evidence.get(f"{side}/{name}", "")
            cells.append(f"![{side}]({url})" if _URL.fullmatch(url) else "none")
        rows.append(f"| {_code(name.removesuffix('.png'))} | " + " | ".join(cells) + " |")
    table = "\n".join(rows)
    return f"{table}\n\n{_line(note)}" if note else table


def _files_section(files: Sequence[str], fence: FenceReport) -> str:
    listed = [f"- {_code(path)}" for path in files[:MAX_LISTED]]
    if len(files) > MAX_LISTED:
        listed.append(f"- and {len(files) - MAX_LISTED} more")
    text = "\n".join(listed) or "- none"
    return f"{text}\n\n{fence.changed_lines} changed lines, not counting PNG files and the generated pages."


# --- git: the commit publish builds ----------------------------------------------------------


def fetch_bundle(repo: Path, bundle: Path, branch: str) -> tuple[str, str]:
    """(head, base): the bundle's branch fetched into a private ref, and its merge base with main.
    Nothing is checked out; git runs no hook."""
    git(repo, "fetch", "--no-tags", "origin", "+refs/heads/main:refs/remotes/origin/main")
    path = str(bundle.resolve())
    git(repo, "bundle", "verify", "--quiet", path)
    git(repo, "fetch", "--no-tags", path, f"+refs/heads/{branch}:{HEAD_REF}")
    head = git(repo, "rev-parse", "--verify", f"{HEAD_REF}^{{commit}}")
    base = git(repo, "merge-base", "refs/remotes/origin/main", head)
    return head, base


def pull_head(repo: Path, branch: str, pull: Mapping[str, Any]) -> str:
    """The pull request's head as GitHub reports it, fetched from origin; a branch that moved since
    is never built on or overwritten."""
    expected = str((pull.get("head") or {}).get("sha") or "")
    git(repo, "fetch", "--no-tags", "origin", f"+refs/heads/{branch}:{PR_HEAD_REF}")
    actual = git(repo, "rev-parse", "--verify", f"{PR_HEAD_REF}^{{commit}}")
    if actual != expected:
        raise FixError(
            f"the pull request branch moved: GitHub reports {_short(expected)}, the branch is at "
            f"{actual[:7]}; nothing was pushed"
        )
    return actual


def tree_of(repo: Path, revision: str) -> str | None:
    if not _SHA.fullmatch(revision):
        return None
    try:
        return git(repo, "rev-parse", "--verify", "--quiet", f"{revision}^{{tree}}")
    except FixError:
        return None


def commit_message(title: str, issue: int, attempt: int, report: FixReport) -> str:
    """English, rendered here from the issue title and the validated report; never the agent's."""
    subject = safe_title(title, issue)
    if len(subject) > MAX_SUBJECT:
        subject = subject[: MAX_SUBJECT - 1].rsplit(" ", 1)[0] + "…"
    paragraphs = [textwrap.fill(_neutral(" ".join(text.split())), 72) for text in (report.cause, report.change)]
    return "\n\n".join([subject, *paragraphs, f"Refs #{issue}. Fixer attempt {attempt}."]) + "\n"


def build_commit(repo: Path, tree: str, parent: str, message: str) -> str:
    """One commit with `tree` on `parent`, authored and committed by the factory."""
    name, email = IDENTITY
    env = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": name,
        "GIT_AUTHOR_EMAIL": email,
        "GIT_COMMITTER_NAME": name,
        "GIT_COMMITTER_EMAIL": email,
    }
    done = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", "commit-tree", tree, "-p", parent],
        cwd=repo,
        env=env,
        input=message.encode(),
        capture_output=True,
        check=False,
    )
    if done.returncode != 0:
        raise FixError(f"git commit-tree failed: {redact(done.stderr.decode(errors='replace')).strip()}")
    return done.stdout.decode().strip()


def _paths(repo: Path, base: str, head: str, statuses: str | None = None) -> list[str]:
    args = ["diff", "--name-only", "--no-renames", "-z"]
    if statuses:
        args.append(f"--diff-filter={statuses}")
    names = _git_bytes(repo, *args, base, head).decode(errors="replace").split("\0")
    return [name for name in names if name]


def _blob(repo: Path, commit: str, path: str) -> bytes | None:
    """The bytes of `path` in `commit`, or None when it is not a blob (a submodule link)."""
    entry = _git_bytes(repo, "ls-tree", "-z", commit, "--", path).decode(errors="replace").split("\0")[0]
    meta = entry.split("\t", 1)[0].split()
    if len(meta) != 3 or meta[1] != "blob":
        return None
    return _git_bytes(repo, "cat-file", "blob", meta[2])


def changed_files(repo: Path, base: str, head: str) -> list[str]:
    return _paths(repo, base, head)


def secret_findings(
    repo: Path,
    parent: str,
    commit: str,
    message: str,
    report: FixReport,
    proof: Proof | None,
    images: Mapping[str, Path],
) -> list[str]:
    """Where secret-shaped text sits in what this attempt would publish: the commit message, every
    file name and every blob (text or binary) that differs between the commit and its parent, the
    report, the proof and the images. The agent's own commits, authors and messages are never
    pushed, so they need no scan."""
    found: list[str] = []

    def check(where: str, text: str) -> None:
        if secret_shape(text) and where not in found:
            found.append(where)

    check("the commit message", message)
    for path in _paths(repo, parent, commit, "AMT"):
        if secret_shape(path):
            check("a file name", path)
            continue
        blob = _blob(repo, commit, path)
        if blob is not None:
            check(path, blob.decode(errors="replace"))
    check("the report", json.dumps(report.to_json()))
    if proof is not None:
        check("the proof", json.dumps(proof.to_json()))
    for key, path in images.items():
        check(f"the image {key}", path.read_bytes().decode(errors="replace"))
    return found


def valid_images(images: Mapping[str, Path]) -> dict[str, Path]:
    """The images that are what they claim: before/, after/ or diff/ PNG files of a sane size."""
    valid: dict[str, Path] = {}
    for key, path in images.items():
        problem = None
        if not _IMAGE_KEY.fullmatch(key):
            problem = "not a before/, after/ or diff/ PNG name"
        elif path.is_symlink() or not path.is_file():
            problem = "not a regular file"
        elif path.stat().st_size > MAX_IMAGE_BYTES:
            problem = f"larger than {MAX_IMAGE_BYTES} bytes"
        elif not path.read_bytes().startswith(PNG_SIGNATURE):
            problem = "not a PNG"
        if problem:
            print(f"warning: image {_line(key, 80)} left out: {problem}", file=sys.stderr)
        else:
            valid[key] = path
    return valid


def flaky_test_ids(data: Mapping[str, Any], trusted: frozenset[str]) -> list[str]:
    """The test ids the trusted issue's flaky groups name (a flaky job's selector is not one)."""
    if (data.get("user") or {}).get("login") not in trusted:
        return []
    try:
        groups = groups_in(str(data.get("body") or ""))
    except (ValueError, KeyError, TypeError):
        return []
    ids = []
    for group in groups:
        if group.check != "flaky":
            continue
        try:
            ids.append(check_test_id(group.selector, "selector"))
        except DraftError:
            continue
    return ids


def proof_mismatch(
    report: FixReport,
    proof: Proof | None,
    base: str,
    tree: str,
    proof_tree: str | None,
    flaky: bool,
    flaky_ids: Sequence[str],
) -> list[str]:
    """Why the proof is not about this fix: no proof, another tree or base, the wrong kind, or a
    flaky fix that leaves out the issue's flaky test."""
    if proof is None:
        return ["the fix job wrote no valid proof"]
    problems: list[str] = []
    if proof_tree is None:
        problems.append(f"the proof is for {_short(proof.head_sha)}, which is not among the fix's commits")
    elif proof_tree != tree:
        problems.append(f"the proof is for {_short(proof.head_sha)}, whose files are not the fix's")
    if proof.base_sha != base:
        problems.append(f"the proof starts from {_short(proof.base_sha)}, not the base {base[:7]}")
    if proof.flaky != flaky:
        problems.append(
            "the issue is a flaky test, but this is not the statistical proof"
            if flaky
            else "the statistical proof is only for a flaky-test issue"
        )
    if flaky:
        problems += [f"the report does not name the flaky test {t}" for t in flaky_ids if t not in report.test_ids]
    return [_line(problem) for problem in problems]


def proof_verdicts(report: FixReport, proof: Proof) -> list[str]:
    """Why the proof does not show red on the base and green on the head. The verdicts are read
    from the data, not from the fix job's own `ok`."""
    problems: list[str] = []
    for test_id in report.test_ids:
        red = proof.red.get(test_id, "not run")
        green = proof.green.get(test_id, "not run")
        if not red.startswith("failed"):
            problems.append(f"not red on the base: {test_id}: {red}")
        if not green.startswith("passed"):
            problems.append(f"not green on the head: {test_id}: {green}")
    if not proof.suite_passed:
        problems.append(f"the full suite fails on the head: {proof.suite_summary or 'no summary'}")
    if not problems and not proof.ok:
        problems += list(proof.reasons) or ["the proof is not green"]
    return [_line(problem) for problem in problems]


def unexplained_baselines(repo: Path, base: str, head: str, report: FixReport) -> list[str]:
    """Changed baseline images that no `baselines` entry of the report explains."""
    named = {b.name for b in report.baselines}
    reasons = []
    for path in _paths(repo, base, head):
        if path.startswith(BASELINE_DIR) and path.endswith(".png"):
            name = path.rsplit("/", 1)[-1]
            if not named & {path, name, name.removesuffix(".png")}:
                reasons.append(f"baseline changed without a stated reason: {path}")
    return reasons


def token_remote(repo_name: str) -> str:
    return f"https://x-access-token:{os.environ.get('GH_TOKEN', '')}@github.com/{repo_name}.git"


def push(repo: Path, remote: str, commit: str, branch: str, expected: str | None) -> None:
    """Push `commit` to `branch`. A branch with a pull request is replaced only while it still
    points at the head GitHub reported (`expected`); without one the branch is the factory's own."""
    lease = f"--force-with-lease=refs/heads/{branch}:{expected}" if expected else "--force"
    try:
        git(repo, "push", lease, remote, f"{commit}:refs/heads/{branch}")
    except FixError as error:
        raise FixError(redact(str(error).replace(remote, "<remote>"))) from None


# --- publishing ------------------------------------------------------------------------------


@dataclass
class _Attempt:
    gh: Any
    api: str
    issue: int
    attempt: int
    limit: int
    dispatch: Dispatch
    report: FixReport | None

    def comment(self, text: str) -> None:
        body = f"{text.rstrip()}\n\n{SENTINEL}\n<!-- factory:attempt={self.attempt} -->\n"
        self.gh.request("POST", f"{self.api}/issues/{self.issue}/comments", {"body": body})

    def remove_label(self, number: int, label: str) -> None:
        try:
            self.gh.request("DELETE", f"{self.api}/issues/{number}/labels/{urllib.parse.quote(label, safe='')}")
        except GitHubError as error:
            if error.status != 404:
                raise

    def failed(self, pull: Mapping[str, Any] | None, reasons: Sequence[str]) -> None:
        """A failed attempt: the next one, or a person after the last."""
        if self.attempt >= self.limit:
            headline = f"Fix attempt {self.attempt} of {self.limit} failed and no attempt is left: the issue needs a person."
            return self.stop(pull, reasons, headline)
        self.comment(
            f"Fix attempt {self.attempt} of {self.limit} failed; attempt {self.attempt + 1} starts now.\n\n"
            + _bullets(reasons)
        )
        self.dispatch(self.issue, self.attempt + 1)
        print(f"#{self.issue}: attempt {self.attempt} failed; dispatched attempt {self.attempt + 1}")
        return None

    def stop(self, pull: Mapping[str, Any] | None, reasons: Sequence[str], headline: str | None = None) -> None:
        """The factory stops touching the issue: a person decides."""
        headline = headline or f"Fix attempt {self.attempt} of {self.limit} stopped: the issue needs a person."
        self.comment(f"{headline}\n\n{_bullets(reasons)}")
        self.gh.request("POST", f"{self.api}/issues/{self.issue}/labels", {"labels": [NEEDS_HUMAN]})
        self.remove_label(self.issue, READY)
        self.remove_label(self.issue, FIXING)
        if pull is not None:
            self.back_to_draft(pull, reasons)
        print(f"#{self.issue}: needs a human after attempt {self.attempt}")
        return None

    def back_to_draft(self, pull: Mapping[str, Any], reasons: Sequence[str]) -> None:
        node = pull.get("node_id")
        if node and not pull.get("draft"):
            self.gh.graphql(CONVERT_TO_DRAFT, {"id": node})
        lines = [
            f"The factory stopped after attempt {self.attempt} of {self.limit} for #{self.issue}: this pull "
            f"request is a draft again and #{self.issue} needs a person.",
        ]
        report = self.report
        if report is not None and report.status == "fixed":
            lines += [
                "",
                "What the last attempt tried:",
                "",
                f"- Cause: {_line(report.cause)}",
                f"- Change: {_line(report.change)}",
                f"- Tests: {', '.join(_code(t) for t in report.test_ids)}",
            ]
        lines += ["", "Why it stopped:", "", _bullets(reasons)]
        self.gh.request("POST", f"{self.api}/issues/{pull['number']}/comments", {"body": "\n".join(lines) + "\n"})


def _label_names(data: Mapping[str, Any]) -> set[str]:
    return {label["name"] for label in data.get("labels") or []}


def publish_images(
    images: Mapping[str, Path], number: int, commit: str, publish_evidence: PublishEvidence | None
) -> tuple[dict[str, str], str | None]:
    """Push the images to the evidence branch under prs/<number>/<commit>/; (urls by key, a note)."""
    if not images:
        return {}, None
    if publish_evidence is None:
        return {}, "The images were not published in this run."
    prefix = f"prs/{number}/{commit}"
    try:
        urls = publish_evidence({f"{prefix}/{key}": path for key, path in images.items()})
    except EvidenceError as error:
        print(f"warning: evidence not published: {redact(str(error))}", file=sys.stderr)
        return {}, "The images could not be published; see the publish job's log."
    return {key: urls[f"{prefix}/{key}"] for key in images if f"{prefix}/{key}" in urls}, None


def publish_fix(
    gh: Any,
    repo: Path,
    issue: int,
    attempt: int,
    bundle: Path | None,
    report: FixReport | None,
    proof: Proof | None,
    images: Mapping[str, Path],
    config: Config,
    *,
    branch: str,
    dispatch: Dispatch,
    remote: str | None = None,
    publish_evidence: PublishEvidence | None = None,
    trusted: frozenset[str] | None = None,
    fix_result: str = "success",
    scan_result: str = "success",
    leaked: Sequence[str] = (),
) -> int | None:
    """Fence a commit built from the attempt's tree and open or update its pull request; returns its
    number, or None when the attempt ends without one (see the module docstring for the outcomes).

    `scan_result` is the outcome of the fix job's own secret scan; `leaked` names artifact files
    that held secret-shaped text before they could be read. Both send the issue to a person.
    """
    trusted = trusted if trusted is not None else config.trusted(os.environ.get("FACTORY_APP_SLUG"))
    repo_name = config.site.repo
    api = f"/repos/{repo_name}"
    if branch and not branch_pattern(issue).fullmatch(branch):
        raise ValueError(f"not a fix branch of #{issue}: {branch!r}")
    run = _Attempt(gh, api, issue, attempt, config.limits.attempts_per_issue, dispatch, report)
    data = gh.request("GET", f"{api}/issues/{issue}")
    labels = _label_names(data)
    # The owner took the issue over while the fix ran (closed it, or set or removed the labels).
    if data.get("state") != "open" or NEEDS_HUMAN in labels or not labels & {READY, FIXING}:
        print(f"#{issue}: closed, or no longer the factory's to fix; nothing published")
        return None
    pull = find_pull(gh, api, repo_name, issue, trusted, branch or None)
    if scan_result == "failure":
        return run.stop(pull, ["the fix job's secret scan found secret-shaped text in its output; nothing was published"])
    if leaked:
        return run.stop(pull, [f"secret-shaped text in {where}; nothing was published" for where in leaked])
    if fix_result != "success":
        return run.failed(pull, [f"the fix job ended with result {fix_result} (see its log)"])
    if report is None:
        return run.failed(pull, ["the fixer returned no valid report (see the fix job's log)"])
    if report.status != "fixed":
        return run.stop(pull, [f"the fixer reports {report.status.replace('_', ' ')}: {report.reason}"])
    if bundle is None or not bundle.is_file() or not branch:
        return run.failed(pull, ["the fix job produced no commits"])
    try:
        head, base = fetch_bundle(repo, bundle, branch)
    except FixError as error:
        return run.failed(pull, [f"the commits could not be read from the bundle: {error}"])
    expected = (pull.get("head") or {}).get("sha") if pull else None
    parent = pull_head(repo, branch, pull) if pull else base
    if pull and not is_ancestor(repo, parent, head):
        return run.failed(pull, ["the fix does not build on the pull request's head (the branch changed while it ran)"])
    tree = git(repo, "rev-parse", f"{head}^{{tree}}")
    if tree == git(repo, "rev-parse", f"{parent}^{{tree}}"):
        return run.failed(pull, ["the fix job produced no change"])
    title = safe_title(str(data.get("title") or ""), issue)
    message = commit_message(title, issue, attempt, report)
    commit = build_commit(repo, tree, parent, message)

    checked = valid_images(images)
    leaks = secret_findings(repo, parent, commit, message, report, proof, checked)
    if leaks:
        return run.stop(pull, [f"secret-shaped text in {where}; nothing was published" for where in leaks])
    try:
        fence = check_fence(repo, base, commit, config.fence, config.limits)
    except GitError as error:
        raise FixError(f"the fence could not run: {error}") from error
    flaky = FLAKY in labels
    if proof is not None and proof.flaky and flaky and not proof.reproducible:
        return run.stop(pull, [*proof.reasons, *proof.red.values()] or ["the flaky test never failed on the base"])
    proof_tree = tree_of(repo, proof.head_sha) if proof is not None else None
    problems = [f"fence: {reason}" for reason in fence.reasons]
    problems += proof_mismatch(report, proof, base, tree, proof_tree, flaky, flaky_test_ids(data, trusted))
    if problems:
        return run.failed(pull, problems)
    assert proof is not None  # proof_mismatch reports a missing proof
    passing = [t for t in report.test_ids if proof.red.get(t, "").startswith("passed")]
    if passing and not flaky:
        return run.stop(pull, [f"the new test passes on the base, so the defect does not reproduce: {t}" for t in passing])
    problems = proof_verdicts(report, proof)
    if problems:
        return run.failed(pull, problems)

    review = [*fence.review, *unexplained_baselines(repo, base, commit, report)]
    files = changed_files(repo, base, commit)

    def body(evidence: Mapping[str, str], note: str | None) -> str:
        text = render_pr_body(
            issue, report, proof, evidence, fence, attempt,
            changed_files=files, limit=run.limit, review=review, images_note=note,
        )
        if secret_shape(text):  # everything in it was scanned; never publish if that ever changes
            raise FixError("the pull request body holds secret-shaped text")
        return text

    first = body({}, "The images are being published." if checked else None)
    push(repo, remote or token_remote(repo_name), commit, branch, expected)
    try:
        if pull is None:
            created = gh.request("POST", f"{api}/pulls", {"title": title, "head": branch, "base": "main", "body": first})
            number = created["number"]
        else:
            number = pull["number"]
        gh.request("POST", f"{api}/issues/{number}/labels", {"labels": [PR_LABEL, *([NEEDS_HUMAN] if review else [])]})
        evidence, note = publish_images(checked, number, commit, publish_evidence)
        gh.request("PATCH", f"{api}/pulls/{number}", {"title": title, "body": body(evidence, note)})
        text = f"Fix attempt {attempt} of {run.limit}: pull request #{number} for `{commit[:7]}`."
        if review:
            text += "\n\nThe pull request needs a human review:\n\n" + _bullets(review)
        run.comment(text)
        gh.request("POST", f"{api}/issues/{issue}/labels", {"labels": [FIXING]})
        run.remove_label(issue, READY)
    except Exception as error:
        # The branch moved already: the attempt is recorded before the error surfaces.
        try:
            run.comment(
                f"Fix attempt {attempt} of {run.limit}: `{commit[:7]}` was pushed to `{branch}`, but publishing "
                f"failed ({type(error).__name__}); see the publish job's log."
            )
        except GitHubError as nested:
            print(f"warning: the attempt could not be recorded: {redact(str(nested))}", file=sys.stderr)
        raise
    print(f"#{issue}: pull request #{number} for {commit[:7]}")
    return number


# --- command line (python -m tools.factory.fix publish) --------------------------------------


def _read_proof(path: Path) -> Proof | None:
    try:
        return Proof.from_json(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        print(f"no proof at {path}", file=sys.stderr)
    except (OSError, ValueError) as error:
        print(f"invalid proof {path}: {error}", file=sys.stderr)
    return None


def _collect_images(directory: Path) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for side in ("before", "after", "diff"):
        folder = directory / side
        if folder.is_dir() and not folder.is_symlink():
            for path in sorted(folder.glob("*.png")):
                found[f"{side}/{path.name}"] = path
    return found


def _leaked(inputs: Path) -> list[str]:
    """The artifact's JSON files that hold secret-shaped text, as written or once their escapes
    are decoded (`\\u005f` for `_`); they are never read further, and the issue goes to a person
    instead of another attempt."""
    found = []
    for name in ("report.json", "proof.json"):
        path = inputs / name
        if not path.is_file() or path.is_symlink():
            continue
        text = path.read_bytes().decode(errors="replace")
        try:
            decoded = json.dumps(json.loads(text), ensure_ascii=False)
        except ValueError:
            decoded = ""
        if secret_shape(text) or secret_shape(decoded):
            found.append(f"the fix job's {name}")
    return found


def main_publish(args: argparse.Namespace, config: Config) -> int:
    """The publish job: GH_TOKEN is the App token; DISPATCH_TOKEN (GITHUB_TOKEN with actions: write)
    only starts the next attempt."""
    repo_name = config.site.repo
    token = os.environ.get("GH_TOKEN", "")
    gh = GitHub(repo_name, token or None)
    dispatcher = GitHub(repo_name, os.environ.get("DISPATCH_TOKEN") or None)

    def dispatch(issue: int, attempt: int) -> None:
        dispatcher.request(
            "POST",
            f"/repos/{repo_name}/actions/workflows/{WORKFLOW}/dispatches",
            {"ref": "main", "inputs": {"issue": str(issue), "attempt": str(attempt)}},
        )

    def publish_evidence(files: Mapping[str, Path]) -> dict[str, str]:
        with tempfile.TemporaryDirectory() as workdir:
            return evidence_module.publish(
                f"https://x-access-token:{token}@github.com/{repo_name}.git",
                files,
                f"Evidence for the fix of #{args.issue}, attempt {args.attempt}",
                f"https://raw.githubusercontent.com/{repo_name}",
                Path(workdir),
            )

    inputs: Path = args.inputs
    report: FixReport | None = None
    proof: Proof | None = None
    bundle: Path | None = None
    images: dict[str, Path] = {}
    leaked: list[str] = []
    if args.fix_result == "success":
        leaked = _leaked(inputs)
        if not leaked:
            report = read_report(inputs / "report.json")
            proof = _read_proof(inputs / "proof.json")
            bundle = inputs / "fix.bundle" if (inputs / "fix.bundle").is_file() else None
            images = _collect_images(inputs / "images")
    number = publish_fix(
        gh, Path.cwd(), args.issue, args.attempt, bundle, report, proof, images, config,
        branch=args.branch, dispatch=dispatch, publish_evidence=publish_evidence,
        fix_result=args.fix_result, scan_result=args.scan_result, leaked=leaked,
    )
    print(f"pull request #{number}" if number else "no pull request from this attempt")
    return 0
