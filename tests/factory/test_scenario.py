"""The factory's state machine end to end, on a GitHub that keeps its state (C1).

Each scenario walks the real code of every step (filing, promotion, the fixer's publish, the merger,
the sweep, live verification, the revert) against StatefulGitHub and temporary git repositories, and
checks the issue's labels after each step. Throughout, nothing past the fix (merged or regressed) is
ever handed back to the fixer: the App never adds factory:ready to such an issue.

Offline: no GitHub, no live site; the reviewer's approval and the checks are facts of the merger.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tests.factory.fakes import APP_EMAIL, APP_LOGIN, StatefulGitHub
from tests.factory.test_fix import FIX_FILES, REPORT, Recorder, World, fix_bundle, make_world, proof_for
from tools.factory import dispatch, fix, merge, verify
from tools.factory.config import load_config
from tools.factory.fence import FenceReport
from tools.factory.file_issues import file
from tools.factory.findings import Cell, Finding, Group
from tools.factory.fix import branch_name
from tools.factory.fix_publish import head_in, publish_fix
from tools.factory.merge import PullFacts, evaluate
from tools.factory.verify import Outcome

CONFIG = load_config()
REPO = CONFIG.site.repo
API = f"/repos/{REPO}"
OWNER = "MBratkowski"
REVIEWER = "github-actions[bot]"
TRUSTED = frozenset({OWNER, APP_LOGIN})
REQUIRED = ["Validate this commit / Tests (pytest, Playwright)"]
NOW = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
PAST_THE_FIX = frozenset({"factory:merged", "factory:regressed"})


def group(selector: str) -> Group:
    cell = Cell("landing", "webkit", 375, 900, "default")
    finding = Finding("overflow", cell, selector, "scrollWidth 640 > clientWidth 375", (0, 0, 640, 20))
    return Group("overflow", "landing", "default", selector, ("webkit",), (375,), (finding,))


GROUPS = {"A": group("div.term"), "B": group("pre.cmd"), "C": group("nav.toc")}
SEVERITY = {"A": "S2", "B": "S3", "C": "S4"}


def draft(name: str) -> dict[str, Any]:
    g = GROUPS[name]
    return {
        "title": f"The block {name} fits a 375 px viewport in WebKit",
        "severity": SEVERITY[name],
        "fingerprints": [g.fingerprint],
        "problem": f"Block {name} overflows.",
        "actual": "640 px wide",
        "expected": "No overflow",
        "why": "Visitors scroll sideways; WCAG 1.4.10.",
        "acceptance": ["The reproduction passes"],
        "sources": ["css/site.css"],
        "reproduction": {"kind": "detector", "groups": [g.to_json()], "script": None},
    }


@dataclass
class Factory:
    """One repository: GitHub's state, the git remote and the publish job's checkout of main."""

    hub: StatefulGitHub
    world: World
    tmp: Path
    recorder: Recorder

    def labels(self, number: int) -> set[str]:
        return self.hub.labels(number)

    def as_owner(self) -> None:
        self.hub.actor = OWNER

    def as_app(self) -> None:
        self.hub.actor = APP_LOGIN

    def file(self) -> list[int]:
        """QA's file job: the three drafts become issues, A (S2), B (S3) and C (S4)."""
        out = self.tmp / "probe"
        (out / "live").mkdir(parents=True)
        findings = {"target": "live", "groups": [g.to_json() for g in GROUPS.values()]}
        (out / "live" / "findings.json").write_text(json.dumps(findings))
        validated = {"drafts": [draft(n) for n in "ABC"], "suggestions": [], "dropped": []}
        self.as_app()
        return file(validated, out, self.hub, CONFIG, TRUSTED, False, self.recorder.publish)

    def promote(self) -> list[int]:
        self.as_app()
        return dispatch._promote(self.hub, REPO, CONFIG.limits)

    def publish(self, issue: int) -> int:
        """The fixer's attempt 1 for `issue`: its commits are pushed by publish as one commit."""
        title = self.hub.items[issue]["title"]
        branch = branch_name(issue, title)
        bundle, head, _ = fix_bundle(self.tmp / f"fix-{issue}", self.world, FIX_FILES, branch=branch)
        self.as_app()
        number = publish_fix(
            self.hub, self.world.main, issue, 1, bundle, REPORT, proof_for(self.world.base, head), {}, CONFIG,
            branch=branch, dispatch=self.recorder.dispatch, remote=str(self.world.bare),
            publish_evidence=self.recorder.publish, trusted=TRUSTED, email=APP_EMAIL,
        )
        assert number is not None
        return number

    def facts(self, pr: int, **changes: Any) -> PullFacts:
        """What the merger gathers, with the reviewer's approval of the head and green checks."""
        pull = self.hub.request("GET", f"{API}/pulls/{pr}")
        ref = pull["head"]["ref"]
        issue = int(ref.split("/", 1)[1].split("-", 1)[0])
        data = self.hub.request("GET", f"{API}/issues/{issue}")
        head = pull["head"]["sha"]
        values: dict[str, Any] = {
            "number": pr,
            "author": pull["user"]["login"],
            "labels": frozenset(label["name"] for label in pull["labels"]),
            "head_ref": ref,
            "same_repo": True,
            "draft": pull["draft"],
            "head_sha": head,
            "issue": issue,
            "issue_labels": frozenset(label["name"] for label in data["labels"]),
            "reviews": [(REVIEWER, "APPROVED", head)],
            "checks": {name: "success" for name in REQUIRED},
            "required": list(REQUIRED),
            "fence": FenceReport(True, [], 12, False),
            "behind": False,
            "pages_conclusion": "success",
            "merges_24h": 0,
            "title": pull["title"],
            "body_head": head_in(pull["body"] or ""),
            "state": pull["state"],
            "issue_open": data["state"] == "open",
            "maintainers": frozenset({OWNER}),
        }
        values.update(changes)
        return PullFacts(**values)

    def merger(self, pr: int, mode: str, **changes: Any) -> str:
        facts = self.facts(pr, **changes)
        verdict = evaluate(facts, mode, APP_LOGIN, CONFIG.limits)
        assert verdict.action == "merge", merge.summary(facts, verdict)
        self.as_app()
        return merge.act(self.hub, facts, verdict, mode, config=CONFIG)

    def verify(self, outcome: Outcome) -> list[int]:
        self.as_app()
        return verify.act(
            [outcome], self.hub, self.world.main, self.recorder.publish,
            remote=str(self.world.bare), trusted=TRUSTED, run_id="7", email=APP_EMAIL,
        )

    def nothing_past_the_fix_was_promoted(self) -> None:
        handed_back = [(n, sorted(labels)) for n, labels in self.hub.ready_by_app if labels & PAST_THE_FIX]
        assert handed_back == []


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def factory(tmp_path: Path) -> Factory:
    world = make_world(tmp_path / "git")

    def head_of(branch: str) -> str:
        return git("rev-parse", f"refs/heads/{branch}", cwd=world.bare)

    def merged(pull: dict[str, Any]) -> str:
        """GitHub's squash of the one commit publish pushed on main's tip: main moves to it."""
        sha = pull["head"]["sha"]
        git("update-ref", "refs/heads/main", sha, cwd=world.bare)
        return sha

    hub = StatefulGitHub(REPO, APP_LOGIN, head_of=head_of, merged=merged)
    return Factory(hub, world, tmp_path, Recorder())


