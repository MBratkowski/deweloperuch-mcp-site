"""The reviewer's evidence job: what the factory proves about a pull request again, without a secret.

Three phases, each uploaded as its own artifact before the next starts (factory-review.yml, job
`evidence`), so nothing that runs in a later phase can touch what an earlier one wrote:

decisive (`gather_decisive`), in which no Python code from the pull request or from an agent runs:
- the fence and the copy guard over base..head, run from this checkout of main (never the head's code);
- a revert (`factory:revert`) gets only the exact-revert check: base..head must be exactly the
  inverse of the merge commit the issue's trusted `factory:merged` marker names;
- the behavioural check (R43): the BASE commit's tests/e2e (Chromium) and tests/visual (`-m visual`)
  run in pytest sessions of their own, once on a pure worktree of the base and once on a worktree of
  the base with only the head's published site files and visual baselines copied in. Every test that
  passed on the pure base must be collected and pass against the head site; one that does not runs
  once more in a fresh head-site session (R46), and a pass there is recorded as flaked once. The
  head's tests/ and tools/ never run in these sessions, so whatever they do cannot rewire them;
- the issue's browser reproduction (its probe groups) on a local build of the head must no longer
  find the defect;
- the neighbourhood: the probe on the head and on the base for the issue's pages across every width
  of its engines; a group on the head whose fingerprint the base does not have is new.

script (`gather_script`): the issue's AI-review reproduction script, downloaded by verify's rules,
runs against a local build of the head and must say NOT REPRODUCED. An agent wrote it, so it runs
only after the decisive evidence has left the job (R45), and what this phase writes is a claim.

proof (`gather_proof`), which runs the head's code: its own new tests (the `factory:tests` marker)
fail on a worktree of the base with only their test files taken from the head and pass on a worktree
of the head; a flaky issue's test runs 50 times under 4x CPU throttling on the base (at least one
failure) and on the head (none). The proof is a claim: `load_evidence` reads its verdicts from the data.
"""

from __future__ import annotations

import http.client
import json
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import ExitStack
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from tools.factory import check_drafts
from tools.factory.config import Config
from tools.factory.evidence import redact
from tools.factory.fence import FenceReport, GitError, check_fence
from tools.factory.findings import Cell, Group
from tools.factory.fix import (
    BASELINE_DIR,
    FLAKY_RUNS,
    FLAKY_THROTTLE,
    RENDERER,
    VISUAL_TESTS,
    FixError,
    check_test_id,
    _changed_test_files,
    _copy_from,
    _git_bytes,
    _pytest,
    _scrubbed_environment,
    _tail,
    clean_environment,
    git,
    is_ancestor,
    worktree,
)
from tools.factory.fix_publish import marked_test_ids
from tools.factory.issues import DraftError, groups_in, script_in
from tools.factory.stress import StressError, StressResult, stress
from tools.factory.verify import download_script, pages_changed, script_url_ok
from tools.package_site import PUBLIC_DIRECTORIES, PUBLIC_FILES

if TYPE_CHECKING:
    from tools.factory.review import Route

# The base's own suites, as validate.yml runs them; `-m visual` overrides the project's addopts.
SUITES: dict[str, tuple[str, ...]] = {
    "e2e": ("tests/e2e", "--browser", "chromium"),
    "visual": ("tests/visual", "-m", "visual", "--browser", "chromium"),
}
# A rerun names its test ids itself; this expression keeps the visual ones `-m visual` selected.
RERUN_MARKERS = "visual or not visual"
# pytest exit codes of a base session that ran: all passed, some failed, nothing collected.
CLEAN_BASE_CODES = frozenset({0, 1, 5})
# Each of the four sessions (the base and the head site, e2e and visual) and the rerun stops after
# this long; factory-review.yml budgets the evidence job's timeout for all of them (a test pins it).
SESSION_TIMEOUT_SECONDS: float = 900
SESSIONS_PER_REVIEW = 5
MAX_REASONS = 20
CI_CHECKS = frozenset({"flaky", "deploy-stale"})
ENGINE_DIFF = "engine-diff"
DECISIVE_FILE = "decisive.json"
SCRIPT_FILE = "script.json"
PROOF_FILE = "proof.json"
_BASELINE_PNG = re.compile(re.escape(BASELINE_DIR) + r"[^/]+\.png")
_SHA = re.compile(r"[0-9a-f]{40}")
_STATUS_RANK = {"passed": 0, "skipped": 1, "failed": 2, "error": 3}
_HEAD_SITE_VERB = {"failed": "failed", "error": "errored", "skipped": "skipped"}

SessionRunner = Callable[[Path, str, Path], int]
RerunRunner = Callable[[Path, Sequence[str], Path], int]
StressFn = Callable[[str, int, int, Path], StressResult]
FetchScript = Callable[[str], bytes]
RunScript = Callable[[str, str], int]


def _short(sha: str) -> str:
    return sha[:7] if _SHA.fullmatch(sha or "") else "?"


def _sha(value: str, what: str) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise ValueError(f"{what} is not a commit SHA: {value!r}")
    return value


# --- test sessions and their JUnit reports -------------------------------------------------------


def junit_results(path: Path) -> dict[str, str] | None:
    """Every testcase of a pytest JUnit report as `classname::name` -> passed, failed, error or
    skipped (the worse result when one id appears twice); None when the report is missing or broken."""
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return None
    results: dict[str, str] = {}
    for case in root.iter("testcase"):
        classname, name = case.get("classname") or "", case.get("name") or ""
        test_id = f"{classname}::{name}" if classname else name
        tags = {child.tag for child in case}
        status = next((s for tag, s in (("error", "error"), ("failure", "failed"), ("skipped", "skipped")) if tag in tags), "passed")
        if _STATUS_RANK[status] >= _STATUS_RANK.get(results.get(test_id, "passed"), 0):
            results[test_id] = status
    return results


