"""Pinned release data shared by synchronization, rendering and validation."""

import json
from dataclasses import dataclass, replace
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


def _metadata_error(path: str, problem: str) -> BuildError:
    return BuildError(f"{path}: {problem}; fix the metadata or run python -m tools.sync_server --pinned")


def _object(value: object, path: str) -> dict:
    if not isinstance(value, dict):
        raise _metadata_error(path, "expected a JSON object")
    return value


def _metadata_records(value: object, path: str) -> tuple:
    if not isinstance(value, (list, tuple)):
        raise _metadata_error(path, "expected an array")
    return tuple(value)


def _optional_text(value: object, path: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise _metadata_error(path, "expected a string or null")
    return value


def _named_metadata(value: object, path: str) -> dict:
    record = _object(value, path)
    if not isinstance(record.get("name"), str) or not record["name"].strip():
        raise _metadata_error(f"{path}.name", "expected a nonempty string")
    return {**record, "description": _optional_text(record.get("description"), f"{path}.description")}


def schema_types(value: object, path: str = "JSON Schema") -> tuple[str, ...]:
    """Explicit types used by the parameter table, including nullable unions.

    This extracts display types, not the schema's value constraints. Expressions
    requiring reference resolution or intersection need renderer support first.
    """
    schema = _object(value, path)
    unsupported = {"$ref", "$dynamicRef", "allOf", "oneOf", "not", "if", "then", "else"} & schema.keys()
    if unsupported or ("type" in schema and "anyOf" in schema):
        expression = ", ".join(sorted(unsupported)) if unsupported else "type with anyOf"
        raise _metadata_error(path, f"unsupported type expression ({expression}); extend tools/release.py schema_types to support it")
    if "anyOf" in schema:
        alternatives = schema["anyOf"]
        if not isinstance(alternatives, list) or not alternatives:
            raise _metadata_error(f"{path}.anyOf", "expected a nonempty array of schemas")
        types = []
        for index, alternative in enumerate(alternatives):
            branch_path = f"{path}.anyOf[{index}]"
            branch_types = schema_types(alternative, branch_path)
            if not branch_types:
                raise _metadata_error(branch_path, "unsupported alternative without an explicit type; add renderer support")
            types.extend(branch_types)
        return tuple(dict.fromkeys(types))
    if "type" not in schema:
        return ()
    declared = schema["type"]
    types = [declared] if isinstance(declared, str) else declared
    valid = {"string", "integer", "number", "boolean", "array", "object", "null"}
    if (not isinstance(types, list) or not types
            or any(not isinstance(item, str) or item not in valid for item in types)
            or len(set(types)) != len(types)):
        raise _metadata_error(f"{path}.type", "expected a JSON Schema type string or a nonempty array of unique type strings")
    return tuple(types)


def normalize_mcp_metadata(release: Release) -> Release:
    """Copy and validate the MCP fields the renderers consume before rendering."""
    normalized = {}
    for kind, records in (("tools", release.tools), ("prompts", release.prompts)):
        path = f"mcp_{kind}.json.{kind}"
        items, names = [], set()
        for index, value in enumerate(_metadata_records(records, path)):
            item_path = f"{path}[{index}]"
            item = _named_metadata(value, item_path)
            name = item["name"]
            if name in names:
                raise _metadata_error(f"{item_path}.name", f"duplicate name {name}")
            names.add(name)
            item_path += f" ({name})"
            item["title"] = _optional_text(item.get("title"), f"{item_path}.title") or name
            if kind == "tools":
                schema_path = f"{item_path}.inputSchema"
                schema = _object(item.get("inputSchema"), schema_path)
                if schema.get("type") != "object":
                    raise _metadata_error(f"{schema_path}.type", "expected object for an MCP tool input schema")
                properties = _object(schema.get("properties", {}), f"{schema_path}.properties")
                props = {}
                for key, prop in properties.items():
                    prop_path = f"{schema_path}.properties.{key}"
                    if not isinstance(key, str) or not key:
                        raise _metadata_error(f"{schema_path}.properties", "expected nonempty parameter names")
                    prop = _object(prop, prop_path)
                    schema_types(prop, prop_path)
                    props[key] = {**prop, "description": _optional_text(prop.get("description"), f"{prop_path}.description")}
                required = schema.get("required", [])
                if (not isinstance(required, list) or any(not isinstance(key, str) for key in required)
                        or len(set(required)) != len(required) or any(key not in props for key in required)):
                    raise _metadata_error(f"{schema_path}.required", "expected an array of unique parameter names present in properties")
                item["inputSchema"] = {**schema, "properties": props, "required": required}
            else:
                arguments = item.get("arguments")
                if arguments is None:
                    arguments = []
                if not isinstance(arguments, list):
                    raise _metadata_error(f"{item_path}.arguments", "expected an array or null")
                args = []
                for arg_index, argument in enumerate(arguments):
                    arg_path = f"{item_path}.arguments[{arg_index}]"
                    argument = _named_metadata(argument, arg_path)
                    required = argument.get("required")
                    if required is not None and not isinstance(required, bool):
                        raise _metadata_error(f"{arg_path}.required", "expected a boolean or null")
                    args.append(argument)
                item["arguments"] = args
            items.append(item)
        normalized[kind] = tuple(items)
    return replace(release, **normalized)


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
    release = Release(
        version=version,
        tools=_metadata_records(tools.get("tools"), "mcp_tools.json.tools"),
        prompts=_metadata_records(prompts.get("prompts"), "mcp_prompts.json.prompts"),
        name=tools["server"]["name"], instructions=tools["server"]["instructions"], cli_commands=commands,
    )
    # Keep snapshots unchanged for synchronization and comparison; only renderers
    # consume the normalized copy.
    normalize_mcp_metadata(release)
    return release
