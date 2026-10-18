"""The GitHub client against a local stub server; nothing here leaves 127.0.0.1."""

import http.server
import json
import threading
from collections.abc import Iterator

import pytest

from tools.factory.github import GitHub, GitHubError


class _Stub(http.server.BaseHTTPRequestHandler):
    seen: list[dict] = []

    def log_message(self, format: str, *args: object) -> None:
        pass

    def _reply(self, status: int, body: object, headers: dict[str, str] | None = None) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        base = f"http://127.0.0.1:{self.server.server_port}"
        _Stub.seen.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}})
        if self.path.startswith("/pages") and "&page=2" not in self.path and "?page=2" not in self.path:
            link = f'<{base}/pages?page=2>; rel="next", <{base}/pages?page=2>; rel="last"'
            self._reply(200, [1, 2], {"Link": link})
        elif self.path.startswith("/pages"):
            self._reply(200, [3])
        elif self.path.startswith("/invalid"):
            self._reply(422, {"message": "Validation Failed"})
        else:
            self._reply(200, {"ok": True})


@pytest.fixture
def api_url() -> Iterator[str]:
    _Stub.seen = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def test_paginate_follows_link_headers(api_url: str) -> None:
    client = GitHub("o/r", token="t", api_url=api_url)
    assert client.paginate("/pages", {"per_page": 2}) == [1, 2, 3]


def test_request_sends_token_and_api_version(api_url: str) -> None:
    client = GitHub("o/r", token="t", api_url=api_url)
    assert client.request("GET", "/thing") == {"ok": True}
    headers = _Stub.seen[0]["headers"]
    assert headers["authorization"] == "Bearer t"
    assert headers["x-github-api-version"] == "2022-11-28"
    assert headers["accept"] == "application/vnd.github+json"


def test_token_falls_back_to_environment(api_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GH_TOKEN", "from-gh")
    GitHub("o/r", api_url=api_url).request("GET", "/thing")
    assert _Stub.seen[0]["headers"]["authorization"] == "Bearer from-gh"
    monkeypatch.setenv("GITHUB_TOKEN", "from-github")
    GitHub("o/r", api_url=api_url).request("GET", "/thing")
    assert _Stub.seen[1]["headers"]["authorization"] == "Bearer from-github"


def test_error_raises_with_status(api_url: str) -> None:
    client = GitHub("o/r", token="t", api_url=api_url)
    with pytest.raises(GitHubError) as error:
        client.request("GET", "/invalid")
    assert error.value.status == 422
    assert "Validation Failed" in error.value.message
