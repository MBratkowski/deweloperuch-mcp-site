"""Targeted Firefox/WebKit coverage without Chromium-only permission assumptions."""

import sys

import pytest
from playwright.sync_api import Page, Route, expect

from tests.e2e.test_pages import WIDE_ELEMENTS


def open_navigation_if_collapsed(page: Page) -> None:
    expect(page.locator("[data-navigation]")).to_have_attribute("data-nav-ready", "")
    toggle = page.get_by_role("button", name="Menu", exact=True)
    if toggle.is_visible():
        toggle.click()


@pytest.mark.parametrize("width", [1440, 768, 375, 320])
def test_navigation_and_responsive_layout(page: Page, site_url: str, width: int) -> None:
    # Firefox does not implement Playwright's is_mobile emulation. Viewports still
    # exercise the actual CSS breakpoints in every engine, including WebKit.
    page.set_viewport_size({"width": width, "height": 900})
    page.emulate_media(reduced_motion="reduce")
    page.goto(site_url)
    open_navigation_if_collapsed(page)
    page.locator('.topbar a[href="docs/"]').click()
    expect(page).to_have_url(site_url + "docs/")
    open_navigation_if_collapsed(page)
    page.locator('.topbar a[href="#krok-po-kroku"]').click()
    expect(page).to_have_url(site_url + "docs/#krok-po-kroku")
    expect(page.locator("#krok-po-kroku h2")).to_be_in_viewport()
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    assert page.evaluate(WIDE_ELEMENTS, width) == []
    open_navigation_if_collapsed(page)
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


@pytest.mark.parametrize("width", [320, 375, 390, 414])
def test_mobile_header_has_one_compact_control_row(page: Page, page_url: str, width: int) -> None:
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(page_url)
    header = page.locator(".topbar")
    box = header.bounding_box()
    assert box and box["height"] <= 100, "Closed mobile navigation must fit in one control row"
    controls = [header.locator(".brand"), header.get_by_role("button", name="Menu", exact=True), header.get_by_role("link", name="Pobierz", exact=True)]
    boxes = []
    for control in controls:
        expect(control).to_be_visible()
        bounds = control.bounding_box()
        assert bounds and bounds["width"] >= 44 and bounds["height"] >= 44
        assert bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= width
        boxes.append(bounds)
    centers = [bounds["y"] + bounds["height"] / 2 for bounds in boxes]
    assert max(centers) - min(centers) <= 1
    expect(header.get_by_role("button", name="Menu", exact=True)).to_have_attribute("aria-expanded", "false")
    expect(header.locator("#site-navigation")).to_be_hidden()
    expect(header.get_by_role("link", name="GitHub", exact=True)).to_be_hidden()


@pytest.mark.parametrize("script_loads", [True, False], ids=["loaded", "failed"])
def test_mobile_navigation_handles_delayed_script(page: Page, page_url: str, script_loads: bool) -> None:
    page.set_viewport_size({"width": 412, "height": 823})
    page.emulate_media(reduced_motion="reduce")
    pending: list[Route] = []
    page.route("**/js/site.js", lambda route: pending.append(route))
    page.goto(page_url, wait_until="commit")
    page.locator("main").wait_for(state="visible")
    frame = "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
    page.evaluate(frame)
    header_before = page.locator(".topbar").bounding_box()
    main_before = page.locator("main").bounding_box()
    assert len(pending) == 1

    if not script_loads:
        pending[0].abort()
        links = page.locator(".topbar nav a")
        assert links.count() == 7
        for link in links.all():
            expect(link).to_be_visible()
        expect(page.get_by_role("button", name="Menu", exact=True)).to_be_hidden()
        return

    pending[0].continue_()
    expect(page.locator("[data-navigation]")).to_have_attribute("data-nav-ready", "")
    page.evaluate(frame)
    header_after = page.locator(".topbar").bounding_box()
    main_after = page.locator("main").bounding_box()

    assert header_before and header_after and main_before and main_after
    assert header_after["height"] == pytest.approx(header_before["height"], abs=1)
    assert main_after["y"] == pytest.approx(main_before["y"], abs=1)


def test_mobile_menu_keyboard_and_anchor_navigation(page: Page, page_url: str, browser_name: str) -> None:
    page.set_viewport_size({"width": 375, "height": 900})
    page.emulate_media(reduced_motion="reduce")
    page.goto(page_url)
    header = page.locator(".topbar")
    toggle = header.get_by_role("button", name="Menu", exact=True)
    toggle.focus()
    page.keyboard.press("Enter")
    expect(toggle).to_have_attribute("aria-expanded", "true")
    links = header.locator("#site-navigation a")
    assert links.count() == 5
    for link in links.all() + [header.get_by_role("link", name="GitHub", exact=True)]:
        expect(link).to_be_visible()
        box = link.bounding_box()
        assert box and box["width"] >= 44 and box["height"] >= 44
    # Safari on macOS uses Option-Tab to include links in keyboard navigation.
    # https://support.apple.com/guide/safari/cpsh003/mac
    page.keyboard.press("Alt+Tab" if browser_name == "webkit" and sys.platform == "darwin" else "Tab")
    expect(links.first).to_be_focused()
    page.keyboard.press("Escape")
    expect(toggle).to_be_focused()
    expect(toggle).to_have_attribute("aria-expanded", "false")
    expect(links.first).to_be_hidden()
    toggle.click()
    anchor = "#narzedzia" if page_url.endswith("docs/") else "#faq"
    header.locator(f'a[href="{anchor}"]').click()
    expect(page).to_have_url(page_url + anchor)
    expect(toggle).to_have_attribute("aria-expanded", "false")
    expect(page.locator(anchor)).to_be_in_viewport()


def test_mobile_menu_resets_after_desktop_resize(page: Page, page_url: str) -> None:
    page.set_viewport_size({"width": 375, "height": 900})
    page.goto(page_url)
    header = page.locator(".topbar")
    toggle = header.get_by_role("button", name="Menu", exact=True)
    toggle.click()
    page.set_viewport_size({"width": 1440, "height": 900})
    expect(toggle).to_be_hidden()
    for link in header.locator("nav a").all():
        expect(link).to_be_visible()
    page.set_viewport_size({"width": 375, "height": 900})
    expect(toggle).to_have_attribute("aria-expanded", "false")
    expect(header.locator("#site-navigation")).to_be_hidden()


def test_mobile_navigation_remains_available_without_javascript(new_context, page_url: str) -> None:
    page = new_context(java_script_enabled=False, viewport={"width": 320, "height": 900}).new_page()
    page.goto(page_url)
    header = page.locator(".topbar")
    expect(header.get_by_role("button", name="Menu", exact=True)).to_be_hidden()
    links = header.locator("nav a")
    assert links.count() == 7
    for link in links.all():
        expect(link).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth") <= 320
    assert page.evaluate(WIDE_ELEMENTS, 320) == []
