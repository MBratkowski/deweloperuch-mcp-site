import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools.factory import ci_facts
from tools.factory.ci_facts import (
    deploy_stale,
    deployed_sha,
    failed_tests,
    find_flakes,
    last_pages_run,
    main_sha,
)
from tools.factory.findings import Group
from tools.factory.github import GitHubError

from .fakes import FakeGitHub

FIXTURES = Path(__file__).parent / "fixtures" / "ci"
REPO = "MBratkowski/deweloperuch-mcp-site"
API = f"/repos/{REPO}"
LIVE = "1f104d1bd39245054cf2f24f2d0729dee399428f"
MAIN = "8b6ec7beb1d3fc2e3c93126df1e68e695445cb9b"
TEST_ID = "tests/e2e/test_docs.py::test_toc_tracks_anchor_navigation_and_history[chromium-no-preference]"
NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)


def load(name: str) -> object:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def responses(main: str = MAIN) -> dict:
    return {
        ("GET", f"{API}/deployments"): load("deployments.json"),
        ("GET", f"{API}/deployments/7120815331/statuses"): load("deployment_statuses.json"),
        ("GET", f"{API}/commits/main"): {"sha": main},
        ("GET", f"{API}/actions/workflows/pages.yml/runs"): load("pages_runs.json"),
        ("GET", f"{API}/actions/workflows/check.yml/runs"): load("check_runs.json"),
        ("GET", f"{API}/actions/runs/38932423183/jobs"): load("jobs_38932423183.json"),
        ("GET", f"{API}/actions/runs/38932423200/jobs"): load("jobs_38932423200.json"),
        ("GET", f"{API}/actions/jobs/117079478213/logs"): (FIXTURES / "job_117079478213.log").read_bytes(),
    }


def test_deployed_sha_is_the_newest_successful_deployment() -> None:
    assert deployed_sha(FakeGitHub(responses())) == LIVE


def test_deployed_sha_skips_deployments_that_did_not_succeed() -> None:
    r = responses()
    r[("GET", f"{API}/deployments/7120815331/statuses")] = [{"state": "failure"}, {"state": "success"}]
    deployments = load("deployments.json")
    r[("GET", f"{API}/deployments")] = deployments
    r[("GET", f"{API}/deployments/{deployments[1]['id']}/statuses")] = [{"state": "success"}]
    assert deployed_sha(FakeGitHub(r)) == deployments[1]["sha"]


def test_main_sha_and_last_pages_run() -> None:
    gh = FakeGitHub(responses())
    assert main_sha(gh) == MAIN
    run = last_pages_run(gh)
    assert run["id"] == 38932423200 and run["conclusion"] == "failure"


def test_deploy_stale_reports_the_failed_pages_run() -> None:
    group = deploy_stale(FakeGitHub(responses()))
    assert group is not None
    assert (group.check, group.page, group.mode, group.selector) == ("deploy-stale", "site", "default", "")
    assert group.engines == () and group.widths == () and group.findings == ()
    assert group.note == (
        "live 1f104d1, main 8b6ec7b, Pages "
        f"https://github.com/{REPO}/actions/runs/38932423200 failed"
    )
    assert group.severity == "S1"


def test_deploy_stale_is_none_when_the_shas_match() -> None:
    assert deploy_stale(FakeGitHub(responses(main=LIVE))) is None


def test_deploy_stale_is_none_when_the_last_pages_run_did_not_fail() -> None:
    r = responses()
    runs = load("pages_runs.json")
    runs["workflow_runs"][0]["conclusion"] = "success"
    r[("GET", f"{API}/actions/workflows/pages.yml/runs")] = runs
    assert deploy_stale(FakeGitHub(r)) is None


def test_find_flakes_returns_the_failing_test_with_both_runs() -> None:
    groups = find_flakes(FakeGitHub(responses()), 14, now=NOW)
    assert len(groups) == 1
    group = groups[0]
    assert (group.check, group.page, group.mode) == ("flaky", "ci", "default")
    assert group.selector == TEST_ID
    assert group.engines == () and group.widths == ()
    assert "actions/runs/38932423200" in group.note
    assert "actions/runs/38932423183" in group.note
    assert Group.from_json(json.loads(json.dumps(group.to_json()))) == group


