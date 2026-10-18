"""Deterministic checks on the diff between two commits: paths, size, tests and copy.

Pure git subprocesses: it reads the diff and file contents with `git diff` and `git show` and
never checks out, imports or executes anything from the commits (Python is only parsed with
`ast`). Reasons are short and stable, and never carry file contents.

Two kinds of reasons (Ruling R37, an allow-list for tests/):
- hard reasons (`ok` is False): forbidden or unexpected paths, test infrastructure, weakened
  tests, size and the copy guard;
- review reasons (`review`, `needs_human`): existing files under tests/ may only gain lines;
  any removed or changed line asks for a human.

Command line: `python -m tools.factory.fence --base SHA --head SHA [--repo PATH]` prints the
report as JSON. Exit codes: 0 ok and nothing to review, 1 not ok, 2 errors (git or
configuration), 3 ok but review reasons exist.
"""

from __future__ import annotations

import argparse
import ast
import difflib
import json
import re
import subprocess
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from tools.factory.config import ConfigError, Fence, Limits, load_config

POLISH_LETTERS = "ąćęłńóśźżĄĆĘŁŃÓŚŹŻ"
COMPARISON = "tests/visual/comparison.py"
PAGES = ("index.html", "docs/index.html")
TEXT_ATTRIBUTES = ("alt", "aria-label", "title", "placeholder")
INLINE_TAGS = frozenset(
    "a abbr b bdi bdo cite code data dfn del em i ins kbd mark q s small span strong sub sup "
    "time u var wbr".split()
)
MARKUP_MARKERS = ("<script", "</script", "<style", "<textarea", "<![cdata[")
INFRASTRUCTURE_NAMES = ("pytest.ini", "tox.ini", "setup.cfg", ".gitattributes")
SYMLINK, GITLINK = "120000", "160000"
SUSPICIOUS_ADDED = re.compile(
    r"mark\.(skip|skipif|xfail|network|visual|multi_engine)"
    r"|\b(skip|xfail|importorskip)\s*\("
    r"|pytestmark|__test__|add_marker|applymarker|pytest_collection_modifyitems"
)
_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_CSS_CONTENT = re.compile(r"content\s*:\s*(\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*')")
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}


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
    old_mode: str = ""
    new_mode: str = ""


@dataclass(frozen=True)
class FenceReport:
    ok: bool
    reasons: list[str]
    changed_lines: int
    copy_changed: bool
    review: list[str] = field(default_factory=list)

    @property
    def needs_human(self) -> bool:
        return bool(self.review)


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
    raw = _git(repo, *common, "--raw", base, head, "--").decode("utf-8", "replace")

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
    tokens = raw.split("\0")
    i = 0
    while i < len(tokens) and tokens[i].startswith(":"):
        old_mode, new_mode, _, _, status = tokens[i][1:].split(" ")
        code = status[0]
        if code in "RC":
            old, path = tokens[i + 1], tokens[i + 2]
            i += 3
        else:
            old, path = None, tokens[i + 1]
            i += 2
        added, deleted, binary = counts.get(path, (0, 0, False))
        result.append(Change(path, old, code, added, deleted, binary, old_mode, new_mode))
    return result


def matches(path: str, pattern: str) -> bool:
    """`dir/**` is a prefix match on `dir/`; anything else is an exact match."""
    if pattern.endswith("/**"):
        return path.startswith(pattern[:-2])
    return path == pattern


def _any(path: str, patterns: tuple[str, ...]) -> bool:
    return any(matches(path, pattern) for pattern in patterns)


# --- copy guard -------------------------------------------------------------------------


class _TextCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.attributes: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip += 1
            return
        if tag not in INLINE_TAGS:
            self.text.append(" ")
        for name, value in attrs:
            if name in TEXT_ATTRIBUTES:
                self._attribute(name, value)
        if tag == "meta":
            names = {(n, (v or "").lower()) for n, v in attrs}
            og = any(n == "property" and (v or "").startswith("og:") for n, v in attrs)
            if ("name", "description") in names or og:
                for name, value in attrs:
                    if name == "content":
                        self._attribute("meta", value)

    def _attribute(self, name: str, value: str | None) -> None:
        normalised = " ".join((value or "").split())
        if normalised:
            self.attributes.append(f"{name}={normalised}")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        elif tag not in INLINE_TAGS:
            self.text.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.text.append(data)


def visible_text(html: str) -> list[str]:
    """The whitespace-normalised visible text, then every copy attribute in document order.

    The text nodes are joined, so wrapping words in an inline element is not a change; every
    occurrence of an attribute counts, duplicates included.
    """
    collector = _TextCollector()
    collector.feed(html)
    collector.close()
    joined = " ".join("".join(collector.text).split())
    return [joined, *collector.attributes]


def _markup_counts(html: str) -> list[int]:
    lowered = html.lower()
    return [lowered.count(marker) for marker in MARKUP_MARKERS]


