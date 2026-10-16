"""Render data/demo.json into the demo window of index.html.

    python3 -m tools.update_demo

Writes the blocks between ``<!-- demo-title:start -->``, ``<!-- demo-chat:start -->`` and
``<!-- demo-call:start -->`` and their ``:end`` markers: the window title, the conversation
(question, tool chip, answer, table, source line with the capture date, composer) and the
call inspector. The rest of the window is hand-written, except the sidebar's prompts and
tool groups, which tools/build_docs.py writes. data/demo.json comes from
tools/capture_demo.py; run this after each capture. CI fails when the page and the data differ.
"""

import html
import json
import math
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from tools.html_helpers import NUMBER_WORDS, _lines, fill_blocks, plural
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


CELL = "padding: 10px 12px; border-top: 1px solid #241D33"
NUMBER_CELL = CELL + "; text-align: right; white-space: nowrap"
ROW = (
    '<tr data-step="row">'
    f"<td style=\"{CELL}; font-family: 'JetBrains Mono', monospace; font-size: 12px; white-space: nowrap\">{{name}}</td>"
    f'<td style="{CELL}">{{investment}} · {{developer}}</td>'
    f'<td style="{NUMBER_CELL}">{{area}}</td>'
    f'<td style="{NUMBER_CELL}">{{price}}</td>'
    f'<td style="{NUMBER_CELL}; color: #C4B5FD; font-weight: 600">{{change}}</td>'
    "</tr>"
)

CHAT = """
<p data-step="question" style="margin: 0; align-self: flex-end; max-width: 80%; padding: 9px 14px; border-radius: 16px 16px 5px 16px; background: #2A2140">{question}</p>
<p data-step="chip" style="margin: 0; align-self: flex-start; display: inline-flex; align-items: center; gap: 8px; padding: 6px 10px; border-radius: 8px; border: 1px solid rgba(255,255,255,0.10); font-family: 'JetBrains Mono', monospace; font-size: 12px; color: #A7A1B8">
<span class="chip-dot" aria-hidden="true" style="width: 6px; height: 6px; border-radius: 50%; background: #34D399"></span>
{tool} · <span class="chip-state">gotowe</span>
</p>
<p data-step="answer" style="margin: 0">{answer}</p>
<div data-step="table" style="border-radius: 10px; overflow: hidden; border: 1px solid rgba(255,255,255,0.08)">
<div style="overflow-x: auto" tabindex="0" role="region" aria-label="Tabela ofert">
<table style="width: 100%; border-collapse: collapse; font-size: 13px; font-variant-numeric: tabular-nums">
<thead>
<tr style="background: #1A1426; text-align: left; font-size: 11px; color: #A7A1B8">
<th scope="col" style="padding: 8px 12px; font-weight: 600">Lokal</th>
<th scope="col" style="padding: 8px 12px; font-weight: 600">Inwestycja · deweloper</th>
<th scope="col" style="padding: 8px 12px; font-weight: 600; text-align: right">Metraż</th>
<th scope="col" style="padding: 8px 12px; font-weight: 600; text-align: right">Cena</th>
<th scope="col" style="padding: 8px 12px; font-weight: 600; text-align: right">Obniżka</th>
</tr>
</thead>
<tbody>
{rows}
</tbody>
</table>
</div>
</div>
<p data-step="source" style="margin: 0; font-size: 12px; color: #A7A1B8">Ceny ofertowe z deweloperuch.pl, stan na {date}. <a href="{list_url}" style="color: #C4B5FD">Otwórz pełną listę</a></p>
<div style="display: flex; align-items: center; gap: 10px; margin-top: 6px; padding: 6px 6px 6px 14px; border-radius: 12px; background: #1A1426; border: 1px solid rgba(255,255,255,0.08)">
<span class="composer-text" data-question="{question}" style="flex: 1; color: #A7A1B8; font-size: 13px">Pokaż historię ceny lokalu {first}…</span>
<span aria-hidden="true" style="flex: none; width: 44px; height: 44px; border-radius: 9px; background: #34D399; color: #052E21; display: inline-flex; align-items: center; justify-content: center">
<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5"></path><path d="M5 12l7-7 7 7"></path></svg>
</span>
</div>
"""

FIELD = (
    '<p data-step="{step}" style="margin: 0; display: flex; gap: 14px"><span style="color: #A7A1B8; min-width: 84px">{name}</span>'
    '<span style="color: {color}; overflow-wrap: anywhere">{value}</span></p>'
)
TOOL = '<p style="margin: 0; font-size: 13px; color: #F1EEF7">{tool}</p>'
RESULT_HEADING = '<p style="margin: 6px 0 0; font-size: 10px; letter-spacing: 0.1em; text-transform: uppercase; color: #A7A1B8">Wynik</p>'


def field(step: str, name: str, value: object) -> str:
    """One inspector line, its value as JSON and coloured by type: strings green, booleans blue, numbers purple."""
    color = "#6EE7B7" if isinstance(value, str) else "#7DD3FC" if isinstance(value, bool) else "#C4B5FD"
    return FIELD.format(step=step, name=e(name), color=color, value=e(json.dumps(value, ensure_ascii=False)))


def short_list_url(url: str) -> str:
    """'…/ceny-ofertowe/wroclaw/…': the list's kind and city, enough to tell it in the narrow inspector."""
    kind, city = urlsplit(url).path.strip("/").split("/")[:2]
    return f"…/{kind}/{city}/…"


def demo_blocks(demo: dict) -> dict[str, str]:
    """The generated parts of the demo window for ``demo`` (data/demo.json), by marker name."""
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
        "demo-title": f"Mieszkania {e(demo['city_locative'])}",
        "demo-chat": chat,
        "demo-call": _lines(call),
    }


def render(index: str, demo: dict) -> str:
    """``index`` (index.html) with its demo window rendered from ``demo``."""
    return fill_blocks(index, demo_blocks(demo))


def main(root: Path = ROOT) -> int:
    demo = json.loads((root / DEMO_PATH).read_text(encoding="utf-8"))
    try:
        index = render((root / "index.html").read_text(encoding="utf-8"), demo)
    except BuildError as error:
        print(f"tools/update_demo.py stopped:\n{error}", file=sys.stderr)
        return 1
    (root / "index.html").write_text(index, encoding="utf-8")
    print(f"index.html: the demo window rendered from {DEMO_PATH} (captured {demo['captured_at']}, {len(demo['rows'])} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
