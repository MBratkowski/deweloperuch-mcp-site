"""Build docs/index.html: hand-written Polish guide + tool and prompt reference generated
from the server's MCP snapshots (data/server/*.json, copied from the deweloperuch-mcp repo:
tests/snapshots/mcp_tools.json and mcp_prompts.json). Re-run after copying newer snapshots:

    python3 tools/build_docs.py
"""

import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = json.loads((ROOT / "data/server/mcp_tools.json").read_text())["tools"]
PROMPTS = json.loads((ROOT / "data/server/mcp_prompts.json").read_text())["prompts"]
REPO = "https://github.com/MBratkowski/deweloperuch-mcp"
RELEASES = REPO + "/releases/latest"
e = html.escape

GROUPS = [
    ("offers", "Ceny ofertowe", "#A78BFA", "Cenniki deweloperów na rynku pierwotnym i ich zmiany.",
     ["search_offers", "get_offer", "search_investments", "get_investment", "get_market_statistics"]),
    ("rcn", "Ceny transakcyjne (RCN)", "#FACC15", "Za ile mieszkania naprawdę się sprzedały, z Rejestru Cen Nieruchomości.",
     ["get_transaction_statistics", "rank_locations", "get_building", "estimate_property_value"]),
    ("official", "Statystyki GUS i NBP", "#38BDF8", "Oficjalne dane: ceny, mieszkania oddane, wynagrodzenia, bezrobocie.",
     ["get_government_statistics"]),
    ("helpers", "Narzędzia pomocnicze", "#34D399", "Zamieniają nazwy miejsc i deweloperów na identyfikatory i dają profil dewelopera.",
     ["search_locations", "search_developers", "get_developer"]),
]
assert sorted(n for g in GROUPS for n in g[4]) == sorted(t["name"] for t in TOOLS), "group the new tool"

COPY_ICON = ('<svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
             'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="11" height="11" rx="2"></rect>'
             '<path d="M5 15V6a2 2 0 0 1 2-2h9"></path></svg>')


def term(command: str, label: str = "Terminal", prompt: bool = True) -> str:
    """A terminal block with a copy button (js/site.js wires every button.copy)."""
    sign = '<span class="prompt-sign" aria-hidden="true">$</span> ' if prompt else ""
    tag = "code" if "\n" not in command else "pre"
    return (
        f'<div class="term"><div class="term-bar"><span aria-hidden="true"></span><span aria-hidden="true"></span>'
        f'<span aria-hidden="true"></span><em>{e(label)}</em></div>'
        f'<div class="cmd"><{tag}>{sign}<span class="cmd-text">{e(command)}</span></{tag}>'
        f'<button type="button" class="copy" data-command="{e(command)}" hidden>{COPY_ICON}'
        f'<span aria-live="polite">Kopiuj</span></button></div></div>'
    )


def schema_type(prop: dict) -> str:
    types = [o.get("type") for o in prop.get("anyOf", [prop])]
    names = {"string": "tekst", "integer": "liczba", "number": "liczba", "boolean": "tak/nie", "array": "lista"}
    return " albo ".join(names.get(t, t) for t in types if t and t != "null") or "—"


def tool_card(tool: dict, color: str) -> str:
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
        f'<code style="color: {color}">{e(tool["name"])}</code></div>'
        f"<p>{e(lead)}</p>"
        f'<details><summary>Parametry ({len(props)})</summary><div class="table-wrap"><table>'
        f'<thead><tr><th scope="col">Parametr</th><th scope="col">Typ</th><th scope="col">Opis</th></tr></thead>'
        f"<tbody>{rows}</tbody></table></div></details></article>"
    )


def tools_section() -> str:
    by_name = {t["name"]: t for t in TOOLS}
    parts = []
    for key, label, color, blurb, names in GROUPS:
        cards = "".join(tool_card(by_name[n], color) for n in names)
        parts.append(
            f'<div class="group" data-group="{key}"><h3><span class="dot" style="background: {color}" aria-hidden="true"></span>'
            f"{e(label)} <small>{len(names)}</small></h3><p>{e(blurb)}</p>{cards}</div>"
        )
    return "".join(parts)


