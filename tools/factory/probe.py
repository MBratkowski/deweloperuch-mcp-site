"""Open the site in a real browser, one cell (page x engine x width x mode) at a time."""

from __future__ import annotations

import argparse
import functools
import http.server
import json
import os
import re
import shutil
import sys
import threading
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import (
    Browser,
    BrowserContext,
    ConsoleMessage,
    Page,
    Playwright,
    Request,
    Response,
    Route,
    sync_playwright,
)
from playwright.sync_api import Error as PlaywrightError

from tools.factory import images
from tools.factory.config import Matrix, load_config
from tools.factory.findings import Cell, Finding, Group, group_findings, normalize_selector
from tools.package_site import package_site

LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost"})
ANALYTICS_HOSTS = frozenset({"cloud.umami.is", "gateway.umami.is"})
CRITICAL_RESOURCES = frozenset({"stylesheet", "script", "font"})
MENU_BUTTON = ".topbar button.nav-toggle"
NAVIGATION_TIMEOUT_MS = 30_000
ROOT = Path(__file__).resolve().parents[2]
DESIGN_PATH = ROOT / "docs" / "design" / "noc.dc.html"
DEFAULT_REFERENCE_HOSTS = frozenset({"fonts.googleapis.com", "fonts.gstatic.com"})
REFERENCE_VIEWPORT = {"width": 1440, "height": 900}
NON_NETWORK_SCHEMES = frozenset({"file", "data", "blob", "about"})
ENGINE_DIFF = "engine-diff"
NOT_REPRODUCIBLE = frozenset({"flaky", "deploy-stale"})
SHEET_COLUMNS = 5


class ProbeError(Exception):
    """The site could not be probed (unreachable, failing, or missing a control)."""


@dataclass(frozen=True)
class Target:
    name: str
    base_url: str


@dataclass
class RequestLog:
    hosts: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    bad_status: list[str] = field(default_factory=list)  # "STATUS URL" of same-host responses >= 400
    critical: list[str] = field(default_factory=list)  # stylesheet, script or font that failed
    aborted: list[str] = field(default_factory=list)  # URLs the guard aborted itself
    images: set[str] = field(default_factory=set)  # URLs requested as images (the images check owns them)


@dataclass
class PageRun:
    page: Page
    cell: Cell
    url: str
    console: list[str]
    requests: RequestLog
    context: BrowserContext

    def close(self) -> None:
        self.context.close()


@functools.cache
def _modes() -> dict[str, dict]:
    return load_config().matrix.modes


def guard_requests(context: BrowserContext, site_host: str) -> RequestLog:
    """Let through only the site's own host, stub Umami with an empty script, abort the rest."""
    log = RequestLog()
    allowed = LOCAL_HOSTS if site_host in LOCAL_HOSTS else frozenset({site_host})

    def handle(route: Route) -> None:
        url = route.request.url
        host = urlparse(url).hostname or ""
        log.hosts.append(host)
        if route.request.resource_type == "image":
            log.images.add(url)
        if host in allowed:
            route.continue_()
        elif host in ANALYTICS_HOSTS:
            route.fulfill(status=200, content_type="text/javascript", body="")
        else:
            log.blocked.append(host)
            log.aborted.append(url)
            route.abort()

    def on_failed(request: Request) -> None:
        if request.url in log.aborted:
            return
        log.failed.append(request.url)
        if (urlparse(request.url).hostname or "") in allowed and request.resource_type in CRITICAL_RESOURCES:
            log.critical.append(f"failed {request.resource_type} {request.url}")

    def on_response(response: Response) -> None:
        url = response.url
        if (urlparse(url).hostname or "") not in allowed or response.status < 400:
            return
        log.bad_status.append(f"{response.status} {url}")
        if response.request.resource_type in CRITICAL_RESOURCES:
            log.critical.append(f"{response.status} {response.request.resource_type} {url}")

    context.route("**/*", handle)
    context.on("requestfailed", on_failed)
    context.on("response", on_response)
    return log


