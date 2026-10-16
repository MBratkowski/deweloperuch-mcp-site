import dataclasses
import json
import shutil
from pathlib import Path

import pytest

from tools import build_docs, docs_content, html_helpers
from tools.release import BuildError, Release, load_release, pinned_version
from tools.docs_content import Group
from tools.html_helpers import fill_blocks
from tools.docs_renderer import render_docs, render_landing
from tools.check_facts import check_all, parse, read_page_facts, read_written_facts, squash
from tools.sync_server import write

ROOT = Path(__file__).resolve().parent.parent
SITE_FILES = ("index.html", "docs/index.html", "data/demo.json", "data/server")

NEW_TOOL = {
    "name": "search_parcels",
    "title": "Szukaj działek",
    "description": "Znajduje działki budowlane w mieście.",
    "inputSchema": {"type": "object", "properties": {"city": {"type": "string", "description": "Miasto."}}, "required": ["city"]},
}
NEW_PROMPT = {
    "name": "znajdz_dzialke",
    "title": "Znajdź działkę",
    "description": "Działki budowlane w jednym mieście.",
    "arguments": [{"name": "miasto", "description": "Miasto.", "required": True}],
}


def read(root: Path, path: str) -> str:
    return (root / path).read_text(encoding="utf-8")


@pytest.fixture
def saved() -> Release:
    return load_release()


@pytest.fixture
def site(tmp_path: Path) -> Path:
    """A copy of the pages and their data to build in."""
    for path in SITE_FILES:
        source, target = ROOT / path, tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy(source, target)
    return tmp_path


@pytest.fixture
def parcels_group(monkeypatch: pytest.MonkeyPatch) -> None:
    """GROUPS with NEW_TOOL in a row of the offers group, as the hand edit for it would add."""
    offers, *rest = docs_content.GROUPS
    extended = dataclasses.replace(offers, rows=offers.rows + ((("search_parcels",), "Działki budowlane w mieście."),))
    monkeypatch.setattr(docs_content, "GROUPS", (extended, *rest))


def test_committed_pages_are_the_build_output(saved: Release) -> None:
    assert render_docs(saved) == read(ROOT, "docs/index.html")
    assert render_landing(read(ROOT, "index.html"), saved) == read(ROOT, "index.html")


def test_building_twice_changes_nothing(site: Path) -> None:
    build_docs.build(site)
    first = {path: read(site, path) for path in ("index.html", "docs/index.html")}
    build_docs.build(site)
    assert {path: read(site, path) for path in first} == first == {path: read(ROOT, path) for path in first}


@pytest.mark.parametrize("old_version, new_version", [("0.1.2", "0.1.3"), ("0.1.3", "0.2.0"), ("0.2.0", "1.0.0")])
def test_a_version_bump_changes_every_version_on_both_pages(
    site: Path, saved: Release, old_version: str, new_version: str
) -> None:
    previous = dataclasses.replace(saved, version=old_version)
    write(previous, site)
    assert build_docs.main(site) == 0
    before = {path: read(site, path) for path in ("index.html", "docs/index.html")}

    release = dataclasses.replace(previous, version=new_version)
    write(release, site)
    assert load_release(site) == release
    assert build_docs.main(site) == 0
    for path, old in before.items():
        new = read(site, path)
        assert old_version in old and old_version not in new, path
        assert new.replace(new_version, old_version) == old, path
    assert f'<span data-fact="version">v{new_version}</span>' in read(site, "index.html")
    docs = read(site, "docs/index.html")
    assert f"deweloperuch-mcp-{new_version}.mcpb" in docs and f"deweloperuch_mcp-{new_version}-py3-none-any.whl" in docs
    assert check_all(release, root=site) == []


