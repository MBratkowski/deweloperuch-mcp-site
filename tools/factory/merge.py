"""The merger (factory-merge.yml): deterministic policy as code, no language model.

`gather` reads one pull request from GitHub and recomputes the fence from the main checkout: the pull
request's head is fetched as git objects into a private ref and diffed against its merge base with
main, never checked out or run; for a revert it checks instead that the head is the exact inverse of
the merge named by the issue's trusted `factory:merged` marker (R49(2)). `evaluate` checks the facts
against the spec's eight conditions in order, plus R48(a) as a ninth row (the pull request, or a
fix's issue, labelled factory:needs-human). `act` does what the verdict says in the mode the owner set
(`FACTORY_MODE`):

- shadow: one comment per head with the condition table and `<!-- factory:verdict=merge|block
  sha=<head> -->`, and `factory:merge-ready` while it would merge (removed while it would not). Later
  runs on the same head edit that comment in place when the action or the set of failed rows changed,
  never add another (R48(c), R49(4)), and set the label again every run. Nothing is said before
  github-actions[bot] reviewed the head, and nothing is written on a pull request that is no longer
  open (R49(5)).
- auto: squash-merge with the App token for exactly the evaluated head (`sha`), with the pull
  request's title through safe_title and the message `Refs #N`, never the description (R48(d)); the
  issue then gets the `factory:merged` marker that verify reads, then moves from factory:fixing to
  factory:merged (R49(6)), the branch is deleted and dispatch promotes the next issues. A revert is
  merged and its branch deleted; its issue keeps factory:regressed and factory:needs-human. A branch
  that is behind main, with everything else passing, is updated instead (for the evaluated head only)
  and the merger stops: Check and the review run again on the new head.

The sweep also reconciles the App's fix pull requests closed in the last 7 days outside the merger
(R49(3)): a merge (by anyone) whose issue is still factory:fixing gets the marker, then
factory:merged, and dispatch promotes; a pull request closed unmerged hands its issue to a person.

Trust (R44, R48(b), R49): the approval counts only as the latest review by github-actions[bot], for
the current head, with no request for changes after it and none standing from the owner, a member or
a collaborator (strangers cannot block); a fix's body markers count only in the block our code
renders, when the App opened the pull request, and only while `factory:head` names the head or a
commit the head updates with main (GitHub's "Update branch", read with git: `branch_updated`).
Reverts carry no markers.

Command line: `python -m tools.factory.merge --pr N` or `--sweep` (every open pull request labelled
`factory`, then the reconciliation), from the root of the main checkout. FACTORY_MODE and
FACTORY_APP_SLUG come from the environment, the token from GH_TOKEN. Exit 0, or 2 when anything failed
(GitHub, git, configuration), with credentials redacted; a failure on one pull request does not stop
the sweep. A harmless race (the head moved since it was read, GitHub's 409 on the merge or 422 on the
update of the branch) is logged and is no failure: a later run decides.
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
from tools.factory.fix_publish import WITHHELD, _cell, _line, branch_updated, head_in, safe_title
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
from tools.factory.review_evidence import exact_revert
from tools.factory.secret_scan import secret_shape
from tools.factory.verify import _MARKER as _MERGED_MARKER
from tools.factory.verify import MERGED, REGRESSED, merge_marker

MODES = ("shadow", "auto")
MERGE_READY = "factory:merge-ready"
MAIN = "main"
HEAD_REF = "refs/factory/merge-head"
WINDOW = timedelta(hours=24)
RECONCILE_WINDOW = timedelta(days=7)
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
# Reviews GitHub associates with the repository this way may request changes that block (R49(6)).
MAINTAINERS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
_SHA = re.compile(r"[0-9a-f]{40}")
_VERDICT = re.compile(r"^<!-- factory:verdict=(merge|block) sha=([0-9a-f]{40}) -->$", re.MULTILINE)
WOULD_MERGE = "**Would merge.**"
WOULD_UPDATE = "**Would update the branch**"
WOULD_BLOCK = "**Would block: "
# A row of the shadow table and its headline, each at the start of a line: a value sits inside one
# table line (`_value`), so it can pose as neither.
_ROW = re.compile(r"^\| [0-9]+ \| ([^|\n]+?) \| (✓|✗) \|", re.MULTILINE)
_HEADLINE = re.compile(r"^\*\*Would (merge\.\*\*|update the branch\*\*|block: )", re.MULTILINE)

Action = Literal["merge", "block", "update-branch"]
Kind = Literal["fix", "revert"]


class Race(FixError):
    """A harmless race (R49(6)): the pull request moved since it was read, or GitHub refused the merge
    (409) or the update of the branch (422) for that reason. The run logs it and exits 0; a later run
    decides on the new state."""


@dataclass(frozen=True)
class PullFacts:
    """What the policy needs to know about one pull request. `reviews` are (login, state, commit_id)
    in the order they were submitted, and `maintainers` the logins among them whose reviews GitHub
    associates with the repository as OWNER, MEMBER or COLLABORATOR; `checks` maps each required check
    to its conclusion on the head ("success", a failed conclusion, a status while running, or
    "missing"). `updated_with_main`: the head is GitHub's update of the marked head `body_head` with
    main (`branch_updated`). For a revert, `merged_sha` is the merge the issue's trusted marker names
    and `exact_revert` whether the head is its exact inverse (`exact_reason` says why not; None: not
    checked). `verdict_id` and `verdict_body`: the App's shadow comment already posted for the head (R50(3)).
    `conflicted`: GitHub reports `mergeable_state` dirty, the branch conflicts with main (R50(1))."""

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
    updated_with_main: bool = False
    exact_revert: bool | None = None
    exact_reason: str = ""
    merged_sha: str | None = None
    maintainers: frozenset[str] = frozenset()
    verdict_id: int | None = None
    verdict_body: str = ""
    conflicted: bool = False


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


def _kind_of(labels: frozenset[str] | set[str], ref: str) -> Kind | None:
    """A fix comes from fix/<N>-… without factory:revert; a revert from revert/<N> with it."""
    revert = REVERT in labels
    if revert and _REVERT_BRANCH.fullmatch(ref):
        return "revert"
    if not revert and _FIX_BRANCH.fullmatch(ref):
        return "fix"
    return None


def _kind(facts: PullFacts) -> Kind | None:
    return _kind_of(facts.labels, facts.head_ref)


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
    # R44: the body's markers count only while factory:head names the head, or a commit the head
    # updates with main (R49(1)); a revert has none.
    updated = kind == "fix" and facts.body_head != facts.head_sha
    if updated and not (facts.body_head and facts.updated_with_main):
        return no("the body's factory:head marker names neither the head nor a commit the head updates with main")
    note = f" (the marked head {_short(facts.body_head or '')} updated with {MAIN})" if updated else ""
    return Row(CONDITIONS[_PULL], True, f"{facts.head_ref} by the App{note}; issue #{facts.issue} is {needed}")


def _standing_changes(reviews: Sequence[tuple[str, str, str]]) -> list[str]:
    """Logins whose latest approving, rejecting or dismissed review requests changes."""
    latest: dict[str, str] = {}
    for login, state, _ in reviews:
        if state in _DECISIVE_STATES:
            latest[login] = state
    return [login for login, state in latest.items() if state == "CHANGES_REQUESTED"]


def _may_block(facts: PullFacts, login: str) -> bool:
    """R49(6): the reviewer, the owner, members and collaborators; a stranger's request for changes
    does not block."""
    return login == REVIEWER or login in facts.maintainers


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
    later = [
        login for login, state, _ in facts.reviews[index + 1 :] if state == "CHANGES_REQUESTED" and _may_block(facts, login)
    ]
    if later:
        return no(f"changes requested after the approval by {', '.join(dict.fromkeys(later))}")
    standing = [login for login in _standing_changes(facts.reviews) if _may_block(facts, login)]
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


def _fence_row(facts: PullFacts, kind: Kind | None) -> Row:
    if kind == "revert":
        # R49(2): a revert removes the fix's own test, which the fence never lets through; what counts
        # is that it undoes exactly the merge the issue's trusted marker names.
        if facts.exact_revert is True:
            merged = _short(facts.merged_sha or "")
            return Row(CONDITIONS[_FENCE], True, f"a revert: the exact inverse of {merged} (the fence does not apply)")
        why = facts.exact_reason or "not checked as the exact inverse of the merge"
        return Row(CONDITIONS[_FENCE], False, f"a revert: {why}")
    report = facts.fence
    if not report.ok:
        return Row(CONDITIONS[_FENCE], False, "; ".join(report.reasons) or "not ok")
    if report.review:  # R48(a): a soft reason needs a person as much as a hard one
        return Row(CONDITIONS[_FENCE], False, "needs a human: " + "; ".join(report.review))
    return Row(CONDITIONS[_FENCE], True, f"hold ({report.changed_lines} changed lines)")


def _up_to_date_row(facts: PullFacts) -> Row:
    if facts.conflicted:
        return Row(CONDITIONS[_UP_TO_DATE], False, f"conflicts with {MAIN}")
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
        _fence_row(facts, kind),
        _up_to_date_row(facts),
        _pages_row(facts, kind),
        _cap_row(facts, kind, limits),
        _taken_over_row(facts, kind),
    ]
    failed = [i for i, row in enumerate(rows) if not row.ok]
    if not failed:
        return Verdict("merge", rows)
    if failed == [_UP_TO_DATE] and not facts.conflicted:
        return Verdict("update-branch", rows)
    return Verdict("block", rows)


# --- the shadow comment ---------------------------------------------------------------------------


def _value(text: str) -> str:
    """A table cell from text that may hold names the pull request chose: one line, no marker, no
    mention, pipes escaped, nothing shaped like a secret."""
    return WITHHELD if secret_shape(text) else _cell(_line(text, MAX_VALUE))


def _headline(verdict: Verdict) -> str:
    if verdict.action == "merge":
        return f"{WOULD_MERGE} In shadow mode the owner merges or closes."
    if verdict.action == "update-branch":
        return (
            f"{WOULD_UPDATE} with {MAIN} and stop; Check and the review would then run again "
            "on the new head. In shadow mode the owner updates it."
        )
    return WOULD_BLOCK + ", ".join(row.condition for row in verdict.failed) + ".**"


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


def _state(verdict: Verdict) -> tuple[str, frozenset[str]]:
    return verdict.action, frozenset(row.condition for row in verdict.failed)


def posted_state(body: str) -> tuple[str, frozenset[str]] | None:
    """(action, failed conditions) of a shadow comment render_shadow wrote, or None for any other text.
    The values in the table do not count: a later run edits the comment only when these change."""
    headline = _HEADLINE.search(body)
    if headline is None:
        return None
    action = {"merge.**": "merge", "update the branch**": "update-branch"}.get(headline.group(1), "block")
    return action, frozenset(condition for condition, mark in _ROW.findall(body) if mark == "✗")


def summary(facts: PullFacts, verdict: Verdict) -> str:
    """The verdict for the job's log."""
    lines = [f"#{facts.number}: {verdict.action} ({_short(facts.head_sha)})"]
    for number, row in enumerate(verdict.rows, 1):
        lines.append(f"  {'ok' if row.ok else 'NO'} {number}. {row.condition}: {_value(row.value)}")
    return "\n".join(lines)


