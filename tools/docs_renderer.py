"""Pure renderers for the documentation page and the landing's generated blocks."""

from pathlib import Path
from string import Template

from tools import docs_content as content
from tools.html_helpers import _lines, e, fill_blocks, prompts_phrase, prompts_sentence_start, term, tools_phrase
from tools.release import PACKAGE, REPO, RELEASES, Release, validate_cli


def schema_type(prop: dict) -> str:
    types = [o.get("type") for o in prop.get("anyOf", [prop])]
    names = {"string": "tekst", "integer": "liczba", "number": "liczba", "boolean": "tak/nie", "array": "lista"}
    return " albo ".join(names.get(t, t) for t in types if t and t != "null") or "—"


def tool_card(tool: dict) -> str:
    lead = tool["description"].split("\n\n")[0]
    props = tool["inputSchema"].get("properties", {})
    required = set(tool["inputSchema"].get("required") or [])
    rows = "".join(
        f'<tr><td><code>{e(name)}</code>{" <span class=\"req\">wymagany</span>" if name in required else ""}</td>'
        f"<td>{e(schema_type(p))}</td><td>{e(p.get('description', ''))}</td></tr>"
        for name, p in props.items()
    )
    return (
        f'<article class="tool" id="tool-{e(tool["name"])}">'
        f'<div class="tool-head"><h4>{e(tool.get("title") or tool["name"])}</h4>'
        f'<code class="tool-identifier">{e(tool["name"])}</code></div>'
        f"<p>{e(lead)}</p>"
        f'<details><summary>Parametry ({len(props)})</summary><div class="table-wrap" tabindex="0" role="region" aria-label="Tabela"><table>'
        f'<thead><tr><th scope="col">Parametr</th><th scope="col">Typ</th><th scope="col">Opis</th></tr></thead>'
        f"<tbody>{rows}</tbody></table></div></details></article>"
    )


def tools_section(release: Release) -> str:
    by_name = {t["name"]: t for t in release.tools}
    parts = []
    for group in content.GROUPS:
        cards = "".join(tool_card(by_name[n]) for n in group.tools)
        parts.append(
            f'<div class="group theme-{group.key}" data-group="{group.key}"><h3><span class="dot" aria-hidden="true"></span>'
            f"{e(group.label)} <small>{len(group.tools)}</small></h3><p>{e(group.blurb)}</p>{cards}</div>"
        )
    return "".join(parts)


def prompts_section(release: Release) -> str:
    cards = []
    for p in release.prompts:
        rows = "".join(
            f'<tr><td><code>{e(a["name"])}</code>{" <span class=\"req\">wymagany</span>" if a.get("required") else ""}</td>'
            f"<td>{e(a.get('description', ''))}</td></tr>"
            for a in p.get("arguments", [])
        )
        cards.append(
            f'<article class="tool"><div class="tool-head"><h3>{e(p["title"])}</h3><code>{e(p["name"])}</code></div>'
            f'<p>{e(p["description"])}</p><div class="table-wrap" tabindex="0" role="region" aria-label="Tabela"><table><thead><tr><th scope="col">Pole</th>'
            f'<th scope="col">Co wpisać</th></tr></thead><tbody>{rows}</tbody></table></div></article>'
        )
    return "".join(cards)


DOTS = '<span aria-hidden="true"></span><span aria-hidden="true"></span><span aria-hidden="true"></span>'


