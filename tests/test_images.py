import dataclasses
import json
from pathlib import Path
import shutil

import pytest
from PIL import Image

from tools import render_images

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize(
    "path, size",
    [("assets/og.png", (1200, 630)), ("favicon.png", (32, 32)), ("apple-touch-icon.png", (180, 180))],
)
def test_image_sizes(path: str, size: tuple[int, int]) -> None:
    with Image.open(ROOT / path) as image:
        assert image.size == size


@pytest.fixture
def image_site(tmp_path: Path) -> Path:
    for name in ("data", "tools/templates", "assets", "css", "js"):
        shutil.copytree(ROOT / name, tmp_path / name)
    for name in ("index.html", "favicon.png", "apple-touch-icon.png", "uv.lock", "tools/render_images.py"):
        shutil.copyfile(ROOT / name, tmp_path / name)
    assert render_images.check_images(tmp_path) == []
    return tmp_path


def test_committed_images_match_current_sources_and_recorded_outputs() -> None:
    assert render_images.check_images() == []


@pytest.mark.parametrize("name", [
    "css/landing.css", "css/site.css", "js/site.js", "uv.lock", "tools/render_images.py",
    "assets/fonts/poppins-latin-700-normal.woff2", "assets/icon/icon-tile-512.png",
])
def test_changed_rendering_input_requires_regeneration(image_site: Path, name: str) -> None:
    path = image_site / name
    path.write_bytes(path.read_bytes() + b"\n")
    assert any(name in error for error in render_images.check_images(image_site))


def test_changed_shared_head_is_detected_before_rebuilding_the_page(image_site: Path) -> None:
    path = image_site / "tools/templates/shared/head.html"
    path.write_text(path.read_text().replace('<meta charset=', '<!-- source changed -->\n<meta charset='))
    assert any("index.html differs from its current source inputs" in error for error in render_images.check_images(image_site))


def test_changed_page_configuration_is_detected_before_rebuilding(image_site: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import page_config

    monkeypatch.setattr(page_config, "SITE", dataclasses.replace(page_config.SITE, theme_color="#123456"))
    assert any("index.html differs from its current source inputs" in error for error in render_images.check_images(image_site))


def test_new_resource_is_included_in_freshness_check(image_site: Path) -> None:
    (image_site / "css/extra.css").write_text("body {color: red}")
    assert any("css/extra.css" in error for error in render_images.check_images(image_site))


def test_same_size_wrong_image_is_detected(image_site: Path) -> None:
    Image.new("RGB", (1200, 630), "black").save(image_site / "assets/og.png")
    assert any("assets/og.png" in error for error in render_images.check_images(image_site))


def test_renderer_platform_and_versions_are_provenance_not_a_pixel_requirement(image_site: Path) -> None:
    path = image_site / render_images.MANIFEST
    manifest = json.loads(path.read_text())
    manifest["renderer"] = {key: "another-environment" for key in manifest["renderer"]}
    path.write_text(json.dumps(manifest))
    assert render_images.check_images(image_site) == []


@pytest.mark.parametrize("contents", [None, "{"])
def test_missing_or_invalid_manifest_has_regeneration_guidance(image_site: Path, contents: str | None) -> None:
    path = image_site / render_images.MANIFEST
    if contents is None:
        path.unlink()
    else:
        path.write_text(contents)
    errors = render_images.check_images(image_site)
    assert errors and "render_images" in " ".join(errors)


def test_check_command_fails_without_rewriting_stale_assets(image_site: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = image_site / "assets/og.png"
    path.write_bytes(b"stale")
    assert render_images.main(image_site, check=True) == 1
    assert path.read_bytes() == b"stale"
    assert "assets/og.png" in capsys.readouterr().err