# --- reading GitHub and the main checkout -----------------------------------------------------------


def _parse(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def _reviews(gh: Any, api: str, pr: int) -> tuple[list[tuple[str, str, str]], frozenset[str]]:
    """The reviews as (login, state, commit_id) in the order they were submitted, and the logins
    whose reviews GitHub associates with the repository as OWNER, MEMBER or COLLABORATOR."""
    items = [r for r in gh.paginate(f"{api}/pulls/{pr}/reviews", {"per_page": PER_PAGE}) if isinstance(r, dict)]
    items.sort(key=lambda r: (str(r.get("submitted_at") or ""), r["id"] if isinstance(r.get("id"), int) else 0))
    reviews = [(_login(r), str(r.get("state") or ""), str(r.get("commit_id") or "")) for r in items]
    maintainers = frozenset(_login(r) for r in items if r.get("author_association") in MAINTAINERS and _login(r))
    return reviews, maintainers


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
        raise Race(f"#{pr} moved to {_short(fetched)} since GitHub reported {_short(head)}; a later run decides")
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


def posted_verdict(gh: Any, api: str, pr: int, head: str, app_login: str) -> tuple[int, str] | None:
    """(id, body) of the newest comment authored by the App whose marker block carries a verdict for
    `head`, the one comment a later run edits in place (R48(c), R49(4)); None when there is none. An
    owner's comment with a verdict is never edited (R50(3)): the merger then posts its own, once."""
    allowed = {app_login.lower()} if app_login else set()
    found: tuple[tuple[str, int], str] | None = None
    for comment in gh.paginate(f"{api}/issues/{pr}/comments", {"per_page": PER_PAGE}):
        if not isinstance(comment, dict) or type(comment.get("id")) is not int:
            continue
        if _login(comment).lower() not in allowed:
            continue
        body = str(comment.get("body") or "")
        if not any(sha == head for _, sha in _VERDICT.findall(_marker_block(body))):
            continue
        key = (str(comment.get("created_at") or ""), comment["id"])
        if found is None or key > found[0]:
            found = (key, body)
    return (found[0][1], found[1]) if found else None


def _revert_check(
    gh: Any, api: str, repo: Path, issue: int, base: str, head: str, trusted: frozenset[str]
) -> tuple[str | None, bool, str]:
    """(merged, exact, why not) for a revert: the merge the issue's latest trusted `factory:merged`
    marker names, and whether base..head is its exact inverse, read from main's checkout (R49(2))."""
    marker = merge_marker(gh.paginate(f"{api}/issues/{issue}/comments", {"per_page": PER_PAGE}), trusted)
    merged = marker[0] if marker else None
    exact, why = exact_revert(repo, base, head, merged)
    return merged, exact, why


def gather(
    gh: Any,
    repo: Path,
    pr: int,
    config: Config,
    *,
    app_slug: str | None = None,
    now: datetime | None = None,
) -> PullFacts:
    """Everything evaluate needs about pull request `pr`. Reads GitHub only; the fence, the exact
    revert and GitHub's update of the branch are read on the main checkout `repo` over git objects.
    Raises on any failure, so that no verdict is ever based on facts that could not be read (a later
    run decides); Race when the head moved since GitHub reported it."""
    slug = os.environ.get("FACTORY_APP_SLUG", "") if app_slug is None else app_slug
    app_login = f"{slug}[bot]" if slug else ""
    trusted = config.trusted(slug or None)
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
    reviews, maintainers = _reviews(gh, api, pr)
    fence = recompute_fence(repo, pr, base, head, config)  # raises Race when the head moved
    kind = _kind_of(labels, ref)
    merged, exact, why = (None, None, "")
    if kind == "revert" and issue is not None:
        merged, exact, why = _revert_check(gh, api, repo, issue, base, head, trusted)
    # Read here, trusted only by evaluate: when the App opened the pull request and it names the head.
    body_head = head_in(body)
    updated = kind == "fix" and body_head is not None and body_head != head and branch_updated(repo, pr, head, body_head)
    posted = posted_verdict(gh, api, pr, head, app_login)
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
        reviews=reviews,
        checks=check_conclusions(gh, api, head, required),
        required=list(dict.fromkeys(context for context, _ in required)),
        fence=fence,
        behind=behind,
        pages_conclusion=pages_conclusion(gh),
        merges_24h=merges_in_window(gh, api, app_login, now),
        title=str(pull.get("title") or ""),
        body_head=body_head,
        state=str(pull.get("state") or ""),
        base_ref=str((pull.get("base") or {}).get("ref") or ""),
        issue_open=issue_open,
        updated_with_main=updated,
        exact_revert=exact,
        exact_reason=why,
        merged_sha=merged,
        maintainers=maintainers,
        verdict_id=posted[0] if posted else None,
        verdict_body=posted[1] if posted else "",
        conflicted=pull.get("mergeable_state") == "dirty",
    )