def _status(status: str | None) -> str:
    return "not collected" if status is None else _HEAD_SITE_VERB.get(status, status)


def compare_sessions(
    base: Mapping[str, str], head: Mapping[str, str], rerun: Mapping[str, str] | None = None
) -> list[str]:
    """Why the head site loses a test the base passed: every test id that passed on the pure base
    must be collected and pass in the head-site session, or pass in `rerun` (the fresh head-site
    session that ran only the lost ids once more). Tests only the head session has, and tests the
    base did not pass, are not part of the check."""
    reasons = []
    for test_id, status in base.items():
        if status != "passed":
            continue
        other = head.get(test_id)
        if other == "passed":
            continue
        if rerun is not None and rerun.get(test_id) == "passed":
            continue  # flaked once: accepted, and recorded by the caller
        what = f"{_status(other)} on the head site"
        if rerun is not None:
            what += f" and {_status(rerun.get(test_id))} on the rerun"
        reasons.append(f"{what}: {test_id}")
    if len(reasons) > MAX_REASONS:
        reasons = [*reasons[:MAX_REASONS], f"and {len(reasons) - MAX_REASONS} more"]
    return reasons


def _session(tree: Path, arguments: Sequence[str], report: Path) -> int:
    """One pytest session in `tree` without any credential in its environment; the JUnit report goes
    to `report`, the output's tail next to it. Returns pytest's exit code (-1 after the timeout)."""
    command = [sys.executable, "-m", "pytest", *arguments, "-q", "-p", "no:cacheprovider", f"--junitxml={report}"]
    env = {**clean_environment(), "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        done = subprocess.run(
            command, cwd=tree, env=env, capture_output=True, text=True, timeout=SESSION_TIMEOUT_SECONDS, check=False
        )
    except subprocess.TimeoutExpired as error:
        output = error.stdout.decode(errors="replace") if isinstance(error.stdout, bytes) else error.stdout or ""
        report.with_suffix(".log").write_text(_tail(output) + "\n(timed out)\n", encoding="utf-8")
        return -1
    report.with_suffix(".log").write_text(_tail(done.stdout + done.stderr) + "\n", encoding="utf-8")
    return done.returncode


def run_session(tree: Path, suite: str, report: Path) -> int:
    """One pytest session of a base worktree's suite (SUITES)."""
    return _session(tree, SUITES[suite], report)


def rerun_session(tree: Path, nodes: Sequence[str], report: Path) -> int:
    """One pytest session of only the named tests, end-to-end and visual alike, in Chromium."""
    return _session(tree, [*nodes, "-m", RERUN_MARKERS, "--browser", "chromium"], report)


def node_id(tree: Path, test_id: str) -> str | None:
    """The pytest node id of a JUnit `classname::name` id in `tree`: the longest dotted prefix of the
    classname that names a module there, then the classes, then the name. None when none does."""
    classname, separator, name = test_id.partition("::")
    if not separator or not classname or not name:
        return None
    parts = classname.split(".")
    if {"", ".", ".."} & set(parts) or "/" in classname or "\\" in classname:
        return None
    for end in range(len(parts), 0, -1):
        path = "/".join(parts[:end]) + ".py"
        if (tree / path).is_file():
            return "::".join([path, *parts[end:], name])
    return None


# --- the head's site in a base worktree ---------------------------------------------------------


def _tree_entries(repo: Path, commit: str, paths: Sequence[str]) -> Iterator[tuple[str, str, str, str]]:
    """(mode, type, object, path) of every file under `paths` in `commit`."""
    literal = [f":(literal){path}" for path in paths]
    output = _git_bytes(repo, "ls-tree", "-r", "-z", commit, "--", *literal).decode(errors="surrogateescape")
    for entry in output.split("\0"):
        if not entry:
            continue
        meta, path = entry.split("\t", 1)
        mode, kind, sha = meta.split()
        yield mode, kind, sha, path


def install_head_site(repo: Path, head: str, tree: Path) -> None:
    """Replace the published site files of a base worktree with the head's (the files and trees
    tools/package_site.py publishes) and its visual baseline images with the head's PNG files.
    Nothing else from the head lands: not its tests, its tools or the baselines' renderer.json.
    Symbolic links and submodules never land."""
    for name in PUBLIC_FILES:
        (tree / name).unlink(missing_ok=True)
    for name in PUBLIC_DIRECTORIES:
        shutil.rmtree(tree / name, ignore_errors=True)
    for image in (tree / BASELINE_DIR).glob("*.png"):
        image.unlink()
    paths = [*PUBLIC_FILES, *PUBLIC_DIRECTORIES, BASELINE_DIR.rstrip("/")]
    for mode, kind, sha, path in _tree_entries(repo, head, paths):
        if kind != "blob" or mode == "120000":
            continue
        if path.startswith(BASELINE_DIR) and not _BASELINE_PNG.fullmatch(path):
            continue
        parts = path.split("/")
        if {"", ".", ".."} & set(parts) or "\\" in path:
            continue
        target = tree.joinpath(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_git_bytes(repo, "cat-file", "blob", sha))


def changed_baselines(repo: Path, base: str, head: str) -> tuple[str, ...]:
    """The visual baseline images that differ between the base and the head."""
    names = _git_bytes(repo, "diff", "--name-only", "--no-renames", "-z", base, head, "--", BASELINE_DIR)
    return tuple(name for name in names.decode(errors="replace").split("\0") if _BASELINE_PNG.fullmatch(name))


