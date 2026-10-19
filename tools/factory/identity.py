"""The commit identity of the factory: the App bot's own noreply address.

GitHub attributes a commit to the account whose noreply address it carries, so every commit the
factory writes carries `<bot user id>+<slug>[bot]@users.noreply.github.com`, the address of the
App's bot account. It is resolved at run time from `GET /users/<slug>[bot]` (public), with the slug
from FACTORY_APP_SLUG. Nothing falls back to a made-up address: a missing slug, a GitHub error or an
answer that is not the App's bot account raises IdentityError, and the step fails closed.
"""

from __future__ import annotations

import os
import re
import urllib.parse
from typing import Any

from tools.factory.github import GitHubError

# GitHub App slugs: lowercase letters, digits and single hyphens inside.
_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


class IdentityError(ValueError):
    """The App bot's commit identity could not be resolved; nothing may be committed."""


def bot_email(gh: Any, slug: str | None = None) -> str:
    """`<id>+<slug>[bot]@users.noreply.github.com` for the App whose slug is `slug` (default:
    FACTORY_APP_SLUG), read from GitHub's public user record of its bot account."""
    slug = os.environ.get("FACTORY_APP_SLUG", "") if slug is None else slug
    if not slug or not _SLUG.fullmatch(slug):
        raise IdentityError(f"FACTORY_APP_SLUG is not an App slug: {slug!r}")
    login = f"{slug}[bot]"
    try:
        data = gh.request("GET", f"/users/{urllib.parse.quote(login, safe='')}")
    except GitHubError as error:
        raise IdentityError(f"cannot read the user {login}: {error}") from error
    if not isinstance(data, dict):
        raise IdentityError(f"no user record for {login}")
    user_id = data.get("id")
    if (
        str(data.get("login") or "").lower() != login.lower()
        or data.get("type") != "Bot"
        or isinstance(user_id, bool)
        or not isinstance(user_id, int)
        or user_id <= 0
    ):
        raise IdentityError(f"{login} is not the App's bot account")
    return f"{user_id}+{login}@users.noreply.github.com"
