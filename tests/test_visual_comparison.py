"""Prove the comparator catches small regressions and produces useful artifacts."""

from pathlib import Path

import pytest
from PIL import Image

from tests.visual.comparison import compare_image


def image(path: Path, color: tuple[int, int, int], size: tuple[int, int] = (100, 100)) -> None:
    Image.new("RGB", size, color).save(path)


def test_identical_and_rounding_only_images_pass(tmp_path: Path) -> None:
    image(tmp_path / "expected.png", (20, 40, 60))
    image(tmp_path / "actual.png", (28, 32, 60))
    compare_image(tmp_path / "actual.png", tmp_path / "expected.png", tmp_path / "artifacts")


def test_one_changed_pixel_fails_even_in_a_large_image(tmp_path: Path) -> None:
    expected = tmp_path / "expected.png"
    actual = tmp_path / "actual.png"
    image(expected, (20, 40, 60))
    altered = Image.open(expected)
    altered.putpixel((25, 25), (29, 40, 60))
    altered.save(actual)
    artifacts = tmp_path / "artifacts"
    with pytest.raises(AssertionError, match="1 pixels"):
        compare_image(actual, expected, artifacts)
    assert Image.open(artifacts / "diff.png").getpixel((25, 25)) == (255, 0, 255)
    assert (artifacts / "expected.png").exists()


def test_dimension_change_fails_even_when_pixels_match(tmp_path: Path) -> None:
    image(tmp_path / "expected.png", (0, 0, 0))
    image(tmp_path / "actual.png", (0, 0, 0), (100, 101))
    with pytest.raises(AssertionError, match="expected .*actual"):
        compare_image(tmp_path / "actual.png", tmp_path / "expected.png", tmp_path / "artifacts")


def test_missing_baseline_fails(tmp_path: Path) -> None:
    image(tmp_path / "actual.png", (0, 0, 0))
    with pytest.raises(AssertionError, match="Missing visual baseline"):
        compare_image(tmp_path / "actual.png", tmp_path / "missing.png", tmp_path / "artifacts")
