"""Test doubles for the factory; GitHub is never contacted from tests."""

from typing import Any

# The App's bot account as GitHub's public user record shows it (GET /users/<slug>[bot]); its
# noreply address is the commit identity of everything the factory commits (D46).
APP_SLUG = "factory-app"
APP_LOGIN = f"{APP_SLUG}[bot]"
APP_USER_ID = 41898282
APP_EMAIL = f"{APP_USER_ID}+{APP_LOGIN}@users.noreply.github.com"
APP_USER = ("GET", "/users/factory-app%5Bbot%5D")
APP_USER_RECORD = {"login": APP_LOGIN, "id": APP_USER_ID, "type": "Bot"}


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


class StatefulGitHub:
    """A small GitHub that keeps issues, pull requests, labels and comments, and changes them as the
    REST API does: enough of its surface for the factory's state machine end to end
    (tests/factory/test_scenario.py). Issues and pull requests share one numbering; every write is
    made by `actor`, which a test switches between the App and the owner.

    `head_of(branch)` names the commit a new pull request's head branch points at; `merged(pull)`
    is called on a merge and returns the merge commit. Every `factory:ready` the App adds is
    recorded with the issue's labels at that moment (`ready_by_app`), so a test can check that
    nothing past the fix is ever handed back to the fixer.
    """

    def __init__(
        self,
        repo: str,
        actor: str,
        *,
        head_of: Any = None,
        merged: Any = None,
        live_sha: str = "1" * 40,
        main_sha: str = "2" * 40,
    ) -> None:
        self.repo = repo
        self.api = f"/repos/{repo}"
        self.actor = actor
        self.app = actor
        self.items: dict[int, dict[str, Any]] = {}
        self.comments: dict[int, list[dict[str, Any]]] = {}
        self.calls: list[tuple[str, str, Any]] = []
        self.deleted_refs: list[str] = []
        self.ready_by_app: list[tuple[int, frozenset[str]]] = []
        self.head_of = head_of or (lambda branch: "f" * 40)
        self.merged = merged or (lambda pull: "e" * 40)
        self.live_sha, self.main_sha = live_sha, main_sha
        self._numbers = 0
        self._comment_ids = 0
        self._ticks = 0

    # --- state -------------------------------------------------------------------------------

    def _now(self) -> str:
        self._ticks += 1
        return f"2026-10-19T10:{self._ticks // 60:02d}:{self._ticks % 60:02d}Z"

    def _item(self, number: int) -> dict[str, Any]:
        from tools.factory.github import GitHubError

        if number not in self.items:
            raise GitHubError(404, "Not Found")
        return self.items[number]

    def labels(self, number: int) -> set[str]:
        return set(self.items[number]["labels"])

    def _create(self, title: str, body: str, labels: Any = (), pull: dict[str, Any] | None = None) -> dict[str, Any]:
        self._numbers += 1
        item = {
            "number": self._numbers,
            "title": title,
            "body": body,
            "state": "open",
            "state_reason": None,
            "user": {"login": self.actor},
            "labels": list(dict.fromkeys(labels)),
            "created_at": self._now(),
            "closed_at": None,
            "pull": pull,
        }
        self.items[item["number"]] = item
        self.comments[item["number"]] = []
        return item

    def _issue_view(self, item: dict[str, Any]) -> dict[str, Any]:
        view = {k: v for k, v in item.items() if k not in ("pull", "labels")}
        view["labels"] = [{"name": name} for name in item["labels"]]
        view["node_id"] = f"I_{item['number']}"
        if item["pull"] is not None:
            view["pull_request"] = {"merged_at": item["pull"]["merged_at"]}
        return view

    def _pull_view(self, item: dict[str, Any]) -> dict[str, Any]:
        pull = item["pull"]
        return {
            "number": item["number"],
            "node_id": f"PR_{item['number']}",
            "title": item["title"],
            "body": item["body"],
            "state": item["state"],
            "draft": pull["draft"],
            "user": item["user"],
            "labels": [{"name": name} for name in item["labels"]],
            "head": {"ref": pull["head"], "sha": pull["sha"], "repo": {"full_name": self.repo}},
            "base": {"ref": pull["base"]},
            "merged_at": pull["merged_at"],
            "merge_commit_sha": pull["merge_commit_sha"],
            "mergeable_state": "clean",
            "closed_at": item["closed_at"],
        }

    def close(self, number: int) -> None:
        item = self._item(number)
        item["state"], item["closed_at"] = "closed", self._now()

    def merge_by_hand(self, number: int) -> str:
        """The owner's merge in shadow mode: GitHub's merge button, outside the factory."""
        item = self._item(number)
        sha = self.merged(self._pull_view(item))
        item["pull"]["merged_at"] = self._now()
        item["pull"]["merge_commit_sha"] = sha
        self.close(number)
        return sha

    # --- the API -----------------------------------------------------------------------------

    def request(self, method: str, path: str, body: dict | None = None, params: dict | None = None) -> Any:
        import re
        import urllib.parse

        from tools.factory.github import GitHubError

        self.calls.append((method, path, body if body is not None else params))
        api = re.escape(self.api)
        params = params or {}

        def match(pattern: str) -> re.Match[str] | None:
            return re.fullmatch(api + pattern, path)

        if method == "GET" and match("/issues"):
            state = params.get("state", "open")
            wanted = {name for name in str(params.get("labels") or "").split(",") if name}
            return [
                self._issue_view(item)
                for item in self.items.values()
                if state in ("all", item["state"]) and wanted <= set(item["labels"])
            ]
        if method == "POST" and match("/issues"):
            return self._issue_view(self._create(body["title"], body["body"], body.get("labels") or ()))
        if found := match(r"/issues/(\d+)"):
            item = self._item(int(found.group(1)))
            if method == "GET":
                return self._issue_view(item)
            if method == "PATCH":
                if body.get("state") == "closed" and item["state"] != "closed":
                    self.close(item["number"])
                item["state_reason"] = body.get("state_reason", item["state_reason"])
                item["body"] = body.get("body", item["body"])
                return self._issue_view(item)
        if (found := match(r"/issues/(\d+)/labels")) and method == "POST":
            item = self._item(int(found.group(1)))
            for name in body["labels"]:
                if name == "factory:ready" and self.actor == self.app:
                    self.ready_by_app.append((item["number"], frozenset(item["labels"])))
                if name not in item["labels"]:
                    item["labels"].append(name)
            return [{"name": name} for name in item["labels"]]
        if (found := match(r"/issues/(\d+)/labels/(.+)")) and method == "DELETE":
            item = self._item(int(found.group(1)))
            name = urllib.parse.unquote(found.group(2))
            if name not in item["labels"]:
                raise GitHubError(404, "Label does not exist")
            item["labels"].remove(name)
            return [{"name": n} for n in item["labels"]]
        if found := match(r"/issues/(\d+)/comments"):
            number = int(found.group(1))
            self._item(number)
            if method == "GET":
                return list(self.comments[number])
            if method == "POST":
                self._comment_ids += 1
                comment = {
                    "id": self._comment_ids,
                    "user": {"login": self.actor},
                    "body": body["body"],
                    "created_at": self._now(),
                }
                self.comments[number].append(comment)
                return comment
        if (found := match(r"/issues/comments/(\d+)")) and method == "PATCH":
            for comment in (c for comments in self.comments.values() for c in comments):
                if comment["id"] == int(found.group(1)):
                    comment["body"] = body["body"]
                    return comment
            raise GitHubError(404, "Not Found")
        if method == "GET" and match("/pulls"):
            state = params.get("state", "open")
            head = str(params.get("head") or "")
            return [
                self._pull_view(item)
                for item in self.items.values()
                if item["pull"] is not None
                and state in ("all", item["state"])
                and (not head or head.split(":", 1)[-1] == item["pull"]["head"])
            ]
        if method == "POST" and match("/pulls"):
            pull = {
                "head": body["head"],
                "sha": self.head_of(body["head"]),
                "base": body["base"],
                "draft": False,
                "merged_at": None,
                "merge_commit_sha": None,
            }
            item = self._create(body["title"], body["body"], (), pull)
            return {"number": item["number"], "node_id": f"PR_{item['number']}"}
        if found := match(r"/pulls/(\d+)"):
            item = self._item(int(found.group(1)))
            if method == "GET":
                return self._pull_view(item)
            if method == "PATCH":
                item["title"] = body.get("title", item["title"])
                item["body"] = body.get("body", item["body"])
                return self._pull_view(item)
        if (found := match(r"/pulls/(\d+)/merge")) and method == "PUT":
            item = self._item(int(found.group(1)))
            pull = item["pull"]
            if item["state"] != "open" or body.get("sha") != pull["sha"]:
                raise GitHubError(409, "Head branch was modified")
            sha = self.merged(self._pull_view(item))
            pull["merged_at"], pull["merge_commit_sha"] = self._now(), sha
            self.close(item["number"])
            return {"merged": True, "sha": sha, "message": "Pull Request successfully merged"}
        if (found := match(r"/git/refs/heads/(.+)")) and method == "DELETE":
            self.deleted_refs.append(urllib.parse.unquote(found.group(1)))
            return None
        if method == "GET" and match("/actions/workflows/pages.yml/runs"):
            run = {"id": 1, "status": "completed", "conclusion": "success", "html_url": "https://x/runs/1"}
            return {"workflow_runs": [run]}
        if method == "GET" and match("/deployments"):
            return [{"id": 1, "sha": self.live_sha}]
        if method == "GET" and match("/deployments/1/statuses"):
            return [{"state": "success"}]
        if method == "GET" and match("/commits/main"):
            return {"sha": self.main_sha}
        raise AssertionError(f"the stateful fake has no {method} {path}")

    def paginate(self, path: str, params: dict | None = None) -> list[Any]:
        return self.request("GET", path, None, params) or []

    def graphql(self, query: str, variables: dict) -> dict:
        self.calls.append(("POST", "/graphql", variables))
        if "convertPullRequestToDraft" in query:
            number = int(str(variables["id"]).removeprefix("PR_"))
            self._item(number)["pull"]["draft"] = True
        return {}
