"""Export a release announcement for X/Twitter, using only local assets.

    uv run python -m tools.render_release data/releases/0.1.3.json
    uv run python -m tools.render_release release.json --output announcement.png
"""

import argparse
from datetime import date
import functools
from html import escape
import http.server
import json
from pathlib import Path
import re
from string import Template
import sys
import threading

from playwright.sync_api import Error as PlaywrightError, Page, Route, sync_playwright

from tools.page_config import PACKAGE, SITE
from tools.release import ROOT

LABELS = {"added": "Nowość", "changed": "Ulepszenie", "fixed": "Poprawka"}
VIEWPORT = {"width": 1200, "height": 630}


class ReleaseCardError(ValueError):
    """Invalid release data or a composition that cannot be exported."""


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


def validate_card(card: object) -> dict:
    """Reject invalid fields before starting a browser or deriving an output path."""
    if not isinstance(card, dict) or set(card) != {"version", "date", "headline", "changes"}:
        raise ReleaseCardError("release: expected exactly version, date, headline and changes")
    for field in ("version", "date", "headline"):
        if not isinstance(card[field], str) or not card[field].strip():
            raise ReleaseCardError(f"{field}: expected nonempty text")
    if len(card["version"]) > 32 or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+[A-Za-z0-9.+-]*", card["version"]):
        raise ReleaseCardError("version: expected a version such as 0.1.3 or 0.2.0rc1 (without v)")
    try:
        if date.fromisoformat(card["date"]).isoformat() != card["date"]:
            raise ValueError
    except ValueError as error:
        raise ReleaseCardError("date: expected a real date in YYYY-MM-DD format") from error
    if not isinstance(card["changes"], list) or not 1 <= len(card["changes"]) <= 3:
        raise ReleaseCardError("changes: expected one to three highlights")
    for index, change in enumerate(card["changes"]):
        if not isinstance(change, dict) or set(change) != {"type", "title", "description"}:
            raise ReleaseCardError(f"changes[{index}]: expected type, title and description")
        if not isinstance(change["type"], str) or change["type"] not in LABELS:
            raise ReleaseCardError(f"changes[{index}].type: expected added, changed or fixed")
        for field in ("title", "description"):
            if not isinstance(change[field], str) or not change[field].strip():
                raise ReleaseCardError(f"changes[{index}].{field}: expected nonempty text")
    return card


def build_document(card: dict, root: Path = ROOT) -> str:
    """Render plain release text into the local, brand-matched card template."""
    validate_card(card)
    changes = []
    for change in card["changes"]:
        kind = change["type"]
        changes.append(
            f'<article class="change change--{kind}" data-card-region>'
            f'<span class="change-label" data-card-text>{LABELS[kind]}</span>'
            f'<h2 data-card-text>{escape(change["title"])}</h2>'
            f'<p data-card-text>{escape(change["description"])}</p></article>'
        )
    return Template((root / "tools/templates/release_card.html").read_text(encoding="utf-8")).substitute(
        styles=(root / "tools/templates/release_card.css").read_text(encoding="utf-8"),
        package=escape(PACKAGE), version=escape(card["version"]),
        date=date.fromisoformat(card["date"]).strftime("%d.%m.%Y"),
        headline=escape(card["headline"]), changes="\n".join(changes),
        site_url=escape(SITE.base_url), site_label=escape(SITE.base_url.removeprefix("https://").rstrip("/")),
    )


def prepare_capture(page: Page) -> None:
    """Wait for fonts and reject missing, hidden or clipped content before export."""
    page.evaluate("document.fonts.ready")
    problems = page.evaluate("""() => {
      const card = document.querySelector('[data-release-card]');
      if (!card) return ['missing release card'];
      const frame = card.getBoundingClientRect();
      if (frame.x !== 0 || frame.y !== 0 || frame.width !== 1200 || frame.height !== 630)
        return ['card must occupy the complete 1200×630 frame'];
      const issues = [];
      const outside = (box, bounds) => box.left < bounds.left - 1 || box.top < bounds.top - 1
        || box.right > bounds.right + 1 || box.bottom > bounds.bottom + 1;
      for (const element of card.querySelectorAll('[data-card-text]')) {
        const label = element.textContent.trim().slice(0, 60);
        if (!label || !element.checkVisibility({checkOpacity: true, checkVisibilityCSS: true})) {
          issues.push('empty or hidden text: ' + label); continue;
        }
        const region = element.closest('[data-card-region]').getBoundingClientRect();
        const range = document.createRange(); range.selectNodeContents(element);
        if (Array.from(range.getClientRects()).some(box => outside(box, frame) || outside(box, region)))
          issues.push('text does not fit its region: ' + label);
      }
      for (const image of card.querySelectorAll('img')) {
        if (!image.complete || !image.naturalWidth) issues.push('missing image: ' + image.getAttribute('src'));
      }
      return issues;
    }""")
    if problems:
        raise ReleaseCardError("; ".join(problems) + ". Shorten the copy or adjust the card template.")


def render_image(card: dict, output: Path, root: Path = ROOT) -> None:
    """Capture the card in Chromium; all requests must stay on the local server."""
    document = build_document(card, root)
    handler = functools.partial(_QuietHandler, directory=str(root))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    site_url = f"http://127.0.0.1:{server.server_port}/"
    card_url = site_url + "__release_card__.html"
    failures: list[str] = []

    def local_only(route: Route) -> None:
        if route.request.url == card_url:
            route.fulfill(status=200, content_type="text/html; charset=utf-8", body=document)
        elif route.request.url.startswith(site_url):
            route.continue_()
        else:
            failures.append(route.request.url)
            route.abort()

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                context = browser.new_context(viewport=VIEWPORT, device_scale_factor=1, reduced_motion="reduce")
                context.route("**/*", local_only)
                page = context.new_page()
                page.on("response", lambda response: failures.append(response.url) if response.status >= 400 else None)
                page.on("requestfailed", lambda request: failures.append(request.url))
                page.goto(card_url, wait_until="networkidle")
                prepare_capture(page)
                if failures:
                    raise ReleaseCardError(f"Card assets failed to load: {failures}")
                image = page.screenshot(type="png", animations="disabled")
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(image)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release", type=Path, help="JSON file: version, date, headline and changes")
    parser.add_argument("--output", type=Path, help="PNG destination (default: exports/release-v<VERSION>.png)")
    args = parser.parse_args(argv)
    try:
        card = validate_card(json.loads(args.release.read_text(encoding="utf-8")))
        output = args.output or ROOT / "exports" / f'release-v{card["version"]}.png'
        render_image(card, output)
    except (OSError, ValueError, KeyError, PlaywrightError) as error:
        print(f"Release card stopped: {error}", file=sys.stderr)
        return 1
    print(f'{output} — {PACKAGE} v{card["version"]}, 1200×630 PNG')
    return 0


if __name__ == "__main__":
    sys.exit(main())
