"""The merger (factory-merge.yml): deterministic policy as code, no language model.

`gather` reads one pull request from GitHub and recomputes the fence from the main checkout: the pull
request's head is fetched as git objects into a private ref and diffed against its merge base with
main, never checked out or run. `evaluate` checks the facts against the spec's eight conditions in
order, plus R48(a) as a ninth row (the pull request, or a fix's issue, labelled factory:needs-human).
`act` does what the verdict says in the mode the owner set (`FACTORY_MODE`):

- shadow: one comment per head with the condition table and `<!-- factory:verdict=merge|block
  sha=<head> -->`, and `factory:merge-ready` while it would merge (removed while it would not). Once a
  trusted comment carries a verdict for the head, nothing is written for that head again (R48(c)), so
  the hourly sweep never repeats itself; and nothing is said before github-actions[bot] reviewed the
  head, so an early sweep cannot use up the head's one comment before the approval arrives.
- auto: squash-merge with the App token for exactly the evaluated head (`sha`), with the pull
  request's title through safe_title and the message `Refs #N`, never the description (R48(d)); the
  issue then moves from factory:fixing to factory:merged and gets the `factory:merged` marker that
  verify reads, the branch is deleted and dispatch promotes the next issues. A revert is merged and
  its branch deleted; its issue keeps factory:regressed and factory:needs-human. A branch that is
  behind main, with everything else passing, is updated instead (for the evaluated head only) and the
  merger stops: Check and the review run again on the new head.

Trust (R44, R48(b)): the approval counts only as the latest review by github-actions[bot], for the
current head, with no request for changes after it and none standing from anyone; a fix's body
markers count only in the block our code renders, when the App opened the pull request, and only
while `factory:head` names the head. Reverts carry no markers.

Command line: `python -m tools.factory.merge --pr N` or `--sweep` (every open pull request labelled
`factory`), from the root of the main checkout. FACTORY_MODE and FACTORY_APP_SLUG come from the
environment, the token from GH_TOKEN. Exit 0, or 2 when anything failed (GitHub, git, configuration),
with credentials redacted; a failure on one pull request does not stop the sweep.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import urllib.parse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from tools.factory import ci_facts, dispatch
from tools.factory.config import Config, ConfigError, Limits, load_config
from tools.factory.evidence import redact
from tools.factory.fence import FenceReport, GitError, check_fence
from tools.factory.fix import FIXING, FLAKY, NEEDS_HUMAN, REVIEWER, FixError, git
from tools.factory.fix_publish import WITHHELD, _cell, _line, head_in, safe_title
from tools.factory.github import GitHub, GitHubError
from tools.factory.issues import SENTINEL, _marker_block
from tools.factory.review import (
    _FIX_BRANCH,
    _REVERT_BRANCH,
    FACTORY,
    REVERT,
    _comment,
    _labels,
    _login,
    _remove_label,
    _short,
)
from tools.factory.secret_scan import secret_shape
from tools.factory.verify import MERGED, REGRESSED

MODES = ("shadow", "auto")
MERGE_READY = "factory:merge-ready"
MAIN = "main"
HEAD_REF = "refs/factory/merge-head"
WINDOW = timedelta(hours=24)
PER_PAGE = 100
MAX_PAGES = 10
MAX_VALUE = 500
CONDITIONS = (
    "Mode",
    "Factory pull request",
    "Approval of the head",
    "Required checks",
    "Fence, copy guard and size",
    "Up to date with main",
    "Pages on main",
    "Factory merges in 24 h",
    "Not taken over",
)
_MODE, _PULL, _APPROVAL, _CHECKS, _FENCE, _UP_TO_DATE, _PAGES, _CAP, _TAKEN_OVER = range(len(CONDITIONS))
_DECISIVE_STATES = frozenset({"APPROVED", "CHANGES_REQUESTED", "DISMISSED"})
_SHA = re.compile(r"[0-9a-f]{40}")
_VERDICT = re.compile(r"^<!-- factory:verdict=(merge|block) sha=([0-9a-f]{40}) -->$", re.MULTILINE)

Action = Literal["merge", "block", "update-branch"]
Kind = Literal["fix", "revert"]


@dataclass(frozen=True)
class PullFacts:
    """What the policy needs to know about one pull request. `reviews` are (login, state, commit_id)
    in the order they were submitted; `checks` maps each required check to its conclusion on the head
    ("success", a failed conclusion, a status while running, or "missing")."""

    number: int
    author: str
    labels: frozenset[str]
    head_ref: str
    same_repo: bool
    draft: bool
    head_sha: str
    issue: int | None
    issue_labels: frozenset[str]
    reviews: list[tuple[str, str, str]]
    checks: dict[str, str]
    required: list[str]
    fence: FenceReport
    behind: bool
    pages_conclusion: str
    merges_24h: int
    title: str = ""
    body_head: str | None = None
    state: str = "open"
    base_ref: str = MAIN
    issue_open: bool = True
    verdict_posted: bool = False


@dataclass(frozen=True)
class Row:
    condition: str
    ok: bool
    value: str


@dataclass(frozen=True)
class Verdict:
    action: Action
    rows: list[Row] = field(default_factory=list)

    @property
    def failed(self) -> list[Row]:
        return [row for row in self.rows if not row.ok]


# --- the policy ---------------------------------------------------------------------------------


def _kind(facts: PullFacts) -> Kind | None:
    """A fix comes from fix/<N>-… without factory:revert; a revert from revert/<N> with it."""
    revert = REVERT in facts.labels
    if revert and _REVERT_BRANCH.fullmatch(facts.head_ref):
        return "revert"
    if not revert and _FIX_BRANCH.fullmatch(facts.head_ref):
        return "fix"
    return None


def reviewed_head(facts: PullFacts) -> bool:
    """Whether github-actions[bot] reviewed the current head, in any state."""
    return any(login == REVIEWER and commit == facts.head_sha for login, _, commit in facts.reviews)


def _mode_row(mode: str) -> Row:
    return Row(CONDITIONS[_MODE], mode in MODES, mode or "unset")


def _pull_row(facts: PullFacts, app_login: str, kind: Kind | None) -> Row:
    def no(value: str) -> Row:
        return Row(CONDITIONS[_PULL], False, value)

    if not app_login:
        return no("no App login is configured (FACTORY_APP_SLUG)")
    if facts.author != app_login:
        return no(f"opened by {facts.author or 'nobody'}, not the App")
    if FACTORY not in facts.labels:
        return no(f"not labelled {FACTORY}")
    if not facts.same_repo:
        return no("the head is in another repository")
    if facts.state != "open":
        return no(f"the pull request is {facts.state or 'not open'}")
    if facts.draft:
        return no("a draft: a person has it")
    if facts.base_ref != MAIN:
        return no(f"targets {facts.base_ref}, not {MAIN}")
    if kind is None:
        return no(f"the branch {facts.head_ref} is neither fix/<N>-… without {REVERT} nor revert/<N> with it")
    if facts.issue is None or not facts.issue_open:
        return no(f"#{facts.issue} is not an open issue")
    needed = FIXING if kind == "fix" else REGRESSED
    if needed not in facts.issue_labels:
        return no(f"issue #{facts.issue} is not labelled {needed}")
    # R44: the body's markers count only while factory:head names the head (a revert has none).
    if kind == "fix" and facts.body_head != facts.head_sha:
        return no("the body's factory:head marker does not name the head")
    return Row(CONDITIONS[_PULL], True, f"{facts.head_ref} by the App; issue #{facts.issue} is {needed}")


def _standing_changes(reviews: Sequence[tuple[str, str, str]]) -> list[str]:
    """Logins whose latest approving, rejecting or dismissed review requests changes."""
    latest: dict[str, str] = {}
    for login, state, _ in reviews:
        if state in _DECISIVE_STATES:
            latest[login] = state
    return [login for login, state in latest.items() if state == "CHANGES_REQUESTED"]


def _approval_row(facts: PullFacts) -> Row:
    def no(value: str) -> Row:
        return Row(CONDITIONS[_APPROVAL], False, value)

    mine = [i for i, (login, _, _) in enumerate(facts.reviews) if login == REVIEWER]
    if not mine:
        return no(f"no review by {REVIEWER}")
    index = mine[-1]
    _, state, commit = facts.reviews[index]
    if state != "APPROVED":
        return no(f"the latest review by {REVIEWER} is {state.lower() or 'unknown'}")
    if commit != facts.head_sha:
        return no(f"{REVIEWER} approved {_short(commit)}, not the head {_short(facts.head_sha)}")
    later = [login for login, state, _ in facts.reviews[index + 1 :] if state == "CHANGES_REQUESTED"]
    if later:
        return no(f"changes requested after the approval by {', '.join(dict.fromkeys(later))}")
    standing = _standing_changes(facts.reviews)
    if standing:
        return no(f"changes requested by {', '.join(standing)}")
    return Row(CONDITIONS[_APPROVAL], True, f"{REVIEWER} approved {_short(facts.head_sha)}")


def _checks_row(facts: PullFacts) -> Row:
    if not facts.required:
        return Row(CONDITIONS[_CHECKS], False, f"no required status checks in the rules of {MAIN}")
    failed = [
        f"{name}: {facts.checks.get(name, 'missing')}" for name in facts.required if facts.checks.get(name) != "success"
    ]
    if failed:
        return Row(CONDITIONS[_CHECKS], False, "; ".join(failed))
    count = len(facts.required)
    return Row(CONDITIONS[_CHECKS], True, f"{count} of {count} succeeded on {_short(facts.head_sha)}")


def _fence_row(facts: PullFacts) -> Row:
    report = facts.fence
    if not report.ok:
        return Row(CONDITIONS[_FENCE], False, "; ".join(report.reasons) or "not ok")
    if report.review:  # R48(a): a soft reason needs a person as much as a hard one
        return Row(CONDITIONS[_FENCE], False, "needs a human: " + "; ".join(report.review))
    return Row(CONDITIONS[_FENCE], True, f"hold ({report.changed_lines} changed lines)")


def _up_to_date_row(facts: PullFacts) -> Row:
    if facts.behind:
        return Row(CONDITIONS[_UP_TO_DATE], False, f"behind {MAIN}")
    return Row(CONDITIONS[_UP_TO_DATE], True, "yes")


def _pages_row(facts: PullFacts, kind: Kind | None) -> Row:
    conclusion = facts.pages_conclusion or "unknown"
    if conclusion == "success":
        return Row(CONDITIONS[_PAGES], True, "the last run succeeded")
    if kind == "revert":
        return Row(CONDITIONS[_PAGES], True, f"the last run: {conclusion}; a revert may merge while {MAIN} is red")
    if FLAKY in facts.issue_labels:
        return Row(
            CONDITIONS[_PAGES], True, f"the last run: {conclusion}; the fix of a flaky test may merge while {MAIN} is red"
        )
    return Row(CONDITIONS[_PAGES], False, f"the last run: {conclusion}")


def _cap_row(facts: PullFacts, kind: Kind | None, limits: Limits) -> Row:
    cap = limits.merges_per_day
    if kind == "revert":
        return Row(CONDITIONS[_CAP], True, f"a revert does not count ({facts.merges_24h} of {cap})")
    return Row(CONDITIONS[_CAP], facts.merges_24h < cap, f"{facts.merges_24h} of {cap}")


def _taken_over_row(facts: PullFacts, kind: Kind | None) -> Row:
    if NEEDS_HUMAN in facts.labels:
        return Row(CONDITIONS[_TAKEN_OVER], False, f"the pull request is labelled {NEEDS_HUMAN}")
    # A revert's issue always carries it (verify hands the regression to a person); only the revert
    # pull request's own label stops the merger then.
    if kind != "revert" and NEEDS_HUMAN in facts.issue_labels:
        return Row(CONDITIONS[_TAKEN_OVER], False, f"issue #{facts.issue} is labelled {NEEDS_HUMAN}")
    return Row(CONDITIONS[_TAKEN_OVER], True, f"no {NEEDS_HUMAN} label")


def evaluate(facts: PullFacts, mode: str, app_login: str, limits: Limits) -> Verdict:
    """The verdict on `facts`: pure, no I/O. Every row is computed, in the spec's order."""
    kind = _kind(facts)
    rows = [
        _mode_row(mode),
        _pull_row(facts, app_login, kind),
        _approval_row(facts),
        _checks_row(facts),
        _fence_row(facts),
        _up_to_date_row(facts),
        _pages_row(facts, kind),
        _cap_row(facts, kind, limits),
        _taken_over_row(facts, kind),
    ]
    failed = [i for i, row in enumerate(rows) if not row.ok]
    if not failed:
        return Verdict("merge", rows)
    if failed == [_UP_TO_DATE]:
        return Verdict("update-branch", rows)
    return Verdict("block", rows)


