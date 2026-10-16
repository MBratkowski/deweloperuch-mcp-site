"""The docs page: one card per tool and prompt of the saved server listing."""

import json
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

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


def test_toc_tracks_anchor_navigation_and_history(page: Page, site_url: str) -> None:
    page.goto(site_url + "docs/#jak-to-dziala")
    toc = page.get_by_role("navigation", name="Spis treści")
    current = toc.locator('a[aria-current="location"]')
    expect(current).to_have_attribute("href", "#jak-to-dziala")
    toc.get_by_role("link", name="Komendy w terminalu").click()
    expect(page).to_have_url(site_url + "docs/#cli")
    expect(current).to_have_count(1)
    expect(current).to_have_attribute("href", "#cli")
    page.go_back()
    expect(current).to_have_attribute("href", "#jak-to-dziala")
    page.go_forward()
    expect(current).to_have_attribute("href", "#cli")


def test_current_toc_link_is_visually_distinct(page: Page, site_url: str) -> None:
    page.goto(site_url + "docs/#jak-to-dziala")
    current = page.locator('.toc a[aria-current="location"]')
    expect(current).to_have_count(1)
    expect(current).to_have_attribute("href", "#jak-to-dziala")
    colors = "el => ({color: getComputedStyle(el).color, background: getComputedStyle(el).backgroundColor})"
    inactive = page.locator('.toc a[href="#wymagania"]').evaluate(colors)
    expect(current).not_to_have_css("color", inactive["color"])
    expect(current).not_to_have_css("background-color", inactive["background"])
