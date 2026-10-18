"""Probe checks: each takes an opened cell and returns findings (at most five per cell)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError

from tools.factory.config import load_config
from tools.factory.detectors import (
    BROKEN_IMAGES,
    DEAD_ANCHORS,
    CLIPPED_TEXT,
    FOCUS_OBSCURED,
    FOCUS_PROBLEMS,
    OVERLAPS,
    SCROLL_CLEANUP,
    SCROLL_REACHED,
    SCROLL_SENTINEL,
    SCROLL_UNREACHABLE,
    TARGET_SIZES,
    WIDE_ELEMENTS,
)
from tools.factory.findings import Finding
from tools.factory.probe import PageRun

MAX_FINDINGS = 5
AXE_PATH = Path(__file__).parent / "vendor/axe.min.js"
AXE_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]


def _findings(run: PageRun, check: str, raw: list[dict]) -> list[Finding]:
    return [
        Finding(check, run.cell, item["selector"], item["measurement"], tuple(item["box"]))
        for item in raw[:MAX_FINDINGS]
    ]


def overflow(run: PageRun) -> list[Finding]:
    return _findings(run, "overflow", run.page.evaluate(WIDE_ELEMENTS, run.cell.width))


def clipped_text(run: PageRun) -> list[Finding]:
    return _findings(run, "clipped-text", run.page.evaluate(CLIPPED_TEXT))


def overlap(run: PageRun) -> list[Finding]:
    return _findings(run, "overlap", run.page.evaluate(OVERLAPS))


# One entry per (rule, node): axe's first target selector, the help text, the element's box.
_AXE_RUN = """async tags => {
  const result = await axe.run(document, { runOnly: { type: 'tag', values: tags } });
  const out = [];
  for (const rule of result.violations) {
    for (const node of rule.nodes) {
      const target = Array.isArray(node.target[0]) ? node.target[0].at(-1) : node.target[0];
      const el = document.querySelector(target);
      const rect = el ? el.getBoundingClientRect() : null;
      out.push({
        selector: target,
        measurement: `${rule.id}: ${rule.help}`,
        box: rect ? [rect.left + scrollX, rect.top + scrollY, rect.width, rect.height].map(Math.round) : [0, 0, 0, 0],
      });
    }
  }
  return out;
}"""


def axe(run: PageRun) -> list[Finding]:
    # The script is read from disk and injected, so the request guard sees no request.
    run.page.add_script_tag(path=str(AXE_PATH))
    return _findings(run, "axe", run.page.evaluate(_AXE_RUN, AXE_TAGS))


def target_size(run: PageRun) -> list[Finding]:
    detectors = load_config().detectors
    sizes = [detectors.target_min_px, detectors.mobile_nav_target_min_px]
    return _findings(run, "target-size", run.page.evaluate(TARGET_SIZES, sizes))


def _scroll_unreachable(run: PageRun) -> list[dict]:
    """Candidates from the static scan that a real Tab press does not reach in this engine."""
    page = run.page
    engine = run.cell.engine
    candidates = page.evaluate(SCROLL_UNREACHABLE)
    unreachable = []
    try:
        for index, candidate in enumerate(candidates):
            page.evaluate(SCROLL_SENTINEL, index)
            page.keyboard.press("Tab")
            if not page.evaluate(SCROLL_REACHED, index):
                measurement = f"scrollable region not reached by Tab in {engine} ({candidate['measurement']})"
                unreachable.append({**candidate, "measurement": measurement})
    finally:
        page.evaluate(SCROLL_CLEANUP)
        page.evaluate("() => { document.activeElement?.blur(); window.scrollTo(0, 0); }")
    return unreachable


def focus(run: PageRun) -> list[Finding]:
    if run.cell.mode != "keyboard":
        # focus() only matches :focus-visible after a keyboard interaction.
        run.page.keyboard.press("Tab")
    problems = run.page.evaluate(FOCUS_PROBLEMS)["problems"]
    run.page.evaluate("() => window.scrollTo({ left: 0, top: 0, behavior: 'instant' })")
    problems += run.page.evaluate(FOCUS_OBSCURED)
    problems += _scroll_unreachable(run)
    return _findings(run, "focus", problems)


MESSAGE_LENGTH = 200
SETTLE_TIMEOUT_MS = 3_000
NO_BOX = [0, 0, 0, 0]


def _distinct(items: list[tuple[str, str]]) -> list[dict]:
    seen: dict[tuple[str, str], None] = dict.fromkeys(items)
    return [{"selector": sel, "measurement": text, "box": NO_BOX} for sel, text in seen]


def _settle(run: PageRun) -> None:
    """Let requests started by scripts at load time finish, so the request log is complete."""
    try:
        run.page.wait_for_load_state("networkidle", timeout=SETTLE_TIMEOUT_MS)
    except PlaywrightError:
        pass  # a page that never goes idle is judged on what it has logged so far


def console(run: PageRun) -> list[Finding]:
    _settle(run)
    messages = [("script", " ".join(m.split())[:MESSAGE_LENGTH]) for m in run.console]
    return _findings(run, "console", _distinct(messages))


def network(run: PageRun) -> list[Finding]:
    _settle(run)
    log = run.requests
    items = [(host, f"blocked third-party host {host}") for host in log.blocked]
    for url in log.failed:
        if url not in log.images and url not in log.aborted:
            items.append((urlparse(url).path, f"failed {url}"))
    for entry in log.bad_status:
        status, _, url = entry.partition(" ")
        if url not in log.images:
            items.append((urlparse(url).path, f"{status} {urlparse(url).path}"))
    return _findings(run, "network", _distinct(items))


def anchors(run: PageRun) -> list[Finding]:
    pages: dict[str, str] = {}
    dead = []
    for item in run.page.evaluate(DEAD_ANCHORS):
        path = item.get("path")
        if path:
            if path not in pages:
                response = run.page.request.get(path)
                pages[path] = response.text() if response.ok else ""
            if f'id="{item["fragment"]}"' in pages[path]:
                continue
        dead.append(item)
    return _findings(run, "anchors", dead)


def images(run: PageRun) -> list[Finding]:
    return _findings(run, "images", run.page.evaluate(BROKEN_IMAGES))


CHECKS: dict[str, Callable[[PageRun], list[Finding]]] = {
    "overflow": overflow,
    "clipped-text": clipped_text,
    "overlap": overlap,
    "axe": axe,
    "target-size": target_size,
    "focus": focus,
    "console": console,
    "network": network,
    "anchors": anchors,
    "images": images,
}
