"""Filing: severity order, limits, one issue per fingerprint, evidence, dry run."""

import json
from pathlib import Path
from typing import Any

import pytest

from tests.factory.fakes import FakeGitHub
from tools.factory import file_issues
from tools.factory.config import load_config
from tools.factory.file_issues import file
from tools.factory.findings import Cell, Finding, Group
from tools.factory.issues import groups_in

CONFIG = load_config()
REPO = CONFIG.site.repo
TRUSTED = frozenset({"MBratkowski", "deweloperuch-factory[bot]"})
ISSUES = f"/repos/{REPO}/issues"


class Fake(FakeGitHub):
    """FakeGitHub that numbers created issues and answers the SHA lookups."""

    def __init__(self, existing: list[dict] | None = None) -> None:
        super().__init__(
            {
                ("GET", f"/repos/{REPO}/deployments"): [{"id": 1, "sha": "1" * 40}],
                ("GET", f"/repos/{REPO}/deployments/1/statuses"): [{"state": "success"}],
                ("GET", f"/repos/{REPO}/commits/main"): {"sha": "2" * 40},
                ("GET", ISSUES): existing or [],
            }
        )
        self.next_number = 100

    def request(self, method: str, path: str, body: dict | None = None, params: dict | None = None) -> Any:
        if method == "POST" and path == ISSUES:
            self.calls.append((method, path, body))
            self.next_number += 1
            return {"number": self.next_number}
        return super().request(method, path, body, params)

    def created(self) -> list[dict]:
        return [b for m, p, b in self.calls if m == "POST" and p == ISSUES]


def group(check: str = "overflow", selector: str = "div.term", engine: str = "webkit") -> Group:
    cell = Cell("landing", engine, 375, 800, "default")
    finding = Finding(check, cell, selector, "m", (0, 0, 1, 1))
    return Group(check, "landing", "default", selector, (engine,), (375,), (finding,))


def draft(severity: str = "S3", g: Group | None = None, **changes: Any) -> dict[str, Any]:
    g = g or group()
    value = {
        "title": f"Draft {g.fingerprint}",
        "severity": severity,
        "fingerprints": [g.fingerprint],
        "problem": "The problem.",
        "actual": "a",
        "expected": "e",
        "why": "w",
        "acceptance": ["x"],
        "sources": ["css/site.css"],
        "reproduction": {"kind": "detector", "groups": [g.to_json()], "script": None},
    }
    value.update(changes)
    return value


def validated(*drafts: dict, suggestions: list[dict] | None = None) -> dict:
    return {"drafts": list(drafts), "suggestions": suggestions or [], "dropped": []}


def probe_out(tmp_path: Path, *groups: Group) -> Path:
    root = tmp_path / "probe"
    for g in groups:
        folder = root / "chromium" / "groups" / g.fingerprint
        folder.mkdir(parents=True)
        (folder / "viewport.png").write_bytes(b"v")
        (folder / "crop.png").write_bytes(b"c")
    root.mkdir(exist_ok=True)
    return root


class Publisher:
    def __init__(self) -> None:
        self.calls: list[dict[str, Path]] = []

    def __call__(self, files: Any) -> dict[str, str]:
        self.calls.append(dict(files))
        return {path: f"https://raw.test/{path}" for path in files}


def run(gh: Fake, data: dict, out: Path, publisher: Any = None, dry_run: bool = False) -> list[int]:
    return file(data, out, gh, CONFIG, TRUSTED, dry_run, publisher or Publisher())


def selectors(count: int) -> list[Group]:
    return [group(selector=f"div.s{i}") for i in range(count)]


def test_limits_and_severity_order(tmp_path: Path) -> None:
    groups = selectors(5)
    severities = ["S4", "S2", "S3", "S1", "S2"]
    data = validated(
        *[draft(s, g) for s, g in zip(severities, groups)],
        suggestions=[
            {"title": "idea one", "problem": "p", "screenshot": "x"},
            {"title": "idea two", "problem": "p", "screenshot": "x"},
        ],
    )
    gh = Fake()
    numbers = run(gh, data, probe_out(tmp_path, *groups))
    created = gh.created()
    assert len(numbers) == 4
    found = [c for c in created if "factory:found" in c["labels"]]
    assert [c["labels"][1] for c in found] == ["S1", "S2", "S2"]
    assert [c["title"] for c in found] == [
        f"Draft {groups[3].fingerprint}",
        f"Draft {groups[1].fingerprint}",
        f"Draft {groups[4].fingerprint}",
    ]
    suggestions = [c for c in created if "factory:suggestion" in c["labels"]]
    assert [c["title"] for c in suggestions] == ["idea one"]