# --- the shadow comment ---------------------------------------------------------------------------


def _value(text: str) -> str:
    """A table cell from text that may hold names the pull request chose: one line, no marker, no
    mention, pipes escaped, nothing shaped like a secret."""
    return WITHHELD if secret_shape(text) else _cell(_line(text, MAX_VALUE))


def _headline(verdict: Verdict) -> str:
    if verdict.action == "merge":
        return "**Would merge.** In shadow mode the owner merges or closes."
    if verdict.action == "update-branch":
        return (
            f"**Would update the branch** with {MAIN} and stop; Check and the review would then run again "
            "on the new head. In shadow mode the owner updates it."
        )
    return "**Would block: " + ", ".join(row.condition for row in verdict.failed) + ".**"


def render_shadow(verdict: Verdict, head_sha: str) -> str:
    """The shadow comment: the condition table, the verdict and the hidden verdict marker (`merge`
    only when it would merge this head; an update of the branch is a `block` of this head)."""
    if not _SHA.fullmatch(head_sha):
        raise ValueError("the head must be a commit SHA")
    lines = [
        f"The factory's merger, in shadow mode, on `{_short(head_sha)}`:",
        "",
        "| # | Condition | | Value |",
        "|---|---|---|---|",
    ]
    for number, row in enumerate(verdict.rows, 1):
        lines.append(f"| {number} | {row.condition} | {'✓' if row.ok else '✗'} | {_value(row.value)} |")
    marker = "merge" if verdict.action == "merge" else "block"
    lines += ["", _headline(verdict), "", SENTINEL, f"<!-- factory:verdict={marker} sha={head_sha} -->"]
    return "\n".join(lines) + "\n"


