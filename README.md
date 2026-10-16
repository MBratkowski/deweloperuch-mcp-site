# deweloperuch-mcp-site

The landing page of [deweloperuch-mcp](https://github.com/MBratkowski/deweloperuch-mcp), an MCP server and CLI over the public data of deweloperuch.pl. Live at https://mbratkowski.github.io/deweloperuch-mcp-site/.

- Static landing page and documentation: `index.html`, `docs/index.html`, `css/`, `js/site.js`. No frontend framework; both HTML pages are generated offline with Python; the only third-party requests are Umami analytics.
- Design: the approved Claude Design artboard, exported to `docs/design/noc.dc.html`. Spec and plan: `docs/superpowers/`.
- Fonts: Poppins and JetBrains Mono from Fontsource 5.3.0, self-hosted in `assets/fonts/` under the SIL Open Font License (`OFL-*.txt`).
- Demo data: `data/demo.json`, captured on 2026-10-16 with the package CLI (`deweloperuch offers search wroclaw --area-min 40 --area-max 55 --sort biggest_drop --json`), one request through the package's shared limiter; see [Demo data](#demo-data).

## Run locally

```bash
python3 -m http.server 8000
```

Then open http://127.0.0.1:8000/.

## Build and edit pages

One offline command renders the complete landing page, documentation and saved demo:

```bash
python3 -m tools.build_docs
```

`python3 tools/build_docs.py` is also supported. The build reads only the shared Python configuration, source templates,
`data/server/` and `data/demo.json`; it never starts the package, captures a demo or makes
network requests. It renders and validates both pages before writing either one. Repeated
builds are deterministic, and existing output files are not inputs. Commit regenerated
`index.html` and `docs/index.html` with source changes; never edit those outputs by hand.

| What to change | Source |
| --- | --- |
| Landing copy, static markup, section order and page-specific navigation/footer | `tools/templates/landing.html` |
| Documentation copy, static markup and section order | `tools/templates/docs.html` |
| Installation commands, MCP client config, agent prompt, project URLs, analytics and page metadata | `tools/page_config.py` |
| Common head, navigation brand/menu/actions and footer notice | `tools/templates/shared/`, rendered by `tools/page_fragments.py` |
| Release-dependent tool groups and editorial copy | `tools/docs_content.py` |
| Release-dependent HTML fragments and installation illustrations | `tools/docs_renderer.py` |
| Saved demo markup, formatting and animation hooks | `tools/update_demo.py` |
| Appearance and responsive rules | `css/site.css`, `css/landing.css`, `css/docs.css` |

The shared header switches to a compact menu at widths up to 760 px. `js/site.js` progressively enhances its native button, keeps `aria-expanded` in sync, and closes the menu on Escape, link activation or a breakpoint change. Without JavaScript every navigation link remains visible. Keep the navigation breakpoint in `css/site.css` and `initNavigation` in sync.

The templates use Python's standard `string.Template` syntax: `${slot_name}` inserts a
rendered fragment and `$$` emits a literal dollar sign (for example a terminal prompt).
Every landing slot must occur exactly once; missing, unknown, malformed or duplicated
slots stop the build. The landing slots come from `landing_blocks()` and `render_landing()` in
`tools/docs_renderer.py`, `demo_blocks()` in `tools/update_demo.py`, and shared
`page_fragments()` in `tools/page_fragments.py`. Documentation and shared-fragment slots
may repeat, such as the repository URL, but must match their renderer values. Shared
fragments are loaded from the same source root as both page templates; a missing or invalid
fragment stops the build before either output is written.

The landing-template work ([#11](https://github.com/MBratkowski/deweloperuch-mcp-site/issues/11))
established the page source templates and one canonical build. Shared configuration
([#12](https://github.com/MBratkowski/deweloperuch-mcp-site/issues/12)) extends that build; it
does not add another page source or build command. `COMMANDS` and `SITE` in
`tools/page_config.py` are the single source for installation commands and common site
settings; `PAGES` holds the metadata, stylesheet and font-preload variants. Both pages
derive command text and clipboard payloads from each command value, including agent
prompts and MCP client JSON. Edit command values there, never in page templates. Keep
page-specific section links and footer layouts in their page templates; reuse the shared
fragments for common content. Asset paths are relative to each page, while canonical and
social-image URLs use the configured production base URL. Existing analytics event names
remain attached to their page-specific controls.

Add, remove or move static sections in the appropriate source template, update its
navigation links and anchor IDs, and rebuild. For a generated landing section, add or remove
its slot and renderer entry together; changing just one fails validation. Preserve the
[demo animation hooks](#demo-animation-hooks) and accessibility attributes when moving demo
markup. Run the offline tests after regenerating both pages.

## Styles

- `css/site.css` owns the self-hosted fonts, design tokens, accessibility defaults and shared components: the page container, navigation, terminal, copy button and agent card.
- `css/landing.css` owns landing sections, cards, the demo window, animation states and their responsive rules. `css/docs.css` owns the documentation layout, reference tables and installation illustrations. Each page loads the shared stylesheet followed by its own stylesheet.
- Reuse component classes (`wrap`, `card`, `section-heading`, `term`, `copy`) and explicit variants (`button--primary`, `term--compact`). Use `:root` tokens for shared colours, fonts and container dimensions. The `theme-offers`, `theme-rcn`, `theme-official` and `theme-helpers` classes set `--theme` and `--theme-text`; generators select a theme by the metadata's group key, without storing CSS colours in Python.
- `--content-width` caps the complete `.wrap` box, including both `--page-gutter` insets. Headers, sections and footer containers share that box on both pages; variants override only their own vertical spacing. Component interiors (cards, demo chat and installation illustrations), text measure caps and the demo window's narrower cap are independent dimensions, even when their values match a page token. Keep those local values literal.
- Paragraph context defaults use `:where(...)` to keep their specificity at zero. Component classes such as `step-num`, `mock-title` and `eyebrow` take precedence regardless of source order; keep component typography on those classes.
- Keep presentation in CSS, including generated HTML. Edit the source files listed above, then run the offline build. The original artboard in `docs/design/` is an archived reference.

## Server data

Everything on the pages that depends on the deweloperuch-mcp release (the version, the release's file names, the counts of tools and prompts, the tool groups and lists, and the tool, prompt and CLI reference) is generated from `data/server/`, which pins one release:

```bash
uv run python -m tools.sync_server
python3 tools/build_docs.py
```

- `tools/sync_server.py` writes `data/server/` from the latest release on public PyPI (or `tools.sync_server 0.1.3`, or `--pinned` for the one in `version.json`). Both metadata readers select that index explicitly, ignore local uv configuration and refresh the package listing so a cached list cannot hide a just-published release. It only lists the MCP tools and prompts, and runs `tools/export_cli.py` in an isolated environment for `deweloperuch-mcp==<version>` to inspect the CLI's command tree. No tool or CLI command callback is invoked, so nothing reaches deweloperuch.pl. The files are `version.json`, `mcp_tools.json` (including the server's instructions), `mcp_prompts.json` and `cli_commands.json` (version, visible leaf commands, usage and help summaries). CLI commands and JSON keys are sorted, parameter order is preserved, and there are no timestamps or terminal-dependent help layouts.
- `tools/build_docs.py` renders both complete pages, including the saved demo, from the source templates and committed data. `tools/release.py` loads and validates release snapshots, `tools/docs_content.py` holds editorial groups and copy, `tools/docs_renderer.py` renders the pages using `tools/page_config.py` and `tools/page_fragments.py`, and `tools/html_helpers.py` holds shared HTML and template helpers. The fact check compares the complete published pages with this same build output, including static content.
- The CLI table comes entirely from `cli_commands.json`, including `stop` and `skill` commands; there is no hand-maintained command list. Missing, malformed, duplicate or version-mismatched CLI metadata stops the build before either page is changed. Tool groups remain editorial: a release with a new or removed MCP tool stops the build with its name and the file to update.
- `.github/workflows/sync.yml` runs both daily when the latest release on PyPI is not the pinned one, runs the tests and opens or updates a pull request from `sync/deweloperuch-mcp` with their outcome. It never merges. It needs "Allow GitHub Actions to create and approve pull requests" in the repository's Actions settings.

## Demo data

The demo window on the landing page is rendered from `data/demo.json`, one real `search_offers` result with its capture date:

```bash
uv run python -m tools.capture_demo wroclaw --city-locative "we Wrocławiu" \
  --question "Pokaż mieszkania 40–55 m² we Wrocławiu z największą obniżką ceny." --area-min 40 --area-max 55
python3 -m tools.build_docs
```

- `tools/capture_demo.py` sends one request to deweloperuch.pl through the pinned release's CLI and its shared limiter (`uvx --from deweloperuch-mcp==<pinned> deweloperuch offers search … --sort biggest_drop --json`). Run it by hand only, never in tests or CI, and never in a loop. It writes nothing when the CLI fails, when an offer shown lacks an area, a price or a drop from the first price, or when the top 3 offers come from fewer than two developers (pick another query); on STOP (exit code 3) stop and report. The question and the city in the locative are written by hand and must say what the filters do.
- `tools/update_demo.py` supplies the demo title, conversation and call-inspector slots to the offline build. CI renders the committed `data/demo.json` as part of the full page build and fails on any difference. The old `python3 -m tools.update_demo` command remains a compatibility alias for that same full build.

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

## Social image and icons

Regenerate after a hero, page-source, local resource or renderer dependency change:

```bash
uv sync --locked
uv run playwright install chromium
uv run python -m tools.build_docs
uv run python -m tools.render_images
uv run python -m tools.render_images --check
```

Review `assets/og.png`: the complete heading, description and skyline must appear;
navigation, release badge, install/command controls and the note must be hidden.
Commit the generated PNGs and `assets/images.json` together. Rendering only serves
local files and blocks every external request, including analytics; it never captures
new demo data or contacts deweloperuch.pl.

The landing source template owns `data-social-hide` roles (`navigation`, `version`,
`actions`, `note`) and `data-social-content` roles (`heading`, `description`, `skyline`).
Each role occurs exactly once. These attributes do not affect the published page.
The renderer preserves layout while hiding the marked controls, waits for fonts,
and rejects missing hooks, hidden/empty copy and copy outside the 1200×630 capture.
Keep the attributes when wrapping or reordering markup.

`--check` checks SHA-256 digests of the published landing HTML, renderer, lockfile,
local CSS/JavaScript, WOFF2 fonts, icon PNGs and generated images. It first compares
`index.html` with the canonical offline page render, so changes to shared page
configuration/fragments, landing templates, release metadata and saved demo inputs
cannot be hidden behind an old generated page. Resource families are tracked
conservatively, including additions. Missing or stale inputs/outputs fail with the
regeneration commands. CI runs this check after the page build; release-sync PRs
regenerate the images before running tests.

Chromium, Playwright, Pillow, OS and architecture are recorded as provenance.
The portable freshness check does not rerender or compare pixels across platforms:
font rasterization can differ between macOS and Linux. Browser regression tests
verify composition and compare wrapper/reorder captures within the same browser.
Review the generated image when updating the renderer or locked dependencies.

The icon in `assets/icon/` and its derivatives (`favicon.png`, `apple-touch-icon.png`,
`assets/og.png`) remain outside MIT: all rights reserved, used with permission of
the deweloperuch.pl authors. See `LICENSE`.

## Checks

The checks use [uv](https://docs.astral.sh/uv/) (Python ≥ 3.12):

```bash
uv sync
uv run playwright install chromium
uv run pytest
```

- `tests/test_check_facts.py`, `tests/test_build_docs.py`, `tests/test_sync_server.py`, `tests/test_export_cli.py`: the fact check, build and sync against the saved MCP/CLI listings, offline; metadata changes, repeated builds, command order, invalid inputs and callback-free CLI discovery are covered.
- `tests/test_capture_demo.py`, `tests/test_update_demo.py`: the capture with the CLI replaced by a fake, and the demo window against `data/demo.json`, offline.
- `tests/test_package_site.py`: the public package against temporary files, including exact contents, excluded repository files, stale-output cleanup and symlink rejection, offline.
- `tests/e2e/`: both pages in headless Chromium against a local server, offline (the analytics script is stubbed): copy buttons, the demo, no horizontal overflow at 320 and 375 px, focus rings, requests, cookies, links.
- `tests/visual/`: maintained Linux Chromium full-page and hover/focus image comparisons. The default test command deselects the `visual` marker; the explicit CI visual job always runs it and fails on missing baselines. See [Visual and browser regression tests](docs/visual-tests.md) for the renderer, inspection and deliberate updates.
- `uv run python -m tools.check_facts` (or explicit `--pinned`): the page facts, written copy, complete generated pages and demo against the committed pinned MCP/CLI snapshots, offline. It never discovers the latest release. `LANDING_NETWORK=1 uv run pytest` additionally checks the pages against metadata inspected from the pinned package and syncs that package into a temporary folder to compare it with `data/server/`.
- `uv run python -m tools.check_facts --latest`: explicit release-freshness monitoring against the latest deweloperuch-mcp on PyPI. It lists MCP tools and prompts and inspects the CLI without invoking callbacks, so nothing reaches deweloperuch.pl. A release other than the pinned one fails with synchronization commands; a failed latest-release lookup also fails this monitor.
- `uv run python -m tools.render_images`: renders `favicon.png`, `apple-touch-icon.png` and `assets/og.png`.
- `tests/test_images.py` and `tests/e2e/test_social_image.py`: image freshness, same-size wrong-image detection, semantic composition and wrapper/reorder regressions, offline. `uv run python -m tools.render_images --check` checks the committed manifest without launching Chromium.

CI (`.github/workflows/check.yml`, on every push, pull request and manual run) calls the reusable `validate.yml` workflow for the tests, the offline pinned fact check, a guard that syncs the pinned release, builds both complete pages including the saved demo and fails on any difference from the commit, the Nu HTML validator, a link check (`lychee.toml`; deweloperuch.pl is never requested) and Lighthouse (`lighthouserc.json`, mobile: accessibility 100, best practices ≥ 95, performance ≥ 90). No validation job resolves the latest deweloperuch-mcp release. Local Lighthouse measures each commit's own files; `pages.yml` measures the live site after every deployment.

Release freshness is a separate scheduled/manual concern. `.github/workflows/freshness.yml` runs `tools.check_facts --latest` every Monday and on manual dispatch, leaves a red workflow status for stale metadata or lookup failures, and records the result in the Actions job summary. `.github/workflows/sync.yml` independently discovers the latest release daily or manually, reports a stale pin with a warning and summary, and opens or updates the synchronization PR as described above. A new upstream release or unavailable latest-version lookup does not fail Check or block publication of otherwise valid pinned inputs.

## Publishing

`.github/workflows/pages.yml` runs on every push to `main` and can be started manually. Both paths first call the same `validate.yml` workflow as Check, from the same commit. Every validation checkout and the deployment checkout use the event's immutable `github.sha`; deployment depends on successful validation in that run, so a failed, skipped or cancelled required check blocks publishing and success for another commit cannot authorize it. The Release freshness and Sync workflows report upstream freshness independently; neither is a deployment gate. Pages publishes only the site files (`index.html`, `docs/index.html`, `css/`, `js/`, `assets/`, the icons), then runs a separate Lighthouse job against the live pages.

`tools/package_site.py` is the single packaging definition used by local Lighthouse in `validate.yml` and by Pages deployment. Both run the same dependency-free command against the same commit:

```bash
python3 -m tools.package_site
```

The script recreates `_site/` from its explicit `PUBLIC_FILES` and `PUBLIC_DIRECTORIES` allowlists. Lighthouse serves `_site/` and Pages uploads `_site/`; tools, tests, data snapshots, design references, plans and repository metadata stay outside the package. Missing public inputs and symlinks in public paths fail packaging before an existing `_site/` is removed. `_site/` must be a regular directory, and the output location is fixed to prevent cleanup of another path.

To add a page, add its path to `PUBLIC_FILES` in `tools/package_site.py`. Files under `css/`, `js/` and `assets/` are included recursively; add a new public asset directory to `PUBLIC_DIRECTORIES` only when it belongs on the site. Keep private source files outside these public directories. No workflow copy list needs changing. Add a new page URL to the Lighthouse collect commands if it should also be audited.

Verify the package offline, inspect the new path under `_site/`, then serve that directory to check its URL and assets:

```bash
uv run pytest tests/test_package_site.py
python3 -m tools.package_site
python3 -m http.server --directory _site 8000
```

Open the page at `http://127.0.0.1:8000/<path>`. This previews the same output directory that the Check workflow measures and the Pages workflow publishes. The `lighthouse` job's “Package the public site” step in `validate.yml` and the `deploy` job's matching step in `pages.yml` show the shared command in each path.

## Licence

MIT for the code (see `LICENSE`). The icon in `assets/icon/` is not covered by the MIT licence: all rights reserved, used with permission of the deweloperuch.pl authors.

This is an unofficial community project, not affiliated with deweloperuch.pl, GUS or NBP.
