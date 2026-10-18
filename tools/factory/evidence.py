"""Publish factory evidence (screenshots, repro scripts) to an orphan branch.

GitHub's API cannot attach images to issues, so the files are pushed to the
``factory-evidence`` branch and linked by commit SHA (immutable raw URLs).

The remote URL may carry a token (``https://x-access-token:<token>@...``). It is
passed to git only as an argument and never appears in logs or exceptions: all
error text goes through :func:`redact`.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path

BRANCH = "factory-evidence"
MAX_ATTEMPTS = 3
IDENTITY = ("factory-evidence", "factory@users.noreply.github.com")

_CREDENTIALS = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://)[^/@\s]*@", re.IGNORECASE)


class EvidenceError(RuntimeError):
    """Publishing evidence failed. The message never contains credentials."""


def redact(text: str) -> str:
    """Mask ``scheme://user:token@`` credentials in any text."""
    return _CREDENTIALS.sub(r"\g<scheme>***@", text)


def _check_path(path: str) -> None:
    if (
        not path
        or "\\" in path
        or path.startswith("/")
        or any(part in ("", "..", ".") for part in path.split("/"))
    ):
        raise ValueError(f"invalid evidence path: {path!r}")


def _run(args: list[str], cwd: Path | None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        cwd=cwd,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        capture_output=True,
        text=True,
    )


def _error(result: subprocess.CompletedProcess[str], verb: str, remote: str) -> EvidenceError:
    detail = redact((result.stderr or result.stdout).replace(remote, "<remote>"))
    return EvidenceError(f"git {verb} failed: {detail.strip()}")


def _git(args: list[str], cwd: Path | None, remote: str) -> str:
    result = _run(args, cwd)
    if result.returncode != 0:
        raise _error(result, args[0], remote)
    return result.stdout.strip()


def _prepare(remote: str, repo: Path) -> None:
    """Clone the evidence branch, or start an orphan branch if it is missing."""
    heads = _git(["ls-remote", "--heads", remote, BRANCH], None, remote)
    if heads:
        _git(
            ["clone", "--depth", "1", "--branch", BRANCH, "--single-branch", remote, str(repo)],
            None,
            remote,
        )
    else:
        repo.mkdir(parents=True)
        _git(["init", "-b", BRANCH], repo, remote)
        _git(["remote", "add", "origin", remote], repo, remote)
    _git(["config", "user.name", IDENTITY[0]], repo, remote)
    _git(["config", "user.email", IDENTITY[1]], repo, remote)


def publish(
    remote: str,
    files: Mapping[str, Path],
    message: str,
    raw_base: str,
    workdir: Path,
    *,
    _before_push: Callable[[int], None] | None = None,
) -> dict[str, str]:
    """Commit ``files`` to the evidence branch and return their raw URLs.

    ``files`` maps the published path (posix, relative) to a local source file.
    If the push is rejected (another job pushed first), the branch is fetched and
    our commit is rebased on top of it, then pushed again: up to 3 attempts. The
    files are additive, so conflicts only occur when the same path is rewritten;
    then our (newer) content wins. ``_before_push`` is a test seam, called with
    the attempt number just before each push.
    """
    for path in files:
        _check_path(path)

    repo = workdir / "evidence"
    workdir.mkdir(parents=True, exist_ok=True)
    if repo.exists():
        shutil.rmtree(repo)
    try:
        _prepare(remote, repo)
        for path, src in files.items():
            dest = repo / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
        _git(["add", "--all"], repo, remote)
        _git(["commit", "--allow-empty", "-m", message], repo, remote)

        for attempt in range(1, MAX_ATTEMPTS + 1):
            if _before_push is not None:
                _before_push(attempt)
            pushed = _run(["push", "origin", f"HEAD:refs/heads/{BRANCH}"], repo)
            if pushed.returncode == 0:
                commit = _git(["rev-parse", "HEAD"], repo, remote)
                return {path: f"{raw_base}/{commit}/{path}" for path in files}
            if attempt == MAX_ATTEMPTS:
                raise _error(pushed, "push", remote)
            _git(["fetch", "origin", BRANCH], repo, remote)
            # In a rebase "theirs" is the commit being replayed: ours, the newer one.
            _git(["rebase", "-X", "theirs", f"origin/{BRANCH}"], repo, remote)
    finally:
        shutil.rmtree(repo, ignore_errors=True)
    raise EvidenceError("unreachable")  # pragma: no cover
