"""A broken animation contract must leave the shipped conversation readable."""

import json
from pathlib import Path

import pytest
from playwright.sync_api import Page, Route, expect

ROOT = Path(__file__).resolve().parents[2]
DEMO = json.loads((ROOT / "data/demo.json").read_text(encoding="utf-8"))
SITE_SCRIPT = (ROOT / "js/site.js").read_text(encoding="utf-8")
ROLES = {
    "composer": ".composer-text",
    "chip-state": ".chip-state",
    "chip-dot": ".chip-dot",
}


def before_initialization(page: Page, mutation: str) -> None:
    """Edit the real DOM immediately before the real site's deferred script runs."""
    def script(route: Route) -> None:
        route.fulfill(content_type="text/javascript", body=mutation + "\n" + SITE_SCRIPT)

    page.route("**/js/site.js", script)


def assert_readable_final(page: Page) -> None:
    expect(page.locator("#demo")).to_have_attribute("data-demo", "final")
    page.wait_for_function(
        """() => [...document.querySelectorAll('#demo [data-step]')]
          .every(el => getComputedStyle(el).opacity === '1')"""
    )
    assert page.locator("#demo .is-typing, #demo .is-busy").count() == 0
    assert page.locator("#demo tbody tr").count() == len(DEMO["rows"])
    assert page.locator("#demo .composer-text").inner_text() == f"Pokaż historię ceny lokalu {DEMO['rows'][0]['name']}…"


@pytest.mark.parametrize("role", ROLES)
def test_missing_behavior_hook_keeps_the_final_html(
    page: Page, site_url: str, console_errors: list[str], role: str
) -> None:
    selector = json.dumps(ROLES[role])
    before_initialization(page, f"document.querySelector({selector}).removeAttribute('data-demo-role');")
    page.goto(site_url)
    assert_readable_final(page)
    assert page.locator("#demo .chip-state").inner_text() == "gotowe"
    assert console_errors == []


def test_missing_status_element_keeps_the_remaining_conversation_readable(
    page: Page, site_url: str, console_errors: list[str]
) -> None:
    before_initialization(page, "document.querySelector('#demo .chip-state').remove();")
    page.goto(site_url)
    page.locator("#demo").scroll_into_view_if_needed()
    assert_readable_final(page)
    assert console_errors == []


@pytest.mark.parametrize("question", [None, "", "   "])
def test_invalid_question_keeps_the_final_html(
    page: Page, site_url: str, console_errors: list[str], question: str | None
) -> None:
    mutation = (
        "delete document.querySelector('.composer-text').dataset.question;"
        if question is None
        else f"document.querySelector('.composer-text').dataset.question = {json.dumps(question)};"
    )
    before_initialization(page, mutation)
    page.goto(site_url)
    assert_readable_final(page)
    assert console_errors == []


@pytest.mark.parametrize("step", ["question", "chip", "arg", "result", "answer", "table", "row", "source"])
def test_missing_step_group_keeps_the_final_html(
    page: Page, site_url: str, console_errors: list[str], step: str
) -> None:
    before_initialization(
        page,
        f"document.querySelectorAll('[data-step={step}]').forEach(el => el.removeAttribute('data-step'));",
    )
    page.goto(site_url)
    assert_readable_final(page)
    assert console_errors == []


def test_initialization_failure_restores_the_final_html(
    page: Page, site_url: str, console_errors: list[str]
) -> None:
    before_initialization(
        page,
        "IntersectionObserver.prototype.observe = () => { throw new Error('observer failed'); };",
    )
    page.goto(site_url)
    assert_readable_final(page)
    assert page.locator("#demo .chip-state").inner_text() == "gotowe"
    assert console_errors == []


def test_playback_failure_restores_text_and_stops_indicators(
    page: Page, site_url: str, console_errors: list[str]
) -> None:
    before_initialization(
        page,
        """const setAttribute = Element.prototype.setAttribute;
        Element.prototype.setAttribute = function (name, value) {
          if (name === 'data-shown' && this.dataset.step === 'arg') {
            throw new Error('interrupted reveal');
          }
          return setAttribute.call(this, name, value);
        };""",
    )
    page.goto(site_url)
    page.locator("#demo").scroll_into_view_if_needed()
    assert_readable_final(page)
    assert page.locator("#demo .chip-state").inner_text() == "gotowe"
    assert console_errors == []


def test_hook_removed_during_playback_restores_the_final_html(
    page: Page, site_url: str, console_errors: list[str]
) -> None:
    page.goto(site_url)
    page.locator("#demo").scroll_into_view_if_needed()
    expect(page.locator("#demo .chip-state")).to_have_text("w toku", timeout=8000)
    page.locator("#demo .chip-state").evaluate("el => el.removeAttribute('data-demo-role')")
    assert_readable_final(page)
    assert page.locator("#demo .chip-state").inner_text() == "gotowe"
    assert console_errors == []


def test_visual_class_changes_do_not_break_playback(page: Page, site_url: str, console_errors: list[str]) -> None:
    before_initialization(
        page,
        """for (const name of ['composer-text', 'chip-state', 'chip-dot']) {
          document.querySelector('.' + name).classList.remove(name);
        }""",
    )
    page.goto(site_url)
    page.locator("#demo").scroll_into_view_if_needed()
    expect(page.locator("#demo")).to_have_attribute("data-demo", "done", timeout=15000)
    assert page.locator('#demo [data-demo-role="chip-state"]').inner_text() == "gotowe"
    assert console_errors == []
