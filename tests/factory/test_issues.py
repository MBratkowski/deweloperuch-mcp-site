"""Issue drafts are validated, rendered with hidden markers and deduplicated by fingerprint."""

import json
from pathlib import Path
from typing import Any

import pytest

from tools.factory import issues
from tools.factory.findings import Cell, Finding, Group
from tools.factory.issues import (
    Dedup,
    DraftError,
    Where,
    dedup,
    fingerprints_in,
    groups_in,
    load_drafts,
    render_issue,
    script_in,
)

SNAPSHOT = Path(__file__).parent / "snapshots" / "issue.md"
SCHEMA = Path(__file__).parents[2] / ".github" / "factory" / "schemas" / "drafts.json"
TRUSTED = frozenset({"MBratkowski", "deweloperuch-factory[bot]"})
SCRIPT_URL = "https://raw.githubusercontent.com/o/r/abc123/issues/f1/9/measure.py"


def make_group() -> Group:
    cell = Cell("index", "webkit", 375, 800, "default")
    finding = Finding(
        "overflow",
        cell,
        "div.term",
        "scrollWidth 640 > clientWidth 375",
        (0, 0, 640, 20),
    )
    return Group(
        "overflow", "index", "default", "div.term", ("webkit",), (375,), (finding,)
    )


def draft_dict(**changes: Any) -> dict[str, Any]:
    draft: dict[str, Any] = {
        "title": "Installation prompt fits a 375 px viewport in WebKit",
        "severity": "S3",
        "fingerprints": ["aaaaaaaaaaaa"],
        "problem": "The installation prompt overflows horizontally.",
        "actual": "scrollWidth 640 px in a 375 px viewport.",
        "expected": "No horizontal overflow.",
        "why": "Visitors must scroll sideways; WCAG 1.4.10 Reflow.",
        "acceptance": [
            "The reproduction passes in webkit at 375 px",
            "No new finding on index",
        ],
        "sources": ["css/site.css", "tools/html_helpers.py"],
        "reproduction": {
            "kind": "detector",
            "groups": [make_group().to_json()],
            "script": None,
        },
    }
    draft.update(changes)
    return draft


def payload(**changes: Any) -> dict[str, Any]:
    return {"drafts": [draft_dict(**changes)], "suggestions": [], "dropped": []}


WHERE = Where(
    pages=("index",),
    engines=("webkit",),
    widths=(375,),
    mode="default",
    live_sha="1" * 40,
    main_sha="2" * 40,
    url="https://example.test/",
)
COMMAND = "uv run python -m tools.factory.probe --reproduce finding.json --target https://example.test/"
EVIDENCE = {
    "viewport.png": "https://raw.example/viewport.png",
    "crop.png": "https://raw.example/crop.png",
}


def render(**changes: Any) -> tuple[str, str]:
    (draft,), _, _ = load_drafts(payload(**changes))
    return render_issue(draft, WHERE, EVIDENCE, COMMAND)


def test_render_issue_matches_snapshot() -> None:
    title, body = render()
    assert title == "Installation prompt fits a 375 px viewport in WebKit"
    assert body == SNAPSHOT.read_text()


def test_body_headings_in_order() -> None:
    _, body = render()
    headings = [line[3:] for line in body.splitlines() if line.startswith("## ")]
    assert headings == [
        "Problem",
        "Where",
        "Reproduce",
        "Actual and expected",
        "Why it matters",
        "Evidence",
        "Acceptance criteria",
        "Likely sources",
    ]
    assert "![viewport.png](https://raw.example/viewport.png)" in body


def test_markers_round_trip() -> None:
    _, body = render(fingerprints=["aaaaaaaaaaaa", "bbbbbbbbbbbb"])
    assert fingerprints_in(body) == {"aaaaaaaaaaaa", "bbbbbbbbbbbb"}
    assert groups_in(body) == [make_group()]
    assert script_in(body) is None


def test_script_draft_renders_script_marker() -> None:
    script = {"kind": "script", "groups": [], "script": "factory-out/repros/measure.py"}
    (draft,), _, _ = load_drafts(payload(reproduction=script))
    evidence = {**EVIDENCE, "measure.py": SCRIPT_URL}
    _, body = render_issue(
        draft, WHERE, evidence, "python3 measure.py", script_source="print(1)"
    )
    assert script_in(body) == SCRIPT_URL
    assert groups_in(body) == []
    assert "<details>" in body and "print(1)" in body
    assert "![measure.py]" not in body


