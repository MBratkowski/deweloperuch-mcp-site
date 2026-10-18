"""The shared in-page detectors report structured findings."""

from playwright.sync_api import Page

from tools.factory.detectors import FOCUS_PROBLEMS, WIDE_ELEMENTS


def test_wide_element_is_described(page: Page) -> None:
    page.set_viewport_size({"width": 375, "height": 900})
    page.set_content('<div id="w" style="width:500px">x</div>')
    found = page.evaluate(WIDE_ELEMENTS, 375)
    assert len(found) == 1
    assert found[0]["selector"] == "div#w"
    assert found[0]["measurement"] == "right=508 > 375"
    assert found[0]["box"][2] == 500


def test_unfocusable_outline_is_described(page: Page) -> None:
    page.set_content('<a href="#" style="outline:none">go</a>')
    result = page.evaluate(FOCUS_PROBLEMS)
    assert result["checked"] == 1
    assert result["problems"][0]["selector"] == "body > a"
    assert result["problems"][0]["measurement"].startswith("outline none")
    assert len(result["problems"][0]["box"]) == 4
