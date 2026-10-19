"""Checks that hold on every page of the site (landing and docs)."""

import pytest
from playwright.sync_api import BrowserContext, Page

from tests.e2e.conftest import ANALYTICS_HOSTS, LOCAL_HOSTS
from tools.factory.detectors import FOCUS_PROBLEMS, WIDE_ELEMENTS

# Counts calls of navigator.clipboard.writeText and whether each ran synchronously inside
# a click listener: Safari drops a write that comes after an await or a timer.
RECORD_WRITES = """
let depth = 0;
const add = EventTarget.prototype.addEventListener;
EventTarget.prototype.addEventListener = function (type, listener, options) {
  if (type !== 'click' || typeof listener !== 'function') return add.call(this, type, listener, options);
  const wrapped = function (...args) {
    depth += 1;
    try { return listener.apply(this, args); } finally { depth -= 1; }
  };
  return add.call(this, type, wrapped, options);
};
window.__writes = [];
navigator.clipboard.writeText = (text) => {
  window.__writes.push({ text, sync: depth > 0 });
  return Promise.resolve();
};
"""

def scroll_through(page: Page) -> None:
    """Scroll to the bottom so lazy work (the demo, images) starts, then let the network settle."""
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    page.wait_for_load_state("networkidle")


def test_copy_buttons_copy_the_visible_command(page: Page, context: BrowserContext, page_url: str) -> None:
    context.grant_permissions(["clipboard-read", "clipboard-write"])
    page.goto(page_url)
    buttons = page.locator("button.copy").all()
    assert buttons
    for button in buttons:
        command = button.get_attribute("data-command")
        assert button.evaluate("b => b.parentElement.querySelector('.cmd-text').innerText") == command
        button.click()
        assert page.evaluate("navigator.clipboard.readText()") == command
        assert button.inner_text().strip() == "Skopiowano"


def test_write_text_called_synchronously(page: Page, page_url: str) -> None:
    page.add_init_script(RECORD_WRITES)
    page.goto(page_url)
    buttons = page.locator("button.copy").all()
    for button in buttons:
        button.click()
    writes = page.evaluate("window.__writes")
    assert [w["text"] for w in writes] == [b.get_attribute("data-command") for b in buttons]
    assert all(w["sync"] for w in writes), writes


def test_fallback_selects_the_command(page: Page, page_url: str) -> None:
    page.add_init_script("navigator.clipboard.writeText = () => Promise.reject(new Error('denied'))")
    page.goto(page_url)
    button = page.locator("button.copy").first
    button.click()
    assert button.inner_text().strip() == "Zaznaczono, skopiuj ręcznie"
    assert page.evaluate("String(getSelection())") == button.get_attribute("data-command")


def test_copy_buttons_hidden_without_js(new_context, page_url: str) -> None:
    page = new_context(java_script_enabled=False).new_page()
    page.goto(page_url)
    assert page.locator("button.copy").count() > 0
    assert page.locator("button.copy:visible").count() == 0


@pytest.mark.parametrize("width", [320, 375])
def test_no_horizontal_scroll(new_context, page_url: str, width: int) -> None:
    page = new_context(viewport={"width": width, "height": 800}, is_mobile=True, has_touch=True).new_page()
    page.goto(page_url)
    scroll_through(page)
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    assert page.evaluate(WIDE_ELEMENTS, width) == []


def test_no_third_party_requests_cookies_or_storage(
    page: Page, context: BrowserContext, requested_hosts: list[str], page_url: str
) -> None:
    page.goto(page_url)
    scroll_through(page)
    assert set(requested_hosts) - LOCAL_HOSTS <= ANALYTICS_HOSTS
    assert context.cookies() == []
    assert page.evaluate("localStorage.length + sessionStorage.length") == 0
    # Umami counts only the production host, never a local copy.
    analytics = page.locator('script[src^="https://cloud.umami.is/"]')
    assert analytics.count() == 1
    assert analytics.get_attribute("data-domains") == "mbratkowski.github.io"


def test_no_console_errors(page: Page, console_errors: list[str], page_url: str) -> None:
    page.goto(page_url)
    scroll_through(page)
    assert console_errors == []


def test_anchors_resolve(page: Page, page_url: str) -> None:
    page.goto(page_url)
    missing = page.evaluate(
        """() => Array.from(document.querySelectorAll('a[href^="#"]'), a => a.getAttribute('href'))
          .filter(href => href !== '#' && !document.getElementById(decodeURIComponent(href.slice(1))))"""
    )
    assert missing == []


def test_internal_links_resolve(page: Page, page_url: str, site_url: str) -> None:
    page.goto(page_url)
    urls = page.evaluate(
        "() => Array.from(document.querySelectorAll('a[href], link[href], script[src], img[src]'), e => e.href || e.src)"
    )
    local = sorted({url for url in urls if url.startswith(site_url)})
    assert local
    problems, pages = [], {}
    for url in local:
        target, _, fragment = url.partition("#")
        if target not in pages:
            response = page.request.get(target)
            html = response.text() if "text/html" in response.headers.get("content-type", "") else ""
            pages[target] = (response.status, html)
        status, html = pages[target]
        if status != 200:
            problems.append(f"{url}: HTTP {status}")
        elif fragment and f'id="{fragment}"' not in html:
            problems.append(f"{url}: no element with id {fragment!r}")
    assert problems == []


def test_focus_ring_visible_on_interactive_elements(page: Page, page_url: str) -> None:
    page.goto(page_url)
    page.keyboard.press("Tab")  # keyboard modality: focus() now matches :focus-visible
    result = page.evaluate(FOCUS_PROBLEMS)
    assert result["checked"] > 10
    assert result["problems"] == []
