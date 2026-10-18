"""Test doubles for the factory; GitHub is never contacted from tests."""

from typing import Any


class FakeGitHub:
    """Same surface as tools.factory.github.GitHub, answering from canned responses.

    Responses are keyed by (METHOD, path); `calls` records (method, path, body).
    A response that is an Exception instance is raised instead of returned.
    """

    def __init__(self, responses: dict[tuple[str, str], Any] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list[tuple[str, str, dict | None]] = []

    def _answer(self, method: str, path: str, body: dict | None) -> Any:
        self.calls.append((method, path, body))
        response = self.responses.get((method, path))
        if isinstance(response, Exception):
            raise response
        return response

    def request(
        self, method: str, path: str, body: dict | None = None, params: dict | None = None
    ) -> Any:
        return self._answer(method, path, body if body is not None else params)

    def paginate(self, path: str, params: dict | None = None) -> list[Any]:
        return self._answer("GET", path, params) or []

    def graphql(self, query: str, variables: dict) -> dict:
        return self._answer("POST", "/graphql", variables) or {}

    def download(self, path: str) -> bytes:
        return self._answer("GET", path, None) or b""
