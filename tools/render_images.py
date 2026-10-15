"""Render favicon.png, apple-touch-icon.png and assets/og.png (the hero at 1200×630).

    uv run python -m tools.render_images

Re-run after changing the icon tile or the hero. Look at assets/og.png afterwards: the
heading and the skyline must be visible and not cut.
"""

import functools
import http.server
import sys
import threading
from pathlib import Path

from PIL import Image
from playwright.sync_api import Route, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
TILE = ROOT / "assets/icon/icon-tile-512.png"
# Hidden in the social image: the navigation, the CTA, the command row and the note.
OG_HIDE = "header{visibility:hidden} .hero-copy > a:first-child, .hero-copy > div:last-of-type, .hero-note{visibility:hidden}"


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


def render_favicons(tile: Path = TILE, out_dir: Path = ROOT) -> None:
    image = Image.open(tile)
    image.resize((32, 32), Image.LANCZOS).save(out_dir / "favicon.png", optimize=True)
    image.resize((180, 180), Image.LANCZOS).save(out_dir / "apple-touch-icon.png", optimize=True)


def render_og(site_url: str, out: Path = ROOT / "assets/og.png") -> None:
    def local_only(route: Route) -> None:
        if route.request.url.startswith(site_url):
            route.continue_()
        else:
            route.abort()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1200, "height": 630}, reduced_motion="reduce", device_scale_factor=1)
        context.route("**/*", local_only)
        page = context.new_page()
        page.goto(site_url)
        page.wait_for_load_state("networkidle")
        page.add_style_tag(content=OG_HIDE)
        page.screenshot(path=out)
        browser.close()


def main() -> int:
    handler = functools.partial(_QuietHandler, directory=str(ROOT))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        render_favicons()
        render_og(f"http://127.0.0.1:{server.server_port}/")
    finally:
        server.shutdown()
    print("favicon.png, apple-touch-icon.png, assets/og.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
