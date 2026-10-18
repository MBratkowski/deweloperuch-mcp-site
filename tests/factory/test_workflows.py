"""The workflows lint clean, every factory job stops when the factory is off, and agents hold no write token."""

from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
MODE_GUARD = "vars.FACTORY_MODE != 'off'"
AGENT_ACTION = "anthropics/claude-code-action"
APP_TOKEN_ACTION = "actions/create-github-app-token"
APP_KEY = "FACTORY_APP_PRIVATE_KEY"
CLAUDE_TOKEN = "CLAUDE_CODE_OAUTH_TOKEN"
ENV_SCRUB = "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"
SETUP_UV = "astral-sh/setup-uv"
# Commands that execute reproduction scripts an agent wrote.
SCRIPT_RUNNERS = ("tools.factory.check_drafts", "tools.factory.verify check")
# What every agent is denied: the network tools, git (its config may hold a token) and every
# place outside the workspace where a token can sit (process environments, home, runner temp).
AGENT_DENIES = (
    "WebFetch",
    "WebSearch",
    "Read(//proc/**)",
    "Read(//sys/**)",
    "Read(//etc/**)",
    "Read(//root/**)",
    "Read(//home/**)",
    "Read(//github/**)",
    "Read(//tmp/**)",
    "Read(~/**)",
    "Read(./.git/**)",
    "Bash(git:*)",
    # Claude Code 2.1.289 runs env-printing forms of these read-only commands unprompted.
    "Bash(ps:*)",
    "Bash(env:*)",
    "Bash(printenv:*)",
    "Bash(set:*)",
    "Bash(declare:*)",
    "Bash(export:*)",
    "Bash(compgen:*)",
)
# Agents that must run git: the fixer commits its fix. Their .git stays, so git itself cannot be
# denied; instead the checkout holds no token, the only git commands allowed are these, pushing,
# gh and curl are denied, and edits stay out of .git and the paths that set the factory's rules.
GIT_AGENTS = {("factory-fix.yml", "fix")}
GIT_RULES = (
    "Bash(git status:*)",
    "Bash(git diff:*)",
    "Bash(git add:*)",
    "Bash(git commit:*)",
    "Bash(git log:*)",
    "Bash(git show:*)",
)
GIT_AGENT_DENIES = (
    *(rule for rule in AGENT_DENIES if rule != "Bash(git:*)"),
    "Edit(./.git/**)",
    "Edit(./.github/**)",
    "Edit(./tools/factory/**)",
    "Edit(./CLAUDE.md)",
    "Edit(./data/**)",
    "Bash(git push:*)",
    "Bash(gh:*)",
    "Bash(curl:*)",
)
# Artifacts that hold agent output, by (workflow, artifact) -> the job that uploads it: scanned for
# secret shapes first, kept one day.
AGENT_ARTIFACTS = {
    ("factory-qa.yml", "drafts"): "triage",
    ("factory-qa.yml", "validated"): "check",
    ("factory-fix.yml", "fix"): "fix",
}
# Steps that run code an agent wrote, or write what it produced, after the agent step.
AGENT_CODE_RUNNERS = (*SCRIPT_RUNNERS, "tools.factory.fix prove", "tools.factory.fix before-after", "git bundle")
SCANNER = "uv run python -I -S tools/factory/secret_scan.py"
AGENT_SETTINGS = {"permissions": {"blockReadsOutsideWorkingDirectories": True}}
QA_CLAUDE_ARGS = (
    "--model ${{ needs.guard.outputs.qa_model }}\n"
    "--max-turns ${{ needs.guard.outputs.qa_max_turns }}\n"
    "--json-schema '${{ needs.guard.outputs.drafts_schema }}'\n"
    '--allowedTools "Read(./**),Glob,Grep,Edit(./factory-out/repros/**),'
    'Bash(uv run python -m tools.factory.probe:*),Bash(uv run python -m tools.factory.findings fingerprint:*)"\n'
    '--disallowedTools "' + ",".join(AGENT_DENIES) + '"\n'
)
FIXER_CLAUDE_ARGS = (
    "--model ${{ needs.guard.outputs.fixer_model }}\n"
    "--max-turns ${{ needs.guard.outputs.fixer_max_turns }}\n"
    "--json-schema '${{ needs.guard.outputs.report_schema }}'\n"
    '--allowedTools "Read(./**),Glob,Grep,Edit(./**),Bash(uv run pytest:*),Bash(python3 -m tools.build_docs:*),'
    "Bash(python3 -m tools.package_site:*),Bash(uv run python -m tools.render_images:*),"
    'Bash(uv run python -m tools.factory.probe:*),' + ",".join(GIT_RULES) + '"\n'
    '--disallowedTools "' + ",".join(GIT_AGENT_DENIES) + '"\n'
)
PLAYWRIGHT_IMAGE = (
    "mcr.microsoft.com/playwright:v1.64.0-noble@sha256:06a9939e57531807f8d5fd76ce44b53165ffb7d7501d87ab10e285c20b1e971f"
)
TOKEN_REFERENCES = ("github.token", "secrets.", "GITHUB_TOKEN", "GH_TOKEN")
# A Bash rule that is only an interpreter prefix (`uv run python -I:*`) runs any file.
_INTERPRETER_RULE = re.compile(r"(?:uv run )?python3?(?: -[A-Za-z]+)*:\*")
# (workflow file, job id) -> the permission scopes an agent job may hold with write access.
# Empty until the reviewer job (Task 20), which posts its review with GITHUB_TOKEN.
AGENT_WRITE_ALLOWED: dict[tuple[str, str], frozenset[str]] = {}


