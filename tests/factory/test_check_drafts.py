"""Every draft's reproduction must exit 1 on the live site and on main; code disposes."""

from pathlib import Path
from typing import Any

import json
import os
import sys
import time

import pytest

from tools.factory import check_drafts
from tools.factory.check_drafts import check
from tools.factory.findings import Cell, Finding, Group
from tools.factory.issues import load_drafts

LIVE = "https://live.test/"
MAIN = "http://127.0.0.1:1/"


def group(check_name: str = "overflow") -> Group:
    cell = Cell("landing", "webkit", 375, 800, "default")
    finding = Finding(check_name, cell, "div.term", "m", (0, 0, 1, 1))
    return Group(check_name, "landing", "default", "div.term", ("webkit",), (375,), (finding,))


def draft_dict(fp: str = "a" * 12, *, check_name: str = "overflow", script: str | None = None) -> dict[str, Any]:
    g = group(check_name)
    reproduction = (
        {"kind": "script", "groups": [], "script": script}
        if script
        else {"kind": "detector", "groups": [g.to_json()], "script": None}
    )
    return {
        "title": f"Draft {fp}",
        "severity": "S3",
        "fingerprints": [fp],
        "problem": "p",
        "actual": "a",
        "expected": "e",
        "why": "w",
        "acceptance": ["x"],
        "sources": [],
        "reproduction": reproduction,
    }


def drafts_of(*items: dict[str, Any]):
    drafts, _, _ = load_drafts({"drafts": list(items), "suggestions": [], "dropped": []})
    return drafts


def never_script(script: str, url: str) -> int:
    raise AssertionError("no script expected")


def by_url(live: int, main: int):
    return lambda _group, url: live if url == LIVE else main


def test_draft_survives_when_both_reproduce() -> None:
    drafts = drafts_of(draft_dict())
    kept, dropped = check(drafts, LIVE, MAIN, never_script, by_url(1, 1))
    assert kept == drafts and dropped == []


def test_draft_dropped_when_main_is_clean() -> None:
    kept, dropped = check(drafts_of(draft_dict()), LIVE, MAIN, never_script, by_url(1, 0))
    assert kept == []
    assert [(d.fingerprint, d.reason) for d in dropped] == [("a" * 12, "fixed on main, not deployed")]


def test_draft_dropped_when_live_is_clean() -> None:
    kept, dropped = check(drafts_of(draft_dict()), LIVE, MAIN, never_script, by_url(0, 1))
    assert kept == []
    assert dropped[0].reason == "does not reproduce on the live site"


def test_error_on_either_url_is_a_reproduction_error() -> None:
    for live, main in ((2, 1), (1, 2)):
        kept, dropped = check(drafts_of(draft_dict()), LIVE, MAIN, never_script, by_url(live, main))
        assert kept == [] and dropped[0].reason == "reproduction error"


def test_group_that_cannot_be_reproduced_is_a_reproduction_error() -> None:
    def refuse(_group: Group, _url: str) -> int:
        raise ValueError("flaky findings cannot be reproduced in a browser")

    kept, dropped = check(drafts_of(draft_dict()), LIVE, MAIN, never_script, refuse)
    assert kept == [] and dropped[0].reason == "reproduction error"


def test_script_draft_runs_the_script_on_both_urls() -> None:
    seen: list[tuple[str, str]] = []

    def run_script(script: str, url: str) -> int:
        seen.append((script, url))
        return 1

    drafts = drafts_of(draft_dict(script="factory-out/repros/m.py"))
    kept, _ = check(drafts, LIVE, MAIN, run_script, by_url(2, 2))
    assert kept == drafts
    assert seen == [("factory-out/repros/m.py", LIVE), ("factory-out/repros/m.py", MAIN)]


def test_ci_group_is_verified_by_its_fingerprint_not_a_browser() -> None:
    flaky = group("flaky")
    fp = flaky.fingerprint
    drafts = drafts_of(draft_dict(fp, check_name="flaky"))

    def no_browser(_group: Group, _url: str) -> int:
        raise AssertionError("a browser must not run")

    kept, _ = check(drafts, LIVE, MAIN, never_script, no_browser, ci_fingerprints={fp})
    assert kept == drafts
    kept, dropped = check(drafts, LIVE, MAIN, never_script, no_browser, ci_fingerprints=set())
    assert kept == [] and dropped[0].reason == "not present in this run's CI facts"


