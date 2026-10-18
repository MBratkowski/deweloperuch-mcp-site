"""The workflows lint clean, every factory job stops when the factory is off, and agents hold no write token."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
MODE_GUARD = "vars.FACTORY_MODE != 'off'"
AGENT_ACTION = "anthropics/claude-code-action"
APP_TOKEN_ACTION = "actions/create-github-app-token"
APP_KEY = "FACTORY_APP_PRIVATE_KEY"
# (workflow file, job id) -> the permission scopes an agent job may hold with write access.
# Empty until the reviewer job (Task 20), which posts its review with GITHUB_TOKEN.
AGENT_WRITE_ALLOWED: dict[tuple[str, str], frozenset[str]] = {}


def _workflow_files() -> list[Path]:
    return sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")])


def _factory_workflows() -> list[Path]:
    return sorted(WORKFLOWS.glob("factory-*.yml"))


def test_actionlint_passes() -> None:
    actionlint = shutil.which("actionlint", path=str(Path(sys.executable).parent))
    assert actionlint, "actionlint is missing from the environment (dev dependency actionlint-py)"
    files = [str(path) for path in _workflow_files()]
    result = subprocess.run(
        [actionlint, "-no-color", *files], cwd=ROOT, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_factory_jobs_guard_on_mode() -> None:
    paths = _factory_workflows()
    assert WORKFLOWS / "factory-qa.yml" in paths
    for path in paths:
        jobs = load_yaml(path)["jobs"]
        assert jobs, f"{path.name} has no jobs"
        for name, job in jobs.items():
            condition = str(job.get("if", ""))
            assert MODE_GUARD in condition, f"{path.name}: job {name} runs when FACTORY_MODE is off"


def test_agents_hold_no_write_token() -> None:
    agents = 0
    for path in _workflow_files():
        workflow = load_yaml(path)
        for name, job in workflow["jobs"].items():
            uses = [str(step.get("uses", "")) for step in job.get("steps") or []]
            if not any(action.startswith(AGENT_ACTION) for action in uses):
                continue
            agents += 1
            where = f"{path.name}: job {name}"
            assert not any(action.startswith(APP_TOKEN_ACTION) for action in uses), f"{where} mints the App token"
            assert APP_KEY not in json.dumps(job), f"{where} can read the App private key"
            permissions = job["permissions"] if "permissions" in job else workflow.get("permissions")
            allowed = AGENT_WRITE_ALLOWED.get((path.name, name), frozenset())
            writes = _write_scopes(permissions, where)
            assert writes <= allowed, f"{where} holds write access: {sorted(writes - allowed)}"
    assert agents, "no job runs anthropics/claude-code-action"


def _write_scopes(permissions: Any, where: str) -> set[str]:
    if permissions is None:
        pytest.fail(f"{where} declares no permissions, so its GITHUB_TOKEN gets the repository default")
    if isinstance(permissions, str):
        assert permissions == "read-all", f"{where} has permissions: {permissions}"
        return set()
    return {scope for scope, level in permissions.items() if level == "write"}


# --- a strict reader for the YAML that workflows use -----------------------------------------
# The lock has no YAML library and the factory adds no dependency but actionlint-py. The reader
# takes block mappings and sequences, plain and quoted one-line scalars, block scalars (| and >)
# and one-line flow collections; anything else raises, so a workflow in unsupported syntax fails
# these tests instead of slipping past them.

_PLAIN_KEY = re.compile(r"([A-Za-z0-9_][\w.\-/]*)[ ]*:(?:[ ]+(.*))?")
_QUOTED_KEY = re.compile(r"""('(?:[^']|'')*'|"(?:[^"\\]|\\.)*")[ ]*:(?:[ ]+(.*))?""")
_ESCAPES = {"n": "\n", "t": "\t", '"': '"', "\\": "\\", "/": "/", "0": "\0"}


def load_yaml(path: Path) -> Any:
    return _Reader(path.read_text(encoding="utf-8")).parse()