@dataclass(frozen=True)
class Behaviour:
    """The behavioural check (R43): the base's tests against the head's site. `flaked` lists the
    tests that failed (or went missing) in the head-site session and passed in the rerun."""

    green: bool
    reasons: tuple[str, ...]
    base_passed: int
    head_passed: int
    changed_baselines: tuple[str, ...]
    flaked: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "reasons": list(self.reasons),
            "changed_baselines": list(self.changed_baselines),
            "flaked": list(self.flaked),
        }

    @classmethod
    def from_json(cls, data: Any) -> Behaviour:
        top = _mapping(data, "behaviour")
        return cls(
            green=_flag(top, "green"),
            reasons=_texts(top, "reasons"),
            base_passed=_count(top, "base_passed"),
            head_passed=_count(top, "head_passed"),
            changed_baselines=_texts(top, "changed_baselines"),
            flaked=_texts(top, "flaked"),
        )


def behaviour_check(
    repo: Path, base: str, head: str, out: Path, run: SessionRunner = run_session, rerun: RerunRunner = rerun_session
) -> Behaviour:
    """Run the base's suites on a pure base worktree and on a base worktree with the head's site, in
    separate sessions, one after the other; compare what passed. The base-passing tests the head
    site loses run once more in a fresh head-site session (R46): a pass there is accepted and
    recorded in `flaked`, anything else keeps the check red."""
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    reasons: list[str] = []
    base_results: dict[str, str] = {}
    head_results: dict[str, str] = {}
    with worktree(repo, base) as pure, worktree(repo, base) as mixed:
        install_head_site(repo, head, mixed)
        for suite, arguments in SUITES.items():
            if not (pure / arguments[0]).is_dir():
                continue
            report = out / f"base-{suite}.xml"
            code = run(pure, suite, report)
            results = junit_results(report)
            if code not in CLEAN_BASE_CODES or results is None:
                reasons.append(f"the base's {suite} tests did not run cleanly (pytest exit code {code})")
            base_results.update(results or {})
            report = out / f"head-site-{suite}.xml"
            run(mixed, suite, report)
            head_results.update(junit_results(report) or {})
    passed = [test_id for test_id, status in base_results.items() if status == "passed"]
    if not passed and not reasons:
        reasons.append("no test passed on the base: the behavioural check proves nothing")
    lost = [test_id for test_id in passed if head_results.get(test_id) != "passed"]
    again: dict[str, str] | None = None
    if lost and not reasons:  # a base that did not run cleanly is red anyway: no rerun
        again = _rerun_lost(repo, base, head, lost, out / "head-site-rerun.xml", rerun)
    flaked = tuple(test_id for test_id in lost if again is not None and again.get(test_id) == "passed")
    reasons += compare_sessions(base_results, head_results, again)
    head_passed = sum(1 for test_id in passed if head_results.get(test_id) == "passed" or test_id in flaked)
    return Behaviour(
        not reasons, tuple(reasons), len(passed), head_passed, changed_baselines(repo, base, head), flaked
    )


def _rerun_lost(repo: Path, base: str, head: str, lost: Sequence[str], report: Path, rerun: RerunRunner) -> dict[str, str]:
    """The lost tests once more, in a fresh worktree of the base with the head's site: the base's
    tests, the head's site, this checkout's code. Results by JUnit id; {} when none could run."""
    with worktree(repo, base) as fresh:
        install_head_site(repo, head, fresh)
        nodes = [node for node in (node_id(fresh, test_id) for test_id in lost) if node is not None]
        if not nodes:
            return {}
        rerun(fresh, nodes, report)
    return junit_results(report) or {}


def export_baselines(repo: Path, base: str, head: str, names: Sequence[str], out: Path) -> None:
    """Each changed baseline as it is on the base and on the head, for the reviewer to compare."""
    for path in names:
        stem = path.rsplit("/", 1)[-1].removesuffix(".png")
        for side, commit in (("base", base), ("head", head)):
            try:
                data = _git_bytes(repo, "show", f"{commit}:{path}")
            except FixError:
                continue  # added or removed by the change
            target = out / "baselines" / f"{stem}-{side}.png"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)


# --- an exact revert --------------------------------------------------------------------------------


def _file_change(repo: Path, a: str, b: str, path: str) -> tuple[Any, ...]:
    """One file's change from `a` to `b`, independent of where its lines sit: the mode lines, then
    the removed and added lines of each hunk (no context, no line numbers), or for a binary file the
    exact blob ids."""
    text = _git_bytes(
        repo, "diff", "--no-renames", "--no-ext-diff", "--no-textconv", "--no-color", "-U0", "--full-index",
        "--binary", a, b, "--", f":(literal){path}",
    ).decode(errors="surrogateescape")
    header: list[str] = []
    index = ""
    hunks: list[tuple[list[str], list[str]]] = []
    last: list[str] | None = None
    for line in text.split("\n"):
        if not hunks and not line.startswith("@@"):
            if line.startswith("index "):
                index = line.split()[1]
            elif line.startswith(("new file mode", "deleted file mode", "old mode", "new mode")):
                header.append(line)
            elif line.startswith(("GIT binary patch", "Binary files")):
                return (tuple(header), "binary", index)
            continue
        if line.startswith("@@"):
            hunks.append(([], []))
            last = None
        elif line.startswith("-"):
            last = hunks[-1][0]
            last.append(line[1:])
        elif line.startswith("+"):
            last = hunks[-1][1]
            last.append(line[1:])
        elif line.startswith("\\") and last is not None:
            last.append(line)  # "No newline at end of file" belongs to the line before it
    return (tuple(header), "text", tuple((tuple(removed), tuple(added)) for removed, added in hunks))


def _changes(repo: Path, a: str, b: str) -> dict[str, tuple[Any, ...]]:
    names = _git_bytes(repo, "diff", "--name-only", "--no-renames", "-z", a, b).decode(errors="surrogateescape")
    return {path: _file_change(repo, a, b, path) for path in names.split("\0") if path}


