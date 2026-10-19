"""The status issue: shadow agreement, the switch rule, and one pinned issue edited in place."""

from datetime import datetime, timezone
from typing import Any

import pytest

from tools.factory import status
from tools.factory.config import load_config
from tools.factory.github import GitHubError
from tools.factory.status import Agreement, agreement, ready_for_auto, render_status

from .fakes import FakeGitHub

REPO = load_config().site.repo
API = f"/repos/{REPO}"
TRUSTED = frozenset({"MBratkowski", "deweloperuch-factory[bot]"})
NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)


def prs(*outcomes: tuple[bool, str]) -> list[dict]:
    return [
        {"number": n, "merged": merged, "verdict": verdict}
        for n, (merged, verdict) in enumerate(outcomes, start=1)
    ]


AGREE = [(True, "merge")] * 5 + [(False, "block")] * 5


def test_agreement_and_switch_rule() -> None:
    nine = agreement(prs(*AGREE[:-1], (True, "block")))
    assert (nine.decided, nine.agree, nine.dangerous) == (10, 9, [])
    assert ready_for_auto(nine)
    assert not ready_for_auto(agreement(prs(*AGREE[:-1], (False, "merge"))))
    assert not ready_for_auto(agreement(prs(*AGREE[:9])))


def test_agreement_classifies_each_outcome() -> None:
    a = agreement(prs((True, "merge"), (False, "block"), (False, "merge"), (True, "block")))
    assert (a.decided, a.agree, a.dangerous) == (4, 2, [3])


def test_agreement_counts_the_last_ten_and_every_dangerous_merge() -> None:
    """M3: agreement is over the last 10 decided pull requests by number; a "would merge" the owner
    closed is dangerous among all of them, so an old one still keeps the factory in shadow mode."""
    old = [{"number": 1, "merged": False, "verdict": "merge"}]
    a = agreement(old + [
        {"number": n, "merged": True, "verdict": "merge"} for n in range(2, 12)
    ])
    assert (a.decided, a.agree, a.dangerous) == (10, 10, [1])
    assert not ready_for_auto(a)
    assert ready_for_auto(agreement([{**old[0], "merged": True}] + [
        {"number": n, "merged": True, "verdict": "merge"} for n in range(2, 12)
    ]))


def test_ready_for_auto_boundaries() -> None:
    assert ready_for_auto(Agreement(10, 9, []))
    assert ready_for_auto(Agreement(10, 10, []))
    assert not ready_for_auto(Agreement(10, 8, []))
    assert not ready_for_auto(Agreement(9, 9, []))
    assert not ready_for_auto(Agreement(10, 10, [4]))


def test_render_status_is_deterministic_and_complete() -> None:
    a = Agreement(10, 9, [])
    args = ({"factory:found": 2, "factory:fixing": 1}, 3, 1, 2, {"2026-10-17": 4, "2026-10-18": 0}, a)
    text = render_status(*args, generated="2026-10-18")
    assert text == render_status(*args, generated="2026-10-18")
    assert text.startswith("<!-- factory:status -->")
    assert text.index("factory:fixing") < text.index("factory:found")
    assert "| `factory:found` | 2 |" in text
    assert "9 of the last 10 decided pull requests agree" in text
    assert "Ready for auto: yes" in text
    assert "| 2026-10-17 | 4 |" in text


def test_render_status_names_the_mode() -> None:
    assert "Mode (`FACTORY_MODE`): shadow" in render_status({}, 0, 0, 0, {}, Agreement(0, 0, []), mode="shadow")
    odd = render_status({}, 0, 0, 0, {}, Agreement(0, 0, []), mode="Shadow")
    assert "Shadow (not off, dry, shadow, auto: nothing runs)" in odd
    assert "Mode" not in render_status({}, 0, 0, 0, {}, Agreement(0, 0, []))


def test_render_status_lists_dangerous_and_says_no() -> None:
    text = render_status({}, 0, 0, 0, {}, Agreement(10, 9, [12, 5]))
    assert "#5, #12" in text
    assert "Ready for auto: no" in text


def pull(number: int, merged_at: str | None, *labels: str) -> dict:
    return {"number": number, "merged_at": merged_at, "labels": [{"name": n} for n in labels]}


def comment(login: str, body: str, created: str) -> dict:
    return {"user": {"login": login}, "body": body, "created_at": created}


def verdict(kind: str) -> str:
    return f"Table\n<!-- factory:verdict={kind} sha=abc123 -->"


