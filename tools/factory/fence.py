"""Deterministic checks on the diff between two commits: paths, size, tests and copy.

Pure git subprocesses: it reads the diff and file contents with `git diff` and `git show` and
never checks out, imports or executes anything from the commits (Python is only parsed with
`ast`). Reasons are short and stable, and never carry file contents.

Two kinds of reasons (Ruling R37, an allow-list for tests/):
- hard reasons (`ok` is False): forbidden or unexpected paths, test infrastructure, weakened
  tests, size and the copy guard;
- review reasons (`review`, `needs_human`): existing files under tests/ may only gain lines;
  any removed or changed line asks for a human.

Honest scope: the static checks catch careless weakening of tests. Deliberate tampering
(code that runs at import or definition time, patching of pytest or of the test helpers) is
routed to a human as a soft review reason, and is defeated for real by the behavioural check of
Task 20, which runs the base commit's tests against the head site in their own pytest session.

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
TEXT_ATTRIBUTES = ("alt", "aria-label", "title", "placeholder", "value")
INLINE_TAGS = frozenset(
    "a abbr b bdi bdo cite code data dfn del em i ins kbd mark q s small span strong sub sup "
    "time u var wbr".split()
)
MARKUP_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"<script\b", r"</script\b", r"<style\b", r"</style\b", r"<textarea\b",
        r"<!--", r"-->", r"--!>", r"<!\[CDATA\[",
    )
)
DIVERGENT_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (r"</(?:script|style)\b(?!\s*>)", r"<!---?>", r"--!>", r"--\s+!?>")
)
SETUP_NAMES = frozenset(
    "setup_module teardown_module setup_function teardown_function setup_class teardown_class "
    "setup_method teardown_method setUpModule tearDownModule".split()
)
ALLOWED_DECORATOR_NAMES = frozenset({"staticmethod", "classmethod", "property"})
SOFT_ASSIGNMENT = "computed assignment"
SETTER_NAMES = frozenset({"setattr", "delattr", "__setattr__", "__delattr__"})
DYNAMIC_CALLS = frozenset("eval exec compile __import__ globals locals vars setattr delattr".split())
INFRASTRUCTURE_NAMES = ("pytest.ini", "tox.ini", "setup.cfg", ".gitattributes")
SYMLINK, GITLINK = "120000", "160000"
SUSPICIOUS_ADDED = re.compile(
    r"mark\.(skip|skipif|xfail|network|visual|multi_engine)"
    r"|\b(skip|xfail|importorskip)\s*\("
    r"|pytestmark|__test__|add_marker|applymarker|pytest_collection_modifyitems"
    r"|SkipTest|skip\.Exception"
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
            twitter = any(n == "name" and (v or "").lower().startswith("twitter:") for n, v in attrs)
            if ("name", "description") in names or og or twitter:
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
    return [len(pattern.findall(html)) for pattern in MARKUP_PATTERNS]


def _divergent_counts(html: str) -> list[int]:
    """Counts of forms that HTML parsers read differently; a new one may hide copy."""
    return [len(pattern.findall(html)) for pattern in DIVERGENT_PATTERNS]


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
            if any(
                new > old
                for old, new in zip(_divergent_counts(old_text), _divergent_counts(new_text))
            ):
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


def _blocks(node: ast.AST) -> list[list[ast.stmt]]:
    """Every statement list directly inside a compound statement (else, except, finally, case)."""
    blocks: list[list[ast.stmt]] = []
    for name in ("body", "orelse", "finalbody"):
        block = getattr(node, name, None)
        if isinstance(block, list):
            blocks.append(block)
    blocks += [handler.body for handler in getattr(node, "handlers", [])]
    blocks += [case.body for case in getattr(node, "cases", [])]
    return blocks


def _parse(source: str) -> ast.Module | None:
    try:
        return ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return None


def _skeleton(tree: ast.Module) -> dict[str, tuple[int, int, int, str]]:
    """Per function and class: (definitions, early exits, raises, decorators).

    The key is the qualified name. An early exit is a `return` or an `if False:`/`if 0:` block.
    Definitions inside any compound statement are found.
    """
    result: dict[str, tuple[int, int, int, str]] = {}

    def exits(node: ast.AST) -> tuple[int, int]:
        count = raises = 0
        stack = list(ast.iter_child_nodes(node))
        while stack:
            child = stack.pop()
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(child, ast.Raise):
                raises += 1
            elif isinstance(child, ast.Return):
                count += 1
            elif (
                isinstance(child, (ast.If, ast.While))
                and isinstance(child.test, ast.Constant)
                and not child.test.value
            ):
                count += 1
            stack.extend(ast.iter_child_nodes(child))
        return count, raises

    def visit(body: list[ast.stmt], prefix: str) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = prefix + node.name
                definitions = result.get(name, (0, 0, 0, ""))[0]
                decorators = "|".join(ast.dump(d) for d in node.decorator_list)
                early, raises = exits(node)
                result[name] = (definitions + 1, early, raises, decorators)
                visit(node.body, name + ".")
            else:
                for block in _blocks(node):
                    visit(block, prefix)

    visit(tree.body, "")
    return result


def _is_test_name(qualified: str) -> bool:
    return any(part.startswith(("test", "Test")) for part in qualified.split("."))


def _bound_names(stmt: ast.stmt) -> list[str]:
    if isinstance(stmt, (ast.Import, ast.ImportFrom)):
        return [(a.asname or a.name.split(".")[0]) for a in stmt.names]
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [stmt.name]
    if isinstance(stmt, ast.Assign):
        return [t.id for t in stmt.targets if isinstance(t, ast.Name)]
    if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
        return [stmt.target.id]
    return []


def _literal(node: ast.expr | None) -> bool:
    if node is None:
        return False
    try:
        ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return False
    return True


def _top_level_problem(stmt: ast.stmt) -> str | None:
    """What is not allowed about an added top-level statement of a test module, or None."""
    if isinstance(stmt, ast.ImportFrom) and any(a.name == "*" for a in stmt.names):
        return "star import"
    if isinstance(stmt, (ast.Import, ast.ImportFrom, ast.ClassDef)):
        return None
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return "hook function" if stmt.name.startswith("pytest_") else None
    if isinstance(stmt, ast.Assign):
        if not all(isinstance(t, ast.Name) for t in stmt.targets):
            return "assignment"
        if any(n.startswith(("pytest_", "__")) for n in _bound_names(stmt)):
            return "reserved name"
        return None if _literal(stmt.value) else SOFT_ASSIGNMENT
    if isinstance(stmt, ast.AnnAssign):
        if not isinstance(stmt.target, ast.Name):
            return "assignment"
        if stmt.target.id.startswith(("pytest_", "__")):
            return "reserved name"
        return None if _literal(stmt.value) else SOFT_ASSIGNMENT
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(
        stmt.value.value, str
    ):
        return None
    return type(stmt).__name__


def _dotted(node: ast.expr) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return ""


def _allowed_name(name: str) -> bool:
    return name == "pytest.fixture" or name == "pytest.param" or name.startswith("pytest.mark.")


def _suspicious(expr: ast.AST | None) -> bool:
    """True when `expr` can run code: a lambda, a walrus or a call outside pytest's markers."""
    if expr is None:
        return False
    for node in ast.walk(expr):
        if isinstance(node, (ast.Lambda, ast.NamedExpr)):
            return True
        if isinstance(node, ast.Call) and not _allowed_name(_dotted(node.func)):
            return True
    return False