def exact_revert(repo: Path, base: str, head: str, merged: str | None) -> tuple[bool, str]:
    """(True, "") when base..head is exactly the inverse of the squash merge `merged`: the same files,
    the same lines removed and added in each (wherever they sit now), the same modes, the same binary
    blobs. Otherwise (False, why)."""
    if not merged or not _SHA.fullmatch(merged):
        return False, "no merge commit to compare with: the issue has no trusted factory:merged marker"
    try:
        parents = git(repo, "rev-list", "--parents", "-n", "1", merged).split()[1:]
    except FixError:
        return False, f"the merge commit {merged[:7]} is not in this repository"
    if len(parents) != 1:
        return False, f"the merge commit {merged[:7]} has {len(parents)} parents; only a squash merge is reverted"
    expected = _changes(repo, merged, parents[0])
    actual = _changes(repo, base, head)
    if expected == actual:
        return True, ""
    differ = sorted(path for path in expected.keys() | actual.keys() if expected.get(path) != actual.get(path))
    shown = ", ".join(differ[:10]) + (f" and {len(differ) - 10} more" if len(differ) > 10 else "")
    return False, f"not the exact inverse of {merged[:7]}: {shown}"


# --- the head's own tests -------------------------------------------------------------------------


@dataclass(frozen=True)
class HeadProof:
    """Red on the base and green on the head for the pull request's own tests; `red` and `green`
    map each test id to a verdict line (`failed: …`, `passed: …` or `error: …`)."""

    head: str
    base: str
    flaky: bool
    red: dict[str, str]
    green: dict[str, str]
    reasons: tuple[str, ...]
    logs: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.reasons

    def to_json(self) -> dict[str, Any]:
        return {
            "head": self.head,
            "base": self.base,
            "flaky": self.flaky,
            "red": dict(self.red),
            "green": dict(self.green),
            "reasons": list(self.reasons),
            "logs": dict(self.logs),
        }

    @classmethod
    def from_json(cls, data: Any) -> HeadProof:
        """Read a proof written by the job that ran the head's code: check every type."""
        top = _mapping(data, "proof")
        return cls(
            head=_text(top, "head"),
            base=_text(top, "base"),
            flaky=_flag(top, "flaky"),
            red=_text_map(top, "red"),
            green=_text_map(top, "green"),
            reasons=_texts(top, "reasons"),
            logs=_text_map(top, "logs"),
        )


def verdict_reasons(test_ids: Sequence[str], red: Mapping[str, str], green: Mapping[str, str]) -> list[str]:
    """Why the verdicts do not show red on the base and green on the head, read from the data."""
    reasons = []
    for test_id in test_ids:
        before, after = red.get(test_id, "not run"), green.get(test_id, "not run")
        if not before.startswith("failed"):
            reasons.append(f"not red on the base: {test_id}: {before}")
        if not after.startswith("passed"):
            reasons.append(f"not green on the head: {test_id}: {after}")
    return reasons


def prove_head_tests(
    repo: Path, base: str, head: str, test_ids: Sequence[str], *, flaky: bool, stress_fn: StressFn | None = None
) -> HeadProof:
    """The pull request's own tests: red on the base, green on the head (or the statistical proof
    for a flaky issue), each in a worktree, with no credential in the environment."""
    _sha(base, "the base")
    _sha(head, "the head")
    if not test_ids:
        return HeadProof(head, base, flaky, {}, {}, ("the pull request names no test (factory:tests)",))
    with _scrubbed_environment():
        if flaky:
            return _stressed(repo, base, head, test_ids, stress_fn or stress)
        return _red_green(repo, base, head, test_ids)


def _red_green(repo: Path, base: str, head: str, test_ids: Sequence[str]) -> HeadProof:
    changed = _changed_test_files(repo, base, head)
    runnable = [t for t in test_ids if t.split("::", 1)[0] in changed]
    red: dict[str, str] = {}
    green: dict[str, str] = {}
    logs: dict[str, str] = {}
    with worktree(repo, base) as tree:
        for path in dict.fromkeys(t.split("::", 1)[0] for t in runnable):
            _copy_from(repo, head, tree, path)
        if any(t.startswith(VISUAL_TESTS) for t in runnable):
            # A visual test on the base compares against the baselines the change renders.
            for path in [*changed_baselines(repo, base, head), RENDERER]:
                try:
                    _copy_from(repo, head, tree, path)
                except FixError:
                    pass  # not in the head: the test reports the missing baseline itself
        for test_id in test_ids:
            if test_id not in runnable:
                red[test_id] = "error: not in a test file that this change adds or modifies"
                continue
            run = _pytest([test_id], tree)
            red[test_id] = run.verdict
            logs[f"base {test_id}"] = run.tail
    with worktree(repo, head) as tree:
        for test_id in test_ids:
            run = _pytest([test_id], tree)
            green[test_id] = run.verdict
            logs[f"head {test_id}"] = run.tail
    return HeadProof(head, base, False, red, green, tuple(verdict_reasons(test_ids, red, green)), logs)


def _stress_all(test_ids: Sequence[str], tree: Path, stress_fn: StressFn, side: str, logs: dict[str, str]) -> dict[str, str]:
    verdicts = {}
    for test_id in test_ids:
        try:
            result = stress_fn(test_id, FLAKY_RUNS, FLAKY_THROTTLE, tree)
        except StressError as error:
            verdicts[test_id] = f"error: {str(error).splitlines()[0]}"
            logs[f"{side} {test_id}"] = _tail(str(error))
            continue
        verdict = "failed" if result.failures else "passed"
        verdicts[test_id] = f"{verdict}: {result.failures} of {result.runs} throttled runs failed"
        logs[f"{side} {test_id}"] = _tail(result.first_failure)
    return verdicts


def _stressed(repo: Path, base: str, head: str, test_ids: Sequence[str], stress_fn: StressFn) -> HeadProof:
    logs: dict[str, str] = {}
    with worktree(repo, base) as tree:
        red = _stress_all(test_ids, tree, stress_fn, "base", logs)
    green: dict[str, str] = {}
    if all(verdict.startswith("failed") for verdict in red.values()):
        with worktree(repo, head) as tree:
            green = _stress_all(test_ids, tree, stress_fn, "head", logs)
    return HeadProof(head, base, True, red, green, tuple(verdict_reasons(test_ids, red, green)), logs)


