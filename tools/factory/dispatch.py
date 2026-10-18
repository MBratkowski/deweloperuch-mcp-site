"""Hand filed issues to the fixer within the WIP limit and check who set `factory:ready`."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from typing import Any

from tools.factory.ci_facts import last_pages_run
from tools.factory.config import Limits, load_config
from tools.factory.github import GitHub, GitHubError
from tools.factory.issues import groups_in

READY = "factory:ready"
FOUND = "factory:found"
FIXING = "factory:fixing"
FLAKY = "factory:flaky"
_BLOCKING = frozenset({READY, FIXING, "factory:needs-human", "factory:suggestion"})
_SEVERITIES = ("S1", "S2", "S3", "S4")


def _labels(issue: dict) -> set[str]:
    return {label["name"] for label in issue.get("labels", [])}


def ready_label_trusted(events: Sequence[dict], trusted: frozenset[str]) -> bool:
    """True when the latest `factory:ready` labelling came from a trusted login and still stands."""
    allowed = {login.lower() for login in trusted}
    latest: dict | None = None
    for event in events:
        if (event.get("label") or {}).get("name") != READY:
            continue
        if event.get("event") == "labeled":
            latest = event
        elif event.get("event") == "unlabeled":
            latest = None
    if latest is None:
        return False
    return ((latest.get("actor") or {}).get("login") or "").lower() in allowed


def _only_deploy_stale(issue: dict) -> bool:
    try:
        groups = groups_in(issue.get("body") or "")
    except (ValueError, KeyError, TypeError):
        return False
    return bool(groups) and all(group.check == "deploy-stale" for group in groups)


def _rank(issue: dict, main_red: bool) -> tuple:
    labels = _labels(issue)
    severity = next((i for i, s in enumerate(_SEVERITIES) if s in labels), len(_SEVERITIES))
    flaky_first = 0 if (main_red and FLAKY in labels) else 1
    return (flaky_first, severity, issue.get("created_at") or "", issue["number"])


def promote(issues: Sequence[dict], limits: Limits, main_red: bool) -> list[int]:
    """Numbers of the issues to hand to the fixer, filling the free slots."""
    open_issues = [
        i for i in issues if i.get("state", "open") == "open" and "pull_request" not in i
    ]
    in_progress = sum(1 for i in open_issues if _labels(i) & {READY, FIXING})
    slots = max(0, limits.fixes_in_progress - in_progress)
    candidates = [
        i
        for i in open_issues
        if FOUND in _labels(i) and not _labels(i) & _BLOCKING and not _only_deploy_stale(i)
    ]
    candidates.sort(key=lambda i: _rank(i, main_red))
    return [i["number"] for i in candidates[:slots]]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tools.factory.dispatch")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check-sender", help="exit 0 when factory:ready came from a trusted sender")
    check.add_argument("--issue", type=int, required=True)
    commands.add_parser("promote", help="add factory:ready to issues that fit the WIP limit")
    return parser


def _check_sender(gh: Any, repo: str, issue: int, trusted: frozenset[str]) -> int:
    events = gh.paginate(f"/repos/{repo}/issues/{issue}/events")
    return 0 if ready_label_trusted(events, trusted) else 1


def _promote(gh: Any, repo: str, limits: Limits) -> list[int]:
    issues = gh.paginate(f"/repos/{repo}/issues", {"state": "open", "per_page": 100})
    main_red = last_pages_run(gh).get("conclusion") == "failure"
    numbers = promote(issues, limits, main_red)
    for number in numbers:
        gh.request("POST", f"/repos/{repo}/issues/{number}/labels", {"labels": [READY]})
    return numbers


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = load_config()
    repo = config.site.repo
    try:
        gh = GitHub(repo)
        if args.command == "check-sender":
            trusted = config.trusted(os.environ.get("FACTORY_APP_SLUG"))
            return _check_sender(gh, repo, args.issue, trusted)
        numbers = _promote(gh, repo, config.limits)
    except GitHubError as error:
        print(f"dispatch failed: {error}", file=sys.stderr)
        return 2
    print(" ".join(str(n) for n in numbers))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