def steps(release: Release) -> str:
    version = e(release.version)
    return f"""
<ol class="steps">
<li class="step">
<div class="step-art" aria-hidden="true">
<div class="mock-window">
<div class="mock-bar">{DOTS}<em>github.com · deweloperuch-mcp · Releases</em></div>
<div class="mock-body">
<p class="mock-label">Assets</p>
<p class="mock-row is-hot"><span>deweloperuch-mcp-{version}.mcpb</span><span class="mock-cursor">⬇</span></p>
<p class="mock-row"><span>deweloperuch_mcp-{version}-py3-none-any.whl</span></p>
<p class="mock-row"><span>Source code (zip)</span></p>
</div>
</div>
</div>
<div class="step-body">
<p class="step-num">Krok 1</p>
<h3>Pobierz plik rozszerzenia</h3>
<p>Otwórz stronę najnowszego wydania i w sekcji „Assets” kliknij plik, którego nazwa kończy się na <code>.mcpb</code>. Zapisze się w folderze Pobrane.</p>
<p><a class="btn-link" href="{RELEASES}" data-umami-event="docs-open-release">Otwórz stronę wydania →</a></p>
</div>
</li>
<li class="step">
<div class="step-art" aria-hidden="true">
<div class="mock-pair">
<div class="mock-file"><span class="file-icon">.mcpb</span><span>deweloperuch-mcp…</span><span class="mock-hint">kliknij 2×</span></div>
<span class="mock-arrow">→</span>
<div class="mock-window mock-dialog">
<div class="mock-bar">{DOTS}<em>Claude</em></div>
<div class="mock-body">
<p class="mock-title">Zainstalować rozszerzenie?</p>
<p class="mock-text">deweloperuch-mcp · {tools_phrase(len(release.tools))}</p>
<p class="mock-actions"><span class="mock-btn-ghost">Cancel</span><span class="mock-btn">Install</span></p>
</div>
</div>
</div>
</div>
<div class="step-body">
<p class="step-num">Krok 2</p>
<h3>Kliknij go dwa razy</h3>
<p>Claude Desktop otworzy okno instalacji rozszerzenia: potwierdź przyciskiem <strong>Install</strong>. Jeśli nic się nie dzieje, otwórz w aplikacji <strong>Settings → Extensions</strong> (Ustawienia → Rozszerzenia) i przeciągnij tam pobrany plik.</p>
</div>
</li>
<li class="step">
<div class="step-art" aria-hidden="true">
<div class="mock-window">
<div class="mock-bar">{DOTS}<em>Claude</em></div>
<div class="mock-body mock-center">
<p class="mock-title">Uruchamianie rozszerzenia…</p>
<p class="mock-progress"><span></span></p>
<p class="mock-text">pierwszy start: około minuty</p>
</div>
</div>
</div>
<div class="step-body">
<p class="step-num">Krok 3</p>
<h3>Uruchom aplikację ponownie</h3>
<p>Zamknij i otwórz Claude Desktop. Za pierwszym razem rozszerzenie przygotowuje się około minuty: samo pobiera to, czego potrzebuje. Niczego nie instalujesz ręcznie i nie potrzebujesz terminala.</p>
</div>
</li>
<li class="step">
<div class="step-art" aria-hidden="true">
<div class="mock-window">
<div class="mock-bar">{DOTS}<em>Nowa rozmowa</em></div>
<div class="mock-body">
<p class="mock-bubble">Znajdź mieszkania 40–55 m² w Krakowie do 800 000 zł.</p>
<p class="mock-chip"><span class="mock-dot"></span>search_offers · gotowe</p>
<p class="mock-lines"><span></span><span></span><span></span></p>
</div>
</div>
</div>
<div class="step-body">
<p class="step-num">Krok 4</p>
<h3>Zapytaj jak człowieka</h3>
<p>Napisz pytanie zwykłymi słowami. Asystent sam sięgnie po narzędzia deweloperuch i odpowie tabelą z linkami do deweloperuch.pl. Przykłady pytań znajdziesz <a href="#pierwsze-pytania">niżej</a>.</p>
</div>
</li>
</ol>
<p class="caption">Ilustracje są poglądowe: wygląd okien w Twojej wersji aplikacji może się nieco różnić.</p>
"""


AGENT_BLOCK = f"""
<div class="agent-card agent-card--landing">
<div class="agent-copy">
<p class="eyebrow">Na skróty</p>
<h3>Niech AI zainstaluje to za Ciebie.</h3>
<p>Masz agenta AI z dostępem do terminala, np. Claude Code? Skopiuj prompt i wklej go agentowi: sprawdzi system, doinstaluje uv i doda serwer do Twojej aplikacji, pytając o zgodę przed zmianą konfiguracji.</p>
</div>
{term(content.AGENT_PROMPT, label="Prompt dla agenta AI", prompt=False, event="copy-agent-prompt")}
</div>
"""

