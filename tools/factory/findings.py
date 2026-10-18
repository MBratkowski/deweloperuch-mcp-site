"""Probe findings, their grouping into defects and the stable fingerprint of a defect."""

from __future__ import annotations

import argparse
import hashlib
import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

DEFAULT_SEVERITY: dict[str, str] = {
    "console": "S1",
    "anchors": "S1",
    "network": "S1",
    "flaky": "S1",
    "deploy-stale": "S1",
    "axe": "S2",
    "target-size": "S2",
    "focus": "S2",
    "overflow": "S3",
    "clipped-text": "S3",
    "overlap": "S3",
    "engine-diff": "S3",
    "images": "S3",
}
FALLBACK_SEVERITY = "S3"
# Defects the QA agent proves with a script of its own; no detector reports them.
AI_REVIEW = "ai-review"

_NTH = re.compile(r":nth-(?:child|of-type)\([^)]*\)")
_COMBINATORS = re.compile(r"\s*([>+~])\s*")
_SPACES = re.compile(r"\s+")


@dataclass(frozen=True)
class Cell:
    page: str
    engine: str
    width: int
    height: int
    mode: str


@dataclass(frozen=True)
class Finding:
    check: str
    cell: Cell
    selector: str
    measurement: str
    box: tuple[float, float, float, float] | None

    def to_json(self) -> dict[str, Any]:
        return {
            "check": self.check,
            "cell": {
                "page": self.cell.page,
                "engine": self.cell.engine,
                "width": self.cell.width,
                "height": self.cell.height,
                "mode": self.cell.mode,
            },
            "selector": self.selector,
            "measurement": self.measurement,
            "box": list(self.box) if self.box is not None else None,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Finding:
        box = data["box"]
        return cls(
            data["check"],
            Cell(**data["cell"]),
            data["selector"],
            data["measurement"],
            tuple(box) if box is not None else None,  # type: ignore[arg-type]
        )


def normalize_selector(selector: str) -> str:
    """Drop positional pseudo-classes and normalise whitespace around combinators."""
    text = _NTH.sub("", selector)
    text = _COMBINATORS.sub(r" \1 ", text)
    return _SPACES.sub(" ", text).strip()


def width_bands(widths: Sequence[int], breakpoints: Sequence[int]) -> tuple[str, ...]:
    """Name the breakpoint bands the widths fall into, narrowest first."""
    names = [f"le{breakpoints[0]}"]
    names += [f"{lo + 1}-{hi}" for lo, hi in zip(breakpoints, breakpoints[1:])]
    names.append(f"ge{breakpoints[-1] + 1}")
    seen = {sum(1 for b in breakpoints if w > b) for w in widths}
    return tuple(names[i] for i in sorted(seen))


def fingerprint(
    check: str,
    page: str,
    mode: str,
    selector: str,
    engines: Sequence[str],
    bands: Sequence[str],
) -> str:
    fields = [check, page, mode, selector, ",".join(engines), ",".join(bands)]
    return hashlib.sha1("|".join(fields).encode()).hexdigest()[:12]


@dataclass(frozen=True)
class Group:
    check: str
    page: str
    mode: str
    selector: str
    engines: tuple[str, ...]
    widths: tuple[int, ...]
    findings: tuple[Finding, ...]
    # Stylesheet breakpoints the width bands come from; group_findings passes the matrix's.
    breakpoints: tuple[int, ...] = (720, 760)
    # Human-readable measurement of a group that has no findings (CI groups); not fingerprinted.
    note: str = ""

    @property
    def fingerprint(self) -> str:
        return fingerprint(
            self.check,
            self.page,
            self.mode,
            normalize_selector(self.selector),
            self.engines,
            width_bands(self.widths, self.breakpoints) if self.widths else (),
        )

    @property
    def severity(self) -> str:
        return DEFAULT_SEVERITY.get(self.check, FALLBACK_SEVERITY)

    def to_json(self) -> dict[str, Any]:
        return {
            "check": self.check,
            "page": self.page,
            "mode": self.mode,
            "selector": self.selector,
            "engines": list(self.engines),
            "widths": list(self.widths),
            "findings": [f.to_json() for f in self.findings],
            "breakpoints": list(self.breakpoints),
            "fingerprint": self.fingerprint,
            "severity": self.severity,
            "note": self.note,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Group:
        return cls(
            data["check"],
            data["page"],
            data["mode"],
            data["selector"],
            tuple(data["engines"]),
            tuple(data["widths"]),
            tuple(Finding.from_json(f) for f in data["findings"]),
            tuple(data["breakpoints"]),
            data.get("note", ""),
        )


def group_findings(
    findings: Iterable[Finding],
    matrix_widths: Sequence[int],
    breakpoints: Sequence[int],
) -> list[Group]:
    """Merge findings that describe one defect: adjacent widths, then equal engines."""
    order = {w: i for i, w in enumerate(sorted(matrix_widths))}
    buckets: dict[tuple[str, str, str, str, str], dict[int, list[Finding]]] = defaultdict(dict)
    for f in findings:
        key = (f.check, f.cell.page, f.cell.mode, normalize_selector(f.selector), f.cell.engine)
        buckets[key].setdefault(f.cell.width, []).append(f)

    runs: dict[tuple[str, str, str, str, tuple[int, ...]], dict[str, list[Finding]]] = defaultdict(
        dict
    )
    for (check, page, mode, selector, engine), by_width in buckets.items():
        current: list[int] = []
        for width in sorted(by_width, key=order.__getitem__):
            if current and order[width] != order[current[-1]] + 1:
                _store(runs, check, page, mode, selector, engine, current, by_width)
                current = []
            current.append(width)
        if current:
            _store(runs, check, page, mode, selector, engine, current, by_width)

    groups = [
        Group(
            check,
            page,
            mode,
            selector,
            tuple(sorted(per_engine)),
            widths,
            tuple(f for engine in sorted(per_engine) for f in per_engine[engine]),
            tuple(breakpoints),
        )
        for (check, page, mode, selector, widths), per_engine in runs.items()
    ]
    return sorted(groups, key=lambda g: (g.page, g.check, g.mode, g.selector, g.widths))


def _store(
    runs: dict[Any, dict[str, list[Finding]]],
    check: str,
    page: str,
    mode: str,
    selector: str,
    engine: str,
    widths: list[int],
    by_width: dict[int, list[Finding]],
) -> None:
    runs[(check, page, mode, selector, tuple(widths))][engine] = [
        f for w in widths for f in by_width[w]
    ]


def ai_review_fingerprint(page: str, selector: str) -> str:
    """The fingerprint of an AI-review defect: page and measured element only, so the same
    defect gets the same fingerprint every night whatever cells the agent's script checks."""
    return fingerprint(AI_REVIEW, page, "default", normalize_selector(selector), (), ())


def main(argv: Sequence[str] | None = None) -> int:
    from tools.factory.config import load_config

    parser = argparse.ArgumentParser(prog="python -m tools.factory.findings")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("fingerprint", help="print the fingerprint of an AI-review defect")
    command.add_argument("--check", required=True, choices=[AI_REVIEW])
    command.add_argument("--page", required=True, choices=sorted(load_config().site.pages))
    command.add_argument("--selector", required=True, help="the CSS selector the script measures")
    args = parser.parse_args(argv)
    if not normalize_selector(args.selector):
        parser.error("--selector must not be empty")
    print(ai_review_fingerprint(args.page, args.selector))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
