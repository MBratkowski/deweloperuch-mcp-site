"""The release-card command exports a complete, reusable image from local data."""

import json
import subprocess
import sys
from pathlib import Path

from PIL import Image
import pytest
from playwright.sync_api import Page, expect

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def card_data() -> dict:
    return {
        "version": "0.1.3",
        "date": "2026-10-16",
        "headline": "Teraz także na Windowsie.",
        "changes": [
            {"type": "added", "title": "Windows 10 i 11", "description": "CLI, MCP i instalacja w PowerShellu."},
            {"type": "fixed", "title": "Czytelniejsze wyceny", "description": "Ostrzeżenie przy ułamkowym metrażu."},
            {"type": "fixed", "title": "Pewna konfiguracja CLI", "description": "Skill rozpoznaje właściwy pakiet."},
        ],
    }


def test_command_exports_a_release_png_from_unicode_path(tmp_path: Path, card_data: dict) -> None:
    source = tmp_path / "wydanie-żółć.json"
    source.write_text(json.dumps(card_data, ensure_ascii=False), encoding="utf-8")
    output = tmp_path / "grafiki" / "release.png"
    done = subprocess.run(
        [sys.executable, "-m", "tools.render_release", str(source), "--expect-version", "0.1.3", "--output", str(output)],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )
    assert done.returncode == 0, done.stderr
    with Image.open(output) as image:
        assert image.format == "PNG"
        assert image.size == (1200, 630)
        assert len(image.convert("RGB").getcolors(1200 * 630)) > 100


def test_release_version_mismatch_does_not_replace_an_export(tmp_path: Path, card_data: dict) -> None:
    source = tmp_path / "release.json"
    source.write_text(json.dumps(card_data), encoding="utf-8")
    output = tmp_path / "keep.png"
    output.write_bytes(b"previous export")
    done = subprocess.run(
        [sys.executable, "-m", "tools.render_release", str(source), "--expect-version", "0.1.4", "--output", str(output)],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )
    assert done.returncode == 1
    assert "expected 0.1.4, got 0.1.3" in done.stderr
    assert output.read_bytes() == b"previous export"


@pytest.mark.parametrize("field,value", [
    ("version", "../outside"),
    ("date", "2026-02-30"),
    ("headline", "  "),
    ("changes", []),
    ("changes", [{"type": "custom", "title": "A", "description": "B"}]),
    ("changes", [{"type": "added", "title": "", "description": "B"}]),
    ("changes", [{"type": "fixed", "title": "A"}]),
])
def test_invalid_release_data_has_a_field_error(card_data: dict, field: str, value: object) -> None:
    from tools.render_release import ReleaseCardError, build_document

    card_data[field] = value
    with pytest.raises(ReleaseCardError, match=field):
        build_document(card_data)


@pytest.mark.parametrize("headline", [
    "Bardzo ważne nowe funkcje dla wszystkich użytkowników. " * 4,
    "\n".join(["Nowe wydanie."] * 5),
    "\n".join(["Nowe wydanie."] * 6),
])
def test_export_rejects_clipped_text_without_overwriting_an_image(tmp_path: Path, card_data: dict, headline: str) -> None:
    card_data["headline"] = headline
    source = tmp_path / "release.json"
    source.write_text(json.dumps(card_data), encoding="utf-8")
    output = tmp_path / "keep.png"
    output.write_bytes(b"previous export")
    done = subprocess.run(
        [sys.executable, "-m", "tools.render_release", str(source), "--output", str(output)],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )
    assert done.returncode == 1
    assert "does not fit" in done.stderr
    assert output.read_bytes() == b"previous export"


def test_future_release_and_markup_are_rendered_as_plain_text(page: Page, site_url: str, card_data: dict) -> None:
    from tools.render_release import build_document, prepare_capture

    card_data["version"] = "0.2.0rc1"
    card_data["headline"] = "Własny <changelog> & więcej."
    card_data["changes"] = [{"type": "changed", "title": "<b>Nowość</b>", "description": "Łódź, żółć i 42,5 m²."}]
    card_url = site_url + "__release_card__.html"
    external = []

    def route_request(route):
        if route.request.url == card_url:
            route.fulfill(content_type="text/html; charset=utf-8", body=build_document(card_data))
        elif route.request.url.startswith(site_url):
            route.continue_()
        else:
            external.append(route.request.url)
            route.abort()

    page.route("**/*", route_request)
    page.set_viewport_size({"width": 1200, "height": 630})
    page.goto(card_url, wait_until="networkidle")
    prepare_capture(page)
    expect(page.locator("[data-release-version]")).to_have_text("v0.2.0rc1")
    expect(page.get_by_role("heading", level=1)).to_have_text("Własny <changelog> & więcej.")
    expect(page.get_by_role("heading", level=2)).to_have_text("<b>Nowość</b>")
    expect(page.locator(".change p")).to_have_text("Łódź, żółć i 42,5 m².")
    expect(page.locator(".change-label")).to_have_text("Ulepszenie")
    assert page.locator("b, changelog, script").count() == 0
    assert external == []