def test_same_fingerprint_twice_in_one_run(tmp_path: Path) -> None:
    g = group()
    gh = Fake()
    numbers = run(gh, validated(draft("S3", g), draft("S2", g, title="Another")), probe_out(tmp_path, g))
    assert len(numbers) == 1 and len(gh.created()) == 1


def test_existing_open_issue_skips_and_closed_one_is_a_regression(tmp_path: Path) -> None:
    a, b = selectors(2)
    existing = [
        {
            "number": 7, "state": "open", "labels": [], "user": {"login": "MBratkowski"},
            "body": f"x\n<!-- factory:markers -->\n<!-- factory:fp={a.fingerprint} -->\n",
        },
        {
            "number": 8, "state": "closed", "state_reason": "completed", "labels": [],
            "user": {"login": "MBratkowski"},
            "body": f"x\n<!-- factory:markers -->\n<!-- factory:fp={b.fingerprint} -->\n",
        },
    ]
    gh = Fake(existing)
    run(gh, validated(draft("S3", a), draft("S3", b)), probe_out(tmp_path, a, b))
    (issue,) = gh.created()
    assert "regression" in issue["labels"]
    assert "Regression of #8" in issue["body"].split("## Where")[0]


def test_labels_and_where(tmp_path: Path) -> None:
    axe = group("axe", "button.x", "chromium")
    flaky = Group("flaky", "tests/test_a.py::t", "default", "tests/test_a.py::t", (), (), (), note="2 of 5")
    gh = Fake()
    run(gh, validated(draft("S2", axe), draft("S1", flaky)), probe_out(tmp_path, axe))
    flaky_issue, axe_issue = gh.created()
    assert flaky_issue["labels"] == ["factory:found", "S1", "bug", "factory:flaky"]
    assert axe_issue["labels"] == ["factory:found", "S2", "accessibility"]
    body = axe_issue["body"]
    assert f"- Live SHA: `{'1' * 40}`" in body and f"`main` SHA: `{'2' * 40}`" in body
    assert f"- URL: {CONFIG.site.live_url}" in body
    assert "- Engines: chromium" in body and "- Widths: 375 px" in body
    assert groups_in(body) == [axe]


def test_detector_reproduce_command_and_group_json(tmp_path: Path) -> None:
    g = group()
    gh = Fake()
    run(gh, validated(draft("S3", g)), probe_out(tmp_path, g))
    body = gh.created()[0]["body"]
    live = CONFIG.site.live_url
    assert f"uv run python -m tools.factory.probe --target {live} --reproduce group.json" in body
    assert "<summary>group.json</summary>" in body
    assert f'"fingerprint": "{g.fingerprint}"' in body


