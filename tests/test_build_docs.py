import dataclasses
import json
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

from tools import build_docs, docs_content, html_helpers
from tools.release import BuildError, Release, load_release, pinned_version
from tools.docs_content import Group
from tools.docs_renderer import render_docs, render_landing
from tools.check_facts import check_all, parse, read_page_facts, read_written_facts, squash
from tools.sync_server import write

ROOT = Path(__file__).resolve().parent.parent
SITE_FILES = ("index.html", "docs/index.html", "data/demo.json", "data/server", "tools/templates")

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
    assert render_landing(read(ROOT, "tools/templates/landing.html"), saved, json.loads(read(ROOT, "data/demo.json"))) == read(ROOT, "index.html")


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
        render_landing(read(ROOT, "tools/templates/landing.html"), release, json.loads(read(ROOT, "data/demo.json")))

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
        render_landing(read(ROOT, "tools/templates/landing.html"), release, json.loads(read(ROOT, "data/demo.json")))


def test_a_tool_in_two_groups_stops_the_build(saved: Release, monkeypatch: pytest.MonkeyPatch) -> None:
    extra = Group("extra", "Extra", "Extra", "", ((("get_offer",), ""),))
    monkeypatch.setattr(docs_content, "GROUPS", docs_content.GROUPS + (extra,))
    with pytest.raises(BuildError, match="more than once: get_offer"):
        render_docs(saved)


@pytest.mark.parametrize(
    "change, problem",
    [
        (lambda page: page.replace("${hero_version}", ""), "missing.*hero_version"),
        (lambda page: page.replace("${hero_version}", "${hero_version}${hero_version}"), "duplicate.*hero_version"),
        (lambda page: page + "${typo}", "unknown.*typo"),
        (lambda page: page + "${}", "invalid placeholder"),
    ],
)
def test_invalid_landing_slots_stop_before_changing_pages(
    site: Path, change, problem: str, capsys: pytest.CaptureFixture[str]
) -> None:
    template = site / "tools/templates/landing.html"
    template.write_text(change(template.read_text(encoding="utf-8")), encoding="utf-8")
    before = {name: read(site, name) for name in ("index.html", "docs/index.html")}
    with pytest.raises(BuildError, match=problem):
        build_docs.build(site)
    assert build_docs.main(site) == 1
    assert "tools/templates/landing.html" in capsys.readouterr().err
    assert {name: read(site, name) for name in before} == before


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


def test_build_recreates_both_pages_without_existing_output(site: Path) -> None:
    (site / "index.html").unlink()
    (site / "docs/index.html").unlink()
    build_docs.build(site)
    assert read(site, "index.html") == read(ROOT, "index.html")
    assert read(site, "docs/index.html") == read(ROOT, "docs/index.html")


def test_build_discards_edits_to_published_static_content(site: Path) -> None:
    page = read(site, "index.html")
    assert "Nieoficjalny projekt społeczności" in page
    (site / "index.html").write_text(page.replace("Nieoficjalny projekt społeczności", "Edited output"))
    build_docs.build(site)
    assert read(site, "index.html") == read(ROOT, "index.html")


def test_one_build_renders_the_saved_demo(site: Path) -> None:
    demo = json.loads(read(site, "data/demo.json"))
    demo["question"] = "Zapisane pytanie bez nowego pobierania danych."
    demo["rows"][0]["price"] = 123456.0
    (site / "data/demo.json").write_text(json.dumps(demo), encoding="utf-8")
    build_docs.build(site)
    page = read(site, "index.html")
    assert demo["question"] in page
    assert "123 456 zł" in page


def test_source_template_edits_reach_both_outputs(site: Path) -> None:
    for name in ("landing", "docs"):
        template = site / f"tools/templates/{name}.html"
        template.write_text(template.read_text(encoding="utf-8").replace("</main>", "<p>Source addition</p></main>"), encoding="utf-8")
    build_docs.build(site)
    assert "<p>Source addition</p>" in read(site, "index.html")
    assert "<p>Source addition</p>" in read(site, "docs/index.html")


@pytest.mark.parametrize("contents", [None, "{", "[]"])
def test_missing_or_malformed_saved_demo_preserves_outputs(
    site: Path, contents: str | None, capsys: pytest.CaptureFixture[str]
) -> None:
    path = site / "data/demo.json"
    if contents is None:
        path.unlink()
    else:
        path.write_text(contents, encoding="utf-8")
    before = {name: read(site, name) for name in ("index.html", "docs/index.html")}
    assert build_docs.main(site) == 1
    assert "data/demo.json" in capsys.readouterr().err
    assert {name: read(site, name) for name in before} == before