# --- acting -------------------------------------------------------------------------------------------


def _delete_branch(gh: Any, api: str, ref: str) -> None:
    try:
        gh.request("DELETE", f"{api}/git/refs/heads/{urllib.parse.quote(ref, safe='/')}")
    except GitHubError as error:
        if error.status not in (404, 422):  # already deleted
            raise


def _sync_label(gh: Any, api: str, facts: PullFacts, ready: bool) -> None:
    """factory:merge-ready while the merger would merge this head, and not otherwise."""
    if ready and MERGE_READY not in facts.labels:
        gh.request("POST", f"{api}/issues/{facts.number}/labels", {"labels": [MERGE_READY]})
    elif not ready and MERGE_READY in facts.labels:
        _remove_label(gh, api, facts.number, MERGE_READY)


def _shadow(gh: Any, api: str, facts: PullFacts, verdict: Verdict) -> str:
    if facts.state != "open":  # R49(5): the owner merged or closed it before this run read it
        print(f"#{facts.number}: the pull request is {facts.state or 'not open'}; nothing written")
        return "not open"
    if not reviewed_head(facts):
        _sync_label(gh, api, facts, False)  # a label left from an older head
        print(f"#{facts.number}: {REVIEWER} has not reviewed {_short(facts.head_sha)} yet; nothing said")
        return "waiting"
    if facts.verdict_id is None:
        _comment(gh, api, facts.number, render_shadow(verdict, facts.head_sha))
        outcome = "commented"
    elif posted_state(facts.verdict_body) != _state(verdict):
        # R49(4): the head's one comment says what holds now (a block cleared, a merge withdrawn).
        body = render_shadow(verdict, facts.head_sha)
        gh.request("PATCH", f"{api}/issues/comments/{facts.verdict_id}", {"body": body})
        outcome = "edited"
    else:
        print(f"#{facts.number}: the verdict on {_short(facts.head_sha)} stands; its comment is unchanged")
        outcome = "unchanged"
    _sync_label(gh, api, facts, verdict.action == "merge")
    return outcome