def prompts_section() -> str:
    cards = []
    for p in PROMPTS:
        rows = "".join(
            f'<tr><td><code>{e(a["name"])}</code>{" <span class=\"req\">wymagany</span>" if a.get("required") else ""}</td>'
            f"<td>{e(a.get('description', ''))}</td></tr>"
            for a in p.get("arguments", [])
        )
        cards.append(
            f'<article class="tool"><div class="tool-head"><h4>{e(p["title"])}</h4><code>{e(p["name"])}</code></div>'
            f'<p>{e(p["description"])}</p><div class="table-wrap"><table><thead><tr><th scope="col">Pole</th>'
            f'<th scope="col">Co wpisać</th></tr></thead><tbody>{rows}</tbody></table></div></article>'
        )
    return "".join(cards)


DOTS = '<span aria-hidden="true"></span><span aria-hidden="true"></span><span aria-hidden="true"></span>'

STEPS = f"""
<ol class="steps">
<li class="step">
<div class="step-art" aria-hidden="true">
<div class="mock-window">
<div class="mock-bar">{DOTS}<em>github.com · deweloperuch-mcp · Releases</em></div>
<div class="mock-body">
<p class="mock-label">Assets</p>
<p class="mock-row is-hot"><span>deweloperuch-mcp-0.1.1.mcpb</span><span class="mock-cursor">⬇</span></p>
<p class="mock-row"><span>deweloperuch_mcp-0.1.1-py3-none-any.whl</span></p>
<p class="mock-row"><span>Source code (zip)</span></p>
</div>
</div>
</div>
<div class="step-body">
<p class="step-num">Krok 1</p>
<h3>Pobierz plik rozszerzenia</h3>
<p>Otwórz stronę najnowszego wydania i w sekcji „Assets” kliknij plik, którego nazwa kończy się na <code>.mcpb</code>. Zapisze się w folderze Pobrane.</p>
<p><a class="btn-link" href="{RELEASES}">Otwórz stronę wydania →</a></p>
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
<p class="mock-text">deweloperuch-mcp · 13 narzędzi</p>
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

CLI_ROWS = [
    ("locations search QUERY", "Szuka miast, dzielnic i ulic; podaje identyfikator miasta (slug) do dalszych komend."),
    ("offers search CITY", "Oferty mieszkań w mieście albo w całej Polsce (polska) z filtrami ceny, metrażu, ceny za m², dzielnicy i obniżki."),
    ("offers get OFFER_ID --investment SLUG", "Jedna oferta z historią ceny i flagami (najniższa cena z 30 i 90 dni, podejrzana obniżka)."),
    ("investments search [CITY]", "Inwestycje w mieście albo po nazwie (--name) w całej Polsce."),
    ("investments get SLUG", "Inwestycja z deweloperem i listą ofert."),
    ("developers search NAME", "Szuka deweloperów po nazwie; podaje ich identyfikator."),
    ("developers get SLUG", "Profil dewelopera: podsumowanie, inwestycje i miasta."),
    ("buildings get SLUG", "Budynek z rejestru RCN z lokalami i transakcjami (--trend dodaje trend okolicy)."),
    ("valuation estimate --lat --lng --area", "Szacunkowa wartość mieszkania w danym miejscu z pobliskich transakcji."),
    ("stats market CITY", "Statystyki rynku ofert w mieście: rozkłady cen i metraży, najwięksi deweloperzy."),
    ("stats transactions CITY", "Ceny transakcyjne miasta miesiąc po miesiącu albo kwartałami."),
    ("stats cities", "Ranking miejscowości albo powiatów według transakcji z RCN."),
    ("stats government LOCATION DATASET", "Statystyki GUS i NBP dla Polski, agregatu albo miasta."),
    ("stop status", "Pokazuje, czy działa blokada STOP i dlaczego; nie łączy się z serwisem."),
    ("stop clear", "Zdejmuje blokadę STOP. Tylko po sprawdzeniu przyczyny."),
]
CLI_TABLE = "".join(f"<tr><td><code>deweloperuch {e(c)}</code></td><td>{e(d)}</td></tr>" for c, d in CLI_ROWS)

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

PAGE = f"""<!doctype html>
<html lang="pl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Dokumentacja · deweloperuch-mcp</title>
<meta name="description" content="Jak zainstalować deweloperuch-mcp krok po kroku i jak z niego korzystać: instalacja z obrazkami, przykładowe pytania, narzędzia, prompty, CLI i rozwiązywanie problemów.">
<meta name="theme-color" content="#0C0A14">
<link rel="canonical" href="https://mbratkowski.github.io/deweloperuch-mcp-site/docs/">
<meta property="og:type" content="website">
<meta property="og:locale" content="pl_PL">
<meta property="og:title" content="Dokumentacja deweloperuch-mcp">
<meta property="og:description" content="Instalacja krok po kroku, przykładowe pytania, narzędzia, prompty i rozwiązywanie problemów.">
<meta property="og:url" content="https://mbratkowski.github.io/deweloperuch-mcp-site/docs/">
<meta property="og:image" content="https://mbratkowski.github.io/deweloperuch-mcp-site/assets/og.png">
<meta name="twitter:card" content="summary_large_image">
<link rel="icon" type="image/png" href="../favicon.png">
<link rel="apple-touch-icon" href="../apple-touch-icon.png">
<link rel="preload" href="../assets/fonts/poppins-latin-700-normal.woff2" as="font" type="font/woff2" crossorigin>
<link rel="stylesheet" href="../css/site.css">
<script src="../js/site.js" defer></script>
</head>
<body>
<a class="skip-link" href="#main">Przejdź do treści</a>
<div class="page docs">
<header class="wrap topbar">
<nav class="pill-nav" aria-label="Nawigacja">
<a href="../" aria-label="deweloperuch-mcp, strona główna" class="brand"><img src="../assets/icon/icon-light.png" alt="" width="26" height="30"></a>
<a href="../">Start</a>
<a href="#krok-po-kroku">Instalacja</a>
<a href="#narzedzia">Narzędzia</a>
<a href="#prompty">Prompty</a>
<a href="#problemy">Problemy</a>
</nav>
<nav class="pill-nav" aria-label="Linki zewnętrzne">
<a href="{REPO}">GitHub</a>
<a href="{RELEASES}" class="pill-strong">Pobierz</a>
</nav>
</header>

