import os
from collections.abc import Callable
from urllib.parse import urlparse

import pytest
from playwright.sync_api import BrowserContext, Page, Route

LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost"})
# Umami Cloud: the script (cloud.umami.is) and its event endpoint (gateway.umami.is).
ANALYTICS_HOSTS = frozenset({"cloud.umami.is", "gateway.umami.is"})
CPU_THROTTLE_ENV = "FACTORY_CPU_THROTTLE"
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


def throttle_rate() -> int | None:
    """The CPU throttling rate asked for by FACTORY_CPU_THROTTLE, None when it is unset."""
    value = os.environ.get(CPU_THROTTLE_ENV)
    if value is None:
        return None
    try:
        rate = int(value)
    except ValueError:
        rate = 0
    if rate < 2:
        raise pytest.UsageError(f"{CPU_THROTTLE_ENV} must be an integer of at least 2, got {value!r}")
    return rate


def throttle_context(context: BrowserContext, rate: int) -> None:
    """Throttle the CPU of every page of the context, the open ones and the ones opened later."""

    def throttle(page: Page) -> None:
        context.new_cdp_session(page).send("Emulation.setCPUThrottlingRate", {"rate": rate})

    for page in context.pages:
        throttle(page)
    context.on("page", throttle)


def apply_cpu_throttle(browser_name: str, context: BrowserContext) -> None:
    """Throttle Chromium when FACTORY_CPU_THROTTLE asks for it; otherwise do nothing."""
    rate = throttle_rate()
    if rate is not None and browser_name == "chromium":
        throttle_context(context, rate)


@pytest.fixture(autouse=True)
def cpu_throttle(browser_name: str, context: BrowserContext) -> None:
    """Statistical proof for flaky tests: the factory runs a test many times with the CPU throttled."""
    apply_cpu_throttle(browser_name, context)


@pytest.fixture
def new_context(
    new_context: Callable[..., BrowserContext], browser_name: str
) -> Callable[..., BrowserContext]:
    """Keep all contexts offline while preserving Playwright's artifact lifecycle."""
    def make(**kwargs: object) -> BrowserContext:
        context = new_context(**kwargs)
        keep_offline(context)
        apply_cpu_throttle(browser_name, context)
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