def open_cell(browser: Browser, target: Target, cell: Cell, path: str) -> PageRun:
    """Open `path` of the target in a fresh context shaped by the cell. The caller closes the run."""
    assert browser.browser_type.name == cell.engine, (
        f"cell wants {cell.engine}, browser is {browser.browser_type.name}"
    )
    options = {k: v for k, v in _modes().get(cell.mode, {}).items() if k != "height"}
    base = target.base_url if target.base_url.endswith("/") else target.base_url + "/"
    url = base + path.lstrip("/")
    context = browser.new_context(viewport={"width": cell.width, "height": cell.height}, **options)
    try:
        requests = guard_requests(context, urlparse(url).hostname or "")
        page = context.new_page()
        console: list[str] = []
        page.on("console", lambda m: _on_console(m, console))
        page.on("pageerror", lambda e: console.append(str(e)))
        try:
            response = page.goto(url, wait_until="load", timeout=NAVIGATION_TIMEOUT_MS)
        except PlaywrightError as error:
            raise ProbeError(f"cannot open {url}: {error}") from error
        if response is None or response.status >= 400:
            status = "no response" if response is None else f"status {response.status}"
            raise ProbeError(f"cannot open {url}: {status}")
        if requests.critical:
            raise ProbeError(f"{url} is half-loaded: {'; '.join(requests.critical)}")
        if cell.mode == "menu":
            _open_menu(page, url)
        elif cell.mode == "keyboard":
            page.keyboard.press("Tab")  # keyboard modality: focus() now matches :focus-visible
        return PageRun(page, cell, url, console, requests, context)
    except BaseException:
        context.close()
        raise


def _on_console(message: ConsoleMessage, console: list[str]) -> None:
    # The browser's "Failed to load resource" lines belong to the network and images checks.
    if message.type == "error" and not message.text.startswith("Failed to load resource"):
        console.append(message.text)


def _open_menu(page: Page, url: str) -> None:
    button = page.locator(MENU_BUTTON)
    if button.count() == 0 or not button.first.is_visible():
        raise ProbeError(f"{url}: no visible {MENU_BUTTON}")
    try:
        button.first.click(timeout=5_000)
        page.wait_for_function(
            "selector => document.querySelector(selector)?.getAttribute('aria-expanded') === 'true'",
            arg=MENU_BUTTON,
            timeout=5_000,
        )
    except PlaywrightError as error:
        raise ProbeError(f"{url}: the menu did not open: {error}") from error


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


class _Server(http.server.ThreadingHTTPServer):
    # The default backlog of 5 resets connections when a page opens many at once (fonts).
    request_queue_size = 128