def summary(facts: PullFacts, verdict: Verdict) -> str:
    """The verdict for the job's log."""
    lines = [f"#{facts.number}: {verdict.action} ({_short(facts.head_sha)})"]
    for number, row in enumerate(verdict.rows, 1):
        lines.append(f"  {'ok' if row.ok else 'NO'} {number}. {row.condition}: {_value(row.value)}")
    return "\n".join(lines)


# --- reading GitHub and the main checkout -----------------------------------------------------------


def _parse(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def _reviews(gh: Any, api: str, pr: int) -> list[tuple[str, str, str]]:
    items = [r for r in gh.paginate(f"{api}/pulls/{pr}/reviews", {"per_page": PER_PAGE}) if isinstance(r, dict)]
    items.sort(key=lambda r: (str(r.get("submitted_at") or ""), r["id"] if isinstance(r.get("id"), int) else 0))
    return [(_login(r), str(r.get("state") or ""), str(r.get("commit_id") or "")) for r in items]


def required_checks(gh: Any, api: str) -> list[tuple[str, int | None]]:
    """(context, integration id) of every required status check in the rules that apply to main,
    in order, without repeats. The rules endpoint needs only metadata: read."""
    found: dict[tuple[str, int | None], None] = {}
    for rule in gh.paginate(f"{api}/rules/branches/{MAIN}", {"per_page": PER_PAGE}):
        if not isinstance(rule, dict) or rule.get("type") != "required_status_checks":
            continue
        for check in (rule.get("parameters") or {}).get("required_status_checks") or []:
            context = check.get("context") if isinstance(check, dict) else None
            if isinstance(context, str) and context:
                integration = check.get("integration_id")
                found[(context, integration if isinstance(integration, int) else None)] = None
    return list(found)


def _conclusion(runs: Sequence[Mapping[str, Any]]) -> str:
    """Success only when every run of the check on the head completed with success."""
    if not runs:
        return "missing"
    for run in runs:
        if run.get("status") != "completed":
            return str(run.get("status") or "pending")
    for run in runs:
        if run.get("conclusion") != "success":
            return str(run.get("conclusion") or "unknown")
    return "success"


def check_conclusions(gh: Any, api: str, head: str, required: Sequence[tuple[str, int | None]]) -> dict[str, str]:
    """Each required check's conclusion on `head`, from the latest check runs (Check runs for the push
    and for the pull request, so a name may have several runs: all of them must succeed). A required
    check bound to an integration counts only that integration's runs."""
    runs: list[dict[str, Any]] = []
    for page in range(1, MAX_PAGES + 1):
        data = gh.request(
            "GET", f"{api}/commits/{head}/check-runs", params={"filter": "latest", "per_page": PER_PAGE, "page": page}
        ) or {}
        batch = [run for run in data.get("check_runs") or [] if isinstance(run, dict)]
        runs += batch
        if len(batch) < PER_PAGE:
            break
    integrations: dict[str, set[int | None]] = {}
    for context, integration in required:
        integrations.setdefault(context, set()).add(integration)
    result: dict[str, str] = {}
    for context, allowed in integrations.items():
        matching = [
            run
            for run in runs
            if run.get("name") == context
            and (None in allowed or (run.get("app") or {}).get("id") in allowed)
        ]
        result[context] = _conclusion(matching)
    return result


def _compare(gh: Any, api: str, head: str) -> tuple[bool, str]:
    """(behind main, the merge base of main and the head)."""
    data = gh.request("GET", f"{api}/compare/{MAIN}...{head}") or {}
    behind = data.get("behind_by")
    base = str((data.get("merge_base_commit") or {}).get("sha") or "")
    if isinstance(behind, bool) or not isinstance(behind, int) or not _SHA.fullmatch(base):
        raise GitHubError(0, f"no comparison of {MAIN} and {_short(head)}")
    return behind > 0, base


def recompute_fence(repo: Path, pr: int, base: str, head: str, config: Config) -> FenceReport:
    """The fence over base..head, from the main checkout: the head is fetched into a private ref (its
    ancestors, the merge base among them, come with it) and read with git only."""
    git(repo, "fetch", "--no-tags", "origin", f"+refs/pull/{pr}/head:{HEAD_REF}")
    fetched = git(repo, "rev-parse", "--verify", f"{HEAD_REF}^{{commit}}")
    if fetched != head:
        raise FixError(f"#{pr} moved to {_short(fetched)} since GitHub reported {_short(head)}; a later run decides")
    return check_fence(repo, base, head, config.fence, config.limits)


def pages_conclusion(gh: Any) -> str:
    """The conclusion of the last Pages run on main, its status while it runs, or "none"."""
    try:
        run = ci_facts.last_pages_run(gh)
    except GitHubError as error:
        if error.status != 0:
            raise
        return "none"
    return str(run.get("conclusion") or run.get("status") or "unknown")


def merges_in_window(gh: Any, api: str, app_login: str, now: datetime | None = None) -> int:
    """Factory pull requests the App merged in the 24 hours before `now`, reverts excluded. A pull
    request merged in the window was updated in it, so `since` bounds the listing."""
    since = (now or datetime.now(timezone.utc)) - WINDOW
    items = gh.paginate(
        f"{api}/issues",
        {"state": "closed", "labels": FACTORY, "since": since.strftime("%Y-%m-%dT%H:%M:%SZ"), "per_page": PER_PAGE},
    )
    count = 0
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("pull_request"), dict) or REVERT in _labels(item):
            continue
        listed = item["pull_request"].get("merged_at", "")
        if listed is None or (listed and _parse(listed) < since):
            continue  # closed unmerged, or merged before the window
        pull = gh.request("GET", f"{api}/pulls/{item['number']}") or {}
        merged_at = pull.get("merged_at")
        if not merged_at or _parse(merged_at) < since or REVERT in _labels(pull):
            continue
        if (pull.get("merged_by") or {}).get("login") == app_login:
            count += 1
    return count


