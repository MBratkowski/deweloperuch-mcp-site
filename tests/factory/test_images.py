"""Evidence images: annotation, crop and contact sheet."""

import io

from PIL import Image

from tools.factory import images


def _png(width: int, height: int, color=(255, 255, 255)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _open(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data)).convert("RGB")


def test_annotate_draws_red_outline() -> None:
    out = _open(images.annotate(_png(200, 200), (50, 60, 40, 30)))
    assert out.size == (200, 200)
    assert out.getpixel((50, 75)) == (255, 0, 0)  # left edge
    assert out.getpixel((89, 75)) == (255, 0, 0)  # right edge
    assert out.getpixel((70, 60)) == (255, 0, 0)  # top edge
    assert out.getpixel((70, 89)) == (255, 0, 0)  # bottom edge
    assert out.getpixel((70, 75)) == (255, 255, 255)  # inside stays untouched
    assert out.getpixel((10, 10)) == (255, 255, 255)


def test_crop_adds_margin_and_clamps() -> None:
    shot = _png(300, 300)
    assert _open(images.crop(shot, (100, 100, 50, 40), margin=24)).size == (50 + 48 + 1, 40 + 48 + 1)
    corner = _open(images.crop(shot, (0, 0, 10, 10), margin=24))
    assert corner.size == (10 + 24 + 1, 10 + 24 + 1)


def test_contact_sheet_size() -> None:
    shots = [(f"s{i}", _png(100, 50)) for i in range(4)]
    sheet = _open(images.contact_sheet(shots, columns=2))
    assert sheet.width == 200
    assert 100 < sheet.height <= 100 + 2 * images.LABEL_HEIGHT


def test_contact_sheet_scales_wide_tiles_down() -> None:
    sheet = _open(images.contact_sheet([("a", _png(1920, 960)), ("b", _png(1920, 960))], columns=2))
    assert sheet.width == 2 * images.TILE_WIDTH
