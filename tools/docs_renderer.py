"""Pure renderers for the documentation and landing source templates."""

import json
from pathlib import Path

from tools import docs_content as content, page_config as config
from tools.html_helpers import _lines, e, landing_command, render_template, prompts_phrase, prompts_sentence_start, term, tools_phrase
from tools.page_fragments import page_fragments
from tools.release import PACKAGE, Release, normalize_mcp_metadata, schema_types, validate_cli
from tools.update_demo import demo_blocks


def schema_type(prop: dict) -> str:
    types = schema_types(prop)
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
<p><a class="btn-link" href="{e(config.SITE.releases_url)}" data-umami-event="docs-open-release">Otwórz stronę wydania →</a></p>
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


def agent_block() -> str:
    return f"""
<div class="agent-card agent-card--landing">
<div class="agent-copy">
<p class="eyebrow">Na skróty</p>
<h3>Niech AI zainstaluje to za Ciebie.</h3>
<p>Masz agenta AI z dostępem do terminala, np. Claude Code? Skopiuj prompt i wklej go agentowi: sprawdzi system, doinstaluje uv i doda serwer do Twojej aplikacji, pytając o zgodę przed zmianą konfiguracji.</p>
</div>
{term(config.agent_prompt(), label="Prompt dla agenta AI", prompt=False, event="copy-agent-prompt")}
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
    """Release-dependent landing slots, keyed by their unique names."""
    release = normalize_mcp_metadata(release)
    content.check_release(release)
    groups = {group.key: group for group in content.GROUPS}
    tools, prompts = len(release.tools), len(release.prompts)
    blocks = {
        "hero_version": f'<span data-fact="version">v{e(release.version)}</span>',
        "demo_tool_count": tools_phrase(tools, fact=True),
        "demo_prompts": _lines([
            SIDEBAR_PROMPT.format(
                title=e(p.get("title") or p["name"]),
                active=" is-active" if p["name"] == content.DEMO_PROMPT else "",
            )
            for p in release.prompts
        ]),
        "demo_tool_groups": _lines([
            SIDEBAR_GROUP.format(label=e(g.short_label), key=g.key, count=len(g.tools)) for g in content.GROUPS
        ]),
        "personas_prompt_count": prompts_sentence_start(prompts),
        "install_counts": f"{tools_phrase(tools)} i {prompts_phrase(prompts)}",
        "plan_counts": f"{tools_phrase(tools, fact=True)} i {prompts_phrase(prompts, fact=True)}",
        "agent_prompt": agent_block(),
    }
    for name, keys in content.DANE_LISTS:
        blocks[name.replace("-", "_")] = _lines([
            DANE_ROW.format(key=key, names=e(" · ".join(names)), text=e(text))
            for key in keys
            for names, text in groups[key].rows
        ])
    return blocks


def render_landing(
    template: str, release: Release, demo: dict, *, shared_templates: dict[str, str] | None = None,
) -> str:
    """Render the complete landing template from saved release and demo data."""
    commands, site = config.COMMANDS, config.SITE
    server_command = e(json.dumps(commands.server_command))
    server_args = e(json.dumps(list(commands.server_args), ensure_ascii=False))
    return render_template(
        template, landing_blocks(release) | demo_blocks(demo) | page_fragments("landing", shared_templates) | dict(
            run_server=landing_command(commands.run_server, "copy-hero", hero=True),
            add_server=landing_command(commands.add_server, "copy-claude"),
            install_skill=landing_command(commands.install_skill, "copy-skill"),
            cli_help=landing_command(commands.cli_help, "copy-cli"),
            server_config_hint=f'# w innych klientach: &quot;command&quot;: {server_command},<br># &quot;args&quot;: {server_args}',
            version_releases=e(site.releases_url), faq_issues=e(site.repository_url + "/issues"),
            plan_repo=e(site.repository_url), footer_repo=e(site.repository_url),
            footer_prompts=e(site.repository_url + "/blob/main/docs/mcp/prompts.md"),
            footer_changelog=e(site.repository_url + "/blob/main/CHANGELOG.md"),
            footer_pypi=e(site.pypi_url), footer_issues=e(site.repository_url + "/issues"),
        ),
        name="tools/templates/landing.html", unique=True,
    )


def cli_table(release: Release) -> str:
    commands = validate_cli({"version": release.version, "commands": list(release.cli_commands)}, release.version)
    return "".join(f"<tr><td><code>{e(command['usage'])}</code></td><td>{e(command['description'])}</td></tr>" for command in commands)


def render_docs(
    release: Release, template: str | None = None, *, shared_templates: dict[str, str] | None = None,
) -> str:
    """Render the docs from the saved release; no subprocess or network access."""
    release = normalize_mcp_metadata(release)
    content.check_release(release)
    if template is None:
        template = (Path(__file__).with_name("templates") / "docs.html").read_text(encoding="utf-8")
    return render_template(template, dict(
        repo=e(config.SITE.repository_url),
        pypi=e(config.SITE.pypi_url),
        run_server=e(config.COMMANDS.run_server),
        installation_steps=steps(release),
        agent_section=content.AGENT_SECTION.replace('{term}', term(config.agent_prompt(), label='Prompt dla agenta AI', prompt=False, event='docs-copy-agent-prompt')),
        tool_count=tools_phrase(len(release.tools)),
        install_uv=term(config.COMMANDS.install_uv),
        install_uv_windows=term(config.COMMANDS.install_uv_windows, label='PowerShell', prompt=False),
        install_uv_homebrew=term(config.COMMANDS.install_uv_homebrew),
        check_uv=term(config.COMMANDS.check_uv),
        add_server=term(config.COMMANDS.add_server),
        client_config=term(config.COMMANDS.client_config, label='claude_desktop_config.json', prompt=False, event='docs-copy-config-json'),
        install_skill=term(config.COMMANDS.install_skill),
        install_skill_cli=term(config.COMMANDS.install_skill_cli),
        cli_help=term(config.COMMANDS.cli_help),
        cli_install=term(config.COMMANDS.cli_install),
        prompts=prompts_section(release),
        tools=tools_section(release),
        cli_table=cli_table(release),
        stop_status=term(config.COMMANDS.stop_status),
        stop_clear=term(config.COMMANDS.stop_clear),
    ) | page_fragments("docs", shared_templates), name="tools/templates/docs.html")
