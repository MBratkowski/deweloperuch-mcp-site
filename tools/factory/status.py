"""The pinned "Factory status" issue: counts, activity, and the shadow-mode agreement; and the QA
guard's daily cap on agent runs.

Command line:

  python -m tools.factory.status update    edit (or create and pin) the status issue (App token)
  python -m tools.factory.status qa-cap    print `agent=true` while fewer than
                                           `limits.qa_agent_runs_per_day` QA runs today (UTC) had a
                                           successful triage job, else `agent=false` (read token)
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from tools.factory.ci_facts import _pages
from tools.factory.config import MODES, load_config
from tools.factory.github import GitHub, GitHubError

MARKER = "<!-- factory:status -->"
TITLE = "Factory status"
WINDOW = 10
AGREE_NEEDED = 9
LABELS = (
    "factory:found",
    "factory:suggestion",
    "factory:ready",
    "factory:fixing",
    "factory:merged",
    "factory:regressed",
    "factory:flaky",
    "factory:needs-human",
)
AGENT_WORKFLOWS = ("factory-qa.yml", "factory-fix.yml", "factory-review.yml")
QA_WORKFLOW = "factory-qa.yml"
# The name of factory-qa.yml's agent job; a test keeps it equal to the workflow's.
TRIAGE_JOB = "Triage the findings (QA agent)"
_VERDICT = re.compile(r"<!-- factory:verdict=(merge|block) sha=\S+ -->")
_PIN = "mutation($id: ID!) { pinIssue(input: {issueId: $id}) { issue { id } } }"


@dataclass(frozen=True)
class Agreement:
    decided: int
    agree: int
    dangerous: list[int] = field(default_factory=list)


def agreement(prs: Sequence[dict]) -> Agreement:
    """Compare shadow verdicts with outcomes over the last 10 decided pull requests; every "would
    merge" the owner closed, among all decided pull requests, is dangerous (the spec's switch rule:
    no such pull request at all, M3)."""
    ordered = sorted(prs, key=lambda pr: pr["number"], reverse=True)
    recent = ordered[:WINDOW]
    agree = sum(1 for pr in recent if pr["merged"] == (pr["verdict"] == "merge"))
    dangerous = [pr["number"] for pr in ordered if pr["verdict"] == "merge" and not pr["merged"]]
    return Agreement(len(recent), agree, dangerous)


def ready_for_auto(a: Agreement) -> bool:
    return a.decided >= WINDOW and a.agree >= AGREE_NEEDED and not a.dangerous


def render_status(
    counts: Mapping[str, int],
    merges_7d: int,
    reverts_7d: int,
    flaky_open: int,
    runs_per_day: Mapping[str, int],
    a: Agreement,
    generated: str = "",
    mode: str = "",
) -> str:
    lines = [MARKER, "", "# Factory status", ""]
    if generated:
        lines += [f"Generated: {generated}", ""]
    if mode:
        shown = mode if mode in MODES else f"{mode} (not {', '.join(MODES)}: nothing runs)"
        lines += [f"Mode (`FACTORY_MODE`): {shown}", ""]
    lines += ["## Open issues by state", "", "| Label | Issues |", "|---|---|"]
    lines += [f"| `{name}` | {counts[name]} |" for name in sorted(counts)]
    lines += [
        "",
        "## Last 7 days",
        "",
        f"- Merges: {merges_7d}",
        f"- Reverts: {reverts_7d}",
        f"- Open flaky issues: {flaky_open}",
        "",
        "## Agent runs per day",
        "",
        "| Day (UTC) | Runs |",
        "|---|---|",
    ]
    lines += [f"| {day} | {runs_per_day[day]} |" for day in sorted(runs_per_day)]
    lines += [
        "",
        "## Shadow agreement",
        "",
        f"{a.agree} of the last {a.decided} decided pull requests agree.",
        "",
    ]
    # Among every decided pull request, not only the last 10: one is enough to keep the factory in shadow.
    if a.dangerous:
        lines.append("Would merge, but closed by the owner (dangerous): " + ", ".join(f"#{n}" for n in sorted(a.dangerous)))
    else:
        lines.append("Would merge, but closed by the owner (dangerous): none")
    lines += ["", f"Ready for auto: {'yes' if ready_for_auto(a) else 'no'}", ""]
    return "\n".join(lines)


def _trusted_login(item: dict, trusted: frozenset[str]) -> bool:
    login = (item.get("user") or {}).get("login") or ""
    return login.lower() in {t.lower() for t in trusted}


def _labels(item: dict) -> set[str]:
    return {label["name"] for label in item.get("labels", [])}


def _parse(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def _verdict(gh: Any, repo: str, number: int, trusted: frozenset[str]) -> str | None:
    comments = gh.paginate(f"/repos/{repo}/issues/{number}/comments")
    for comment in sorted(comments, key=lambda c: c.get("created_at") or "", reverse=True):
        if not _trusted_login(comment, trusted):
            continue
        match = _VERDICT.search(comment.get("body") or "")
        if match:
            return match.group(1)
    return None


def _factory_prs(gh: Any, repo: str) -> list[dict]:
    pulls = gh.paginate(f"/repos/{repo}/pulls", {"state": "closed", "per_page": 100})
    return [pr for pr in pulls if "factory" in _labels(pr)]


def decided_prs(gh: Any, repo: str, prs: Sequence[dict], trusted: frozenset[str]) -> list[dict]:
    decided = []
    for pr in prs:
        if "factory:revert" in _labels(pr):
            continue
        verdict = _verdict(gh, repo, pr["number"], trusted)
        if verdict is not None:
            decided.append({"number": pr["number"], "merged": bool(pr.get("merged_at")), "verdict": verdict})
    return decided


def _runs_since(gh: Any, repo: str, workflow: str, day: str) -> list[dict]:
    """Every run of `workflow` created on or after `day` (UTC), past the first 100 (M3)."""
    path = f"/repos/{repo}/actions/workflows/{workflow}/runs"
    return _pages(gh, path, "workflow_runs", {"created": f">={day}"})


def runs_per_day(gh: Any, repo: str, now: datetime) -> dict[str, int]:
    """Runs of the agent workflows per UTC day over the last 7 days; a run that ended `skipped` (the
    mode, the label or the guard ended it before any job ran) is no agent run (M3)."""
    days = [(now - timedelta(days=i)).date().isoformat() for i in range(7)]
    counts = dict.fromkeys(sorted(days), 0)
    for workflow in AGENT_WORKFLOWS:
        try:
            runs = _runs_since(gh, repo, workflow, min(days))
        except GitHubError as error:
            if error.status == 404:
                continue
            raise
        for run in runs:
            if run.get("conclusion") == "skipped":
                continue
            day = _parse(run["created_at"]).astimezone(timezone.utc).date().isoformat()
            if day in counts:
                counts[day] += 1
    return counts


def qa_agent_runs_today(gh: Any, repo: str, now: datetime, current: int | None = None) -> int:
    """QA runs created today (UTC), other than `current`, whose triage job succeeded in any attempt:
    the QA agent's runs that count against `limits.qa_agent_runs_per_day` (M11)."""
    today = now.astimezone(timezone.utc).date().isoformat()
    count = 0
    for run in _runs_since(gh, repo, QA_WORKFLOW, today):
        if run.get("id") == current:
            continue
        if _parse(run["created_at"]).astimezone(timezone.utc).date().isoformat() != today:
            continue
        jobs = _pages(gh, f"/repos/{repo}/actions/runs/{run['id']}/jobs", "jobs", {"filter": "all"})
        if any(job.get("name") == TRIAGE_JOB and job.get("conclusion") == "success" for job in jobs):
            count += 1
    return count


