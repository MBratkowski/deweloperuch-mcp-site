"""Check the landing page and the docs page against the released deweloperuch-mcp.

    uv run python -m tools.check_facts

Reads the latest version from PyPI, starts that release over stdio
(``uvx deweloperuch-mcp==<version>``) and reads ``tools/list`` and ``prompts/list``.
Listing calls no tool, so nothing reaches deweloperuch.pl: the server's session only
constructs its HTTP client (checked in the server code of 0.1.1). Then it compares:

- the version pinned in data/server/version.json with the release, so a new release
  fails with the commands that take the pages to it;
- the facts marked in index.html (``data-fact``, ``data-group``, ``data-prompt``), the
  tool names in the Dane section and the persona prompts with the release;
- counts and versions written in the copy of both pages ("13 narzędzi",
  "deweloperuch-mcp-0.1.2.mcpb") with the release;
- data/server/*.json, the source of the generated parts, with the release;
- docs/index.html and the generated blocks of index.html with tools/build_docs.py output;
- the demo block of index.html with data/demo.json.

Exit code 0 when everything matches; otherwise 1, with one line per mismatch.
"""

import asyncio
import json
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen

from tools import build_docs
from tools.build_docs import Release

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = build_docs.PACKAGE
PYPI_URL = f"https://pypi.org/pypi/{PACKAGE}/json"


def groups() -> dict[str, frozenset[str]]:
    """The tool groups of tools/build_docs.py by key."""
    return {group.key: frozenset(group.tools) for group in build_docs.GROUPS}


def dane_rows() -> tuple[tuple[str, ...], ...]:
    """The groups of each list of the Dane section, in page order."""
    return tuple(keys for _, keys in build_docs.DANE_LISTS)


# What the docs page shows of a tool or a prompt; other listing fields may come and go
# with MCP SDK versions without changing the page.
TOOL_FIELDS = ("name", "title", "description", "inputSchema")
PROMPT_FIELDS = ("name", "title", "description", "arguments")


# A small DOM: html.parser events folded into a tree, enough to read the marked facts.

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


@dataclass
class Node:
    tag: str
    attrs: dict[str, str]
    children: list["Node | str"]

    def iter(self) -> Iterator["Node"]:
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.iter()

    def find_all(self, tag: str | None = None, **attrs: str | None) -> list["Node"]:
        """Descendants (and self) with ``tag`` whose attributes match: ``data_fact="version"``
        for ``data-fact``, ``class_`` for one of the classes, ``None`` for any value."""
        return [node for node in self.iter() if (tag is None or node.tag == tag) and node._matches(attrs)]

    def _matches(self, attrs: dict[str, str | None]) -> bool:
        for key, value in attrs.items():
            name = key.rstrip("_").replace("_", "-")
            if name not in self.attrs:
                return False
            if value is not None and value not in (self.attrs[name].split() if name == "class" else [self.attrs[name]]):
                return False
        return True

    def text(self) -> str:
        if self.tag in ("script", "style"):
            return ""
        return "".join(child if isinstance(child, str) else child.text() for child in self.children)


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#document", {}, [])
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag, {name: value or "" for name, value in attrs}, [])
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.stack[-1].children.append(Node(tag, {name: value or "" for name, value in attrs}, []))

    def handle_endtag(self, tag: str) -> None:
        for depth in range(len(self.stack) - 1, 0, -1):
            if self.stack[depth].tag == tag:
                del self.stack[depth:]
                return

    def handle_data(self, data: str) -> None:
        self.stack[-1].children.append(data)


def parse(html: str) -> Node:
    builder = _TreeBuilder()
    builder.feed(html)
    builder.close()
    return builder.root


def squash(text: str) -> str:
    return " ".join(text.split())


# Facts written in the copy rather than marked.

