"""Re-run every draft's reproduction on the live site and on a build of main; no write token."""

from __future__ import annotations

import argparse
import json
import os
import pwd
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from collections.abc import Callable, Collection, Mapping, Sequence
from pathlib import Path
from typing import Any

from tools.factory.config import load_config
from tools.factory.findings import Group
from tools.factory.issues import (
    SCRIPT_PATTERN,
    Draft,
    DraftError,
    Dropped,
    canonical_groups,
    load_drafts,
    rebuild_groups,
)

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_TIMEOUT_SECONDS = 120
# The unprivileged account agent scripts run as, when set (the check and verify jobs set `pwuser`,
# the Playwright image's user, D40): no root /proc of the job's processes, no Docker socket.
SCRIPT_USER_ENV = "FACTORY_SCRIPT_USER"
# Checks whose defect is a fact about CI, not about a page: verified from the same run's ci_facts.
CI_CHECKS = frozenset({"flaky", "deploy-stale"})

FIXED = "fixed on main, not deployed"
GONE = "does not reproduce on the live site"
ERROR = "reproduction error"
NOT_IN_CI = "not present in this run's CI facts"

RunScript = Callable[[str, str], int]
RunGroup = Callable[[Group, str], int]


REPRODUCED = re.compile(r"^REPRODUCED: .+$", re.MULTILINE)
NOT_REPRODUCED = re.compile(r"^NOT REPRODUCED$", re.MULTILINE)


class ScriptUserError(RuntimeError):
    """FACTORY_SCRIPT_USER names an account the scripts cannot be run as."""


def script_user() -> tuple[int, int] | None:
    """(uid, gid) of FACTORY_SCRIPT_USER, or None when it is not set. Raises ScriptUserError when
    the account is unknown, is root, or this process cannot switch to it (only root can)."""
    name = os.environ.get(SCRIPT_USER_ENV, "")
    if not name:
        return None
    try:
        entry = pwd.getpwnam(name)
    except KeyError:
        raise ScriptUserError(f"{SCRIPT_USER_ENV} names no account: {name!r}") from None
    if entry.pw_uid == 0 or entry.pw_gid == 0:
        raise ScriptUserError(f"{SCRIPT_USER_ENV} must name an unprivileged account, not {name!r}")
    if os.geteuid() != 0:
        raise ScriptUserError(f"only root can run the scripts as {name!r}")
    return entry.pw_uid, entry.pw_gid


def _launch(home: Path | None, user: tuple[int, int] | None) -> dict[str, object]:
    """Popen options for a script: as `user`, with no supplementary group and a fresh HOME of its own
    (browsers need a writable one), or unchanged when no user is configured."""
    if user is None or home is None:
        return {}
    return {
        "user": user[0],
        "group": user[1],
        "extra_groups": [],
        "env": {**os.environ, "HOME": str(home)},
    }


def run_script(script: str, url: str) -> int:
    """Run an agent's reproduction script in isolated mode.

    1 only for exit 1 plus a `REPRODUCED:` line, 0 only for exit 0 plus `NOT REPRODUCED`;
    2 for anything else (other codes, a traceback, a missing line, a timeout, a bad path).

    The script runs in a session of its own, and its whole process group is killed once it exits
    or times out, so nothing it started outlives it. Output goes to files, not pipes, so a child
    holding them open cannot stall the wait. With FACTORY_SCRIPT_USER set, it runs as that
    unprivileged account (D40); an account it cannot switch to fails closed (2, nothing runs).
    """
    if not SCRIPT_PATTERN.fullmatch(script) or ".." in script:
        return 2
    try:
        user = script_user()
    except ScriptUserError as error:
        print(f"warning: {script} not run: {error}", file=sys.stderr)
        return 2
    home = Path(tempfile.mkdtemp(prefix="factory-script-home-")) if user else None
    try:
        if home is not None and user is not None:
            os.chown(home, user[0], user[1])
        return _run_script(script, url, _launch(home, user))
    finally:
        if home is not None:
            shutil.rmtree(home, ignore_errors=True)