@contextmanager
def local_site(root: Path) -> Iterator[str]:
    """Package `root` into `_site` and serve it on 127.0.0.1; yields the base URL."""
    site = package_site(root)
    handler = functools.partial(_QuietHandler, directory=str(site))
    server = _Server(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


# --- the matrix run -------------------------------------------------------------------------


def build_cells(matrix: Matrix, pages: Iterable[str]) -> list[Cell]:
    """Every cell of the run: default on all engines and widths, the other modes as configured."""
    pages = list(pages)
    cells: list[Cell] = []
    for page in pages:
        for engine in matrix.engines:
            cells += [Cell(page, engine, w, matrix.height, "default") for w in matrix.widths]
    for page in pages:
        for mode, options in matrix.modes.items():
            height = options.get("height", matrix.height)
            for engine in matrix.mode_engines:
                cells += [Cell(page, engine, w, height, mode) for w in matrix.mode_widths]
    for mode, widths in (("menu", matrix.menu_widths), ("keyboard", matrix.keyboard_widths)):
        for page in pages:
            for engine in matrix.engines:
                cells += [Cell(page, engine, w, matrix.height, mode) for w in widths]
    return cells


def checks_for_mode(mode: str, names: Sequence[str]) -> list[str]:
    """Menu skips overlap (the open menu overlays by design); keyboard runs only focus."""
    if mode == "keyboard":
        return [n for n in names if n == "focus"]
    if mode == "menu":
        return [n for n in names if n != "overlap"]
    return list(names)


def _page_path(page: str) -> str:
    return load_config().site.pages.get(page, page)


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", text) or "root"


def _shot_name(cell: Cell) -> str:
    return f"{_slug(cell.page)}-{cell.engine}-{cell.width}-{cell.mode}.png"


@contextmanager
def _browsers(playwright: Playwright, engines: Iterable[str]) -> Iterator[dict[str, Browser]]:
    launched: dict[str, Browser] = {}
    try:
        for engine in dict.fromkeys(engines):
            try:
                launched[engine] = getattr(playwright, engine).launch()
            except PlaywrightError as error:
                raise ProbeError(f"cannot launch {engine}: {error}") from error
        yield launched
    finally:
        for browser in launched.values():
            browser.close()


def _validate(checks: Sequence[str] | None) -> list[str]:
    from tools.factory.checks import CHECKS

    known = [*CHECKS, ENGINE_DIFF]
    names = list(known if checks is None else checks)
    unknown = [n for n in names if n not in known]
    if unknown:
        raise ValueError(f"unknown checks: {', '.join(unknown)}")
    return names


def run_matrix(
    playwright: Playwright,
    target: Target,
    cells: Sequence[Cell],
    out: Path,
    checks: Sequence[str] | None = None,
    delta_sink: list[Finding] | None = None,
) -> list[Group]:
    """Run the checks over the cells and write the artifacts; raises ProbeError, writing no findings.json."""
    from tools.factory.checks import CHECKS, engine_diff, landmark_boxes

    config = load_config()
    names = _validate(checks)
    probe_names = [n for n in names if n != ENGINE_DIFF]
    want_diff = ENGINE_DIFF in names
    _clear_previous(out)
    shots = out / "shots"
    shots.mkdir(parents=True, exist_ok=True)
    findings: list[Finding] = []
    boxes: dict[Cell, dict] = {}
    with _browsers(playwright, (c.engine for c in cells)) as browsers:
        for cell in cells:
            try:
                run = open_cell(browsers[cell.engine], target, cell, _page_path(cell.page))
            except (ProbeError, PlaywrightError) as error:
                raise ProbeError(f"{cell.page} {cell.engine} {cell.width} {cell.mode}: {error}") from error
            try:
                (shots / _shot_name(cell)).write_bytes(run.page.screenshot(full_page=True))
                if want_diff and cell.mode == "default":
                    boxes[cell] = landmark_boxes(run)
                for name in checks_for_mode(cell.mode, probe_names):
                    findings += CHECKS[name](run)
            except PlaywrightError as error:
                raise ProbeError(f"{cell}: {error}") from error
            finally:
                run.close()
    if want_diff:
        findings += engine_diff(boxes, config.detectors.engine_diff_px)
        if delta_sink is not None:
            delta_sink += engine_diff(boxes, 0)
    widths = sorted(set(config.matrix.widths) | {c.width for c in cells})
    groups = group_findings(findings, widths, config.matrix.breakpoints)
    _write_group_evidence(out, groups)
    _write_sheets(out, cells)
    document = {"target": target.name, "base_url": target.base_url, "groups": [g.to_json() for g in groups]}
    temporary = out / "findings.json.tmp"
    temporary.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, out / "findings.json")
    return groups


def _clear_previous(out: Path) -> None:
    """Remove what an earlier run left in `out`, so a failed run cannot leave stale findings."""
    for name in ("findings.json", "findings.json.tmp", "reference.png"):
        (out / name).unlink(missing_ok=True)
    for name in ("shots", "groups", "sheets"):
        shutil.rmtree(out / name, ignore_errors=True)


def _write_group_evidence(out: Path, groups: Sequence[Group]) -> None:
    for group in groups:
        first = group.findings[0]
        shot = (out / "shots" / _shot_name(first.cell)).read_bytes()
        directory = out / "groups" / group.fingerprint
        directory.mkdir(parents=True, exist_ok=True)
        if images.has_area(first.box):
            (directory / "viewport.png").write_bytes(images.annotate(shot, first.box))
            (directory / "crop.png").write_bytes(images.crop(shot, first.box))
        else:
            (directory / "viewport.png").write_bytes(shot)


