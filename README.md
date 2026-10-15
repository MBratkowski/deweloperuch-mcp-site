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

## Checks

The checks use [uv](https://docs.astral.sh/uv/) (Python ≥ 3.12):

```bash
uv sync
uv run playwright install chromium
uv run pytest
```

- `tests/test_check_facts.py`: the fact check against the saved server listing, offline.
- `tests/e2e/`: both pages in headless Chromium against a local server, offline (the analytics script is stubbed): copy buttons, the demo, no horizontal overflow at 320 and 375 px, focus rings, requests, cookies, links.
- `uv run python -m tools.check_facts`: the pages against the latest deweloperuch-mcp on PyPI. It starts that release with `uvx` and only lists its tools and prompts, which sends nothing to deweloperuch.pl. `LANDING_NETWORK=1 uv run pytest` runs the same check as a test.
- `python3 tools/build_docs.py`: rebuilds `docs/index.html` and the landing page's agent prompt from `data/server/*.json` (the server's `tests/snapshots/`).
- `uv run python -m tools.render_images`: renders `favicon.png`, `apple-touch-icon.png` and `assets/og.png`.

CI (`.github/workflows/check.yml`, on every push, pull request and weekly) runs the tests, the fact check, the Nu HTML validator, a link check (`lychee.toml`; deweloperuch.pl is never requested) and Lighthouse (`lighthouserc.json`, mobile: accessibility 100, best practices ≥ 95, performance ≥ 90). Lighthouse measures each commit's own files served locally; `pages.yml` measures the live site after every deployment.

## Publishing

`.github/workflows/pages.yml` deploys on every push to `main`. It publishes only the site files (`index.html`, `docs/index.html`, `css/`, `js/`, `assets/`, the icons), not the specs, tools or tests, and then runs Lighthouse against the live pages.

## Licence

MIT for the code (see `LICENSE`). The icon in `assets/icon/` is not covered by the MIT licence: all rights reserved, used with permission of the deweloperuch.pl authors.

This is an unofficial community project, not affiliated with deweloperuch.pl, GUS or NBP.
