import asyncio
import copy
from concurrent.futures import ThreadPoolExecutor
import dataclasses
import json
from pathlib import Path

import pytest

from tools import check_facts, docs_renderer
from tools.release import load_release
from tools.check_facts import (
    Release,
    check_all,
    check_demo,
    check_docs_build,
    compare,
    compare_pinned,
    compare_snapshots,
    compare_written,
    groups,
    read_page_facts,
    read_release,
    read_written_facts,
)

ROOT = Path(__file__).resolve().parent.parent
ALL_TOOLS = frozenset({
    "search_locations", "search_offers", "get_offer", "search_investments", "get_investment",
    "search_developers", "get_developer", "get_market_statistics", "get_transaction_statistics",
    "rank_locations", "get_building", "estimate_property_value", "get_government_statistics",
})
PROMPT_TITLES = frozenset({"Znajdź mieszkanie", "Oceń ofertę", "Prześwietl dewelopera", "Porównaj miasta"})


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def in_thread(function):
    """Run ``function`` in a worker thread: the sync Playwright fixtures keep an event loop
    running in the main thread, where ``asyncio.run`` refuses to start."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(function).result()


@pytest.fixture(scope="module")
def index_html() -> str:
    return read("index.html")


@pytest.fixture(scope="module")
def tools_json() -> dict:
    return json.loads(read("data/server/mcp_tools.json"))


@pytest.fixture(scope="module")
def prompts_json() -> dict:
    return json.loads(read("data/server/mcp_prompts.json"))


@pytest.fixture
def release() -> Release:
    """The saved listing (data/server/*.json) as the release the pages show."""
    return load_release()


def with_tools(release: Release, *, add: tuple[str, ...] = (), remove: tuple[str, ...] = ()) -> Release:
    tools = tuple(t for t in release.tools if t["name"] not in remove)
    tools += tuple({"name": name, "description": "", "inputSchema": {"type": "object"}} for name in add)
    return dataclasses.replace(release, tools=tools)


def test_saved_listing_is_the_known_one(release: Release) -> None:
    assert release.tool_names == ALL_TOOLS
    assert release.prompt_titles == PROMPT_TITLES


def test_pages_match_the_saved_listing(release: Release) -> None:
    assert check_all(release) == []


def test_groups_cover_tools_exactly() -> None:
    assert frozenset().union(*groups().values()) == ALL_TOOLS
    assert sum(len(names) for names in groups().values()) == len(ALL_TOOLS)


def test_compare_pinned_names_the_commands(release: Release) -> None:
    assert compare_pinned("0.1.2", release) == []
    [message] = compare_pinned("0.1.2", dataclasses.replace(release, version="0.1.3"))
    assert "pins deweloperuch-mcp 0.1.2, but the release is 0.1.3" in message
    assert "tools.sync_server 0.1.3" in message and "tools/build_docs.py" in message


def test_check_all_puts_a_new_release_first(release: Release) -> None:
    messages = check_all(dataclasses.replace(release, version="0.1.3"))
    assert messages[0].startswith("data/server/version.json pins")


def test_page_facts_are_read_from_the_markers(index_html: str) -> None:
    page = read_page_facts(index_html)
    assert page.tool_counts and set(page.tool_counts) == {13}
    assert page.prompt_counts == (4,)
    assert page.group_counts == {"offers": 5, "rcn": 4, "official": 1, "helpers": 3}
    assert frozenset(page.prompt_titles) == PROMPT_TITLES
    assert frozenset().union(*page.dane_rows) == ALL_TOOLS


def test_compare_reports_version(index_html: str, release: Release) -> None:
    messages = compare(read_page_facts(index_html), dataclasses.replace(release, version="0.9.9"))
    assert any("0.9.9" in m for m in messages)


def test_compare_names_new_tool(index_html: str, release: Release) -> None:
    messages = compare(read_page_facts(index_html), with_tools(release, add=("search_parcels",)))
    assert any("search_parcels" in m and "no group" in m for m in messages)
    assert any("search_parcels" in m and "Dane section misses" in m for m in messages)
    assert any("tool-count 13" in m and "14 tools" in m for m in messages)


def test_compare_names_removed_tool(index_html: str, release: Release) -> None:
    messages = compare(read_page_facts(index_html), with_tools(release, remove=("get_building",)))
    assert any("get_building" in m and "does not have" in m for m in messages)
    assert any("group-count rcn is 4" in m for m in messages)


def test_compare_names_renamed_prompt(index_html: str, release: Release) -> None:
    prompts = tuple({**p, "title": "Oceń cenę"} if p.get("title") == "Oceń ofertę" else p for p in release.prompts)
    messages = compare(read_page_facts(index_html), dataclasses.replace(release, prompts=prompts))
    assert any("Oceń ofertę" in m and "does not have" in m for m in messages)
    assert any("Oceń cenę" in m and "misses" in m for m in messages)
    assert any("persona card" in m and "Oceń ofertę" in m for m in messages)


def test_compare_names_a_tool_in_the_wrong_dane_row(index_html: str, release: Release) -> None:
    swapped = index_html.replace(">get_offer<", ">@@<").replace(">get_building<", ">get_offer<").replace(">@@<", ">get_building<")
    messages = compare(read_page_facts(swapped), release)
    assert any("offers lists get_building" in m for m in messages)
    assert any("rcn lists get_offer" in m for m in messages)


def test_compare_reports_missing_markers(release: Release) -> None:
    messages = compare(read_page_facts("<html><body></body></html>"), release)
    assert any('no data-fact="version"' in m for m in messages)
    assert any('no data-fact="tool-count"' in m for m in messages)
    assert any('group-count" for group helpers' in m for m in messages)


def test_written_counts_and_versions(release: Release) -> None:
    html = (
        "<p>Dostajesz 14 narzędzi i Pięć gotowych promptów.</p>"
        "<p>Pobierz deweloperuch-mcp-0.1.0.mcpb albo uvx deweloperuch-mcp==0.1.0.</p>"
        "<p>Parametry (13), krok 4, cztery kroki.</p>"
    )
    written = read_written_facts(html)
    assert written.tool_counts == (("14 narzędzi", 14),)
    assert written.prompt_counts == (("Pięć gotowych promptów", 5),)
    assert [v for _, v in written.versions] == ["0.1.0", "0.1.0"]
    messages = compare_written("page", written, release)
    assert len(messages) == 4
    assert any("'14 narzędzi'" in m and "13 tools" in m for m in messages)


def test_written_facts_ignore_scripts(release: Release) -> None:
    written = read_written_facts("<script>const s = '99 narzędzi';</script><p>13 narzędzi</p>")
    assert compare_written("page", written, release) == []


def test_compare_snapshots_names_changed_and_new_entries(tools_json: dict, prompts_json: dict, release: Release) -> None:
    tools = copy.deepcopy(list(release.tools))
    tools[0]["description"] += " Zmiana."
    prompts = tuple(p for p in release.prompts if p["name"] != "porownaj_miasta")
    changed = with_tools(dataclasses.replace(release, tools=tuple(tools), prompts=prompts), add=("search_parcels",))
    messages = compare_snapshots(tools_json, prompts_json, changed)
    assert any(f"tool {tools[0]['name']} differs" in m and "description" in m for m in messages)
    assert any("misses the tool search_parcels" in m for m in messages)
    assert any("prompt porownaj_miasta" in m and "does not have" in m for m in messages)


def test_compare_snapshots_ignores_fields_the_docs_do_not_show(tools_json: dict, prompts_json: dict, release: Release) -> None:
    tools = tuple({**t, "annotations": {"readOnlyHint": False}, "_meta": {"x": 1}} for t in release.tools)
    assert compare_snapshots(tools_json, prompts_json, dataclasses.replace(release, tools=tools)) == []


def test_docs_build_detects_stale_output(index_html: str, release: Release) -> None:
    docs_html = read("docs/index.html")
    assert check_docs_build(docs_html, index_html, release) == []
    assert check_docs_build(docs_html.replace("13 narzędzi", "12 narzędzi", 1), index_html, release)
    stale = index_html.replace(docs_renderer.AGENT_BLOCK, docs_renderer.AGENT_BLOCK.replace("Na skróty", "Skrót"))
    assert stale != index_html
    assert any("generated blocks" in m for m in check_docs_build(docs_html, stale, release))
    hand_edited = index_html.replace(">v0.1.2<", ">v0.1.3<")
    assert hand_edited != index_html
    assert any("generated blocks" in m for m in check_docs_build(docs_html, hand_edited, release))


def test_docs_build_reports_what_stops_the_build(index_html: str, release: Release) -> None:
    messages = check_docs_build(read("docs/index.html"), index_html, with_tools(release, add=("search_parcels",)))
    assert any("tools/build_docs.py stops" in m and "search_parcels" in m for m in messages)


def test_compare_snapshots_reports_changed_instructions(tools_json: dict, prompts_json: dict, release: Release) -> None:
    changed = dataclasses.replace(release, instructions=release.instructions + " Nowe zdanie.")
    assert any("server instructions differ" in m for m in compare_snapshots(tools_json, prompts_json, changed))


def test_demo_matches_its_data(index_html: str) -> None:
    assert check_demo(index_html, json.loads(read("data/demo.json"))) == []


def test_check_all_detects_changed_cli_metadata(release: Release) -> None:
    changed = dataclasses.replace(release, cli_commands=tuple(
        {**command, "description": "Nowy opis komendy."} if command["name"] == "offers search" else command
        for command in release.cli_commands
    ))
    messages = check_all(changed)
    assert any("cli_commands.json" in message and "offers search" in message and "description" in message for message in messages)


def test_check_all_detects_added_and_removed_cli_commands(release: Release) -> None:
    changed = dataclasses.replace(release, cli_commands=tuple(
        command for command in release.cli_commands if command["name"] != "skill show"
    ) + ({"name": "reports export", "usage": "deweloperuch reports export", "description": "Eksportuj dane."},))
    messages = check_all(changed)
    assert any("cli_commands.json" in message and "reports export" in message and "misses" in message for message in messages)
    assert any("cli_commands.json" in message and "skill show" in message and "does not have" in message for message in messages)


@pytest.mark.parametrize(
    "change, expected",
    [
        (lambda d: d["rows"][0].update(price=475300.0), "table row 1"),
        (lambda d: d["rows"].pop(), "the table has"),
        (lambda d: d.update(captured_at="2020-01-31"), "capture date 31.01.2020"),
        (lambda d: d.update(question="Inne pytanie."), "the question"),
        (lambda d: d["inspector_args"].update(area_max=70), "the arguments"),
        (lambda d: d["result"].update(total=d["result"]["total"] + 1), "result total"),
        (lambda d: d["result"].update(list_url="https://example.invalid/x"), "list_url"),
        (lambda d: d.update(city_locative="w Krakowie"), "window title"),
    ],
)
def test_demo_reports_each_difference(index_html: str, change, expected: str) -> None:
    demo = json.loads(read("data/demo.json"))
    change(demo)
    messages = check_demo(index_html, demo)
    assert any(expected in m for m in messages), messages


def test_main_exit_codes(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], release: Release) -> None:
    async def fake_release() -> Release:
        return release

    monkeypatch.setattr(check_facts, "read_release", fake_release)
    assert in_thread(check_facts.main) == 0
    assert "OK" in capsys.readouterr().out

    async def newer_release() -> Release:
        return dataclasses.replace(release, version="9.9.9")

    monkeypatch.setattr(check_facts, "read_release", newer_release)
    assert in_thread(check_facts.main) == 1
    assert "9.9.9" in capsys.readouterr().out


@pytest.mark.network
def test_live_release_matches_the_pages() -> None:
    assert check_all(in_thread(lambda: asyncio.run(read_release()))) == []
