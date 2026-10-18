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


def test_cli_exits_2_on_a_failing_site(unavailable_url: str, tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "tools.factory.probe", "--target", unavailable_url, "--out", str(tmp_path), "--only", "overflow"],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2, result.stderr
    assert not (tmp_path / "findings.json").exists()


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