def test_build_is_offline_and_never_starts_capture_or_sync(site: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args, **kwargs):
        pytest.fail("The page build must not use sockets or subprocesses")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    build_docs.build(site)
    assert read(site, "index.html") == read(ROOT, "index.html")
    assert read(site, "docs/index.html") == read(ROOT, "docs/index.html")


@pytest.mark.parametrize("missing", [True, False], ids=["omitted", "null"])
def test_optional_mcp_display_metadata_is_normalized(site: Path, saved: Release, missing: bool) -> None:
    tools = json.loads(json.dumps(saved.tools))
    prompts = json.loads(json.dumps(saved.prompts))
    tool, prompt = tools[0], prompts[0]
    parameter = next(iter(tool["inputSchema"]["properties"].values()))
    fields = [(tool, "title"), (tool, "description"), (parameter, "description"),
              (prompt, "title"), (prompt, "description"), (prompt["arguments"][0], "description")]
    for record, key in fields:
        if missing:
            record.pop(key, None)
        else:
            record[key] = None
    release = dataclasses.replace(saved, tools=tuple(tools), prompts=tuple(prompts))
    docs = render_docs(release)
    assert f'<h4>{tool["name"]}</h4>' in docs
    assert f'<h3>{prompt["name"]}</h3>' in docs
    assert "None" not in docs
    assert f'data-prompt="{prompt["name"]}"' in render_landing(read(ROOT, "tools/templates/landing.html"), release, json.loads(read(ROOT, "data/demo.json")))
    write(release, site)
    assert load_release(site) == release
    assert build_docs.main(site) == 0
    assert read(site, "docs/index.html") == docs


@pytest.mark.parametrize("missing", [True, False], ids=["omitted", "null"])
def test_optional_prompt_arguments_render_an_empty_table(saved: Release, missing: bool) -> None:
    prompt = dict(saved.prompts[0])
    if missing:
        prompt.pop("arguments")
    else:
        prompt["arguments"] = None
    release = dataclasses.replace(saved, prompts=(prompt, *saved.prompts[1:]))
    docs = parse(render_docs(release))
    [section] = docs.find_all("section", id="prompty")
    [card, *_] = section.find_all("article")
    assert card.find_all("tbody")[0].text() == ""


@pytest.mark.parametrize("schema, expected", [
    ({"type": "string"}, "tekst"),
    ({"type": "integer"}, "liczba"),
    ({"type": "number"}, "liczba"),
    ({"type": "boolean"}, "tak/nie"),
    ({"type": "array"}, "lista"),
    ({"type": "object"}, "object"),
    ({"type": "null"}, "—"),
    ({}, "—"),
    ({"type": ["string", "null"]}, "tekst"),
    ({"type": ["null", "integer", "boolean"]}, "liczba albo tak/nie"),
    ({"anyOf": [{"type": ["string", "null"]}, {"type": "array"}]}, "tekst albo lista"),
    ({"anyOf": [{"anyOf": [{"type": "number"}, {"type": "null"}]}, {"type": "boolean"}]}, "liczba albo tak/nie"),
])
def test_json_schema_type_labels(schema: dict, expected: str) -> None:
    from tools.docs_renderer import schema_type

    assert schema_type(schema) == expected


