"""The factory's commit identity is the App bot's own noreply address, or nothing (D46)."""

from typing import Any

import pytest

from tests.factory.fakes import APP_EMAIL, APP_SLUG, APP_USER, APP_USER_RECORD, FakeGitHub
from tools.factory.github import GitHubError
from tools.factory.identity import IdentityError, bot_email


def test_the_address_is_the_bots_id_and_login() -> None:
    gh = FakeGitHub({APP_USER: APP_USER_RECORD})
    assert bot_email(gh, APP_SLUG) == APP_EMAIL == "41898282+factory-app[bot]@users.noreply.github.com"
    assert gh.calls == [("GET", "/users/factory-app%5Bbot%5D", None)]


def test_the_slug_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FACTORY_APP_SLUG", APP_SLUG)
    assert bot_email(FakeGitHub({APP_USER: APP_USER_RECORD})) == APP_EMAIL


@pytest.mark.parametrize("slug", ["", "Factory", "factory app", "../users", "factory-app[bot]", "-x"])
def test_no_slug_no_address(slug: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FACTORY_APP_SLUG", slug)
    gh = FakeGitHub({APP_USER: APP_USER_RECORD})
    with pytest.raises(IdentityError):
        bot_email(gh)
    assert gh.calls == []


@pytest.mark.parametrize(
    "record",
    [
        None,
        [],
        {**APP_USER_RECORD, "type": "User"},
        {**APP_USER_RECORD, "login": "someone-else[bot]"},
        {**APP_USER_RECORD, "id": True},
        {**APP_USER_RECORD, "id": "41898282"},
        {**APP_USER_RECORD, "id": 0},
        GitHubError(404, "Not Found"),
    ],
)
def test_anything_but_the_apps_bot_fails_closed(record: Any) -> None:
    with pytest.raises(IdentityError):
        bot_email(FakeGitHub({APP_USER: record}), APP_SLUG)
