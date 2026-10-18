"""Layout checks against small fixture pages: canaries must be found, the clean page must not."""

from collections.abc import Callable

import pytest
from playwright.sync_api import Browser

from tools.factory.checks import CHECKS
from tools.factory.findings import Cell
from tools.factory.probe import Target, open_cell

LAYOUT = ["overflow", "clipped-text", "overlap"]


def _findings(browser: Browser, url: str, cell: Cell, path: str, check: str):
    run = open_cell(browser, Target("fixtures", url), cell, path)
    try:
        return CHECKS[check](run)
    finally:
        run.close()


@pytest.mark.parametrize("check", LAYOUT)
def test_canary_is_found(
    check: str, browser: Browser, fixtures_url: str, probe_cell: Callable[..., Cell]
) -> None:
    found = _findings(browser, fixtures_url, probe_cell(check, 375), f"canary/{check}.html", check)
    assert found
    assert all(f.check == check for f in found)
    assert any(f.selector.endswith("#planted") for f in found)
    assert len(found) <= 5


@pytest.mark.parametrize("check", LAYOUT)
def test_clean_page_has_no_findings(
    check: str, browser: Browser, fixtures_url: str, probe_cell: Callable[..., Cell]
) -> None:
    assert _findings(browser, fixtures_url, probe_cell("clean", 375), "clean/index.html", check) == []


def test_link_covering_text_is_found(browser: Browser) -> None:
    from tools.factory.detectors import OVERLAPS

    page = browser.new_page()
    try:
        page.set_content(
            '<p style="margin:0;height:30px;width:200px">some text</p>'
            '<a id="planted" href="#" style="position:absolute;top:0;left:0;width:300px;height:30px">x</a>'
        )
        found = page.evaluate(OVERLAPS)
    finally:
        page.close()
    assert [f["selector"] for f in found] == ["a#planted"]
