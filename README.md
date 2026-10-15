# deweloperuch-mcp-site

The landing page of [deweloperuch-mcp](https://github.com/MBratkowski/deweloperuch-mcp), an MCP server and CLI over the public data of deweloperuch.pl. Live at https://mbratkowski.github.io/deweloperuch-mcp-site/.

- One static page: `index.html`, `css/site.css`, `js/site.js`. No build step, no framework, no third-party requests.
- Design: the approved Claude Design artboard, exported to `docs/design/noc.dc.html`. Spec and plan: `docs/superpowers/`.
- Fonts: Poppins and JetBrains Mono from Fontsource 5.3.0, self-hosted in `assets/fonts/` under the SIL Open Font License (`OFL-*.txt`).
- Demo data: `data/demo.json`, captured on 2026-10-15 with the package CLI (`deweloperuch offers search wroclaw --area-min 40 --area-max 60 --sort biggest_drop --json`), one request through the package's shared limiter.

## Run locally

```bash
python3 -m http.server 8000
```

Then open http://127.0.0.1:8000/.

## Server data

Everything on the pages that depends on the deweloperuch-mcp release (the version, the release's file names, the counts of tools and prompts, the tool groups and lists, the tool and prompt reference) is generated from `data/server/`, which pins one release:

```bash
uv run python -m tools.sync_server
python3 tools/build_docs.py
```

- `tools/sync_server.py` writes `data/server/` from the latest release on PyPI (or `tools.sync_server 0.1.2`, or `--pinned` for the one in `version.json`). It starts the release with `uvx deweloperuch-mcp==<version>` and only lists its tools and prompts, which sends nothing to deweloperuch.pl. The files (`version.json`, `mcp_tools.json` with the server's instructions, `mcp_prompts.json`) depend only on the release: sorted keys, no timestamps.
- `tools/build_docs.py` writes `docs/index.html` whole and the blocks of `index.html` between `<!-- name:start -->` and `<!-- name:end -->` markers; do not edit those by hand. The tool groups (with their lines in the landing's Dane section) and the CLI table are written by hand in the script: a release with a new or removed tool stops the build with the tool's name.
- `.github/workflows/sync.yml` runs both daily when the latest release on PyPI is not the pinned one, runs the tests and opens or updates a pull request from `sync/deweloperuch-mcp` with their outcome. It never merges. It needs "Allow GitHub Actions to create and approve pull requests" in the repository's Actions settings.

## Checks

The checks use [uv](https://docs.astral.sh/uv/) (Python ≥ 3.12):

```bash
uv sync
uv run playwright install chromium
uv run pytest
```

- `tests/test_check_facts.py`, `tests/test_build_docs.py`, `tests/test_sync_server.py`: the fact check, the build and the sync against the saved server listing, offline.
- `tests/e2e/`: both pages in headless Chromium against a local server, offline (the analytics script is stubbed): copy buttons, the demo, no horizontal overflow at 320 and 375 px, focus rings, requests, cookies, links.
- `uv run python -m tools.check_facts`: the pages against the latest deweloperuch-mcp on PyPI. It starts that release with `uvx` and only lists its tools and prompts, which sends nothing to deweloperuch.pl. A release other than the pinned one fails it first, with the sync commands. `LANDING_NETWORK=1 uv run pytest` runs the same check as a test and also syncs the pinned release into a temporary folder to compare it with `data/server/`.
- `uv run python -m tools.render_images`: renders `favicon.png`, `apple-touch-icon.png` and `assets/og.png`.

CI (`.github/workflows/check.yml`, on every push, pull request and weekly) runs the tests, a guard that syncs the pinned release, builds the pages and fails on any difference from the commit, the fact check against the latest release, the Nu HTML validator, a link check (`lychee.toml`; deweloperuch.pl is never requested) and Lighthouse (`lighthouserc.json`, mobile: accessibility 100, best practices ≥ 95, performance ≥ 90). Lighthouse measures each commit's own files served locally; `pages.yml` measures the live site after every deployment.

## Publishing

`.github/workflows/pages.yml` deploys on every push to `main`. It publishes only the site files (`index.html`, `docs/index.html`, `css/`, `js/`, `assets/`, the icons), not the specs, tools or tests, and then runs Lighthouse against the live pages.

## Licence

MIT for the code (see `LICENSE`). The icon in `assets/icon/` is not covered by the MIT licence: all rights reserved, used with permission of the deweloperuch.pl authors.

This is an unofficial community project, not affiliated with deweloperuch.pl, GUS or NBP.