NUMBER_WORDS = {
    "jeden": 1, "jedno": 1, "jedna": 1, "dwa": 2, "dwie": 2, "trzy": 3, "cztery": 4, "pięć": 5,
    "sześć": 6, "siedem": 7, "osiem": 8, "dziewięć": 9, "dziesięć": 10, "jedenaście": 11,
    "dwanaście": 12, "trzynaście": 13, "czternaście": 14, "piętnaście": 15, "szesnaście": 16,
}
_NUMBER = r"(\d+|" + "|".join(NUMBER_WORDS) + r")"
WRITTEN_TOOL_COUNT = re.compile(_NUMBER + r"\s+narzędz(?:i|ia|ie)\b", re.IGNORECASE)
WRITTEN_PROMPT_COUNT = re.compile(_NUMBER + r"\s+(?:gotow\w+\s+)?prompt(?:y|ów)?\b", re.IGNORECASE)
WRITTEN_VERSION = re.compile(r"\bdeweloperuch[-_]mcp(?:-|==)(\d+(?:\.\d+)+)")


def _number(word: str) -> int:
    return int(word) if word.isdigit() else NUMBER_WORDS[word.lower()]


@dataclass(frozen=True)
class WrittenFacts:
    """Counts and versions written in a page's copy, as (phrase, value) pairs."""

    tool_counts: tuple[tuple[str, int], ...]
    prompt_counts: tuple[tuple[str, int], ...]
    versions: tuple[tuple[str, str], ...]


def read_written_facts(html: str) -> WrittenFacts:
    text = squash(parse(html).text())
    return WrittenFacts(
        tool_counts=tuple((m.group(0), _number(m.group(1))) for m in WRITTEN_TOOL_COUNT.finditer(text)),
        prompt_counts=tuple((m.group(0), _number(m.group(1))) for m in WRITTEN_PROMPT_COUNT.finditer(text)),
        versions=tuple((m.group(0), m.group(1)) for m in WRITTEN_VERSION.finditer(text)),
    )


def compare_written(where: str, written: WrittenFacts, release: Release) -> list[str]:
    problems = []
    for phrase, count in written.tool_counts:
        if count != len(release.tools):
            problems.append(f"{where}: '{phrase}' but {PACKAGE} {release.version} has {len(release.tools)} tools")
    for phrase, count in written.prompt_counts:
        if count != len(release.prompts):
            problems.append(f"{where}: '{phrase}' but {PACKAGE} {release.version} has {len(release.prompts)} prompts")
    for phrase, version in written.versions:
        if version != release.version:
            problems.append(f"{where}: '{phrase}' but the released version is {release.version}")
    return problems


# The facts marked on the landing page.


@dataclass(frozen=True)
class PageFacts:
    versions: tuple[str, ...]  # every data-fact="version", without the leading "v"
    tool_counts: tuple[int, ...]  # every data-fact="tool-count"
    prompt_counts: tuple[int, ...]  # every data-fact="prompt-count"
    group_counts: dict[str, int]  # data-fact="group-count" by data-group
    prompt_titles: tuple[str, ...]  # the text of every data-prompt element
    persona_prompts: tuple[str, ...]  # "prompt: <title>" on the persona cards
    dane_rows: tuple[frozenset[str], ...]  # the tool names of each row of the Dane section


def _int(text: str) -> int:
    try:
        return int(text.strip())
    except ValueError:
        return -1


def read_page_facts(html: str) -> PageFacts:
    root = parse(html)
    dane = root.find_all("section", id="dane")
    personas = root.find_all("section", id="dla-kogo")
    return PageFacts(
        versions=tuple(n.text().strip().removeprefix("v") for n in root.find_all(data_fact="version")),
        tool_counts=tuple(_int(n.text()) for n in root.find_all(data_fact="tool-count")),
        prompt_counts=tuple(_int(n.text()) for n in root.find_all(data_fact="prompt-count")),
        group_counts={n.attrs.get("data-group", ""): _int(n.text()) for n in root.find_all(data_fact="group-count")},
        prompt_titles=tuple(squash(n.text()) for n in root.find_all(data_prompt=None)),
        persona_prompts=tuple(
            squash(p.text()).removeprefix("prompt:").strip()
            for section in personas
            for p in section.find_all("p")
            if squash(p.text()).startswith("prompt:")
        ),
        dane_rows=tuple(
            frozenset(name.strip() for dt in dl.find_all("dt") for name in dt.text().split("·") if name.strip())
            for section in dane
            for dl in section.find_all("dl")
        ),
    )


def _names(names: frozenset[str] | set[str]) -> str:
    return ", ".join(sorted(names))