def _write_sheets(out: Path, cells: Sequence[Cell]) -> None:
    sheets = out / "sheets"
    sheets.mkdir(parents=True, exist_ok=True)
    wanted = sorted({(c.page, c.engine) for c in cells if c.mode == "default"})
    for page, engine in wanted:
        tiles = [
            (str(c.width), (out / "shots" / _shot_name(c)).read_bytes())
            for c in sorted(cells, key=lambda c: c.width)
            if c.mode == "default" and (c.page, c.engine) == (page, engine)
        ]
        (sheets / f"{_slug(page)}-{engine}.png").write_bytes(images.contact_sheet(tiles, SHEET_COLUMNS))


# --- reproduction ---------------------------------------------------------------------------


def _identity(check: str, cell: Cell, selector: str) -> tuple[str, str, str, str]:
    return (check, cell.page, cell.mode, normalize_selector(selector))


def reproduce(playwright: Playwright, group: Group, base_url: str) -> int:
    """1 when the group's defect reappears in its cells, 0 when not, 2 when the site cannot be probed."""
    from tools.factory.checks import CHECKS, engine_diff, landmark_boxes

    if group.check in NOT_REPRODUCIBLE:
        raise ValueError(f"{group.check} findings cannot be reproduced in a browser")
    if group.check != ENGINE_DIFF and group.check not in CHECKS:
        raise ValueError(f"unknown check {group.check}")
    target = Target("reproduce", base_url)
    cells = list(dict.fromkeys(f.cell for f in group.findings))
    wanted = (group.check, group.page, group.mode, normalize_selector(group.selector))
    config = load_config()
    try:
        if group.check == ENGINE_DIFF:
            references = [Cell(c.page, "chromium", c.width, c.height, c.mode) for c in cells]
            all_cells = list(dict.fromkeys([*cells, *references]))
            boxes: dict[Cell, dict] = {}
            with _browsers(playwright, (c.engine for c in all_cells)) as browsers:
                for cell in all_cells:
                    run = open_cell(browsers[cell.engine], target, cell, _page_path(cell.page))
                    try:
                        boxes[cell] = landmark_boxes(run)
                    finally:
                        run.close()
            found = engine_diff(boxes, config.detectors.engine_diff_px)
        else:
            found = []
            with _browsers(playwright, (c.engine for c in cells)) as browsers:
                for cell in cells:
                    run = open_cell(browsers[cell.engine], target, cell, _page_path(cell.page))
                    try:
                        found += CHECKS[group.check](run)
                    finally:
                        run.close()
    except (PlaywrightError, ProbeError) as error:
        print(f"reproduction failed: {error}", file=sys.stderr)
        return 2
    return 1 if any(_identity(f.check, f.cell, f.selector) == wanted for f in found) else 0


def reproduce_any(playwright: Playwright, groups: Sequence[Group], base_url: str) -> int:
    """1 when any group reappears, 2 when none did and one could not be probed, else 0."""
    codes = [reproduce(playwright, group, base_url) for group in groups]
    if 1 in codes:
        return 1
    return 2 if 2 in codes or not codes else 0


# --- the design reference -------------------------------------------------------------------


def reference_guard(
    context: BrowserContext, allowed_hosts: frozenset[str], aborted: list[str] | None = None
) -> list[str]:
    """Abort every network request to a host outside `allowed_hosts` (and the local server)."""
    log = aborted if aborted is not None else []

    def handle(route: Route) -> None:
        parsed = urlparse(route.request.url)
        host = parsed.hostname or ""
        if parsed.scheme in NON_NETWORK_SCHEMES or host in LOCAL_HOSTS or host in allowed_hosts:
            route.continue_()
        else:
            log.append(route.request.url)
            route.abort()

    context.route("**/*", handle)
    return log


