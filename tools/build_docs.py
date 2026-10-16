"""Build both complete pages and the saved demo, offline.

    python3 tools/build_docs.py
    python3 -m tools.build_docs

Only templates and committed data are inputs. All inputs are read and rendered
before any page is written; capture and release synchronization are separate.
"""

import json
import sys
from pathlib import Path

# Preserve the documented direct-script entry point as well as `python -m`.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.docs_renderer import render_docs, render_landing
from tools.page_fragments import load_shared_templates
from tools.release import ROOT, PACKAGE, BuildError, Release, load_release
from tools.update_demo import DEMO_PATH


def read_source(root: Path, path: str) -> str:
    try:
        return (root / path).read_text(encoding="utf-8")
    except OSError as error:
        raise BuildError(f"{path}: {error}") from error


def render_pages(release: Release, root: Path = ROOT) -> dict[str, str]:
    """Render the published paths without reading outputs, writing or using the network."""
    try:
        demo = json.loads(read_source(root, DEMO_PATH))
    except ValueError as error:
        raise BuildError(f"{DEMO_PATH}: {error}") from error
    if not isinstance(demo, dict):
        raise BuildError(f"{DEMO_PATH}: expected a JSON object")
    landing_template = read_source(root, "tools/templates/landing.html")
    docs_template = read_source(root, "tools/templates/docs.html")
    shared_templates = load_shared_templates(root)
    try:
        index = render_landing(landing_template, release, demo, shared_templates=shared_templates)
    except (KeyError, TypeError, ValueError, IndexError) as error:
        raise BuildError(f"{DEMO_PATH}: invalid saved demo: {error}") from error
    try:
        docs = render_docs(release, docs_template, shared_templates=shared_templates)
    except (KeyError, ValueError) as error:
        raise BuildError(f"tools/templates/docs.html: {error}") from error
    return {"index.html": index, "docs/index.html": docs}


def build(root: Path = ROOT) -> Release:
    """Write both complete pages from source templates and saved data."""
    release = load_release(root)
    pages = render_pages(release, root)
    for path, page in pages.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(page, encoding="utf-8")
    return release


def main(root: Path = ROOT) -> int:
    try:
        release = build(root)
    except BuildError as error:
        print(f"tools/build_docs.py stopped:\n{error}", file=sys.stderr)
        return 1
    print(f"index.html (including saved demo) and docs/index.html built offline from {PACKAGE} {release.version} ({len(release.tools)} tools, {len(release.prompts)} prompts)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
