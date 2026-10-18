"""Probe checks: each takes an opened cell and returns findings (at most five per cell)."""

from __future__ import annotations

from collections.abc import Callable

from tools.factory.detectors import CLIPPED_TEXT, OVERLAPS, WIDE_ELEMENTS
from tools.factory.findings import Finding
from tools.factory.probe import PageRun

MAX_FINDINGS = 5


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


CHECKS: dict[str, Callable[[PageRun], list[Finding]]] = {
    "overflow": overflow,
    "clipped-text": clipped_text,
    "overlap": overlap,
}