def render_reference(
    playwright: Playwright,
    design: Path,
    out: Path,
    allowed_hosts: frozenset[str] = DEFAULT_REFERENCE_HOSTS,
    aborted: list[str] | None = None,
) -> Path:
    """Render the exported design at 1440 x 900, full page, to out/reference.png."""
    out.mkdir(parents=True, exist_ok=True)
    target = out / "reference.png"
    browser = playwright.chromium.launch()
    try:
        context = browser.new_context(viewport=REFERENCE_VIEWPORT)
        try:
            reference_guard(context, allowed_hosts, aborted)
            page = context.new_page()
            page.goto(design.resolve().as_uri(), wait_until="load", timeout=NAVIGATION_TIMEOUT_MS)
            page.evaluate("document.fonts.ready.then(() => true)")
            target.write_bytes(page.screenshot(full_page=True))
        finally:
            context.close()
    finally:
        browser.close()
    return target


# --- command line ---------------------------------------------------------------------------

_DELTA = re.compile(r"d[xwy]=(-?[\d.]+)")


def _delta_size(finding: Finding) -> float:
    return max((abs(float(v)) for v in _DELTA.findall(finding.measurement)), default=0.0)


def _print_deltas(deltas: Sequence[Finding], limit: int = 15) -> None:
    print(f"engine-diff deltas (largest {min(limit, len(deltas))} of {len(deltas)} non-zero):")
    for f in sorted(deltas, key=_delta_size, reverse=True)[:limit]:
        c = f.cell
        print(f"  {_delta_size(f):g}px  {c.page} {c.engine} {c.width} {c.mode}  {f.selector}  {f.measurement}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tools.factory.probe")
    parser.add_argument("--target", required=True, help="a URL, or 'main' for the repository's own build")
    parser.add_argument("--out", type=Path, help="directory for the run artifacts")
    parser.add_argument("--only", nargs="+", metavar="CHECK", help="run only these checks")
    parser.add_argument("--engines", help="comma-separated subset of the configured engines")
    parser.add_argument("--report-deltas", action="store_true", help="print the largest engine differences")
    parser.add_argument("--reproduce", type=Path, metavar="GROUP_JSON_FILE", help="re-check one group")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.reproduce is None and args.out is None:
        parser.error("--out is required unless --reproduce is given")
    from tools.factory.checks import CHECKS

    known = [*CHECKS, ENGINE_DIFF]
    for name in args.only or []:
        if name not in known:
            parser.error(f"unknown check {name!r}; choose from {', '.join(known)}")
    configured = load_config().matrix.engines
    for name in (args.engines.split(",") if args.engines else []):
        if name not in configured:
            parser.error(f"unknown engine {name!r}; choose from {', '.join(configured)}")
    try:
        if args.target == "main":
            with local_site(ROOT) as url:
                return _run(args, Target("main", url))
        return _run(args, Target(args.target, args.target))
    except (ProbeError, PlaywrightError) as error:
        print(f"probe failed: {error}", file=sys.stderr)
        return 2
    except Exception as error:
        if args.reproduce is None:
            raise
        # In reproduce mode exit 1 means "the defect is there"; a crash must never look like that.
        print(f"reproduction failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 2


def _run(args: argparse.Namespace, target: Target) -> int:
    config = load_config()
    with sync_playwright() as playwright:
        if args.reproduce is not None:
            data = json.loads(args.reproduce.read_text(encoding="utf-8"))
            groups = [Group.from_json(d) for d in (data if isinstance(data, list) else [data])]
            return reproduce_any(playwright, groups, target.base_url)
        matrix = config.matrix
        if args.engines:
            wanted = args.engines.split(",")
            matrix = replace(matrix, engines=tuple(wanted), mode_engines=tuple(e for e in matrix.mode_engines if e in wanted))
        cells = build_cells(matrix, config.site.pages)
        deltas: list[Finding] = []
        groups = run_matrix(
            playwright, target, cells, args.out, args.only, deltas if args.report_deltas else None
        )
        print(f"{len(cells)} cells, {len(groups)} groups -> {args.out / 'findings.json'}")
        for check in sorted({g.check for g in groups}):
            print(f"  {check}: {sum(1 for g in groups if g.check == check)} groups")
        if args.report_deltas:
            _print_deltas(deltas)
        try:
            path = render_reference(playwright, DESIGN_PATH, args.out)
            print(f"reference: {path}")
        except PlaywrightError as error:
            print(f"warning: no design reference: {error}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
