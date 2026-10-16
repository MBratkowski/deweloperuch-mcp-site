"""Explicit visual job; the default pytest invocation deselects this directory."""

import importlib.metadata
import json
import os
import platform
from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page

from tests.e2e.conftest import keep_offline
from tests.visual.comparison import compare_image

BASELINES = Path(__file__).parent / "baselines"
ARTIFACTS = Path("test-results/visual")
RENDERER = "playwright-1.64.0-noble-amd64"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--update-visual-baselines", action="store_true", help="Deliberately replace selected visual baselines")
    parser.addoption("--visual-baseline-dir", default=str(BASELINES), help="Alternate scratch baselines, e.g. for a local macOS preview")


@pytest.fixture(scope="session")
def visual_baselines(request: pytest.FixtureRequest, browser: Browser, browser_name: str) -> Path:
    target = Path(request.config.getoption("--visual-baseline-dir")).resolve()
    canonical = target == BASELINES.resolve()
    assert browser_name == "chromium", "Maintained visual baselines use Chromium only"
    renderer = {
        "renderer": os.environ.get("VISUAL_RENDERER", "local-preview"),
        "system": platform.system(),
        "machine": platform.machine(),
        "playwright": importlib.metadata.version("playwright"),
        "chromium": browser.version,
    }
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    (ARTIFACTS / "renderer.json").write_text(json.dumps(renderer, indent=2) + "\n")
    if canonical:
        assert renderer["renderer"] == RENDERER and renderer["system"] == "Linux" and renderer["machine"] == "x86_64", (
            "Canonical baselines require the pinned Linux container; see docs/visual-tests.md. "
            "Use --visual-baseline-dir=/tmp/visual-preview for local scratch images."
        )
        assert renderer["playwright"] == "1.64.0", "Update the renderer and baselines together with Playwright"
    metadata = target / "renderer.json"
    if request.config.getoption("--update-visual-baselines"):
        target.mkdir(parents=True, exist_ok=True)
        metadata.write_text(json.dumps(renderer, indent=2) + "\n")
    return target


@pytest.fixture
def visual_page(new_context):
    context = new_context(
        viewport={"width": 1440, "height": 900}, device_scale_factor=1,
        reduced_motion="reduce", color_scheme="dark", locale="pl-PL", timezone_id="Europe/Warsaw",
    )
    hosts = keep_offline(context)
    page = context.new_page()
    yield page
    context.close()
    assert set(hosts) <= {"localhost", "127.0.0.1", "cloud.umami.is", "gateway.umami.is"}, hosts


@pytest.fixture
def assert_visual(request: pytest.FixtureRequest, visual_baselines: Path) -> Callable[[str, bytes], None]:
    def compare(name: str, screenshot: bytes) -> None:
        artifact = ARTIFACTS / name
        artifact.mkdir(parents=True, exist_ok=True)
        actual = artifact / "actual.png"
        actual.write_bytes(screenshot)
        baseline = visual_baselines / f"{name}.png"
        if request.config.getoption("--update-visual-baselines"):
            baseline.write_bytes(screenshot)
        else:
            compare_image(actual, baseline, artifact)
            metadata = visual_baselines / "renderer.json"
            assert metadata.exists(), f"Missing renderer baseline: {metadata}; inspect {ARTIFACTS}"
            assert json.loads(metadata.read_text()) == json.loads((ARTIFACTS / "renderer.json").read_text()), (
                "Renderer differs from the maintained baseline; see test-results/visual/renderer.json"
            )
    return compare


def settle(page: Page) -> None:
    """Wait for local fonts/images; the saved demo stays final with reduced motion."""
    page.evaluate("""async () => {
      const text = 'Zażółć gęślą jaźń 0123456789';
      for (const face of ['600 16px Poppins', '700 16px Poppins', '400 16px "JetBrains Mono"', '500 16px "JetBrains Mono"']) {
        if (!(await document.fonts.load(face, text)).length) throw new Error(`Missing self-hosted font: ${face}`);
      }
      await document.fonts.ready;
      await Promise.all(Array.from(document.images, image => image.decode()));
    }""")
    page.mouse.move(0, 0)
    page.evaluate("document.activeElement.blur()")
