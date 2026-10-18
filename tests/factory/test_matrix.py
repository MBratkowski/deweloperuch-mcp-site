"""The matrix run: cell set, artifacts, reference rendering and reproduction."""

import http.server
import json
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Playwright

from tools.factory import probe
from tools.factory.config import load_config
from tools.factory.findings import Cell, Group
from tools.factory.probe import (
    DEFAULT_REFERENCE_HOSTS,
    Target,
    build_cells,
    checks_for_mode,
    reproduce,
    run_matrix,
)


def test_cell_count() -> None:
    config = load_config()
    cells = build_cells(config.matrix, ["landing", "docs"])
    assert len(cells) == 132
    assert len(set(cells)) == 132
    by_mode = {m: sum(1 for c in cells if c.mode == m) for m in {c.mode for c in cells}}
    assert by_mode["default"] == 78
    assert by_mode["menu"] == 24 and by_mode["keyboard"] == 12
    assert by_mode["no-js"] + by_mode["reduced-motion"] + by_mode["short"] == 18
    assert {c.height for c in cells if c.mode == "short"} == {500}


def test_checks_per_mode() -> None:
    names = list(probe_checks())
    assert checks_for_mode("default", names) == names
    assert "overlap" not in checks_for_mode("menu", names)
    assert len(checks_for_mode("menu", names)) == len(names) - 1
    assert checks_for_mode("keyboard", names) == ["focus"]
    assert checks_for_mode("keyboard", ["overflow"]) == []


def probe_checks() -> list[str]:
    from tools.factory.checks import CHECKS

    return list(CHECKS)


def test_run_matrix_writes_artifacts(
    playwright: Playwright, fixtures_url: str, probe_cell: Callable[..., Cell], tmp_path: Path
) -> None:
    cells = [probe_cell("canary/overflow.html", 375), probe_cell("canary/overflow.html", 390)]
    groups = run_matrix(playwright, Target("fixtures", fixtures_url), cells, tmp_path, ["overflow"])
    assert len(groups) == 1
    data = json.loads((tmp_path / "findings.json").read_text())
    assert data["target"] == "fixtures" and data["base_url"] == fixtures_url
    assert len(data["groups"]) == 1
    fp = groups[0].fingerprint
    assert data["groups"][0]["fingerprint"] == fp
    assert (tmp_path / "groups" / fp / "viewport.png").is_file()
    assert (tmp_path / "groups" / fp / "crop.png").is_file()
    assert (tmp_path / "shots" / "canary_overflow.html-chromium-375-default.png").is_file() or list(
        (tmp_path / "shots").glob("*-chromium-375-default.png")
    )
    assert list((tmp_path / "sheets").glob("*-chromium.png"))


def test_clean_run_writes_empty_findings(
    playwright: Playwright, fixtures_url: str, probe_cell: Callable[..., Cell], tmp_path: Path
) -> None:
    cells = [probe_cell("clean/index.html", 375)]
    assert run_matrix(playwright, Target("f", fixtures_url), cells, tmp_path, ["overflow"]) == []
    assert json.loads((tmp_path / "findings.json").read_text())["groups"] == []


class _Unavailable(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(503)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def unavailable_url() -> Iterator[str]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Unavailable)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/"
    server.shutdown()
    server.server_close()


def test_failing_site_leaves_no_findings(
    playwright: Playwright, unavailable_url: str, probe_cell: Callable[..., Cell], tmp_path: Path
) -> None:
    with pytest.raises(probe.ProbeError):
        run_matrix(playwright, Target("down", unavailable_url), [probe_cell("", 375)], tmp_path)
    assert not (tmp_path / "findings.json").exists()


def _cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "tools.factory.probe", *args],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
    )


def test_cli_exits_2_on_a_failing_site(unavailable_url: str, tmp_path: Path) -> None:
    result = _cli("--target", unavailable_url, "--out", str(tmp_path), "--only", "overflow", "--engines", "chromium")
    assert result.returncode == 2, result.stderr
    assert "cannot open" in result.stderr and "503" in result.stderr
    assert not (tmp_path / "findings.json").exists()


