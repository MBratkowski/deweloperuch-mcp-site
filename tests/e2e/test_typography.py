"""Component typography must win over paragraph defaults in its surrounding card."""

import pytest
from playwright.sync_api import Locator, Page, expect


ACCENT = "rgb(52, 211, 153)"
TEXT = "rgb(241, 238, 247)"
MUTED = "rgb(167, 161, 184)"


def assert_typography(elements: Locator, *, size: str, color: str) -> None:
    assert elements.count() > 0
    for element in elements.all():
        expect(element).to_have_css("font-size", size)
        expect(element).to_have_css("color", color)


@pytest.mark.parametrize("width", [1440, 375], ids=["desktop", "mobile"])
@pytest.mark.parametrize(
    "selector, size, color", [(".step-num", "12px", ACCENT), (".mock-title", "13px", TEXT)],
    ids=["step-label", "mock-title"],
)
def test_step_components_override_paragraph_defaults(
    page: Page, site_url: str, width: int, selector: str, size: str, color: str
) -> None:
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(site_url + "docs/")

    assert_typography(page.locator(selector), size=size, color=color)
    if selector == ".mock-title":
        for title in page.locator(selector).all():
            expect(title).to_have_css("font-weight", "600")

    # Explanatory paragraphs keep the context's body size, colour and rhythm.
    paragraphs = page.locator(".step-body > p:not([class]), .mock-text")
    assert_typography(paragraphs, size="15px", color=MUTED)
    for paragraph in paragraphs.all():
        expect(paragraph).to_have_css("line-height", "24px")
        expect(paragraph).to_have_css("margin", "0px")


@pytest.mark.parametrize("width", [1440, 375], ids=["desktop", "mobile"])
def test_agent_eyebrow_overrides_paragraph_defaults_in_interactive_states(
    page: Page, page_url: str, width: int
) -> None:
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(page_url)
    card = page.locator(".agent-card")
    eyebrow = card.locator(".eyebrow")
    body = card.locator(".agent-copy > p:not([class])")
    copy = card.locator("button.copy")

    def check_card_typography() -> None:
        assert_typography(eyebrow, size="12px", color=ACCENT)
        expect(eyebrow).to_have_css("text-transform", "uppercase")
        assert_typography(body, size="15px", color=MUTED)
        expect(body).to_have_css("line-height", "24px")
        expect(body).to_have_css("margin", "0px")

    check_card_typography()
    copy.hover()
    check_card_typography()
    page.keyboard.press("Tab")
    copy.focus()
    assert copy.evaluate("element => element.matches(':focus-visible')")
    check_card_typography()
    copy.click()
    expect(copy).to_have_class("copy is-copied")
    check_card_typography()
