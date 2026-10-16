"""Pinned release data shared by synchronization, rendering and validation."""

import json
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER_DIR = "data/server"
PACKAGE = "deweloperuch-mcp"
REPO = "https://github.com/MBratkowski/deweloperuch-mcp"
RELEASES = REPO + "/releases/latest"


class BuildError(Exception):
    """The release or page inputs cannot be built; no output should be written."""


@dataclass(frozen=True)
class Release:
    version: str
    tools: tuple[dict, ...]
    prompts: tuple[dict, ...]
    name: str = ""
    instructions: str = ""
    cli_commands: tuple[dict, ...] = ()

    @property
    def tool_names(self) -> frozenset[str]:
        return frozenset(tool["name"] for tool in self.tools)

    @property
    def prompt_titles(self) -> frozenset[str]:
        return frozenset(prompt.get("title") or prompt["name"] for prompt in self.prompts)


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise BuildError(f"{path.name}: {error}; run python -m tools.sync_server --pinned") from error
    if not isinstance(value, dict):
        raise BuildError(f"{path.name}: expected a JSON object")
    return value


def pinned_version(root: Path = ROOT) -> str:
    return read_json(root / SERVER_DIR / "version.json")["version"]


def validate_cli(snapshot: dict, version: str) -> tuple[dict, ...]:
    """A version-matched, nonempty CLI listing, sorted independently of registration order."""
    prefix = "cli_commands.json"
    if not isinstance(snapshot, dict) or snapshot.get("version") != version:
        raise BuildError(f"{prefix}: expected {PACKAGE} {version}; resync the pinned release")
    commands = snapshot.get("commands")
    if not isinstance(commands, list) or not commands:
        raise BuildError(f"{prefix}: expected a nonempty command list")
    names = set()
    for command in commands:
        if not isinstance(command, dict) or any(not isinstance(command.get(key), str) or not command[key].strip() for key in ("name", "usage", "description")):
            raise BuildError(f"{prefix}: each command needs a name, usage and description")
        name = command["name"]
        if name in names:
            raise BuildError(f"{prefix}: duplicate command {name}")
        names.add(name)
        if command["usage"] != f"deweloperuch {name}" and not command["usage"].startswith(f"deweloperuch {name} "):
            raise BuildError(f"{prefix}: usage does not match command {name}")
    return tuple(sorted(commands, key=lambda command: command["name"]))


def load_release(root: Path = ROOT) -> Release:
    directory = root / SERVER_DIR
    version = pinned_version(root)
    tools = read_json(directory / "mcp_tools.json")
    prompts = read_json(directory / "mcp_prompts.json")
    commands = validate_cli(read_json(directory / "cli_commands.json"), version)
    return Release(version=version, tools=tuple(tools["tools"]), prompts=tuple(prompts["prompts"]),
                   name=tools["server"]["name"], instructions=tools["server"]["instructions"], cli_commands=commands)
