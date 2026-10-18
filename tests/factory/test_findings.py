"""Findings merge by adjacent widths and engines and get a stable fingerprint."""

import json

from tools.factory.findings import (
    DEFAULT_SEVERITY,
    Cell,
    Finding,
    Group,
    group_findings,
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