def verdict_posted(gh: Any, api: str, pr: int, head: str, trusted: frozenset[str]) -> bool:
    """Whether a trusted comment's marker block already carries a verdict for `head` (R48(c))."""
    allowed = {login.lower() for login in trusted}
    for comment in gh.paginate(f"{api}/issues/{pr}/comments", {"per_page": PER_PAGE}):
        if not isinstance(comment, dict) or _login(comment).lower() not in allowed:
            continue
        if any(sha == head for _, sha in _VERDICT.findall(_marker_block(str(comment.get("body") or "")))):
            return True
    return False


def gather(
    gh: Any,
    repo: Path,
    pr: int,
    config: Config,
    *,
    app_slug: str | None = None,
    now: datetime | None = None,
) -> PullFacts:
    """Everything evaluate needs about pull request `pr`. Reads GitHub only; the fence runs on the
    main checkout `repo` over git objects. Raises on any failure, so that no verdict is ever based on
    facts that could not be read (a later run decides)."""
    slug = os.environ.get("FACTORY_APP_SLUG", "") if app_slug is None else app_slug
    app_login = f"{slug}[bot]" if slug else ""
    name = config.site.repo
    api = f"/repos/{name}"
    pull = gh.request("GET", f"{api}/pulls/{pr}") or {}
    head_data = pull.get("head") or {}
    head = str(head_data.get("sha") or "")
    if not _SHA.fullmatch(head):
        raise GitHubError(0, f"#{pr} names no head commit")
    labels = frozenset(label for label in _labels(pull) if isinstance(label, str))
    ref = str(head_data.get("ref") or "")
    branch = _FIX_BRANCH.fullmatch(ref) or _REVERT_BRANCH.fullmatch(ref)
    issue = int(branch.group(1)) if branch else None
    issue_labels: frozenset[str] = frozenset()
    issue_open = False
    if issue is not None:
        data = gh.request("GET", f"{api}/issues/{issue}") or {}
        issue_open = data.get("state") == "open" and "pull_request" not in data
        issue_labels = frozenset(label for label in _labels(data) if isinstance(label, str))
    required = required_checks(gh, api)
    behind, base = _compare(gh, api, head)
    body = str(pull.get("body") or "")
    return PullFacts(
        number=pr,
        author=_login(pull),
        labels=labels,
        head_ref=ref,
        same_repo=(head_data.get("repo") or {}).get("full_name") == name,
        draft=bool(pull.get("draft")),
        head_sha=head,
        issue=issue,
        issue_labels=issue_labels,
        reviews=_reviews(gh, api, pr),
        checks=check_conclusions(gh, api, head, required),
        required=list(dict.fromkeys(context for context, _ in required)),
        fence=recompute_fence(repo, pr, base, head, config),
        behind=behind,
        pages_conclusion=pages_conclusion(gh),
        merges_24h=merges_in_window(gh, api, app_login, now),
        title=str(pull.get("title") or ""),
        # Read here, trusted only by evaluate: when the App opened the pull request and it names the head.
        body_head=head_in(body),
        state=str(pull.get("state") or ""),
        base_ref=str((pull.get("base") or {}).get("ref") or ""),
        issue_open=issue_open,
        verdict_posted=verdict_posted(gh, api, pr, head, config.trusted(slug or None)),
    )


