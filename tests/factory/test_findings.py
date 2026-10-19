"""Findings merge by adjacent widths and engines and get a stable fingerprint."""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tools.factory.findings import (
    DEFAULT_SEVERITY,
    Cell,
    Finding,
    Group,
    fingerprint,
    group_findings,
    main,
    normalize_selector,
    width_bands,
)

WIDTHS = (320, 375, 390, 414, 719, 721, 759, 761, 768, 1024, 1280, 1440, 1920)
BREAKPOINTS = (720, 760)


def finding(
    width: int, engine: str = "webkit", selector: str = "div#w", check: str = "overflow"
) -> Finding:
    cell = Cell("index", engine, width, 800, "default")
    return Finding(check, cell, selector, f"scrollWidth {width + 10}", (0.0, 0.0, 10.0, 20.0))


def group(widths: tuple[int, ...], selector: str = "div#w") -> Group:
    found = [finding(w, selector=selector) for w in widths]
    (result,) = group_findings(found, WIDTHS, BREAKPOINTS)
    return result


def test_adjacent_widths_form_one_group() -> None:
    groups = group_findings([finding(w) for w in (320, 375, 390, 414)], WIDTHS, BREAKPOINTS)
    assert len(groups) == 1
    assert groups[0].widths == (320, 375, 390, 414)
    assert len(groups[0].findings) == 4


def test_gap_in_widths_splits_groups() -> None:
    groups = group_findings([finding(320), finding(768)], WIDTHS, BREAKPOINTS)
    assert len(groups) == 2


def test_engines_merge_when_cells_match() -> None:
    found = [finding(w, e) for e in ("webkit", "firefox") for w in (320, 375)]
    groups = group_findings(found, WIDTHS, BREAKPOINTS)
    assert len(groups) == 1
    assert groups[0].engines == ("firefox", "webkit")
    assert groups[0].widths == (320, 375)


def test_engines_with_different_widths_stay_apart() -> None:
    found = [finding(320, "webkit"), finding(768, "firefox")]
    assert len(group_findings(found, WIDTHS, BREAKPOINTS)) == 2


def test_fingerprint_is_stable_across_runs_and_boundaries() -> None:
    wide = group((320, 375, 390, 414))
    narrow = group((320, 375, 390))
    assert wide.fingerprint == narrow.fingerprint
    assert len(wide.fingerprint) == 12
    assert group((320, 375), selector="p").fingerprint != wide.fingerprint
    assert group((768,)).fingerprint != wide.fingerprint
    assert normalize_selector("main > section:nth-child(3) pre") == "main > section pre"
    assert normalize_selector("ul  li:nth-of-type(2)>a") == "ul li > a"


def test_width_bands() -> None:
    assert width_bands((320, 720), BREAKPOINTS) == ("le720",)
    assert width_bands((719, 721, 760, 761), BREAKPOINTS) == ("le720", "721-760", "ge761")
    assert width_bands((1024,), (600,)) == ("ge601",)


def test_default_severity() -> None:
    assert DEFAULT_SEVERITY == {
        "console": "S1", "anchors": "S1", "network": "S1", "flaky": "S1",
        "deploy-stale": "S1", "axe": "S2", "target-size": "S2", "focus": "S2",
        "overflow": "S3", "clipped-text": "S3", "overlap": "S3", "engine-diff": "S3",
        "images": "S3",
    }  # fmt: skip
    assert group((320,)).severity == "S3"


def test_group_round_trips_through_json() -> None:
    original = group_findings([finding(w) for w in (320, 375)], WIDTHS, BREAKPOINTS)[0]
    data = json.loads(json.dumps(original.to_json()))
    assert Group.from_json(data) == original


def test_non_browser_group_is_valid() -> None:
    ci = Group("flaky", "ci", "default", "tests/test_x.py::test_y", (), (), ())
    assert ci.severity == "S1"
    assert len(ci.fingerprint) == 12
    assert Group.from_json(json.loads(json.dumps(ci.to_json()))) == ci


def test_group_note_round_trips_and_stays_out_of_the_fingerprint() -> None:
    plain = Group("deploy-stale", "site", "default", "", (), (), ())
    noted = Group("deploy-stale", "site", "default", "", (), (), (), note="live 1f104d1, main 8b6ec7b")
    assert noted.fingerprint == plain.fingerprint
    assert noted.to_json()["note"] == "live 1f104d1, main 8b6ec7b"
    assert Group.from_json(json.loads(json.dumps(noted.to_json()))) == noted
    assert plain.note == ""
    legacy = {k: v for k, v in plain.to_json().items() if k != "note"}
    assert Group.from_json(legacy) == plain


def test_ai_review_fingerprint_command(capsys: pytest.CaptureFixture[str]) -> None:
    args = ["fingerprint", "--check", "ai-review", "--page", "docs", "--selector", "nav.toc>a:nth-child(2)"]
    assert main(args) == 0
    printed = capsys.readouterr().out
    assert re.fullmatch(r"[0-9a-f]{12}\n", printed)
    assert printed.strip() == fingerprint("ai-review", "docs", "default", "nav.toc > a", (), ())
    # The same element on another night, written another way, gets the same fingerprint.
    assert main([*args[:-1], "nav.toc > a:nth-child(5)"]) == 0
    assert capsys.readouterr().out == printed
    assert main([*args[:-3], "landing", *args[-2:]]) == 0
    assert capsys.readouterr().out != printed


@pytest.mark.parametrize(
    "args",
    [
        ["fingerprint", "--check", "overflow", "--page", "docs", "--selector", "nav"],
        ["fingerprint", "--check", "ai-review", "--page", "ci", "--selector", "nav"],
        ["fingerprint", "--check", "ai-review", "--page", "docs", "--selector", "  "],
    ],
)
def test_fingerprint_command_rejects_other_checks_pages_and_empty_selectors(args: list[str]) -> None:
    with pytest.raises(SystemExit) as error:
        main(args)
    assert error.value.code == 2


def test_fingerprint_command_runs_as_a_module() -> None:
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-m", "tools.factory.findings", "fingerprint", "--check", "ai-review",
         "--page", "landing", "--selector", "header .brand"],
        cwd=root, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == fingerprint("ai-review", "landing", "default", "header .brand", (), ())
