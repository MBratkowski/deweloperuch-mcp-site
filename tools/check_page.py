"""Headless checks of the landing page; also renders the OG image and favicons."""
import functools
import http.server
import sys
import threading
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image
from playwright.sync_api import sync_playwright

repo = Path(sys.argv[1]).resolve()
shots = Path(sys.argv[2]).resolve()
shots.mkdir(parents=True, exist_ok=True)
handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(repo))
handler.log_message = lambda *a, **k: None
server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{server.server_port}/"
problems = []

tile = Image.open(repo / "assets/icon/icon-tile-512.png")
tile.resize((32, 32), Image.LANCZOS).save(repo / "favicon.png", optimize=True)
tile.resize((180, 180), Image.LANCZOS).save(repo / "apple-touch-icon.png", optimize=True)

with sync_playwright() as p:
    browser = p.chromium.launch()

    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, permissions=["clipboard-read", "clipboard-write"])
    page = ctx.new_page()
    errors, hosts = [], set()
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("request", lambda r: hosts.add(urlparse(r.url).hostname))
    page.goto(url)
    page.wait_for_load_state("networkidle")
    if not page.evaluate("document.fonts.check('700 16px Poppins')"):
        problems.append("Poppins 700 not loaded")
    page.screenshot(path=shots / "desktop-top.png")
    page.locator("#demo").scroll_into_view_if_needed()
    try:
        page.wait_for_selector("#demo[data-demo=done]", timeout=20000)
    except Exception:
        problems.append("demo did not finish: " + str(page.locator("#demo").get_attribute("data-demo")))
    page.locator("#demo").screenshot(path=shots / "demo.png")
    for button in page.locator("button.copy").all():
        command = button.get_attribute("data-command")
        button.scroll_into_view_if_needed()
        button.click()
        got = page.evaluate("navigator.clipboard.readText()")
        if got != command:
            problems.append(f"copy {command!r} gave {got!r}")
        if button.inner_text().strip() != "Skopiowano":
            problems.append(f"label after copy: {button.inner_text()!r}")
        visible = button.evaluate("b => b.parentElement.querySelector('.cmd-text').textContent")
        if visible != command:
            problems.append(f"visible {visible!r} != {command!r}")
    page.screenshot(path=shots / "desktop-full.png", full_page=True)
    external = {h for h in hosts if h not in ("127.0.0.1", "localhost", None)}
    if external:
        problems.append(f"external requests: {external}")
    if ctx.cookies():
        problems.append("cookies set")
    if errors:
        problems.append(f"console errors: {errors}")
    for selector in ["header", "main", "footer", "#dane", "#dla-kogo", "#instalacja", "#zasady", "#faq", ".hero-note"]:
        if page.locator(selector).count() != 1:
            problems.append(f"{selector} count {page.locator(selector).count()}")
    for href in page.locator("a[href^='#']").evaluate_all("els => els.map(e => e.getAttribute('href'))"):
        if href != "#" and page.locator(href).count() != 1:
            problems.append(f"anchor {href} missing")
    ctx.close()

    for width in (320, 375):
        ctx = browser.new_context(viewport={"width": width, "height": 800}, is_mobile=True)
        page = ctx.new_page()
        page.goto(url)
        page.wait_for_load_state("networkidle")
        sw = page.evaluate("document.documentElement.scrollWidth")
        if sw > width:
            wide = page.evaluate(f"""Array.from(document.querySelectorAll('body *')).filter(e => e.getBoundingClientRect().right > {width} + 1).slice(0, 5).map(e => e.tagName + '.' + e.className + ' ' + Math.round(e.getBoundingClientRect().right))""")
            problems.append(f"horizontal scroll at {width}: {sw} {wide}")
        page.screenshot(path=shots / f"mobile-{width}.png", full_page=True)
        ctx.close()

    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = ctx.new_page()
    page.goto(url)
    if page.locator("#demo").get_attribute("data-demo") != "final":
        problems.append("reduced motion: demo not final")
    ctx.close()

    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, java_script_enabled=False)
    page = ctx.new_page()
    page.goto(url)
    if page.locator("button.copy:visible").count():
        problems.append("copy buttons visible without JS")
    ctx.close()

    ctx = browser.new_context(viewport={"width": 1200, "height": 630}, reduced_motion="reduce", device_scale_factor=1)
    page = ctx.new_page()
    page.goto(url)
    page.wait_for_load_state("networkidle")
    page.add_style_tag(content="header{visibility:hidden} .hero-copy > a:first-child, .hero-copy > div:last-of-type, .hero-note{visibility:hidden}")
    page.screenshot(path=repo / "assets/og.png")
    ctx.close()
    browser.close()

server.shutdown()
print("PROBLEMS:" if problems else "ALL OK")
for p_ in problems:
    print(" -", p_)