def compare(page: PageFacts, release: Release) -> list[str]:
    """One English line per mismatch between the landing page's marked facts and ``release``."""
    where, version = "index.html", f"{PACKAGE} {release.version}"
    tools, titles = release.tool_names, release.prompt_titles
    tool_groups, lists = groups(), dane_rows()
    problems = []

    if not page.versions:
        problems.append(f'{where}: no data-fact="version"')
    for shown in page.versions:
        if shown != release.version:
            problems.append(f"{where}: shows version v{shown}, the released version is {release.version}")
    if not page.tool_counts:
        problems.append(f'{where}: no data-fact="tool-count"')
    for count in page.tool_counts:
        if count != len(tools):
            problems.append(f"{where}: tool-count {count}, {version} has {len(tools)} tools")
    if not page.prompt_counts:
        problems.append(f'{where}: no data-fact="prompt-count"')
    for count in page.prompt_counts:
        if count != len(titles):
            problems.append(f"{where}: prompt-count {count}, {version} has {len(titles)} prompts")

    ungrouped = tools - frozenset().union(*tool_groups.values())
    if ungrouped:
        problems.append(f"{version} has tools in no group of tools/build_docs.py GROUPS: {_names(ungrouped)}")
    for group, members in tool_groups.items():
        expected = len(members & tools)
        if group not in page.group_counts:
            problems.append(f'{where}: no data-fact="group-count" for group {group}')
        elif page.group_counts[group] != expected:
            problems.append(f"{where}: group-count {group} is {page.group_counts[group]}, {version} has {expected}")
    for group in page.group_counts.keys() - tool_groups.keys():
        problems.append(f"{where}: group-count for an unknown group {group!r}")

    listed = frozenset().union(*page.dane_rows)
    if tools - listed:
        problems.append(f"{where}: the Dane section misses tools of {version}: {_names(tools - listed)}")
    if listed - tools:
        problems.append(f"{where}: the Dane section lists tools {version} does not have: {_names(listed - tools)}")
    if len(page.dane_rows) != len(lists):
        problems.append(f"{where}: the Dane section has {len(page.dane_rows)} rows, expected {len(lists)}")
    else:
        for row, keys in zip(page.dane_rows, lists):
            expected = frozenset().union(*(tool_groups[key] for key in keys)) & tools
            for name in sorted(row & tools - expected):
                problems.append(f"{where}: the Dane row for {' + '.join(keys)} lists {name}, which belongs elsewhere")

    shown = frozenset(page.prompt_titles)
    if titles - shown:
        problems.append(f"{where}: data-prompt misses prompts of {version}: {_names(titles - shown)}")
    if shown - titles:
        problems.append(f"{where}: data-prompt shows prompts {version} does not have: {_names(shown - titles)}")
    for title in sorted(frozenset(page.persona_prompts) - titles):
        problems.append(f"{where}: a persona card names the prompt {title!r}, which {version} does not have")
    return problems


# data/server/*.json (the docs page's source) against the release.


def _subset(entry: dict, fields: tuple[str, ...]) -> dict:
    return {field: entry[field] for field in fields if field in entry}


def compare_listing(kind: str, saved: list[dict], released: tuple[dict, ...], fields: tuple[str, ...], version: str) -> list[str]:
    where = f"data/server/mcp_{kind}s.json"
    saved_by_name = {entry["name"]: _subset(entry, fields) for entry in saved}
    released_by_name = {entry["name"]: _subset(entry, fields) for entry in released}
    problems = []
    for name in sorted(released_by_name.keys() - saved_by_name.keys()):
        problems.append(f"{where}: misses the {kind} {name} of {PACKAGE} {version}")
    for name in sorted(saved_by_name.keys() - released_by_name.keys()):
        problems.append(f"{where}: has the {kind} {name}, which {PACKAGE} {version} does not have")
    for name in sorted(saved_by_name.keys() & released_by_name.keys()):
        changed = [f for f in fields if saved_by_name[name].get(f) != released_by_name[name].get(f)]
        if changed:
            problems.append(f"{where}: the {kind} {name} differs from {PACKAGE} {version} in {', '.join(changed)}")
    return problems


