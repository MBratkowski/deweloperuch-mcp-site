"""Deterministic checks on the diff between two commits: paths, size, weakened tests, copy.

Pure git subprocesses: it reads the diff and file contents with `git diff` and `git show` and
never checks out or executes anything from the commits. Reasons are short and stable, and never
carry file contents.

Command line: `python -m tools.factory.fence --base SHA --head SHA [--repo PATH]` prints the
report as JSON and exits 0 when ok, 1 when not, 2 on git errors.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from pathlib import Path

from tools.factory.config import Fence, Limits, load_config

POLISH_LETTERS = "ąćęłńóśźżĄĆĘŁŃÓŚŹŻ"
COMPARISON = "tests/visual/comparison.py"
PAGES = ("index.html", "docs/index.html")
TEXT_ATTRIBUTES = ("alt", "aria-label", "title", "placeholder")
SKIP_MARKERS = (
    "@pytest.mark.skip",
    "@pytest.mark.skipif",
    "@pytest.mark.xfail",
    "pytest.skip(",
    "pytest.xfail(",
)
_JS_STRING = re.compile(r"\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*'|`(?:[^`\\]|\\.)*`", re.DOTALL)
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


class GitError(RuntimeError):
    """A git command failed."""


@dataclass(frozen=True)
class Change:
    path: str
    old_path: str | None
    status: str
    added: int
    deleted: int
    binary: bool


@dataclass(frozen=True)
class FenceReport:
    ok: bool
    reasons: list[str]
    changed_lines: int
    copy_changed: bool


def _git(repo: Path, *args: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, check=False, timeout=60
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise GitError(f"git {args[0]} could not run: {error}") from error
    if result.returncode != 0:
        raise GitError(f"git {args[0]} failed with exit code {result.returncode}")
    return result.stdout


def _check_rev(rev: str) -> None:
    if not rev or rev.startswith("-"):
        raise GitError("invalid revision")


def _show(repo: Path, rev: str, path: str | None) -> str:
    """The text of `path` at `rev`; empty when the file is absent there."""
    if path is None:
        return ""
    try:
        data = _git(repo, "show", f"{rev}:{path}")
    except GitError:
        return ""
    return data.decode("utf-8", errors="replace")


def changes(repo: Path, base: str, head: str) -> list[Change]:
    """Files changed between `base` and `head`, renames detected."""
    _check_rev(base)
    _check_rev(head)
    common = ("diff", "-M", "--no-ext-diff", "--no-textconv", "-z")
    numstat = _git(repo, *common, "--numstat", base, head, "--").decode("utf-8", "replace")
    status = _git(repo, *common, "--name-status", base, head, "--").decode("utf-8", "replace")

    counts: dict[str, tuple[int, int, bool]] = {}
    tokens = numstat.split("\0")
    i = 0
    while i < len(tokens) and tokens[i]:
        added, deleted, rest = tokens[i].split("\t", 2)
        if rest:
            path = rest
            i += 1
        else:  # rename: the old and the new path follow as separate tokens
            path = tokens[i + 2]
            i += 3
        binary = added == "-" or deleted == "-"
        counts[path] = (0 if binary else int(added), 0 if binary else int(deleted), binary)

    result: list[Change] = []
    tokens = status.split("\0")
    i = 0
    while i < len(tokens) and tokens[i]:
        code = tokens[i][0]
        if code in "RC":
            old, path = tokens[i + 1], tokens[i + 2]
            i += 3
        else:
            old, path = None, tokens[i + 1]
            i += 2
        added, deleted, binary = counts.get(path, (0, 0, False))
        result.append(Change(path, old, code, added, deleted, binary))
    return result


def matches(path: str, pattern: str) -> bool:
    """`dir/**` is a prefix match on `dir/`; anything else is an exact match."""
    if pattern.endswith("/**"):
        return path.startswith(pattern[:-2])
    return path == pattern


def _any(path: str, patterns: tuple[str, ...]) -> bool:
    return any(matches(path, pattern) for pattern in patterns)


class _TextCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def _add(self, text: str | None) -> None:
        normalised = " ".join((text or "").split())
        if normalised:
            self.parts.append(normalised)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip += 1
            return
        values = dict(attrs)
        for name in TEXT_ATTRIBUTES:
            self._add(values.get(name))
        if tag == "meta":
            key = values.get("property") or ""
            if values.get("name") == "description" or key.startswith("og:"):
                self._add(values.get("content"))

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._add(data)


def visible_text(html: str) -> list[str]:
    """Text nodes and copy-bearing attributes of a page, whitespace-normalised."""
    collector = _TextCollector()
    collector.feed(html)
    collector.close()
    return collector.parts


def js_polish_strings(source: str) -> set[str]:
    """String literals in `source` that contain a Polish letter."""
    return {
        match.group(0)
        for match in _JS_STRING.finditer(source)
        if any(letter in match.group(0) for letter in POLISH_LETTERS)
    }


def _largest_number(lines: list[str]) -> float | None:
    numbers = [float(n) for line in lines for n in _NUMBER.findall(line)]
    return max(numbers) if numbers else None


def _weakened_in(old: str, new: str, path: str) -> list[str]:
    found: list[str] = []
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    new_stripped = {line.strip() for line in new_lines}
    if any(
        line.strip().startswith("assert ") and line.strip() not in new_stripped
        for line in old_lines
    ):
        found.append(f"{path}: assertion removed or changed")

    old_counts: dict[str, int] = {}
    for line in old_lines:
        old_counts[line.strip()] = old_counts.get(line.strip(), 0) + 1
    seen: dict[str, int] = {}
    for line in new_lines:
        text = line.strip()
        seen[text] = seen.get(text, 0) + 1
        if seen[text] > old_counts.get(text, 0) and any(m in text for m in SKIP_MARKERS):
            found.append(f"{path}: skip or xfail added")
            break

    removed: list[str] = []
    added: list[str] = []
    for line in difflib.unified_diff(old_lines, new_lines, lineterm="", n=0):
        if line.startswith(("---", "+++")) or "timeout" not in line.lower():
            continue
        if line.startswith("-"):
            removed.append(line[1:])
        elif line.startswith("+"):
            added.append(line[1:])
    before, after = _largest_number(removed), _largest_number(added)
    if before is not None and after is not None and after > before:
        found.append(f"{path}: timeout raised")
    return found


def _weakened(repo: Path, base: str, head: str, files: list[Change]) -> list[str]:
    found: list[str] = []
    for change in files:
        source = change.old_path or change.path
        in_tests = source.startswith("tests/")
        if COMPARISON in (change.path, change.old_path):
            found.append(f"{COMPARISON}: changed")
        if not in_tests or change.status == "A":
            continue
        if change.status == "D":
            found.append(f"{source}: test file deleted")
            continue
        found += _weakened_in(
            _show(repo, base, source), _show(repo, head, change.path), change.path
        )
    return found


def weakened_tests(repo: Path, base: str, head: str) -> list[str]:
    """Ways in which the diff weakens existing tests, as short descriptions."""
    return _weakened(repo, base, head, changes(repo, base, head))


def _copy_changed(repo: Path, base: str, head: str, files: list[Change]) -> bool:
    for change in files:
        source = change.old_path or change.path
        if change.path in PAGES or source in PAGES:
            old = visible_text(_show(repo, base, source))
            new = visible_text(_show(repo, head, change.path))
            if old != new:
                return True
        elif change.path.startswith("js/") or source.startswith("js/"):
            if js_polish_strings(_show(repo, base, source)) != js_polish_strings(
                _show(repo, head, change.path)
            ):
                return True
    return False


def check_fence(
    repo: Path, base: str, head: str, fence: Fence, limits: Limits
) -> FenceReport:
    files = changes(repo, base, head)
    reasons: list[str] = []

    def add(reason: str) -> None:
        if reason not in reasons:
            reasons.append(reason)

    for change in files:
        for path in dict.fromkeys(p for p in (change.old_path, change.path) if p):
            if _any(path, fence.forbidden):
                add(f"forbidden path: {path}")
            elif not _any(path, fence.allowed):
                add(f"outside the allowed paths: {path}")

    changed_lines = sum(
        c.added + c.deleted
        for c in files
        if not c.binary and not _any(c.path, fence.generated)
    )
    if changed_lines > limits.max_changed_lines:
        add("size")

    weakened = _weakened(repo, base, head, files)
    if weakened:
        add("weakened tests")
        for detail in weakened:
            add(f"weakened tests: {detail}")

    copy_changed = _copy_changed(repo, base, head, files)
    if copy_changed:
        add("copy")

    return FenceReport(not reasons, reasons, changed_lines, copy_changed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--repo", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    config = load_config()
    try:
        report = check_fence(args.repo, args.base, args.head, config.fence, config.limits)
    except GitError as error:
        print(f"fence: {error}", file=sys.stderr)
        return 2
    print(json.dumps(asdict(report), indent=2))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