def _workflow_files() -> list[Path]:
    return sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")])


def _factory_workflows() -> list[Path]:
    return sorted(WORKFLOWS.glob("factory-*.yml"))


def test_actionlint_passes() -> None:
    actionlint = shutil.which("actionlint", path=str(Path(sys.executable).parent))
    assert actionlint, "actionlint is missing from the environment (dev dependency actionlint-py)"
    files = [str(path) for path in _workflow_files()]
    result = subprocess.run(
        [actionlint, "-no-color", *files], cwd=ROOT, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_factory_jobs_guard_on_mode() -> None:
    paths = _factory_workflows()
    assert {WORKFLOWS / "factory-qa.yml", WORKFLOWS / "factory-fix.yml"} <= set(paths)
    for path in paths:
        jobs = load_yaml(path)["jobs"]
        assert jobs, f"{path.name} has no jobs"
        for name, job in jobs.items():
            condition = str(job.get("if", ""))
            assert MODE_GUARD in condition, f"{path.name}: job {name} runs when FACTORY_MODE is off"


def test_agents_hold_no_write_token() -> None:
    agents = 0
    for path in _workflow_files():
        workflow = load_yaml(path)
        for name, job in workflow["jobs"].items():
            uses = [str(step.get("uses", "")) for step in job.get("steps") or []]
            if not any(action.startswith(AGENT_ACTION) for action in uses):
                continue
            agents += 1
            where = f"{path.name}: job {name}"
            assert not any(action.startswith(APP_TOKEN_ACTION) for action in uses), f"{where} mints the App token"
            assert APP_KEY not in json.dumps(job), f"{where} can read the App private key"
            permissions = job["permissions"] if "permissions" in job else workflow.get("permissions")
            allowed = AGENT_WRITE_ALLOWED.get((path.name, name), frozenset())
            writes = _write_scopes(permissions, where)
            assert writes <= allowed, f"{where} holds write access: {sorted(writes - allowed)}"
    assert agents, "no job runs anthropics/claude-code-action"


def test_claude_token_reaches_only_the_agent_step() -> None:
    seen = 0
    for path in _workflow_files():
        workflow = load_yaml(path)
        for where, text in _strings(workflow, ()):
            if CLAUDE_TOKEN not in text:
                continue
            seen += 1
            in_agent_with = (
                len(where) == 6
                and where[0] == "jobs"
                and where[2:5:2] == ("steps", "with")
                and _uses_agent(workflow["jobs"][where[1]]["steps"][where[3]])
            )
            assert in_agent_with, f"{path.name}: {'.'.join(map(str, where))} mentions {CLAUDE_TOKEN}"
    assert seen, f"no agent step receives {CLAUDE_TOKEN}"


def test_agent_steps_scrub_the_environment_of_their_subprocesses() -> None:
    for path, workflow, name, job, step in _agent_steps():
        env = {**(workflow.get("env") or {}), **(job.get("env") or {}), **(step.get("env") or {})}
        assert str(env.get(ENV_SCRUB)) == "1", f"{path.name}: job {name} does not set {ENV_SCRUB}=1"


def test_agents_cannot_run_the_scripts_they_write() -> None:
    for path, _, name, _, step in _agent_steps():
        where = f"{path.name}: job {name}"
        rules = _allowed_tools(str((step.get("with") or {}).get("claude_args", "")))
        assert rules, f"{where} has no --allowedTools list"
        for rule in rules:
            if rule != "Bash" and not rule.startswith("Bash("):
                continue
            command = rule[len("Bash(") : -1] if rule.startswith("Bash(") else ""
            assert command.strip(" :*"), f"{where} allows any shell command: {rule}"
            assert "factory-out/repros" not in command, f"{where} may run its own scripts: {rule}"
            assert not _INTERPRETER_RULE.fullmatch(command), f"{where} may run any Python file: {rule}"


def test_qa_agent_arguments_are_pinned() -> None:
    jobs = load_yaml(WORKFLOWS / "factory-qa.yml")["jobs"]
    (step,) = [step for step in jobs["triage"]["steps"] if _uses_agent(step)]
    assert step["with"]["claude_args"] == QA_CLAUDE_ARGS
    assert json.loads(step["with"]["settings"]) == AGENT_SETTINGS


def test_fixer_agent_arguments_are_pinned() -> None:
    steps = load_yaml(WORKFLOWS / "factory-fix.yml")["jobs"]["fix"]["steps"]
    (step,) = [step for step in steps if _uses_agent(step)]
    assert step["with"]["claude_args"] == FIXER_CLAUDE_ARGS
    assert json.loads(step["with"]["settings"]) == AGENT_SETTINGS
    assert step["with"]["allowed_bots"] == "${{ vars.FACTORY_APP_SLUG }}[bot],github-actions[bot]"
    assert step["with"]["github_token"] == "${{ secrets.GITHUB_TOKEN }}"
    assert step["with"]["prompt"] == "${{ steps.prompt.outputs.text }}"
    (prompt,) = [s for s in steps if s.get("id") == "prompt"]
    assert "cat .github/factory/prompts/fixer.md" in prompt["run"] and "factory-out/context.md" in prompt["run"]


def test_fix_workflow_triggers_jobs_and_concurrency() -> None:
    workflow = load_yaml(WORKFLOWS / "factory-fix.yml")
    assert workflow["on"]["issues"] == {"types": ["labeled"]}
    assert set(workflow["on"]["workflow_dispatch"]["inputs"]) == {"issue", "attempt"}
    assert workflow["permissions"] == {}
    group = workflow["concurrency"]["group"]
    assert "format('factory-fix-{0}', github.event.issue.number || inputs.issue)" in group
    assert workflow["concurrency"]["cancel-in-progress"] is False
    jobs = workflow["jobs"]
    assert set(jobs) == {"guard", "fix", "publish"}
    assert (jobs["fix"]["needs"], jobs["publish"]["needs"]) == ("guard", ["guard", "fix"])
    guard = jobs["guard"]
    assert "github.event.label.name == 'factory:ready'" in guard["if"]
    assert guard["permissions"] == {"contents": "read", "issues": "read"}
    attempt = next(s["run"] for s in guard["steps"] if s.get("id") == "attempt")
    assert "tools.factory.dispatch check-sender" in attempt and "tools.factory.fix guard" in attempt
    config = next(s["run"] for s in guard["steps"] if s.get("id") == "config")
    assert all(query in config for query in (".models.fixer", ".max_turns.fixer", "schemas/fix-report.json"))


def test_fix_job_holds_no_write_token_and_no_token_after_the_agent() -> None:
    job = load_yaml(WORKFLOWS / "factory-fix.yml")["jobs"]["fix"]
    assert job["permissions"] == {"checks": "read", "contents": "read", "issues": "read", "pull-requests": "read"}
    assert job["environment"] == "factory"  # for the Claude token of the agent step only
    assert job["timeout-minutes"] == 45
    assert job["container"]["image"] == PLAYWRIGHT_IMAGE
    assert job["env"] == {"HOME": "/root", "VISUAL_RENDERER": "playwright-1.64.0-noble-amd64"}
    steps = job["steps"]
    assert not any(str(s.get("uses", "")).startswith(APP_TOKEN_ACTION) for s in steps)
    agent = next(i for i, s in enumerate(steps) if _uses_agent(s))
    hooks = next(i for i, s in enumerate(steps) if "core.hooksPath /dev/null" in str(s.get("run", "")))
    assert hooks < agent
    # Before the agent only prepare reads GitHub, with the read-only token.
    for step in steps[:agent]:
        if any(reference in json.dumps(step) for reference in TOKEN_REFERENCES):
            assert "tools.factory.fix prepare" in str(step.get("run", "")), step
            assert step["env"]["GITHUB_TOKEN"] == "${{ github.token }}"
    # From the agent on, its code runs: no later step can reach a token.
    for step in steps[agent + 1 :]:
        assert not any(reference in json.dumps(step) for reference in TOKEN_REFERENCES), step
    later = " ".join(str(s.get("run", "")) for s in steps[agent + 1 :])
    for command in ("tools.factory.fix prove", "tools.factory.fix before-after", "git bundle create", SCANNER):
        assert command in later


def test_publish_job_holds_the_app_token_and_runs_no_fix_code() -> None:
    job = load_yaml(WORKFLOWS / "factory-fix.yml")["jobs"]["publish"]
    assert (job["runs-on"], job["environment"]) == ("ubuntu-24.04", "factory")
    assert "container" not in job and "!cancelled()" in job["if"]
    # GITHUB_TOKEN only dispatches the next attempt.
    assert job["permissions"] == {"actions": "write", "contents": "read"}
    steps = job["steps"]
    assert not any(_uses_agent(s) for s in steps)
    (checkout,) = [s for s in steps if str(s.get("uses", "")).startswith("actions/checkout")]
    assert checkout["with"] == {"ref": "${{ github.sha }}", "fetch-depth": 0, "persist-credentials": False}
    (download,) = [s for s in steps if str(s.get("uses", "")).startswith("actions/download-artifact")]
    assert download["with"] == {"name": "fix", "path": "factory-in/fix"}
    (app,) = [s for s in steps if str(s.get("uses", "")).startswith(APP_TOKEN_ACTION)]
    scopes = {k: v for k, v in app["with"].items() if k.startswith("permission-")}
    assert scopes == {"permission-contents": "write", "permission-issues": "write", "permission-pull-requests": "write"}
    commands = [str(s["run"]) for s in steps if s.get("run")]
    assert commands == ["uv sync --locked", commands[-1]]
    assert commands[-1].startswith("uv run python -m tools.factory.fix publish") and "--in factory-in/fix" in commands[-1]
    assert not any(runner in commands[-1] for runner in ("pytest", *AGENT_CODE_RUNNERS))
    env = steps[-1]["env"]
    assert (env["GH_TOKEN"], env["DISPATCH_TOKEN"]) == ("${{ steps.app.outputs.token }}", "${{ github.token }}")


def test_agents_read_only_the_workspace() -> None:
    for path, _, name, job, step in _agent_steps():
        where = f"{path.name}: job {name}"
        uses_git = (path.name, name) in GIT_AGENTS
        settings = (step.get("with") or {}).get("settings")
        assert settings and json.loads(settings) == AGENT_SETTINGS, f"{where} may read outside the workspace"
        claude_args = str((step.get("with") or {}).get("claude_args", ""))
        allowed = _allowed_tools(claude_args)
        assert "Read" not in allowed, f"{where} may read any path"
        outside = [r for r in allowed if r.startswith("Read(") and not r.startswith("Read(./")]
        assert not outside, f"{where} may read outside the workspace: {outside}"
        # Claude Code never consults Write(path) rules; Edit(path) rules govern the Write tool.
        assert not [r for r in allowed if r == "Write" or r.startswith("Write(")], f"{where}: use Edit(path)"
        denied = _tool_rules(claude_args, ("--disallowedTools", "--disallowed-tools"))
        missing = [rule for rule in (GIT_AGENT_DENIES if uses_git else AGENT_DENIES) if rule not in denied]
        assert not missing, f"{where} does not deny {missing}"
        steps = job["steps"]
        if uses_git:
            git_rules = [r for r in allowed if r.startswith("Bash(git")]
            assert set(git_rules) <= set(GIT_RULES), f"{where} may run other git commands: {git_rules}"
            checkouts = [s for s in steps if str(s.get("uses", "")).startswith("actions/checkout")]
            assert checkouts and all((s.get("with") or {}).get("persist-credentials") is False for s in checkouts), (
                f"{where} keeps a token in .git/config"
            )
            continue
        dropped = [i for i, s in enumerate(steps) if "rm -rf .git" in str(s.get("run", ""))]
        assert dropped and dropped[0] < steps.index(step), f"{where} keeps .git, where a token may land"


def test_agent_output_is_scanned_before_upload_and_kept_one_day() -> None:
    uploads: dict[tuple[str, str], str] = {}
    for path in _factory_workflows():
        for name, job in load_yaml(path)["jobs"].items():
            _check_agent_uploads(path.name, name, job, uploads)
    assert uploads == AGENT_ARTIFACTS


def _check_agent_uploads(workflow: str, name: str, job: dict[str, Any], uploads: dict[tuple[str, str], str]) -> None:
    steps = job.get("steps") or []
    agent_job = any(_uses_agent(s) for s in steps)
    for i, step in enumerate(steps):
        settings = step.get("with") or {}
        if not str(step.get("uses", "")).startswith("actions/upload-artifact"):
            continue
        artifact = settings.get("name")
        key = (workflow, artifact)
        # Every upload of a job that runs an agent is checked here, not only the known ones.
        assert key in AGENT_ARTIFACTS or not agent_job, f"{workflow}: job {name} uploads {artifact} unchecked"
        if key not in AGENT_ARTIFACTS:
            continue
        uploads[key] = name
        assert settings.get("retention-days") == 1, f"{artifact} is kept longer than a day"
        producers = [
            j
            for j, s in enumerate(steps[:i])
            if _uses_agent(s) or any(r in str(s.get("run", "")) for r in AGENT_CODE_RUNNERS)
        ]
        scans = [j for j, s in enumerate(steps[:i]) if SCANNER in str(s.get("run", ""))]
        assert scans and scans[-1] > max(producers, default=-1), f"{artifact} is not scanned last"
        command = str(steps[scans[-1]]["run"])
        paths = [line.strip().rstrip("/") for line in str(settings["path"]).splitlines() if line.strip()]
        assert all(path in command for path in paths), f"the scan before {artifact} misses {paths}"


def test_check_job_holds_only_a_read_token() -> None:
    job = load_yaml(WORKFLOWS / "factory-qa.yml")["jobs"]["check"]
    assert job["permissions"] == {"contents": "read"}
    assert "environment" not in job
    text = json.dumps(job)
    for reference in ("github.token", "secrets.", "GITHUB_TOKEN", "GH_TOKEN"):
        assert reference not in text, f"the check job mentions {reference}"
    checkouts = [s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/checkout")]
    assert checkouts and all((s.get("with") or {}).get("persist-credentials") is False for s in checkouts)


def test_file_job_takes_groups_from_the_probe_artifact() -> None:
    steps = load_yaml(WORKFLOWS / "factory-qa.yml")["jobs"]["file"]["steps"]
    probe = [
        (s.get("with") or {}).get("path")
        for s in steps
        if str(s.get("uses", "")).startswith("actions/download-artifact") and (s.get("with") or {}).get("name") == "probe"
    ]
    assert probe == ["factory-in/probe"]
    command = " ".join(str(s.get("run", "")) for s in steps if "tools.factory.file_issues" in str(s.get("run", "")))
    assert "--probe-out factory-in/probe" in command


def test_script_runners_hold_no_secret_and_no_write_token() -> None:
    found: set[str] = set()
    for path in _factory_workflows():
        workflow = load_yaml(path)
        for name, job in workflow["jobs"].items():
            commands = " ".join(str(step.get("run", "")) for step in job.get("steps") or [])
            runners = {runner for runner in SCRIPT_RUNNERS if runner in commands}
            if not runners:
                continue
            found |= runners
            where = f"{path.name}: job {name}"
            assert "environment" not in job, f"{where} runs agent scripts in an environment with secrets"
            assert "secrets." not in json.dumps(job), f"{where} runs agent scripts next to a secret"
            permissions = job["permissions"] if "permissions" in job else workflow.get("permissions")
            assert not _write_scopes(permissions, where), f"{where} runs agent scripts with a write token"
    assert found == set(SCRIPT_RUNNERS)


def test_factory_jobs_never_use_the_uv_cache() -> None:
    steps = [
        step
        for path in _factory_workflows()
        for job in load_yaml(path)["jobs"].values()
        for step in job.get("steps") or []
        if str(step.get("uses", "")).startswith(SETUP_UV)
    ]
    assert steps
    for step in steps:
        assert (step.get("with") or {}).get("enable-cache") in (False, "false"), step


def _uses_agent(step: Any) -> bool:
    return isinstance(step, dict) and str(step.get("uses", "")).startswith(AGENT_ACTION)


def _agent_steps() -> list[tuple[Path, dict[str, Any], str, dict[str, Any], dict[str, Any]]]:
    found = [
        (path, workflow, name, job, step)
        for path in _workflow_files()
        for workflow in [load_yaml(path)]
        for name, job in workflow["jobs"].items()
        for step in job.get("steps") or []
        if _uses_agent(step)
    ]
    assert found, "no job runs anthropics/claude-code-action"
    return found


def _strings(value: Any, where: tuple[Any, ...]) -> list[tuple[tuple[Any, ...], str]]:
    """Every mapping key and every string value, with the path that leads to it."""
    if isinstance(value, dict):
        items: list[tuple[tuple[Any, ...], str]] = []
        for key, child in value.items():
            items.append(((*where, key), str(key)))
            items += _strings(child, (*where, key))
        return items
    if isinstance(value, list):
        return [item for i, child in enumerate(value) for item in _strings(child, (*where, i))]
    return [(where, value)] if isinstance(value, str) else []


def _allowed_tools(claude_args: str) -> list[str]:
    return _tool_rules(claude_args, ("--allowedTools", "--allowed-tools"))


def _tool_rules(claude_args: str, flags: tuple[str, ...]) -> list[str]:
    """The rules after the given flags, split on commas as the action does."""
    tokens = shlex.split(claude_args)
    rules: list[str] = []
    for i, token in enumerate(tokens):
        if token not in flags:
            continue
        for value in tokens[i + 1 :]:
            if value.startswith("--"):
                break
            rules += [rule.strip() for rule in value.split(",") if rule.strip()]
    return rules


def _write_scopes(permissions: Any, where: str) -> set[str]:
    if permissions is None:
        pytest.fail(f"{where} declares no permissions, so its GITHUB_TOKEN gets the repository default")
    if isinstance(permissions, str):
        assert permissions == "read-all", f"{where} has permissions: {permissions}"
        return set()
    return {scope for scope, level in permissions.items() if level == "write"}


# --- a strict reader for the YAML that workflows use -----------------------------------------
# The lock has no YAML library and the factory adds no dependency but actionlint-py. The reader
# takes block mappings and sequences, plain and quoted one-line scalars, block scalars (| and >)
# and one-line flow collections; anything else raises, so a workflow in unsupported syntax fails
# these tests instead of slipping past them.

_PLAIN_KEY = re.compile(r"([A-Za-z0-9_][\w.\-/]*)[ ]*:(?:[ ]+(.*))?")
_QUOTED_KEY = re.compile(r"""('(?:[^']|'')*'|"(?:[^"\\]|\\.)*")[ ]*:(?:[ ]+(.*))?""")
_ESCAPES = {"n": "\n", "t": "\t", '"': '"', "\\": "\\", "/": "/", "0": "\0"}


def load_yaml(path: Path) -> Any:
    return _Reader(path.read_text(encoding="utf-8")).parse()


class _Reader:
    def __init__(self, text: str) -> None:
        self.lines = text.splitlines()
        self.i = 0

    def parse(self) -> Any:
        head = self._peek()
        if head is None:
            return None
        value = self._node(head[0])
        if self._peek() is not None:
            raise ValueError(f"line {self.i + 1}: unexpected indentation")
        return value

    def _peek(self) -> tuple[int, str] | None:
        """The indentation and text of the next line that is not blank or a comment."""
        while self.i < len(self.lines):
            line = self.lines[self.i]
            text = line.lstrip(" ")
            if text and not text.startswith("#"):
                if text.startswith("\t") or text.startswith(("---", "...", "? ", "%")):
                    raise ValueError(f"line {self.i + 1}: unsupported YAML: {text!r}")
                return len(line) - len(text), text.rstrip()
            self.i += 1
        return None

    def _node(self, indent: int) -> Any:
        head = self._peek()
        assert head is not None
        return self._sequence(indent) if _is_item(head[1]) else self._mapping(indent)

    def _mapping(self, indent: int) -> dict[str, Any]:
        result: dict[str, Any] = {}
        while (head := self._peek()) is not None and head[0] == indent and not _is_item(head[1]):
            key, rest = _split_key(head[1], self.i)
            if key in result:
                raise ValueError(f"line {self.i + 1}: duplicate key {key!r}")
            self.i += 1
            result[key] = self._value(rest, indent)
        if head is not None and head[0] > indent:
            raise ValueError(f"line {self.i + 1}: unexpected indentation")
        return result

    def _sequence(self, indent: int) -> list[Any]:
        items: list[Any] = []
        while (head := self._peek()) is not None and head[0] == indent and _is_item(head[1]):
            content = head[1][1:].lstrip(" ")
            if not content:
                self.i += 1
                items.append(self._value("", indent))
            elif _is_key(content):
                # The item is a mapping whose first key sits at the item's content column.
                column = indent + len(head[1]) - len(content)
                self.lines[self.i] = " " * column + content
                items.append(self._mapping(column))
            else:
                if _is_item(content) or content[0] in "|>":
                    raise ValueError(f"line {self.i + 1}: unsupported YAML: {head[1]!r}")
                items.append(_scalar(content, self.i))
                self.i += 1
        if head is not None and head[0] > indent:
            raise ValueError(f"line {self.i + 1}: unexpected indentation")
        return items

    def _value(self, rest: str, indent: int) -> Any:
        if rest and not rest.startswith("#"):
            return self._block_scalar(rest, indent) if rest[0] in "|>" else _scalar(rest, self.i - 1)
        head = self._peek()
        if head is not None and head[0] > indent:
            return self._node(head[0])
        if head is not None and head[0] == indent and _is_item(head[1]):
            return self._sequence(indent)
        return None

    def _block_scalar(self, header: str, indent: int) -> str:
        chomp = header[1:].split(" #")[0].strip()
        if chomp not in ("", "-", "+"):
            raise ValueError(f"line {self.i}: unsupported block scalar header {header!r}")
        lines: list[str] = []
        column: int | None = None
        while self.i < len(self.lines):
            line = self.lines[self.i]
            text = line.lstrip(" ")
            if text:
                current = len(line) - len(text)
                if current <= indent:
                    break
                column = current if column is None else column
                if current < column:
                    raise ValueError(f"line {self.i + 1}: block scalar line indented less than the first")
                lines.append(line[column:])
            else:
                lines.append("")
            self.i += 1
        while lines and not lines[-1]:
            lines.pop()
        if header[0] == "|":
            text = "\n".join(lines)
        else:  # folded: the lines of each paragraph joined by spaces
            paragraphs = "\n".join(lines).split("\n\n")
            text = "\n".join(" ".join(p.split("\n")) for p in paragraphs)
        return text if chomp == "-" or not text else text + "\n"


def _is_item(text: str) -> bool:
    return text == "-" or text.startswith("- ")


def _is_key(text: str) -> bool:
    return bool(_PLAIN_KEY.fullmatch(text) or _QUOTED_KEY.fullmatch(text))


def _split_key(text: str, line: int) -> tuple[str, str]:
    match = _PLAIN_KEY.fullmatch(text)
    if match:
        return match.group(1), (match.group(2) or "").strip()
    match = _QUOTED_KEY.fullmatch(text)
    if match:
        key, _ = _quoted(match.group(1), line)
        return key, (match.group(2) or "").strip()
    raise ValueError(f"line {line + 1}: expected 'key: value', got {text!r}")


def _scalar(text: str, line: int) -> Any:
    if text[0] in "'\"":
        value, end = _quoted(text, line)
        _comment_only(text[end:], line)
        return value
    if text[0] in "[{":
        return _flow(text, line)
    if text[0] in "&*!%@`|>":
        raise ValueError(f"line {line + 1}: unsupported YAML: {text!r}")
    cut = text.find(" #")
    return _plain(text if cut == -1 else text[:cut].rstrip())


def _plain(text: str) -> Any:
    if text in ("true", "True", "TRUE"):
        return True
    if text in ("false", "False", "FALSE"):
        return False
    if text in ("", "~", "null", "Null", "NULL"):
        return None
    return int(text) if re.fullmatch(r"-?[0-9]+", text) else text


def _quoted(text: str, line: int) -> tuple[str, int]:
    """The value of the quoted scalar at the start of `text` and the index just past it."""
    quote, out, i = text[0], [], 1
    while i < len(text):
        char = text[i]
        if quote == "'" and char == "'":
            if text[i + 1 : i + 2] != "'":
                return "".join(out), i + 1
            out.append("'")
            i += 2
            continue
        if quote == '"' and char == '"':
            return "".join(out), i + 1
        if quote == '"' and char == "\\":
            escape = text[i + 1 : i + 2]
            if escape not in _ESCAPES:
                raise ValueError(f"line {line + 1}: unsupported escape \\{escape}")
            out.append(_ESCAPES[escape])
            i += 2
            continue
        out.append(char)
        i += 1
    raise ValueError(f"line {line + 1}: unterminated or multi-line quoted scalar")


def _comment_only(tail: str, line: int) -> None:
    tail = tail.strip()
    if tail and not tail.startswith("#"):
        raise ValueError(f"line {line + 1}: unexpected text after a scalar: {tail!r}")


def _flow(text: str, line: int) -> Any:
    """A one-line flow sequence or mapping of scalars: [a, "b"], {}, {k: v}."""
    close = "]" if text[0] == "[" else "}"
    parts, current, i = [], "", 1
    while i < len(text):
        char = text[i]
        if char in "'\"":
            _, end = _quoted(text[i:], line)
            current += text[i : i + end]
            i += end
            continue
        if char in "[{":
            raise ValueError(f"line {line + 1}: nested flow collections are unsupported")
        if char == close:
            _comment_only(text[i + 1 :], line)
            if current.strip():
                parts.append(current.strip())
            break
        if char == ",":
            parts.append(current.strip())
            current = ""
        else:
            current += char
        i += 1
    else:
        raise ValueError(f"line {line + 1}: unterminated flow collection")
    if close == "]":
        return [_scalar(part, line) for part in parts]
    return dict(_split_key_value(part, line) for part in parts)


def _split_key_value(part: str, line: int) -> tuple[str, Any]:
    key, rest = _split_key(part, line)
    return key, _scalar(rest, line) if rest else None
