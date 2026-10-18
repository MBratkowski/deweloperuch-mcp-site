"""Issue drafts from the QA agent: validation, GitHub issue bodies with hidden markers, dedup."""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from tools.factory.findings import Group

# The rules below mirror .github/factory/schemas/drafts.json; a test keeps them equal.
SEVERITIES = ("S1", "S2", "S3", "S4")
KINDS = ("detector", "script")
TOP_LEVEL = ("drafts", "suggestions", "dropped")
DRAFT_FIELDS = (
    "title",
    "severity",
    "fingerprints",
    "problem",
    "actual",
    "expected",
    "why",
    "acceptance",
    "sources",
    "reproduction",
)
REPRODUCTION_FIELDS = ("kind", "groups", "script")
SUGGESTION_FIELDS = ("title", "problem", "screenshot")
DROPPED_FIELDS = ("fingerprint", "reason")
SCRIPT_PATTERN = re.compile(r"^factory-out/repros/[A-Za-z0-9_.-]+\.py$")

_FP = re.compile(r"<!-- factory:fp=([0-9a-f]+) -->")
_GROUPS = re.compile(r"<!-- factory:groups=([A-Za-z0-9+/=]*) -->")
_SCRIPT = re.compile(r"<!-- factory:script=(\S+) -->")
_REJECTED_LABELS = frozenset({"invalid", "wontfix", "duplicate"})


class DraftError(ValueError):
    def __init__(self, path: str, message: str) -> None:
        super().__init__(f"{path}: {message}")
        self.path = path
        self.message = message


@dataclass(frozen=True)
class Reproduction:
    kind: Literal["detector", "script"]
    groups: list[dict[str, Any]]
    script: str | None


@dataclass(frozen=True)
class Draft:
    title: str
    severity: str
    fingerprints: list[str]
    problem: str
    actual: str
    expected: str
    why: str
    acceptance: list[str]
    sources: list[str]
    reproduction: Reproduction


@dataclass(frozen=True)
class Suggestion:
    title: str
    problem: str
    screenshot: str


@dataclass(frozen=True)
class Dropped:
    fingerprint: str
    reason: str


@dataclass(frozen=True)
class Where:
    pages: Sequence[str]
    engines: Sequence[str]
    widths: Sequence[int]
    mode: str
    live_sha: str
    main_sha: str
    url: str


@dataclass(frozen=True)
class Dedup:
    decision: Literal["new", "skip", "regression"]
    related: int | None


