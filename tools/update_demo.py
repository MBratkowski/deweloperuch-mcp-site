"""Pure saved-demo rendering and number formatting for the offline page build.

``python3 -m tools.update_demo`` remains a compatibility alias for the complete
``python3 -m tools.build_docs`` build. Capture is separate: tools/capture_demo.py
is manual and rate-limited; rendering only reads committed data.
"""

import html
import json
import math
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from tools.html_helpers import NUMBER_WORDS, _lines, plural
from tools.release import ROOT, BuildError

DEMO_PATH = "data/demo.json"
# The window's copy (the answer, the "Obniżka" column) is written for this order; tools/capture_demo.py uses it.
SORT = "biggest_drop"
NBSP = " "
e = html.escape

# Polish formatting of the numbers in the window.


def group_thousands(value: int) -> str:
    return f"{value:,}".replace(",", NBSP)


def format_pln(value: float) -> str:
    """Whole złoty, halves rounded up (round() would round them to even)."""
    return f"{group_thousands(math.floor(value + 0.5))}{NBSP}zł"


def format_area(value: float) -> str:
    return f"{value:.2f}".replace(".", ",") + f"{NBSP}m²"


def format_change(value: float) -> str:
    return f"{value:.1f}".replace(".", ",").replace("-", "−") + "%"


def format_date(value: str) -> str:
    return date.fromisoformat(value).strftime("%d.%m.%Y")


def offers_count_word(count: int) -> str:
    """The number of offers as a word, agreeing with "oferta": jedna, dwie, trzy … pięć."""
    feminine = {1: "jedna", 2: "dwie"}
    return feminine.get(count) or (NUMBER_WORDS[count] if count < len(NUMBER_WORDS) else str(count))


def answer(demo: dict) -> str:
    total = demo["result"]["total"]
    place = demo["city_locative"][:1].upper() + demo["city_locative"][1:]
    return (
        f"{e(place)} {plural(total, 'jest', 'są', 'jest')} <strong>{group_thousands(total)}</strong> "
        f"{plural(total, 'pasująca oferta', 'pasujące oferty', 'pasujących ofert')}. "
        f"{offers_count_word(len(demo['rows'])).capitalize()} z największą obniżką od pierwszej ceny:"
    )


ROW = (
    '<tr data-step="row">'
    '<td>{name}</td>'
    '<td>{investment} · {developer}</td>'
    '<td class="numeric">{area}</td>'
    '<td class="numeric">{price}</td>'
    '<td class="offer-change numeric">{change}</td>'
    "</tr>"
)

CHAT = """
<p data-step="question" class="chat-question">{question}</p>
<p data-step="chip" class="tool-chip">
<span data-demo-role="chip-dot" class="chip-dot status-dot" aria-hidden="true"></span>
{tool} · <span data-demo-role="chip-state" class="chip-state">gotowe</span>
</p>
<p data-step="answer" class="chat-answer">{answer}</p>
<div data-step="table" class="offers-table-frame">
<div tabindex="0" role="region" aria-label="Tabela ofert" class="scroll-region">
<table class="offers-table">
<thead>
<tr>
<th scope="col">Lokal</th>
<th scope="col">Inwestycja · deweloper</th>
<th scope="col" class="numeric">Metraż</th>
<th scope="col" class="numeric">Cena</th>
<th scope="col" class="numeric">Obniżka</th>
</tr>
</thead>
<tbody>
{rows}
</tbody>
</table>
</div>
</div>
<p data-step="source" class="chat-source">Ceny ofertowe z deweloperuch.pl, stan na {date}. <a href="{list_url}" class="text-offers">Otwórz pełną listę</a></p>
<div class="composer">
<span data-demo-role="composer" class="composer-text" data-question="{question}">Pokaż historię ceny lokalu {first}…</span>
<span aria-hidden="true" class="composer-send">
<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5"></path><path d="M5 12l7-7 7 7"></path></svg>
</span>
</div>
"""

FIELD = (
    '<p data-step="{step}" class="inspector-field"><span class="inspector-key">{name}</span>'
    '<span class="inspector-value {kind}">{value}</span></p>'
)
TOOL = '<p class="inspector-tool">{tool}</p>'
RESULT_HEADING = '<p class="inspector-label">Wynik</p>'


def field(step: str, name: str, value: object) -> str:
    """One inspector line, its value as JSON and coloured by type: strings green, booleans blue, numbers purple."""
    kind = "text-accent-soft" if isinstance(value, str) else "text-official" if isinstance(value, bool) else "text-offers"
    return FIELD.format(step=step, name=e(name), kind=kind, value=e(json.dumps(value, ensure_ascii=False)))


def short_list_url(url: str) -> str:
    """'…/ceny-ofertowe/wroclaw/…': the list's kind and city, enough to tell it in the narrow inspector."""
    kind, city = urlsplit(url).path.strip("/").split("/")[:2]
    return f"…/{kind}/{city}/…"


def demo_blocks(demo: dict) -> dict[str, str]:
    """The three landing template slots for a saved capture; no I/O or requests."""
    if (sort := demo["inspector_args"].get("sort")) != SORT:
        raise BuildError(f"{DEMO_PATH} is sorted by {sort!r}; the window's copy is written for {SORT!r}: capture again")
    result = demo["result"]
    rows = [
        ROW.format(
            name=e(row["name"]),
            investment=e(row["investment_name"]),
            developer=e(row["developer_name"]),
            area=format_area(row["area_m2"]),
            price=format_pln(row["price"]),
            change=format_change(row["price_change_since_start_pct"]),
        )
        for row in demo["rows"]
    ]
    chat = CHAT.format(
        question=e(demo["question"]),
        tool=e(demo["tool"]),
        answer=answer(demo),
        rows="\n".join(rows),
        date=format_date(demo["captured_at"]),
        list_url=e(result["list_url"]),
        first=e(demo["rows"][0]["name"]),
    )
    call = [
        TOOL.format(tool=e(demo["tool"])),
        *(field("arg", name, value) for name, value in demo["inspector_args"].items()),
        RESULT_HEADING,
        *(field("result", name, result[name]) for name in ("total", "returned", "truncated")),
        field("result", "list_url", short_list_url(result["list_url"])),
    ]
    return {
        "demo_title": f"Mieszkania {e(demo['city_locative'])}",
        "demo_chat": chat,
        "demo_call": _lines(call),
    }


def main(root: Path = ROOT) -> int:
    """Compatibility entry point: use the same complete offline build."""
    from tools.build_docs import main as build_main

    return build_main(root)


if __name__ == "__main__":
    sys.exit(main())