<main id="main">
<section class="docs-hero">
<div class="wrap">
<p class="eyebrow">Dokumentacja</p>
<h1>Zainstaluj i zapytaj w&nbsp;kilka minut.</h1>
<p class="lead">Najprostsza droga to rozszerzenie do aplikacji Claude Desktop: cztery kroki, bez terminala. Niżej znajdziesz też instalację dla innych aplikacji i agentów AI, opis wszystkich narzędzi i to, co zrobić, gdy coś nie działa.</p>
</div>
</section>

<section id="krok-po-kroku" class="wrap docs-steps" aria-labelledby="krok-po-kroku-title">
<h2 id="krok-po-kroku-title">Instalacja krok po kroku</h2>
<p class="section-lead">Dla Claude Desktop na macOS. Linux działa tak samo; Windows nie jest jeszcze obsługiwany.</p>
{STEPS}
</section>
{AGENT_SECTION.replace("{term}", term(AGENT_PROMPT, label="Prompt dla agenta AI", prompt=False))}

<div class="wrap docs-layout">
<nav class="toc" aria-label="Spis treści">
<p class="toc-title">Na tej stronie</p>
<a href="#krok-po-kroku">Instalacja krok po kroku</a>
<a href="#zainstaluj-z-ai">Instalacja z pomocą AI</a>
<a href="#jak-to-dziala">Jak to działa</a>
<a href="#wymagania">Czego potrzebujesz</a>
<a href="#uv">Program uv</a>
<a href="#inne-aplikacje">Inne aplikacje i agenci</a>
<a href="#pierwsze-pytania">Pierwsze pytania</a>
<a href="#ceny">Trzy rodzaje cen</a>
<a href="#prompty">Gotowe prompty</a>
<a href="#narzedzia">Narzędzia</a>
<a href="#cli">Komendy w terminalu</a>
<a href="#limity">Limity i blokada STOP</a>
<a href="#problemy">Rozwiązywanie problemów</a>
<a href="#prywatnosc">Prywatność</a>
</nav>

<div class="prose">
<section id="jak-to-dziala">
<h2>Jak to działa</h2>
<p>Twój asystent AI uruchamia na Twoim komputerze mały program: serwer MCP. Gdy pytasz o mieszkania, asystent prosi ten program o dane, program pobiera je z deweloperuch.pl i oddaje asystentowi, a asystent układa odpowiedź, zwykle w tabeli z linkami do źródeł.</p>
<p><strong>MCP</strong> (Model Context Protocol) to otwarty standard, dzięki któremu asystenci AI mogą korzystać z zewnętrznych narzędzi i danych. deweloperuch-mcp daje im 13 narzędzi, które tylko czytają dane: niczego w serwisie nie tworzą ani nie zmieniają.</p>
<p>Nie zakładasz konta i nie potrzebujesz żadnego klucza. Projekt jest darmowy i otwarty (licencja MIT). To nieoficjalny projekt społeczności, który działa za zgodą autorów deweloperuch.pl.</p>
</section>