class _Reader:
    def __init__(self, text: str) -> None:
        self.lines = text.splitlines()
        self.i = 0

    def parse(self) -> Any:
        head = self._peek()
        if head is None:
            return None
        value = self._node(head[0])
        if self._peek() is not None:
            raise ValueError(f"line {self.i + 1}: unexpected indentation")
        return value

    def _peek(self) -> tuple[int, str] | None:
        """The indentation and text of the next line that is not blank or a comment."""
        while self.i < len(self.lines):
            line = self.lines[self.i]
            text = line.lstrip(" ")
            if text and not text.startswith("#"):
                if text.startswith("\t") or text.startswith(("---", "...", "? ", "%")):
                    raise ValueError(f"line {self.i + 1}: unsupported YAML: {text!r}")
                return len(line) - len(text), text.rstrip()
            self.i += 1
        return None

    def _node(self, indent: int) -> Any:
        head = self._peek()
        assert head is not None
        return self._sequence(indent) if _is_item(head[1]) else self._mapping(indent)

    def _mapping(self, indent: int) -> dict[str, Any]:
        result: dict[str, Any] = {}
        while (head := self._peek()) is not None and head[0] == indent and not _is_item(head[1]):
            key, rest = _split_key(head[1], self.i)
            if key in result:
                raise ValueError(f"line {self.i + 1}: duplicate key {key!r}")
            self.i += 1
            result[key] = self._value(rest, indent)
        if head is not None and head[0] > indent:
            raise ValueError(f"line {self.i + 1}: unexpected indentation")
        return result

    def _sequence(self, indent: int) -> list[Any]:
        items: list[Any] = []
        while (head := self._peek()) is not None and head[0] == indent and _is_item(head[1]):
            content = head[1][1:].lstrip(" ")
            if not content:
                self.i += 1
                items.append(self._value("", indent))
            elif _is_key(content):
                # The item is a mapping whose first key sits at the item's content column.
                column = indent + len(head[1]) - len(content)
                self.lines[self.i] = " " * column + content
                items.append(self._mapping(column))
            else:
                if _is_item(content) or content[0] in "|>":
                    raise ValueError(f"line {self.i + 1}: unsupported YAML: {head[1]!r}")
                items.append(_scalar(content, self.i))
                self.i += 1
        if head is not None and head[0] > indent:
            raise ValueError(f"line {self.i + 1}: unexpected indentation")
        return items

    def _value(self, rest: str, indent: int) -> Any:
        if rest and not rest.startswith("#"):
            return self._block_scalar(rest, indent) if rest[0] in "|>" else _scalar(rest, self.i - 1)
        head = self._peek()
        if head is not None and head[0] > indent:
            return self._node(head[0])
        if head is not None and head[0] == indent and _is_item(head[1]):
            return self._sequence(indent)
        return None

    def _block_scalar(self, header: str, indent: int) -> str:
        chomp = header[1:].split(" #")[0].strip()
        if chomp not in ("", "-", "+"):
            raise ValueError(f"line {self.i}: unsupported block scalar header {header!r}")
        lines: list[str] = []
        column: int | None = None
        while self.i < len(self.lines):
            line = self.lines[self.i]
            text = line.lstrip(" ")
            if text:
                current = len(line) - len(text)
                if current <= indent:
                    break
                column = current if column is None else column
                if current < column:
                    raise ValueError(f"line {self.i + 1}: block scalar line indented less than the first")
                lines.append(line[column:])
            else:
                lines.append("")
            self.i += 1
        while lines and not lines[-1]:
            lines.pop()
        if header[0] == "|":
            text = "\n".join(lines)
        else:  # folded: the lines of each paragraph joined by spaces
            paragraphs = "\n".join(lines).split("\n\n")
            text = "\n".join(" ".join(p.split("\n")) for p in paragraphs)
        return text if chomp == "-" or not text else text + "\n"


def _is_item(text: str) -> bool:
    return text == "-" or text.startswith("- ")


def _is_key(text: str) -> bool:
    return bool(_PLAIN_KEY.fullmatch(text) or _QUOTED_KEY.fullmatch(text))


def _split_key(text: str, line: int) -> tuple[str, str]:
    match = _PLAIN_KEY.fullmatch(text)
    if match:
        return match.group(1), (match.group(2) or "").strip()
    match = _QUOTED_KEY.fullmatch(text)
    if match:
        key, _ = _quoted(match.group(1), line)
        return key, (match.group(2) or "").strip()
    raise ValueError(f"line {line + 1}: expected 'key: value', got {text!r}")


def _scalar(text: str, line: int) -> Any:
    if text[0] in "'\"":
        value, end = _quoted(text, line)
        _comment_only(text[end:], line)
        return value
    if text[0] in "[{":
        return _flow(text, line)
    if text[0] in "&*!%@`|>":
        raise ValueError(f"line {line + 1}: unsupported YAML: {text!r}")
    cut = text.find(" #")
    return _plain(text if cut == -1 else text[:cut].rstrip())


def _plain(text: str) -> Any:
    if text in ("true", "True", "TRUE"):
        return True
    if text in ("false", "False", "FALSE"):
        return False
    if text in ("", "~", "null", "Null", "NULL"):
        return None
    return int(text) if re.fullmatch(r"-?[0-9]+", text) else text


def _quoted(text: str, line: int) -> tuple[str, int]:
    """The value of the quoted scalar at the start of `text` and the index just past it."""
    quote, out, i = text[0], [], 1
    while i < len(text):
        char = text[i]
        if quote == "'" and char == "'":
            if text[i + 1 : i + 2] != "'":
                return "".join(out), i + 1
            out.append("'")
            i += 2
            continue
        if quote == '"' and char == '"':
            return "".join(out), i + 1
        if quote == '"' and char == "\\":
            escape = text[i + 1 : i + 2]
            if escape not in _ESCAPES:
                raise ValueError(f"line {line + 1}: unsupported escape \\{escape}")
            out.append(_ESCAPES[escape])
            i += 2
            continue
        out.append(char)
        i += 1
    raise ValueError(f"line {line + 1}: unterminated or multi-line quoted scalar")


def _comment_only(tail: str, line: int) -> None:
    tail = tail.strip()
    if tail and not tail.startswith("#"):
        raise ValueError(f"line {line + 1}: unexpected text after a scalar: {tail!r}")


def _flow(text: str, line: int) -> Any:
    """A one-line flow sequence or mapping of scalars: [a, "b"], {}, {k: v}."""
    close = "]" if text[0] == "[" else "}"
    parts, current, i = [], "", 1
    while i < len(text):
        char = text[i]
        if char in "'\"":
            _, end = _quoted(text[i:], line)
            current += text[i : i + end]
            i += end
            continue
        if char in "[{":
            raise ValueError(f"line {line + 1}: nested flow collections are unsupported")
        if char == close:
            _comment_only(text[i + 1 :], line)
            if current.strip():
                parts.append(current.strip())
            break
        if char == ",":
            parts.append(current.strip())
            current = ""
        else:
            current += char
        i += 1
    else:
        raise ValueError(f"line {line + 1}: unterminated flow collection")
    if close == "]":
        return [_scalar(part, line) for part in parts]
    return dict(_split_key_value(part, line) for part in parts)


def _split_key_value(part: str, line: int) -> tuple[str, Any]:
    key, rest = _split_key(part, line)
    return key, _scalar(rest, line) if rest else None