SYNC_COMMANDS = "run uv run python -m tools.sync_server {version}, then python3 tools/build_docs.py"


def compare_snapshots(tools_json: dict, prompts_json: dict, release: Release) -> list[str]:
    problems = compare_listing("tool", tools_json["tools"], release.tools, TOOL_FIELDS, release.version)
    problems += compare_listing("prompt", prompts_json["prompts"], release.prompts, PROMPT_FIELDS, release.version)
    if release.instructions and tools_json.get("server", {}).get("instructions") != release.instructions:
        problems.append(f"data/server/mcp_tools.json: the server instructions differ from {PACKAGE} {release.version}")
    if problems:
        problems.append(f"  {SYNC_COMMANDS.format(version=release.version)}")
    return problems


def compare_pinned(pinned: str, release: Release) -> list[str]:
    """The version data/server/version.json pins, the source of every generated part, against ``release``."""
    if pinned == release.version:
        return []
    return [
        f"data/server/version.json pins {PACKAGE} {pinned}, but the release is {release.version}: "
        f"{SYNC_COMMANDS.format(version=release.version)} (or merge the pull request of the Sync workflow)"
    ]


# Generated parts against tools/build_docs.py.


def check_docs_build(docs_html: str, index_html: str, saved: Release) -> list[str]:
    """Both pages against what tools/build_docs.py builds from ``saved`` (data/server/)."""
    try:
        docs, index = build_docs.render_docs(saved), build_docs.render_landing(index_html, saved)
    except build_docs.BuildError as error:
        return [f"tools/build_docs.py stops: {line}" for line in str(error).splitlines()]
    problems = []
    if docs_html != docs:
        problems.append("docs/index.html differs from tools/build_docs.py output; run python3 tools/build_docs.py")
    if index_html != index:
        problems.append("index.html: the generated blocks differ from tools/build_docs.py output; run python3 tools/build_docs.py")
    return problems


# The demo block against data/demo.json.

NBSP = " "


def group_thousands(value: int) -> str:
    return f"{value:,}".replace(",", NBSP)


def format_pln(value: float) -> str:
    return f"{group_thousands(round(value))}{NBSP}zł"


def format_area(value: float) -> str:
    return f"{value:.2f}".replace(".", ",") + f"{NBSP}m²"


def format_change(value: float) -> str:
    return f"{value:.1f}".replace(".", ",").replace("-", "−") + "%"


def format_date(value: str) -> str:
    return date.fromisoformat(value).strftime("%d.%m.%Y")


def check_demo(index_html: str, demo: dict) -> list[str]:
    where = "index.html demo"
    blocks = parse(index_html).find_all("figure", id="demo")
    if len(blocks) != 1:
        return [f'{where}: expected one <figure id="demo">, found {len(blocks)}']
    block = blocks[0]
    problems = []

    def step(name: str) -> list[Node]:
        return block.find_all(data_step=name)

    def expect(label: str, shown: str, wanted: str) -> None:
        if shown != wanted:
            problems.append(f"{where}: {label} is {shown!r}, data/demo.json gives {wanted!r}")

    if f"Mieszkania {demo['city_locative']}" not in squash(block.text()):
        problems.append(f"{where}: the window title does not name 'Mieszkania {demo['city_locative']}'")
    expect("the question", squash("".join(n.text() for n in step("question"))), demo["question"])
    composer = block.find_all(class_="composer-text")
    expect("the composer's data-question", composer[0].attrs.get("data-question", "") if composer else "", demo["question"])
    if demo["tool"] not in "".join(n.text() for n in step("chip")):
        problems.append(f"{where}: the tool chip does not name {demo['tool']}")

    def pairs(name: str) -> list[tuple[str, str]]:
        return [tuple(squash(span.text()) for span in n.find_all("span")[:2]) for n in step(name)]

    expect("the arguments", repr(pairs("arg")), repr([(k, json.dumps(v, ensure_ascii=False)) for k, v in demo["inspector_args"].items()]))
    result = dict(pairs("result"))
    for field in ("total", "returned", "truncated"):
        expect(f"result {field}", result.get(field, ""), json.dumps(demo["result"][field]))
    shortened = result.get("list_url", "").strip('"')
    if not shortened or any(part not in demo["result"]["list_url"] for part in shortened.split("…") if part):
        problems.append(f"{where}: result list_url {shortened!r} is not a shortening of the list_url in data/demo.json")
    if group_thousands(demo["result"]["total"]) not in "".join(n.text() for n in step("answer")):
        problems.append(f"{where}: the answer does not give the total {demo['result']['total']}")

    # Cells keep their no-break spaces (squash would turn them into plain spaces).
    rows = [[td.text().strip() for td in tr.find_all("td")] for tr in step("row")]
    wanted_rows = [
        [
            row["name"],
            f"{row['investment_name']} · {row['developer_name']}",
            format_area(row["area_m2"]),
            format_pln(row["price"]),
            format_change(row["price_change_since_start_pct"]),
        ]
        for row in demo["rows"]
    ]
    if len(rows) != len(wanted_rows):
        problems.append(f"{where}: the table has {len(rows)} rows, data/demo.json has {len(wanted_rows)}")
    for number, (shown, wanted) in enumerate(zip(rows, wanted_rows), start=1):
        expect(f"table row {number}", " | ".join(shown), " | ".join(wanted))

    source = step("source")
    source_text = squash(source[0].text()) if source else ""
    if f"stan na {format_date(demo['captured_at'])}" not in source_text:
        problems.append(f"{where}: the source line does not give the capture date {format_date(demo['captured_at'])}")
    links = [a.attrs.get("href") for n in source for a in n.find_all("a")]
    if links != [demo["result"]["list_url"]]:
        problems.append(f"{where}: the source link {links!r} is not the list_url in data/demo.json")
    return problems


