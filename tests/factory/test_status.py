"""The status issue: shadow agreement, the switch rule, and one pinned issue edited in place."""

from datetime import datetime, timezone

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


def test_agreement_uses_only_the_last_ten_by_number() -> None:
    old = [{"number": 1, "merged": False, "verdict": "merge"}]
    a = agreement(old + [
        {"number": n, "merged": True, "verdict": "merge"} for n in range(2, 12)
    ])
    assert (a.decided, a.agree, a.dangerous) == (10, 10, [])


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
