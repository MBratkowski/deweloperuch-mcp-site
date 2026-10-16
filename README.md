# deweloperuch-mcp-site

The landing page of [deweloperuch-mcp](https://github.com/MBratkowski/deweloperuch-mcp), an MCP server and CLI over the public data of deweloperuch.pl. Live at https://mbratkowski.github.io/deweloperuch-mcp-site/.

- Static landing page and documentation: `index.html`, `docs/index.html`, `css/`, `js/site.js`. No frontend build step or framework; the only third-party requests are Umami analytics.
- Design: the approved Claude Design artboard, exported to `docs/design/noc.dc.html`. Spec and plan: `docs/superpowers/`.
- Fonts: Poppins and JetBrains Mono from Fontsource 5.3.0, self-hosted in `assets/fonts/` under the SIL Open Font License (`OFL-*.txt`).
- Demo data: `data/demo.json`, captured on 2026-10-16 with the package CLI (`deweloperuch offers search wroclaw --area-min 40 --area-max 55 --sort biggest_drop --json`), one request through the package's shared limiter; see [Demo data](#demo-data).

## Run locally

```bash
python3 -m http.server 8000
```

Then open http://127.0.0.1:8000/.

## Styles

- `css/site.css` owns the self-hosted fonts, design tokens, accessibility defaults and shared components: the page container, navigation, terminal, copy button and agent card.
- `css/landing.css` owns landing sections, cards, the demo window, animation states and their responsive rules. `css/docs.css` owns the documentation layout, reference tables and installation illustrations. Each page loads the shared stylesheet followed by its own stylesheet.
- Reuse component classes (`wrap`, `card`, `section-heading`, `term`, `copy`) and explicit variants (`button--primary`, `term--compact`). Use `:root` tokens for shared colours, fonts and container dimensions. The `theme-offers`, `theme-rcn`, `theme-official` and `theme-helpers` classes set `--theme` and `--theme-text`; generators select a theme by the metadata's group key, without storing CSS colours in Python.
- Keep presentation in CSS, including generated HTML. Change `tools/docs_renderer.py`, `tools/templates/docs.html` or `tools/update_demo.py`, then regenerate the affected blocks with the commands below. The original artboard in `docs/design/` is an archived reference.

## Server data

Everything on the pages that depends on the deweloperuch-mcp release (the version, the release's file names, the counts of tools and prompts, the tool groups and lists, and the tool, prompt and CLI reference) is generated from `data/server/`, which pins one release:

```bash
uv run python -m tools.sync_server
python3 tools/build_docs.py
```

- `tools/sync_server.py` writes `data/server/` from the latest release on PyPI (or `tools.sync_server 0.1.2`, or `--pinned` for the one in `version.json`). It only lists the MCP tools and prompts, and runs `tools/export_cli.py` in an isolated environment for `deweloperuch-mcp==<version>` to inspect the CLI's command tree. No tool or CLI command callback is invoked, so nothing reaches deweloperuch.pl. The files are `version.json`, `mcp_tools.json` (including the server's instructions), `mcp_prompts.json` and `cli_commands.json` (version, visible leaf commands, usage and help summaries). CLI commands and JSON keys are sorted, parameter order is preserved, and there are no timestamps or terminal-dependent help layouts.
- `tools/build_docs.py` only coordinates reading, rendering and writing `docs/index.html` and the blocks of `index.html` between `<!-- name:start -->` and `<!-- name:end -->` markers; do not edit generated HTML by hand. `tools/release.py` loads and validates snapshots, `tools/docs_content.py` holds editorial groups and copy, `tools/docs_renderer.py` renders the pages with `tools/templates/docs.html`, and `tools/html_helpers.py` holds helpers shared with the demo renderer.
- The CLI table comes entirely from `cli_commands.json`, including `stop` and `skill` commands; there is no hand-maintained command list. Missing, malformed, duplicate or version-mismatched CLI metadata stops the build before either page is changed. Tool groups remain editorial: a release with a new or removed MCP tool stops the build with its name and the file to update.
- `.github/workflows/sync.yml` runs both daily when the latest release on PyPI is not the pinned one, runs the tests and opens or updates a pull request from `sync/deweloperuch-mcp` with their outcome. It never merges. It needs "Allow GitHub Actions to create and approve pull requests" in the repository's Actions settings.

## Demo data

The demo window on the landing page is rendered from `data/demo.json`, one real `search_offers` result with its capture date:

```bash
uv run python -m tools.capture_demo wroclaw --city-locative "we Wrocławiu" \
  --question "Pokaż mieszkania 40–55 m² we Wrocławiu z największą obniżką ceny." --area-min 40 --area-max 55
python3 -m tools.update_demo
```

- `tools/capture_demo.py` sends one request to deweloperuch.pl through the pinned release's CLI and its shared limiter (`uvx --from deweloperuch-mcp==<pinned> deweloperuch offers search … --sort biggest_drop --json`). Run it by hand only, never in tests or CI, and never in a loop. It writes nothing when the CLI fails, when an offer shown lacks an area, a price or a drop from the first price, or when the top 3 offers come from fewer than two developers (pick another query); on STOP (exit code 3) stop and report. The question and the city in the locative are written by hand and must say what the filters do.
- `tools/update_demo.py` writes the blocks of `index.html` between `<!-- demo-title:start -->`, `<!-- demo-chat:start -->`, `<!-- demo-call:start -->` and their end markers; do not edit those by hand. CI renders the committed `data/demo.json` and fails on any difference.

## Demo animation hooks

The HTML contains the complete, readable conversation. `js/site.js` enhances it only when
motion is allowed and `IntersectionObserver` is available. Keep these behavior hooks when
changing presentation classes or moving the markup into a template:

- `#demo` is the animation root. Its `data-demo` state ships as `final`; JavaScript uses
  `ready`, `playing` and `done`. Only `ready` and `playing` hide unrevealed content.
- Exactly one element inside the root has each `data-demo-role`: `composer`, `chip-state`
  and `chip-dot`. The composer needs a nonempty `data-question` and final placeholder text;
  the status needs its completed text. Those shipped texts are restored on completion.
- `data-step` marks reveal phases: `question`, `chip`, `arg`, `result`, `answer`, `table`,
  `row` and `source`. Every group needs at least one element. JavaScript adds `data-shown`
  as each step appears. The renderer in `tools/update_demo.py` owns these hooks.
- `is-typing` and `is-busy` are temporary presentation states on the composer and dot;
  their CSS animations run only while the root is `playing`. Other CSS classes are visual
  and are never used to locate animation elements.

Initialization validates the contract before hiding content. Playback checks it after each
wait. A missing/changed hook or an initialization/playback exception returns the root to
`final`, restores the available shipped text, removes typing/busy indicators, and disconnects
the observer. No JavaScript and reduced motion keep the shipped final state.

## Checks

The checks use [uv](https://docs.astral.sh/uv/) (Python ≥ 3.12):

```bash
uv sync
uv run playwright install chromium
uv run pytest
```

- `tests/test_check_facts.py`, `tests/test_build_docs.py`, `tests/test_sync_server.py`, `tests/test_export_cli.py`: the fact check, build and sync against the saved MCP/CLI listings, offline; metadata changes, repeated builds, command order, invalid inputs and callback-free CLI discovery are covered.
- `tests/test_capture_demo.py`, `tests/test_update_demo.py`: the capture with the CLI replaced by a fake, and the demo window against `data/demo.json`, offline.
- `tests/e2e/`: both pages in headless Chromium against a local server, offline (the analytics script is stubbed): copy buttons, the demo, no horizontal overflow at 320 and 375 px, focus rings, requests, cookies, links.
- `uv run python -m tools.check_facts`: the pages and saved MCP/CLI metadata against the latest deweloperuch-mcp on PyPI. It lists the MCP tools and prompts and inspects the CLI without invoking callbacks, which sends nothing to deweloperuch.pl. A release other than the pinned one fails it first, with the sync commands. `LANDING_NETWORK=1 uv run pytest` runs the same check as a test and also syncs the pinned release into a temporary folder to compare it with `data/server/`.
- `uv run python -m tools.render_images`: renders `favicon.png`, `apple-touch-icon.png` and `assets/og.png`.

CI (`.github/workflows/check.yml`, on every push, pull request and weekly) calls the reusable `validate.yml` workflow for the tests, a guard that syncs the pinned release, builds the pages, renders the demo and fails on any difference from the commit, the Nu HTML validator, a link check (`lychee.toml`; deweloperuch.pl is never requested) and Lighthouse (`lighthouserc.json`, mobile: accessibility 100, best practices ≥ 95, performance ≥ 90). Check also runs the fact check against the latest release. Local Lighthouse measures each commit's own files; `pages.yml` measures the live site after every deployment.

## Publishing

`.github/workflows/pages.yml` runs on every push to `main` and can be started manually. Both paths first call the same `validate.yml` workflow as Check, from the same commit. Every validation checkout and the deployment checkout use the event's immutable `github.sha`; deployment depends on successful validation in that run, so a failed, skipped or cancelled required check blocks publishing and success for another commit cannot authorize it. The latest-release fact check stays in Check and is not a deployment gate. Pages publishes only the site files (`index.html`, `docs/index.html`, `css/`, `js/`, `assets/`, the icons), then runs a separate Lighthouse job against the live pages.

## Licence

MIT for the code (see `LICENSE`). The icon in `assets/icon/` is not covered by the MIT licence: all rights reserved, used with permission of the deweloperuch.pl authors.

This is an unofficial community project, not affiliated with deweloperuch.pl, GUS or NBP.