@pytest.mark.parametrize("snapshot, path, value, field", [
    ("mcp_tools.json", ("tools", 0, "description"), 42, "description"),
    ("mcp_tools.json", ("tools", 0, "name"), None, "name"),
    ("mcp_tools.json", ("tools", 0, "inputSchema"), None, "inputSchema"),
    ("mcp_tools.json", ("tools", 0, "inputSchema", "properties"), [], "properties"),
    ("mcp_tools.json", ("tools", 0, "inputSchema", "properties", "city"), None, "city"),
    ("mcp_tools.json", ("tools", 0, "inputSchema", "properties", "city"), {"type": ["string", []]}, "city.type"),
    ("mcp_tools.json", ("tools", 0, "inputSchema", "properties", "city"), {"type": "date"}, "city.type"),
    ("mcp_tools.json", ("tools", 0, "inputSchema", "properties", "city"), {"type": []}, "city.type"),
    ("mcp_tools.json", ("tools", 0, "inputSchema", "properties", "city"), {"type": ["string", "string"]}, "city.type"),
    ("mcp_tools.json", ("tools", 0, "inputSchema", "properties", "city"), {"anyOf": {}}, "city.anyOf"),
    ("mcp_tools.json", ("tools", 0, "inputSchema", "properties", "city"), {"anyOf": [None]}, "city.anyOf"),
    ("mcp_tools.json", ("tools", 0, "inputSchema", "properties", "city"), {"$ref": "#/$defs/City"}, "city"),
    ("mcp_tools.json", ("tools", 0, "inputSchema", "required"), "city", "required"),
    ("mcp_prompts.json", ("prompts", 0, "title"), [], "title"),
    ("mcp_prompts.json", ("prompts", 0, "description"), False, "description"),
    ("mcp_prompts.json", ("prompts", 0, "arguments"), {}, "arguments"),
    ("mcp_prompts.json", ("prompts", 0, "arguments", 0, "description"), {}, "description"),
    ("mcp_prompts.json", ("prompts", 0, "arguments", 0, "required"), "yes", "required"),
    ("mcp_tools.json", ("tools",), None, "tools"),
    ("mcp_prompts.json", ("prompts",), [None], "prompts"),
])
def test_bad_mcp_metadata_stops_before_changing_pages(
    site: Path, snapshot: str, path: tuple, value: object, field: str, capsys: pytest.CaptureFixture[str],
) -> None:
    source = site / "data/server" / snapshot
    metadata = json.loads(source.read_text(encoding="utf-8"))
    record = metadata
    for key in path[:-1]:
        record = record[key]
    record[path[-1]] = value
    source.write_text(json.dumps(metadata), encoding="utf-8")
    before = {name: read(site, name) for name in ("index.html", "docs/index.html")}
    assert build_docs.main(site) == 1
    error = capsys.readouterr().err
    assert snapshot in error and field in error
    assert "sync_server" in error or "support" in error
    assert {name: read(site, name) for name in before} == before