def walk_to_a_merge(f: Factory, mode: str) -> tuple[int, str]:
    """File, promote, publish A and merge it: by the App in auto mode, by the owner in shadow mode
    (reconciled by the sweep). Returns A's pull request and the merge commit."""
    assert f.file() == [1, 2, 3]
    for number in (1, 2, 3):
        assert f.labels(number) == {"factory:found", SEVERITY["ABC"[number - 1]], "bug"}
    assert f.promote() == [1, 2]  # the two fixer slots: A and B
    assert f.labels(1) == {"factory:found", "factory:ready", "S2", "bug"}

    pr = f.publish(1)
    assert pr == 4
    # C1: factory:found goes when factory:fixing comes; factory:ready is gone too.
    assert f.labels(1) == {"factory:fixing", "S2", "bug"}
    assert f.labels(pr) == {"factory"}
    assert f.promote() == []  # A fixing and B ready hold both slots

    if mode == "auto":
        assert f.merger(pr, "auto") == "merged"
        sha = f.hub.items[pr]["pull"]["merge_commit_sha"]
    else:
        assert f.merger(pr, "shadow") == "commented"
        assert f.labels(pr) == {"factory", "factory:merge-ready"}
        assert f.labels(1) == {"factory:fixing", "S2", "bug"}
        f.as_owner()
        sha = f.hub.merge_by_hand(pr)
        f.as_app()
        done = merge.reconcile(f.hub, CONFIG, APP_LOGIN, TRUSTED, set(), now=NOW)
        assert done[0].startswith(f"#1: #{pr} was merged as {sha[:7]}")
    # Merged: the marker verify reads is on the issue, the slot is free, and promotion hands the
    # free slot to C, never back to A.
    assert f.labels(1) == {"factory:merged", "S2", "bug"}
    assert verify.merge_marker(f.hub.comments[1], TRUSTED) == (sha, pr)
    assert f.labels(3) == {"factory:found", "factory:ready", "S4", "bug"}
    assert f.labels(2) == {"factory:found", "factory:ready", "S3", "bug"}
    assert f.promote() == []
    f.nothing_past_the_fix_was_promoted()
    return pr, sha


