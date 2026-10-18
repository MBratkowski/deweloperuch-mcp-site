"""A small GitHub REST and GraphQL client on the standard library only."""

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

API_VERSION = "2022-11-28"
TIMEOUT_SECONDS = 30
_NEXT_LINK = re.compile(r'<([^>]+)>\s*;\s*rel="next"')
_MAX_REDIRECTS = 5


class GitHubError(Exception):
    """A GitHub response with a status of 400 or more."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"GitHub API error {status}: {message}")
        self.status = status
        self.message = message


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


class GitHub:
    def __init__(
        self, repo: str, token: str | None = None, api_url: str = "https://api.github.com"
    ) -> None:
        self.repo = repo
        self.token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        self.api_url = api_url.rstrip("/")
        self._opener = urllib.request.build_opener(_NoRedirect)

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "deweloperuch-mcp-site-factory",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _send(
        self,
        method: str,
        url: str,
        body: dict | None = None,
        headers: dict[str, str] | None = None,
        follow_redirects: bool = False,
    ) -> tuple[bytes, Any]:
        data = None if body is None else json.dumps(body).encode()
        request_headers = self._headers() if headers is None else headers
        if data is not None:
            request_headers = {**request_headers, "Content-Type": "application/json"}
        request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
        try:
            with self._opener.open(request, timeout=TIMEOUT_SECONDS) as response:
                return response.read(), response.headers
        except urllib.error.HTTPError as error:
            payload = error.read()
            if error.code in (301, 302, 303, 307, 308):
                if follow_redirects:
                    return b"", error.headers
                raise GitHubError(
                    error.code, f"unexpected redirect to {error.headers.get('Location')}"
                ) from None
            try:
                message = json.loads(payload).get("message", "")
            except (ValueError, AttributeError):
                message = payload.decode(errors="replace")
            raise GitHubError(error.code, message) from None

    def _url(self, path: str, params: dict | None = None) -> str:
        url = f"{self.api_url}{path}"
        return f"{url}?{urllib.parse.urlencode(params)}" if params else url

    def request(
        self, method: str, path: str, body: dict | None = None, params: dict | None = None
    ) -> Any:
        payload, _ = self._send(method, self._url(path, params), body)
        return json.loads(payload) if payload else None

    def paginate(self, path: str, params: dict | None = None) -> list[Any]:
        items: list[Any] = []
        url: str | None = self._url(path, params)
        while url:
            payload, headers = self._send("GET", url)
            items.extend(json.loads(payload))
            match = _NEXT_LINK.search(headers.get("Link", ""))
            url = match.group(1) if match else None
        return items

    def graphql(self, query: str, variables: dict) -> dict:
        result = self.request("POST", "/graphql", {"query": query, "variables": variables})
        if result.get("errors"):
            raise GitHubError(200, json.dumps(result["errors"]))
        return result["data"]

    def download(self, path: str) -> bytes:
        """Fetch bytes, following redirects without forwarding the token to other hosts."""
        url = self._url(path)
        headers = self._headers()
        for _ in range(_MAX_REDIRECTS):
            payload, response_headers = self._send("GET", url, headers=headers, follow_redirects=True)
            location = response_headers.get("Location")
            if not location:
                return payload
            url = urllib.parse.urljoin(url, location)
            if urllib.parse.urlsplit(url).netloc != urllib.parse.urlsplit(self.api_url).netloc:
                headers = {k: v for k, v in headers.items() if k != "Authorization"}
        raise GitHubError(0, "too many redirects")