def test_shared_commands_update_both_pages_and_copy_payloads(saved: Release, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import page_config

    monkeypatch.setattr(page_config, "COMMANDS", dataclasses.replace(
        page_config.COMMANDS,
        server_command="uvx-next", server_args=("new-server",),
        install_skill='npx skills add "owner/new&skill"',
        cli_help="uvx --from new-cli cli --help",
    ))
    pages = build_docs.render_pages(saved)
    expected = {
        'claude mcp add deweloperuch -- uvx-next new-server',
        'npx skills add "owner/new&skill"',
        'uvx --from new-cli cli --help',
    }
    for path, html in pages.items():
        page = parse(html)
        commands = {button.attrs["data-command"] for button in page.find_all("button", class_="copy")}
        assert expected <= commands, path
        # Each copy target must match the rendered text, including escaped punctuation.
        for row in page.iter():
            buttons = [child for child in row.children if not isinstance(child, str) and child.tag == "button" and "copy" in child.attrs.get("class", "").split()]
            if buttons:
                [text] = row.find_all(class_="cmd-text")
                assert text.text() == buttons[0].attrs["data-command"], path
        [agent] = page.find_all("button", data_umami_event="copy-agent-prompt" if path == "index.html" else "docs-copy-agent-prompt")
        assert "uvx-next new-server --help" in agent.attrs["data-command"]
        assert '"command": "uvx-next"' in agent.attrs["data-command"]
    assert "uvx-next new-server" in parse(pages["docs/index.html"]).text()
    [config] = parse(pages["docs/index.html"]).find_all("button", data_umami_event="docs-copy-config-json")
    assert json.loads(config.attrs["data-command"])["mcpServers"]["deweloperuch"] == {
        "command": "uvx-next", "args": ["new-server"],
    }


def test_shared_site_settings_reach_head_navigation_footer_and_install_steps(
    saved: Release, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools import page_config

    monkeypatch.setattr(page_config, "SITE", dataclasses.replace(
        page_config.SITE,
        base_url="https://example.invalid/nested/site/",
        repository_url="https://code.invalid/owner/server",
        pypi_url="https://packages.invalid/project/server/",
        analytics_script_url="https://analytics.invalid/script.js",
        analytics_website_id="new-website-id", analytics_domains="example.invalid",
    ))
    for path, html in build_docs.render_pages(saved).items():
        page = parse(html)
        expected_url = "https://example.invalid/nested/site/" + ("docs/" if path.startswith("docs/") else "")
        assert page.find_all("link", rel="canonical")[0].attrs["href"] == expected_url
        assert page.find_all("meta", property="og:url")[0].attrs["content"] == expected_url
        assert page.find_all("meta", property="og:image")[0].attrs["content"] == "https://example.invalid/nested/site/assets/og.png"
        [analytics] = page.find_all("script", src="https://analytics.invalid/script.js")
        assert analytics.attrs["data-website-id"] == "new-website-id"
        assert analytics.attrs["data-domains"] == "example.invalid"
        [header], [footer] = page.find_all("header"), page.find_all("footer")
        assert header.find_all("a", href="https://code.invalid/owner/server")
        assert header.find_all("a", href="https://code.invalid/owner/server/releases/latest")
        assert footer.find_all("a", href="https://code.invalid/owner/server")
        assert footer.find_all("a", href="https://packages.invalid/project/server/")
        assert "https://github.com/MBratkowski/deweloperuch-mcp" not in html
        assert "https://pypi.org/project/deweloperuch-mcp/" not in html
        if path == "docs/index.html":
            assert page.find_all("a", href="https://code.invalid/owner/server/releases/latest", data_umami_event="docs-open-release")
        else:
            [script] = page.find_all("script", type="application/ld+json")
            metadata = json.loads("".join(script.children))
            assert metadata["url"] == expected_url
            assert metadata["sameAs"] == ["https://code.invalid/owner/server", "https://packages.invalid/project/server/"]


@pytest.mark.parametrize("fragment", ["head", "nav_brand", "nav_toggle", "nav_actions", "footer_notice"])
def test_shared_fragment_edits_reach_both_pages(site: Path, fragment: str) -> None:
    source = site / f"tools/templates/shared/{fragment}.html"
    marker = f"<!-- edited shared {fragment} -->"
    source.write_text(source.read_text(encoding="utf-8") + marker, encoding="utf-8")
    build_docs.build(site)
    for path in ("index.html", "docs/index.html"):
        assert read(site, path).count(marker) == 1


@pytest.mark.parametrize("problem", ["missing", "unknown-slot"])
def test_bad_shared_fragment_preserves_both_outputs(site: Path, problem: str) -> None:
    source = site / "tools/templates/shared/head.html"
    if problem == "missing":
        source.unlink()
    else:
        source.write_text(source.read_text(encoding="utf-8") + "${typo}", encoding="utf-8")
    before = {path: read(site, path) for path in ("index.html", "docs/index.html")}
    with pytest.raises(BuildError, match="tools/templates/shared/head.html"):
        build_docs.build(site)
    assert {path: read(site, path) for path in before} == before


def test_page_variants_preserve_canonical_asset_paths_and_analytics(saved: Release) -> None:
    pages = build_docs.render_pages(saved)
    expected_events = {
        "index.html": ['nav-github', 'nav-download', 'cta-install', 'copy-hero', 'card-guide', 'copy-claude', 'copy-skill', 'copy-cli', 'copy-agent-prompt', 'cta-guide', 'plan-github'],
        "docs/index.html": ['docs-nav-download', 'docs-open-release', 'docs-copy-agent-prompt', 'docs-copy-curl-lssf-https-astral-sh-uv-install-sh-sh', 'docs-copy-brew-install-uv', 'docs-copy-powershell-executionpolicy-bypass-c-irm-https-as', 'docs-copy-uvx-version', 'docs-copy-claude-mcp-add-deweloperuch-uvx-deweloperuch-mcp', 'docs-copy-config-json', 'docs-copy-npx-skills-add-mbratkowski-deweloperuch-mcp', 'docs-copy-uvx-from-deweloperuch-mcp-deweloperuch-skill-ins', 'docs-copy-uvx-from-deweloperuch-mcp-deweloperuch-help', 'docs-copy-uv-tool-install-deweloperuch-mcp', 'docs-copy-uvx-from-deweloperuch-mcp-deweloperuch-stop-stat', 'docs-copy-uvx-from-deweloperuch-mcp-deweloperuch-stop-clea'],
    }
    for path, html in pages.items():
        page = parse(html)
        prefix = "../" if path.startswith("docs/") else ""
        canonical = "https://mbratkowski.github.io/deweloperuch-mcp-site/" + ("docs/" if prefix else "")
        assert page.find_all("link", rel="canonical")[0].attrs["href"] == canonical
        for asset in ("favicon.png", "apple-touch-icon.png", "css/site.css", "css/docs.css" if prefix else "css/landing.css"):
            assert page.find_all("link", href=prefix + asset)
        assert page.find_all("script", src=prefix + "js/site.js")
        assert page.find_all("img", src=prefix + "assets/icon/icon-light.png")
        assert [node.attrs["data-umami-event"] for node in page.find_all(data_umami_event=None)] == expected_events[path]