def gh_for(issues: list[dict]) -> FakeGitHub:
    return FakeGitHub(
        {
            ("GET", f"{API}/issues"): issues,
            ("GET", f"{API}/pulls"): [
                pull(10, "2026-10-17T10:00:00Z", "factory"),
                pull(11, None, "factory"),
                pull(12, "2026-10-16T10:00:00Z", "factory", "factory:revert"),
                pull(13, "2026-09-01T10:00:00Z", "factory"),
                pull(14, "2026-10-17T10:00:00Z"),
            ],
            ("GET", f"{API}/issues/10/comments"): [
                comment("stranger", verdict("block"), "2026-10-17T11:00:00Z"),
                comment("MBratkowski", verdict("block"), "2026-10-17T09:00:00Z"),
                comment("deweloperuch-factory[bot]", verdict("merge"), "2026-10-17T09:30:00Z"),
            ],
            ("GET", f"{API}/issues/11/comments"): [
                comment("deweloperuch-factory[bot]", verdict("merge"), "2026-10-17T09:30:00Z")
            ],
            ("GET", f"{API}/issues/13/comments"): [comment("MBratkowski", "no marker", "2026-09-01T00:00:00Z")],
            ("GET", f"{API}/actions/workflows/factory-qa.yml/runs"): {
                "workflow_runs": [
                    {"created_at": "2026-10-18T01:00:00Z"},
                    {"created_at": "2026-10-18T02:00:00Z"},
                    {"created_at": "2026-09-01T02:00:00Z"},
                ]
            },
            ("GET", f"{API}/actions/workflows/factory-fix.yml/runs"): {
                "workflow_runs": [{"created_at": "2026-10-17T23:59:00Z"}]
            },
            ("GET", f"{API}/actions/workflows/factory-review.yml/runs"): GitHubError(404, "Not Found"),
        }
    )


def open_issue(number: int, *labels: str, body: str = "", login: str = "x", **extra) -> dict:
    return {
        "number": number,
        "state": "open",
        "body": body,
        "user": {"login": login},
        "labels": [{"name": n} for n in labels],
        **extra,
    }


def test_gather_collects_counts_prs_and_runs() -> None:
    issues = [
        open_issue(1, "factory:found", "factory:flaky"),
        open_issue(2, "factory:found"),
        open_issue(3, "factory:found", pull_request={}),
    ]
    data = status.gather(gh_for(issues), REPO, TRUSTED, NOW)
    assert data["counts"]["factory:found"] == 2
    assert data["counts"]["factory:fixing"] == 0
    assert data["flaky_open"] == 1
    assert (data["merges_7d"], data["reverts_7d"]) == (1, 1)
    assert data["runs_per_day"]["2026-10-18"] == 2
    assert data["runs_per_day"]["2026-10-17"] == 1
    assert len(data["runs_per_day"]) == 7
    a = data["agreement"]
    assert (a.decided, a.agree, a.dangerous) == (2, 1, [11])


def test_update_creates_pins_once_then_patches() -> None:
    fake = gh_for([open_issue(3, "factory:found", body="<!-- factory:status -->", login="stranger")])
    fake.responses[("POST", f"{API}/issues")] = {"number": 50, "node_id": "I_node"}
    assert status.update(fake, REPO, TRUSTED, NOW) == 50
    assert [c[0] for c in fake.calls if c[0] == "POST" and c[1] == f"{API}/issues"] == ["POST"]
    pin = [c for c in fake.calls if c[1] == "/graphql"]
    assert len(pin) == 1 and pin[0][2] == {"id": "I_node"}
    created = next(c for c in fake.calls if c[1] == f"{API}/issues" and c[0] == "POST")
    assert created[2]["title"] == "Factory status"
    assert "labels" not in created[2]

    existing = open_issue(
        51, body="<!-- factory:status -->\nold", login="deweloperuch-factory[bot]"
    )
    again = gh_for([existing])
    assert status.update(again, REPO, TRUSTED, NOW) == 51
    assert not [c for c in again.calls if c[0] == "POST"]
    patches = [c for c in again.calls if c[0] == "PATCH"]
    assert len(patches) == 1 and patches[0][1] == f"{API}/issues/51"
    assert patches[0][2]["body"].startswith("<!-- factory:status -->")


class PagedRuns(FakeGitHub):
    """Run listings answered page by page; any other path as FakeGitHub."""

    def __init__(self, runs: dict[str, list[dict]], responses: dict | None = None) -> None:
        super().__init__(responses or {})
        self.runs = runs

    def request(self, method: str, path: str, body: dict | None = None, params: dict | None = None) -> Any:
        if method == "GET" and path in self.runs:
            self.calls.append((method, path, params))
            page, size = (params or {}).get("page", 1), (params or {}).get("per_page", 30)
            return {"workflow_runs": self.runs[path][(page - 1) * size : page * size]}
        return super().request(method, path, body, params)


def runs_path(workflow: str) -> str:
    return f"{API}/actions/workflows/{workflow}/runs"