# --- the browser side: reproduction and neighbourhood ---------------------------------------------


class Lab(Protocol):
    """Local builds of commits and a browser to measure them."""

    def site(self, sha: str) -> str: ...

    def reproduce(self, group: Group, url: str) -> int: ...

    def probe(self, cells: Sequence[Cell], url: str, out: Path) -> list[Group]: ...


class PlaywrightLab:
    """Worktrees of commits packaged and served on 127.0.0.1, and one Playwright driver, until the
    `with` block ends. The probe's own guard lets only 127.0.0.1 through and stubs Umami."""

    def __init__(self, repo: Path) -> None:
        self.repo = repo
        self.stack = ExitStack()
        self.sites: dict[str, str] = {}
        self._playwright: Any = None

    def __enter__(self) -> PlaywrightLab:
        return self

    def __exit__(self, *exc: object) -> None:
        self.stack.close()

    def site(self, sha: str) -> str:
        from tools.factory.probe import local_site

        if sha not in self.sites:
            tree = self.stack.enter_context(worktree(self.repo, sha))
            self.sites[sha] = self.stack.enter_context(local_site(tree))
        return self.sites[sha]

    @property
    def playwright(self) -> Any:
        if self._playwright is None:
            from playwright.sync_api import sync_playwright

            self._playwright = self.stack.enter_context(sync_playwright())
        return self._playwright

    def reproduce(self, group: Group, url: str) -> int:
        from tools.factory.probe import reproduce

        return reproduce(self.playwright, group, url)

    def probe(self, cells: Sequence[Cell], url: str, out: Path) -> list[Group]:
        from tools.factory.probe import Target, run_matrix

        groups = run_matrix(self.playwright, Target("review", url), cells, out)
        # Keep the findings and each group's images; the full-page shots would only bloat the artifact.
        for name in ("shots", "sheets"):
            shutil.rmtree(out / name, ignore_errors=True)
        return groups


def _describe(group: Group) -> str:
    return f"{group.check} {group.fingerprint} ({group.page}, {', '.join(group.engines)})"


def original_reproduction(groups: Sequence[Group], script: bool, head_url: str, lab: Lab) -> tuple[int, str]:
    """(0, why) when the issue's browser reproduction no longer finds the defect on the head, (1, why)
    when it still does, (2, why) when it cannot tell. The reproduction script (`script`: the issue
    has one) runs in a phase of its own after this evidence is uploaded; a flaky issue's proof is
    statistical (the proof phase), so its CI groups have no browser reproduction."""
    browser = [g for g in groups if g.check not in CI_CHECKS]
    parts: list[tuple[int, str]] = []
    for group in browser:
        try:
            code = lab.reproduce(group, head_url)
        except ValueError:
            code = 2
        parts.append((code, _describe(group)))
    if not parts:
        if script:
            return 0, "no probe groups: the reproduction script decides, in the script phase"
        if groups and all(g.check == "flaky" for g in groups):
            return 0, "a flaky test: the statistical proof stands in for a browser reproduction"
        return 2, "the issue carries no reproduction (no probe groups and no script)"
    still = [what for code, what in parts if code == 1]
    if still:
        return 1, f"{'; '.join(still)} still reproduces on the head"
    failed = [what for code, what in parts if code != 0]
    if failed:
        return 2, f"the reproduction of {'; '.join(failed)} failed on the head"
    return 0, "the browser reproduction no longer finds the defect on the head"


def _script_code(
    url: str, head_url: str, repo_name: str, name: str, fetch_script: FetchScript, run_script: RunScript, root: Path
) -> int:
    """The AI-review script, downloaded by verify's rules (raw.githubusercontent.com of this
    repository, no redirect, no credential) and run in isolated mode against the head site."""
    if not script_url_ok(url, repo_name):
        return 2
    try:
        source = fetch_script(url)
    except (OSError, ValueError, http.client.HTTPException):
        return 2
    relative = f"factory-out/repros/{name}.py"
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(source)
    with _scrubbed_environment():
        return run_script(relative, head_url)


def neighbourhood_cells(groups: Sequence[Group], changed: Sequence[str], config: Config) -> list[Cell]:
    """The issue's pages across every width (and mode) of its engines; without probe groups, the
    pages the change affects on every engine."""
    from tools.factory.probe import build_cells

    browser = [g for g in groups if g.check not in CI_CHECKS and g.page in config.site.pages]
    if browser:
        pages = sorted({g.page for g in browser})
        wanted = {e for g in browser for e in g.engines}
        if any(g.check == ENGINE_DIFF for g in browser):
            wanted.add("chromium")  # engine differences are measured against Chromium
        engines = tuple(e for e in config.matrix.engines if e in wanted)
    else:
        pages = sorted(pages_changed(changed))
        engines = config.matrix.engines
    if not pages or not engines:
        return []
    matrix = replace(
        config.matrix, engines=engines, mode_engines=tuple(e for e in config.matrix.mode_engines if e in engines)
    )
    return build_cells(matrix, pages)


def new_groups(lab: Lab, cells: Sequence[Cell], base_url: str, head_url: str, out: Path) -> list[Group]:
    """Groups the probe finds on the head and not on the base, by fingerprint."""
    head_groups = lab.probe(cells, head_url, out / "probe-head")
    known = {g.fingerprint for g in lab.probe(cells, base_url, out / "probe-base")}
    return [g for g in head_groups if g.fingerprint not in known]


# --- the decisive phase -------------------------------------------------------------------------