def test_auto_merge_then_live_verification_closes_the_issue(tmp_path: Path) -> None:
    f = factory(tmp_path)
    pr, sha = walk_to_a_merge(f, "auto")
    assert f.hub.deleted_refs == [branch_name(1, f.hub.items[1]["title"])]

    # The owner hands the merged issue to the fixer by mistake: the guard declines it and the
    # decline job takes factory:ready off again.
    f.as_owner()
    f.hub.request("POST", f"{API}/issues/1/labels", {"labels": ["factory:ready"]})
    f.hub.actor = REVIEWER  # the decline job's GITHUB_TOKEN
    assert fix.decline(f.hub, 1, None, CONFIG, TRUSTED) == "merged"
    assert f.labels(1) == {"factory:merged", "S2", "bug"}

    assert f.verify(Outcome(1, "fixed", sha, "gone on the live site", None, GROUPS["A"].fingerprint, pr)) == []
    assert f.hub.items[1]["state"] == "closed"
    assert f.labels(1) == {"S2", "bug"}
    assert f.promote() == []
    f.nothing_past_the_fix_was_promoted()


def test_owner_merge_in_shadow_mode_is_reconciled_by_the_sweep(tmp_path: Path) -> None:
    f = factory(tmp_path)
    pr, sha = walk_to_a_merge(f, "shadow")
    # A second sweep finds nothing to do and promotes nothing.
    assert merge.reconcile(f.hub, CONFIG, APP_LOGIN, TRUSTED, set(), now=NOW) == []
    assert f.verify(Outcome(1, "fixed", sha, "gone on the live site", None, GROUPS["A"].fingerprint, pr)) == []
    assert f.hub.items[1]["state"] == "closed" and f.labels(1) == {"S2", "bug"}
    f.nothing_past_the_fix_was_promoted()


def test_a_regression_is_reverted_and_never_handed_back(tmp_path: Path) -> None:
    f = factory(tmp_path)
    pr, sha = walk_to_a_merge(f, "auto")

    assert f.verify(Outcome(1, "reproduces", sha, "still overflows", None, GROUPS["A"].fingerprint, pr)) == []
    assert f.labels(1) == {"factory:regressed", "factory:needs-human", "S2", "bug"}
    revert = max(f.hub.items)
    assert f.hub.items[revert]["pull"]["head"] == "revert/1"
    assert f.labels(revert) == {"factory", "factory:revert"}
    reverted = f.hub.items[revert]["pull"]["sha"]
    assert git("rev-parse", f"{reverted}^", cwd=f.world.bare) == sha

    # The owner frees both fixer slots: promotion still never picks the regressed issue.
    f.as_owner()
    f.hub.close(2)
    f.hub.close(3)
    assert f.promote() == []

    # The revert goes through the merger (the exact inverse of the merge, as the merger checks it).
    assert f.merger(revert, "auto", exact_revert=True, merged_sha=sha, body_head=None) == "merged"
    assert f.labels(1) == {"factory:regressed", "factory:needs-human", "S2", "bug"}
    assert f.hub.items[1]["state"] == "open"

    # Even with factory:needs-human removed by the owner, a regressed issue is never promoted.
    f.as_owner()
    f.hub.request("DELETE", f"{API}/issues/1/labels/factory%3Aneeds-human")
    assert f.promote() == []
    f.nothing_past_the_fix_was_promoted()
