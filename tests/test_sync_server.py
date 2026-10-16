import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import mcp
import pytest
from mcp.types import Implementation, ListPromptsResult, ListToolsResult, Prompt, Tool

from tools import check_facts, sync_server
from tools.release import load_release
from tools.sync_server import dump, server_files, stable, write
from tools.release import BuildError

ROOT = Path(__file__).resolve().parent.parent
SERVER_FILES = ("version.json", "mcp_tools.json", "mcp_prompts.json", "cli_commands.json")


def committed(name: str) -> str:
    return (ROOT / "data/server" / name).read_text(encoding="utf-8")


def in_thread(function):
    """Run ``function`` in a worker thread: the sync Playwright fixtures keep an event loop
    running in the main thread, where ``asyncio.run`` refuses to start."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(function).result()


class FakeClient:
    """The committed listing served as ``mcp.Client`` would; anything but listing fails."""

    version = load_release().version
    started: list[list[str]] = []
    calls: list[str] = []

    def __init__(self, server: mcp.StdioServerParameters) -> None:
        self.started.append([server.command, *server.args])

    async def __aenter__(self) -> "FakeClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        pass

    async def list_tools(self, cursor: str | None = None) -> ListToolsResult:
        self.calls.append("tools/list")
        return ListToolsResult(tools=[Tool.model_validate(tool) for tool in load_release().tools])

    async def list_prompts(self, cursor: str | None = None) -> ListPromptsResult:
        self.calls.append("prompts/list")
        return ListPromptsResult(prompts=[Prompt.model_validate(prompt) for prompt in load_release().prompts])

    @property
    def server_info(self) -> Implementation:
        return Implementation(name="deweloperuch", version=self.version)

    @property
    def instructions(self) -> str:
        return load_release().instructions

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"tools/sync_server.py used Client.{name}; it may only list")


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> type[FakeClient]:
    monkeypatch.setattr(mcp, "Client", FakeClient)
    monkeypatch.setattr(sync_server, "read_cli", lambda version: load_release().cli_commands)
    monkeypatch.setattr(FakeClient, "started", [])
    monkeypatch.setattr(FakeClient, "calls", [])
    return FakeClient


def test_stable_sorts_keys_but_keeps_the_parameter_order() -> None:
    schema = {"type": "object", "required": ["b", "a"], "properties": {"z": {"type": "string", "description": "Z"}, "properties": {"type": "object", "properties": {"y": {}, "x": {}}}}}
    assert json.dumps(stable({"tools": [schema], "server": {}})) == json.dumps({
        "server": {},
        "tools": [{
            "properties": {
                "z": {"description": "Z", "type": "string"},
                "properties": {"properties": {"y": {}, "x": {}}, "type": "object"},
            },
            "required": ["b", "a"],
            "type": "object",
        }],
    })


def test_dump_is_indented_utf8_with_a_final_newline() -> None:
    assert dump({"title": "Szukaj ofert", "a": 1}) == '{\n  "a": 1,\n  "title": "Szukaj ofert"\n}\n'
    assert dump({"miasto": "Kraków"}) == '{\n  "miasto": "Kraków"\n}\n'


def test_committed_files_are_what_the_sync_writes() -> None:
    assert server_files(load_release()) == {name: committed(name) for name in SERVER_FILES}


def test_sync_only_lists_and_reproduces_the_committed_files(tmp_path: Path, fake_client: type[FakeClient], capsys: pytest.CaptureFixture[str]) -> None:
    assert in_thread(lambda: sync_server.main([fake_client.version], root=tmp_path)) == 0
    assert fake_client.started == [["uvx", f"deweloperuch-mcp=={fake_client.version}"]]
    assert fake_client.calls == ["tools/list", "prompts/list"]
    assert {name: (tmp_path / "data/server" / name).read_text(encoding="utf-8") for name in SERVER_FILES} == {
        name: committed(name) for name in SERVER_FILES
    }
    assert "next: python3 tools/build_docs.py" in capsys.readouterr().out


def test_sync_pinned_reads_version_json(tmp_path: Path, fake_client: type[FakeClient]) -> None:
    (tmp_path / "data/server").mkdir(parents=True)
    (tmp_path / "data/server/version.json").write_text(committed("version.json"), encoding="utf-8")
    assert in_thread(lambda: sync_server.main(["--pinned"], root=tmp_path)) == 0
    assert fake_client.started == [["uvx", f"deweloperuch-mcp=={fake_client.version}"]]


def test_sync_defaults_to_the_latest_release(tmp_path: Path, fake_client: type[FakeClient], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sync_server, "latest_version", lambda: fake_client.version)
    assert in_thread(lambda: sync_server.main([], root=tmp_path)) == 0
    assert fake_client.started == [["uvx", f"deweloperuch-mcp=={fake_client.version}"]]


def test_sync_refuses_a_server_of_another_version(tmp_path: Path, fake_client: type[FakeClient]) -> None:
    with pytest.raises(RuntimeError, match="reports version"):
        in_thread(lambda: sync_server.main(["0.0.1"], root=tmp_path))
    assert not (tmp_path / "data/server").exists()


def test_cli_export_failure_leaves_snapshots_untouched(tmp_path: Path, fake_client: type[FakeClient], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def failed_export(version: str) -> tuple[dict, ...]:
        raise BuildError("cli_commands.json: metadata export failed")

    write(load_release(), tmp_path)
    before = {name: (tmp_path / "data/server" / name).read_bytes() for name in SERVER_FILES}
    monkeypatch.setattr(sync_server, "read_cli", failed_export)
    assert in_thread(lambda: sync_server.main([fake_client.version], root=tmp_path)) == 1
    assert "cli_commands.json" in capsys.readouterr().err
    assert {name: (tmp_path / "data/server" / name).read_bytes() for name in SERVER_FILES} == before


def test_cli_export_timeout_has_a_useful_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def timeout(command: list[str], **kwargs) -> subprocess.CompletedProcess:
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(sync_server.subprocess, "run", timeout)
    with pytest.raises(BuildError, match="cli_commands.json.*timed out"):
        sync_server.read_cli("0.1.2")


@pytest.mark.network
def test_sync_of_the_pinned_release_reproduces_the_committed_files(tmp_path: Path) -> None:
    (tmp_path / "data/server").mkdir(parents=True)
    (tmp_path / "data/server/version.json").write_text(committed("version.json"), encoding="utf-8")
    assert in_thread(lambda: sync_server.main(["--pinned"], root=tmp_path)) == 0
    for name in SERVER_FILES:
        assert (tmp_path / "data/server" / name).read_text(encoding="utf-8") == committed(name), name