# The release.


def latest_version() -> str:
    request = Request(PYPI_URL, headers={"Accept": "application/json"})
    with urlopen(request, timeout=30) as response:
        return json.load(response)["info"]["version"]


async def read_release(version: str | None = None) -> Release:
    """``version`` (default: the latest on PyPI) as ``tools/list`` and ``prompts/list`` give it."""
    from mcp import Client, StdioServerParameters

    version = version or latest_version()
    server = StdioServerParameters(command="uvx", args=[f"{PACKAGE}=={version}"])
    async with Client(server) as client:
        tools, prompts, cursor = [], [], None
        while True:
            listed = await client.list_tools(cursor=cursor)
            tools += listed.tools
            if not (cursor := listed.next_cursor):
                break
        while True:
            listed = await client.list_prompts(cursor=cursor)
            prompts += listed.prompts
            if not (cursor := listed.next_cursor):
                break
        info, instructions = client.server_info, client.instructions
    served = info.version if info else None
    if served != version:
        raise RuntimeError(f"uvx {PACKAGE}=={version} reports version {served}")
    dump = {"mode": "json", "by_alias": True, "exclude_none": True}
    return Release(
        version=version,
        tools=tuple(tool.model_dump(**dump) for tool in tools),
        prompts=tuple(prompt.model_dump(**dump) for prompt in prompts),
        name=info.name if info else "",
        instructions=instructions or "",
    )


def check_all(release: Release, root: Path = ROOT) -> list[str]:
    def read(path: str) -> str:
        return (root / path).read_text(encoding="utf-8")

    index_html, docs_html = read("index.html"), read("docs/index.html")
    saved = build_docs.load_release(root)
    problems = compare_pinned(saved.version, release)
    problems += compare(read_page_facts(index_html), release)
    problems += compare_written("index.html", read_written_facts(index_html), release)
    problems += compare_written("docs/index.html", read_written_facts(docs_html), release)
    problems += compare_snapshots(json.loads(read("data/server/mcp_tools.json")), json.loads(read("data/server/mcp_prompts.json")), release)
    problems += check_docs_build(docs_html, index_html, saved)
    problems += check_demo(index_html, json.loads(read("data/demo.json")))
    return problems


def main() -> int:
    release = asyncio.run(read_release())
    problems = check_all(release)
    if problems:
        print(f"The pages do not match {PACKAGE} {release.version}:")
        for problem in problems:
            print(f"- {problem}")
        return 1
    print(f"OK: the pages match {PACKAGE} {release.version} ({len(release.tools)} tools, {len(release.prompts)} prompts)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