def _merged_text(pr: int, merged: str, how: str) -> str:
    return (
        f"{how} in #{pr} as `{_short(merged)}`. QA verifies the fix on the live site once Pages has "
        f"deployed it; the issue closes only then.\n\n{SENTINEL}\n<!-- factory:merged sha={merged} pr={pr} -->"
    )


def _merge(gh: Any, api: str, facts: PullFacts, config: Config) -> str:
    issue = facts.issue
    if issue is None or _kind(facts) is None:
        raise ValueError(f"#{facts.number} links no issue; refusing to merge")
    try:
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
    except GitHubError as error:
        if error.status == 409:
            raise Race(
                f"#{facts.number}: GitHub refused the merge of {_short(facts.head_sha)} (409: the head moved); "
                "a later run decides"
            ) from error
        raise
    merged = str(result.get("sha") or "")
    if result.get("merged") is not True or not _SHA.fullmatch(merged):
        reason = _line(str(result.get("message") or "no reason given"), 200)
        raise GitHubError(0, f"#{facts.number} was not merged: {reason}")
    print(f"#{facts.number}: squash-merged {_short(facts.head_sha)} as {_short(merged)}")
    revert = _kind(facts) == "revert"
    if not revert:
        # What verify needs, the marker before the label (R49(6)): verify takes up an issue labelled
        # factory:merged, and must find the merge commit then.
        _comment(gh, api, issue, _merged_text(facts.number, merged, "Merged by the factory"))
        gh.request("POST", f"{api}/issues/{issue}/labels", {"labels": [MERGED]})
        _remove_label(gh, api, issue, FIXING)
    _delete_branch(gh, api, facts.head_ref)
    if not revert:
        promoted = dispatch._promote(gh, config.site.repo, config.limits)
        print(f"dispatch promoted: {' '.join(f'#{n}' for n in promoted) or 'nothing'}")
    return "merged"


