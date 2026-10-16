"""Targeted Firefox/WebKit coverage without Chromium-only permission assumptions."""

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.test_pages import WIDE_ELEMENTS


@pytest.mark.parametrize("width", [1440, 768, 375, 320])
def test_navigation_and_responsive_layout(page: Page, site_url: str, width: int) -> None:
    # Firefox does not implement Playwright's is_mobile emulation. Viewports still
    # exercise the actual CSS breakpoints in every engine, including WebKit.
    page.set_viewport_size({"width": width, "height": 900})
    page.emulate_media(reduced_motion="reduce")
    page.goto(site_url)
    page.locator('.topbar a[href="docs/"]').click()
    expect(page).to_have_url(site_url + "docs/")
    page.locator('.topbar a[href="#krok-po-kroku"]').click()
    expect(page).to_have_url(site_url + "docs/#krok-po-kroku")
    expect(page.locator("#krok-po-kroku h2")).to_be_in_viewport()
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    assert page.evaluate(WIDE_ELEMENTS, width) == []
    page.locator('.topbar a[href="../"]').filter(has_text="Start").click()
    expect(page).to_have_url(site_url)
    page.locator('.hero-actions a[href="#instalacja"]').click()
    expect(page).to_have_url(site_url + "#instalacja")
    expect(page.locator("#instalacja")).to_be_in_viewport()
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    assert page.evaluate(WIDE_ELEMENTS, width) == []


@pytest.mark.parametrize("clipboard", ["success", "denied", "missing"])
def test_copy_and_manual_fallback(page: Page, page_url: str, clipboard: str) -> None:
    # Firefox/WebKit do not support Chromium's clipboard-read/write permissions.
    # A deterministic clipboard boundary verifies each engine's real click,
    # synchronous invocation, success label and range-selection fallback.
    page.add_init_script("""
      window.__writes = [];
      window.__clicking = false;
      document.addEventListener('click', () => { window.__clicking = true; }, true);
      document.addEventListener('click', () => { window.__clicking = false; });
    """)
    if clipboard == "missing":
        page.add_init_script("Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined })")
    else:
        result = "Promise.resolve()" if clipboard == "success" else "Promise.reject(new Error('denied'))"
        page.add_init_script("""Object.defineProperty(navigator, 'clipboard', { configurable: true, value: {
          writeText(text) { window.__writes.push({text, sync: window.__clicking}); return RESULT; }
        } });""".replace("RESULT", result))
    page.goto(page_url)
    buttons = page.locator("button.copy").all()
    assert buttons
    for button in buttons:
        command = button.get_attribute("data-command")
        button.click()
        if clipboard == "success":
            expect(button).to_have_text("Skopiowano")
        else:
            expect(button).to_have_text("Zaznaczono, skopiuj ręcznie")
            assert page.evaluate("String(getSelection())") == command
    writes = page.evaluate("window.__writes")
    if clipboard == "missing":
        assert writes == []
    else:
        assert [write["text"] for write in writes] == [button.get_attribute("data-command") for button in buttons]
        assert all(write["sync"] for write in writes)
