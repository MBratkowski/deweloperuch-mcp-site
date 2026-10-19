"""Find text shaped like a token or a key in files the factory is about to publish.

Standard library only, with no import from this repository, so a workflow step can run it as
`python -I -S tools/factory/secret_scan.py PATH...` without trusting the environment. The
patterns are defined here once; tools/factory/issues.py checks agent text against them too.

Exit 0 when nothing is found, 1 when a file holds a secret shape. It names the file and the
shape, never the text around it.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

# Prefixes of the Claude and GitHub tokens and of PEM keys.
SECRET_SHAPES = ("sk-ant-", "ghp_", "gho_", "ghs_", "ghu_", "github_pat_", "-----BEGIN")


def secret_shape(text: str) -> str | None:
    """The first secret shape in `text`, or None."""
    for shape in SECRET_SHAPES:
        if shape in text:
            return shape
    return None


def _files(paths: Iterable[Path]) -> list[Path]:
    """Every file under the given files and directories; missing paths hold nothing to publish."""
    found: list[Path] = []
    for path in paths:
        if path.is_dir():
            found += sorted(p for p in path.rglob("*") if p.is_file())
        elif path.is_file():
            found.append(path)
    return found


def scan(paths: Iterable[Path]) -> list[tuple[Path, str]]:
    """(file, shape) for every file that holds a secret shape."""
    hits = []
    for path in _files(paths):
        shape = secret_shape(path.read_bytes().decode("utf-8", errors="replace"))
        if shape is not None:
            hits.append((path, shape))
    return hits


def main(argv: Sequence[str] | None = None) -> int:
    paths = [Path(arg) for arg in (sys.argv[1:] if argv is None else argv)]
    hits = scan(paths)
    for path, shape in hits:
        print(f"secret-shaped text ({shape!r}) in {path}", file=sys.stderr)
    print(f"{len(_files(paths))} files scanned, {len(hits)} with secret-shaped text")
    return 1 if hits else 0


if __name__ == "__main__":
    raise SystemExit(main())
