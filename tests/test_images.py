from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize(
    "path, size",
    [("assets/og.png", (1200, 630)), ("favicon.png", (32, 32)), ("apple-touch-icon.png", (180, 180))],
)
def test_image_sizes(path: str, size: tuple[int, int]) -> None:
    assert Image.open(ROOT / path).size == size