<section id="wymagania">
<h2>Czego potrzebujesz</h2>
<ul>
<li><strong>Komputera z macOS albo Linuxem.</strong> Windows nie jest jeszcze obsługiwany.</li>
<li><strong>Asystenta AI, który uruchamia lokalne serwery MCP</strong>, np. aplikacji Claude Desktop albo Claude Code. Asystenci, którzy przyjmują tylko zdalne serwery MCP, na razie z niego nie skorzystają.</li>
<li><strong>Do instalacji przez terminal: programu uv</strong> (opis niżej). Rozszerzenie dla Claude Desktop go nie wymaga.</li>
</ul>
</section>

<section id="uv">
<h2>Program uv</h2>
<p>uv to darmowy program, który pobiera i uruchamia narzędzia napisane w Pythonie. Nie musisz znać Pythona ani niczego konfigurować: wystarczy zainstalować uv raz. Potem komenda <code>uvx deweloperuch-mcp</code> sama pobierze i uruchomi serwer.</p>
<h3>Instalacja uv</h3>
<p>Otwórz aplikację Terminal (na Macu znajdziesz ją przez Spotlight: <kbd>⌘</kbd>+<kbd>spacja</kbd>, wpisz „Terminal”), wklej tę komendę i naciśnij Enter:</p>
{term("curl -LsSf https://astral.sh/uv/install.sh | sh")}
<p>Jeśli masz Homebrew, możesz też użyć:</p>
{term("brew install uv")}
<p>Po instalacji zamknij okno terminala i otwórz nowe. Sprawdź, czy wszystko działa:</p>
{term("uvx --version")}
<p>Komenda powinna wypisać numer wersji. Jeśli widzisz „command not found”, zajrzyj do <a href="#problemy">rozwiązywania problemów</a>.</p>
</section>

<section id="inne-aplikacje">
<h2>Inne aplikacje i agenci AI</h2>
<h3>Claude Code</h3>
<p>Jedna komenda w terminalu (wymaga <a href="#uv">uv</a>):</p>
{term("claude mcp add deweloperuch -- uvx deweloperuch-mcp")}
<h3>Dowolna aplikacja obsługująca lokalne serwery MCP</h3>
<p>Dodaj serwer w ustawieniach MCP swojej aplikacji. W Claude Desktop bez rozszerzenia jest to plik <code>claude_desktop_config.json</code>; inne aplikacje przyjmują to samo polecenie i argumenty:</p>
{term(CONFIG_JSON, label="claude_desktop_config.json", prompt=False)}
<h3>Agent AI w terminalu: skill</h3>
<p>Skill to gotowa instrukcja dla agenta kodującego (np. Claude Code, Codex, Cursor): podpowiada mu, których komend <code>deweloperuch</code> użyć i jak czytać wyniki. Instalacja wymaga Node.js:</p>
{term("npx skills add MBratkowski/deweloperuch-mcp")}
<h3>Samo CLI</h3>
<p>Komendę <code>deweloperuch</code> możesz uruchomić bez instalowania albo zainstalować na stałe:</p>
{term("uvx --from deweloperuch-mcp deweloperuch --help")}
{term("uv tool install deweloperuch-mcp")}
</section>

<section id="pierwsze-pytania">
<h2>Pierwsze pytania</h2>
<p>Pisz tak, jak pytałbyś znajomego, który zna rynek. Kilka pytań na dobry początek:</p>
<ul class="examples">
<li>„Znajdź mieszkania 40–55 m² na krakowskim Podgórzu do 800 000 zł, od najtańszych za metr.”</li>
<li>„Czy ta oferta to okazja? Pokaż historię ceny, najniższą cenę z 30 i 90 dni i czy obniżka nie wygląda podejrzanie.”</li>
<li>„Co buduje ten deweloper i w których miastach ma aktywne oferty?”</li>
<li>„Jak ceny ofertowe w Gdańsku mają się do cen transakcyjnych z ostatnich kwartałów?”</li>
<li>„Ile mniej więcej jest wart 55-metrowy lokal pod tym adresem, na podstawie transakcji w okolicy?”</li>
<li>„Uszereguj miasta według ceny transakcyjnej za m² i pokaż obok wynagrodzenia i bezrobocie.”</li>
</ul>
<div class="note"><p><strong>Dwie wskazówki.</strong> Serwis nie ma danych o liczbie pokoi ani piętrze, więc zawężaj wyniki metrażem i ceną. Pytaj konkretnie (miasto, metraż, budżet): deweloperuch.pl to projekt społeczny, a wąskie pytania oszczędzają serwis i dają lepsze odpowiedzi.</p></div>
</section>