def test_stale_findings_do_not_survive_a_failed_run(
    playwright: Playwright, unavailable_url: str, probe_cell: Callable[..., Cell], tmp_path: Path
) -> None:
    (tmp_path / "findings.json").write_text("{}")
    (tmp_path / "groups").mkdir()
    with pytest.raises(probe.ProbeError):
        run_matrix(playwright, Target("down", unavailable_url), [probe_cell("", 375)], tmp_path)
    assert not (tmp_path / "findings.json").exists()
    assert not (tmp_path / "groups").exists()


def test_cli_rejects_unknown_check_and_engine(tmp_path: Path) -> None:
    assert _cli("--target", "main", "--out", str(tmp_path), "--only", "nope").returncode == 2
    assert _cli("--target", "main", "--out", str(tmp_path), "--engines", "opera").returncode == 2


def test_cli_reproduce_crashes_exit_2(tmp_path: Path) -> None:
    flaky = tmp_path / "flaky.json"
    flaky.write_text(json.dumps(Group("flaky", "p", "default", "x", ("chromium",), (375,), ()).to_json()))
    result = _cli("--target", "http://127.0.0.1:9/", "--reproduce", str(flaky))
    assert result.returncode == 2 and "reproduction failed" in result.stderr
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    result = _cli("--target", "http://127.0.0.1:9/", "--reproduce", str(bad))
    assert result.returncode == 2 and "reproduction failed" in result.stderr


def test_reproduce_exit_codes(
    playwright: Playwright,
    fixtures_url: str,
    unavailable_url: str,
    probe_cell: Callable[..., Cell],
    tmp_path: Path,
) -> None:
    canary = probe_cell("canary/overflow.html", 375)
    groups = run_matrix(playwright, Target("f", fixtures_url), [canary], tmp_path, ["overflow"])
    assert reproduce(playwright, groups[0], fixtures_url) == 1
    # the same group against a page that no longer has the defect
    clean = Group(
        groups[0].check,
        "clean/index.html",
        groups[0].mode,
        groups[0].selector,
        groups[0].engines,
        groups[0].widths,
        tuple(
            type(f)(f.check, probe_cell("clean/index.html", 375), f.selector, f.measurement, f.box)
            for f in groups[0].findings
        ),
        groups[0].breakpoints,
    )
    assert reproduce(playwright, clean, fixtures_url) == 0
    assert reproduce(playwright, groups[0], unavailable_url) == 2


@pytest.mark.parametrize("check", ["flaky", "deploy-stale"])
def test_reproduce_rejects_non_browser_checks(
    check: str, playwright: Playwright, fixtures_url: str, probe_cell: Callable[..., Cell]
) -> None:
    group = Group(check, "p", "default", "x", ("chromium",), (375,), ())
    with pytest.raises(ValueError):
        reproduce(playwright, group, fixtures_url)


def test_default_reference_hosts() -> None:
    assert DEFAULT_REFERENCE_HOSTS == frozenset({"fonts.googleapis.com", "fonts.gstatic.com"})


def test_reference_blocks_all_but_fonts(
    playwright: Playwright, fixtures_url: str, tmp_path: Path
) -> None:
    design = tmp_path / "design.html"
    design.write_text(
        "<!doctype html><title>d</title>"
        f'<link rel="stylesheet" href="{fixtures_url}clean/index.html">'
        '<img src="https://example.com/x.png"><img src="https://fonts.gstatic.com/y.png">'
        "<p>design</p>"
    )
    aborted: list[str] = []
    out = probe.render_reference(
        playwright, design, tmp_path / "out", allowed_hosts=frozenset({"127.0.0.1"}), aborted=aborted
    )
    assert out == tmp_path / "out" / "reference.png" and out.is_file()
    assert sorted(aborted) == ["https://example.com/x.png", "https://fonts.gstatic.com/y.png"]


