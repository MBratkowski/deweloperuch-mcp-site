"""Evidence branch publishing, against local bare repositories (offline)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools.factory import evidence

RAW = "https://raw.githubusercontent.com/MBratkowski/deweloperuch-mcp-site"
BRANCH = "factory-evidence"
EMAIL = "41898282+factory-app[bot]@users.noreply.github.com"


def publish(*args: object, **kwargs: object) -> dict[str, str]:
    """evidence.publish as a caller uses it: with the App bot's resolved address."""
    return evidence.publish(*args, email=EMAIL, **kwargs)  # type: ignore[arg-type]


def git(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def make_remote(tmp_path: Path, with_main: bool = True) -> str:
    bare = tmp_path / "bare.git"
    git("init", "--bare", "-b", "main", str(bare))
    if with_main:
        seed = tmp_path / "seed"
        git("init", "-b", "main", str(seed))
        (seed / "README.md").write_text("main\n")
        git("add", ".", cwd=seed)
        git(
            "-c", "user.name=t", "-c", "user.email=t@example.com",
            "commit", "-m", "init", cwd=seed,
        )
        git("push", str(bare), "main", cwd=seed)
    return f"file://{bare}"


def bare_path(remote: str) -> str:
    return remote.removeprefix("file://")


def source(tmp_path: Path, name: str, content: bytes = b"data") -> Path:
    path = tmp_path / "src" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_creates_orphan_branch(tmp_path: Path) -> None:
    remote = make_remote(tmp_path)
    shot = source(tmp_path, "a.png")
    publish(
        remote, {"issues/abc/1/a.png": shot}, "Add evidence", RAW, tmp_path / "work"
    )
    bare = bare_path(remote)
    parents = git("rev-list", "--parents", "-n", "1", BRANCH, cwd=Path(bare)).split()
    assert len(parents) == 1  # the commit has no parent
    assert git("rev-list", "--max-parents=0", BRANCH, cwd=Path(bare)) == parents[0]
    base = subprocess.run(
        ["git", "merge-base", BRANCH, "main"], cwd=bare, capture_output=True, text=True
    )
    assert base.returncode != 0 and base.stdout.strip() == ""


def test_commits_carry_the_app_bots_address(tmp_path: Path) -> None:
    remote = make_remote(tmp_path)
    publish(remote, {"a/1.png": source(tmp_path, "1")}, "one", RAW, tmp_path / "w1")
    who = git("log", "-1", "--format=%an <%ae>|%cn <%ce>", BRANCH, cwd=Path(bare_path(remote)))
    assert who == f"factory-evidence <{EMAIL}>|factory-evidence <{EMAIL}>"


@pytest.mark.parametrize("email", ["", "factory"])
def test_no_commit_without_a_resolved_address(tmp_path: Path, email: str) -> None:
    remote = make_remote(tmp_path)
    with pytest.raises(evidence.EvidenceError):
        evidence.publish(remote, {"a/1.png": source(tmp_path, "1")}, "one", RAW, tmp_path / "w1", email=email)
    assert not git("ls-remote", "--heads", remote, BRANCH)


def test_links_use_the_commit_sha(tmp_path: Path) -> None:
    remote = make_remote(tmp_path)
    urls = publish(
        remote,
        {
            "issues/abc/1/a.png": source(tmp_path, "a.png", b"A"),
            "repros/abc.py": source(tmp_path, "r.py", b"print(1)\n"),
        },
        "Add evidence",
        RAW,
        tmp_path / "work",
    )
    head = git("rev-parse", BRANCH, cwd=Path(bare_path(remote)))
    assert len(head) == 40
    assert urls == {
        "issues/abc/1/a.png": f"{RAW}/{head}/issues/abc/1/a.png",
        "repros/abc.py": f"{RAW}/{head}/repros/abc.py",
    }
    blob = git("show", f"{head}:repros/abc.py", cwd=Path(bare_path(remote)))
    assert blob == "print(1)"


def test_second_publish_extends_existing_branch(tmp_path: Path) -> None:
    remote = make_remote(tmp_path)
    publish(remote, {"a/1.png": source(tmp_path, "1")}, "one", RAW, tmp_path / "w1")
    publish(remote, {"a/2.png": source(tmp_path, "2")}, "two", RAW, tmp_path / "w2")
    files = git("ls-tree", "-r", "--name-only", BRANCH, cwd=Path(bare_path(remote)))
    assert files.split() == ["a/1.png", "a/2.png"]


def test_concurrent_push_rebases(tmp_path: Path) -> None:
    remote = make_remote(tmp_path)
    publish(remote, {"base.txt": source(tmp_path, "base")}, "base", RAW, tmp_path / "w0")
    calls: list[int] = []

    def other_job_pushes(attempt: int) -> None:
        calls.append(attempt)
        if attempt == 1:
            publish(
                remote, {"other.txt": source(tmp_path, "other")}, "other", RAW, tmp_path / "w2"
            )

    urls = publish(
        remote,
        {"mine.txt": source(tmp_path, "mine")},
        "mine",
        RAW,
        tmp_path / "w1",
        _before_push=other_job_pushes,
    )
    assert calls == [1, 2]
    bare = Path(bare_path(remote))
    head = git("rev-parse", BRANCH, cwd=bare)
    assert urls["mine.txt"] == f"{RAW}/{head}/mine.txt"
    files = git("ls-tree", "-r", "--name-only", head, cwd=bare).split()
    assert files == ["base.txt", "mine.txt", "other.txt"]


def test_same_path_rewritten_newer_content_wins(tmp_path: Path) -> None:
    remote = make_remote(tmp_path)
    publish(remote, {"x.txt": source(tmp_path, "x0", b"old")}, "base", RAW, tmp_path / "w0")

    def other(attempt: int) -> None:
        if attempt == 1:
            publish(
                remote, {"x.txt": source(tmp_path, "x1", b"other")}, "o", RAW, tmp_path / "w2"
            )

    publish(
        remote, {"x.txt": source(tmp_path, "x2", b"mine")}, "m", RAW, tmp_path / "w1",
        _before_push=other,
    )
    assert git("show", f"{BRANCH}:x.txt", cwd=Path(bare_path(remote))) == "mine"


def test_gives_up_after_three_attempts(tmp_path: Path) -> None:
    remote = make_remote(tmp_path)
    publish(remote, {"b.txt": source(tmp_path, "b")}, "base", RAW, tmp_path / "w0")
    n = 0

    def always_race(attempt: int) -> None:
        nonlocal n
        n += 1
        publish(
            remote, {f"r{n}.txt": source(tmp_path, f"r{n}")}, "r", RAW, tmp_path / f"r{n}"
        )

    with pytest.raises(evidence.EvidenceError):
        publish(
            remote, {"m.txt": source(tmp_path, "m")}, "m", RAW, tmp_path / "w1",
            _before_push=always_race,
        )
    assert n == 3


def test_error_text_never_contains_the_token(tmp_path: Path) -> None:
    secret = "ghs_SECRETTOKEN123"
    remote = f"https://x-access-token:{secret}@127.0.0.1:1/owner/repo.git"
    with pytest.raises(evidence.EvidenceError) as info:
        publish(
            remote, {"a.txt": source(tmp_path, "a")}, "m", RAW, tmp_path / "work"
        )
    assert secret not in str(info.value)
    assert secret not in repr(info.value)


def test_redact_masks_credentials() -> None:
    text = "fatal: unable to access 'https://x-access-token:abc@github.com/o/r.git/'"
    assert "abc" not in evidence.redact(text)


@pytest.mark.parametrize(
    "bad", ["/abs.png", "../x.png", "a/../x.png", "a\\b.png", "", "a//b.png"]
)
def test_invalid_paths_rejected_before_git(tmp_path: Path, bad: str) -> None:
    with pytest.raises(ValueError):
        publish(
            "file:///nonexistent.git", {bad: source(tmp_path, "a")}, "m", RAW, tmp_path / "w"
        )
    assert not (tmp_path / "w").exists()