def gather(gh: Any, repo: str, trusted: frozenset[str], now: datetime) -> dict:
    issues = [
        i
        for i in gh.paginate(f"/repos/{repo}/issues", {"state": "open", "per_page": 100})
        if "pull_request" not in i and i.get("state", "open") == "open"
    ]
    counts = {name: sum(1 for i in issues if name in _labels(i)) for name in LABELS}
    prs = _factory_prs(gh, repo)
    since = now - timedelta(days=7)
    merged = [pr for pr in prs if pr.get("merged_at") and _parse(pr["merged_at"]) >= since]
    reverts = sum(1 for pr in merged if "factory:revert" in _labels(pr))
    return {
        "counts": counts,
        "merges_7d": len(merged) - reverts,
        "reverts_7d": reverts,
        "flaky_open": counts["factory:flaky"],
        "runs_per_day": runs_per_day(gh, repo, now),
        "agreement": agreement(decided_prs(gh, repo, prs, trusted)),
    }


def update(gh: Any, repo: str, trusted: frozenset[str], now: datetime, mode: str = "") -> int:
    """Edit the status issue's body, creating and pinning it once if it does not exist."""
    data = gather(gh, repo, trusted, now)
    body = render_status(
        data["counts"],
        data["merges_7d"],
        data["reverts_7d"],
        data["flaky_open"],
        data["runs_per_day"],
        data["agreement"],
        generated=now.date().isoformat(),
        mode=mode,
    )
    existing = [
        i
        for i in gh.paginate(f"/repos/{repo}/issues", {"state": "open", "per_page": 100})
        if "pull_request" not in i
        and i.get("state", "open") == "open"
        and MARKER in (i.get("body") or "")
        and _trusted_login(i, trusted)
    ]
    if existing:
        number = min(i["number"] for i in existing)
        gh.request("PATCH", f"/repos/{repo}/issues/{number}", {"body": body})
        return number
    created = gh.request("POST", f"/repos/{repo}/issues", {"title": TITLE, "body": body})
    gh.graphql(_PIN, {"id": created["node_id"]})
    return created["number"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def main(argv: Sequence[str] | None = None) -> int:
    import os

    parser = argparse.ArgumentParser(prog="python -m tools.factory.status")
    parser.add_argument("command", choices=["update", "qa-cap"])
    args = parser.parse_args(argv)
    config = load_config()
    repo = config.site.repo
    trusted = config.trusted(os.environ.get("FACTORY_APP_SLUG"))
    now = _now()
    try:
        if args.command == "qa-cap":
            current = os.environ.get("GITHUB_RUN_ID", "")
            used = qa_agent_runs_today(GitHub(repo), repo, now, int(current) if current.isdigit() else None)
            limit = config.limits.qa_agent_runs_per_day
            print(f"qa_agent_runs={used}")
            print(f"agent={'true' if used < limit else 'false'}")
            return 0
        number = update(GitHub(repo), repo, trusted, now, os.environ.get("FACTORY_MODE", ""))
    except GitHubError as error:
        print(f"status failed: {error}", file=sys.stderr)
        return 2
    print(f"status issue #{number}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
