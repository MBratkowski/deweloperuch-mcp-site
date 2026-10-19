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


def write_findings(out: Path, live: Any = (), ci: Any = ()) -> None:
    """This run's probe and CI findings under the probe artifact, as the file job downloads them."""
    for name, groups in (("live", live), ("ci", ci)):
        folder = out / name
        folder.mkdir(parents=True, exist_ok=True)
        items = [g if isinstance(g, dict) else g.to_json() for g in groups]
        (folder / "findings.json").write_text(json.dumps({"target": name, "groups": items}))


def record_findings(out: Path, data: dict) -> Path:
    """Unless a test wrote its own, record the drafts' groups as this run's findings."""
    if not any(out.rglob("findings.json")):
        write_findings(out, [g for d in data["drafts"] for g in d["reproduction"]["groups"]])
    return out


def run(gh: Fake, data: dict, out: Path, publisher: Any = None, dry_run: bool = False) -> list[int]:
    return file(data, record_findings(out, data), gh, CONFIG, TRUSTED, dry_run, publisher or Publisher())


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


def test_a_real_run_files_nothing_without_the_app_bots_identity(tmp_path: Path, monkeypatch: Any) -> None:
    """D46: evidence is committed only under the App bot's noreply address; a real run that cannot
    resolve it (no slug here) files nothing and publishes nothing."""
    gh = Fake()
    monkeypatch.setattr(file_issues, "GitHub", lambda *a, **k: gh)
    monkeypatch.setattr(file_issues.evidence_module, "publish", lambda *a, **k: pytest.fail("published"))
    monkeypatch.setenv("GH_TOKEN", "t")
    monkeypatch.delenv("FACTORY_APP_SLUG", raising=False)
    path = tmp_path / "v.json"
    data = validated(draft())
    path.write_text(json.dumps(data))
    out = record_findings(probe_out(tmp_path, group()), data)
    assert file_issues.main(["--validated", str(path), "--probe-out", str(out)]) == 2
    assert gh.created() == []


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
    out = record_findings(probe_out(tmp_path, *groups), data)
    numbers = file(data, out, gh, config, TRUSTED, False, Publisher())
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


@pytest.mark.parametrize(
    "content",
    [
        "TOKEN = 'sk-ant-oat01-AbCdEf'",
        "TOKEN = 'ghs_AbCdEf012345'",
        "KEY = '-----BEGIN OPENSSH PRIVATE KEY-----'",
        "# <!-- factory:fp=bbbbbbbbbbbb -->",
    ],
)
def test_script_with_secret_shaped_text_is_never_published(
    tmp_path: Path, monkeypatch: Any, capsys: Any, content: str
) -> None:
    monkeypatch.setattr(file_issues.check_drafts, "ROOT", tmp_path)
    repros = tmp_path / "factory-out" / "repros"
    repros.mkdir(parents=True)
    (repros / "leak.py").write_text(f"{content}\nprint('REPRODUCED: x')\n")
    leak = draft(
        "S1", fingerprints=["a" * 12],
        reproduction={"kind": "script", "groups": [], "script": "factory-out/repros/leak.py"},
    )
    others = selectors(3)
    publisher = Publisher()
    gh = Fake()
    numbers = run(gh, validated(leak, *[draft("S3", g) for g in others]), probe_out(tmp_path, *others), publisher)
    assert not any(path.startswith("repros/") for call in publisher.calls for path in call)
    assert all(content not in issue["body"] for issue in gh.created())
    # The skipped draft does not use up one of the per-run issues.
    assert len(numbers) == CONFIG.limits.issues_per_run == 3
    assert "leak.py" in capsys.readouterr().err


def test_groups_are_rederived_from_this_runs_findings(tmp_path: Path) -> None:
    g = group()
    planted = {**g.to_json(), "note": "ghs_planted", "extra": "sk-ant-planted"}
    out = probe_out(tmp_path, g)
    write_findings(out, live=[g])
    gh = Fake()
    run(gh, validated(draft("S3", g, reproduction={"kind": "detector", "groups": [planted], "script": None})), out)
    body = gh.created()[0]["body"]
    assert "planted" not in body
    assert groups_in(body) == [g]


def test_draft_with_an_unknown_fingerprint_is_skipped_and_not_counted(tmp_path: Path, capsys: Any) -> None:
    others = selectors(3)
    unknown = group(selector="div.unknown")
    out = probe_out(tmp_path, *others, unknown)
    write_findings(out, live=others)
    gh = Fake()
    numbers = run(gh, validated(draft("S1", unknown), *[draft("S3", g) for g in others]), out)
    assert len(numbers) == 3
    assert f"Draft {unknown.fingerprint}" not in [c["title"] for c in gh.created()]
    err = capsys.readouterr().err
    assert unknown.fingerprint in err and "unknown fingerprint" in err


def test_ci_draft_groups_come_from_the_ci_findings(tmp_path: Path) -> None:
    flaky = Group("flaky", "ci", "default", "tests/test_a.py::t", (), (), (), note="failed in https://x/runs/1")
    planted = {**flaky.to_json(), "note": "ghp_planted"}
    out = probe_out(tmp_path)
    write_findings(out, ci=[flaky])
    gh = Fake()
    run(gh, validated(draft("S1", flaky, reproduction={"kind": "detector", "groups": [planted], "script": None})), out)
    body = gh.created()[0]["body"]
    assert "planted" not in body and "https://x/runs/1" in body
