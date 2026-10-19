"""The dispatcher honours only trusted `factory:ready` labels and respects the WIP limit."""

import base64
import json

import pytest

from tools.factory import dispatch
from tools.factory.config import load_config
from tools.factory.dispatch import promote, ready_label_trusted
from tools.factory.findings import Group
from tools.factory.github import GitHubError
from tools.factory.issues import SENTINEL

from .fakes import FakeGitHub

TRUSTED = frozenset({"MBratkowski", "deweloperuch-factory[bot]"})
LIMITS = load_config().limits
REPO = load_config().site.repo
API = f"/repos/{REPO}"


def event(kind: str, login: str, label: str = "factory:ready") -> dict:
    return {"event": kind, "label": {"name": label}, "actor": {"login": login}}


def body_with(*checks: str) -> str:
    groups = [Group(c, "site", "default", "", (), (), (), note="n").to_json() for c in checks]
    encoded = base64.b64encode(json.dumps(groups).encode()).decode()
    return f"## Problem\n\nx\n\n{SENTINEL}\n<!-- factory:groups={encoded} -->\n"


def issue(number: int, *labels: str, created: str = "2026-10-01T00:00:00Z", body: str = "", **extra) -> dict:
    return {
        "number": number,
        "state": "open",
        "created_at": created,
        "body": body,
        "labels": [{"name": name} for name in labels],
        **extra,
    }


def test_stranger_label_is_ignored() -> None:
    assert not ready_label_trusted([event("labeled", "stranger")], TRUSTED)


def test_trusted_label_is_accepted_case_insensitively() -> None:
    assert ready_label_trusted([event("labeled", "mbratkowski")], TRUSTED)
    assert ready_label_trusted([event("labeled", "deweloperuch-factory[bot]")], TRUSTED)


def test_latest_labeled_event_decides() -> None:
    events = [event("labeled", "MBratkowski"), event("labeled", "stranger")]
    assert not ready_label_trusted(events, TRUSTED)
    assert ready_label_trusted(list(reversed(events)), TRUSTED)


def test_unlabeled_after_trusted_label_is_not_trusted() -> None:
    events = [event("labeled", "MBratkowski"), event("unlabeled", "MBratkowski")]
    assert not ready_label_trusted(events, TRUSTED)


def test_other_labels_and_no_events_are_ignored() -> None:
    assert not ready_label_trusted([], TRUSTED)
    assert not ready_label_trusted([event("labeled", "MBratkowski", "factory:found")], TRUSTED)
    assert not ready_label_trusted([{"event": "closed", "actor": {"login": "MBratkowski"}}], TRUSTED)


def test_promote_respects_wip() -> None:
    issues = [
        issue(1, "factory:fixing"),
        issue(2, "factory:found", "S3"),
        issue(3, "factory:found", "S3"),
    ]
    assert promote(issues, LIMITS, False) == [2]


def test_promote_never_negative_when_over_limit() -> None:
    issues = [issue(n, "factory:ready") for n in (1, 2, 3)] + [issue(4, "factory:found")]
    assert promote(issues, LIMITS, False) == []


def test_closed_issues_do_not_use_slots() -> None:
    issues = [issue(1, "factory:fixing", state="closed"), issue(2, "factory:found")]
    assert promote(issues, LIMITS, False) == [2]


def test_flaky_first_when_main_red() -> None:
    issues = [issue(1, "factory:found", "S1"), issue(2, "factory:found", "factory:flaky", "S4")]
    assert promote(issues, LIMITS, True)[0] == 2
    assert promote(issues, LIMITS, False)[0] == 1


def test_order_is_severity_then_oldest_with_missing_severity_last() -> None:
    issues = [
        issue(1, "factory:found"),
        issue(2, "factory:found", "S3", created="2026-10-03T00:00:00Z"),
        issue(3, "factory:found", "S2", created="2026-10-09T00:00:00Z"),
        issue(4, "factory:found", "S3", created="2026-10-02T00:00:00Z"),
    ]
    limits = type(LIMITS)(**{**vars(LIMITS), "fixes_in_progress": 4})
    assert promote(issues, limits, False) == [3, 4, 2, 1]


def test_deploy_stale_never_promoted() -> None:
    issues = [
        issue(1, "factory:found", "S1", body=body_with("deploy-stale")),
        issue(2, "factory:found", "S4", body=body_with("deploy-stale", "overflow")),
    ]
    assert promote(issues, LIMITS, False) == [2]


@pytest.mark.parametrize(
    "label",
    [
        "factory:needs-human",
        "factory:suggestion",
        "factory:ready",
        "factory:fixing",
        "factory:merged",
        "factory:regressed",
    ],
)
def test_blocked_labels_and_pull_requests_are_skipped(label: str) -> None:
    issues = [
        issue(1, "factory:found", label),
        issue(2, "factory:found", pull_request={}),
        issue(3, "S1"),
    ]
    limits = type(LIMITS)(**{**vars(LIMITS), "fixes_in_progress": 9})
    assert promote(issues, limits, False) == []


def test_a_merged_found_issue_is_never_handed_back() -> None:
    """C1: the reported shape, an issue merged while it still carried factory:found."""
    issues = [issue(1, "factory:found", "S2", "factory:merged"), issue(2, "factory:found", "S3")]
    assert promote(issues, LIMITS, False) == [2]


@pytest.mark.parametrize("parked", ["factory:needs-human", "factory:merged", "factory:regressed"])
@pytest.mark.parametrize("held", ["factory:ready", "factory:fixing"])
def test_a_parked_issue_frees_its_slot(held: str, parked: str) -> None:
    """R53c: an issue the factory stopped on, or whose fix is merged, holds no fixer slot even when
    factory:ready or factory:fixing remains."""
    issues = [
        issue(1, held, parked),
        issue(2, "factory:fixing"),
        issue(3, "factory:found", "S3"),
    ]
    assert promote(issues, LIMITS, False) == [3]
    assert not dispatch.in_progress(issues[0]) and dispatch.in_progress(issues[1])


def test_check_sender_exit_codes() -> None:
    path = f"{API}/issues/7/events"
    assert dispatch._check_sender(FakeGitHub({("GET", path): [event("labeled", "MBratkowski")]}), REPO, 7, TRUSTED) == 0
    assert dispatch._check_sender(FakeGitHub({("GET", path): [event("labeled", "x")]}), REPO, 7, TRUSTED) == 1


def test_main_check_sender_returns_2_on_github_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeGitHub({("GET", f"{API}/issues/7/events"): GitHubError(500, "boom")})
    monkeypatch.setattr(dispatch, "GitHub", lambda repo: fake)
    assert dispatch.main(["check-sender", "--issue", "7"]) == 2


def test_promote_command_labels_issues_and_reads_main_red() -> None:
    runs = {"workflow_runs": [{"conclusion": "failure", "html_url": "u"}]}
    fake = FakeGitHub(
        {
            ("GET", f"{API}/issues"): [
                issue(1, "factory:found", "S1"),
                issue(2, "factory:found", "factory:flaky", "S4"),
            ],
            ("GET", f"{API}/actions/workflows/pages.yml/runs"): runs,
        }
    )
    assert dispatch._promote(fake, REPO, LIMITS) == [2, 1]
    posts = [c for c in fake.calls if c[0] == "POST"]
    assert posts == [
        ("POST", f"{API}/issues/2/labels", {"labels": ["factory:ready"]}),
        ("POST", f"{API}/issues/1/labels", {"labels": ["factory:ready"]}),
    ]
