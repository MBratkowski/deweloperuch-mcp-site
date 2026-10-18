"""The landing page: head, sections, install commands and the demo window."""

import json
from pathlib import Path

import pytest
from playwright.sync_api import Page

ROOT = Path(__file__).resolve().parents[2]
DEMO = json.loads((ROOT / "data/demo.json").read_text(encoding="utf-8"))
COMMANDS = [
    "uvx deweloperuch-mcp",
    "claude mcp add deweloperuch -- uvx deweloperuch-mcp",
    "npx skills add MBratkowski/deweloperuch-mcp",
    "uvx --from deweloperuch-mcp deweloperuch --help",
]


def demo_state(page: Page) -> str | None:
    return page.locator("#demo").get_attribute("data-demo")


def final_text(new_context, site_url: str) -> str:
    """The demo window as the HTML ships it (no JavaScript): the state every visitor must end in."""
    page = new_context(java_script_enabled=False).new_page()
    page.goto(site_url)
    return page.locator("#demo").inner_text()


def test_head(page: Page, site_url: str) -> None:
    page.goto(site_url)
    assert page.locator("html").get_attribute("lang") == "pl"
    assert page.title() == "deweloperuch-mcp: dane o rynku mieszkań w rozmowie z AI"
    assert page.locator('meta[name="description"]').get_attribute("content")
    assert page.locator('meta[property="og:image"]').get_attribute("content").endswith("assets/og.png")
    data = json.loads(page.locator('script[type="application/ld+json"]').inner_text())
    assert data["@type"] == "SoftwareApplication"
    assert data["offers"]["price"] == "0" and data["offers"]["priceCurrency"] == "PLN"


def test_landmarks_and_sections(page: Page, site_url: str) -> None:
    page.goto(site_url)
    for selector in ["header", "main", "footer", "#dane", "#dla-kogo", "#instalacja", "#zasady", "#faq", ".hero-note"]:
        assert page.locator(selector).count() == 1, selector


def test_fonts_are_self_hosted_and_loaded(page: Page, site_url: str, requested_hosts: list[str]) -> None:
    page.goto(site_url)
    page.evaluate("document.fonts.ready")
    assert page.evaluate("document.fonts.check('700 16px Poppins')")
    assert page.evaluate("""document.fonts.check('500 16px "JetBrains Mono"')""")
    assert not [host for host in requested_hosts if "googleapis" in host or "gstatic" in host]


def test_every_install_command_has_a_copy_button(page: Page, site_url: str) -> None:
    page.goto(site_url)
    commands = page.locator("button.copy").evaluate_all("buttons => buttons.map(b => b.dataset.command)")
    assert set(COMMANDS) <= set(commands)


def test_install_ctas_keep_readable_contrast_on_hover(page: Page, site_url: str) -> None:
    page.goto(site_url)
    buttons = page.get_by_role("link", name="Zainstaluj za darmo", exact=True).all()
    assert buttons
    for button in buttons:
        button.hover()
        contrast = button.evaluate(
            r"""el => {
              const luminance = color => color.match(/[\d.]+/g).slice(0, 3)
                .map(Number).map(c => c / 255)
                .map(c => c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4)
                .reduce((sum, c, i) => sum + c * [0.2126, 0.7152, 0.0722][i], 0);
              const style = getComputedStyle(el);
              const text = luminance(style.color), background = luminance(style.backgroundColor);
              return (Math.max(text, background) + 0.05) / (Math.min(text, background) + 0.05);
            }"""
        )
        assert contrast >= 4.5, f"Hovered install button has {contrast:.2f}:1 text contrast"


def test_demo_waits_until_in_view(page: Page, site_url: str) -> None:
    page.set_viewport_size({"width": 1440, "height": 600})
    page.goto(site_url)
    page.wait_for_timeout(500)
    assert demo_state(page) == "ready"


def test_demo_plays_to_done_in_view(page: Page, site_url: str, new_context) -> None:
    page.goto(site_url)
    page.locator("#demo").scroll_into_view_if_needed()
    page.wait_for_selector("#demo[data-demo=done]", timeout=15000)
    assert page.locator("#demo [data-step=row]:visible").count() == len(DEMO["rows"])
    assert page.locator("#demo .chip-state").inner_text() == "gotowe"
    assert page.locator("#demo").inner_text() == final_text(new_context, site_url)


def test_demo_is_final_under_reduced_motion(page: Page, site_url: str) -> None:
    page.emulate_media(reduced_motion="reduce")
    page.goto(site_url)
    page.locator("#demo").scroll_into_view_if_needed()
    page.wait_for_timeout(300)
    assert demo_state(page) == "final"
    assert page.locator("#demo [data-step]:not(:visible)").count() == 0
    assert page.locator("#demo [data-step=row]").count() == len(DEMO["rows"])


def test_demo_is_complete_without_js(new_context, site_url: str) -> None:
    page = new_context(java_script_enabled=False).new_page()
    page.goto(site_url)
    assert page.locator("#demo [data-step=row]").count() == len(DEMO["rows"])
    assert page.locator("#demo .chip-state").inner_text() == "gotowe"
    assert page.locator("#demo .composer-text").get_attribute("data-question") == DEMO["question"]


def test_demo_finishes_after_scrolling_away(page: Page, site_url: str, new_context) -> None:
    page.clock.install()
    page.goto(site_url)
    page.locator("#demo").scroll_into_view_if_needed()
    page.wait_for_selector("#demo[data-demo=playing]", state="attached")
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    page.clock.run_for(20_000)
    page.wait_for_selector("#demo[data-demo=done]", state="attached", timeout=2000)
    assert page.locator("#demo").inner_text() == final_text(new_context, site_url)


def test_demo_finishes_at_once_when_the_tab_is_hidden(page: Page, site_url: str, new_context) -> None:
    page.goto(site_url)
    page.locator("#demo").scroll_into_view_if_needed()
    page.wait_for_selector("#demo[data-demo=playing]", state="attached")
    page.evaluate(
        """() => {
          Object.defineProperty(document, 'hidden', { configurable: true, get: () => true });
          document.dispatchEvent(new Event('visibilitychange'));
        }"""
    )
    page.wait_for_selector("#demo[data-demo=done]", state="attached", timeout=1000)
    assert page.locator("#demo").inner_text() == final_text(new_context, site_url)


@pytest.mark.parametrize("width", [320, 375, 768, 1024, 1440])
def test_hero_skyline_never_cuts_a_crane(new_context, site_url: str, width: int) -> None:
    """Narrow screens crop the skyline; each crane is either whole in view or out of it, and one always shows."""
    phone = width < 768
    page = new_context(viewport={"width": width, "height": 900}, is_mobile=phone, has_touch=phone).new_page()
    page.goto(site_url)
    cranes = page.locator(".hero-art .crane").evaluate_all(
        "cranes => cranes.map((crane) => { const box = crane.getBoundingClientRect(); return [box.left, box.right]; })"
    )
    assert len(cranes) == 2
    whole = [left >= -1 and right <= width + 1 for left, right in cranes]
    gone = [right <= 0 or left >= width for left, right in cranes]
    assert all(w or g for w, g in zip(whole, gone)), cranes
    assert any(whole), cranes
