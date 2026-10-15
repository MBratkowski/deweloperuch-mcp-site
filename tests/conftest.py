import functools
import http.server
import os
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if os.environ.get("LANDING_NETWORK") == "1":
        return
    skip = pytest.mark.skip(reason="needs PyPI and uvx; run with LANDING_NETWORK=1")
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip)


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture(scope="session")
def site_url() -> Iterator[str]:
    """The repository root served over HTTP, as GitHub Pages serves the main branch."""
    handler = functools.partial(_QuietHandler, directory=str(ROOT))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/"
    server.shutdown()
    server.server_close()