def _object(value: Any, path: str, fields: Sequence[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DraftError(path, "must be an object")
    for name in fields:
        if name not in value:
            raise DraftError(f"{path}.{name}" if path else name, "is required")
    for name in value:
        if name not in fields:
            raise DraftError(f"{path}.{name}" if path else name, "is not allowed")
    return value


def _text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DraftError(path, "must be a non-empty string")
    return value


def _texts(value: Any, path: str, *, minimum: int) -> list[str]:
    if not isinstance(value, list):
        raise DraftError(path, "must be an array")
    if len(value) < minimum:
        raise DraftError(path, f"needs at least {minimum} item(s)")
    return [_text(item, f"{path}[{i}]") for i, item in enumerate(value)]


def _array(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise DraftError(path, "must be an array")
    return value


def _reproduction(value: Any, path: str) -> Reproduction:
    data = _object(value, path, REPRODUCTION_FIELDS)
    kind = data["kind"]
    if kind not in KINDS:
        raise DraftError(f"{path}.kind", f"must be one of {', '.join(KINDS)}")
    groups = _array(data["groups"], f"{path}.groups")
    for i, group in enumerate(groups):
        if not isinstance(group, dict):
            raise DraftError(f"{path}.groups[{i}]", "must be an object")
    script = data["script"]
    if kind == "script":
        if (
            not isinstance(script, str)
            or not SCRIPT_PATTERN.match(script)
            or ".." in script
        ):
            raise DraftError(f"{path}.script", "must be factory-out/repros/<name>.py")
        if groups:
            raise DraftError(
                f"{path}.groups", "must be empty for a script reproduction"
            )
    else:
        if script is not None:
            raise DraftError(
                f"{path}.script", "must be null for a detector reproduction"
            )
        if not groups:
            raise DraftError(f"{path}.groups", "needs at least 1 item(s)")
    return Reproduction(kind, groups, script)


def _draft(value: Any, path: str) -> Draft:
    data = _object(value, path, DRAFT_FIELDS)
    severity = data["severity"]
    if severity not in SEVERITIES:
        raise DraftError(f"{path}.severity", f"must be one of {', '.join(SEVERITIES)}")
    return Draft(
        title=_text(data["title"], f"{path}.title"),
        severity=severity,
        fingerprints=_texts(data["fingerprints"], f"{path}.fingerprints", minimum=1),
        problem=_text(data["problem"], f"{path}.problem"),
        actual=_text(data["actual"], f"{path}.actual"),
        expected=_text(data["expected"], f"{path}.expected"),
        why=_text(data["why"], f"{path}.why"),
        acceptance=_texts(data["acceptance"], f"{path}.acceptance", minimum=1),
        sources=_texts(data["sources"], f"{path}.sources", minimum=0),
        reproduction=_reproduction(data["reproduction"], f"{path}.reproduction"),
    )


def load_drafts(data: Any) -> tuple[list[Draft], list[Suggestion], list[Dropped]]:
    """Validate the agent's output by the rules of schemas/drafts.json."""
    top = _object(data, "", TOP_LEVEL)
    drafts = [
        _draft(v, f"drafts[{i}]") for i, v in enumerate(_array(top["drafts"], "drafts"))
    ]
    suggestions = []
    for i, value in enumerate(_array(top["suggestions"], "suggestions")):
        path = f"suggestions[{i}]"
        item = _object(value, path, SUGGESTION_FIELDS)
        suggestions.append(
            Suggestion(*(_text(item[f], f"{path}.{f}") for f in SUGGESTION_FIELDS))
        )
    dropped = []
    for i, value in enumerate(_array(top["dropped"], "dropped")):
        path = f"dropped[{i}]"
        item = _object(value, path, DROPPED_FIELDS)
        dropped.append(
            Dropped(*(_text(item[f], f"{path}.{f}") for f in DROPPED_FIELDS))
        )
    return drafts, suggestions, dropped


def _bullets(items: Sequence[str]) -> str:
    return "\n".join(f"- {item}" for item in items) if items else "- none identified"


def _list(items: Sequence[object]) -> str:
    return ", ".join(str(item) for item in items)


def render_issue(
    draft: Draft,
    where: Where,
    evidence: Mapping[str, str],
    reproduce_command: str,
    script_source: str | None = None,
) -> tuple[str, str]:
    """Return the issue title and body: eight sections, then the hidden markers."""
    script = draft.reproduction.script
    script_name = script.rsplit("/", 1)[-1] if script else None
    reproduce = [f"```sh\n{reproduce_command}\n```"]
    if script is not None:
        shown = (
            script_source
            if script_source is not None
            else f"See {evidence.get(script_name or '', script)}"
        )
        reproduce.append(
            f"<details>\n<summary>{script_name}</summary>\n\n```python\n{shown.rstrip()}\n```\n\n</details>"
        )
    images = [
        f"![{name}]({url})" for name, url in evidence.items() if name != script_name
    ]
    sections = [
        ("Problem", draft.problem),
        (
            "Where",
            "\n".join(
                [
                    f"- Pages: {_list(where.pages)}",
                    f"- Engines: {_list(where.engines)}",
                    f"- Widths: {_list(where.widths)} px",
                    f"- Mode: {where.mode}",
                    f"- Live SHA: `{where.live_sha}`",
                    f"- `main` SHA: `{where.main_sha}`",
                    f"- URL: {where.url}",
                ]
            ),
        ),
        ("Reproduce", "\n\n".join(reproduce)),
        (
            "Actual and expected",
            f"**Actual:** {draft.actual}\n\n**Expected:** {draft.expected}",
        ),
        ("Why it matters", draft.why),
        ("Evidence", "\n\n".join(images) if images else "No images."),
        ("Acceptance criteria", _bullets(draft.acceptance)),
        ("Likely sources", _bullets([f"`{s}`" for s in draft.sources])),
    ]
    parts = [f"## {name}\n\n{text}" for name, text in sections]
    markers = [f"<!-- factory:fp={fp} -->" for fp in draft.fingerprints]
    encoded = base64.b64encode(
        json.dumps(draft.reproduction.groups, sort_keys=True).encode()
    )
    markers.append(f"<!-- factory:groups={encoded.decode()} -->")
    if script is not None:
        markers.append(f"<!-- factory:script={evidence.get(script_name or '', '')} -->")
    return draft.title, "\n\n".join(parts) + "\n\n" + "\n".join(markers) + "\n"


def fingerprints_in(body: str) -> set[str]:
    return set(_FP.findall(body or ""))


def groups_in(body: str) -> list[Group]:
    match = _GROUPS.search(body or "")
    if match is None:
        return []
    return [Group.from_json(d) for d in json.loads(base64.b64decode(match.group(1)))]


def script_in(body: str) -> str | None:
    match = _SCRIPT.search(body or "")
    return match.group(1) if match else None


def dedup(
    fingerprints: set[str], issues: Sequence[dict[str, Any]], trusted: frozenset[str]
) -> Dedup:
    """Decide from issues by trusted authors only; a stranger's forged marker is ignored."""
    open_numbers: list[int] = []
    rejected: list[int] = []
    completed: list[int] = []
    for issue in issues:
        if (
            "pull_request" in issue
            or (issue.get("user") or {}).get("login") not in trusted
        ):
            continue
        if not fingerprints & fingerprints_in(issue.get("body") or ""):
            continue
        number = issue["number"]
        labels = {label["name"] for label in issue.get("labels", [])}
        if issue["state"] == "open":
            open_numbers.append(number)
        elif issue.get("state_reason") == "not_planned" or labels & _REJECTED_LABELS:
            rejected.append(number)
        else:
            completed.append(number)
    if open_numbers:
        return Dedup("skip", max(open_numbers))
    if rejected:
        return Dedup("skip", max(rejected))
    if completed:
        return Dedup("regression", max(completed))
    return Dedup("new", None)