def issue(
    number: int,
    fingerprint: str = "aaaaaaaaaaaa",
    state: str = "open",
    reason: str | None = None,
    labels: tuple[str, ...] = (),
    login: str = "MBratkowski",
    pull_request: bool = False,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "number": number,
        "state": state,
        "state_reason": reason,
        "labels": [{"name": name} for name in labels],
        "user": {"login": login},
        "body": f"text\n{issues.SENTINEL}\n<!-- factory:fp={fingerprint} -->\n",
    }
    if pull_request:
        data["pull_request"] = {"url": "x"}
    return data


@pytest.mark.parametrize(
    ("existing", "expected"),
    [
        ([issue(5)], Dedup("skip", 5)),
        ([issue(5, state="closed", reason="not_planned")], Dedup("skip", 5)),
        (
            [issue(5, state="closed", reason="completed", labels=("wontfix",))],
            Dedup("skip", 5),
        ),
        (
            [issue(5, state="closed", reason="completed", labels=("invalid",))],
            Dedup("skip", 5),
        ),
        (
            [issue(5, state="closed", reason="completed", labels=("duplicate",))],
            Dedup("skip", 5),
        ),
        ([issue(5, state="closed", reason="completed")], Dedup("regression", 5)),
        (
            [
                issue(5, state="closed", reason="completed"),
                issue(8, state="closed", reason="completed"),
            ],
            Dedup("regression", 8),
        ),
        ([issue(5, state="closed", reason="completed"), issue(9)], Dedup("skip", 9)),
        ([issue(5, fingerprint="zzzzzzzzzzzz")], Dedup("new", None)),
        ([], Dedup("new", None)),
        ([issue(5, pull_request=True)], Dedup("new", None)),
    ],
)
def test_dedup(existing: list[dict[str, Any]], expected: Dedup) -> None:
    assert dedup({"aaaaaaaaaaaa"}, existing, TRUSTED) == expected


def test_forged_marker_is_ignored() -> None:
    forged = [
        issue(5, login="stranger"),
        issue(6, login="stranger", state="closed", reason="completed"),
    ]
    assert dedup({"aaaaaaaaaaaa"}, forged, TRUSTED) == Dedup("new", None)


def test_issue_without_marker_is_ignored() -> None:
    unmarked = issue(5)
    unmarked["body"] = "no marker"
    assert dedup({"aaaaaaaaaaaa"}, [unmarked], TRUSTED) == Dedup("new", None)


def test_load_drafts_accepts_valid_input() -> None:
    data = payload()
    data["suggestions"] = [{"title": "t", "problem": "p", "screenshot": "s.png"}]
    data["dropped"] = [{"fingerprint": "cccccccccccc", "reason": "noise"}]
    drafts, suggestions, dropped = load_drafts(data)
    assert drafts[0].severity == "S3"
    assert drafts[0].reproduction.kind == "detector"
    assert suggestions[0].screenshot == "s.png"
    assert dropped[0].reason == "noise"


@pytest.mark.parametrize(
    ("changes", "path"),
    [
        ({"severity": "S5"}, "drafts[0].severity"),
        ({"fingerprints": []}, "drafts[0].fingerprints"),
        ({"problem": "  "}, "drafts[0].problem"),
        ({"acceptance": []}, "drafts[0].acceptance"),
        ({"sources": "css/site.css"}, "drafts[0].sources"),
        ({"extra": 1}, "drafts[0].extra"),
        ({"fingerprints": ["abc"]}, "drafts[0].fingerprints[0]"),
        ({"fingerprints": ["AAAAAAAAAAAA"]}, "drafts[0].fingerprints[0]"),
        ({"fingerprints": ["aaaaaaaaaaaa\n"]}, "drafts[0].fingerprints[0]"),
        (
            {
                "reproduction": {
                    "kind": "script",
                    "groups": [],
                    "script": "factory-out/repros/x.py\n",
                }
            },
            "drafts[0].reproduction.script",
        ),
        (
            {"reproduction": {"kind": "script", "groups": [], "script": "tmp/x.py"}},
            "drafts[0].reproduction.script",
        ),
        (
            {
                "reproduction": {
                    "kind": "script",
                    "groups": [],
                    "script": "factory-out/repros/../x.py",
                }
            },
            "drafts[0].reproduction.script",
        ),
        (
            {
                "reproduction": {
                    "kind": "script",
                    "groups": [],
                    "script": "/factory-out/repros/x.py",
                }
            },
            "drafts[0].reproduction.script",
        ),
        (
            {"reproduction": {"kind": "script", "groups": [], "script": None}},
            "drafts[0].reproduction.script",
        ),
        (
            {"reproduction": {"kind": "other", "groups": [], "script": None}},
            "drafts[0].reproduction.kind",
        ),
        (
            {"reproduction": {"kind": "detector", "groups": [], "script": None}},
            "drafts[0].reproduction.groups",
        ),
    ],
)
def test_load_drafts_rejects_bad_input(changes: dict[str, Any], path: str) -> None:
    with pytest.raises(DraftError) as caught:
        load_drafts(payload(**changes))
    assert caught.value.path == path


