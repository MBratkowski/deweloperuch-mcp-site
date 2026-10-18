"""The artifact scan finds token- and key-shaped text and runs on the standard library alone."""

import subprocess
import sys
from pathlib import Path

import pytest

from tools.factory import secret_scan
from tools.factory.secret_scan import SECRET_SHAPES, scan

SCANNER = Path(secret_scan.__file__)


def test_shapes() -> None:
    assert SECRET_SHAPES == ("sk-ant-", "ghp_", "gho_", "ghs_", "ghu_", "github_pat_", "-----BEGIN")


@pytest.mark.parametrize("shape", SECRET_SHAPES)
def test_scan_finds_each_shape_in_files_and_directories(tmp_path: Path, shape: str) -> None:
    (tmp_path / "drafts.json").write_text('{"drafts": []}')
    repros = tmp_path / "repros"
    repros.mkdir()
    (repros / "clean.py").write_text("print('NOT REPRODUCED')\n")
    (repros / "leak.py").write_text(f"TOKEN = '{shape}AbCdEf'\n")
    assert scan([tmp_path / "drafts.json", repros]) == [(repros / "leak.py", shape)]


def test_command_line_runs_isolated_without_site_packages(tmp_path: Path) -> None:
    clean = tmp_path / "clean.json"
    clean.write_text('{"drafts": []}')
    leak = tmp_path / "leak.py"
    leak.write_text("KEY = '-----BEGIN OPENSSH PRIVATE KEY-----'\n")
    run = lambda *paths: subprocess.run(  # noqa: E731
        [sys.executable, "-I", "-S", str(SCANNER), *map(str, paths)], capture_output=True, text=True
    )
    ok = run(clean, tmp_path / "missing-dir-is-fine-when-absent")
    assert ok.returncode == 0, ok.stderr
    found = run(clean, leak)
    assert found.returncode == 1
    assert "leak.py" in found.stderr and "-----BEGIN" in found.stderr
    assert "PRIVATE KEY" not in found.stderr + found.stdout
