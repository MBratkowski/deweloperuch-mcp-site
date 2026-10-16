# Visual and browser regression tests

The default `uv run pytest` runs the offline unit and Chromium behavior suite. It deselects the `visual` marker because screenshots depend on the operating system's font rasterizer. CI separately runs all maintained visual tests; missing images, changed renderer metadata and changed pixels fail that required job.

## Coverage and determinism

`tests/visual/test_visual.py` compares the entire landing and docs pages at 1440 × 900 (desktop), 768 × 1024 (tablet) and 375 × 812 (mobile), at device scale 1. Separate desktop crops cover install-button hover and keyboard focus, copy-button hover/focus, docs navigation hover and docs copy focus. Mobile header crops also cover the expanded menu on both pages at 375 px. The numeric install-button hover contrast regression remains in `tests/e2e/test_landing.py`.

The pages run on the fixture's local HTTP server. All browser requests are intercepted: local assets pass, Umami receives an empty script, and every other host is blocked and fails the visual fixture. No test captures live apartment data. The committed `data/demo.json` and its generated HTML remain the source of the demo; reduced motion keeps its complete final state. Tests wait for self-hosted Poppins and JetBrains Mono, including Polish glyphs, and decoded images. Reduced motion, disabled screenshot animations, hidden carets, a fixed locale/time zone and a cleared pointer/focus state remove timing differences. Nothing is masked out of the page comparisons.

The canonical renderer is **Playwright 1.64.0, Linux x86_64, Chromium**, inside this immutable image:

```text
mcr.microsoft.com/playwright:v1.64.0-noble@sha256:06a9939e57531807f8d5fd76ce44b53165ffb7d7501d87ab10e285c20b1e971f
```

The Node Playwright image supplies browsers and system fonts; `uv sync --locked --python 3.12` supplies the Python test environment. `renderer.json` records the actual browser/package/platform versions. Keep the image, the lockfile and the baselines together when updating Playwright. CI uses the same job in the reusable `validate.yml` workflow called by Check and Pages.

The Pillow comparator accepts at most 8 levels of difference per RGB channel for rounding. It allows **zero pixels** beyond that tolerance and requires identical dimensions. There is no whole-page percentage budget that could hide a small control regression.

## Inspect a failure

For a failure in **Tests (pytest, Playwright)**, download `main-chromium-results` from the Check or Pages run. Each failed browser test retains Playwright trace ZIPs and `test-failed-*.png` screenshots under its test directory. Open a trace with `uv run playwright show-trace <trace-file.zip>` (a test with multiple contexts has numbered trace files). CI retains this artifact for 14 days and uploads it only on failure; passing tests discard their diagnostics. A failure before the browser creates diagnostics may have no artifact, so check the job log as well.

Open the failing **Visual baselines (pinned Linux Chromium)** job and download its `visual-results` artifact. For each comparison, `visual/<name>/` contains:

- `actual.png`: this commit's rendered page or control state.
- `expected.png`: the maintained baseline, when present.
- `diff.png`: changed pixels in magenta over a dimmed actual image, when different.

`visual/renderer.json` identifies the actual renderer. Retained Playwright `trace.zip` files under the test directories can be opened with `uv run playwright show-trace <path>/trace.zip`. The artifact uploads even when the assertion fails; missing baselines still produce actual images. Artifacts expire after 14 days.

Inspect expected, actual and diff at native resolution, including the bottom of full-page images. Review typography, line wrapping, responsive gutters, tables and visible keyboard rings. Fix an accidental appearance change in the source. Never update baselines solely to make CI pass.

## Deliberately update baselines

With Docker available, render using the exact Linux image. Run from the repository root:

```bash
docker run --rm --ipc=host --platform linux/amd64 \
  -v "$PWD:/work" -w /work \
  -e VISUAL_RENDERER=playwright-1.64.0-noble-amd64 \
  -e UV_PROJECT_ENVIRONMENT=/tmp/visual-venv \
  mcr.microsoft.com/playwright:v1.64.0-noble@sha256:06a9939e57531807f8d5fd76ce44b53165ffb7d7501d87ab10e285c20b1e971f \
  bash -lc '
    set -eu
    curl -LsSf https://astral.sh/uv/0.9.26/install.sh | env UV_INSTALL_DIR=/tmp/uv sh
    /tmp/uv/uv sync --locked --python 3.12
    /tmp/uv/uv run pytest tests/visual -m visual --browser chromium --update-visual-baselines
  '
```

If uv is already installed in your container, use `uv` directly instead of installing it. The temporary environment avoids overwriting a host macOS `.venv`. Review every changed PNG and `renderer.json`, then commit only intentional baselines with the source change. Re-run the same command **without** `--update-visual-baselines`; it must pass before publishing the change.

Without Docker, a draft feature PR can use the required visual job as the canonical renderer. Download the artifact for that exact head commit. After inspecting its actuals, copy each `visual/<name>/actual.png` to `tests/visual/baselines/<name>.png`, and `visual/renderer.json` to the baselines directory. Commit those files and verify the visual job passes on the new head. This is an explicit human/agent review step; CI never accepts or rewrites its own baselines. Initial bootstrap failures are temporary and must be resolved before the PR is ready.

For a macOS preview, use a scratch directory so canonical Linux images cannot be overwritten:

```bash
uv run pytest tests/visual -m visual --browser chromium \
  --visual-baseline-dir=/tmp/visual-preview --update-visual-baselines
uv run pytest tests/visual -m visual --browser chromium \
  --visual-baseline-dir=/tmp/visual-preview
```

These previews help inspect local appearance; the required CI comparison still uses the canonical Linux baselines.

## Firefox and WebKit smoke tests

```bash
uv run playwright install firefox webkit
uv run pytest tests/e2e/test_browser_smoke.py --browser firefox --browser webkit \
  --tracing retain-on-failure --screenshot only-on-failure
```

The smoke suite exercises landing → docs → installation anchors → landing navigation, responsive containment at 1440, 768, 375 and 320 px, every copy button, clipboard rejection and a missing clipboard API. It uses viewport resizing because Firefox does not support Playwright's `is_mobile` emulation. It stubs the clipboard boundary rather than requesting Chromium-only clipboard permissions, and verifies synchronous click-time writes, the success label, and manual selection fallback. The ordinary Chromium suite still verifies real clipboard contents. CI uploads retained traces and failure screenshots as `browser-smoke-results`.