def _decode_escapes(body: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(body):
        char = body[i]
        if char != "\\" or i + 1 >= len(body):
            out.append(char)
            i += 1
            continue
        nxt = body[i + 1]
        if nxt == "u" and body[i + 2 : i + 3] == "{":
            end = body.find("}", i + 3)
            digits = body[i + 3 : end] if end != -1 else ""
            if end != -1 and re.fullmatch(r"[0-9a-fA-F]{1,6}", digits) and int(digits, 16) < 0x110000:
                out.append(chr(int(digits, 16)))
                i = end + 1
                continue
        elif nxt == "u" and re.fullmatch(r"[0-9a-fA-F]{4}", body[i + 2 : i + 6]):
            out.append(chr(int(body[i + 2 : i + 6], 16)))
            i += 6
            continue
        elif nxt == "x" and re.fullmatch(r"[0-9a-fA-F]{2}", body[i + 2 : i + 4]):
            out.append(chr(int(body[i + 2 : i + 4], 16)))
            i += 4
            continue
        out.append(_ESCAPES.get(nxt, nxt))
        i += 2
    return "".join(out)


def _js_literals(source: str) -> list[str]:
    """Decoded bodies of every string literal in `source`, outside comments."""
    found: list[str] = []
    i, n = 0, len(source)
    while i < n:
        char = source[i]
        pair = source[i : i + 2]
        if pair == "//":
            end = source.find("\n", i)
            i = n if end == -1 else end
        elif pair == "/*":
            end = source.find("*/", i + 2)
            i = n if end == -1 else end + 2
        elif char in "\"'`":
            j = i + 1
            while j < n and source[j] != char and (char == "`" or source[j] != "\n"):
                j += 2 if source[j] == "\\" else 1
            found.append(_decode_escapes(source[i + 1 : j]))
            i = j + 1
        else:
            i += 1
    return found


def js_polish_strings(source: str) -> Counter[str]:
    """Multiset of the copy-like string literals in `source`.

    A literal counts when it holds a Polish letter or a space, or starts with an uppercase
    letter; comments are ignored and escapes decoded.
    """
    return Counter(
        body
        for body in _js_literals(source)
        if any(letter in body for letter in POLISH_LETTERS)
        or " " in body
        or body[:1].isupper()
    )


def _css_contents(source: str) -> Counter[str]:
    return Counter(_CSS_CONTENT.findall(_CSS_COMMENT.sub("", source)))


def _copy_changed(repo: Path, base: str, head: str, files: list[Change]) -> bool:
    for change in files:
        source = change.old_path or change.path
        names = {change.path, source}
        old_text = new_text = ""
        if names & set(PAGES):
            old_text, new_text = _show(repo, base, source), _show(repo, head, change.path)
            if visible_text(old_text) != visible_text(new_text):
                return True
            if _markup_counts(old_text) != _markup_counts(new_text):
                return True
        elif any(name.startswith("js/") for name in names):
            if js_polish_strings(_show(repo, base, source)) != js_polish_strings(
                _show(repo, head, change.path)
            ):
                return True
        elif any(name.startswith("css/") and name.endswith(".css") for name in names):
            if _css_contents(_show(repo, base, source)) != _css_contents(
                _show(repo, head, change.path)
            ):
                return True
    return False


# --- tests ------------------------------------------------------------------------------


def _added_lines(old: str, new: str) -> list[str]:
    return [
        line[1:]
        for line in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=0)
        if line.startswith("+") and not line.startswith("+++")
    ]


