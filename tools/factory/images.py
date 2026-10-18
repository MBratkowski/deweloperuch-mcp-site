"""Image helpers for probe evidence: annotate a box, crop around it, tile screenshots, diff two."""

from __future__ import annotations

import io
from collections.abc import Sequence

from PIL import Image, ImageChops, ImageDraw

Box = tuple[float, float, float, float]
RED = (255, 0, 0)
OUTLINE = 2
TILE_WIDTH = 240
LABEL_HEIGHT = 16
SHEET_BACKGROUND = (255, 255, 255)
MAGENTA = (255, 0, 255)
# Channel differences up to this are rounding, as in tests/visual/comparison.py.
DIFF_TOLERANCE = 8


def _png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _load(shot: bytes) -> Image.Image:
    return Image.open(io.BytesIO(shot)).convert("RGB")


def has_area(box: Box | None) -> bool:
    return box is not None and box[2] > 0 and box[3] > 0


def annotate(shot: bytes, box: Box) -> bytes:
    """Draw a 2 px red outline just around the box (edge pixels included)."""
    image = _load(shot)
    x, y, w, h = (round(v) for v in box)
    ImageDraw.Draw(image).rectangle([x - 1, y - 1, x + w, y + h], outline=RED, width=OUTLINE)
    return _png(image)


def crop(shot: bytes, box: Box, margin: int = 24) -> bytes:
    """The region around the box, `margin` px on every side, clamped to the screenshot."""
    image = _load(shot)
    x, y, w, h = box
    left = max(0, int(x) - margin)
    top = max(0, int(y) - margin)
    right = min(image.width, int(x + w) + margin + 1)
    bottom = min(image.height, int(y + h) + margin + 1)
    return _png(image.crop((left, top, max(right, left + 1), max(bottom, top + 1))))


def contact_sheet(shots: Sequence[tuple[str, bytes]], columns: int) -> bytes:
    """Tile labelled screenshots; tiles wider than TILE_WIDTH are scaled down to it."""
    tiles: list[tuple[str, Image.Image]] = []
    for label, data in shots:
        image = _load(data)
        if image.width > TILE_WIDTH:
            image = image.resize((TILE_WIDTH, max(1, round(image.height * TILE_WIDTH / image.width))))
        tiles.append((label, image))
    if not tiles:
        raise ValueError("a contact sheet needs at least one screenshot")
    columns = max(1, min(columns, len(tiles)))
    cell_width = max(image.width for _, image in tiles)
    rows = [tiles[i : i + columns] for i in range(0, len(tiles), columns)]
    row_heights = [LABEL_HEIGHT + max(image.height for _, image in row) for row in rows]
    sheet = Image.new("RGB", (cell_width * columns, sum(row_heights)), SHEET_BACKGROUND)
    draw = ImageDraw.Draw(sheet)
    top = 0
    for row, height in zip(rows, row_heights):
        for column, (label, image) in enumerate(row):
            draw.text((column * cell_width + 2, top + 2), label, fill=(0, 0, 0))
            sheet.paste(image, (column * cell_width, top + LABEL_HEIGHT))
        top += height
    return _png(sheet)


def diff(before: bytes, after: bytes) -> bytes:
    """Changed pixels in magenta over a dimmed `after`, both padded to the larger size: the same
    picture as the visual tests' diff.png."""
    old, new = _load(before), _load(after)
    size = (max(old.width, new.width), max(old.height, new.height))
    old_canvas, new_canvas = Image.new("RGB", size), Image.new("RGB", size)
    old_canvas.paste(old)
    new_canvas.paste(new)
    red, green, blue = ImageChops.difference(old_canvas, new_canvas).split()
    largest = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    mask = largest.point(lambda value: 255 if value > DIFF_TOLERANCE else 0)
    overlay = new_canvas.point(lambda value: value // 3)
    overlay.paste(MAGENTA, mask=mask)
    return _png(overlay)
