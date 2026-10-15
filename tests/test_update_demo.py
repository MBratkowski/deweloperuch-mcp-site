"""tools/update_demo.py: the demo window of index.html rendered from data/demo.json."""

import json
import shutil
from pathlib import Path

import pytest

from tools.check_facts import check_demo, parse, squash
from tools.update_demo import format_area, format_change, format_date, format_pln, main, render

ROOT = Path(__file__).resolve().parent.parent
KRAKOW = {
    "captured_at": "2026-10-16",
    "city_locative": "w Krakowie",
    "question": "Pokaż mieszkania do 700 tys. zł w Krakowie z największą obniżką ceny.",
    "tool": "search_offers",
    "inspector_args": {"city": "krakow", "price_max": 700000, "sort": "biggest_drop"},
    "result": {
        "total": 2582,
        "returned": 10,
        "truncated": True,
        "list_url": "https://deweloperuch.pl/ceny-ofertowe/krakow/mieszkania?filterPrice=0-700000&sortBy=start_amount_difference_percent&sortOrder=asc&page=1&perPage=10",
    },
    "rows": [
        {"name": "A3/13", "investment_name": "Dom & Ogród", "developer_name": "Pierwszy S.A.",
         "area_m2": 41.5, "price": 612000.0, "price_per_m2": 14746.99, "price_change_since_start_pct": -19.04},
        {"name": "B2", "investment_name": "Nad Wisłą", "developer_name": "Drugi Sp. z o.o.",
         "area_m2": 38.0, "price": 589990.0, "price_per_m2": 15526.05, "price_change_since_start_pct": -15.5},
        {"name": "C/7", "investment_name": "Nad Wisłą", "developer_name": "Drugi Sp. z o.o.",
         "area_m2": 52.25, "price": 699000.4, "price_per_m2": 13377.99, "price_change_since_start_pct": -9.96},
    ],
}


def read(path: str, root: Path = ROOT) -> str:
    return (root / path).read_text(encoding="utf-8")


def committed_demo() -> dict:
    return json.loads(read("data/demo.json"))


def answer(page: str) -> str:
    return squash(parse(page).find_all(data_step="answer")[0].text())


def test_polish_formatting() -> None:
    assert format_pln(475200.0) == "475 200 zł"
    assert format_pln(11429.6) == "11 430 zł"
    assert format_pln(568408.5) == "568 409 zł"
    assert format_area(55.66) == "55,66 m²"
    assert format_area(57.7) == "57,70 m²"
    assert format_change(-27.59) == "−27,6%"
    assert format_date("2026-10-15") == "15.10.2026"


def test_committed_demo_is_the_render_of_its_data() -> None:
    assert render(read("index.html"), committed_demo()) == read("index.html")


def test_a_new_capture_passes_the_fact_check() -> None:
    assert check_demo(render(read("index.html"), KRAKOW), KRAKOW) == []


def test_a_new_capture_names_its_city_and_first_offer() -> None:
    page = render(read("index.html"), KRAKOW)
    assert answer(page) == "W Krakowie są 2 582 pasujące oferty. Trzy z największą obniżką od pierwszej ceny:"
    assert squash(parse(page).find_all(class_="composer-text")[0].text()) == "Pokaż historię ceny lokalu A3/13…"
    assert "Dom &amp; Ogród · Pierwszy S.A." in page


@pytest.mark.parametrize(
    "total, rows, wanted",
    [
        (2587, 5, "jest 2 587 pasujących ofert. Pięć z"),
        (2582, 3, "są 2 582 pasujące oferty. Trzy z"),
        (12, 3, "jest 12 pasujących ofert. Trzy z"),
        (22, 2, "są 22 pasujące oferty. Dwie z"),
        (1, 1, "jest 1 pasująca oferta. Jedna z"),
    ],
)
def test_the_answer_agrees_with_its_counts(total: int, rows: int, wanted: str) -> None:
    demo = committed_demo()
    demo["result"]["total"] = total
    demo["rows"] = (demo["rows"] * 5)[:rows]
    assert wanted in answer(render(read("index.html"), demo))


def test_main_writes_the_page_from_the_data(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir()
    shutil.copy(ROOT / "index.html", tmp_path / "index.html")
    (tmp_path / "data/demo.json").write_text(json.dumps(KRAKOW, ensure_ascii=False), encoding="utf-8")
    assert main(tmp_path) == 0
    assert check_demo(read("index.html", tmp_path), KRAKOW) == []


def test_main_stops_on_a_page_without_the_markers(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "data").mkdir()
    (tmp_path / "index.html").write_text("<p>no demo</p>", encoding="utf-8")
    shutil.copy(ROOT / "data/demo.json", tmp_path / "data/demo.json")
    assert main(tmp_path) == 1
    assert read("index.html", tmp_path) == "<p>no demo</p>"
    assert "demo-chat" in capsys.readouterr().err


def test_main_stops_on_data_sorted_otherwise(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "data").mkdir()
    shutil.copy(ROOT / "index.html", tmp_path / "index.html")
    demo = committed_demo()
    demo["inspector_args"]["sort"] = "price_per_m2_asc"
    (tmp_path / "data/demo.json").write_text(json.dumps(demo, ensure_ascii=False), encoding="utf-8")
    assert main(tmp_path) == 1
    assert read("index.html", tmp_path) == read("index.html")
    assert "biggest_drop" in capsys.readouterr().err
