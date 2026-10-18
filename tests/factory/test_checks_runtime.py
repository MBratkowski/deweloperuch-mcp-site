"""Runtime checks against canary pages: each planted defect is found, the clean page is not."""

from playwright.sync_api import Browser

from tools.factory.checks import CHECKS
from tools.factory.probe import Target, open_cell

RUNTIME = ["console", "network", "anchors", "images"]


def _check(browser: Browser, url: str, cell, path: str, check: str):
    run = open_cell(browser, Target("fixtures", url), cell, path)
    try:
        return CHECKS[check](run)
    finally:
        run.close()


def test_console_reports_page_error(browser, fixtures_url, probe_cell) -> None:
    found = _check(browser, fixtures_url, probe_cell("console", 375), "canary/console.html", "console")
    assert [f.selector for f in found] == ["script"]
    assert "planted console error" in found[0].measurement


def test_network_reports_blocked_host(browser, fixtures_url, probe_cell) -> None:
    found = _check(browser, fixtures_url, probe_cell("network", 375), "canary/network.html", "network")
    assert [f.selector for f in found] == ["example.com"]
    assert "blocked third-party host example.com" in found[0].measurement
    # The abort's own "Failed to load resource" message is not a console defect.
    assert _check(browser, fixtures_url, probe_cell("console", 375), "canary/network.html", "console") == []


def test_network_leaves_images_to_the_images_check(browser, fixtures_url, probe_cell) -> None:
    cell = probe_cell("p", 375)
    assert _check(browser, fixtures_url, cell, "probe/missing-image.html", "network") == []
    found = _check(browser, fixtures_url, cell, "probe/missing-image.html", "images")
    assert [f.check for f in found] == ["images"]


def test_anchors_finds_dead_links(browser, fixtures_url, probe_cell) -> None:
    found = _check(browser, fixtures_url, probe_cell("anchors", 375), "canary/anchors.html", "anchors")
    assert sorted(f.selector for f in found) == ["a#dead", "a#other-dead"]


def test_images_finds_broken_image(browser, fixtures_url, probe_cell) -> None:
    found = _check(browser, fixtures_url, probe_cell("images", 375), "canary/images.html", "images")
    assert [f.selector.split(" > ")[-1] for f in found] == ["img#broken"]


def test_clean_page_yields_nothing(browser, fixtures_url, probe_cell) -> None:
    for check in RUNTIME:
        assert _check(browser, fixtures_url, probe_cell("clean", 375), "clean/index.html", check) == [], check