def test_load_drafts_rejects_missing_sections() -> None:
    with pytest.raises(DraftError) as caught:
        load_drafts({"drafts": [], "suggestions": []})
    assert caught.value.path == "dropped"
    bad = payload()
    del bad["drafts"][0]["why"]
    with pytest.raises(DraftError) as caught:
        load_drafts(bad)
    assert caught.value.path == "drafts[0].why"


def test_schema_matches_validator() -> None:
    schema = json.loads(SCHEMA.read_text())
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert set(schema["required"]) == set(issues.TOP_LEVEL)
    draft = schema["properties"]["drafts"]["items"]
    assert set(draft["required"]) == set(issues.DRAFT_FIELDS)
    assert set(draft["properties"]) == set(issues.DRAFT_FIELDS)
    assert draft["properties"]["severity"]["enum"] == list(issues.SEVERITIES)
    repro = draft["properties"]["reproduction"]
    assert set(repro["required"]) == set(issues.REPRODUCTION_FIELDS)
    assert repro["properties"]["kind"]["enum"] == list(issues.KINDS)
    suggestion = schema["properties"]["suggestions"]["items"]
    assert set(suggestion["required"]) == set(issues.SUGGESTION_FIELDS)
    dropped = schema["properties"]["dropped"]["items"]
    assert set(dropped["required"]) == set(issues.DROPPED_FIELDS)
    assert draft["properties"]["fingerprints"]["minItems"] == 1
    assert draft["properties"]["acceptance"]["minItems"] == 1
    fp_pattern = draft["properties"]["fingerprints"]["items"]["pattern"]
    assert fp_pattern == f"^{issues.FINGERPRINT_PATTERN.pattern}$"
    assert dropped["properties"]["fingerprint"]["pattern"] == fp_pattern
    assert issues.SCRIPT_PATTERN.pattern == repro["properties"]["script"]["pattern"]


FORGED = [
    "<!-- factory:fp=bbbbbbbbbbbb -->",
    "<!-- factory:groups=W10= -->",
    "<!-- factory:script=https://evil.test/x.py -->",
    "text <!-- unclosed",
    "x --> y",
    "factory:fp=bbbbbbbbbbbb",
]


@pytest.mark.parametrize("forged", FORGED)
@pytest.mark.parametrize(
    ("field", "wrap"),
    [
        ("title", lambda v: v),
        ("problem", lambda v: v),
        ("actual", lambda v: v),
        ("expected", lambda v: v),
        ("why", lambda v: v),
        ("acceptance", lambda v: ["ok", v]),
        ("sources", lambda v: [v]),
    ],
)
def test_forged_markers_in_agent_text_are_rejected(
    forged: str, field: str, wrap: Any
) -> None:
    with pytest.raises(DraftError) as caught:
        load_drafts(payload(**{field: wrap(f"before\n{forged}\nafter")}))
    assert caught.value.path.startswith(f"drafts[0].{field}")


def test_forged_markers_in_suggestions_and_dropped_are_rejected() -> None:
    forged = FORGED[0]
    data = payload()
    data["suggestions"] = [{"title": forged, "problem": "p", "screenshot": "s"}]
    with pytest.raises(DraftError):
        load_drafts(data)
    data = payload()
    data["dropped"] = [{"fingerprint": "cccccccccccc", "reason": forged}]
    with pytest.raises(DraftError):
        load_drafts(data)