def _decorator_suspicious(node: ast.expr) -> bool:
    if isinstance(node, ast.Call):
        return _suspicious(node)
    name = _dotted(node)
    return not (_allowed_name(name) or name in ALLOWED_DECORATOR_NAMES)


def _is_autouse(node: ast.expr) -> bool:
    return isinstance(node, ast.Call) and any(
        k.arg == "autouse" and not (isinstance(k.value, ast.Constant) and k.value.value is False)
        for k in node.keywords
    )


def _class_body_problem(stmt: ast.stmt) -> bool:
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Pass)):
        return False
    if isinstance(stmt, ast.Assign):
        return not (all(isinstance(t, ast.Name) for t in stmt.targets) and _literal(stmt.value))
    if isinstance(stmt, ast.AnnAssign):
        return not (isinstance(stmt.target, ast.Name) and _literal(stmt.value))
    return not (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )


def _soft_counts(tree: ast.Module) -> Counter[str]:
    """Counts of constructs that run code at definition time or rewire state; a human reads them."""
    counts: Counter[str] = Counter()
    from_builtins = {
        (a.asname or a.name)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "builtins"
        for a in node.names
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "__dict__":
            counts["dynamic attribute set"] += 1
        elif isinstance(node, ast.Call):
            callee = node.func
            last = callee.attr if isinstance(callee, ast.Attribute) else getattr(callee, "id", "")
            if last in SETTER_NAMES or (isinstance(callee, ast.Name) and callee.id in from_builtins):
                counts["dynamic attribute set"] += 1
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            counts["decorator"] += sum(_decorator_suspicious(d) for d in node.decorator_list)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            arguments = node.args
            every = [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]
            every += [a for a in (arguments.vararg, arguments.kwarg) if a]
            counts["annotation"] += sum(_suspicious(a.annotation) for a in every)
            counts["annotation"] += _suspicious(node.returns)
            counts["default argument"] += sum(
                _suspicious(d) for d in [*arguments.defaults, *arguments.kw_defaults] if d
            )
            if node.name in SETUP_NAMES:
                counts["setup or teardown function"] += 1
            if any(_is_autouse(d) for d in node.decorator_list):
                counts["autouse fixture"] += 1
        elif isinstance(node, ast.ClassDef):
            counts["class bases"] += sum(_suspicious(b) for b in node.bases) + sum(
                _suspicious(k.value) for k in node.keywords
            )
            counts["class body statement"] += sum(_class_body_problem(s) for s in node.body)
        elif isinstance(node, ast.AnnAssign):
            counts["annotation"] += _suspicious(node.annotation)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and not (
                len(node.args) > 1
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
            )
        ):
            counts["non-literal getattr"] += 1
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        elif isinstance(node, ast.Delete):
            targets = list(node.targets)
        for target in targets:
            if isinstance(target, (ast.Attribute, ast.Subscript)):
                root = target
                while isinstance(root, (ast.Attribute, ast.Subscript)):
                    root = root.value
                if not (isinstance(root, ast.Name) and root.id in ("self", "cls")):
                    counts["attribute or item assigned"] += 1
    return counts


