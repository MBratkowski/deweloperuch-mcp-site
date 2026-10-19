"""The probe core: request guard, cell setup and failure handling."""

import http.server
import socket
import threading
from collections.abc import Callable, Iterator

import pytest
from playwright.sync_api import Browser

from tools.factory.findings import Cell
from tools.factory.probe import ProbeError, Target, open_cell


def test_umami_is_stubbed_and_other_hosts_blocked(
    browser: Browser, fixtures_url: str, probe_cell: Callable[..., Cell]
) -> None:
    run = open_cell(browser, Target("fixtures", fixtures_url), probe_cell("external", 375), "probe/external.html")
    try:
        assert run.requests.blocked == ["example.com"]
        assert "cloud.umami.is" in run.requests.hosts
        assert run.requests.failed == []
        seen = []
        run.context.on("response", lambda r: "cloud.umami.is" in r.url and seen.append(r))
        run.page.reload()
        assert [(r.status, r.body()) for r in seen] == [(200, b"")]
        assert set(run.requests.hosts) <= {"127.0.0.1", "cloud.umami.is", "example.com"}
    finally:
        run.close()


class _Unavailable(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(503)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def unavailable_url() -> Iterator[str]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Unavailable)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/"
    server.shutdown()
    server.server_close()


def test_unreachable_site_raises(
    browser: Browser, unavailable_url: str, probe_cell: Callable[..., Cell]
) -> None:
    with pytest.raises(ProbeError):
        open_cell(browser, Target("down", unavailable_url), probe_cell("p", 375), "")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with pytest.raises(ProbeError):
        open_cell(browser, Target("refused", f"http://127.0.0.1:{port}/"), probe_cell("p", 375), "")


def test_engine_must_match_the_browser(
    browser: Browser, fixtures_url: str, probe_cell: Callable[..., Cell]
) -> None:
    with pytest.raises(AssertionError):
        open_cell(browser, Target("f", fixtures_url), probe_cell("p", 375, engine="webkit"), "clean/index.html")


def test_no_js_mode_disables_scripts(
    browser: Browser, fixtures_url: str, probe_cell: Callable[..., Cell]
) -> None:
    run = open_cell(browser, Target("f", fixtures_url), probe_cell("p", 375, mode="no-js"), "clean/index.html")
    try:
        assert run.page.viewport_size == {"width": 375, "height": 900}
        assert run.console == []
    finally:
        run.close()


def test_missing_stylesheet_raises(
    browser: Browser, fixtures_url: str, probe_cell: Callable[..., Cell]
) -> None:
    with pytest.raises(ProbeError, match="missing.css"):
        open_cell(browser, Target("f", fixtures_url), probe_cell("p", 375), "probe/missing-css.html")


def test_missing_image_is_only_recorded(
    browser: Browser, fixtures_url: str, probe_cell: Callable[..., Cell]
) -> None:
    run = open_cell(browser, Target("f", fixtures_url), probe_cell("p", 375), "probe/missing-image.html")
    try:
        assert any(e.startswith("404 ") and e.endswith("/probe/missing.png") for e in run.requests.bad_status)
        assert run.requests.critical == []
    finally:
        run.close()


def test_menu_mode_opens_the_menu(
    browser: Browser, fixtures_url: str, probe_cell: Callable[..., Cell]
) -> None:
    run = open_cell(browser, Target("f", fixtures_url), probe_cell("p", 375, mode="menu"), "probe/menu.html")
    try:
        assert run.page.get_attribute(".nav-toggle", "aria-expanded") == "true"
    finally:
        run.close()


def test_menu_mode_without_a_toggle_raises(
    browser: Browser, fixtures_url: str, probe_cell: Callable[..., Cell]
) -> None:
    with pytest.raises(ProbeError):
        open_cell(browser, Target("f", fixtures_url), probe_cell("p", 375, mode="menu"), "clean/index.html")


def test_keyboard_mode_focuses_an_element(
    browser: Browser, fixtures_url: str, probe_cell: Callable[..., Cell]
) -> None:
    run = open_cell(browser, Target("f", fixtures_url), probe_cell("p", 375, mode="keyboard"), "clean/index.html")
    try:
        assert run.page.evaluate("document.activeElement.tagName") == "A"
    finally:
        run.close()
