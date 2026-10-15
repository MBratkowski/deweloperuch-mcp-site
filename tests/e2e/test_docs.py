"""The docs page: one card per tool and prompt of the saved server listing."""

import json
from pathlib import Path

from playwright.sync_api import Page

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