def test_runs_per_day_pages_past_a_hundred_and_skips_skipped_runs() -> None:
    """M3: a busy week has more than 100 runs, and a run that ended skipped ran no agent."""
    busy = [{"id": i, "created_at": "2026-10-18T03:00:00Z", "conclusion": "success"} for i in range(150)]
    skipped = [{"id": 900 + i, "created_at": "2026-10-18T04:00:00Z", "conclusion": "skipped"} for i in range(30)]
    fake = PagedRuns(
        {
            runs_path("factory-qa.yml"): busy + skipped,
            runs_path("factory-fix.yml"): [{"id": 1, "created_at": "2026-10-17T03:00:00Z", "conclusion": None}],
            runs_path("factory-review.yml"): [],
        }
    )
    counts = status.runs_per_day(fake, REPO, NOW)
    assert counts["2026-10-18"] == 150 and counts["2026-10-17"] == 1
    pages = [p["page"] for _, path, p in fake.calls if path == runs_path("factory-qa.yml")]
    assert pages == [1, 2]


def qa_run(run_id: int, created: str) -> dict:
    return {"id": run_id, "created_at": created, "conclusion": "success"}


def jobs(*conclusions: str | None) -> dict:
    return {
        "jobs": [{"name": status.TRIAGE_JOB, "conclusion": c, "run_attempt": i + 1} for i, c in enumerate(conclusions)]
        + [{"name": "Probe the live site", "conclusion": "success"}]
    }


def cap_github() -> PagedRuns:
    return PagedRuns(
        {
            runs_path("factory-qa.yml"): [
                qa_run(10, "2026-10-18T02:30:00Z"),  # nightly: the agent ran
                qa_run(11, "2026-10-18T06:00:00Z"),  # after a deploy: the agent failed, then a re-run passed
                qa_run(12, "2026-10-18T08:00:00Z"),  # the guard skipped the agent (the cap)
                qa_run(13, "2026-10-18T09:00:00Z"),  # the agent failed
                qa_run(14, "2026-10-18T12:00:00Z"),  # this run
                qa_run(9, "2026-10-17T23:59:00Z"),  # yesterday (UTC)
            ]
        },
        {
            ("GET", f"{API}/actions/runs/10/jobs"): jobs("success"),
            ("GET", f"{API}/actions/runs/11/jobs"): jobs("failure", "success"),
            ("GET", f"{API}/actions/runs/12/jobs"): jobs("skipped"),
            ("GET", f"{API}/actions/runs/13/jobs"): jobs("failure"),
            ("GET", f"{API}/actions/runs/14/jobs"): jobs("success"),
            ("GET", f"{API}/actions/runs/9/jobs"): jobs("success"),
        },
    )


def test_qa_agent_runs_today_counts_runs_whose_triage_succeeded() -> None:
    """M11: today's (UTC) QA runs, besides this one, whose triage job succeeded in some attempt."""
    fake = cap_github()
    assert status.qa_agent_runs_today(fake, REPO, NOW, current=14) == 2
    listed = [p for _, path, p in fake.calls if path == runs_path("factory-qa.yml")]
    assert listed[0]["created"] == ">=2026-10-18"
    assert ("GET", f"{API}/actions/runs/11/jobs", {"filter": "all", "per_page": 100, "page": 1}) in fake.calls
    assert status.qa_agent_runs_today(fake, REPO, NOW) == 3


@pytest.mark.parametrize(("earlier", "agent"), [(0, "true"), (1, "true"), (2, "false"), (3, "false")])
def test_cli_qa_cap(earlier: int, agent: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """M11: at `limits.qa_agent_runs_per_day` (2) earlier agent runs today, the guard skips the agent."""
    runs = [qa_run(20 + i, f"2026-10-18T0{i}:00:00Z") for i in range(earlier)] + [qa_run(30, "2026-10-18T11:00:00Z")]
    responses = {("GET", f"{API}/actions/runs/{20 + i}/jobs"): jobs("success") for i in range(earlier)}
    responses[("GET", f"{API}/actions/runs/30/jobs")] = jobs(None)  # this run: triage has not run yet
    fake = PagedRuns({runs_path("factory-qa.yml"): runs}, responses)
    monkeypatch.setattr(status, "GitHub", lambda repo, *a, **k: fake)
    monkeypatch.setattr(status, "_now", lambda: NOW)
    monkeypatch.setenv("GITHUB_RUN_ID", "30")
    assert status.main(["qa-cap"]) == 0
    assert capsys.readouterr().out.splitlines() == [f"qa_agent_runs={earlier}", f"agent={agent}"]
    assert not [c for c in fake.calls if c[0] != "GET"]