def test_cli_reproduce_accepts_a_group_array(tmp_path: Path) -> None:
    flaky = Group("flaky", "p", "default", "x", ("chromium",), (375,), ()).to_json()
    path = tmp_path / "groups.json"
    path.write_text(json.dumps([flaky, flaky]))
    result = _cli("--target", "http://127.0.0.1:9/", "--reproduce", str(path))
    assert result.returncode == 2 and "reproduction failed" in result.stderr


def test_reproduce_any_codes(monkeypatch: pytest.MonkeyPatch) -> None:
    results = iter([0, 1, 2])
    monkeypatch.setattr(probe, "reproduce", lambda *_: next(results))
    group = Group("overflow", "p", "default", "x", ("chromium",), (375,), ())
    assert probe.reproduce_any(None, [group] * 3, "u") == 1  # type: ignore[arg-type]
    for codes, expected in (([0, 0], 0), ([0, 2], 2), ([2, 1], 1)):
        it = iter(codes)
        monkeypatch.setattr(probe, "reproduce", lambda *_: next(it))
        assert probe.reproduce_any(None, [group] * len(codes), "u") == expected  # type: ignore[arg-type]


def test_cli_reproduce_array_with_canary(
    playwright: Playwright, fixtures_url: str, probe_cell: Callable[..., Cell], tmp_path: Path
) -> None:
    canary = probe_cell("canary/overflow.html", 375)
    (group,) = run_matrix(playwright, Target("f", fixtures_url), [canary], tmp_path, ["overflow"])
    clean = Group(
        group.check, "clean/index.html", group.mode, group.selector, group.engines, group.widths,
        tuple(type(f)(f.check, probe_cell("clean/index.html", 375), f.selector, f.measurement, f.box) for f in group.findings),
        group.breakpoints,
    )
    mixed = tmp_path / "mixed.json"
    mixed.write_text(json.dumps([clean.to_json(), group.to_json()]))
    assert _cli("--target", fixtures_url, "--reproduce", str(mixed)).returncode == 1
    none = tmp_path / "none.json"
    none.write_text(json.dumps([clean.to_json()]))
    assert _cli("--target", fixtures_url, "--reproduce", str(none)).returncode == 0


LIVE_URL = load_config().site.live_url


@pytest.mark.parametrize(
    "target",
    [LIVE_URL, "main", "http://127.0.0.1/", "http://127.0.0.1:8123/", "http://localhost:9/canary/"],
)
def test_probe_accepts_the_live_site_main_and_local_servers(target: str) -> None:
    assert probe.target_allowed(target, LIVE_URL)


@pytest.mark.parametrize(
    "target",
    [
        "https://evil.example/",
        LIVE_URL.replace("https://", "http://", 1),
        "https://mbratkowski.github.io/",
        "https://mbratkowski.github.io/another-site/",
        LIVE_URL + "docs/",
        LIVE_URL.rstrip("/"),
        LIVE_URL + "?leak=1",
        "https://127.0.0.1/",
        "http://127.0.0.1@evil.example/",
        "http://user:pass@127.0.0.1/",
        "http://localhost.evil.example/",
        "http://127.0.0.1:99999/",
        "http://127.0.0.1",
        "file:///etc/passwd",
        "Main",
        "",
    ],
)
def test_probe_rejects_every_other_target(target: str) -> None:
    assert not probe.target_allowed(target, LIVE_URL)


def test_cli_refuses_another_target_before_launching_a_browser(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    def launched(*args: object, **kwargs: object) -> None:
        raise AssertionError("a browser was launched")

    monkeypatch.setattr(probe, "sync_playwright", launched)
    with pytest.raises(SystemExit) as error:
        probe.main(["--target", "https://evil.example/", "--out", str(tmp_path)])
    assert error.value.code == 2
    message = capsys.readouterr().err
    assert "https://evil.example/" in message and LIVE_URL in message
    assert not (tmp_path / "findings.json").exists()
