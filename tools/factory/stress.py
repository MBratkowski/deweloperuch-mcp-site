"""Prove a flaky fix statistically: run one test many times with Chromium's CPU throttled.

A flaky test cannot be made deterministically red, so it must fail at least once on the base commit and
pass every run after the fix. Every run is a separate pytest process; the throttle reaches the browser
through FACTORY_CPU_THROTTLE (see the `cpu_throttle` fixture in tests/e2e/conftest.py)."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

RUN_TIMEOUT_SECONDS: float = 300
TAIL_LINES = 40
_TESTS_FAILED = 1
_NO_TESTS_COLLECTED = 5


class StressError(RuntimeError):
    """A stress run that is not a plain pass or test failure: it proves nothing either way."""


@dataclass(frozen=True)
class StressResult:
    runs: int
    failures: int
    first_failure: str


def _tail(text: str) -> str:
    return "\n".join(text.splitlines()[-TAIL_LINES:])


def stress(test_id: str, runs: int, throttle: int, cwd: Path) -> StressResult:
    command = [sys.executable, "-m", "pytest", test_id, "--browser", "chromium", "-x", "-q", "-p", "no:cacheprovider"]
    env = {**os.environ, "FACTORY_CPU_THROTTLE": str(throttle)}
    failures = 0
    first_failure = ""
    for number in range(1, runs + 1):
        try:
            done = subprocess.run(
                command, cwd=cwd, env=env, capture_output=True, text=True,
                timeout=RUN_TIMEOUT_SECONDS, check=False,
            )
        except subprocess.TimeoutExpired as error:
            output = error.stdout.decode(errors="replace") if isinstance(error.stdout, bytes) else error.stdout or ""
            raise StressError(f"run {number} timed out after {RUN_TIMEOUT_SECONDS:g} s\n{_tail(output)}") from error
        output = done.stdout + done.stderr
        if done.returncode == 0:
            continue
        if done.returncode == _TESTS_FAILED:
            failures += 1
            first_failure = first_failure or _tail(output)
            continue
        reason = "collected no tests" if done.returncode == _NO_TESTS_COLLECTED else "did not run cleanly"
        raise StressError(f"run {number} {reason} (pytest exit code {done.returncode})\n{_tail(output)}")
    return StressResult(runs, failures, first_failure)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test", required=True, help="pytest node id")
    parser.add_argument("--runs", type=int, default=50)
    parser.add_argument("--throttle", type=int, default=4)
    parser.add_argument("--cwd", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    try:
        result = stress(args.test, args.runs, args.throttle, args.cwd)
    except StressError as error:
        print(error, file=sys.stderr)
        return 2
    print(json.dumps({"runs": result.runs, "failures": result.failures}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
