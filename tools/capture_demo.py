"""Write data/demo.json from one search_offers query through the deweloperuch-mcp CLI.

    uv run python -m tools.capture_demo wroclaw --city-locative "we Wrocławiu" \\
        --question "Pokaż mieszkania 30–50 m² we Wrocławiu z największą obniżką ceny." \\
        --area-min 30 --area-max 50

Then run ``python3 -m tools.build_docs``. Run it by hand when the demo should be refreshed,
never in tests or CI and never in a loop: each run sends one request to deweloperuch.pl
through the package's shared limiter and cache (``uvx --from deweloperuch-mcp==<pinned>
deweloperuch offers search <city> <filters> --sort biggest_drop --json``, the release pinned in
data/server/version.json). The demo always shows the biggest drops from the first price.

Nothing is written when the CLI fails, and on STOP (exit code 3) the code is passed on: stop
and report, do not retry. Nothing is written either when an offer shown lacks an area, a price
or a drop from the first price, or when the top 3 come from fewer than two developers (by
developer slug), so the demo does not read as an advert for one company; pick another query.
The question and the city in the locative are written by hand and must say what the filters do.
"""

import argparse
import json
import subprocess
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path

from tools.release import PACKAGE, ROOT, pinned_version
from tools.update_demo import SORT

DEMO_PATH = "data/demo.json"
ROWS = 5
TOP = 3
STOP = 3
TIMEOUT = 180
# The search_offers filters the capture takes, in the order of the tool's parameters; the CLI
# names them with dashes (--price-max), the MCP tool with underscores (price_max).
FILTERS = (
    ("price_min", int, "lowest price in PLN"),
    ("price_max", int, "highest price in PLN"),
    ("area_min", int, "smallest area in m²"),
    ("area_max", int, "largest area in m²"),
    ("price_per_m2_min", int, "lowest price per m² in PLN"),
    ("price_per_m2_max", int, "highest price per m² in PLN"),
    ("district", str, "a district as `deweloperuch locations search` names it"),
)
ROW_FIELDS = ("name", "investment_name", "developer_name", "area_m2", "price", "price_per_m2", "price_change_since_start_pct")
# The fields the window shows; the server may leave them null.
SHOWN = ("area_m2", "price", "price_change_since_start_pct")

Run = Callable[..., subprocess.CompletedProcess]


def command(version: str, city: str, filters: dict) -> list[str]:
    options = [part for name, value in filters.items() for part in ("--" + name.replace("_", "-"), str(value))]
    return ["uvx", "--from", f"{PACKAGE}=={version}", "deweloperuch", "offers", "search", city, *options, "--sort", SORT, "--json"]


def to_demo(result: dict, *, city: str, filters: dict, city_locative: str, question: str, captured_at: date) -> dict:
    """data/demo.json from the CLI's ``--json`` output: the call, the result fields the inspector
    shows and the first ROWS offers."""
    return {
        "captured_at": captured_at.isoformat(),
        "city_locative": city_locative,
        "question": question,
        "tool": "search_offers",
        "inspector_args": {"city": city, **filters, "sort": SORT},
        "result": {field: result[field] for field in ("total", "returned", "truncated", "list_url")},
        "rows": [{field: item[field] for field in ROW_FIELDS} for item in result["items"][:ROWS]],
    }


def main(argv: list[str] | None = None, root: Path = ROOT, run: Run = subprocess.run, today: date | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.capture_demo", description=__doc__.split("\n\n")[0])
    parser.add_argument("city", help="the city slug, e.g. wroclaw")
    parser.add_argument("--city-locative", required=True, help='the city in the locative with its preposition, e.g. "we Wrocławiu"')
    parser.add_argument("--question", required=True, help="the user's question in the demo")
    for name, kind, text in FILTERS:
        parser.add_argument("--" + name.replace("_", "-"), dest=name, type=kind, help=text)
    args = parser.parse_args(argv)
    filters = {name: getattr(args, name) for name, _, _ in FILTERS if getattr(args, name) is not None}

    def fail(reason: str) -> int:
        print(f"{reason}; {DEMO_PATH} is unchanged.", file=sys.stderr)
        return 1

    try:
        done = run(command(pinned_version(root), args.city, filters), capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return fail(f"The CLI did not finish in {TIMEOUT} s and was stopped; it may have sent its request. "
                    "Check the service by hand before running again")
    except FileNotFoundError:
        return fail("uvx is not installed (https://docs.astral.sh/uv/)")
    if done.returncode != 0:
        if done.stderr.strip():
            print(done.stderr.rstrip(), file=sys.stderr)
        if done.returncode == STOP:
            print("STOP: the CLI stopped itself. Report it and do not retry.", file=sys.stderr)
        elif done.returncode < 0:
            print(f"The CLI was ended by signal {-done.returncode}.", file=sys.stderr)
        print(f"The CLI exited with {done.returncode}; {DEMO_PATH} is unchanged.", file=sys.stderr)
        return done.returncode if done.returncode > 0 else 1
    try:
        result = json.loads(done.stdout)
    except json.JSONDecodeError:
        return fail(f"The CLI's output is not JSON: {done.stdout[:200]!r}")

    demo = to_demo(result, city=args.city, filters=filters, city_locative=args.city_locative,
                   question=args.question, captured_at=today or date.today())
    unusable = [
        row["name"] for row in demo["rows"]
        if any(row[field] is None for field in SHOWN) or row["price_change_since_start_pct"] >= 0
    ]
    if unusable:
        return fail(f"Offers {', '.join(unusable)} lack an area, a price or a drop from the first price. Pick another query")
    top = [row["developer_name"] for row in demo["rows"][:TOP]]
    if len({item["developer_slug"] for item in result["items"][:TOP]}) < 2:
        return fail(f"The top {TOP} come from fewer than two developers ({', '.join(top) or 'no offers'}). Pick another query")
    (root / DEMO_PATH).write_text(json.dumps(demo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{DEMO_PATH} written from 1 request ({demo['result']['total']} offers, top {TOP}: {', '.join(top)}); "
          "next: python3 -m tools.build_docs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
