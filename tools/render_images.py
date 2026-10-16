"""Render the icons and the semantic hero capture, using only local assets.

    uv run python -m tools.build_docs
    uv run python -m tools.render_images
    uv run python -m tools.render_images --check

Review assets/og.png after regeneration. The manifest checks source/output
freshness without comparing pixels from different renderer environments.
"""

import argparse
import functools
import hashlib
import http.server
import json
import platform
import sys
import threading
from importlib.metadata import version
from pathlib import Path

from PIL import Image
from playwright.sync_api import Error as PlaywrightError, Page, Route, sync_playwright

from tools.build_docs import render_pages
from tools.release import BuildError, load_release

ROOT = Path(__file__).resolve().parent.parent
TILE = ROOT / "assets/icon/icon-tile-512.png"
MANIFEST = "assets/images.json"
OUTPUTS = ("favicon.png", "apple-touch-icon.png", "assets/og.png")
OG_CONTEXT = {"viewport": {"width": 1200, "height": 630}, "reduced_motion": "reduce", "device_scale_factor": 1}
HIDDEN_ROLES = {"navigation", "version", "actions", "note"}
CONTENT_ROLES = {"heading", "description", "skyline"}
# Visibility preserves the hero's original spacing. Descendants cannot opt back in.
OG_HIDE = "[data-social-hide], [data-social-hide] * { visibility: hidden !important; }"
REGENERATE = "Run python -m tools.build_docs, then uv run python -m tools.render_images."


class ImageRenderError(RuntimeError):
    """The page cannot produce the intended social composition."""


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


def render_favicons(tile: Path = TILE, out_dir: Path = ROOT) -> None:
    with Image.open(tile) as image:
        image.resize((32, 32), Image.LANCZOS).save(out_dir / "favicon.png", optimize=True)
        image.resize((180, 180), Image.LANCZOS).save(out_dir / "apple-touch-icon.png", optimize=True)


def prepare_og(page: Page) -> None:
    """Apply and verify the social contract before taking any screenshot."""
    for attribute, expected in (("data-social-hide", HIDDEN_ROLES), ("data-social-content", CONTENT_ROLES)):
        roles = page.locator(f"[{attribute}]").evaluate_all(
            "(elements, attribute) => elements.map(e => e.getAttribute(attribute))", attribute,
        )
        if len(roles) != len(expected) or set(roles) != expected:
            raise ImageRenderError(f"Invalid social hooks for {attribute}: expected {sorted(expected)}, got {roles}")
    page.add_style_tag(content=OG_HIDE)
    page.evaluate("document.fonts.ready")
    for role in sorted(HIDDEN_ROLES):
        if page.locator(f'[data-social-hide="{role}"]').is_visible():
            raise ImageRenderError(f"Social control {role} is visible")
    viewport = page.viewport_size
    if viewport != OG_CONTEXT["viewport"]:
        raise ImageRenderError("Social capture requires the 1200×630 viewport")
    for role in sorted(CONTENT_ROLES):
        element = page.locator(f'[data-social-content="{role}"]')
        box = element.bounding_box()
        if not element.is_visible() or not box or element.evaluate("e => getComputedStyle(e).opacity") == "0":
            raise ImageRenderError(f"Social content {role} is hidden")
        right, bottom = box["x"] + box["width"], box["y"] + box["height"]
        if role == "skyline":
            if box["x"] >= viewport["width"] or box["y"] >= viewport["height"] or right <= 0 or bottom <= 0:
                raise ImageRenderError("Social skyline is outside the capture")
        else:
            inside = box["x"] >= 0 and box["y"] >= 0 and right <= viewport["width"] and bottom <= viewport["height"]
            if not element.inner_text().strip() or not inside:
                raise ImageRenderError(f"Social content {role} is empty or clipped")