@dataclass(frozen=True)
class Decisive:
    pr: int
    issue: int
    base: str
    head: str
    revert: bool
    fence: FenceReport | None
    exact_revert: bool | None
    behaviour: Behaviour | None
    original: int
    original_reason: str
    new_groups: tuple[dict[str, Any], ...]
    reasons: tuple[str, ...]
    # The issue carries a reproduction script: the script phase's result must say NOT REPRODUCED.
    script: bool = False

    def problems(self) -> list[str]:
        """Why this is not green: the recorded reasons, and a generic one should a recorded field
        disagree with them (the fields decide, not the list)."""
        if self.revert:
            holds = self.exact_revert is True
        else:
            holds = (
                self.fence is not None
                and self.fence.ok
                and self.behaviour is not None
                and self.behaviour.green
                and self.original == 0
                and not self.new_groups
            )
        if self.reasons:
            return list(self.reasons)
        return [] if holds else ["the decisive evidence is not green"]

    @property
    def green(self) -> bool:
        return not self.problems()

    def to_json(self) -> dict[str, Any]:
        return {
            "pr": self.pr,
            "issue": self.issue,
            "base": self.base,
            "head": self.head,
            "revert": self.revert,
            "green": self.green,
            "fence": asdict(self.fence) if self.fence is not None else None,
            "exact_revert": self.exact_revert,
            "behaviour": self.behaviour.to_json() if self.behaviour is not None else None,
            "original": self.original,
            "original_reason": self.original_reason,
            "new_groups": list(self.new_groups),
            "reasons": list(self.reasons),
            "script": self.script,
        }

    @classmethod
    def from_json(cls, data: Any) -> Decisive:
        top = _mapping(data, "decisive")
        fence = top.get("fence")
        behaviour = top.get("behaviour")
        exact = top.get("exact_revert")
        if exact is not None and not isinstance(exact, bool):
            raise ValueError("decisive.exact_revert must be true, false or null")
        groups = top.get("new_groups")
        if not isinstance(groups, list) or not all(isinstance(g, dict) for g in groups):
            raise ValueError("decisive.new_groups must be a list of objects")
        return cls(
            pr=_count(top, "pr"),
            issue=_count(top, "issue"),
            base=_sha(_text(top, "base"), "decisive.base"),
            head=_sha(_text(top, "head"), "decisive.head"),
            revert=_flag(top, "revert"),
            fence=_fence(fence) if fence is not None else None,
            exact_revert=exact,
            behaviour=Behaviour.from_json(behaviour) if behaviour is not None else None,
            original=_count(top, "original"),
            original_reason=_text(top, "original_reason"),
            new_groups=tuple(groups),
            reasons=_texts(top, "reasons"),
            script=_flag(top, "script"),
        )


def _issue_groups(issue_body: str) -> tuple[list[Group], str | None]:
    try:
        return groups_in(issue_body), None
    except (ValueError, KeyError, TypeError):
        return [], "the issue's groups marker is unreadable"


def gather_decisive(
    repo: Path,
    route: Route,
    issue_body: str,
    config: Config,
    out: Path,
    *,
    lab: Lab | None = None,
    run: SessionRunner = run_session,
    rerun: RerunRunner = rerun_session,
) -> Decisive:
    """Everything that decides, with no Python code from the pull request or an agent running (see
    the module docstring). `issue_body` counts only when the route found its author trusted."""
    base, head = _sha(route.base, "the base"), _sha(route.head, "the head")
    out.mkdir(parents=True, exist_ok=True)
    reasons: list[str] = []
    if base == head or not is_ancestor(repo, base, head):
        reasons.append(f"{_short(head)} does not build on the base {_short(base)}")
    try:
        fence: FenceReport | None = check_fence(repo, base, head, config.fence, config.limits)
    except GitError as error:
        fence = None
        reasons.append(f"the fence could not run: {error}")
    common: dict[str, Any] = {
        "pr": route.pr or 0, "issue": route.issue or 0, "base": base, "head": head, "revert": route.revert, "fence": fence
    }
    if route.revert:
        exact, why = exact_revert(repo, base, head, route.merged)
        if not exact:
            reasons.append(why)
        return Decisive(**common, exact_revert=exact, behaviour=None, original=0,
                        original_reason="a revert: only the exact inverse of the merge counts", new_groups=(),
                        reasons=tuple(reasons))
    if fence is not None and not fence.ok:
        reasons += [f"fence: {reason}" for reason in fence.reasons]
    behaviour = behaviour_check(repo, base, head, out / "sessions", run, rerun)
    reasons += behaviour.reasons
    export_baselines(repo, base, head, behaviour.changed_baselines, out)
    groups, problem = _issue_groups(issue_body)
    script = script_in(issue_body) is not None
    found: list[Group] = []
    original, original_reason = 2, problem or "not run"
    with ExitStack() as stack:
        lab = lab or stack.enter_context(PlaywrightLab(repo))
        try:
            if problem is None:
                original, original_reason = original_reproduction(groups, script, lab.site(head), lab)
            changed = [p for p in _git_bytes(repo, "diff", "--name-only", "--no-renames", "-z", base, head)
                       .decode(errors="replace").split("\0") if p]
            cells = neighbourhood_cells(groups, changed, config)
            if cells:
                found = new_groups(lab, cells, lab.site(base), lab.site(head), out)
        except Exception as error:  # a build or a browser failure is infrastructure, never green
            reasons.append(f"the browser checks failed: {type(error).__name__}: {redact(str(error))[:300]}")
    if original != 0:
        reasons.append(original_reason)
    reasons += [f"new finding on the head: {_describe(g)}" for g in found]
    return Decisive(**common, exact_revert=None, behaviour=behaviour, original=original, original_reason=original_reason,
                    new_groups=tuple(g.to_json() for g in found), reasons=tuple(reasons), script=script)


# --- the script phase -----------------------------------------------------------------------------


