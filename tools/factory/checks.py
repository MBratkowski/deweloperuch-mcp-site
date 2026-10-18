"""Probe checks: each takes an opened cell and returns findings (at most five per cell)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from tools.factory.config import load_config
from tools.factory.detectors import (
    CLIPPED_TEXT,
    FOCUS_OBSCURED,
    FOCUS_PROBLEMS,
    OVERLAPS,
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


def focus(run: PageRun) -> list[Finding]:
    if run.cell.mode != "keyboard":
        # focus() only matches :focus-visible after a keyboard interaction.
        run.page.keyboard.press("Tab")
    problems = run.page.evaluate(FOCUS_PROBLEMS)["problems"]
    run.page.evaluate("() => window.scrollTo({ left: 0, top: 0, behavior: 'instant' })")
    problems += run.page.evaluate(FOCUS_OBSCURED)
    problems += run.page.evaluate(SCROLL_UNREACHABLE)
    return _findings(run, "focus", problems)


CHECKS: dict[str, Callable[[PageRun], list[Finding]]] = {
    "overflow": overflow,
    "clipped-text": clipped_text,
    "overlap": overlap,
    "axe": axe,
    "target-size": target_size,
    "focus": focus,
}