<section id="ceny">
<h2>Trzy rodzaje cen</h2>
<p>Narzędzia rozróżniają trzy rodzaje cen. Mówią o różnych rzeczach, więc asystent dostaje instrukcję, żeby ich nie mieszać:</p>
<ul>
<li><strong style="color: #C4B5FD">Ceny ofertowe</strong>: ile deweloperzy chcą dziś za mieszkania na rynku pierwotnym, z historią zmian cen.</li>
<li><strong style="color: #FDE68A">Ceny transakcyjne</strong>: za ile mieszkania naprawdę się sprzedały, według Rejestru Cen Nieruchomości (RCN). Najnowsze okresy bywają niepełne.</li>
<li><strong style="color: #7DD3FC">Dane oficjalne</strong>: statystyki GUS i NBP, czyli tło rynku: ceny, mieszkania oddane do użytkowania, wynagrodzenia i bezrobocie.</li>
</ul>
</section>

<section id="prompty">
<h2>Gotowe prompty</h2>
<p>Prompty to gotowe polecenia, które wybierasz w aplikacji zamiast pisać pytanie od zera. Każdy prowadzi asystenta przez właściwe narzędzia w dobrej kolejności. Same niczego nie pobierają.</p>
{prompts_section()}
</section>

<section id="narzedzia">
<h2>Narzędzia</h2>
<p>Z narzędzi korzysta asystent, nie Ty: wystarczy, że zadasz pytanie. Ta lista przyda się, gdy chcesz wiedzieć, o co można zapytać. Opisy pochodzą prosto z serwera.</p>
{tools_section()}
</section>

<section id="cli">
<h2>Komendy w terminalu</h2>
<p>Każde narzędzie ma odpowiednik w komendzie <code>deweloperuch</code>. Dodaj <code>--help</code> do dowolnej komendy, żeby zobaczyć jej opcje, a <code>--json</code>, żeby dostać wynik jako JSON.</p>
<div class="table-wrap"><table><thead><tr><th scope="col">Komenda</th><th scope="col">Co robi</th></tr></thead><tbody>{CLI_TABLE}</tbody></table></div>
</section>

<section id="limity">
<h2>Limity i blokada STOP</h2>
<p>Serwer jest „grzecznym klientem”: wysyła najwyżej jedno zapytanie na sekundę, jedno naraz, wspólnie dla wszystkich programów na Twoim komputerze, i pamięta odpowiedzi przez krótki czas.</p>
<p>Gdy serwis odpowie odmową (błąd 401, 403, 429) albo pokaże zabezpieczenie typu captcha, serwer włącza <strong>blokadę STOP</strong> i przestaje wysyłać zapytania. Asystent zobaczy wtedy komunikat ze słowem STOP i powód.</p>
<h3>Co zrobić przy blokadzie STOP</h3>
<ol>
<li>Sprawdź przyczynę:
{term("uvx --from deweloperuch-mcp deweloperuch stop status")}</li>
<li>Jeśli to chwilowe ograniczenie (429), odczekaj wskazany czas.</li>
<li>Gdy przyczyna jest jasna, zdejmij blokadę:
{term("uvx --from deweloperuch-mcp deweloperuch stop clear")}</li>
<li>Uruchom ponownie aplikację z asystentem: serwer po blokadzie potrzebuje restartu.</li>
</ol>
<div class="note warn"><p><strong>Nie usuwaj katalogu <code>~/.cache/deweloperuch-mcp</code></strong> ani plików w nim. To obchodzi blokadę bez sprawdzenia przyczyny. Używaj komendy <code>stop clear</code>.</p></div>
</section>

