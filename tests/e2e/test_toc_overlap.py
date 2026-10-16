"""The docs navigation never covers the article when its flex columns wrap."""

import pytest
from playwright.sync_api import Page, Route, expect


def assert_toc_does_not_cover_article(page: Page) -> None:
    toc = page.locator(".toc").bounding_box()
    article = page.locator(".prose").bounding_box()
    assert toc is not None and article is not None
    overlap_width = min(toc["x"] + toc["width"], article["x"] + article["width"]) - max(toc["x"], article["x"])
    overlap_height = min(toc["y"] + toc["height"], article["y"] + article["height"]) - max(toc["y"], article["y"])
    assert overlap_width <= 0 or overlap_height <= 0, {"toc": toc, "article": article}


@pytest.mark.parametrize("javascript", [True, False], ids=["javascript", "no-javascript"])
@pytest.mark.parametrize("width,gutter,content_width,sidebar", [
    (320, 24, 1240, False),
    (768, 24, 1240, False),
    (900, 24, 1240, False),
    (901, 24, 1240, False),
    (910, 24, 1240, False),
    (915, 24, 1240, False),
    (916, 24, 1240, True),
    (1024, 24, 1240, True),
    (1440, 24, 1240, True),
    (947, 40, 1240, False),
    (948, 40, 1240, True),
    (1440, 24, 900, False),
])
def test_direct_anchor_keeps_toc_clear_of_article(
    new_context, site_url: str, width: int, gutter: int, content_width: int, sidebar: bool, javascript: bool,
) -> None:
    page = new_context(java_script_enabled=javascript, viewport={"width": width, "height": 800}).new_page()
    page.emulate_media(reduced_motion="reduce")

    def configure_tokens(route: Route) -> None:
        response = route.fetch()
        overrides = f"\n:root {{ --page-gutter: {gutter}px; --content-width: {content_width}px; }}"
        route.fulfill(response=response, body=response.text() + overrides)

    # Tokens are stylesheet configuration. Loading them up front also avoids
    # Firefox retaining a stale query width after a runtime padding-only change.
    if (gutter, content_width) != (24, 1240):
        page.route("**/css/site.css", configure_tokens)
    page.goto(site_url + "docs/#cli")
    if javascript:
        page.evaluate("document.fonts.ready")
    page.locator("#cli").evaluate("el => el.scrollIntoView({behavior: 'instant'})")
    expect(page.locator("#cli h2")).to_be_in_viewport()
    if sidebar:
        expect(page.locator(".toc")).to_be_in_viewport()
    else:
        expect(page.locator(".toc")).not_to_be_in_viewport()
    assert_toc_does_not_cover_article(page)


def test_resizing_across_toc_wrap_threshold_keeps_article_uncovered(page: Page, site_url: str) -> None:
    page.emulate_media(reduced_motion="reduce")
    page.goto(site_url + "docs/#cli")
    page.evaluate("document.fonts.ready")
    for width, sidebar in [
        (900, False), (901, False), (910, False), (915, False), (916, True),
        (1024, True), (1440, True), (916, True), (915, False), (910, False),
        (901, False), (900, False),
    ]:
        page.set_viewport_size({"width": width, "height": 800})
        page.locator("#cli").evaluate("el => el.scrollIntoView({behavior: 'instant'})")
        expect(page.locator("#cli h2")).to_be_in_viewport()
        if sidebar:
            expect(page.locator(".toc")).to_be_in_viewport()
        else:
            expect(page.locator(".toc")).not_to_be_in_viewport()
        assert_toc_does_not_cover_article(page)


@pytest.mark.parametrize("width", [910, 916, 1440])
def test_keyboard_toc_anchor_keeps_article_uncovered(page: Page, site_url: str, width: int) -> None:
    page.set_viewport_size({"width": width, "height": 800})
    page.emulate_media(reduced_motion="reduce")
    page.goto(site_url + "docs/")
    page.evaluate("document.fonts.ready")
    link = page.locator('.toc a[href="#cli"]')
    link.focus()
    page.keyboard.press("Enter")
    expect(page).to_have_url(site_url + "docs/#cli")
    expect(page.locator("#cli h2")).to_be_in_viewport()
    expect(link).to_have_attribute("aria-current", "location")
    assert_toc_does_not_cover_article(page)