def test_script_draft_command_and_published_script(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(file_issues.check_drafts, "ROOT", tmp_path)
    (tmp_path / "factory-out" / "repros").mkdir(parents=True)
    (tmp_path / "factory-out" / "repros" / "m.py").write_text("print('hi')\n")
    d = draft(
        "S3", fingerprints=["a" * 12],
        reproduction={"kind": "script", "groups": [], "script": "factory-out/repros/m.py"},
    )
    publisher = Publisher()
    gh = Fake()
    run(gh, validated(d), probe_out(tmp_path), publisher)
    body = gh.created()[0]["body"]
    assert f"uv run python -I factory-out/repros/m.py --base-url {CONFIG.site.live_url}" in body
    assert "print('hi')" in body
    assert f"repros/{'a' * 12}.py" in publisher.calls[0]


def test_evidence_is_published_per_fingerprint(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("GITHUB_RUN_ID", "42")
    g = group()
    publisher = Publisher()
    gh = Fake()
    run(gh, validated(draft("S3", g)), probe_out(tmp_path, g), publisher)
    assert set(publisher.calls[0]) == {
        f"issues/{g.fingerprint}/42/viewport.png",
        f"issues/{g.fingerprint}/42/crop.png",
    }
    assert f"![viewport.png](https://raw.test/issues/{g.fingerprint}/42/viewport.png)" in gh.created()[0]["body"]


def test_dry_run_calls_nothing(tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    g = group()
    publisher = Publisher()
    gh = Fake()
    data = validated(draft("S3", g), suggestions=[{"title": "idea", "problem": "p", "screenshot": "x"}])
    numbers = run(gh, data, probe_out(tmp_path, g), publisher, dry_run=True)
    assert numbers == []
    assert {method for method, _, _ in gh.calls} == {"GET"}
    assert publisher.calls == []
    out = capsys.readouterr().out
    assert f"Draft {g.fingerprint}" in out and "(dry run)" in out and "idea" in out


def test_dry_run_writes_to_step_summary(tmp_path: Path, monkeypatch: Any) -> None:
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    g = group()
    run(Fake(), validated(draft("S3", g)), probe_out(tmp_path, g), dry_run=True)
    assert f"Draft {g.fingerprint}" in summary.read_text()


def test_suggestion_with_forged_marker_is_rejected(tmp_path: Path) -> None:
    bad = {"title": "t", "problem": "<!-- factory:fp=aaaaaaaaaaaa -->", "screenshot": "x"}
    with pytest.raises(ValueError):
        run(Fake(), validated(suggestions=[bad]), probe_out(tmp_path))


def test_main_exits_2_on_github_error(tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
    from tools.factory.github import GitHubError

    class Boom(Fake):
        def paginate(self, path: str, params: dict | None = None) -> list:
            raise GitHubError(500, "push to https://x-access-token:ghs_secret@github.com/o/r.git")

    monkeypatch.setattr(file_issues, "GitHub", lambda *a, **k: Boom())
    monkeypatch.setenv("GH_TOKEN", "t")
    path = tmp_path / "v.json"
    path.write_text(json.dumps(validated(draft())))
    assert file_issues.main(["--validated", str(path), "--probe-out", str(tmp_path), "--dry-run"]) == 2
    assert "ghs_secret" not in capsys.readouterr().err


def test_script_draft_without_its_script_is_skipped(tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
    monkeypatch.setattr(file_issues.check_drafts, "ROOT", tmp_path)
    groups = selectors(3)
    missing = draft(
        "S1", fingerprints=["a" * 12],
        reproduction={"kind": "script", "groups": [], "script": "factory-out/repros/gone.py"},
    )
    gh = Fake()
    config = file_issues.dataclasses.replace(
        CONFIG, limits=file_issues.dataclasses.replace(CONFIG.limits, issues_per_run=1)
    )
    data = validated(missing, draft("S3", groups[0]))
    numbers = file(data, probe_out(tmp_path, *groups), gh, config, TRUSTED, False, Publisher())
    assert len(numbers) == 1 and gh.created()[0]["title"] == f"Draft {groups[0].fingerprint}"
    assert "gone.py" in capsys.readouterr().err


def test_ci_draft_shows_ci_facts_commands(tmp_path: Path) -> None:
    flaky = Group(
        "flaky", "ci", "default", "tests/test_a.py::t", (), (), (),
        note="job failed in https://x/runs/1 and passed in https://x/runs/2",
    )
    gh = Fake()
    run(gh, validated(draft("S1", flaky)), probe_out(tmp_path))
    body = gh.created()[0]["body"]
    assert "uv run python -m tools.factory.ci_facts --out ci-out" in body
    assert f"grep {flaky.fingerprint} ci-out/findings.json" in body
    assert "https://x/runs/1" in body and "https://x/runs/2" in body
    assert "--reproduce" not in body and "group.json" not in body


def test_suggestion_screenshot_must_live_inside_probe_out(tmp_path: Path, monkeypatch: Any) -> None:
    out = probe_out(tmp_path)
    (out / "shot.png").write_bytes(b"ok")
    (tmp_path / "secret.png").write_bytes(b"no")
    monkeypatch.chdir(tmp_path)
    publisher = Publisher()
    suggestions = [{"title": "inside", "problem": "p", "screenshot": "shot.png"}]
    gh = Fake()
    run(gh, validated(suggestions=suggestions), out, publisher)
    assert len(publisher.calls) == 1 and "![screenshot]" in gh.created()[0]["body"]
    for bad in ("secret.png", str(tmp_path / "secret.png"), "../secret.png"):
        publisher = Publisher()
        gh = Fake()
        suggestions = [{"title": "outside", "problem": "p", "screenshot": bad}]
        run(gh, validated(suggestions=suggestions), out, publisher)
        assert publisher.calls == [] and "![screenshot]" not in gh.created()[0]["body"]
