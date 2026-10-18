import importlib.util
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.factory import stress as stress_module
from tools.factory.stress import StressError, StressResult, main, stress

PROJECT = Path(__file__).resolve().parent / "fixtures" / "stress_project"
E2E_CONFTEST = Path(__file__).resolve().parents[1] / "e2e" / "conftest.py"
CASE = "flaky_case.py::test_flaky"


@pytest.fixture(autouse=True)
def counter_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "counter"
    monkeypatch.setenv("STRESS_COUNTER_FILE", str(path))
    return path


def test_stress_counts_the_failing_runs() -> None:
    result = stress(CASE, runs=6, throttle=4, cwd=PROJECT)
    assert isinstance(result, StressResult)
    assert (result.runs, result.failures) == (6, 2)
    assert "flaky failure on run 3" in result.first_failure


def test_stress_without_failures_has_no_first_failure() -> None:
    result = stress(CASE, runs=2, throttle=4, cwd=PROJECT)
    assert (result.failures, result.first_failure) == (0, "")


def test_a_test_id_that_collects_nothing_is_an_error() -> None:
    with pytest.raises(StressError, match="no tests ran"):
        stress("flaky_case.py::test_missing", runs=1, throttle=4, cwd=PROJECT)


def test_a_hanging_run_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(stress_module, "RUN_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(StressError, match="timed out"):
        stress(CASE, runs=1, throttle=4, cwd=PROJECT)


def test_the_cli_prints_a_summary(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--test", CASE, "--runs", "3", "--throttle", "4", "--cwd", str(PROJECT)])
    assert code == 0
    assert capsys.readouterr().out.strip() == '{"runs": 3, "failures": 1}'


def test_the_cli_exits_2_on_a_stress_error(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--test", "flaky_case.py::nope", "--runs", "1", "--throttle", "4", "--cwd", str(PROJECT)])
    assert code == 2
    assert capsys.readouterr().err


def test_the_fixture_project_is_not_collected_by_the_main_suite() -> None:
    root = PROJECT.parents[3]
    run = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/factory", "--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=root, capture_output=True, text=True, check=False,
    )
    assert run.returncode == 0, run.stdout
    assert "flaky_case" not in run.stdout
    assert "test_stress.py" in run.stdout


# The cpu_throttle fixture: its helpers are checked against a recording stand-in for the browser context.


@pytest.fixture(scope="module")
def e2e_conftest():
    spec = importlib.util.spec_from_file_location("e2e_conftest_under_test", E2E_CONFTEST)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    del sys.modules[spec.name]


class FakeContext:
    def __init__(self, pages: int = 1) -> None:
        self.pages = [object() for _ in range(pages)]
        self.sent: list[tuple[object, str, dict]] = []
        self.listeners: dict[str, list] = {}

    def new_cdp_session(self, page: object) -> SimpleNamespace:
        return SimpleNamespace(send=lambda method, params: self.sent.append((page, method, params)))

    def on(self, event: str, callback) -> None:
        self.listeners.setdefault(event, []).append(callback)


def test_throttle_rate_is_none_when_unset(e2e_conftest, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FACTORY_CPU_THROTTLE", raising=False)
    assert e2e_conftest.throttle_rate() is None


def test_throttle_rate_reads_the_integer(e2e_conftest, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FACTORY_CPU_THROTTLE", "4")
    assert e2e_conftest.throttle_rate() == 4


@pytest.mark.parametrize("value", ["abc", "1", "0", "-3", "2.5", ""])
def test_throttle_rate_rejects_invalid_values(e2e_conftest, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("FACTORY_CPU_THROTTLE", value)
    with pytest.raises(pytest.UsageError, match="FACTORY_CPU_THROTTLE"):
        e2e_conftest.throttle_rate()


def test_throttling_reaches_existing_and_later_pages(e2e_conftest) -> None:
    context = FakeContext(pages=2)
    e2e_conftest.throttle_context(context, 4)
    later = object()
    for callback in context.listeners["page"]:
        callback(later)
    expected = ("Emulation.setCPUThrottlingRate", {"rate": 4})
    assert [(method, params) for _, method, params in context.sent] == [expected] * 3
    assert [page for page, _, _ in context.sent] == [*context.pages, later]


def test_the_fixture_does_nothing_without_the_variable(e2e_conftest, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FACTORY_CPU_THROTTLE", raising=False)
    context = FakeContext()
    e2e_conftest.apply_cpu_throttle("chromium", context)
    assert context.sent == [] and context.listeners == {}


def test_the_fixture_skips_other_browsers(e2e_conftest, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FACTORY_CPU_THROTTLE", "4")
    context = FakeContext()
    e2e_conftest.apply_cpu_throttle("firefox", context)
    assert context.sent == [] and context.listeners == {}


def test_the_fixture_throttles_chromium(e2e_conftest, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FACTORY_CPU_THROTTLE", "4")
    context = FakeContext()
    e2e_conftest.apply_cpu_throttle("chromium", context)
    assert [m for _, m, _ in context.sent] == ["Emulation.setCPUThrottlingRate"]


def test_a_file_without_tests_is_reported_as_collecting_nothing(tmp_path: Path) -> None:
    (tmp_path / "empty_case.py").write_text("")
    with pytest.raises(StressError, match="collected no tests"):
        stress("empty_case.py", runs=1, throttle=4, cwd=tmp_path)


def test_a_setup_error_is_not_a_flaky_failure() -> None:
    with pytest.raises(StressError, match="errors outside the test body"):
        stress("flaky_case.py::test_setup_error", runs=1, throttle=4, cwd=PROJECT)


@pytest.mark.parametrize("flag, value", [("--runs", "0"), ("--throttle", "1")])
def test_the_cli_rejects_out_of_range_values(flag: str, value: str) -> None:
    args = ["--test", CASE, "--runs", "1", "--throttle", "4"]
    args[args.index(flag) + 1] = value
    with pytest.raises(SystemExit) as exit_info:
        main(args)
    assert exit_info.value.code == 2