# --- acting -------------------------------------------------------------------------------------------


def _delete_branch(gh: Any, api: str, ref: str) -> None:
    try:
        gh.request("DELETE", f"{api}/git/refs/heads/{urllib.parse.quote(ref, safe='/')}")
    except GitHubError as error:
        if error.status not in (404, 422):  # already deleted
            raise


def _shadow(gh: Any, api: str, facts: PullFacts, verdict: Verdict) -> str:
    if facts.verdict_posted:
        print(f"#{facts.number}: a verdict on {_short(facts.head_sha)} was already posted; nothing to add")
        return "unchanged"
    if not reviewed_head(facts):
        if MERGE_READY in facts.labels:  # from an older head
            _remove_label(gh, api, facts.number, MERGE_READY)
        print(f"#{facts.number}: {REVIEWER} has not reviewed {_short(facts.head_sha)} yet; nothing said")
        return "waiting"
    _comment(gh, api, facts.number, render_shadow(verdict, facts.head_sha))
    if verdict.action == "merge":
        if MERGE_READY not in facts.labels:
            gh.request("POST", f"{api}/issues/{facts.number}/labels", {"labels": [MERGE_READY]})
    elif MERGE_READY in facts.labels:
        _remove_label(gh, api, facts.number, MERGE_READY)
    return "commented"


