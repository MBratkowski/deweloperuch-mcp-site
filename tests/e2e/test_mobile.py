"""Phone widths: the demo shows just the conversation; tables and commands fit without sideways scrolling."""

import json
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

ROOT = Path(__file__).resolve().parents[2]
DEMO = json.loads((ROOT / "data/demo.json").read_text(encoding="utf-8"))
PHONES = [320, 375]
PHONE_HEIGHT = 812

# Lines of a single-line command that break inside a word, as "before|after".
# A break is fine after a space, a slash or a hyphen inside a word (deweloperuch-|mcp), the
# places a reader expects; never inside an option such as "--" or "--from".
WORD_SPLITS = """() => {
  const word = (char) => /[\\p{L}\\p{N}]/u.test(char || '');
  const fine = (data, i) => data[i] === ' ' || ' /'.includes(data[i - 1])
    || (data[i - 1] === '-' && word(data[i - 2]) && word(data[i]));
  const splits = [];
  for (const el of document.querySelectorAll('.cmd-text')) {
    if (!el.checkVisibility() || el.closest('pre')) continue;
    const chars = [];
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      for (let i = 0; i < node.length; i += 1) chars.push([node, i]);
    }
    const data = el.textContent;
    let top = null;
    chars.forEach(([node, offset], i) => {
      const range = document.createRange();
      range.setStart(node, offset);
      range.setEnd(node, offset + 1);
      const rect = range.getClientRects()[0];
      if (!rect) return;
      if (top !== null && rect.top > top + 1 && !fine(data, i)) {
        splits.push(data.slice(Math.max(0, i - 12), i) + '|' + data.slice(i, i + 12));
      }
      top = rect.top;
    });
  }
  return splits;
}"""

# Badges that do not follow their parameter name on the same line.
DETACHED_BADGES = """() => Array.from(document.querySelectorAll('.req')).filter((badge) => {
  const name = badge.previousElementSibling.getBoundingClientRect();
  const box = badge.getBoundingClientRect();
  return !(box.top < name.bottom && box.bottom > name.top && box.left >= name.right);
}).map((badge) => badge.previousElementSibling.textContent)"""


def phone(new_context, url: str, width: int) -> Page:
    touch = width < 768
    page = new_context(
        viewport={"width": width, "height": PHONE_HEIGHT}, is_mobile=touch, has_touch=touch, reduced_motion="reduce"
    ).new_page()
    page.goto(url)
    return page


def open_all_details(page: Page) -> None:
    page.evaluate("document.querySelectorAll('details').forEach((d) => { d.open = true; })")


@pytest.mark.parametrize("width", PHONES)
def test_demo_shows_only_the_conversation_on_phones(new_context, site_url: str, width: int) -> None:
    page = phone(new_context, site_url, width)
    expect(page.locator("#demo .demo-chat")).to_be_visible()
    expect(page.locator("#demo .demo-sidebar")).to_be_hidden()
    expect(page.locator("#demo .demo-inspector")).to_be_hidden()


@pytest.mark.parametrize("width", PHONES)
def test_demo_offers_fit_the_window_on_phones(new_context, site_url: str, width: int) -> None:
    page = phone(new_context, site_url, width)
    assert page.locator("#demo .scroll-region").evaluate("el => el.scrollWidth <= el.clientWidth")
    frame = page.locator("#demo .offers-table-frame").bounding_box()
    rows = page.locator('#demo [data-step="row"]')
    expect(rows).to_have_count(len(DEMO["rows"]))
    for row in rows.all():
        for cell in row.locator("td").all():
            box = cell.bounding_box()
            assert box["width"] > 0, cell.inner_text()
            assert frame["x"] <= box["x"] and box["x"] + box["width"] <= frame["x"] + frame["width"] + 0.5, cell.inner_text()


def test_demo_fits_one_phone_screen(new_context, site_url: str) -> None:
    """The reference phone is 375×812; 320-wide phones have shorter screens, so there it only has to fit sideways.

    Body text uses the system font stack: about 765 px with the San Francisco and Roboto metrics of real
    phones, more where a wider fallback font wraps the investment names again (823 px on a bare Ubuntu
    runner). Allow 10% for that; the old stacked layout was 1,770 px."""
    page = phone(new_context, site_url, 375)
    assert page.locator("#demo").bounding_box()["height"] <= PHONE_HEIGHT * 1.1


def accessible_counts(page: Page, table) -> dict[str, int]:
    """Rows, column headers and cells of one table in Chromium's accessibility tree, not Playwright's own role guess."""
    cdp = page.context.new_cdp_session(page)
    root = cdp.send("DOM.getDocument", {"depth": 0})["root"]["nodeId"]
    path = table.evaluate("t => Array.from(document.querySelectorAll('table')).indexOf(t)")
    node = cdp.send("DOM.querySelectorAll", {"nodeId": root, "selector": "table"})["nodeIds"][path]
    return {role: len(cdp.send("Accessibility.queryAXTree", {"nodeId": node, "role": role})["nodes"]) for role in ("row", "columnheader", "cell")}


@pytest.mark.parametrize("path", ["", "docs/"], ids=["landing", "docs"])
def test_tables_keep_their_semantics_on_phones(new_context, site_url: str, browser_name: str, path: str) -> None:
    """Phones lay table rows out as grid and flex boxes; Chromium must still expose every row, header and cell."""
    if browser_name != "chromium":
        pytest.skip("reads Chromium's accessibility tree over CDP")
    page = phone(new_context, site_url + path, 375)
    open_all_details(page)
    tables = page.locator("#demo table, .ref-table").all()
    assert tables
    for table in tables:
        expected = {"row": table.locator("tr").count(), "columnheader": table.locator("th").count(), "cell": table.locator("td").count()}
        assert accessible_counts(page, table) == expected


@pytest.mark.parametrize("width", PHONES)
def test_docs_tables_fit_without_sideways_scrolling_on_phones(new_context, site_url: str, width: int) -> None:
    page = phone(new_context, site_url + "docs/", width)
    open_all_details(page)
    wide = page.evaluate("""() => Array.from(document.querySelectorAll('.table-wrap'))
      .filter((wrap) => wrap.scrollWidth > wrap.clientWidth + 1)
      .map((wrap) => wrap.querySelector('td code').textContent)""")
    assert wide == []


@pytest.mark.parametrize("width", [*PHONES, 1280])
def test_required_badge_follows_the_name_on_its_line(new_context, site_url: str, width: int) -> None:
    page = phone(new_context, site_url + "docs/", width)
    open_all_details(page)
    assert page.locator(".req").count() > 0
    assert page.evaluate(DETACHED_BADGES) == []


@pytest.mark.parametrize("path", ["", "docs/"], ids=["landing", "docs"])
@pytest.mark.parametrize("width", PHONES)
def test_commands_are_not_cut_off_on_phones(new_context, site_url: str, path: str, width: int) -> None:
    page = phone(new_context, site_url + path, width)
    cut = page.evaluate("""() => Array.from(document.querySelectorAll('.cmd-text'))
      .filter((el) => el.checkVisibility())
      .map((el) => el.closest('code, pre'))
      .filter((box) => box.scrollWidth > box.clientWidth + 1)
      .map((box) => box.textContent)""")
    assert cut == []


@pytest.mark.parametrize("path", ["", "docs/"], ids=["landing", "docs"])
@pytest.mark.parametrize("width", PHONES)
def test_commands_wrap_between_words_on_phones(new_context, site_url: str, path: str, width: int) -> None:
    page = phone(new_context, site_url + path, width)
    assert page.evaluate(WORD_SPLITS) == []
