"""Process defects read from GitHub: a stale deployment and flaky jobs."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tools.factory.config import load_config
from tools.factory.findings import Group
from tools.factory.github import GitHub, GitHubError

PAGES_ENVIRONMENT = "github-pages"
WORKFLOWS = ("check.yml", "pages.yml")
_FAILED_TEST = re.compile(r"\bFAILED (tests/\S+)")


def _repo(gh: Any) -> str:
    return getattr(gh, "repo", None) or load_config().site.repo


def _api(gh: Any) -> str:
    return f"/repos/{_repo(gh)}"


def deployed_sha(gh: Any) -> str:
    """The commit of the newest github-pages deployment whose latest status is success."""
    api = _api(gh)
    deployments = gh.request(
        "GET", f"{api}/deployments", params={"environment": PAGES_ENVIRONMENT, "per_page": 30}
    )
    for deployment in deployments or []:
        statuses = gh.request("GET", f"{api}/deployments/{deployment['id']}/statuses") or []
        if statuses and statuses[0]["state"] == "success":
            return deployment["sha"]
    raise GitHubError(0, "no successful github-pages deployment found")


def main_sha(gh: Any) -> str:
    return gh.request("GET", f"{_api(gh)}/commits/main")["sha"]


def _runs(gh: Any, workflow: str, **params: Any) -> list[dict]:
    path = f"{_api(gh)}/actions/workflows/{workflow}/runs"
    data = gh.request("GET", path, params={"per_page": 100, **params}) or {}
    return data.get("workflow_runs", [])


def last_pages_run(gh: Any) -> dict:
    runs = _runs(gh, "pages.yml", branch="main", event="push")
    if not runs:
        raise GitHubError(0, "no Pages run found on main")
    return runs[0]


def deploy_stale(gh: Any) -> Group | None:
    live, main = deployed_sha(gh), main_sha(gh)
    if live == main:
        return None
    run = last_pages_run(gh)
    if run["conclusion"] != "failure":
        return None
    note = f"live {live[:7]}, main {main[:7]}, Pages {run['html_url']} failed"
    return Group("deploy-stale", "site", "default", "", (), (), (), note=note)


def failed_tests(log: str) -> list[str]:
    """Pytest node ids from `FAILED` summary lines, in order, without repeats."""
    return list(dict.fromkeys(_FAILED_TEST.findall(log)))


def _jobs(gh: Any, run_id: int) -> list[dict]:
    data = gh.request("GET", f"{_api(gh)}/actions/runs/{run_id}/jobs", params={"per_page": 100})
    return (data or {}).get("jobs", [])


def find_flakes(gh: Any, days: int, now: datetime | None = None) -> list[Group]:
    """Jobs that failed in one run and passed in another run of the same commit."""
    since = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    by_sha: dict[str, list[dict]] = {}
    for workflow in WORKFLOWS:
        for run in _runs(gh, workflow):
            created = datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
            if run["status"] == "completed" and created >= since:
                by_sha.setdefault(run["head_sha"], []).append(run)

    flakes: dict[str, Group] = {}
    for runs in by_sha.values():
        outcomes: dict[str, dict[str, list[tuple[dict, dict]]]] = {}
        for run in runs:
            for job in _jobs(gh, run["id"]):
                if job["conclusion"] in ("success", "failure"):
                    outcomes.setdefault(job["name"], {}).setdefault(job["conclusion"], []).append(
                        (run, job)
                    )
        for name, by_result in sorted(outcomes.items()):
            if "failure" not in by_result or "success" not in by_result:
                continue
            failed_run, failed_job = by_result["failure"][0]
            passed_run, _ = by_result["success"][0]
            log = gh.download(f"{_api(gh)}/actions/jobs/{failed_job['id']}/logs")
            selectors = failed_tests(log.decode("utf-8", errors="replace")) or [name]
            note = (
                f"{name} failed in {failed_run['html_url']} and passed in "
                f"{passed_run['html_url']} on {failed_run['head_sha'][:7]}"
            )
            for selector in selectors:
                if selector not in flakes:
                    flakes[selector] = Group("flaky", "ci", "default", selector, (), (), (), note=note)
    return list(flakes.values())


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tools.factory.ci_facts")
    parser.add_argument("--out", type=Path, required=True, help="directory for findings.json")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = load_config()
    try:
        gh = GitHub(config.site.repo)
        groups = [g for g in (deploy_stale(gh),) if g is not None]
        groups += find_flakes(gh, config.detectors.flaky_window_days)
    except GitHubError as error:
        print(f"ci facts failed: {error}", file=sys.stderr)
        return 2
    payload = {"target": "ci", "groups": [g.to_json() for g in groups]}
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "findings.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"{len(groups)} groups -> {args.out / 'findings.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