def _merge(gh: Any, api: str, facts: PullFacts, config: Config) -> str:
    issue = facts.issue
    if issue is None or _kind(facts) is None:
        raise ValueError(f"#{facts.number} links no issue; refusing to merge")
    result = gh.request(
        "PUT",
        f"{api}/pulls/{facts.number}/merge",
        {
            "merge_method": "squash",
            "sha": facts.head_sha,
            "commit_title": safe_title(facts.title, issue),
            "commit_message": f"Refs #{issue}",
        },
    ) or {}
    merged = str(result.get("sha") or "")
    if result.get("merged") is not True or not _SHA.fullmatch(merged):
        reason = _line(str(result.get("message") or "no reason given"), 200)
        raise GitHubError(0, f"#{facts.number} was not merged: {reason}")
    print(f"#{facts.number}: squash-merged {_short(facts.head_sha)} as {_short(merged)}")
    revert = _kind(facts) == "revert"
    if not revert:
        # What verify needs first: the label and the marker of the merge commit.
        gh.request("POST", f"{api}/issues/{issue}/labels", {"labels": [MERGED]})
        _remove_label(gh, api, issue, FIXING)
        _comment(
            gh, api, issue,
            f"Merged by the factory in #{facts.number} as `{_short(merged)}`. QA verifies the fix on the live "
            f"site once Pages has deployed it; the issue closes only then.\n\n{SENTINEL}\n"
            f"<!-- factory:merged sha={merged} pr={facts.number} -->",
        )
    _delete_branch(gh, api, facts.head_ref)
    if not revert:
        promoted = dispatch._promote(gh, config.site.repo, config.limits)
        print(f"dispatch promoted: {' '.join(f'#{n}' for n in promoted) or 'nothing'}")
    return "merged"


