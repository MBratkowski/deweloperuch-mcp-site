"""Build both pages from the pinned release, offline.

    python3 tools/build_docs.py
    python -m tools.build_docs

All inputs are read and rendered before any page is written.
"""

import sys
from pathlib import Path

# Preserve the documented direct-script entry point as well as `python -m`.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.docs_renderer import render_docs, render_landing
from tools.release import ROOT, PACKAGE, BuildError, Release, load_release


def build(root: Path = ROOT) -> Release:
    """Write ``root``/docs/index.html and the generated parts of ``root``/index.html from ``root``/data/server/."""
    release = load_release(root)
    docs = render_docs(release)
    index = render_landing((root / "index.html").read_text(encoding="utf-8"), release)
    (root / "docs").mkdir(exist_ok=True)
    (root / "docs/index.html").write_text(docs, encoding="utf-8")
    (root / "index.html").write_text(index, encoding="utf-8")
    return release


def main(root: Path = ROOT) -> int:
    try:
        release = build(root)
    except BuildError as error:
        print(f"tools/build_docs.py stopped:\n{error}", file=sys.stderr)
        return 1
    print(f"docs/index.html and index.html built from {PACKAGE} {release.version} ({len(release.tools)} tools, {len(release.prompts)} prompts)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