def _skeleton(source: str) -> dict[str, tuple[int, int, str]] | None:
    """Per function and class: (definitions, early exits, decorators), or None on a syntax error.

    The key is the qualified name. An early exit is a `return` or an `if False:`/`if 0:` block.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return None
    result: dict[str, tuple[int, int, str]] = {}

    def exits(node: ast.AST) -> int:
        count = 0
        stack = list(ast.iter_child_nodes(node))
        while stack:
            child = stack.pop()
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(child, ast.Return):
                count += 1
            elif (
                isinstance(child, (ast.If, ast.While))
                and isinstance(child.test, ast.Constant)
                and not child.test.value
            ):
                count += 1
            stack.extend(ast.iter_child_nodes(child))
        return count

    def visit(body: list[ast.stmt], prefix: str) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = prefix + node.name
                definitions, _, _ = result.get(name, (0, 0, ""))
                decorators = "|".join(ast.dump(d) for d in node.decorator_list)
                result[name] = (definitions + 1, exits(node), decorators)
                visit(node.body, name + ".")
            elif isinstance(node, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
                visit(node.body, prefix)

    visit(tree.body, "")
    return result


def _is_test_name(qualified: str) -> bool:
    return any(part.startswith(("test", "Test")) for part in qualified.split("."))


def _weakened_python(old: str, new: str, path: str) -> list[str]:
    after = _skeleton(new)
    if after is None:
        return [f"{path}: does not parse"]
    before = _skeleton(old)
    if before is None:
        return []
    found: list[str] = []
    for name, (definitions, exits, decorators) in before.items():
        if name not in after:
            if _is_test_name(name):
                found.append(f"{path}: test removed")
            continue
        new_definitions, new_exits, new_decorators = after[name]
        if new_definitions > definitions:
            found.append(f"{path}: definition repeated")
        if new_exits > exits:
            found.append(f"{path}: early exit added")
        if new_decorators != decorators:
            found.append(f"{path}: decorators changed")
    return list(dict.fromkeys(found))


def _is_test_module(path: str) -> bool:
    return path.startswith("tests/") and path.endswith(".py")


def _new_test_path(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    if path.startswith("tests/visual/baselines/") and path.endswith(".png"):
        return True
    return path.startswith("tests/") and name.startswith("test_") and name.endswith(".py")


def _weakened(repo: Path, base: str, head: str, files: list[Change]) -> list[str]:
    found: list[str] = []
    for change in files:
        if COMPARISON in (change.path, change.old_path):
            found.append(f"{COMPARISON}: changed")
        if change.status != "M" or not _is_test_module(change.path) or change.binary:
            continue
        old, new = _show(repo, base, change.path), _show(repo, head, change.path)
        if any(SUSPICIOUS_ADDED.search(line) for line in _added_lines(old, new)):
            found.append(f"{change.path}: skip, marker or collection change added")
        found += _weakened_python(old, new, change.path)
    return found


def weakened_tests(repo: Path, base: str, head: str) -> list[str]:
    """Hard ways in which the diff weakens existing tests, as short descriptions."""
    return _weakened(repo, base, head, changes(repo, base, head))


def _structure(change: Change) -> list[str]:
    """Hard reasons that depend on the shape of one change, not on its content."""
    reasons: list[str] = []
    paths = [p for p in dict.fromkeys((change.old_path, change.path)) if p]
    for path in paths:
        name = path.rsplit("/", 1)[-1]
        if any(ord(c) < 32 or ord(c) == 127 for c in path) or "<!--" in path:
            reasons.append("invalid path")
        if "__pycache__/" in path or path.endswith(".pyc"):
            reasons.append(f"test infrastructure: {path}")
        if name == "conftest.py" or name in INFRASTRUCTURE_NAMES:
            reasons.append(f"test infrastructure: {path}")
    if change.path.endswith("/__init__.py") or change.path == "__init__.py":
        if change.status in "ACR":
            reasons.append(f"test infrastructure: {change.path}")
    if SYMLINK in (change.old_mode, change.new_mode) or GITLINK in (
        change.old_mode,
        change.new_mode,
    ):
        reasons.append(f"symlink or submodule: {change.path}")
    if change.binary and not change.path.endswith(".png"):
        reasons.append(f"binary file: {change.path}")
    if change.status == "R" and any(_is_test_module(p) for p in paths):
        reasons.append(f"test file renamed: {change.old_path}")
    if change.status in "ACR" and change.path.startswith("tests/") and not _new_test_path(change.path):
        reasons.append(f"test file not allowed: {change.path}")
    return reasons


def _review(change: Change) -> list[str]:
    """Soft reasons: an existing file under tests/ lost or changed lines."""
    source = change.old_path or change.path
    if not source.startswith("tests/"):
        return []
    if change.status in "DR" or (change.status == "M" and not change.binary and change.deleted):
        return [f"existing tests changed: {source}"]
    return []


def check_fence(
    repo: Path, base: str, head: str, fence: Fence, limits: Limits
) -> FenceReport:
    files = changes(repo, base, head)
    reasons: list[str] = []
    review: list[str] = []

    def add(reason: str) -> None:
        if reason not in reasons:
            reasons.append(reason)

    for change in files:
        for path in dict.fromkeys(p for p in (change.old_path, change.path) if p):
            if _any(path, fence.forbidden):
                add(f"forbidden path: {path}")
            elif not _any(path, fence.allowed):
                add(f"outside the allowed paths: {path}")
        for reason in _structure(change):
            add(reason)
        for reason in _review(change):
            if reason not in review:
                review.append(reason)

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

    return FenceReport(not reasons, reasons, changed_lines, copy_changed, review)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--repo", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    try:
        config = load_config()
        report = check_fence(args.repo, args.base, args.head, config.fence, config.limits)
    except (GitError, ConfigError) as error:
        print(f"fence: {error}", file=sys.stderr)
        return 2
    print(json.dumps({**asdict(report), "needs_human": report.needs_human}, indent=2))
    if not report.ok:
        return 1
    return 3 if report.needs_human else 0


if __name__ == "__main__":
    sys.exit(main())
