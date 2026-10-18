"""Open the site in a real browser, one cell (page x engine x width x mode) at a time."""

from __future__ import annotations

import functools
import http.server
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Browser, BrowserContext, Page, Request, Response, Route
from playwright.sync_api import Error as PlaywrightError

from tools.factory.config import load_config
from tools.factory.findings import Cell
from tools.package_site import package_site

LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost"})
ANALYTICS_HOSTS = frozenset({"cloud.umami.is", "gateway.umami.is"})
CRITICAL_RESOURCES = frozenset({"stylesheet", "script", "font"})
MENU_BUTTON = ".topbar button.nav-toggle"
NAVIGATION_TIMEOUT_MS = 30_000


class ProbeError(Exception):
    """The site could not be probed (unreachable, failing, or missing a control)."""


@dataclass(frozen=True)
class Target:
    name: str
    base_url: str


@dataclass
class RequestLog:
    hosts: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    bad_status: list[str] = field(default_factory=list)  # "STATUS URL" of same-host responses >= 400
    critical: list[str] = field(default_factory=list)  # stylesheet, script or font that failed
    aborted: list[str] = field(default_factory=list)  # URLs the guard aborted itself
    images: set[str] = field(default_factory=set)  # URLs requested as images (the images check owns them)


@dataclass
class PageRun:
    page: Page
    cell: Cell
    url: str
    console: list[str]
    requests: RequestLog
    context: BrowserContext

    def close(self) -> None:
        self.context.close()


@functools.cache
def _modes() -> dict[str, dict]:
    return load_config().matrix.modes


def guard_requests(context: BrowserContext, site_host: str) -> RequestLog:
    """Let through only the site's own host, stub Umami with an empty script, abort the rest."""
    log = RequestLog()
    allowed = LOCAL_HOSTS if site_host in LOCAL_HOSTS else frozenset({site_host})

    def handle(route: Route) -> None:
        url = route.request.url
        host = urlparse(url).hostname or ""
        log.hosts.append(host)
        if route.request.resource_type == "image":
            log.images.add(url)
        if host in allowed:
            route.continue_()
        elif host in ANALYTICS_HOSTS:
            route.fulfill(status=200, content_type="text/javascript", body="")
        else:
            log.blocked.append(host)
            log.aborted.append(url)
            route.abort()

    def on_failed(request: Request) -> None:
        if request.url in log.aborted:
            return
        log.failed.append(request.url)
        if (urlparse(request.url).hostname or "") in allowed and request.resource_type in CRITICAL_RESOURCES:
            log.critical.append(f"failed {request.resource_type} {request.url}")

    def on_response(response: Response) -> None:
        url = response.url
        if (urlparse(url).hostname or "") not in allowed or response.status < 400:
            return
        log.bad_status.append(f"{response.status} {url}")
        if response.request.resource_type in CRITICAL_RESOURCES:
            log.critical.append(f"{response.status} {response.request.resource_type} {url}")

    context.route("**/*", handle)
    context.on("requestfailed", on_failed)
    context.on("response", on_response)
    return log


def open_cell(browser: Browser, target: Target, cell: Cell, path: str) -> PageRun:
    """Open `path` of the target in a fresh context shaped by the cell. The caller closes the run."""
    assert browser.browser_type.name == cell.engine, (
        f"cell wants {cell.engine}, browser is {browser.browser_type.name}"
    )
    options = {k: v for k, v in _modes().get(cell.mode, {}).items() if k != "height"}
    base = target.base_url if target.base_url.endswith("/") else target.base_url + "/"
    url = base + path.lstrip("/")
    context = browser.new_context(viewport={"width": cell.width, "height": cell.height}, **options)
    try:
        requests = guard_requests(context, urlparse(url).hostname or "")
        page = context.new_page()
        console: list[str] = []
        page.on("console", lambda m: _on_console(m, console))
        page.on("pageerror", lambda e: console.append(str(e)))
        try:
            response = page.goto(url, wait_until="load", timeout=NAVIGATION_TIMEOUT_MS)
        except PlaywrightError as error:
            raise ProbeError(f"cannot open {url}: {error}") from error
        if response is None or response.status >= 400:
            status = "no response" if response is None else f"status {response.status}"
            raise ProbeError(f"cannot open {url}: {status}")
        if requests.critical:
            raise ProbeError(f"{url} is half-loaded: {'; '.join(requests.critical)}")
        if cell.mode == "menu":
            _open_menu(page, url)
        elif cell.mode == "keyboard":
            page.keyboard.press("Tab")  # keyboard modality: focus() now matches :focus-visible
        return PageRun(page, cell, url, console, requests, context)
    except BaseException:
        context.close()
        raise


def _on_console(message: ConsoleMessage, console: list[str]) -> None:
    # The browser's "Failed to load resource" lines belong to the network and images checks.
    if message.type == "error" and not message.text.startswith("Failed to load resource"):
        console.append(message.text)


def _open_menu(page: Page, url: str) -> None:
    button = page.locator(MENU_BUTTON)
    if button.count() == 0 or not button.first.is_visible():
        raise ProbeError(f"{url}: no visible {MENU_BUTTON}")
    try:
        button.first.click(timeout=5_000)
        page.wait_for_function(
            "selector => document.querySelector(selector)?.getAttribute('aria-expanded') === 'true'",
            arg=MENU_BUTTON,
            timeout=5_000,
        )
    except PlaywrightError as error:
        raise ProbeError(f"{url}: the menu did not open: {error}") from error


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


@contextmanager
def local_site(root: Path) -> Iterator[str]:
    """Package `root` into `_site` and serve it on 127.0.0.1; yields the base URL."""
    site = package_site(root)
    handler = functools.partial(_QuietHandler, directory=str(site))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
