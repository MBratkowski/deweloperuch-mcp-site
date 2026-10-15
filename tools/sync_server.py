"""Write data/server/ from a released deweloperuch-mcp: the source of the docs page and of the
server-dependent parts of the landing page.

    uv run python -m tools.sync_server            # the latest release on PyPI
    uv run python -m tools.sync_server 0.1.3      # a given release
    uv run python -m tools.sync_server --pinned   # the release in data/server/version.json

Then run ``python3 tools/build_docs.py``. Starts the release over stdio (``uvx
deweloperuch-mcp==<version>``, tools/check_facts.py:read_release) and asks it only for
``initialize``, ``tools/list`` and ``prompts/list``: no tool is called, so nothing reaches
deweloperuch.pl. Writes version.json, mcp_tools.json (the tools with the server's name and
instructions) and mcp_prompts.json. The files depend only on the release: keys sorted, except
the ``properties`` of a JSON Schema, which keep the server's parameter order (the order of the
docs' parameter tables); two-space indentation; no timestamps.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from tools.build_docs import ROOT, SERVER_DIR, Release, pinned_version
from tools.check_facts import PACKAGE, read_release


def stable(value: Any, keep_order: bool = False) -> Any:
    """``value`` with the keys of every mapping sorted, except the mapping under a
    ``properties`` key: a JSON Schema's parameters, in the server's order."""
    if isinstance(value, dict):
        items = value.items() if keep_order else sorted(value.items())
        return {key: stable(item, keep_order=not keep_order and key == "properties") for key, item in items}
    if isinstance(value, list):
        return [stable(item) for item in value]
    return value


def dump(value: Any) -> str:
    return json.dumps(stable(value), ensure_ascii=False, indent=2) + "\n"


def server_files(release: Release) -> dict[str, str]:
    """The files of data/server/ for ``release``, by name."""
    return {
        "version.json": dump({"version": release.version}),
        "mcp_tools.json": dump({"server": {"name": release.name, "instructions": release.instructions}, "tools": list(release.tools)}),
        "mcp_prompts.json": dump({"prompts": list(release.prompts)}),
    }


def write(release: Release, root: Path = ROOT) -> None:
    directory = root / SERVER_DIR
    directory.mkdir(parents=True, exist_ok=True)
    for name, text in server_files(release).items():
        (directory / name).write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.sync_server", description=__doc__.split("\n\n")[0])
    which = parser.add_mutually_exclusive_group()
    which.add_argument("version", nargs="?", help="the release to write (default: the latest on PyPI)")
    which.add_argument("--pinned", action="store_true", help="the release in data/server/version.json")
    args = parser.parse_args(argv)

    release = asyncio.run(read_release(pinned_version(root) if args.pinned else args.version))
    write(release, root)
    print(
        f"{SERVER_DIR}/ written from {PACKAGE} {release.version} ({len(release.tools)} tools, {len(release.prompts)} prompts); "
        "next: python3 tools/build_docs.py"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
