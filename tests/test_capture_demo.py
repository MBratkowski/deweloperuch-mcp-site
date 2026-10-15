"""tools/capture_demo.py with the CLI replaced by a fake: tests never reach deweloperuch.pl."""

import json
import subprocess
from datetime import date
from pathlib import Path

import pytest

from tools.capture_demo import main

ROOT = Path(__file__).resolve().parent.parent
# The real `deweloperuch offers search warszawa --json` output; its top 3 all come from ATAL S.A.
CLI_RESULT = json.loads((ROOT / "tests/fixtures/cli_offers_search.json").read_text(encoding="utf-8"))
ARGS = [
    "warszawa",
    "--city-locative", "w Warszawie",
    "--question", "Pokaż mieszkania 40–60 m² w Warszawie z największą obniżką ceny.",
    "--area-min", "40",
    "--area-max", "60",
]
OLD_DEMO = '{"old": true}\n'


class FakeCli:
    """Stands in for subprocess.run: records each command and answers with one result."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.commands: list[list[str]] = []
        self.answer = (returncode, stdout, stderr)

    def __call__(self, command: list[str], **kwargs) -> subprocess.CompletedProcess:
        self.commands.append(command)
        returncode, stdout, stderr = self.answer
        return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr=stderr)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A site whose data/server/ pins 9.9.9 and whose data/demo.json holds an older capture."""
    (tmp_path / "data/server").mkdir(parents=True)
    (tmp_path / "data/server/version.json").write_text('{"version": "9.9.9"}\n', encoding="utf-8")
    (tmp_path / "data/demo.json").write_text(OLD_DEMO, encoding="utf-8")
    return tmp_path


def two_developers() -> dict:
    """CLI_RESULT with its second offer from another developer."""
    result = json.loads(json.dumps(CLI_RESULT))
    result["items"][1].update(developer_name="Dom Development S.A.", developer_slug="dom-development")
    return result


def test_capture_sends_one_search_through_the_pinned_cli(root: Path) -> None:
    cli = FakeCli(stdout=json.dumps(two_developers()))
    assert main(ARGS, root=root, run=cli, today=date(2026, 10, 16)) == 0
    assert cli.commands == [[
        "uvx", "--from", "deweloperuch-mcp==9.9.9", "deweloperuch", "offers", "search", "warszawa",
        "--area-min", "40", "--area-max", "60", "--sort", "biggest_drop", "--json",
    ]]


def test_capture_writes_the_top_five_with_the_call_and_the_date(root: Path) -> None:
    cli = FakeCli(stdout=json.dumps(two_developers()))
    main(ARGS, root=root, run=cli, today=date(2026, 10, 16))
    text = (root / "data/demo.json").read_text(encoding="utf-8")
    assert text.endswith("}\n") and "Targówek" in text
    assert json.loads(text) == {
        "captured_at": "2026-10-16",
        "city_locative": "w Warszawie",
        "question": "Pokaż mieszkania 40–60 m² w Warszawie z największą obniżką ceny.",
        "tool": "search_offers",
        "inspector_args": {"city": "warszawa", "area_min": 40, "area_max": 60, "sort": "biggest_drop"},
        "result": {
            "total": 13324,
            "returned": 10,
            "truncated": True,
            "list_url": "https://deweloperuch.pl/ceny-ofertowe/warszawa/mieszkania?page=1&perPage=10",
        },
        "rows": [
            {"name": "D1.1.1.4", "investment_name": "Nowy Targówek VI", "developer_name": "ATAL S.A.",
             "area_m2": 55.25, "price": 994012.0, "price_per_m2": 17991.17, "price_change_since_start_pct": -0.6},
            {"name": "D1.2.0.1", "investment_name": "Nowy Targówek VI", "developer_name": "Dom Development S.A.",
             "area_m2": 36.49, "price": 659315.0, "price_per_m2": 18068.37, "price_change_since_start_pct": -4.45},
            {"name": "D1.2.2.1", "investment_name": "Nowy Targówek VI", "developer_name": "ATAL S.A.",
             "area_m2": 66.58, "price": 1191364.0, "price_per_m2": 17893.72, "price_change_since_start_pct": -0.72},
            {"name": "D1.2.2.2", "investment_name": "Nowy Targówek VI", "developer_name": "ATAL S.A.",
             "area_m2": 59.44, "price": 1067126.0, "price_per_m2": 17952.99, "price_change_since_start_pct": -0.27},
            {"name": "D1.2.2.3", "investment_name": "Nowy Targówek VI", "developer_name": "ATAL S.A.",
             "area_m2": 56.85, "price": 1019445.0, "price_per_m2": 17932.19, "price_change_since_start_pct": -0.05},
        ],
    }