def _hazards(tree: ast.Module, tests: set[str]) -> Counter[str]:
    """Counts of constructs that can rewire other tests; any increase is a hard reason."""
    counts: Counter[str] = Counter()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            if node.id in tests:
                counts["a test name is rebound or deleted"] += 1
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in DYNAMIC_CALLS
        ):
            counts["dynamic code access"] += 1
    return counts


def _binding_counts(tree: ast.Module) -> Counter[str]:
    return Counter(name for stmt in tree.body for name in _bound_names(stmt))


def _import_sources(tree: ast.Module) -> dict[str, set[tuple]]:
    sources: dict[str, set[tuple]] = {}
    for stmt in tree.body:
        if isinstance(stmt, ast.Import):
            for a in stmt.names:
                sources.setdefault(a.asname or a.name.split(".")[0], set()).add((a.name,))
        elif isinstance(stmt, ast.ImportFrom):
            for a in stmt.names:
                sources.setdefault(a.asname or a.name, set()).add(
                    (stmt.module, stmt.level, a.name)
                )
    return sources


def _top_names(tree: ast.Module) -> set[str]:
    return {name for stmt in tree.body for name in _bound_names(stmt)}


def _conftest_fixtures(repo: Path, head: str) -> set[str]:
    """Names of the fixtures defined in every conftest.py of the head tree."""
    listing = _git(repo, "ls-tree", "-r", "--name-only", "-z", head).decode("utf-8", "replace")
    names: set[str] = set()
    for path in listing.split("\0"):
        if path.rsplit("/", 1)[-1] != "conftest.py":
            continue
        tree = _parse(_show(repo, head, path))
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
                "fixture" in ast.dump(d) for d in node.decorator_list
            ):
                names.add(node.name)
    return names