@dataclass(frozen=True)
class ScriptResult:
    """The issue's reproduction script on the head: `code` 0 (NOT REPRODUCED), 1 (REPRODUCED) or 2
    (cannot tell); `ran` is false when the issue carries no script. Written in the job where the
    script ran, so it is a claim."""

    pr: int
    issue: int
    base: str
    head: str
    ran: bool
    code: int
    reason: str

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: Any) -> ScriptResult:
        top = _mapping(data, "script")
        code = _count(top, "code")
        if code not in (0, 1, 2):
            raise ValueError("script.code must be 0, 1 or 2")
        return cls(
            pr=_count(top, "pr"),
            issue=_count(top, "issue"),
            base=_sha(_text(top, "base"), "script.base"),
            head=_sha(_text(top, "head"), "script.head"),
            ran=_flag(top, "ran"),
            code=code,
            reason=_text(top, "reason"),
        )


_SCRIPT_REASONS = {
    0: "the reproduction script no longer finds the defect on the head",
    1: "the reproduction script still finds the defect on the head",
    2: "the reproduction script could not tell on the head (a refused or failed download, an error or a timeout)",
}


def gather_script(
    repo: Path,
    route: Route,
    issue_body: str,
    config: Config,
    *,
    lab: Lab | None = None,
    fetch_script: FetchScript = download_script,
    run_script: RunScript = check_drafts.run_script,
    script_root: Path = check_drafts.ROOT,
) -> ScriptResult:
    """The AI-review reproduction script of a trusted issue against a local build of the head. It
    runs after the decisive evidence has been uploaded: whatever it does cannot reach that."""
    base, head = _sha(route.base, "the base"), _sha(route.head, "the head")
    common: dict[str, Any] = {"pr": route.pr or 0, "issue": route.issue or 0, "base": base, "head": head}
    url = None if route.revert else script_in(issue_body)
    if url is None:
        return ScriptResult(**common, ran=False, code=0, reason="the issue carries no reproduction script")
    with ExitStack() as stack:
        lab = lab or stack.enter_context(PlaywrightLab(repo))
        try:
            site = lab.site(head)
        except Exception as error:  # a build failure is infrastructure, never green
            reason = f"the head could not be built: {type(error).__name__}: {redact(str(error))[:300]}"
            return ScriptResult(**common, ran=True, code=2, reason=reason)
        code = _script_code(url, site, config.site.repo, f"review-{route.issue}", fetch_script, run_script, script_root)
    code = code if code in (0, 1) else 2
    return ScriptResult(**common, ran=True, code=code, reason=_SCRIPT_REASONS[code])


# --- the proof phase and the whole -------------------------------------------------------------------


def flaky_selectors(issue_body: str) -> list[str]:
    """The test ids the issue's flaky groups name (a flaky job's selector is not a test id)."""
    groups, _ = _issue_groups(issue_body)
    ids = []
    for group in groups:
        if group.check != "flaky":
            continue
        try:
            ids.append(check_test_id(group.selector, "selector"))
        except DraftError:
            continue
    return ids


def gather_proof(
    repo: Path, route: Route, issue_body: str, pr_body: str, *, stress_fn: StressFn | None = None
) -> HeadProof:
    """The head's own tests (this runs the pull request's code). A revert has none."""
    base, head = _sha(route.base, "the base"), _sha(route.head, "the head")
    if route.revert:
        return HeadProof(head, base, False, {}, {}, ("not applicable: a revert is proven by the exact-revert check",))
    try:
        test_ids = marked_test_ids(pr_body)
    except ValueError:
        return HeadProof(head, base, route.flaky, {}, {}, ("the factory:tests marker is unreadable",))
    proof = prove_head_tests(repo, base, head, test_ids, flaky=route.flaky, stress_fn=stress_fn)
    missing = [s for s in flaky_selectors(issue_body) if route.flaky and s not in test_ids]
    if missing:
        proof = replace(proof, reasons=(*proof.reasons, *(f"the pull request does not name the flaky test {s}" for s in missing)))
    return proof


@dataclass(frozen=True)
class Evidence:
    """What `post` and the reviewer agent decide on: the decisive evidence, the script's result and
    the proof together. `original` combines the browser reproduction and the script; `notes` holds
    what was accepted with a remark (a test that flaked once in the behavioural check)."""

    green: bool
    red_green: dict[str, Any]
    original: int
    new_groups: list[dict[str, Any]]
    fence: FenceReport | None
    exact_revert: bool | None
    pr: int = 0
    head: str = ""
    base: str = ""
    revert: bool = False
    reasons: tuple[str, ...] = ()
    changed_baselines: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


def _script_reasons(decisive: Decisive, script: ScriptResult | None, missing: str) -> tuple[int, list[str]]:
    """The script phase's code and why it is not green, for an issue that carries a script."""
    if script is None:
        return 2, [missing]
    if (script.pr, script.head, script.base) != (decisive.pr, decisive.head, decisive.base):
        return 2, ["the reproduction script's result is for another commit"]
    if not script.ran:
        return 2, ["the reproduction script did not run, though the issue carries one"]
    return script.code, [] if script.code == 0 else [_SCRIPT_REASONS[script.code]]