def test_filters_reach_the_cli_and_the_inspector_under_their_own_names(root: Path) -> None:
    cli = FakeCli(stdout=json.dumps(two_developers()))
    args = ["krakow", "--city-locative", "w Krakowie", "--question", "Pytanie.",
            "--district", "Podgórze", "--price-max", "700000", "--price-per-m2-max", "15000"]
    main(args, root=root, run=cli, today=date(2026, 10, 16))
    assert cli.commands[0][6:-3] == ["krakow", "--price-max", "700000", "--price-per-m2-max", "15000", "--district", "Podgórze"]
    written = json.loads((root / "data/demo.json").read_text(encoding="utf-8"))
    assert written["inspector_args"] == {
        "city": "krakow", "price_max": 700000, "price_per_m2_max": 15000, "district": "Podgórze", "sort": "biggest_drop",
    }


def test_one_developer_in_the_top_three_writes_nothing(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cli = FakeCli(stdout=json.dumps(CLI_RESULT))
    assert main(ARGS, root=root, run=cli, today=date(2026, 10, 16)) == 1
    assert (root / "data/demo.json").read_text(encoding="utf-8") == OLD_DEMO
    assert "ATAL S.A." in capsys.readouterr().err


def test_stop_writes_nothing_and_passes_the_exit_code_on(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cli = FakeCli(returncode=3, stderr="STOP: serwis odpowiedział 429.\n")
    assert main(ARGS, root=root, run=cli, today=date(2026, 10, 16)) == 3
    assert len(cli.commands) == 1
    assert (root / "data/demo.json").read_text(encoding="utf-8") == OLD_DEMO
    err = capsys.readouterr().err
    assert "serwis odpowiedział 429" in err and "STOP" in err


def test_a_failed_search_writes_nothing(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cli = FakeCli(returncode=2, stderr="Nieznane miasto.\n")
    assert main(ARGS, root=root, run=cli, today=date(2026, 10, 16)) == 2
    assert (root / "data/demo.json").read_text(encoding="utf-8") == OLD_DEMO
    assert "Nieznane miasto." in capsys.readouterr().err


@pytest.mark.parametrize(
    "field, value",
    [
        ("area_m2", None),
        ("price", None),
        ("price_change_since_start_pct", None),
        ("price_change_since_start_pct", 0.0),
        ("price_change_since_start_pct", 5.89),
    ],
)
def test_an_offer_without_a_number_or_a_drop_writes_nothing(
    root: Path, capsys: pytest.CaptureFixture[str], field: str, value: float | None
) -> None:
    result = two_developers()
    result["items"][4][field] = value
    assert main(ARGS, root=root, run=FakeCli(stdout=json.dumps(result)), today=date(2026, 10, 16)) == 1
    assert (root / "data/demo.json").read_text(encoding="utf-8") == OLD_DEMO
    assert "D1.2.2.3" in capsys.readouterr().err


def test_one_developer_under_two_names_writes_nothing(root: Path) -> None:
    result = json.loads(json.dumps(CLI_RESULT))
    result["items"][1]["developer_name"] = "ATAL Spółka Akcyjna"
    assert main(ARGS, root=root, run=FakeCli(stdout=json.dumps(result)), today=date(2026, 10, 16)) == 1
    assert (root / "data/demo.json").read_text(encoding="utf-8") == OLD_DEMO


def test_output_that_is_not_json_writes_nothing(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cli = FakeCli(stdout="Uwaga: nowa wersja.\n" + json.dumps(two_developers()))
    assert main(ARGS, root=root, run=cli, today=date(2026, 10, 16)) == 1
    assert (root / "data/demo.json").read_text(encoding="utf-8") == OLD_DEMO
    assert "not JSON" in capsys.readouterr().err


def timed_out(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    raise subprocess.TimeoutExpired(command, kwargs["timeout"])


def no_uvx(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    raise FileNotFoundError(2, "No such file or directory", "uvx")


@pytest.mark.parametrize("run, said", [(timed_out, "may have sent its request"), (no_uvx, "uvx")])
def test_a_cli_that_cannot_answer_writes_nothing(root: Path, capsys: pytest.CaptureFixture[str], run, said: str) -> None:
    assert main(ARGS, root=root, run=run, today=date(2026, 10, 16)) == 1
    assert (root / "data/demo.json").read_text(encoding="utf-8") == OLD_DEMO
    assert said in capsys.readouterr().err


def test_a_cli_killed_by_a_signal_fails_with_a_plain_code(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(ARGS, root=root, run=FakeCli(returncode=-15), today=date(2026, 10, 16)) == 1
    assert (root / "data/demo.json").read_text(encoding="utf-8") == OLD_DEMO
    assert capsys.readouterr().err.strip()
