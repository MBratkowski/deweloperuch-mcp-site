from collections.abc import Callable
from urllib.parse import urlparse

import pytest
from playwright.sync_api import BrowserContext, Page, Route

LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost"})
# Umami Cloud: the script (cloud.umami.is) and its event endpoint (gateway.umami.is).
ANALYTICS_HOSTS = frozenset({"cloud.umami.is", "gateway.umami.is"})
PAGES = {"landing": "", "docs": "docs/"}


def keep_offline(context: BrowserContext) -> list[str]:
    """Serve only the local site: the analytics script gets an empty stub and every other
    request that leaves 127.0.0.1 is aborted. Returns the hosts asked for, filled as pages load."""
    hosts: list[str] = []

    def handle(route: Route) -> None:
        host = urlparse(route.request.url).hostname or ""
        hosts.append(host)
        if host in LOCAL_HOSTS:
            route.continue_()
        elif host in ANALYTICS_HOSTS:
            route.fulfill(status=200, content_type="text/javascript", body="")
        else:
            route.abort()

    context.route("**/*", handle)
    return hosts


@pytest.fixture(autouse=True)
def requested_hosts(context: BrowserContext) -> list[str]:
    """Every end-to-end test runs offline; the hosts the default context asked for."""
    return keep_offline(context)


@pytest.fixture
def new_context(new_context: Callable[..., BrowserContext]) -> Callable[..., BrowserContext]:
    """Keep all contexts offline while preserving Playwright's artifact lifecycle."""
    def make(**kwargs: object) -> BrowserContext:
        context = new_context(**kwargs)
        keep_offline(context)
        return context

    return make


@pytest.fixture
def console_errors(page: Page) -> list[str]:
    errors: list[str] = []
    page.on("console", lambda message: message.type == "error" and errors.append(message.text))
    page.on("pageerror", lambda error: errors.append(str(error)))
    return errors


@pytest.fixture(params=list(PAGES), ids=list(PAGES))
def page_url(request: pytest.FixtureRequest, site_url: str) -> str:
    """Each page of the site in turn."""
    return site_url + PAGES[request.param]