def evaluate(
    decisive: Decisive, proof: HeadProof | None, *, revert: bool, tests: Sequence[str], flaky: bool,
    flaky_tests: Sequence[str] = (), missing_proof: str = "no proof of the head's own tests",
    script: ScriptResult | None = None, script_required: bool = False,
    missing_script: str = "no result of the reproduction script",
) -> Evidence:
    """Combine the phases; the proof's verdicts are read from its data, never from its own reasons
    alone, and must cover the tests the pull request names, for its head and base. A flaky issue's
    pull request must name the issue's flaky tests (`flaky_tests`, from the trusted issue). When the
    issue carries a reproduction script (the decisive evidence says so, or `script_required`, from
    the trusted issue), the script phase must have run it on this head and found nothing."""
    reasons = decisive.problems()
    original = decisive.original
    if flaky and not revert:
        reasons += [f"the pull request does not name the flaky test {t}" for t in flaky_tests if t not in tests]
    if decisive.revert != revert:
        reasons.append("the evidence is for another kind of pull request")
    if not revert and (decisive.script or script_required):
        code, problems = _script_reasons(decisive, script, missing_script)
        original = original or code
        reasons += problems
    red_green: dict[str, Any] = {}
    if not revert:
        if proof is None:
            reasons.append(missing_proof)
        else:
            red_green = {"flaky": proof.flaky, "red": dict(proof.red), "green": dict(proof.green), "reasons": list(proof.reasons)}
            if (proof.head, proof.base) != (decisive.head, decisive.base):
                reasons.append("the proof of the head's tests is for another commit")
            if proof.flaky != flaky:
                reasons.append("the proof of the head's tests is of the wrong kind (flaky or not)")
            if not tests:
                reasons.append("the pull request names no test (factory:tests)")
            reasons += verdict_reasons(tests, proof.red, proof.green)
            # The proof's own reasons come from the job that ran the head's code: they count, but
            # their text never reaches a review (see proof.json for it).
            if any(r not in reasons for r in proof.reasons):
                reasons.append("the proof of the head's tests reports further problems (see proof.json)")
    behaviour = decisive.behaviour
    flaked = behaviour.flaked if behaviour is not None else ()
    return Evidence(
        green=not reasons,
        red_green=red_green,
        original=original,
        new_groups=list(decisive.new_groups),
        fence=decisive.fence,
        exact_revert=decisive.exact_revert,
        pr=decisive.pr,
        head=decisive.head,
        base=decisive.base,
        revert=decisive.revert,
        reasons=tuple(dict.fromkeys(reasons)),
        changed_baselines=behaviour.changed_baselines if behaviour is not None else (),
        notes=(f"flaked once: {', '.join(flaked)}",) if flaked else (),
    )


def gather_evidence(
    repo: Path, route: Route, issue_body: str, pr_body: str, config: Config, out: Path, **hooks: Any
) -> Evidence:
    """The three phases in one process (tests and local runs; the workflow uploads between them)."""
    stress_fn = hooks.pop("stress_fn", None)
    script_hooks = {name: hooks.pop(name) for name in ("fetch_script", "run_script", "script_root") if name in hooks}
    decisive = gather_decisive(repo, route, issue_body, config, out / "decisive", **hooks)
    script = gather_script(repo, route, issue_body, config, lab=hooks.get("lab"), **script_hooks)
    proof = gather_proof(repo, route, issue_body, pr_body, stress_fn=stress_fn)
    try:
        tests = marked_test_ids(pr_body)
    except ValueError:
        tests = []
    return evaluate(
        decisive, proof, revert=route.revert, tests=tests, flaky=route.flaky, flaky_tests=flaky_selectors(issue_body),
        script=script, script_required=script_in(issue_body) is not None,
    )


def load_evidence(
    decisive_path: Path, proof_path: Path, *, revert: bool, tests: Sequence[str], flaky: bool,
    flaky_tests: Sequence[str] = (), script_path: Path | None = None, script_required: bool = False,
) -> Evidence:
    """The evidence artifacts as `post` reads them. Unreadable decisive evidence leaves no head to
    post for; an unreadable script result or proof is simply not green."""
    try:
        decisive = Decisive.from_json(json.loads(_read(decisive_path)))
    except (OSError, ValueError, KeyError, TypeError) as error:
        reason = f"the decisive evidence is unreadable: {type(error).__name__}"
        return Evidence(False, {}, 2, [], None, None, reasons=(reason,))
    proof: HeadProof | None = None
    missing = "no proof of the head's own tests"
    if not revert:
        try:
            proof = HeadProof.from_json(json.loads(_read(proof_path)))
        except (OSError, ValueError, KeyError, TypeError) as error:
            missing = f"the proof of the head's tests is unreadable: {type(error).__name__}"
    script: ScriptResult | None = None
    missing_script = "no result of the reproduction script"
    if script_path is not None and not revert:
        try:
            script = ScriptResult.from_json(json.loads(_read(script_path)))
        except (OSError, ValueError, KeyError, TypeError) as error:
            missing_script = f"the reproduction script's result is unreadable: {type(error).__name__}"
    return evaluate(
        decisive, proof, revert=revert, tests=tests, flaky=flaky, flaky_tests=flaky_tests, missing_proof=missing,
        script=script, script_required=script_required, missing_script=missing_script,
    )


def _read(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise FileNotFoundError(str(path))
    return path.read_text(encoding="utf-8")


# --- strict readers for the artifacts -------------------------------------------------------------


def _mapping(data: Any, what: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError(f"{what} must be an object")
    return data


def _text(data: Mapping[str, Any], name: str) -> str:
    value = data.get(name)
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value


def _flag(data: Mapping[str, Any], name: str) -> bool:
    value = data.get(name)
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be true or false")
    return value


def _count(data: Mapping[str, Any], name: str) -> int:
    value = data.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a whole number")
    return value


def _texts(data: Mapping[str, Any], name: str) -> tuple[str, ...]:
    value = data.get(name)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{name} must be a list of strings")
    return tuple(value)


def _text_map(data: Mapping[str, Any], name: str) -> dict[str, str]:
    value = data.get(name)
    if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        raise ValueError(f"{name} must map strings to strings")
    return dict(value)


def _fence(data: Any) -> FenceReport:
    top = _mapping(data, "fence")
    return FenceReport(
        ok=_flag(top, "ok"),
        reasons=list(_texts(top, "reasons")),
        changed_lines=_count(top, "changed_lines"),
        copy_changed=_flag(top, "copy_changed"),
        review=list(_texts(top, "review")),
    )