CONFLICT_COMMENT = "This pull request conflicts with main; a person needs to resolve it."


def _hand_over_conflict(gh: Any, api: str, facts: PullFacts) -> str:
    """R50(1): the branch conflicts with main, which only a person can resolve. Label the pull request
    and its issue factory:needs-human, take the issue off factory:fixing and say so once, in a fixed
    text. The labels come first: they stop every later run (the "Not taken over" row), so the comment
    is never repeated."""
    gh.request("POST", f"{api}/issues/{facts.number}/labels", {"labels": [NEEDS_HUMAN]})
    if facts.issue is not None:
        gh.request("POST", f"{api}/issues/{facts.issue}/labels", {"labels": [NEEDS_HUMAN]})
        if _kind(facts) == "fix":
            _remove_label(gh, api, facts.issue, FIXING)
    _comment(gh, api, facts.number, CONFLICT_COMMENT)
    print(f"#{facts.number}: conflicts with {MAIN}; labelled {NEEDS_HUMAN} for a person")
    return "handed over"


def _update_branch(gh: Any, api: str, facts: PullFacts) -> str:
    try:
        gh.request("PUT", f"{api}/pulls/{facts.number}/update-branch", {"expected_head_sha": facts.head_sha})
    except GitHubError as error:
        if error.status != 422:
            raise
        # 422 is both a head that moved (expected_head_sha) and a branch that cannot merge with main:
        # read the pull request again to tell them apart (R50(1)).
        now = gh.request("GET", f"{api}/pulls/{facts.number}") or {}
        if (now.get("head") or {}).get("sha") == facts.head_sha or now.get("mergeable_state") == "dirty":
            return _hand_over_conflict(gh, api, facts)
        raise Race(
            f"#{facts.number}: GitHub refused to update {_short(facts.head_sha)} (422: the head moved); "
            "a later run decides"
        ) from error
    print(f"#{facts.number}: updated {_short(facts.head_sha)} with {MAIN}; Check and the review run again")
    return "updated"