<section id="problemy">
<h2>Rozwiązywanie problemów</h2>
<h3>„command not found: uvx”</h3>
<p>uv nie jest zainstalowany albo terminal go jeszcze nie widzi. Zainstaluj <a href="#uv">uv</a> i otwórz nowe okno terminala. Aplikację z asystentem uruchom ponownie, żeby też go zobaczyła.</p>
<h3>Pierwsze uruchomienie trwa długo</h3>
<p>To normalne: za pierwszym razem pobierane są potrzebne pliki. Trwa to zwykle około minuty, kolejne starty są szybkie.</p>
<h3>Asystent nie widzi narzędzi</h3>
<p>Uruchom aplikację ponownie i sprawdź, czy rozszerzenie albo serwer <code>deweloperuch</code> jest włączony w jej ustawieniach. W Claude Desktop na Macu dzienniki serwerów MCP są w katalogu <code>~/Library/Logs/Claude/</code> (pliki <code>mcp-server-*.log</code>).</p>
<h3>„Następne zapytanie do deweloperuch.pl wolno wysłać dopiero za N s…”</h3>
<p>Serwer pilnuje limitu zapytań. Odczekaj podany czas i zapytaj ponownie.</p>
<h3>Wyniki są obcięte</h3>
<p>Serwis pozwala przejrzeć najwyżej 20 stron wyników. Zamiast prosić o kolejne strony, zawęź pytanie: miasto, dzielnica, metraż, budżet.</p>
<h3>Windows</h3>
<p>Nie jest jeszcze obsługiwany: współdzielony limit zapytań korzysta z mechanizmu blokad plików, którego Windows nie ma.</p>
<h3>Nadal nie działa?</h3>
<p><a href="{REPO}/issues">Zgłoś problem na GitHubie</a>. Opisz, jak instalowałeś serwer, w jakiej aplikacji i jaki komunikat widzisz.</p>
</section>

<section id="prywatnosc">
<h2>Prywatność</h2>
<p>Serwer działa na Twoim komputerze i łączy się wyłącznie z deweloperuch.pl, i to tylko po dane do odczytu. Nie zbiera danych o Tobie i niczego nie wysyła autorom projektu.</p>
<p>Twoje pytania trafiają do dostawcy Twojego asystenta AI na jego zasadach. Do deweloperuch.pl serwer wysyła tylko parametry potrzebne do pobrania danych (np. miasto, metraż, budżet).</p>
<p>Pełna dokumentacja techniczna (po angielsku) jest <a href="{REPO}#readme">w repozytorium na GitHubie</a>.</p>
</section>
</div>
</div>
</main>

<footer class="docs-footer">
<div class="wrap">
<p>Nieoficjalny projekt społeczności, niepowiązany z deweloperuch.pl, GUS ani NBP. Dane bez gwarancji poprawności; to nie jest porada inwestycyjna. Licencja MIT.</p>
<p><a href="../">Strona główna</a> · <a href="{REPO}">GitHub</a> · <a href="https://pypi.org/project/deweloperuch-mcp/">PyPI</a></p>
</div>
</footer>
</div>
</body>
</html>
"""

(ROOT / "docs").mkdir(exist_ok=True)
(ROOT / "docs/index.html").write_text(PAGE)
print("docs/index.html", len(PAGE))

# The landing page carries the same prompt between markers in its installation section.
LANDING_START, LANDING_END = "<!-- agent-prompt:start -->", "<!-- agent-prompt:end -->"
landing_block = f"""{LANDING_START}
<div class="agent-card agent-card--landing">
<div class="agent-copy">
<p class="eyebrow">Na skróty</p>
<h3>Niech AI zainstaluje to za Ciebie.</h3>
<p>Masz agenta AI z dostępem do terminala, np. Claude Code? Skopiuj prompt i wklej go agentowi: sprawdzi system, doinstaluje uv i doda serwer do Twojej aplikacji, pytając o zgodę przed zmianą konfiguracji.</p>
</div>
{term(AGENT_PROMPT, label="Prompt dla agenta AI", prompt=False)}
</div>
{LANDING_END}"""
index = (ROOT / "index.html").read_text()
if LANDING_START in index:
    head, rest = index.split(LANDING_START, 1)
    index = head + landing_block + rest.split(LANDING_END, 1)[1]
else:
    start = index.index('<section id="instalacja"')
    end = index.index("</section>", start)
    index = index[:end] + landing_block + "\n" + index[end:]
(ROOT / "index.html").write_text(index)
print("index.html agent prompt synced")