def make_script(root: Path, body: str) -> str:
    folder = root / "factory-out" / "repros"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "m.py").write_text(body)
    return "factory-out/repros/m.py"


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("print('REPRODUCED: 640 > 375')\nraise SystemExit(1)", 1),
        ("print('NOT REPRODUCED')", 0),
        ("print('REPRODUCED: x')\nraise SystemExit(3)", 2),
        ("print('REPRODUCED: x')\nraise SystemExit(137)", 2),
        ("import os, signal\nprint('REPRODUCED: x', flush=True)\nos.kill(os.getpid(), signal.SIGKILL)", 2),
        ("print('NOT REPRODUCED')\nraise SystemExit(3)", 2),
        ("raise SystemExit(1)", 2),
        ("print('all fine')", 2),
        ("print('REPRODUCED: x')\nprint('NOT REPRODUCED')", 2),
        ("raise TimeoutError('selector')", 2),
        ("import sys\nprint('REPRODUCED: x')\nsys.stderr.write('Traceback (most recent call last)')\nraise SystemExit(1)", 2),
    ],
)
def test_run_script_contract(body: str, expected: int, tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(check_drafts, "ROOT", tmp_path)
    assert check_drafts.run_script(make_script(tmp_path, body), LIVE) == expected


def test_run_script_timeout_and_bad_path(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(check_drafts, "ROOT", tmp_path)
    monkeypatch.setattr(check_drafts, "SCRIPT_TIMEOUT_SECONDS", 1)
    script = make_script(tmp_path, "import time\ntime.sleep(10)")
    assert check_drafts.run_script(script, LIVE) == 2
    assert check_drafts.run_script("../evil.py", LIVE) == 2


MARK = "import pathlib, sys\npathlib.Path(sys.argv[0]).with_suffix('.ran').write_text('x')\nprint('NOT REPRODUCED')"


@pytest.mark.parametrize("account", ["no-such-account-x9", "current"])
def test_a_script_user_that_cannot_be_used_fails_closed(
    account: str, tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    """D40: with FACTORY_SCRIPT_USER set, a script runs as that unprivileged account or not at all
    (an unknown account, or a process that is not root and so cannot switch)."""
    import pwd

    monkeypatch.setattr(check_drafts, "ROOT", tmp_path)
    name = pwd.getpwuid(os.getuid()).pw_name if account == "current" else account
    monkeypatch.setenv(check_drafts.SCRIPT_USER_ENV, name)
    if os.geteuid() == 0 and account == "current":
        pytest.skip("root may switch accounts")
    script = make_script(tmp_path, MARK)
    assert check_drafts.run_script(script, LIVE) == 2
    assert not (tmp_path / "factory-out" / "repros" / "m.ran").exists()
    assert "not run" in capsys.readouterr().err


def test_a_script_runs_as_the_script_user_with_a_home_of_its_own(tmp_path: Path, monkeypatch: Any) -> None:
    """D40: as root, the script is started as FACTORY_SCRIPT_USER (uid, gid, no supplementary group)
    with a fresh HOME owned by it, removed afterwards; the job's other steps stay root."""
    import pwd

    monkeypatch.setattr(check_drafts, "ROOT", tmp_path)
    monkeypatch.setenv(check_drafts.SCRIPT_USER_ENV, "pwuser")
    monkeypatch.setattr(check_drafts.os, "geteuid", lambda: 0)
    entry = pwd.struct_passwd(("pwuser", "x", 1000, 1001, "", "/home/pwuser", "/bin/bash"))
    monkeypatch.setattr(check_drafts.pwd, "getpwnam", lambda name: entry)
    owned: list[tuple[str, int, int]] = []
    monkeypatch.setattr(check_drafts.os, "chown", lambda path, uid, gid: owned.append((str(path), uid, gid)))
    seen: dict[str, Any] = {}

    def popen(*args: Any, **kwargs: Any) -> Any:
        seen.update(kwargs)
        seen["home_existed"] = Path(kwargs["env"]["HOME"]).is_dir()
        raise OSError("not started in a test")

    monkeypatch.setattr(check_drafts.subprocess, "Popen", popen)
    assert check_drafts.run_script(make_script(tmp_path, MARK), LIVE) == 2
    assert (seen["user"], seen["group"], seen["extra_groups"]) == (1000, 1001, [])
    home = seen["env"]["HOME"]
    assert seen["home_existed"] and owned == [(home, 1000, 1001)] and not Path(home).exists()
    assert seen["start_new_session"] is True


@pytest.mark.parametrize("uid, gid", [(0, 1001), (1000, 0)])
def test_the_script_user_is_never_root(uid: int, gid: int, monkeypatch: Any) -> None:
    import pwd

    monkeypatch.setenv(check_drafts.SCRIPT_USER_ENV, "pwuser")
    monkeypatch.setattr(check_drafts.os, "geteuid", lambda: 0)
    entry = pwd.struct_passwd(("pwuser", "x", uid, gid, "", "/home/pwuser", "/bin/bash"))
    monkeypatch.setattr(check_drafts.pwd, "getpwnam", lambda name: entry)
    with pytest.raises(check_drafts.ScriptUserError):
        check_drafts.script_user()


def test_without_a_script_user_scripts_run_as_before(monkeypatch: Any) -> None:
    monkeypatch.delenv(check_drafts.SCRIPT_USER_ENV, raising=False)
    assert check_drafts.script_user() is None


def test_ignored_lists_findings_in_neither_a_draft_nor_dropped() -> None:
    """M4: a finding of this run the agent neither drafted nor dropped is reported, not lost."""
    drafted, dropped, left = group("overflow"), group("axe"), group("target-size")
    flaky = Group("flaky", "ci", "default", "tests/e2e/test_x.py::test_y", (), (), (), note="n")
    canonical = {g.fingerprint: g.to_json() for g in (drafted, dropped, left, flaky)}
    incoming = {
        "drafts": [draft_dict(drafted.fingerprint)],
        "suggestions": [],
        "dropped": [{"fingerprint": dropped.fingerprint, "reason": "a false alarm"}],
    }
    found = check_drafts.ignored(incoming, canonical)
    assert found == sorted(
        [
            {"fingerprint": left.fingerprint, "check": "target-size", "page": "landing", "selector": "div.term"},
            {"fingerprint": flaky.fingerprint, "check": "flaky", "page": "ci", "selector": "tests/e2e/test_x.py::test_y"},
        ],
        key=lambda item: item["fingerprint"],
    )
    assert check_drafts.ignored({**incoming, "dropped": [*incoming["dropped"], {"fingerprint": left.fingerprint, "reason": "r"},
                                                          {"fingerprint": flaky.fingerprint, "reason": "r"}]}, canonical) == []


def test_validated_json_keeps_survivors_limits_suggestions_and_extends_dropped() -> None:
    incoming = {
        "drafts": [draft_dict("a" * 12), draft_dict("b" * 12, check_name="axe")],
        "suggestions": [
            {"title": "s1", "problem": "p", "screenshot": "x"},
            {"title": "s2", "problem": "p", "screenshot": "x"},
        ],
        "dropped": [{"fingerprint": "c" * 12, "reason": "old"}],
    }

    def run_group(g: Group, url: str) -> int:
        return 1 if g.check == "overflow" or url == LIVE else 0

    canonical = {"a" * 12: group().to_json(), "b" * 12: group("axe").to_json()}
    result = check_drafts.build_validated(
        incoming, LIVE, MAIN, never_script, run_group, None, suggestions_per_run=1, canonical=canonical
    )
    assert result["drafts"] == [incoming["drafts"][0]]
    assert [s["title"] for s in result["suggestions"]] == ["s1"]
    assert result["dropped"] == [
        {"fingerprint": "c" * 12, "reason": "old"},
        {"fingerprint": "b" * 12, "reason": "fixed on main, not deployed"},
    ]


def test_agent_groups_are_replaced_by_this_runs_groups() -> None:
    canonical = group()
    planted = {**canonical.to_json(), "note": "ghs_planted", "leak": "sk-ant-planted"}
    incoming = {"drafts": [draft_dict(canonical.fingerprint)], "suggestions": [], "dropped": []}
    incoming["drafts"][0]["reproduction"]["groups"] = [planted]
    seen: list[Group] = []

    def run_group(g: Group, _url: str) -> int:
        seen.append(g)
        return 1

    result = check_drafts.build_validated(
        incoming, LIVE, MAIN, never_script, run_group, None, suggestions_per_run=1,
        canonical={canonical.fingerprint: canonical.to_json()},
    )
    assert result["drafts"][0]["reproduction"]["groups"] == [canonical.to_json()]
    assert seen == [canonical, canonical]
    assert "planted" not in json.dumps(result)


def test_unknown_fingerprint_is_dropped_before_any_reproduction() -> None:
    def no_browser(_group: Group, _url: str) -> int:
        raise AssertionError("an unknown fingerprint must not be reproduced")

    incoming = {"drafts": [draft_dict("d" * 12)], "suggestions": [], "dropped": []}
    result = check_drafts.build_validated(
        incoming, LIVE, MAIN, never_script, no_browser, None, suggestions_per_run=1,
        canonical={group().fingerprint: group().to_json()},
    )
    assert result["drafts"] == []
    assert result["dropped"] == [{"fingerprint": "d" * 12, "reason": "unknown fingerprint"}]


def test_ci_draft_is_rebuilt_from_the_ci_findings() -> None:
    flaky = Group("flaky", "ci", "default", "tests/e2e/test_docs.py::test_toc", (), (), (), note="failed in run 1")
    planted = {**flaky.to_json(), "note": "ghp_planted"}
    raw = draft_dict(flaky.fingerprint)
    raw["reproduction"]["groups"] = [planted]

    def no_browser(_group: Group, _url: str) -> int:
        raise AssertionError("a CI draft is not reproduced in a browser")

    result = check_drafts.build_validated(
        {"drafts": [raw], "suggestions": [], "dropped": []}, LIVE, MAIN, never_script, no_browser,
        {flaky.fingerprint}, suggestions_per_run=1, canonical={flaky.fingerprint: flaky.to_json()},
    )
    assert result["drafts"][0]["reproduction"]["groups"] == [flaky.to_json()]
    assert "planted" not in json.dumps(result)


def test_script_drafts_carry_no_groups_and_need_no_known_fingerprint() -> None:
    raw = draft_dict("e" * 12, script="factory-out/repros/m.py")
    result = check_drafts.build_validated(
        {"drafts": [raw], "suggestions": [], "dropped": []}, LIVE, MAIN, lambda _s, _u: 1,
        by_url(0, 0), None, suggestions_per_run=1, canonical={},
    )
    assert result["drafts"] == [raw]


def test_canonical_groups_come_from_the_live_and_ci_findings(tmp_path: Path) -> None:
    live = group()
    flaky = Group("flaky", "ci", "default", "tests/x.py::test_y", (), (), ())
    probe_file = tmp_path / "live.json"
    probe_file.write_text(json.dumps({"target": "https://live.test/", "groups": [live.to_json()]}))
    ci_file = tmp_path / "ci.json"
    ci_file.write_text(json.dumps({"target": "ci", "groups": [flaky.to_json()]}))
    assert check_drafts.canonical_groups(probe_file, ci_file) == {
        live.fingerprint: live.to_json(),
        flaky.fingerprint: flaky.to_json(),
    }
    assert check_drafts.canonical_groups(probe_file, None) == {live.fingerprint: live.to_json()}


def _gone(pid: int, seconds: float = 5.0) -> bool:
    """True once `pid` no longer exists (a zombie waiting for init to reap it counts as gone)."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        status = Path(f"/proc/{pid}/status")
        if status.exists() and "State:\tZ" in status.read_text():
            return True
        time.sleep(0.05)
    return False


@pytest.mark.parametrize("output", ["subprocess.DEVNULL", "None"], ids=["detached", "inherits-the-output"])
def test_no_process_a_script_starts_outlives_it(tmp_path: Path, monkeypatch: Any, output: str) -> None:
    monkeypatch.setattr(check_drafts, "ROOT", tmp_path)
    pid_file = tmp_path / "child.pid"
    body = (
        "import subprocess, sys\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],"
        f" stdout={output}, stderr={output})\n"
        f"open({str(pid_file)!r}, 'w').write(str(child.pid))\n"
        "print('REPRODUCED: x')\n"
        "raise SystemExit(1)\n"
    )
    started = time.monotonic()
    assert check_drafts.run_script(make_script(tmp_path, body), LIVE) == 1
    assert time.monotonic() - started < 30
    assert _gone(int(pid_file.read_text()))


def test_a_timed_out_script_takes_its_children_with_it(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(check_drafts, "ROOT", tmp_path)
    monkeypatch.setattr(check_drafts, "SCRIPT_TIMEOUT_SECONDS", 1)
    pid_file = tmp_path / "child.pid"
    body = (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        f"open({str(pid_file)!r}, 'w').write(str(child.pid))\n"
        "time.sleep(30)\n"
    )
    assert check_drafts.run_script(make_script(tmp_path, body), LIVE) == 2
    assert _gone(int(pid_file.read_text()))