def _auto(gh: Any, api: str, facts: PullFacts, verdict: Verdict, config: Config) -> str:
    if facts.conflicted and verdict.rows[_PULL].ok and NEEDS_HUMAN not in facts.labels:
        return _hand_over_conflict(gh, api, facts)
    if verdict.action == "update-branch":
        return _update_branch(gh, api, facts)
    if verdict.action != "merge":
        return "blocked"
    return _merge(gh, api, facts, config)


def act(gh: Any, facts: PullFacts, verdict: Verdict, mode: str, *, config: Config | None = None) -> str:
    """Do what the verdict says in `mode`. Returns what happened: "commented", "edited",
    "unchanged", "waiting" or "not open" (shadow), "merged", "updated" or "blocked" (auto), "off" (any
    other mode). Raises Race for a harmless race."""
    config = config or load_config()
    api = f"/repos/{config.site.repo}"
    if mode == "shadow":
        return _shadow(gh, api, facts, verdict)
    if mode == "auto":
        return _auto(gh, api, facts, verdict, config)
    return "off"


# --- reconciling pull requests closed outside the merger ----------------------------------------------


def _marked_pulls(comments: Sequence[Any], trusted: frozenset[str]) -> set[int]:
    """The pull requests named by trusted comments' `factory:merged` markers (read as verify reads them)."""
    allowed = {login.lower() for login in trusted}
    return {
        int(match.group(2))
        for comment in comments
        if isinstance(comment, dict) and _login(comment).lower() in allowed
        for match in _MERGED_MARKER.finditer(str(comment.get("body") or ""))
    }


def _closed_fixes(gh: Any, name: str, app_login: str, since: datetime) -> list[tuple[dict[str, Any], int]]:
    """(pull request, issue) of the App's fix pull requests from this repository, labelled factory
    and not factory:revert, closed since `since`, merged ones first. A pull request closed in the
    window was updated in it."""
    api = f"/repos/{name}"
    items = gh.paginate(
        f"{api}/issues",
        {"state": "closed", "labels": FACTORY, "since": since.strftime("%Y-%m-%dT%H:%M:%SZ"), "per_page": PER_PAGE},
    )
    found: list[tuple[dict[str, Any], int]] = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("pull_request"), dict):
            continue
        if type(item.get("number")) is not int or REVERT in _labels(item):
            continue
        if not item.get("closed_at") or _parse(str(item["closed_at"])) < since:
            continue
        pull = gh.request("GET", f"{api}/pulls/{item['number']}") or {}
        head = pull.get("head") or {}
        labels = _labels(pull)
        branch = _FIX_BRANCH.fullmatch(str(head.get("ref") or ""))
        if (
            branch is None
            or _login(pull) != app_login
            or pull.get("state") != "closed"
            or FACTORY not in labels
            or REVERT in labels
            or (pull.get("base") or {}).get("ref") != MAIN  # R50(4): reconcile and verify assume main
            or (head.get("repo") or {}).get("full_name") != name
        ):
            continue
        found.append((pull, int(branch.group(1))))
    found.sort(key=lambda entry: (not entry[0].get("merged_at"), entry[0]["number"]))
    return found


