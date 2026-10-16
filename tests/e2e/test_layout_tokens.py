"""Shared container tokens keep the header, sections and footer in one column."""

import pytest
from playwright.sync_api import Page


@pytest.mark.parametrize("width", [320, 720, 721, 900, 901, 1440])
def test_page_variants_follow_shared_container_tokens(page: Page, page_url: str, width: int) -> None:
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(page_url)
    page.evaluate("document.fonts.ready")

    # Check the unchanged defaults, then vary each token independently. The two
    # breakpoints cover the shared/mobile rules and the documentation sidebar.
    for gutter, content_width in [(24, 1240), (40, 1240), (24, 1000)]:
        if (gutter, content_width) != (24, 1240):
            page.evaluate(
                """({gutter, contentWidth}) => {
                    document.documentElement.style.setProperty('--page-gutter', `${gutter}px`);
                    document.documentElement.style.setProperty('--content-width', `${contentWidth}px`);
                }""",
                {"gutter": gutter, "contentWidth": content_width},
            )

        container_width = min(width, content_width)
        container_left = (width - container_width) / 2
        content_left = container_left + gutter
        containers = page.locator(".wrap").evaluate_all(
            """elements => elements.map(element => {
                const bounds = element.getBoundingClientRect();
                const style = getComputedStyle(element);
                return {
                    name: element.className,
                    left: bounds.left,
                    width: bounds.width,
                    paddingLeft: parseFloat(style.paddingLeft),
                    paddingRight: parseFloat(style.paddingRight),
                };
            })"""
        )
        assert containers
        for container in containers:
            assert container["left"] == pytest.approx(container_left), container
            assert container["width"] == pytest.approx(container_width), container
            assert container["paddingLeft"] == gutter, container
            assert container["paddingRight"] == gutter, container

        # Measure visible content too: changing a container must also move the
        # navigation, section heading and footer that share its left edge.
        aligned_content = page.locator(
            ".topbar > nav:first-child, #dane > .section-heading, .footer-copy, "
            ".docs-hero .eyebrow, .docs-steps > h2, .docs-footer .wrap > p"
        )
        assert aligned_content.count() >= 3
        for element in aligned_content.all():
            bounds = element.bounding_box()
            assert bounds is not None
            assert bounds["x"] == pytest.approx(content_left), element.get_attribute("class")

    # The landing variant only changes bottom spacing; horizontal tokens never
    # alter either header's vertical rhythm.
    header = page.locator(".topbar")
    assert header.evaluate("e => getComputedStyle(e).paddingTop") == "20px"
    bottom = "0px" if page.locator(".landing").count() else "20px"
    assert header.evaluate("e => getComputedStyle(e).paddingBottom") == bottom
