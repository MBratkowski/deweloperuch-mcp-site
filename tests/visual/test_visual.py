"""Maintained composition and interaction baselines, rendered only by the explicit job."""

import json
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from tests.visual.conftest import settle

pytestmark = pytest.mark.visual
DEMO = json.loads((Path(__file__).resolve().parents[2] / "data/demo.json").read_text())
LAYOUTS = [("desktop", 1440, 900), ("tablet", 768, 1024), ("mobile", 375, 812)]


@pytest.mark.parametrize("layout,width,height", LAYOUTS, ids=[layout[0] for layout in LAYOUTS])
@pytest.mark.parametrize("route", ["landing", "docs"])
def test_page_composition(visual_page: Page, site_url: str, assert_visual, route: str, layout: str, width: int, height: int) -> None:
    page = visual_page
    page.set_viewport_size({"width": width, "height": height})
    page.goto(site_url + ("docs/" if route == "docs" else ""))
    settle(page)
    if route == "landing":
        expect(page.locator("#demo")).to_have_attribute("data-demo", "final")
        expect(page.locator('#demo [data-step="row"]:visible')).to_have_count(len(DEMO["rows"]))
        expect(page.locator("#demo .chip-state")).to_have_text("gotowe")
    assert_visual(f"{route}-{layout}", page.screenshot(full_page=True, animations="disabled", caret="hide"))


@pytest.mark.parametrize("name,route,target,crop,state", [
    ("install-hover", "", ".hero-actions .button", ".hero-copy", "hover"),
    ("install-focus", "", ".hero-actions .button", ".hero-copy", "focus"),
    ("copy-hover", "", ".hero-actions .copy", ".hero-copy", "hover"),
    ("copy-focus", "", ".hero-actions .copy", ".hero-copy", "focus"),
    ("docs-nav-hover", "docs/", '.topbar a[href="#krok-po-kroku"]', ".topbar", "hover"),
    ("docs-copy-focus", "docs/", ".agent-card .copy", ".agent-card", "focus"),
])
def test_interaction_states(visual_page: Page, site_url: str, assert_visual, name: str, route: str, target: str, crop: str, state: str) -> None:
    page = visual_page
    page.goto(site_url + route)
    settle(page)
    control = page.locator(target).first
    if state == "hover":
        control.hover()
    else:
        page.keyboard.press("Tab")
        control.focus()
        assert control.evaluate("el => el.matches(':focus-visible')")
    assert_visual(name, page.locator(crop).screenshot(animations="disabled", caret="hide"))