def test_find_flakes_ignores_runs_outside_the_window() -> None:
    later = datetime(2026, 11, 20, tzinfo=timezone.utc)
    assert find_flakes(FakeGitHub(responses()), 14, now=later) == []


def test_find_flakes_needs_both_outcomes_on_one_sha() -> None:
    r = responses()
    jobs = load("jobs_38932423183.json")
    for job in jobs["jobs"]:
        job["conclusion"] = "failure" if "Tests" in job["name"] else job["conclusion"]
    r[("GET", f"{API}/actions/runs/38932423183/jobs")] = jobs
    assert find_flakes(FakeGitHub(r), 14, now=NOW) == []


def test_failed_tests_reads_pytest_summary_lines() -> None:
    log = (FIXTURES / "job_117079478213.log").read_text(encoding="utf-8")
    assert failed_tests(log) == [TEST_ID]


def test_failed_tests_ignores_failed_inside_other_words_and_deduplicates() -> None:
    log = "\n".join(
        [
            "2026-10-16T16:33:07.1Z XFAILED tests/a.py::t",
            "2026-10-16T16:33:07.2Z NOTFAILED tests/b.py::t",
            "2026-10-16T16:33:07.3Z FAILED tests/c.py::t[x] - boom",
            "2026-10-16T16:33:07.4Z FAILED tests/d.py::t",
            "2026-10-16T16:33:07.5Z FAILED tests/c.py::t[x]",
            "FAILED other/e.py::t",
            "3 failed, 1 passed",
        ]
    )
    assert failed_tests(log) == ["tests/c.py::t[x]", "tests/d.py::t"]


def test_cli_writes_findings_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ci_facts, "GitHub", lambda repo, *a, **k: FakeGitHub(responses()))
    assert ci_facts.main(["--out", str(tmp_path)]) == 0
    data = json.loads((tmp_path / "findings.json").read_text(encoding="utf-8"))
    assert data["target"] == "ci"
    assert [g["check"] for g in data["groups"]] == ["deploy-stale", "flaky"]
    assert data["groups"][1]["selector"] == TEST_ID


def test_cli_exits_2_and_writes_nothing_on_github_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeGitHub({("GET", f"{API}/deployments"): GitHubError(403, "rate limit exceeded")})
    monkeypatch.setattr(ci_facts, "GitHub", lambda repo, *a, **k: fake)
    assert ci_facts.main(["--out", str(tmp_path)]) == 2
    assert "rate limit exceeded" in capsys.readouterr().err
    assert not (tmp_path / "findings.json").exists()


def test_find_flakes_reports_a_job_without_test_ids_by_its_name() -> None:
    name = "Validate this commit / Lighthouse (mobile)"
    base = {"status": "completed", "head_sha": MAIN, "created_at": "2026-10-16T16:30:00Z"}
    r = {
        ("GET", f"{API}/actions/workflows/check.yml/runs"): {
            "workflow_runs": [{**base, "id": 1, "html_url": "https://x/runs/1"}]
        },
        ("GET", f"{API}/actions/workflows/pages.yml/runs"): {
            "workflow_runs": [{**base, "id": 2, "html_url": "https://x/runs/2"}]
        },
        ("GET", f"{API}/actions/runs/1/jobs"): {
            "jobs": [{"id": 10, "name": name, "conclusion": "failure"}]
        },
        ("GET", f"{API}/actions/runs/2/jobs"): {
            "jobs": [{"id": 20, "name": name, "conclusion": "success"}]
        },
        ("GET", f"{API}/actions/jobs/10/logs"): b"2026-10-16T16:31:00Z score 0.4 below 0.9\n",
    }
    groups = find_flakes(FakeGitHub(r), 14, now=NOW)
    assert [(g.check, g.page, g.selector) for g in groups] == [("flaky", "ci", name)]
    assert "https://x/runs/1" in groups[0].note and "https://x/runs/2" in groups[0].note