SIDEBAR_PROMPT = '<p class="sidebar-prompt{active}" data-prompt="{title}">{title}</p>'
SIDEBAR_GROUP = (
    '<p class="sidebar-group theme-{key}">'
    '<span aria-hidden="true" class="theme-dot"></span><span class="sidebar-group-name">{label}</span>'
    '<span class="sidebar-group-count" data-fact="group-count" data-group="{key}">{count}</span></p>'
)
DANE_ROW = (
    '<div class="theme-{key}"><dt class="tool-name">{names}</dt>'
    '<dd>{text}</dd></div>'
)


def landing_blocks(release: Release) -> dict[str, str]:
    """The generated parts of index.html for ``release``, by marker name."""
    content.check_release(release)
    groups = {group.key: group for group in content.GROUPS}
    tools, prompts = len(release.tools), len(release.prompts)
    blocks = {
        "hero-version": f'<span data-fact="version">v{e(release.version)}</span>',
        "demo-tool-count": tools_phrase(tools, fact=True),
        "demo-prompts": _lines([
            SIDEBAR_PROMPT.format(
                title=e(p.get("title") or p["name"]),
                active=" is-active" if p["name"] == content.DEMO_PROMPT else "",
            )
            for p in release.prompts
        ]),
        "demo-tool-groups": _lines([
            SIDEBAR_GROUP.format(label=e(g.short_label), key=g.key, count=len(g.tools)) for g in content.GROUPS
        ]),
        "personas-prompt-count": prompts_sentence_start(prompts),
        "install-counts": f"{tools_phrase(tools)} i {prompts_phrase(prompts)}",
        "plan-counts": f"{tools_phrase(tools, fact=True)} i {prompts_phrase(prompts, fact=True)}",
        "agent-prompt": AGENT_BLOCK,
    }
    for name, keys in content.DANE_LISTS:
        blocks[name] = _lines([
            DANE_ROW.format(key=key, names=e(" · ".join(names)), text=e(text))
            for key in keys
            for names, text in groups[key].rows
        ])
    return blocks


def render_landing(index: str, release: Release) -> str:
    """``index`` (index.html) with its generated parts for ``release``."""
    return fill_blocks(index, landing_blocks(release))


def cli_table(release: Release) -> str:
    commands = validate_cli({"version": release.version, "commands": list(release.cli_commands)}, release.version)
    return "".join(f"<tr><td><code>{e(command['usage'])}</code></td><td>{e(command['description'])}</td></tr>" for command in commands)


def render_docs(release: Release) -> str:
    """Render the docs from the saved release; no subprocess or network access."""
    content.check_release(release)
    template = Template((Path(__file__).with_name("templates") / "docs.html").read_text(encoding="utf-8"))
    return template.substitute(
        repo=REPO,
        releases=RELEASES,
        installation_steps=steps(release),
        agent_section=content.AGENT_SECTION.replace('{term}', term(content.AGENT_PROMPT, label='Prompt dla agenta AI', prompt=False, event='docs-copy-agent-prompt')),
        tool_count=tools_phrase(len(release.tools)),
        install_uv=term('curl -LsSf https://astral.sh/uv/install.sh | sh'),
        install_uv_homebrew=term('brew install uv'),
        check_uv=term('uvx --version'),
        add_server=term('claude mcp add deweloperuch -- uvx deweloperuch-mcp'),
        client_config=term(content.CONFIG_JSON, label='claude_desktop_config.json', prompt=False, event='docs-copy-config-json'),
        install_skill=term('npx skills add MBratkowski/deweloperuch-mcp'),
        install_skill_cli=term('uvx --from deweloperuch-mcp deweloperuch skill install'),
        cli_help=term('uvx --from deweloperuch-mcp deweloperuch --help'),
        cli_install=term('uv tool install deweloperuch-mcp'),
        prompts=prompts_section(release),
        tools=tools_section(release),
        cli_table=cli_table(release),
        stop_status=term('uvx --from deweloperuch-mcp deweloperuch stop status'),
        stop_clear=term('uvx --from deweloperuch-mcp deweloperuch stop clear'),
    )
