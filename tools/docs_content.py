"""Editorial grouping and Polish copy; release metadata is never duplicated here."""

from dataclasses import dataclass

from tools.release import PACKAGE, BuildError, Release


@dataclass(frozen=True)
class Group:
    """Tools that give one kind of data: a heading with cards in the docs, a line in the
    landing's demo sidebar and rows in the landing's Dane section."""

    key: str
    label: str  # the docs heading
    short_label: str  # the line in the landing's demo sidebar
    color: str
    tint: str  # the tool names in the Dane section
    blurb: str  # under the docs heading
    rows: tuple[tuple[tuple[str, ...], str], ...]  # Dane rows: tool names and what they give

    @property
    def tools(self) -> tuple[str, ...]:
        """The group's tools in docs order."""
        return tuple(name for names, _ in self.rows for name in names)


GROUPS = (
    Group("offers", "Ceny ofertowe", "Ceny ofertowe", "#A78BFA", "#C4B5FD", "Cenniki deweloperów na rynku pierwotnym i ich zmiany.", (
        (("search_offers",), "Oferty w mieście albo w całej Polsce, z filtrami ceny, metrażu, ceny za m², dzielnicy i obniżki."),
        (("get_offer",), "Jedna oferta z pełną historią ceny i flagami: najniższa cena z 30 i 90 dni, podejrzana obniżka."),
        (("search_investments", "get_investment"), "Inwestycje w mieście albo po nazwie, a w nich deweloper i lista ofert."),
        (("get_market_statistics",), "Rynek ofert jednego miasta: liczby, rozkłady cen i metraży, najwięksi deweloperzy."),
    )),
    Group("rcn", "Ceny transakcyjne (RCN)", "Transakcje RCN", "#FACC15", "#FDE68A", "Za ile mieszkania naprawdę się sprzedały, z Rejestru Cen Nieruchomości.", (
        (("get_transaction_statistics",), "Ceny transakcyjne miasta miesiąc po miesiącu albo kwartałami."),
        (("rank_locations",), "Ranking miejscowości albo powiatów według transakcji i ceny za m²."),
        (("get_building",), "Budynek z rejestru z lokalami i transakcjami, opcjonalnie z trendem okolicy."),
        (("estimate_property_value",), "Przybliżona wartość mieszkania w danym miejscu z pobliskich transakcji."),
    )),
    Group("official", "Statystyki GUS i NBP", "GUS i NBP", "#38BDF8", "#7DD3FC", "Oficjalne dane: ceny, mieszkania oddane, wynagrodzenia, bezrobocie.", (
        (("get_government_statistics",), "Oficjalne szeregi czasowe dla Polski, grup największych miast albo miasta na prawach powiatu: ceny mieszkań, mieszkania oddane, wynagrodzenia i bezrobocie."),
    )),
    Group("helpers", "Narzędzia pomocnicze", "Pomocnicze", "#34D399", "#6EE7B7", "Zamieniają nazwy miejsc i deweloperów na identyfikatory i dają profil dewelopera.", (
        (("search_locations", "search_developers", "get_developer"), "Narzędzia pomocnicze: zamieniają nazwę miasta albo dewelopera na identyfikator i dają profil dewelopera."),
    )),
)
# The landing's Dane section: one list per kind of price; the helpers share the last one.
DANE_LISTS = (("dane-offers", ("offers",)), ("dane-rcn", ("rcn",)), ("dane-official", ("official", "helpers")))
# The prompt the landing's demo sidebar highlights: the demo is a search.
DEMO_PROMPT = "znajdz_mieszkanie"


def check_release(release: Release) -> None:
    """Stop the build when the release has a tool or prompt the hand-written parts do not know."""
    grouped = [name for group in GROUPS for name in group.tools]
    unknown = sorted(release.tool_names - set(grouped))
    gone = sorted(set(grouped) - release.tool_names)
    twice = sorted({name for name in grouped if grouped.count(name) > 1})
    problems = []
    if unknown:
        problems.append(
            f"{PACKAGE} {release.version} has tools that no group in tools/docs_content.py GROUPS holds: {', '.join(unknown)}. "
            "Add each to a row of a group (the row's text is its line in the landing's Dane section)."
        )
    if gone:
        problems.append(
            f"tools/docs_content.py GROUPS names tools that {PACKAGE} {release.version} does not have: {', '.join(gone)}. "
            "Remove them from their rows."
        )
    if twice:
        problems.append(f"tools/docs_content.py GROUPS names tools more than once: {', '.join(twice)}.")
    if DEMO_PROMPT not in {prompt["name"] for prompt in release.prompts}:
        problems.append(
            f"the landing's demo highlights the prompt {DEMO_PROMPT}, which {PACKAGE} {release.version} does not have: "
            "set DEMO_PROMPT in tools/docs_content.py."
        )
    if problems:
        raise BuildError("\n".join(problems))


AGENT_PROMPT = """Zainstaluj mi serwer MCP deweloperuch-mcp (dane o rynku mieszkań w Polsce z deweloperuch.pl, projekt: https://github.com/MBratkowski/deweloperuch-mcp).

1. Sprawdź, czy mam macOS albo Linuxa. Windows nie jest obsługiwany: jeśli to Windows, przerwij i powiedz mi o tym.
2. Sprawdź, czy mam program uv (uvx --version). Jeśli nie, zainstaluj go oficjalnym skryptem: curl -LsSf https://astral.sh/uv/install.sh | sh
3. Sprawdź, czy serwer się uruchamia: uvx deweloperuch-mcp --help
4. Dodaj serwer do aplikacji, której używam:
   - Claude Code: claude mcp add deweloperuch -- uvx deweloperuch-mcp
   - Claude Desktop: w pliku claude_desktop_config.json (na macOS w ~/Library/Application Support/Claude/) dopisz w sekcji mcpServers wpis "deweloperuch": {"command": "uvx", "args": ["deweloperuch-mcp"]}. Zachowaj istniejące wpisy i najpierw zrób kopię pliku.
   - Inna aplikacja: dodaj lokalny serwer MCP z poleceniem uvx i argumentem deweloperuch-mcp.
   Zanim zmienisz jakikolwiek plik konfiguracyjny, pokaż mi, co dokładnie zmienisz, i zapytaj o zgodę.
5. Nie wysyłaj w ramach instalacji żadnych zapytań do deweloperuch.pl.
6. Na koniec powiedz mi, czy muszę ponownie uruchomić aplikację, i podaj przykładowe pytanie na start."""

AGENT_SECTION = f"""
<section id="zainstaluj-z-ai" class="wrap agent-install" aria-labelledby="zainstaluj-z-ai-title">
<div class="agent-card">
<div class="agent-copy">
<p class="eyebrow">Na skróty</p>
<h2 id="zainstaluj-z-ai-title">Niech AI zainstaluje to za Ciebie.</h2>
<p>Masz agenta AI z dostępem do terminala, np. Claude Code? Skopiuj ten prompt i wklej go agentowi. Sprawdzi Twój system, w razie potrzeby zainstaluje uv i doda serwer do Twojej aplikacji. Przed każdą zmianą w plikach konfiguracji pokaże, co zmieni, i zapyta o zgodę.</p>
</div>
{{term}}
</div>
</section>
"""

CONFIG_JSON ='{\n  "mcpServers": {\n    "deweloperuch": {\n      "command": "uvx",\n      "args": ["deweloperuch-mcp"]\n    }\n  }\n}'
