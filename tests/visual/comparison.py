"""Small, strict RGB image comparisons with inspectable failure artifacts."""

from pathlib import Path

from PIL import Image, ImageChops

# Ignore tiny channel rounding only. No percentage budget: even a small control
# or one shifted line must fail within a full-page screenshot.
CHANNEL_TOLERANCE = 8


def compare_image(actual_path: Path, expected_path: Path, artifact_dir: Path) -> None:
    actual = Image.open(actual_path).convert("RGB")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    if not expected_path.exists():
        raise AssertionError(f"Missing visual baseline: {expected_path}; inspect {actual_path}")
    expected = Image.open(expected_path).convert("RGB")
    expected.save(artifact_dir / "expected.png")
    size = (max(actual.width, expected.width), max(actual.height, expected.height))
    actual_canvas = Image.new("RGB", size)
    expected_canvas = Image.new("RGB", size)
    actual_canvas.paste(actual)
    expected_canvas.paste(expected)
    difference = ImageChops.difference(actual_canvas, expected_canvas)
    channels = difference.split()
    maximum = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2])
    mask = maximum.point(lambda value: 255 if value > CHANNEL_TOLERANCE else 0)
    changed = mask.histogram()[255]
    if changed or actual.size != expected.size:
        # Highlight differences in magenta over a dimmed actual image.
        overlay = actual_canvas.point(lambda value: value // 3)
        overlay.paste((255, 0, 255), mask=mask)
        overlay.save(artifact_dir / "diff.png")
        raise AssertionError(
            f"Visual mismatch: {changed:,} pixels exceed RGB tolerance {CHANNEL_TOLERANCE}; "
            f"expected {expected.size}, actual {actual.size}. Inspect {artifact_dir}"
        )