def test_a_release_with_another_tool_and_prompt_changes_every_count(site: Path, saved: Release, parcels_group: None) -> None:
    release = dataclasses.replace(saved, version="0.2.0", tools=saved.tools + (NEW_TOOL,), prompts=saved.prompts + (NEW_PROMPT,))
    write(release, site)
    assert build_docs.main(site) == 0
    index, docs = read(site, "index.html"), read(site, "docs/index.html")

    # tools/check_facts.py reads every marked and written count, version, group and prompt.
    assert check_all(release, root=site) == []
    for path, page in (("index.html", index), ("docs/index.html", docs)):
        old, new = read_written_facts(read(ROOT, path)), read_written_facts(page)
        assert len(new.tool_counts) == len(old.tool_counts) > 0, path
        assert {count for _, count in new.tool_counts} == {14}, path
        assert len(new.prompt_counts) == len(old.prompt_counts), path
        assert {count for _, count in new.prompt_counts} <= {5}, path
    facts = read_page_facts(index)
    assert set(facts.tool_counts) == {14} and facts.prompt_counts == (5,)
    assert facts.group_counts == {"offers": 6, "rcn": 4, "official": 1, "helpers": 3}
    assert "search_parcels" in facts.dane_rows[0]
    assert "Znajdź działkę" in facts.prompt_titles
    text = squash(parse(index).text())
    assert "Dostajesz 14 narzędzi i 5 gotowych promptów." in text
    assert "Pięć gotowych promptów prowadzi model krok po kroku." in text
    assert 'id="tool-search_parcels"' in docs and "<code>znajdz_dzialke</code>" in docs
    assert "deweloperuch-mcp daje im 14 narzędzi" in docs


def test_a_new_tool_stops_the_build_with_its_name(site: Path, saved: Release, capsys: pytest.CaptureFixture[str]) -> None:
    release = dataclasses.replace(saved, version="0.2.0", tools=saved.tools + (NEW_TOOL,))
    with pytest.raises(BuildError, match="search_parcels"):
        render_docs(release)
    with pytest.raises(BuildError, match="search_parcels"):
        render_landing(read(ROOT, "index.html"), release)

    write(release, site)
    assert build_docs.main(site) == 1
    error = capsys.readouterr().err
    assert "deweloperuch-mcp 0.2.0 has tools that no group in tools/docs_content.py GROUPS holds: search_parcels" in error
    assert read(site, "index.html") == read(ROOT, "index.html")
    assert read(site, "docs/index.html") == read(ROOT, "docs/index.html")


def test_a_removed_tool_stops_the_build_with_its_name(saved: Release) -> None:
    release = dataclasses.replace(saved, tools=tuple(t for t in saved.tools if t["name"] != "get_building"))
    with pytest.raises(BuildError, match=rf"GROUPS names tools that deweloperuch-mcp {saved.version} does not have: get_building"):
        render_docs(release)


def test_a_missing_demo_prompt_stops_the_build(saved: Release) -> None:
    release = dataclasses.replace(saved, prompts=tuple(p for p in saved.prompts if p["name"] != docs_content.DEMO_PROMPT))
    with pytest.raises(BuildError, match="DEMO_PROMPT"):
        render_landing(read(ROOT, "index.html"), release)


def test_a_tool_in_two_groups_stops_the_build(saved: Release, monkeypatch: pytest.MonkeyPatch) -> None:
    extra = Group("extra", "Extra", "Extra", "", ((("get_offer",), ""),))
    monkeypatch.setattr(docs_content, "GROUPS", docs_content.GROUPS + (extra,))
    with pytest.raises(BuildError, match="more than once: get_offer"):
        render_docs(saved)


def test_missing_markers_stop_the_build(saved: Release) -> None:
    index = read(ROOT, "index.html").replace("<!-- hero-version:start -->", "")
    with pytest.raises(BuildError, match="hero-version"):
        render_landing(index, saved)


def test_fill_blocks_fills_every_occurrence_and_only_named_blocks() -> None:
    page = "<!-- a:start -->1<!-- a:end --> <!-- demo:start -->x <!-- a:start -->2<!-- a:end --><!-- demo:end -->"
    assert fill_blocks(page, {"a": "9"}) == "<!-- a:start -->9<!-- a:end --> <!-- demo:start -->x <!-- a:start -->9<!-- a:end --><!-- demo:end -->"


