"""The social capture contract, independent of presentation classes and DOM order."""

from playwright.sync_api import Page
import pytest

from tools import render_images


def social_page(page: Page, site_url: str) -> None:
    page.set_viewport_size({"width": 1200, "height": 630})
    page.emulate_media(reduced_motion="reduce")
    page.goto(site_url)
    page.wait_for_load_state("networkidle")


def test_social_controls_are_hidden_after_wrapping_and_reordering(page: Page, site_url: str) -> None:
    social_page(page, site_url)
    # The hooks alone leave the published page's controls and copy visible.
    for selector in ("header", ".version-pill", ".hero-actions > a", ".cmd-pill", ".hero-note"):
        assert page.locator(selector).is_visible()
    render_images.prepare_og(page)
    before = page.screenshot(animations="disabled")
    page.evaluate("""() => {
      const version = document.querySelector('.version-pill');
      const wrapper = document.createElement('div');
      wrapper.style.display = 'contents';
      version.before(wrapper); wrapper.append(version);
      const extra = document.createElement('div'); extra.style.display = 'contents';
      document.querySelector('.hero-copy').append(extra);
      const actions = document.querySelector('.hero-actions');
      actions.prepend(actions.querySelector('.hero-note'));
      actions.append(actions.querySelector('a'));
    }""")
    render_images.prepare_og(page)
    for selector in ("header", ".version-pill", ".hero-actions > a", ".cmd-pill", ".hero-note"):
        assert not page.locator(selector).is_visible()
    assert page.locator("h1").is_visible()
    assert page.locator(".hero-lead").is_visible()
    assert page.locator(".hero-art").is_visible()
    # Both screenshots use the same browser/fonts; cross-platform pixels are not compared.
    assert page.screenshot(animations="disabled") == before


@pytest.mark.parametrize("selector", ['[data-social-hide="version"]', '[data-social-content="heading"]'])
def test_missing_social_hook_rejects_capture(page: Page, site_url: str, selector: str) -> None:
    social_page(page, site_url)
    page.locator(selector).evaluate("element => element.remove()")
    with pytest.raises(render_images.ImageRenderError, match="social"):
        render_images.prepare_og(page)


@pytest.mark.parametrize("change", ["hidden", "clipped", "empty"])
def test_incorrect_hero_composition_rejects_capture(page: Page, site_url: str, change: str) -> None:
    social_page(page, site_url)
    page.locator("h1").evaluate("""(element, change) => {
      if (change === 'hidden') element.style.visibility = 'hidden';
      if (change === 'clipped') element.style.transform = 'translateY(700px)';
      if (change === 'empty') element.textContent = '';
    }""", change)
    with pytest.raises(render_images.ImageRenderError, match="heading"):
        render_images.prepare_og(page)
