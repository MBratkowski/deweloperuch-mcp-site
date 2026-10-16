"""Write data/server/ from a released deweloperuch-mcp: the source of the docs page and of the
server-dependent parts of the landing page.

    uv run python -m tools.sync_server            # the latest release on PyPI
    uv run python -m tools.sync_server 0.1.3      # a given release
    uv run python -m tools.sync_server --pinned   # the release in data/server/version.json

Then run ``python3 tools/build_docs.py``. Starts the release over stdio (``uvx
deweloperuch-mcp==<version>``) and asks it only for
``initialize``, ``tools/list`` and ``prompts/list``: no tool is called, so nothing reaches
deweloperuch.pl. Writes version.json, mcp_tools.json (the tools with the server's name and
instructions), mcp_prompts.json and cli_commands.json. CLI metadata is inspected in the same
pinned package environment without executing command callbacks. The files have sorted keys, except
the ``properties`` of a JSON Schema, which keep the server's parameter order (the order of the
docs' parameter tables); commands are sorted by name; two-space indentation; no timestamps.
"""

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from tools.release import PACKAGE, ROOT, SERVER_DIR, BuildError, Release, pinned_version, validate_cli

PYPI_URL = f"https://pypi.org/pypi/{PACKAGE}/json"
# Use the same public source as latest_version and revalidate just-published releases.
UVX_OPTIONS = ("--no-config", "--default-index", "https://pypi.org/simple", "--refresh-package", PACKAGE)


def latest_version() -> str:
    request = Request(PYPI_URL, headers={"Accept": "application/json"})
    with urlopen(request, timeout=30) as response:
        return json.load(response)["info"]["version"]


def read_cli(version: str) -> tuple[dict, ...]:
    """Inspect the pinned CLI in its own environment, without calling a command."""
    command = ["uvx", *UVX_OPTIONS, "--from", f"{PACKAGE}=={version}", "python", "-I", str(Path(__file__).with_name("export_cli.py"))]
    try:
        done = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=120)
    except subprocess.TimeoutExpired as error:
        raise BuildError("cli_commands.json: metadata export timed out after 120 seconds") from error
    except OSError as error:
        raise BuildError(f"cli_commands.json: could not start metadata export: {error}") from error
    if done.returncode:
        raise BuildError(f"cli_commands.json: metadata export failed (exit {done.returncode}): {done.stderr.strip()}")
    try:
        snapshot = json.loads(done.stdout)
    except ValueError as error:
        raise BuildError("cli_commands.json: metadata export did not return valid JSON") from error
    return validate_cli(snapshot, version)


async def read_release(version: str | None = None) -> Release:
    """List the pinned MCP server and inspect its CLI; never execute a data tool."""
    from mcp import Client, StdioServerParameters

    version = version or latest_version()
    server = StdioServerParameters(command="uvx", args=[*UVX_OPTIONS, f"{PACKAGE}=={version}"])
    async with Client(server) as client:
        tools, prompts, cursor = [], [], None
        while True:
            listed = await client.list_tools(cursor=cursor)
            tools += listed.tools
            if not (cursor := listed.next_cursor):
                break
        while True:
            listed = await client.list_prompts(cursor=cursor)
            prompts += listed.prompts
            if not (cursor := listed.next_cursor):
                break
        info, instructions = client.server_info, client.instructions
    served = info.version if info else None
    if served != version:
        raise RuntimeError(f"uvx {PACKAGE}=={version} reports version {served}")
    options = {"mode": "json", "by_alias": True, "exclude_none": True}
    return Release(
        version=version,
        tools=tuple(tool.model_dump(**options) for tool in tools),
        prompts=tuple(prompt.model_dump(**options) for prompt in prompts),
        name=info.name if info else "",
        instructions=instructions or "",
        cli_commands=read_cli(version),
    )


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
    cli = {"version": release.version, "commands": list(release.cli_commands)}
    cli["commands"] = list(validate_cli(cli, release.version))
    return {
        "version.json": dump({"version": release.version}),
        "mcp_tools.json": dump({"server": {"name": release.name, "instructions": release.instructions}, "tools": list(release.tools)}),
        "mcp_prompts.json": dump({"prompts": list(release.prompts)}),
        "cli_commands.json": dump(cli),
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

    try:
        release = asyncio.run(read_release(pinned_version(root) if args.pinned else args.version))
        write(release, root)
    except BuildError as error:
        print(f"tools/sync_server.py stopped:\n{error}", file=sys.stderr)
        return 1
    print(
        f"{SERVER_DIR}/ written from {PACKAGE} {release.version} ({len(release.tools)} tools, {len(release.prompts)} prompts); "
        "next: python3 tools/build_docs.py"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