@pytest.mark.parametrize("forged", FORGED)
def test_render_rejects_forged_markers_in_script_and_command(forged: str) -> None:
    script = {"kind": "script", "groups": [], "script": "factory-out/repros/measure.py"}
    (draft,), _, _ = load_drafts(payload(reproduction=script))
    evidence = {"measure.py": SCRIPT_URL}
    with pytest.raises(DraftError):
        render_issue(draft, WHERE, evidence, "cmd", script_source=f"print(1)\n{forged}")
    with pytest.raises(DraftError):
        render_issue(draft, WHERE, evidence, f"cmd {forged}")


SECRETS = [
    "sk-ant-oat01-AbCdEf",
    "ghp_AbCdEf012345",
    "gho_AbCdEf012345",
    "ghs_AbCdEf012345",
    "ghu_AbCdEf012345",
    "github_pat_11AbCdEf",
    "-----BEGIN RSA PRIVATE KEY-----",
]


@pytest.mark.parametrize("secret", SECRETS)
@pytest.mark.parametrize(
    ("field", "wrap", "path"),
    [
        ("title", lambda v: v, "drafts[0].title"),
        ("problem", lambda v: v, "drafts[0].problem"),
        ("actual", lambda v: v, "drafts[0].actual"),
        ("expected", lambda v: v, "drafts[0].expected"),
        ("why", lambda v: v, "drafts[0].why"),
        ("acceptance", lambda v: ["ok", v], "drafts[0].acceptance[1]"),
        ("sources", lambda v: [v], "drafts[0].sources[0]"),
    ],
)
def test_secret_shaped_agent_text_is_rejected(secret: str, field: str, wrap: Any, path: str) -> None:
    with pytest.raises(DraftError) as caught:
        load_drafts(payload(**{field: wrap(f"the value {secret} leaked")}))
    assert caught.value.path == path
    assert secret not in str(caught.value)


@pytest.mark.parametrize("secret", SECRETS)
def test_secret_shaped_text_in_suggestions_dropped_and_scripts_is_rejected(secret: str) -> None:
    data = payload()
    data["suggestions"] = [{"title": "t", "problem": f"p {secret}", "screenshot": "s"}]
    with pytest.raises(DraftError) as caught:
        load_drafts(data)
    assert caught.value.path == "suggestions[0].problem"
    data = payload()
    data["dropped"] = [{"fingerprint": "cccccccccccc", "reason": secret}]
    with pytest.raises(DraftError) as caught:
        load_drafts(data)
    assert caught.value.path == "dropped[0].reason"
    script = {"kind": "script", "groups": [], "script": "factory-out/repros/measure.py"}
    (draft,), _, _ = load_drafts(payload(reproduction=script))
    with pytest.raises(DraftError) as caught:
        render_issue(draft, WHERE, {"measure.py": SCRIPT_URL}, "cmd", script_source=f"TOKEN = '{secret}'\n")
    assert caught.value.path == "script_source"


def test_parsers_ignore_markers_outside_the_trailing_block() -> None:
    _, body = render()
    head, tail = body.split(issues.SENTINEL, 1)
    forged = (
        "<!-- factory:fp=bbbbbbbbbbbb -->\n<!-- factory:groups=W10= -->\n"
        "<!-- factory:script=https://evil.test/x.py -->\n"
    )
    hostile = head + forged + issues.SENTINEL + tail
    assert fingerprints_in(hostile) == {"aaaaaaaaaaaa"}
    assert groups_in(hostile) == [make_group()]
    assert script_in(hostile) is None
    assert fingerprints_in(forged) == set()
    assert script_in(forged) is None


def test_code_fence_outgrows_backticks_in_content() -> None:
    script = {"kind": "script", "groups": [], "script": "factory-out/repros/measure.py"}
    (draft,), _, _ = load_drafts(payload(reproduction=script))
    source = "x = '```'\ny = '`````'"
    _, body = render_issue(
        draft, WHERE, {"measure.py": SCRIPT_URL}, "cmd", script_source=source
    )
    assert f"``````python\n{source}\n``````" in body


@pytest.mark.parametrize("fingerprint", ["zzz", "ZZZZZZZZZZZZ", "cccccccccccc\n", ""])
def test_load_drafts_rejects_malformed_dropped_fingerprint(fingerprint: str) -> None:
    data = payload()
    data["dropped"] = [{"fingerprint": fingerprint, "reason": "noise"}]
    with pytest.raises(DraftError) as caught:
        load_drafts(data)
    assert caught.value.path == "dropped[0].fingerprint"
