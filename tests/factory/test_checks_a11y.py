"""Accessibility checks against canary pages: each planted defect is found, the clean page is not."""

from collections.abc import Callable

import pytest
from playwright.sync_api import Browser

from tools.factory.checks import CHECKS
from tools.factory.findings import Cell
from tools.factory.probe import Target, open_cell

A11Y = ["axe", "target-size", "focus"]


def _findings(browser: Browser, url: str, cell: Cell, path: str, check: str):
    run = open_cell(browser, Target("fixtures", url), cell, path)
    try:
        return CHECKS[check](run), run
    finally:
        run.close()


def _check(browser, url, cell, path, check):
    return _findings(browser, url, cell, path, check)[0]


def test_axe_finds_low_contrast(browser: Browser, fixtures_url: str, probe_cell: Callable[..., Cell]) -> None:
    run = open_cell(browser, Target("fixtures", fixtures_url), probe_cell("axe", 375), "canary/axe.html")
    try:
        found = CHECKS["axe"](run)
        assert run.requests.blocked == []
    finally:
        run.close()
    assert [f.check for f in found] == ["axe"] * len(found)
    contrast = [f for f in found if "color-contrast" in f.measurement]
    assert contrast
    assert contrast[0].selector.endswith("#planted")
    assert contrast[0].box[2] > 0
    assert len(found) <= 5


def test_target_size_finds_small_button(browser, fixtures_url, probe_cell) -> None:
    found = _check(browser, fixtures_url, probe_cell("target-size", 375), "canary/target-size.html", "target-size")
    assert any(f.selector.endswith("button#planted") for f in found)


def test_target_size_finds_small_nav_link(browser, fixtures_url, probe_cell) -> None:
    found = _check(browser, fixtures_url, probe_cell("target-size", 375), "canary/target-size.html", "target-size")
    nav = [f for f in found if f.selector.endswith("a#planted-nav")]
    assert nav
    assert "44" in nav[0].measurement


def test_target_size_exempts_inline_links(browser, fixtures_url, probe_cell) -> None:
    page = browser.new_page()
    try:
        from tools.factory.detectors import TARGET_SIZES

        page.set_content('<p>Some words with <a id="inline" href="#">a short link</a> inside a sentence.</p>')
        assert page.evaluate(TARGET_SIZES, [24, 44]) == []
    finally:
        page.close()


@pytest.mark.parametrize(
    ("name", "fragment"),
    [
        ("focus-ring", "button#planted"),
        ("focus-obscured", "a#planted"),
        ("scroll-unreachable", "pre#planted"),
    ],
)
def test_focus_canary_is_found(name, fragment, browser, fixtures_url, probe_cell) -> None:
    found = _check(browser, fixtures_url, probe_cell(name, 375), f"canary/{name}.html", "focus")
    assert found
    assert all(f.check == "focus" for f in found)
    assert any(f.selector.endswith(fragment) for f in found)


def test_focus_works_in_keyboard_mode(browser, fixtures_url, probe_cell) -> None:
    found = _check(
        browser, fixtures_url, probe_cell("focus-ring", 375, mode="keyboard"), "canary/focus-ring.html", "focus"
    )
    assert any(f.selector.endswith("button#planted") for f in found)


@pytest.mark.parametrize("check", A11Y)
def test_clean_page_has_no_findings(check, browser, fixtures_url, probe_cell) -> None:
    assert _check(browser, fixtures_url, probe_cell("clean", 375), "clean/index.html", check) == []