@pytest.mark.parametrize(
    "count, tools, prompts, sentence",
    [
        (1, "1 narzędzie", "1 gotowy prompt", "Jeden gotowy prompt prowadzi"),
        (2, "2 narzędzia", "2 gotowe prompty", "Dwa gotowe prompty prowadzą"),
        (4, "4 narzędzia", "4 gotowe prompty", "Cztery gotowe prompty prowadzą"),
        (5, "5 narzędzi", "5 gotowych promptów", "Pięć gotowych promptów prowadzi"),
        (12, "12 narzędzi", "12 gotowych promptów", "Dwanaście gotowych promptów prowadzi"),
        (13, "13 narzędzi", "13 gotowych promptów", "Trzynaście gotowych promptów prowadzi"),
        (22, "22 narzędzia", "22 gotowe prompty", "22 gotowe prompty prowadzą"),
        (25, "25 narzędzi", "25 gotowych promptów", "25 gotowych promptów prowadzi"),
    ],
)
def test_polish_counts(count: int, tools: str, prompts: str, sentence: str) -> None:
    assert html_helpers.tools_phrase(count) == tools
    assert html_helpers.prompts_phrase(count) == prompts
    assert html_helpers.prompts_sentence_start(count) == sentence


def test_marked_counts() -> None:
    assert html_helpers.tools_phrase(13, fact=True) == '<span data-fact="tool-count">13</span> narzędzi'
    assert html_helpers.prompts_phrase(4, fact=True) == '<span data-fact="prompt-count">4</span> gotowe prompty'


def cli_snapshot(site: Path, commands: list[dict], version: str | None = None) -> None:
    (site / "data/server/cli_commands.json").write_text(
        json.dumps({"version": version if version is not None else pinned_version(site), "commands": commands}), encoding="utf-8"
    )


def test_cli_table_comes_from_the_pinned_snapshot(site: Path) -> None:
    cli_snapshot(site, [{
        "name": "reports export",
        "usage": "deweloperuch reports export [OPTIONS] TARGET",
        "description": "Eksportuj <dane> & statystyki.",
    }])
    build_docs.build(site)
    docs = parse(read(site, "docs/index.html"))
    [section] = docs.find_all("section", id="cli")
    text = section.text()
    assert "deweloperuch reports export [OPTIONS] TARGET" in text
    assert "Eksportuj <dane> & statystyki." in text
    assert "locations search" not in text
    assert "&lt;dane&gt; &amp; statystyki." in read(site, "docs/index.html")


def test_cli_command_order_does_not_depend_on_snapshot_order(site: Path) -> None:
    commands = [
        {"name": "reports export", "usage": "deweloperuch reports export", "description": "Eksportuj dane."},
        {"name": "buildings get", "usage": "deweloperuch buildings get SLUG", "description": "Pokaż budynek."},
    ]
    cli_snapshot(site, commands)
    build_docs.build(site)
    first = read(site, "docs/index.html")
    cli_snapshot(site, list(reversed(commands)))
    build_docs.build(site)
    assert read(site, "docs/index.html") == first
    assert first.index("deweloperuch buildings get SLUG") < first.index("deweloperuch reports export")


@pytest.mark.parametrize("problem", ["missing", "version", "duplicate", "empty", "malformed"])
def test_bad_cli_snapshot_stops_before_changing_pages(site: Path, problem: str, capsys: pytest.CaptureFixture[str]) -> None:
    command = {"name": "reports export", "usage": "deweloperuch reports export", "description": "Eksportuj dane."}
    cli_snapshot(site, [command])
    path = site / "data/server/cli_commands.json"
    if problem == "missing":
        path.unlink()
    elif problem == "version":
        cli_snapshot(site, [command], version=pinned_version(site) + ".1")
    elif problem == "duplicate":
        cli_snapshot(site, [command, command])
    elif problem == "empty":
        cli_snapshot(site, [])
    else:
        path.write_text("{", encoding="utf-8")
    before = {name: read(site, name) for name in ("index.html", "docs/index.html")}
    assert build_docs.main(site) == 1
    assert "cli_commands.json" in capsys.readouterr().err
    assert {name: read(site, name) for name in before} == before
