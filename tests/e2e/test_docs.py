"""The docs page: one card per tool and prompt of the saved server listing."""

import json
from pathlib import Path

import pytest
from playwright.sync_api import Page, Route, expect

ROOT = Path(__file__).resolve().parents[2]
TOOLS = json.loads((ROOT / "data/server/mcp_tools.json").read_text(encoding="utf-8"))["tools"]
PROMPTS = json.loads((ROOT / "data/server/mcp_prompts.json").read_text(encoding="utf-8"))["prompts"]


def test_one_card_per_tool_and_prompt(page: Page, site_url: str) -> None:
    page.goto(site_url + "docs/")
    assert page.locator(".tool").count() == len(TOOLS) + len(PROMPTS)
    for tool in TOOLS:
        assert page.locator(f"#tool-{tool['name']}").count() == 1, tool["name"]


def test_landmarks(page: Page, site_url: str) -> None:
    page.goto(site_url + "docs/")
    for selector in ["header", "main", "footer", "#krok-po-kroku", "#zainstaluj-z-ai", "#narzedzia", "#prompty"]:
        assert page.locator(selector).count() == 1, selector


def test_toc_tracks_scrolling_in_both_directions(page: Page, site_url: str) -> None:
    page.goto(site_url + "docs/")
    current = page.locator('.toc a[aria-current="location"]')
    expect(current).to_have_count(0)
    sections = [
        "krok-po-kroku", "zainstaluj-z-ai", "jak-to-dziala", "wymagania", "uv",
        "inne-aplikacje", "pierwsze-pytania", "ceny", "prompty", "narzedzia",
        "cli", "limity", "problemy", "prywatnosc",
    ]
    for section in sections + list(reversed(sections)):
        page.locator(f"#{section}").evaluate("el => el.scrollIntoView({behavior: 'instant'})")
        expect(current).to_have_count(1)
        expect(current).to_have_attribute("href", f"#{section}")
    page.evaluate("window.scrollTo({top: 0, behavior: 'instant'})")
    expect(current).to_have_count(0)


@pytest.mark.parametrize("motion", ["reduce", "no-preference"])
@pytest.mark.parametrize("fragment, section", [
    ("krok-po-kroku", "krok-po-kroku"),
    ("zainstaluj-z-ai", "zainstaluj-z-ai"),
    ("wymagania", "wymagania"),
    ("tool-get_offer", "narzedzia"),
    ("prywatnosc", "prywatnosc"),
])
def test_toc_tracks_direct_links(
    page: Page, site_url: str, fragment: str, section: str, motion: str,
) -> None:
    page.emulate_media(reduced_motion=motion)
    page.goto(site_url + f"docs/#{fragment}")
    current = page.locator('.toc a[aria-current="location"]')
    expect(current).to_have_count(1)
    expect(current).to_have_attribute("href", f"#{section}")


def wait_until_settled_at(page: Page, fragment: str) -> None:
    """Wait for a stable frame with the fragment's target at the top of the viewport."""
    settled = page.wait_for_function(
        """state => {
            const y = window.scrollY;
            const stable = state.previousY === y;
            state.previousY = y;
            return stable && Math.abs(document.getElementById(state.id).getBoundingClientRect().top) <= 1;
        }""",
        arg={"id": fragment, "previousY": None},
        polling="raf",
        timeout=5000,
    )
    assert settled.json_value() is True


@pytest.mark.parametrize("motion", ["reduce", "no-preference"])
def test_deep_link_lands_on_its_section_when_fonts_swap_late(page: Page, site_url: str, motion: str) -> None:
    # Swapped-in web fonts move the section after the load-time fragment scroll has started.
    # An animated scroll used to keep the old target and stop about 16 px short.
    def after_fragment_scroll_starts(route: Route) -> None:
        page.wait_for_function("window.scrollY > 0", polling="raf")
        route.continue_()

    page.emulate_media(reduced_motion=motion)
    page.route("**/assets/fonts/*.woff2", after_fragment_scroll_starts)
    page.goto(site_url + "docs/#jak-to-dziala")
    page.evaluate("document.fonts.ready")
    wait_until_settled_at(page, "jak-to-dziala")


@pytest.mark.parametrize("motion", ["reduce", "no-preference"])
def test_toc_tracks_anchor_navigation_and_history(page: Page, site_url: str, motion: str) -> None:
    page.emulate_media(reduced_motion=motion)
    page.goto(site_url + "docs/#jak-to-dziala")
    page.evaluate("document.fonts.ready")
    toc = page.get_by_role("navigation", name="Spis treści")
    current = toc.locator('a[aria-current="location"]')

    def expect_section(fragment: str) -> None:
        expect(page).to_have_url(site_url + f"docs/#{fragment}")
        # aria-current changes within the reading area, before smooth scrolling ends.
        # Wait for the native anchor and a stable frame before history saves its offset.
        wait_until_settled_at(page, fragment)
        expect(current).to_have_count(1)
        expect(current).to_have_attribute("href", f"#{fragment}")

    expect_section("jak-to-dziala")
    initial_history_length = page.evaluate("history.length")
    toc.get_by_role("link", name="Komendy w terminalu").click()
    expect_section("cli")
    assert page.evaluate("history.length") == initial_history_length + 1
    for _ in range(3):
        page.go_back()
        expect_section("jak-to-dziala")
        page.go_forward()
        expect_section("cli")
        assert page.evaluate("history.length") == initial_history_length + 1


def test_current_toc_link_is_visually_distinct(page: Page, site_url: str) -> None:
    page.goto(site_url + "docs/#jak-to-dziala")
    current = page.locator('.toc a[aria-current="location"]')
    expect(current).to_have_count(1)
    expect(current).to_have_attribute("href", "#jak-to-dziala")
    colors = "el => ({color: getComputedStyle(el).color, background: getComputedStyle(el).backgroundColor})"
    inactive = page.locator('.toc a[href="#wymagania"]').evaluate(colors)
    expect(current).not_to_have_css("color", inactive["color"])
    expect(current).not_to_have_css("background-color", inactive["background"])