def render_og(site_url: str, out: Path = ROOT / "assets/og.png") -> str:
    """Capture verified content; return the Chromium version for provenance."""
    def local_only(route: Route) -> None:
        if route.request.url.startswith(site_url):
            route.continue_()
        else:
            route.abort()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            context = browser.new_context(**OG_CONTEXT)
            context.route("**/*", local_only)
            page = context.new_page()
            failures: list[str] = []
            page.on("response", lambda response: failures.append(response.url) if response.status >= 400 else None)
            page.on("requestfailed", lambda request: failures.append(request.url) if request.url.startswith(site_url) else None)
            page.goto(site_url)
            page.wait_for_load_state("networkidle")
            prepare_og(page)
            if failures:
                raise ImageRenderError(f"Local image inputs failed to load: {failures}")
            page.screenshot(path=out, animations="disabled")
            return browser.version
        finally:
            browser.close()


def hashes(root: Path, paths: list[str] | tuple[str, ...]) -> dict[str, str]:
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in sorted(paths)}


def input_hashes(root: Path) -> dict[str, str]:
    # Canonical HTML represents all upstream templates, shared fragments/config,
    # release metadata and saved demo. Reject stale HTML before capturing it.
    landing = render_pages(load_release(root), root)["index.html"]
    if (root / "index.html").read_text(encoding="utf-8") != landing:
        raise BuildError(f"index.html differs from its current source inputs. {REGENERATE}")
    paths = {"index.html", "tools/render_images.py", "uv.lock"}
    # Include the complete local resource families, so additions are tracked too.
    for pattern in ("css/*.css", "js/*.js", "assets/fonts/*.woff2", "assets/icon/*.png"):
        paths.update(path.relative_to(root).as_posix() for path in root.glob(pattern))
    return hashes(root, sorted(paths))


def check_images(root: Path = ROOT) -> list[str]:
    """Check committed source/output digests, without launching a browser or writing."""
    try:
        manifest = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or manifest.get("schema") != 1:
            raise ValueError("expected image manifest schema 1")
        if not all(isinstance(manifest.get(key), dict) for key in ("inputs", "outputs", "renderer")):
            raise ValueError("expected inputs, outputs and renderer records")
        actual = {"inputs": input_hashes(root), "outputs": hashes(root, OUTPUTS)}
    except (OSError, ValueError, BuildError) as error:
        return [f"Image freshness check failed: {error}. {REGENERATE}"]
    errors = []
    for group, current in actual.items():
        recorded = manifest[group]
        changed = sorted(name for name in current.keys() | recorded.keys() if current.get(name) != recorded.get(name))
        if changed:
            errors.append(f"Stale image {group}: {', '.join(changed)}. {REGENERATE}")
    # Renderer versions/platform are provenance, not an exact-pixel requirement.
    return errors


def main(root: Path = ROOT, *, check: bool = False) -> int:
    if check:
        errors = check_images(root)
        if errors:
            print("\n".join(errors), file=sys.stderr)
            return 1
        print("Image inputs and output hashes match assets/images.json")
        return 0
    try:
        inputs = input_hashes(root)
        handler = functools.partial(_QuietHandler, directory=str(root))
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            render_favicons(root / "assets/icon/icon-tile-512.png", root)
            chromium = render_og(f"http://127.0.0.1:{server.server_port}/", root / "assets/og.png")
        finally:
            server.shutdown()
            server.server_close()
        manifest = {
            "schema": 1, "inputs": inputs, "outputs": hashes(root, OUTPUTS),
            "renderer": {"chromium": chromium, "playwright": version("playwright"), "pillow": version("Pillow"),
                         "platform": platform.system(), "architecture": platform.machine()},
        }
        (root / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except (OSError, ValueError, BuildError, ImageRenderError, PlaywrightError) as error:
        print(f"Image rendering stopped: {error}", file=sys.stderr)
        return 1
    print("favicon.png, apple-touch-icon.png, assets/og.png, assets/images.json")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check portable source/output freshness without rendering")
    sys.exit(main(check=parser.parse_args().check))
