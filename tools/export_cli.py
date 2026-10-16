"""Export the pinned package's CLI metadata; never invoke its callbacks.

Run by tools.sync_server inside the package's isolated uv environment. Typer is
imported only there; the site does not need a dependency on the application.
"""

import inspect
import json
from importlib.metadata import distribution
from typing import Any


def describe(command: Any) -> list[dict[str, str]]:
    """Visible leaf commands, their declared usage and first help paragraph."""
    def walk(node: Any, context: Any) -> list[dict[str, str]]:
        if node.hidden:
            return []
        if hasattr(node, "commands"):
            rows = []
            for name in sorted(node.list_commands(context)):
                child = node.get_command(context, name)
                if child is not None:
                    rows.extend(walk(child, child.context_class(child, info_name=name, parent=context)))
            return rows
        help_text = inspect.cleandoc(node.help or node.short_help or "").split("\n\n")[0]
        return [{
            "name": context.command_path.removeprefix("deweloperuch "),
            "usage": " ".join([context.command_path, *node.collect_usage_pieces(context)]),
            "description": " ".join(help_text.splitlines()),
        }]

    return walk(command, command.context_class(command, info_name="deweloperuch"))


def main() -> None:
    from deweloperuch_mcp.consumers.cli.app import app
    from typer.main import get_command

    package = distribution("deweloperuch-mcp")
    print(json.dumps({"version": package.version, "commands": describe(get_command(app))}, ensure_ascii=False))


if __name__ == "__main__":
    main()
