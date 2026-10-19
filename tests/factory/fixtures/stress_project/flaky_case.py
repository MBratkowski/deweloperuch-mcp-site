"""A test that fails on every third run; the run count lives in the file named by STRESS_COUNTER_FILE.

Deliberately not named test_*.py so the main suite never collects it; the stress tests pass its path to
pytest explicitly."""

import os
from pathlib import Path


def test_flaky() -> None:
    counter = Path(os.environ["STRESS_COUNTER_FILE"])
    count = int(counter.read_text()) + 1 if counter.exists() else 1
    counter.write_text(str(count))
    assert count % 3 != 0, f"flaky failure on run {count}"


def test_setup_error(missing_fixture_that_raises) -> None:  # noqa: ANN001
    raise AssertionError("never reached")
