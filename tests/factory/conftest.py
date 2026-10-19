import functools
import http.server
import threading
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from tools.factory.findings import Cell

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SHORT_HEIGHT = 500
HEIGHT = 900


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture(scope="session")
def fixtures_url() -> Iterator[str]:
    """tests/factory/fixtures/ served on 127.0.0.1."""
    handler = functools.partial(_QuietHandler, directory=str(FIXTURES))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/"
    server.shutdown()
    server.server_close()


@pytest.fixture
def probe_cell() -> Callable[..., Cell]:
    def make(page: str, width: int, engine: str = "chromium", mode: str = "default") -> Cell:
        return Cell(page, engine, width, SHORT_HEIGHT if mode == "short" else HEIGHT, mode)

    return make

# Fixture projects hold sample tests that only the tests under test run explicitly.
collect_ignore_glob = ["fixtures/*"]