def _auto(gh: Any, api: str, facts: PullFacts, verdict: Verdict, config: Config) -> str:
    if verdict.action == "update-branch":
        gh.request("PUT", f"{api}/pulls/{facts.number}/update-branch", {"expected_head_sha": facts.head_sha})
        print(f"#{facts.number}: updated {_short(facts.head_sha)} with {MAIN}; Check and the review run again")
        return "updated"
    if verdict.action != "merge":
        return "blocked"
    return _merge(gh, api, facts, config)


def act(gh: Any, facts: PullFacts, verdict: Verdict, mode: str, *, config: Config | None = None) -> str:
    """Do what the verdict says in `mode`. Returns what happened: "commented", "unchanged" or
    "waiting" (shadow), "merged", "updated" or "blocked" (auto), "off" (any other mode)."""
    config = config or load_config()
    api = f"/repos/{config.site.repo}"
    if mode == "shadow":
        return _shadow(gh, api, facts, verdict)
    if mode == "auto":
        return _auto(gh, api, facts, verdict, config)
    return "off"


# --- command line ---------------------------------------------------------------------------------------


def _positive(text: str) -> int:
    if not re.fullmatch(r"[1-9][0-9]*", text):
        raise argparse.ArgumentTypeError(f"not a pull request number: {text!r}")
    return int(text)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tools.factory.merge", description=__doc__.split("\n\n")[0])
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--pr", type=_positive, help="evaluate this pull request")
    target.add_argument("--sweep", action="store_true", help="evaluate every open pull request labelled factory")
    return parser


def open_factory_pulls(gh: Any, api: str) -> list[int]:
    pulls = gh.paginate(f"{api}/pulls", {"state": "open", "per_page": PER_PAGE})
    return sorted(
        p["number"] for p in pulls if isinstance(p, dict) and type(p.get("number")) is int and FACTORY in _labels(p)
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    mode = os.environ.get("FACTORY_MODE", "")
    if mode not in MODES:
        print(f"FACTORY_MODE is {mode or 'unset'}: the merger does nothing")
        return 0
    slug = os.environ.get("FACTORY_APP_SLUG", "")
    try:
        if not slug:
            raise ConfigError("FACTORY_APP_SLUG is not set: the App's pull requests cannot be told apart")
        config = load_config()
        gh = GitHub(config.site.repo)
        numbers = [args.pr] if args.pr else open_factory_pulls(gh, f"/repos/{config.site.repo}")
    except (GitHubError, ConfigError) as error:
        print(f"merge failed: {redact(str(error))}", file=sys.stderr)
        return 2
    print(f"mode {mode}: {len(numbers)} pull request(s)")
    failed = 0
    for number in numbers:
        try:
            facts = gather(gh, Path.cwd(), number, config, app_slug=slug)
            verdict = evaluate(facts, mode, f"{slug}[bot]", config.limits)
            print(summary(facts, verdict))
            print(f"#{number}: {act(gh, facts, verdict, mode, config=config)}")
        except (GitHubError, GitError, FixError, ConfigError, OSError, ValueError) as error:
            failed += 1
            print(f"#{number}: merge failed: {redact(str(error))}", file=sys.stderr)
    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