def reconcile(
    gh: Any,
    config: Config,
    app_login: str,
    trusted: frozenset[str],
    open_issues: set[int],
    now: datetime | None = None,
) -> list[str]:
    """R49(3): the App's `fix/<N>-…` pull requests labelled factory and closed in the last 7 days,
    whose issue N is still open and labelled factory:fixing, closed outside the merger (the owner's
    merge in shadow mode, or an auto merge whose follow-up failed):

    - merged (by anyone): the `factory:merged` marker of the merge commit first, unless a trusted
      comment already names this pull request, then factory:merged in place of factory:fixing; then
      dispatch promotes the next issues;
    - closed unmerged: factory:needs-human in place of factory:fixing, unless an open factory pull
      request is the issue's later attempt (`open_issues`).

    Merges are reconciled first, so an earlier attempt closed unmerged never hands over an issue whose
    fix was merged. A second run finds nothing to do. Returns what was done, one line each."""
    api = f"/repos/{config.site.repo}"
    since = (now or datetime.now(timezone.utc)) - RECONCILE_WINDOW
    done: list[str] = []
    merged_any = False
    for pull, issue in _closed_fixes(gh, config.site.repo, app_login, since):
        number = pull["number"]
        data = gh.request("GET", f"{api}/issues/{issue}") or {}
        if data.get("state") != "open" or "pull_request" in data or FIXING not in _labels(data):
            continue
        if pull.get("merged_at"):
            sha = str(pull.get("merge_commit_sha") or "")
            if not _SHA.fullmatch(sha):
                raise GitHubError(0, f"#{number} was merged but names no merge commit")
            comments = gh.paginate(f"{api}/issues/{issue}/comments", {"per_page": PER_PAGE})
            if number in _marked_pulls(comments, trusted):
                # R50(2): an interrupted follow-up (the marker is posted, the labels are not). With no
                # open factory pull request for the issue (a re-attempt after a revert has one), finish it.
                if issue in open_issues:
                    continue
                gh.request("POST", f"{api}/issues/{issue}/labels", {"labels": [MERGED]})
                _remove_label(gh, api, issue, FIXING)
                merged_any = True
                done.append(f"#{issue}: #{number} was merged as {_short(sha)}; finished the interrupted follow-up")
                continue
            _comment(gh, api, issue, _merged_text(number, sha, "Merged outside the factory's merger"))
            gh.request("POST", f"{api}/issues/{issue}/labels", {"labels": [MERGED]})
            _remove_label(gh, api, issue, FIXING)
            merged_any = True
            done.append(f"#{issue}: #{number} was merged as {_short(sha)}; marked and labelled {MERGED}")
        elif issue not in open_issues:
            gh.request("POST", f"{api}/issues/{issue}/labels", {"labels": [NEEDS_HUMAN]})
            _remove_label(gh, api, issue, FIXING)
            done.append(f"#{issue}: #{number} was closed unmerged; labelled {NEEDS_HUMAN}")
    if merged_any:
        promoted = dispatch._promote(gh, config.site.repo, config.limits)
        done.append(f"dispatch promoted: {' '.join(f'#{n}' for n in promoted) or 'nothing'}")
    return done


# --- command line ---------------------------------------------------------------------------------------


def _positive(text: str) -> int:
    if not re.fullmatch(r"[1-9][0-9]*", text):
        raise argparse.ArgumentTypeError(f"not a pull request number: {text!r}")
    return int(text)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tools.factory.merge", description=__doc__.split("\n\n")[0])
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--pr", type=_positive, help="evaluate this pull request")
    target.add_argument(
        "--sweep", action="store_true",
        help="evaluate every open pull request labelled factory, then reconcile the ones closed outside the merger",
    )
    return parser


def _factory_pulls(pulls: Sequence[Any]) -> list[dict[str, Any]]:
    return sorted(
        (p for p in pulls if isinstance(p, dict) and type(p.get("number")) is int and FACTORY in _labels(p)),
        key=lambda p: p["number"],
    )


def _fix_issues(pulls: Sequence[dict[str, Any]]) -> set[int]:
    """The issues that open factory pull requests from fix/<N>-… branches are attempts for."""
    issues = set()
    for pull in pulls:
        branch = _FIX_BRANCH.fullmatch(str((pull.get("head") or {}).get("ref") or ""))
        if branch:
            issues.add(int(branch.group(1)))
    return issues


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
        pulls: list[dict[str, Any]] = []
        if args.sweep:
            pulls = _factory_pulls(gh.paginate(f"/repos/{config.site.repo}/pulls", {"state": "open", "per_page": PER_PAGE}))
        numbers = [args.pr] if args.pr else [p["number"] for p in pulls]
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
        except Race as race:  # R49(6): no failure, a later run decides
            print(f"#{number}: {redact(str(race))}")
        except (GitHubError, GitError, FixError, ConfigError, OSError, ValueError) as error:
            failed += 1
            print(f"#{number}: merge failed: {redact(str(error))}", file=sys.stderr)
    if args.sweep:
        try:
            for line in reconcile(gh, config, f"{slug}[bot]", config.trusted(slug), _fix_issues(pulls)):
                print(line)
        except (GitHubError, ValueError) as error:
            failed += 1
            print(f"reconcile failed: {redact(str(error))}", file=sys.stderr)
    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
