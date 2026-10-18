"""Every draft's reproduction must exit 1 on the live site and on main; code disposes."""

import subprocess
from typing import Any

from tools.factory import check_drafts
from tools.factory.check_drafts import check
from tools.factory.findings import Cell, Finding, Group
from tools.factory.issues import load_drafts

LIVE = "https://live.test/"
MAIN = "http://127.0.0.1:1/"


def group(check_name: str = "overflow") -> Group:
    cell = Cell("landing", "webkit", 375, 800, "default")
    finding = Finding(check_name, cell, "div.term", "m", (0, 0, 1, 1))
    return Group(check_name, "landing", "default", "div.term", ("webkit",), (375,), (finding,))


def draft_dict(fp: str = "a" * 12, *, check_name: str = "overflow", script: str | None = None) -> dict[str, Any]:
    g = group(check_name)
    reproduction = (
        {"kind": "script", "groups": [], "script": script}
        if script
        else {"kind": "detector", "groups": [g.to_json()], "script": None}
    )
    return {
        "title": f"Draft {fp}",
        "severity": "S3",
        "fingerprints": [fp],
        "problem": "p",
        "actual": "a",
        "expected": "e",
        "why": "w",
        "acceptance": ["x"],
        "sources": [],
        "reproduction": reproduction,
    }


def drafts_of(*items: dict[str, Any]):
    drafts, _, _ = load_drafts({"drafts": list(items), "suggestions": [], "dropped": []})
    return drafts


def never_script(script: str, url: str) -> int:
    raise AssertionError("no script expected")


def by_url(live: int, main: int):
    return lambda _group, url: live if url == LIVE else main


def test_draft_survives_when_both_reproduce() -> None:
    drafts = drafts_of(draft_dict())
    kept, dropped = check(drafts, LIVE, MAIN, never_script, by_url(1, 1))
    assert kept == drafts and dropped == []


def test_draft_dropped_when_main_is_clean() -> None:
    kept, dropped = check(drafts_of(draft_dict()), LIVE, MAIN, never_script, by_url(1, 0))
    assert kept == []
    assert [(d.fingerprint, d.reason) for d in dropped] == [("a" * 12, "fixed on main, not deployed")]


def test_draft_dropped_when_live_is_clean() -> None:
    kept, dropped = check(drafts_of(draft_dict()), LIVE, MAIN, never_script, by_url(0, 1))
    assert kept == []
    assert dropped[0].reason == "does not reproduce on the live site"


def test_error_on_either_url_is_a_reproduction_error() -> None:
    for live, main in ((2, 1), (1, 2)):
        kept, dropped = check(drafts_of(draft_dict()), LIVE, MAIN, never_script, by_url(live, main))
        assert kept == [] and dropped[0].reason == "reproduction error"


def test_group_that_cannot_be_reproduced_is_a_reproduction_error() -> None:
    def refuse(_group: Group, _url: str) -> int:
        raise ValueError("flaky findings cannot be reproduced in a browser")

    kept, dropped = check(drafts_of(draft_dict()), LIVE, MAIN, never_script, refuse)
    assert kept == [] and dropped[0].reason == "reproduction error"


def test_script_draft_runs_the_script_on_both_urls() -> None:
    seen: list[tuple[str, str]] = []

    def run_script(script: str, url: str) -> int:
        seen.append((script, url))
        return 1

    drafts = drafts_of(draft_dict(script="factory-out/repros/m.py"))
    kept, _ = check(drafts, LIVE, MAIN, run_script, by_url(2, 2))
    assert kept == drafts
    assert seen == [("factory-out/repros/m.py", LIVE), ("factory-out/repros/m.py", MAIN)]


def test_ci_group_is_verified_by_its_fingerprint_not_a_browser() -> None:
    flaky = group("flaky")
    fp = flaky.fingerprint
    drafts = drafts_of(draft_dict(fp, check_name="flaky"))

    def no_browser(_group: Group, _url: str) -> int:
        raise AssertionError("a browser must not run")

    kept, _ = check(drafts, LIVE, MAIN, never_script, no_browser, ci_fingerprints={fp})
    assert kept == drafts
    kept, dropped = check(drafts, LIVE, MAIN, never_script, no_browser, ci_fingerprints=set())
    assert kept == [] and dropped[0].reason == "not present in this run's CI facts"


def test_run_script_uses_isolated_python_and_a_timeout(monkeypatch: Any) -> None:
    calls: list[tuple[Any, dict]] = []

    def fake_run(args: Any, **kwargs: Any) -> Any:
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 1)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert check_drafts.run_script("factory-out/repros/m.py", LIVE) == 1
    args, kwargs = calls[0]
    assert args[1:] == ["-I", "factory-out/repros/m.py", "--base-url", LIVE]
    assert kwargs["timeout"] == 120 and kwargs["cwd"] == check_drafts.ROOT

    def timeout(*a: Any, **k: Any) -> Any:
        raise subprocess.TimeoutExpired("x", 120)

    monkeypatch.setattr(subprocess, "run", timeout)
    assert check_drafts.run_script("factory-out/repros/m.py", LIVE) == 2
    assert check_drafts.run_script("../evil.py", LIVE) == 2


def test_validated_json_keeps_survivors_limits_suggestions_and_extends_dropped() -> None:
    incoming = {
        "drafts": [draft_dict("a" * 12), draft_dict("b" * 12, check_name="axe")],
        "suggestions": [
            {"title": "s1", "problem": "p", "screenshot": "x"},
            {"title": "s2", "problem": "p", "screenshot": "x"},
        ],
        "dropped": [{"fingerprint": "c" * 12, "reason": "old"}],
    }

    def run_group(g: Group, url: str) -> int:
        return 1 if g.check == "overflow" or url == LIVE else 0

    result = check_drafts.build_validated(
        incoming, LIVE, MAIN, never_script, run_group, None, suggestions_per_run=1
    )
    assert result["drafts"] == [incoming["drafts"][0]]
    assert [s["title"] for s in result["suggestions"]] == ["s1"]
    assert result["dropped"] == [
        {"fingerprint": "c" * 12, "reason": "old"},
        {"fingerprint": "b" * 12, "reason": "fixed on main, not deployed"},
    ]