def _module_findings(
    old: str, new: str, path: str, fixtures: set[str]
) -> tuple[list[str], list[str], list[str]]:
    """(infrastructure, weakened-test, human-review) details for one new or changed test module."""
    after = _parse(new)
    if after is None:
        return [], [f"{path}: does not parse"], []
    before = _parse(old) if old else ast.Module(body=[], type_ignores=[])
    if before is None:
        return [], [], []
    infra: list[str] = []
    weak: list[str] = []
    soft: list[str] = []

    seen = Counter(ast.dump(stmt) for stmt in before.body)
    for stmt in after.body:
        dump = ast.dump(stmt)
        if seen[dump] > 0:
            seen[dump] -= 1
            continue
        problem = _top_level_problem(stmt)
        if problem == SOFT_ASSIGNMENT:
            soft.append(f"{path}: computed assignment at the top level")
        elif problem:
            infra.append(f"{path}: {problem} at the top level")

    base_count, head_count = _binding_counts(before), _binding_counts(after)
    if any(
        head_count[n] > base_count[n] and (base_count[n] > 0 or n in fixtures)
        for n in head_count
    ):
        infra.append(f"{path}: rebinds an existing name")
    base_sources = _import_sources(before)
    for name, sources in _import_sources(after).items():
        if name in base_sources and not sources <= base_sources[name]:
            infra.append(f"{path}: rebinds an existing name")

    tests = {
        s.name
        for s in before.body
        if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and _is_test_name(s.name)
    }
    old_hazards, new_hazards = _hazards(before, tests), _hazards(after, tests)
    for kind, count in new_hazards.items():
        if count > old_hazards[kind]:
            weak.append(f"{path}: {kind}")

    old_soft, new_soft = _soft_counts(before), _soft_counts(after)
    for kind, count in new_soft.items():
        if kind == "autouse fixture" and not old:
            continue
        if count > old_soft[kind]:
            soft.append(f"{path}: {kind}")

    if old:
        old_skeleton, new_skeleton = _skeleton(before), _skeleton(after)
        for name, (definitions, exits, raises, decorators) in old_skeleton.items():
            if name not in new_skeleton:
                if _is_test_name(name):
                    weak.append(f"{path}: test removed")
                continue
            new_definitions, new_exits, new_raises, new_decorators = new_skeleton[name]
            if new_definitions > definitions:
                weak.append(f"{path}: definition repeated")
            if new_exits > exits:
                weak.append(f"{path}: early exit added")
            if new_raises > raises:
                if name.rsplit(".", 1)[-1].startswith("test"):
                    weak.append(f"{path}: early exit added")
                else:
                    soft.append(f"{path}: raise added in a helper")
            if new_decorators != decorators:
                soft.append(f"{path}: decorators changed")
    return (
        list(dict.fromkeys(infra)),
        list(dict.fromkeys(weak)),
        list(dict.fromkeys(soft)),
    )


def _is_test_module(path: str) -> bool:
    return path.startswith("tests/") and path.endswith(".py")


def _new_test_path(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    if path.startswith("tests/visual/baselines/") and path.endswith(".png"):
        return True
    return path.startswith("tests/") and name.startswith("test_") and name.endswith(".py")


def _test_findings(
    repo: Path, base: str, head: str, files: list[Change]
) -> tuple[list[str], list[str], list[str]]:
    """(infrastructure, weakened-test, human-review) details for the test modules in the diff."""
    infra: list[str] = []
    weak: list[str] = []
    soft: list[str] = []
    fixtures: set[str] | None = None
    for change in files:
        if COMPARISON in (change.path, change.old_path):
            weak.append(f"{COMPARISON}: changed")
        if change.status not in "AM" or not _is_test_module(change.path) or change.binary:
            continue
        old = _show(repo, base, change.path) if change.status == "M" else ""
        new = _show(repo, head, change.path)
        if change.status == "M" and any(
            SUSPICIOUS_ADDED.search(line) for line in _added_lines(old, new)
        ):
            weak.append(f"{change.path}: skip, marker or collection change added")
        if fixtures is None:
            fixtures = _conftest_fixtures(repo, head)
        found_infra, found_weak, found_soft = _module_findings(old, new, change.path, fixtures)
        infra += found_infra
        weak += found_weak
        soft += found_soft
    return infra, weak, soft


def weakened_tests(repo: Path, base: str, head: str) -> list[str]:
    """Hard ways in which the diff weakens existing tests, as short descriptions."""
    return _test_findings(repo, base, head, changes(repo, base, head))[1]


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

    infra, weakened, human = _test_findings(repo, base, head, files)
    for detail in human:
        reason = f"test code needs a human: {detail}"
        if reason not in review:
            review.append(reason)
    for reason in infra:
        add(f"test infrastructure: {reason}")
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
