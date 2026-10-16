"""Every docs navigation link stays usable in a short desktop window."""

import sys

import pytest
from playwright.sync_api import Locator, Page, expect


def open_article(page: Page, site_url: str, javascript: bool) -> None:
    page.emulate_media(reduced_motion="reduce")
    page.goto(site_url + "docs/#cli")
    if javascript:
        page.evaluate("document.fonts.ready")
    page.locator("#cli").evaluate("el => el.scrollIntoView({behavior: 'instant'})")
    expect(page.locator("#cli h2")).to_be_in_viewport()


def assert_visible_focus(link: Locator) -> None:
    expect(link).to_be_focused()
    expect(link).to_be_in_viewport(ratio=1)
    focus = link.evaluate(
        """el => {
            const style = getComputedStyle(el);
            const toc = el.closest('.toc');
            const rect = el.getBoundingClientRect();
            const clip = toc.getBoundingClientRect();
            const outset = Math.max(0, parseFloat(style.outlineWidth) + parseFloat(style.outlineOffset));
            return {
                visible: el.matches(':focus-visible'),
                outline: style.outlineStyle,
                width: parseFloat(style.outlineWidth),
                clips: getComputedStyle(toc).overflowY !== 'visible',
                left: rect.left - outset, right: rect.right + outset,
                top: rect.top - outset, bottom: rect.bottom + outset,
                clipLeft: clip.left, clipRight: clip.right,
                clipTop: clip.top, clipBottom: clip.bottom,
                viewportHeight: innerHeight,
            };
        }"""
    )
    assert focus["visible"] and focus["outline"] != "none" and focus["width"] > 0, focus
    assert focus["top"] >= 0 and focus["bottom"] <= focus["viewportHeight"], focus
    if focus["clips"]:
        # Overflow must not cut off the focus ring at the scrollport's edges.
        assert focus["left"] >= focus["clipLeft"] and focus["right"] <= focus["clipRight"], focus
        assert focus["top"] >= focus["clipTop"] and focus["bottom"] <= focus["clipBottom"], focus


@pytest.mark.parametrize("javascript", [True, False], ids=["javascript", "no-javascript"])
def test_short_toc_reaches_last_link_by_pointer(new_context, site_url: str, javascript: bool) -> None:
    page = new_context(java_script_enabled=javascript, viewport={"width": 1440, "height": 400}).new_page()
    open_article(page, site_url, javascript)
    toc = page.locator(".toc")
    box = toc.bounding_box()
    assert box is not None
    article_scroll = page.evaluate("window.scrollY")
    page.mouse.move(box["x"] + box["width"] / 2, min(box["y"] + box["height"] / 2, 350))
    page.mouse.wheel(0, 1000)

    last = toc.get_by_role("link", name="Prywatność", exact=True)
    expect(last).to_be_in_viewport(ratio=1)
    assert abs(page.evaluate("window.scrollY") - article_scroll) <= 1
    last.click()
    expect(page).to_have_url(site_url + "docs/#prywatnosc")
    expect(page.locator("#prywatnosc h2")).to_be_in_viewport()
    if javascript:
        expect(last).to_have_attribute("aria-current", "location")


@pytest.mark.parametrize("javascript", [True, False], ids=["javascript", "no-javascript"])
@pytest.mark.parametrize("height", [400, 720], ids=["short", "normal"])
def test_toc_keyboard_navigation_keeps_links_and_focus_visible(
    new_context, site_url: str, javascript: bool, height: int, browser_name: str,
) -> None:
    page = new_context(java_script_enabled=javascript, viewport={"width": 1440, "height": height}).new_page()
    open_article(page, site_url, javascript)
    toc = page.locator(".toc")
    links = toc.get_by_role("link")
    article_scroll = page.evaluate("window.scrollY")
    links.first.focus()
    # Safari on macOS uses Option-Tab to include links in keyboard navigation.
    tab = "Alt+Tab" if browser_name == "webkit" and sys.platform == "darwin" else "Tab"
    for index in range(links.count()):
        if index:
            page.keyboard.press(tab)
        assert_visible_focus(links.nth(index))
    assert abs(page.evaluate("window.scrollY") - article_scroll) <= 1
    if height == 720:
        assert toc.evaluate("el => el.scrollHeight <= el.clientHeight + 1")
    page.keyboard.press("Enter")
    expect(page).to_have_url(site_url + "docs/#prywatnosc")
    expect(page.locator("#prywatnosc h2")).to_be_in_viewport()