def _run_script(script: str, url: str, options: dict[str, object]) -> int:
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        try:
            process = subprocess.Popen(  # type: ignore[call-overload]
                [sys.executable, "-I", script, "--base-url", url],
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=err,
                start_new_session=True,
                **options,
            )
        except OSError:
            return 2
        try:
            returncode: int | None = process.wait(timeout=SCRIPT_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            returncode = None
        finally:
            _kill_group(process.pid)
            process.wait()
        out.seek(0)
        err.seek(0)
        stdout = out.read().decode("utf-8", errors="replace")
        stderr = err.read().decode("utf-8", errors="replace")
    if returncode is None or "Traceback (most recent call last)" in stderr:
        return 2
    if returncode == 1 and REPRODUCED.search(stdout) and not NOT_REPRODUCED.search(stdout):
        return 1
    if returncode == 0 and NOT_REPRODUCED.search(stdout) and not REPRODUCED.search(stdout):
        return 0
    return 2


def _kill_group(pgid: int) -> None:
    """SIGKILL every process left in the script's process group (its id is the script's pid)."""
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _is_ci(draft: Draft) -> bool:
    groups = draft.reproduction.groups
    return bool(groups) and all(g.get("check") in CI_CHECKS for g in groups)


def _outcome(draft: Draft, url: str, scripts: RunScript, groups: RunGroup) -> int:
    """1 when every part of the reproduction finds the defect at `url`, 0 when one does not, 2 on error."""
    reproduction = draft.reproduction
    try:
        if reproduction.kind == "script":
            assert reproduction.script is not None
            return scripts(reproduction.script, url)
        results = [groups(Group.from_json(data), url) for data in reproduction.groups]
    except (ValueError, KeyError, TypeError):
        return 2
    if any(code not in (0, 1) for code in results):
        return 2
    return 1 if all(code == 1 for code in results) else 0


def _reason(
    draft: Draft,
    live_url: str,
    main_url: str,
    scripts: RunScript,
    groups: RunGroup,
    ci_fingerprints: Collection[str] | None,
) -> str | None:
    """Why the draft does not survive, or None when it does."""
    if _is_ci(draft):
        return None if set(draft.fingerprints) <= set(ci_fingerprints or ()) else NOT_IN_CI
    live = _outcome(draft, live_url, scripts, groups)
    if live == 2:
        return ERROR
    if live == 0:
        return GONE
    main = _outcome(draft, main_url, scripts, groups)
    if main == 2:
        return ERROR
    return FIXED if main == 0 else None


def check(
    drafts: Sequence[Draft],
    live_url: str,
    main_url: str,
    run_script: RunScript,
    run_group: RunGroup,
    ci_fingerprints: Collection[str] | None = None,
) -> tuple[list[Draft], list[Dropped]]:
    """A draft survives only when its reproduction exits 1 on the live site and on main."""
    kept: list[Draft] = []
    dropped: list[Dropped] = []
    for draft in drafts:
        reason = _reason(draft, live_url, main_url, run_script, run_group, ci_fingerprints)
        if reason is None:
            kept.append(draft)
        else:
            dropped.append(Dropped(draft.fingerprints[0], reason))
    return kept, dropped


def build_validated(
    incoming: dict[str, Any],
    live_url: str,
    main_url: str,
    run_script: RunScript,
    run_group: RunGroup,
    ci_fingerprints: Collection[str] | None,
    suggestions_per_run: int,
    *,
    canonical: Mapping[str, dict[str, Any]],
) -> dict[str, Any]:
    """The validated.json payload: surviving draft dicts, capped suggestions, all dropped.

    Only the probe's own groups reach it: the agent's group objects are data it could have edited.
    """
    load_drafts(incoming)
    raw_drafts, unknown = rebuild_groups(incoming["drafts"], canonical)
    drafts, _, _ = load_drafts({**incoming, "drafts": raw_drafts})
    kept, new_dropped = check(drafts, live_url, main_url, run_script, run_group, ci_fingerprints)
    survivors = {id(d) for d in kept}
    return {
        "drafts": [raw for draft, raw in zip(drafts, raw_drafts) if id(draft) in survivors],
        "suggestions": incoming["suggestions"][:suggestions_per_run],
        "dropped": [
            *incoming["dropped"],
            *({"fingerprint": d.fingerprint, "reason": d.reason} for d in [*unknown, *new_dropped]),
        ],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tools.factory.check_drafts")
    parser.add_argument("--drafts", type=Path, required=True, help="the QA agent's drafts.json")
    parser.add_argument("--live", required=True, help="base URL of the live site")
    parser.add_argument("--main-root", type=Path, required=True, help="checkout of main to build and serve")
    parser.add_argument("--out", type=Path, required=True, help="validated.json to write")
    parser.add_argument(
        "--probe-findings", type=Path, required=True, help="findings.json of this run's live probe"
    )
    parser.add_argument("--ci-findings", type=Path, help="findings.json from this run's ci_facts")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = load_config()
    try:
        incoming = json.loads(args.drafts.read_text(encoding="utf-8"))
        load_drafts(incoming)
        canonical = canonical_groups(args.probe_findings, args.ci_findings)
        ci_fingerprints = set(canonical_groups(args.ci_findings)) if args.ci_findings else None
        from playwright.sync_api import sync_playwright

        from tools.factory.probe import local_site, reproduce

        with sync_playwright() as playwright, local_site(args.main_root) as main_url:
            result = build_validated(
                incoming,
                args.live,
                main_url,
                run_script,
                lambda group, url: reproduce(playwright, group, url),
                ci_fingerprints,
                config.limits.suggestions_per_run,
                canonical=canonical,
            )
    except (OSError, ValueError, KeyError, DraftError) as error:
        print(f"check drafts failed: {error}", file=sys.stderr)
        return 2
    except Exception as error:  # a browser or build failure is infrastructure, never a verdict
        print(f"check drafts failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"{len(result['drafts'])} kept, {len(result['dropped'])} dropped -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
