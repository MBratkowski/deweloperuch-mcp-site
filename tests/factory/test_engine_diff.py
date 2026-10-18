"""Engine differences: the pure comparison and a canary page that only Firefox lays out differently."""

from dataclasses import replace
from pathlib import Path

import pytest

from tools.factory.checks import engine_diff, landmark_boxes
from tools.factory.findings import Cell
from tools.factory.probe import Target, open_cell

CHROMIUM = Cell("home", "chromium", 375, 900, "default")
FIREFOX = replace(CHROMIUM, engine="firefox")
WEBKIT = replace(CHROMIUM, engine="webkit")


def _boxes(h2_x: float, header_y: float = 0) -> dict:
    return {
        "header": (0, header_y, 375, 60),
        "main > section#s > h2": (h2_x, 200, 300, 30),
    }


def test_engine_diff_pure_reports_the_differing_engine() -> None:
    found = engine_diff({CHROMIUM: _boxes(16), FIREFOX: _boxes(56), WEBKIT: _boxes(16)}, 8)
    assert len(found) == 1
    assert found[0].check == "engine-diff"
    assert found[0].cell == FIREFOX
    assert found[0].selector == "main > section#s > h2"
    assert found[0].measurement == "dx=40, dw=0 vs chromium"
    assert found[0].box == (56, 200, 300, 30)


def test_engine_diff_pure_ignores_small_shifts() -> None:
    assert engine_diff({CHROMIUM: _boxes(16), FIREFOX: _boxes(19)}, 8) == []


def test_engine_diff_pure_compares_y_only_for_header_and_nav() -> None:
    assert [f.selector for f in engine_diff({CHROMIUM: _boxes(16), FIREFOX: _boxes(16, 30)}, 8)] == ["header"]
    other = {"main > section#s > h2": (16, 260, 300, 30)}
    assert engine_diff({CHROMIUM: _boxes(16), FIREFOX: other}, 8) == []


def test_engine_diff_pure_skips_keys_missing_in_one_engine() -> None:
    assert engine_diff({CHROMIUM: _boxes(16), FIREFOX: {"header": (0, 0, 375, 60)}}, 8) == []


def test_engine_diff_pure_keeps_pages_widths_and_modes_apart() -> None:
    narrow = replace(FIREFOX, width=320)
    assert engine_diff({CHROMIUM: _boxes(16), narrow: _boxes(56)}, 8) == []


@pytest.fixture
def firefox(playwright):
    if not Path(playwright.firefox.executable_path).exists():
        pytest.skip("Firefox is not installed")
    browser = playwright.firefox.launch()
    yield browser
    browser.close()


def _boxes_of(browser, url: str, cell: Cell, path: str) -> dict:
    run = open_cell(browser, Target("fixtures", url), cell, path)
    try:
        return landmark_boxes(run)
    finally:
        run.close()


def test_landmark_boxes_uses_stable_selectors(browser, fixtures_url, probe_cell) -> None:
    boxes = _boxes_of(browser, fixtures_url, probe_cell("engine-diff", 375), "canary/engine-diff.html")
    assert {"body > header", "body > header > nav", "body > main", "body > footer"} <= set(boxes)
    assert any(k.endswith("h1") for k in boxes) and any(k.endswith("h2") for k in boxes)
    assert any(k.endswith("section#s") for k in boxes)


@pytest.mark.multi_engine
def test_engine_diff_canary(browser, firefox, fixtures_url, probe_cell) -> None:
    path = "canary/engine-diff.html"
    chromium_cell = probe_cell("engine-diff", 375)
    firefox_cell = probe_cell("engine-diff", 375, engine="firefox")
    boxes = {
        chromium_cell: _boxes_of(browser, fixtures_url, chromium_cell, path),
        firefox_cell: _boxes_of(firefox, fixtures_url, firefox_cell, path),
    }
    found = engine_diff(boxes, 8)
    assert found
    assert {f.cell.engine for f in found} == {"firefox"}
    assert all(f.check == "engine-diff" for f in found)
    assert any(f.selector.endswith("h2") and "dx=40" in f.measurement for f in found)


@pytest.mark.multi_engine
def test_engine_diff_clean_page(browser, firefox, fixtures_url, probe_cell) -> None:
    boxes = {}
    for engine, instance in (("chromium", browser), ("firefox", firefox)):
        cell = probe_cell("clean", 375, engine=engine)
        boxes[cell] = _boxes_of(instance, fixtures_url, cell, "clean/index.html")
    assert engine_diff(boxes, 8) == []
