# deweloperuch-mcp Landing Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and publish the one-page Polish landing page "Noc nad miastem" for deweloperuch-mcp as a static site on GitHub Pages.

**Architecture:** Hand-written `index.html` + `css/site.css` + one small `js/site.js`, no build step. Python tools (run with uv) capture demo data through the package CLI, render the demo block into the HTML, check page facts against the published package, and render images. Tests are pytest (unit) and pytest-playwright (end-to-end against a local static server).

**Tech Stack:** HTML, CSS, vanilla JS; Python ≥ 3.12 with uv; pytest, pytest-playwright (Chromium), Pillow, `mcp` Python SDK ≥ 1.12; GitHub Actions and GitHub Pages; GoatCounter.

**Spec:** `docs/superpowers/specs/2026-10-15-landing-page-design.md` (read it first). Visual reference: `docs/design/noc.dc.html` (export of the approved Claude Design artboard; its `/_blob/005ab9279f69d7a56f907d8f79cef53d` is `assets/icon/icon-light.png`).

## Global Constraints

- Page copy is Polish and taken verbatim from the spec §5 and `docs/design/noc.dc.html`; code, comments, tests, commit messages and docs are English.
- Copy says "AI", not "Claude"; Claude Desktop and Claude Code appear only in the Instalacja section and the FAQ answer "Z jakim AI to działa?" (L7).
- Persona examples stay neutral, no developer names (L14). The demo is the only place with real offer data.
- The page makes no network request except GoatCounter (none at all until Task 11); fonts are self-hosted; no Google Fonts.
- Relative URLs only (the site is served under `/deweloperuch-mcp-site/`).
- deweloperuch.pl is reached only through the package CLI in `tools/capture_demo.py`, run by hand, at most 3 requests per capture; never from tests or CI. On exit code 3 (STOP) stop and report.
- Tests never touch the network, except tests marked `@pytest.mark.network` (PyPI and `uvx`), which are skipped unless `LANDING_NETWORK=1`.
- WCAG 2.1 AA: tokens from spec §4, focus ring `2px solid var(--accent)` with `outline-offset: 3px`, touch targets ≥ 44×44 px, `prefers-reduced-motion: reduce` disables all animation.
- Demo timings: typing 30 characters/s, inspector lines 80 ms apart, chip "w toku" → "gotowe" after 900 ms, table rows 120 ms apart, IntersectionObserver threshold 0.4. Copy feedback lasts 1800 ms.
- Copy button labels: "Kopiuj", "Skopiowano", "Zaznaczono, skopiuj ręcznie".
- The official icon is not MIT-licensed: "All rights reserved, used with permission of the deweloperuch.pl authors" (L13).
- Commit steps run only if the user approved per-task commits for this execution; otherwise leave the changes uncommitted and continue.

## Review Focus

1. Safari requires `navigator.clipboard.writeText` to be called synchronously inside the click handler; an `await` or timer before it makes copying fail silently on iPhone. Pinned in Task 6 (`test_write_text_called_synchronously`).
2. The longest command (`uvx --from deweloperuch-mcp deweloperuch --help`) on a 320 px phone must scroll inside its row, never the page. Pinned in Task 7 (`test_no_horizontal_scroll_at_320`).
3. A visitor who scrolls past the demo mid-animation or switches tabs must still end with the complete final state, not a half-typed question. Pinned in Task 5 (`test_demo_finishes_after_scrolling_away`).
4. Keyboard users must see focus on every copy button, FAQ summary and CTA. Pinned in Task 7 (`test_focus_ring_visible_on_interactive_elements`).
5. A new server release that adds or renames a tool or prompt must turn CI red with a message naming it. Pinned in Task 8 (`test_compare_names_new_tool`).

---

## File Structure

```
index.html                 the page; the demo window sits between <!-- demo:start --> and <!-- demo:end -->
css/site.css               tokens (spec §4), @font-face, layout, components, motion
js/site.js                 copy buttons + demo animation (IIFE, < 8 KB, no dependencies)
js/vendor/count.js         GoatCounter script, self-hosted (Task 11)
assets/icon/               source.webp, icon-light.png, icon-dark.png, icon-tile-512.png (exist)
assets/fonts/              8 woff2 files + OFL-Poppins.txt, OFL-JetBrainsMono.txt
assets/og.png              1200×630
favicon.png (32×32), apple-touch-icon.png (180×180)
data/demo.json             captured demo data
tools/__init__.py
tools/demo.py              demo data model, formatting, HTML rendering, block sync
tools/capture_demo.py      CLI: capture data/demo.json through the package CLI
tools/update_demo.py       CLI: render data/demo.json into index.html
tools/check_facts.py       CLI + library: page facts vs the published package
tools/render_images.py     CLI: og.png, favicon.png, apple-touch-icon.png
tests/conftest.py          static server fixture, `network` marker
tests/fixtures/cli_offers_search.json   real CLI JSON shape (exists)
tests/test_demo.py, tests/test_check_facts.py, tests/test_images.py
tests/e2e/test_head.py, test_base.py, test_hero.py, test_demo_window.py, test_demo_animation.py, test_copy.py, test_sections.py, test_requests.py
.github/workflows/check.yml, .github/workflows/pages.yml, lighthouserc.json, lychee.toml
pyproject.toml, uv.lock, .gitignore, README.md, LICENSE, CLAUDE.md
```

---

### Task 1: Project skeleton and page head

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `README.md`, `LICENSE`, `CLAUDE.md`, `index.html`, `tools/__init__.py`, `tests/conftest.py`, `tests/e2e/test_head.py`

**Interfaces:**
- Produces: pytest fixture `site_url() -> str` (session scope; serves the repo root with `http.server` on a free port, returns `http://127.0.0.1:<port>/`); marker `network`; `index.html` landmarks `header`, `main`, `footer` and section ids `dane`, `dla-kogo`, `instalacja`, `zasady`, `faq` (empty placeholders filled by later tasks).

- [ ] **Step 1: Write `pyproject.toml`** with `[project] name = "deweloperuch-mcp-site"`, `requires-python = ">=3.12"`, no runtime dependencies, `[dependency-groups] dev = ["pytest>=8", "pytest-playwright>=0.5", "pillow>=10", "mcp>=1.12"]`, `[tool.pytest.ini_options] markers = ["network: needs PyPI and uvx; run with LANDING_NETWORK=1"]`. Run `uv sync` and `uv run playwright install chromium`.

- [ ] **Step 2: Write the failing test** `tests/e2e/test_head.py`:

```python
def test_head_metadata(page, site_url):
    page.goto(site_url)
    assert page.locator("html").get_attribute("lang") == "pl"
    assert page.title() == "deweloperuch-mcp: dane o rynku mieszkań w rozmowie z AI"
    assert page.locator('meta[name="description"]').get_attribute("content")
    assert page.locator('meta[property="og:image"]').get_attribute("content").endswith("assets/og.png")
    assert page.locator('link[rel="icon"]').get_attribute("href") == "favicon.png"
    data = json.loads(page.locator('script[type="application/ld+json"]').inner_text())
    assert data["@type"] == "SoftwareApplication"
    assert data["offers"]["price"] == "0" and data["offers"]["priceCurrency"] == "PLN"
    assert data["operatingSystem"] == "macOS, Linux"

def test_landmarks_and_sections(page, site_url):
    page.goto(site_url)
    for selector in ["header", "main", "footer", "#dane", "#dla-kogo", "#instalacja", "#zasady", "#faq"]:
        assert page.locator(selector).count() == 1, selector
```

- [ ] **Step 3: Run** `uv run pytest tests/e2e/test_head.py -v`. Expected: FAIL (no `index.html` / fixture).

- [ ] **Step 4: Implement** `tests/conftest.py` (`site_url` fixture with `http.server.ThreadingHTTPServer` and `functools.partial(SimpleHTTPRequestHandler, directory=<repo root>)` in a daemon thread; `network` tests skipped unless `os.environ.get("LANDING_NETWORK") == "1"`) and `index.html` with the head from spec §9 (title, description "Serwer MCP, który daje Twojemu asystentowi AI ceny ofertowe deweloperów, ceny transakcyjne z RCN oraz statystyki GUS i NBP. Za darmo i open source.", canonical `https://mbratkowski.github.io/deweloperuch-mcp-site/`, Open Graph and Twitter card tags pointing to `assets/og.png`, `favicon.png`, `apple-touch-icon.png`, JSON-LD `SoftwareApplication` with `name`, `description`, `applicationCategory: "DeveloperApplication"`, `operatingSystem: "macOS, Linux"`, `license: "https://opensource.org/licenses/MIT"`, `offers: {"@type": "Offer", "price": "0", "priceCurrency": "PLN"}`, `sameAs` GitHub and PyPI URLs) and the body skeleton (landmarks, empty sections with the ids above, `<link rel="stylesheet" href="css/site.css">`, `<script src="js/site.js" defer></script>`).

- [ ] **Step 5: Write the repository texts.** `LICENSE`: MIT for everything except `assets/icon/`, followed by the paragraph "The icon in assets/icon/ is not covered by the MIT licence. All rights reserved; used with permission of the deweloperuch.pl authors." and a pointer to the OFL files in `assets/fonts/`. `README.md`: what the site is, how to run the tests, the tools, and the licence note. `CLAUDE.md`: the language rule (code and docs English, UI copy Polish), the service etiquette from spec §11, and "commit only on the user's instruction". `.gitignore`: `.venv/`, `__pycache__/`, `.pytest_cache/`, `_site/`, `test-results/`.

- [ ] **Step 6: Run** `uv run pytest tests/e2e/test_head.py -v`. Expected: PASS.

- [ ] **Step 7: Commit** `git add -A && git commit -m "Add project skeleton, page head and test harness"`.

### Task 2: Fonts, tokens and base styles

**Files:**
- Create: `assets/fonts/*`, `css/site.css`, `tests/e2e/test_base.py`

**Interfaces:**
- Produces: CSS custom properties exactly as named in spec §4 (`--bg`, `--surface`, `--surface-sunken`, `--bubble`, `--border`, `--text`, `--text-2`, `--muted`, `--comment`, `--accent`, `--accent-text`, `--offers`, `--offers-text`, `--rcn`, `--rcn-text`, `--official`, `--official-text`); font families `"Poppins"` (600, 700) and `"JetBrains Mono"` (400, 500); utility classes `.eyebrow`, `.container` (max-width 1240px, inline padding 24px), `.btn`, `.btn--primary`, `.btn--ghost`, `.pill`, `.glass`.

- [ ] **Step 1: Write the failing test** `tests/e2e/test_base.py`:

```python
def test_tokens_and_fonts(page, site_url):
    requests = []
    page.on("request", lambda r: requests.append(r.url))
    page.goto(site_url)
    page.evaluate("document.fonts.ready")
    assert page.evaluate("getComputedStyle(document.body).backgroundColor") == "rgb(12, 10, 20)"
    assert page.evaluate("document.fonts.check('700 16px Poppins')")
    assert page.evaluate("document.fonts.check('500 16px \"JetBrains Mono\"')")
    assert not [u for u in requests if "googleapis" in u or "gstatic" in u]
```

- [ ] **Step 2: Run** `uv run pytest tests/e2e/test_base.py -v`. Expected: FAIL.

- [ ] **Step 3: Download the fonts** from Fontsource on jsDelivr. Resolve exact versions first: `curl -s "https://data.jsdelivr.com/v1/packages/npm/@fontsource/poppins/resolved?specifier=5"` and the same for `@fontsource/jetbrains-mono`; then fetch `files/poppins-{latin,latin-ext}-{600,700}-normal.woff2` and `files/jetbrains-mono-{latin,latin-ext}-{400,500}-normal.woff2` from `https://cdn.jsdelivr.net/npm/@fontsource/<pkg>@<version>/`, plus each package's `LICENSE` saved as `assets/fonts/OFL-Poppins.txt` and `assets/fonts/OFL-JetBrainsMono.txt`. Record both versions in `README.md`.

- [ ] **Step 4: Write `css/site.css`**: the tokens on `:root`, `@font-face` rules with `font-display: swap` and the Fontsource `unicode-range` values for latin and latin-ext, body with the system UI stack from spec §4 on `var(--bg)`, `:focus-visible` ring per Global Constraints, the utility classes above, and a `@media (prefers-reduced-motion: reduce)` block that sets `animation: none` and `transition: none` on `*`. Add `<link rel="preload" as="font" type="font/woff2" crossorigin>` for `poppins-latin-700-normal.woff2` in `index.html`.

- [ ] **Step 5: Run** `uv run pytest tests/e2e/test_base.py -v`. Expected: PASS.

- [ ] **Step 6: Commit** `git commit -am "Add self-hosted fonts, design tokens and base styles"` (add the new files first).

### Task 3: Navigation and hero

**Files:**
- Modify: `index.html` (header and the hero part of `main`), `css/site.css`
- Create: `tests/e2e/test_hero.py`

**Interfaces:**
- Consumes: tokens and utility classes from Task 2.
- Produces: `<section class="hero">`; under the command row the line "Nieoficjalny projekt, działa za zgodą autorów deweloperuch.pl." (`.hero-note`); the hero copy pill as a command row (the pattern every command uses): `<div class="cmd"><code data-copy-source>uvx deweloperuch-mcp</code><button type="button" class="copy" data-command="uvx deweloperuch-mcp" data-goatcounter-click="copy-hero" hidden><svg aria-hidden="true">…</svg><span aria-live="polite">Kopiuj</span></button></div>`; SVG classes `.star--twinkle` (on about a third of the stars) and `.beacon`.

- [ ] **Step 1: Write the failing test** `tests/e2e/test_hero.py`:

```python
def test_hero_copy_and_cta(page, site_url):
    page.goto(site_url)
    h1 = page.locator("h1")
    assert h1.inner_text().replace("\n", " ") == "Każdy ruch rynku mieszkań. W rozmowie z AI."
    cta = page.get_by_role("link", name="Zainstaluj za darmo").first
    assert cta.get_attribute("href") == "#instalacja"
    assert page.locator('[data-fact="version"]').inner_text().startswith("v")
    assert page.locator(".hero .cmd code").inner_text() == "uvx deweloperuch-mcp"
    assert page.locator(".hero-note").inner_text() == "Nieoficjalny projekt, działa za zgodą autorów deweloperuch.pl."

def test_nav_links_resolve(page, site_url):
    page.goto(site_url)
    for href in page.locator("header nav a[href^='#']").evaluate_all("els => els.map(e => e.getAttribute('href'))"):
        assert page.locator(href).count() == 1, href

def test_reduced_motion_stops_beacon(page, site_url):
    page.emulate_media(reduced_motion="reduce")
    page.goto(site_url)
    assert page.locator(".beacon").evaluate("e => getComputedStyle(e).animationName") == "none"
```

- [ ] **Step 2: Run** `uv run pytest tests/e2e/test_hero.py -v`. Expected: FAIL.

- [ ] **Step 3: Port the header and hero** from `docs/design/noc.dc.html` into semantic markup with classes in `css/site.css` (no inline styles): the two glass nav pills (links "Dane", "Dla kogo", "Instalacja", "FAQ", "Dokumentacja" → `https://github.com/MBratkowski/deweloperuch-mcp#readme`; right pill "GitHub", "Pobierz" → `https://github.com/MBratkowski/deweloperuch-mcp/releases/latest`), the hero SVG verbatim (decorative, `aria-hidden="true"`, positioned behind the content, height 760px, `preserveAspectRatio="xMidYMax slice"`), the version pill with `<span data-fact="version">v0.1.2</span> · MIT · w oficjalnym MCP Registry →` linking to `https://registry.modelcontextprotocol.io/` search for the server, the H1 (second line in `--accent`), the lead, the CTA "Zainstaluj za darmo" → `#instalacja`, and the command row above with the label "albo w terminalu:". Icon: `assets/icon/icon-light.png`, `width="26" height="30"`, `alt=""` inside a link with `aria-label="deweloperuch-mcp, strona główna"`.

- [ ] **Step 4: Add motion** in `css/site.css`: `.star--twinkle` opacity keyframes over 4 s with staggered `animation-delay`, `.beacon` opacity blink over 2.4 s.

- [ ] **Step 5: Run** `uv run pytest tests/e2e/test_hero.py -v`. Expected: PASS.

- [ ] **Step 6: Commit** `git commit -am "Add navigation and illustrated hero"`.

### Task 4: Demo data and the app window

**Files:**
- Create: `tools/demo.py`, `tools/capture_demo.py`, `tools/update_demo.py`, `data/demo.json`, `tests/test_demo.py`, `tests/e2e/test_demo_window.py`
- Modify: `index.html` (demo markers after the hero), `css/site.css`

**Interfaces:**
- Produces in `tools/demo.py`:
  - `CANDIDATES: list[Candidate]` and `@dataclass(frozen=True) class Candidate: city: str; city_locative: str; question: str; cli_args: list[str]; inspector_args: dict[str, str | int]`
  - `to_demo(cli_result: dict, candidate: Candidate, captured_at: date) -> dict`
  - `has_distinct_developers(demo: dict, minimum: int = 2) -> bool`
  - `format_pln(value: float) -> str`, `format_area(value: float) -> str`, `format_date(value: str) -> str`
  - `render_demo_block(demo: dict) -> str`
  - `sync_demo(html: str, demo: dict) -> str` and `demo_block_matches(html: str, demo: dict) -> bool`
  - Markers: `DEMO_START = "<!-- demo:start -->"`, `DEMO_END = "<!-- demo:end -->"`
- Produces in the rendered HTML (used by Tasks 5 and 8): `<figure id="demo" class="app-window" data-demo="final">`; animated parts carry `data-step` with values `question`, `chip`, `arg` (one per inspector argument), `result` (one per result line), `answer`, `row` (one per table row), `source`; the composer text `<span class="composer-text" data-question="…">`; the chip state text `<span class="chip-state">gotowe</span>`; sidebar group counts `<span data-fact="group-count" data-group="offers|rcn|official|helpers">`; sidebar prompt titles `data-prompt="<title>"`.
- `data/demo.json` schema:

```json
{
  "captured_at": "2026-10-15",
  "city_locative": "Krakowie",
  "question": "Znajdź mieszkania 40–55 m² w Krakowie do 800 000 zł, najtańsze za metr.",
  "tool": "search_offers",
  "inspector_args": {"city": "krakow", "area_min": 40, "area_max": 55, "price_max": 800000, "sort": "price_per_m2_asc"},
  "result": {"total": 1854, "returned": 10, "truncated": true, "list_url": "https://…"},
  "rows": [{"name": "…", "investment_name": "…", "developer_name": "…", "area_m2": 54.68, "price": 624942.0, "price_per_m2": 11429.08}]
}
```

- [ ] **Step 1: Write the failing unit tests** `tests/test_demo.py` (fixture `tests/fixtures/cli_offers_search.json` is the real CLI `--json` shape; its top 3 all come from "ATAL S.A."). Module fixtures: `cli_result` loads that file, `candidate` is `CANDIDATES[0]`, `demo` is `to_demo(cli_result, candidate, date(2026, 10, 15))`.

```python
def test_to_demo_takes_top_three_and_result_fields(cli_result, candidate):
    demo = to_demo(cli_result, candidate, date(2026, 10, 15))
    assert demo["captured_at"] == "2026-10-15"
    assert demo["result"] == {"total": 13324, "returned": 10, "truncated": True, "list_url": cli_result["list_url"]}
    assert [r["name"] for r in demo["rows"]] == [i["name"] for i in cli_result["items"][:3]]
    assert set(demo["rows"][0]) == {"name", "investment_name", "developer_name", "area_m2", "price", "price_per_m2"}

def test_distinct_developers_rule(cli_result, candidate):
    demo = to_demo(cli_result, candidate, date(2026, 10, 15))
    assert not has_distinct_developers(demo)
    demo["rows"][1]["developer_name"] = "Inny Deweloper S.A."
    assert has_distinct_developers(demo)

def test_polish_formatting():
    assert format_pln(624942.0) == "624 942 zł"
    assert format_pln(11429.08) == "11 429 zł"
    assert format_area(54.68) == "54,68 m²"
    assert format_date("2026-10-15") == "15.10.2026"

def test_render_contains_steps_in_order(demo):
    html = render_demo_block(demo)
    steps = re.findall(r'data-step="(\w+)"', html)
    assert steps[0] == "question" and steps[-1] == "source"
    assert steps.count("row") == 3 and steps.count("arg") == len(demo["inspector_args"])
    assert "Asystent AI · Mieszkania w " + demo["city_locative"] in html
    assert "stan na 15.10.2026" in html

def test_sync_replaces_only_the_block(demo):
    html = f"<p>before</p>{DEMO_START}old{DEMO_END}<p>after</p>"
    synced = sync_demo(html, demo)
    assert synced.startswith("<p>before</p>") and synced.endswith("<p>after</p>")
    assert demo_block_matches(synced, demo) and not demo_block_matches(html, demo)
```

- [ ] **Step 2: Run** `uv run pytest tests/test_demo.py -v`. Expected: FAIL (module missing).

- [ ] **Step 3: Implement `tools/demo.py`.** Formatting: thousands grouped with U+00A0, prices rounded to whole złoty, area with a comma and two decimals. `render_demo_block` produces the whole `<figure id="demo">` (title bar, sidebar with the 4 prompts and group counts 5/4/1/3, conversation, table, source line "Ceny ofertowe z deweloperuch.pl, stan na DD.MM.YYYY. Otwórz pełną listę" linking to `list_url`, composer "Pokaż historię ceny pierwszego lokalu…", inspector) following `docs/design/noc.dc.html`, with HTML escaping for every value. `CANDIDATES`, in order:
  1. Kraków: `["offers", "search", "krakow", "--area-min", "40", "--area-max", "55", "--price-max", "800000", "--sort", "price_per_m2_asc", "--json"]`, locative "Krakowie", question as in the schema.
  2. Warszawa: `[… "warszawa", "--area-min", "40", "--area-max", "60", "--price-max", "900000", "--sort", "price_per_m2_asc", "--json"]`, "Warszawie", "Znajdź mieszkania 40–60 m² w Warszawie do 900 000 zł, najtańsze za metr."
  3. Wrocław: `[… "wroclaw", "--area-min", "40", "--area-max", "60", "--sort", "biggest_drop", "--json"]`, "Wrocławiu", "Pokaż mieszkania 40–60 m² we Wrocławiu z największą obniżką ceny."
  `inspector_args` mirror each candidate's arguments with the MCP names (`city`, `area_min`, `area_max`, `price_max`, `sort`).

- [ ] **Step 4: Run** `uv run pytest tests/test_demo.py -v`. Expected: PASS.

- [ ] **Step 5: Implement the CLIs.** `tools/capture_demo.py` (`main() -> int`): for each candidate run `uvx --from deweloperuch-mcp deweloperuch <cli_args>` with `subprocess.run(..., capture_output=True, text=True, timeout=120)`; exit code 3 → print the stderr and return 3 (STOP: do not retry); other non-zero → return it; build the demo; stop at the first one with `has_distinct_developers`; write `data/demo.json` (UTF-8, `ensure_ascii=False`, indent 2); print which candidate won and how many requests were sent. `tools/update_demo.py` (`main() -> int`): read `data/demo.json`, `sync_demo` into `index.html`, write it back.

- [ ] **Step 6: Capture the data once** (live, at most 3 requests): `uv run python -m tools.capture_demo`. Expected: "candidate N chosen" and `data/demo.json` with three rows from at least two developers. If all three candidates fail the rule, stop and ask the user which query to use. Then add `<!-- demo:start --><!-- demo:end -->` after the hero in `index.html` and run `uv run python -m tools.update_demo`.

- [ ] **Step 7: Style the window** in `css/site.css` following the artboard: three panes in a wrapping flex row (sidebar `flex: 1 1 200px`, conversation `flex: 999 1 440px`, inspector `flex: 1 1 260px`), so they stack below about 900 px; the table scrolls inside `.table-scroll`.

- [ ] **Step 8: Write and run the e2e test** `tests/e2e/test_demo_window.py`:

```python
def test_final_state_without_js(browser, site_url):
    page = browser.new_page(java_script_enabled=False)
    page.goto(site_url)
    demo = json.loads(Path("data/demo.json").read_text())
    assert page.locator("#demo [data-step=row]").count() == 3
    assert page.locator("#demo .composer-text").count() == 1
    assert format_date(demo["captured_at"]) in page.locator("#demo [data-step=source]").inner_text()
    assert page.locator("#demo .chip-state").inner_text() == "gotowe"
```

Run `uv run pytest tests/e2e/test_demo_window.py -v`. Expected: PASS.

- [ ] **Step 9: Commit** `git add -A && git commit -m "Add demo data pipeline and the app window"`.

### Task 5: Demo animation

**Files:**
- Create: `js/site.js`, `tests/e2e/test_demo_animation.py`
- Modify: `css/site.css`

**Interfaces:**
- Consumes: the `#demo` markup contract from Task 4.
- Produces: `data-demo` states on `#demo`: `final` (HTML default, no JS or reduced motion), `ready` (JS armed, steps hidden), `playing`, `done`; CSS hides `[data-demo=ready] [data-step], [data-demo=playing] [data-step]:not([data-shown])`.

- [ ] **Step 1: Write the failing tests** `tests/e2e/test_demo_animation.py`:

```python
def test_waits_until_in_view(page, site_url):
    page.set_viewport_size({"width": 1440, "height": 600})
    page.goto(site_url)
    assert page.locator("#demo").get_attribute("data-demo") == "ready"

def test_plays_to_done_in_view(page, site_url):
    page.goto(site_url)
    page.locator("#demo").scroll_into_view_if_needed()
    page.wait_for_selector("#demo[data-demo=done]", timeout=15000)
    assert page.locator("#demo [data-step=row]:visible").count() == 3
    assert page.locator("#demo .chip-state").inner_text() == "gotowe"

def test_reduced_motion_shows_final_state(page, site_url):
    page.emulate_media(reduced_motion="reduce")
    page.goto(site_url)
    assert page.locator("#demo").get_attribute("data-demo") == "final"
    assert page.locator("#demo [data-step=row]:visible").count() == 3

def test_demo_finishes_after_scrolling_away(page, site_url):
    page.goto(site_url)
    page.locator("#demo").scroll_into_view_if_needed()
    page.wait_for_selector("#demo[data-demo=playing]")
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    page.wait_for_selector("#demo[data-demo=done]", state="attached", timeout=15000)
    assert page.locator("#demo .composer-text").get_attribute("data-question") == page.locator("#demo [data-step=question]").inner_text()
```

- [ ] **Step 2: Run** `uv run pytest tests/e2e/test_demo_animation.py -v`. Expected: FAIL.

- [ ] **Step 3: Implement the demo part of `js/site.js`** (`function initDemo(root: HTMLElement): void`): bail out when `matchMedia('(prefers-reduced-motion: reduce)')` matches; otherwise set `ready`, observe with threshold 0.4, and on first intersection run the sequence from spec §6 with the timings from Global Constraints (type the `data-question` text into `.composer-text`, then show the question bubble and clear the composer; show the chip with `.chip-state` "w toku"; reveal `arg` lines; switch the chip to "gotowe"; reveal `result`, `answer`, `row`s, `source`), setting `data-shown` on each step, then `done`. Once started the sequence always completes, whether visible or not (timers continue; do not pause on scroll).

- [ ] **Step 4: Run** `uv run pytest tests/e2e/test_demo_animation.py -v`. Expected: PASS.

- [ ] **Step 5: Commit** `git commit -am "Animate the demo conversation"`.

### Task 6: Copy buttons

**Files:**
- Modify: `js/site.js`, `css/site.css`
- Create: `tests/e2e/test_copy.py`

**Interfaces:**
- Consumes: the `.cmd` row pattern from Task 3 (every command on the page uses it, Task 7 adds three more).
- Produces: `function initCopy(button: HTMLButtonElement): void`.

- [ ] **Step 1: Write the failing tests** `tests/e2e/test_copy.py` (Chromium). Module fixture `clipboard_page`: `browser.new_context(permissions=["clipboard-read", "clipboard-write"]).new_page()`, closed after the test.

```python
COMMANDS = [
    "uvx deweloperuch-mcp",
    "claude mcp add deweloperuch -- uvx deweloperuch-mcp",
    "npx skills add MBratkowski/deweloperuch-mcp",
    "uvx --from deweloperuch-mcp deweloperuch --help",
]

def test_each_button_copies_its_command(clipboard_page, site_url):
    clipboard_page.goto(site_url)
    for command in COMMANDS:
        button = clipboard_page.locator(f'button.copy[data-command="{command}"]')
        button.click()
        assert clipboard_page.evaluate("navigator.clipboard.readText()") == command
        assert button.inner_text().strip() == "Skopiowano"
    clipboard_page.wait_for_timeout(1900)
    assert clipboard_page.locator("button.copy").first.inner_text().strip() == "Kopiuj"

def test_visible_command_equals_copied_text(page, site_url):
    page.goto(site_url)
    for row in page.locator(".cmd").all():
        assert row.locator("code[data-copy-source]").inner_text().lstrip("$ ") == row.locator("button.copy").get_attribute("data-command")

def test_fallback_selects_text(page, site_url):
    page.add_init_script("navigator.clipboard.writeText = () => Promise.reject(new Error('denied'))")
    page.goto(site_url)
    page.locator("button.copy").first.click()
    assert page.locator("button.copy").first.inner_text().strip() == "Zaznaczono, skopiuj ręcznie"
    assert page.evaluate("String(getSelection())") == COMMANDS[0]

def test_write_text_called_synchronously(page, site_url):
    page.add_init_script("""
      let inClick = false;
      addEventListener('click', () => { inClick = true; setTimeout(() => { inClick = false; }); }, true);
      navigator.clipboard.writeText = (t) => { window.__syncCopy = inClick; return Promise.resolve(); };
    """)
    page.goto(site_url)
    page.locator("button.copy").first.click()
    assert page.evaluate("window.__syncCopy") is True

def test_buttons_hidden_without_js(browser, site_url):
    page = browser.new_page(java_script_enabled=False)
    page.goto(site_url)
    assert page.locator("button.copy:visible").count() == 0
```

`test_each_button_copies_its_command` needs all four commands, so it passes only after Task 7; mark it `xfail(strict=True, reason="Instalacja commands arrive in Task 7")` here and remove the mark in Task 7.

- [ ] **Step 2: Run** `uv run pytest tests/e2e/test_copy.py -v`. Expected: FAIL (except the xfail).

- [ ] **Step 3: Implement `initCopy`** in `js/site.js`: remove `hidden`; on click call `navigator.clipboard.writeText(command)` synchronously; on success set the label "Skopiowano" and class `is-copied` (text colour `--accent-text`) and restore "Kopiuj" after 1800 ms (reset the timer on repeated clicks); on rejection or a missing API select the row's `code[data-copy-source]` text without the leading "$ " with a `Range`, and set "Zaznaczono, skopiuj ręcznie". The `$` prompt stays a separate `<span aria-hidden="true">` outside the selected text node.

- [ ] **Step 4: Run** `uv run pytest tests/e2e/test_copy.py -v`. Expected: PASS (the strict xfail still xfails).

- [ ] **Step 5: Commit** `git commit -am "Add copy buttons for commands"`.

### Task 7: Content sections

**Files:**
- Modify: `index.html` (sections `#dane`, `#dla-kogo`, `#instalacja`, `#zasady`, `#faq`, final CTA, footer), `css/site.css`, `tests/e2e/test_copy.py` (remove the xfail mark)
- Create: `tests/e2e/test_sections.py`

**Interfaces:**
- Consumes: `.cmd` rows (Task 3/6), tokens (Task 2).
- Produces: the final CTA as `<section class="final-cta">` containing the `.plan` card.
- Produces for Task 8: every tool name in `#dane` as `<dt data-tool="<name>">` (13, helpers row included), group rows with `data-group="offers|rcn|official|helpers"`; persona prompt chips with `data-prompt="<title>"`; plan card list item `<span data-fact="tool-count">13</span> narzędzi i <span data-fact="prompt-count">4</span> gotowe prompty`.

- [ ] **Step 1: Write the failing tests** `tests/e2e/test_sections.py`:

```python
def test_dane_lists_all_tools_by_group(page, site_url):
    page.goto(site_url)
    groups = {g: page.locator(f'#dane [data-group="{g}"] [data-tool]').count() for g in ("offers", "rcn", "official", "helpers")}
    assert groups == {"offers": 5, "rcn": 4, "official": 1, "helpers": 3}

def test_personas_are_neutral(page, site_url):
    page.goto(site_url)
    text = page.locator("#dla-kogo").inner_text()
    assert "Co buduje ten deweloper i w których miastach ma aktywne oferty?" in text
    assert "Atal" not in text and "ATAL" not in text

def test_faq(page, site_url):
    page.goto(site_url)
    items = page.locator("#faq details")
    assert items.count() == 6
    assert items.first.get_attribute("open") is not None
    assert "lokalne serwery MCP" in page.locator("#faq").inner_text()

def test_plan_card_and_footer(page, site_url):
    page.goto(site_url)
    assert "0 zł" in page.locator(".plan").inner_text()
    assert "Nieoficjalny projekt społeczności, niepowiązany z deweloperuch.pl, GUS ani NBP." in page.locator("footer").inner_text()

def test_claude_named_only_where_allowed(page, site_url):
    page.goto(site_url)
    for section in ["header", ".hero", "#dane", "#dla-kogo", "#zasady", ".final-cta", "footer"]:
        assert "Claude" not in page.locator(section).inner_text(), section

def test_no_horizontal_scroll_at_320(page, site_url):
    for width in (320, 360):
        page.set_viewport_size({"width": width, "height": 800})
        page.goto(site_url)
        assert page.evaluate("document.documentElement.scrollWidth") <= width

def test_focus_ring_visible_on_interactive_elements(page, site_url):
    page.goto(site_url)
    for selector in ["button.copy", "#faq summary", ".btn--primary"]:
        page.locator(selector).first.focus()
        page.keyboard.press("Shift+Tab"); page.keyboard.press("Tab")
        style = page.evaluate("getComputedStyle(document.activeElement).outlineStyle")
        assert style != "none", selector
```

- [ ] **Step 2: Run** `uv run pytest tests/e2e/test_sections.py -v`. Expected: FAIL.

- [ ] **Step 3: Port the sections** from `docs/design/noc.dc.html` with the copy from spec §5 and the markers above: Dane (rows 01/02/03 plus the helpers row), Dla kogo (three persona cards; the third example is "Co buduje ten deweloper i w których miastach ma aktywne oferty?"), Instalacja (three cards; the two terminal cards use `.cmd` rows for the three commands with `data-goatcounter-click` values `copy-claude`, `copy-skill`, `copy-cli`; the Claude Desktop card links to the latest release), Zasady, FAQ (six `<details>`, the first `open`, a CSS "+" that rotates 45° on `[open]`; `summary::-webkit-details-marker { display: none }`), final CTA with the large icon `assets/icon/icon-light.png` (`width="121" height="140"`) and the `.plan` card, footer with link columns and the line "Liczymy wejścia bez cookies (GoatCounter)." (added in Task 11, leave it out until then). Remove the xfail mark from `test_each_button_copies_its_command`.

- [ ] **Step 4: Run** `uv run pytest -v`. Expected: all PASS.

- [ ] **Step 5: Commit** `git commit -am "Add data, personas, installation, principles, FAQ and pricing sections"`.

### Task 8: Fact check against the published package

**Files:**
- Create: `tools/check_facts.py`, `tests/test_check_facts.py`

**Interfaces:**
- Consumes: the markers from Tasks 3, 4, 7; `tools.demo.demo_block_matches`.
- Produces:
  - `@dataclass(frozen=True) class PackageFacts: version: str; tools: frozenset[str]; prompt_titles: frozenset[str]`
  - `@dataclass(frozen=True) class PageFacts: version: str; tool_count: int; prompt_count: int; tools: frozenset[str]; tool_groups: dict[str, frozenset[str]]; group_counts: dict[str, int]; prompt_titles: frozenset[str]`
  - `GROUPS: dict[str, frozenset[str]]` (offers, rcn, official, helpers; spec §5 item 4 and the server's `docs/mcp/README.md`)
  - `read_page_facts(html: str) -> PageFacts` (stdlib `html.parser`)
  - `compare(page: PageFacts, package: PackageFacts) -> list[str]` (one Polish-free English message per mismatch, naming the item)
  - `async def read_package_facts() -> PackageFacts` (PyPI JSON `https://pypi.org/pypi/deweloperuch-mcp/json` → `info.version`; then `StdioServerParameters(command="uvx", args=[f"deweloperuch-mcp=={version}"])`, `ClientSession.initialize`, `list_tools`, `list_prompts`, using `prompt.title`)
  - `main() -> int` (0 when `compare` is empty and the demo block matches `data/demo.json`, else prints the messages and returns 1)

Verified in the server code (2026-10-15): starting the server opens a session that only constructs its HTTP client, and `tools/list` / `prompts/list` are answered by the MCP framework without calling a use case, so this sends no request to deweloperuch.pl. The server's real tool names: `search_locations`, `search_offers`, `get_offer`, `search_investments`, `get_investment`, `search_developers`, `get_developer`, `get_market_statistics`, `get_transaction_statistics`, `rank_locations`, `get_building`, `estimate_property_value`, `get_government_statistics`; prompt titles: "Znajdź mieszkanie", "Oceń ofertę", "Prześwietl dewelopera", "Porównaj miasta".

- [ ] **Step 1: Write the failing tests** `tests/test_check_facts.py`:

```python
def package(**overrides):
    base = dict(version="0.1.2", tools=frozenset(ALL_TOOLS), prompt_titles=frozenset(PROMPT_TITLES))
    return PackageFacts(**{**base, **overrides})

def test_page_matches_package():
    page = read_page_facts(Path("index.html").read_text())
    assert compare(page, package(version=page.version)) == []

def test_compare_reports_version():
    page = read_page_facts(Path("index.html").read_text())
    assert any("0.9.9" in m for m in compare(page, package(version="0.9.9")))

def test_compare_names_new_tool():
    page = read_page_facts(Path("index.html").read_text())
    messages = compare(page, package(version=page.version, tools=frozenset(ALL_TOOLS | {"search_parcels"})))
    assert any("search_parcels" in m for m in messages)

def test_compare_names_renamed_prompt():
    page = read_page_facts(Path("index.html").read_text())
    titles = frozenset(PROMPT_TITLES - {"Oceń ofertę"} | {"Oceń cenę"})
    messages = compare(page, package(version=page.version, prompt_titles=titles))
    assert any("Oceń ofertę" in m for m in messages) and any("Oceń cenę" in m for m in messages)

def test_groups_cover_tools_exactly():
    assert frozenset().union(*GROUPS.values()) == ALL_TOOLS

@pytest.mark.network
def test_live_package_matches_page():
    assert asyncio.run(read_package_facts()).tools == ALL_TOOLS
```

(`ALL_TOOLS` and `PROMPT_TITLES` are module constants in the test file with the values listed above.)

- [ ] **Step 2: Run** `uv run pytest tests/test_check_facts.py -v`. Expected: FAIL.

- [ ] **Step 3: Implement `tools/check_facts.py`** with the interfaces above. `compare` checks: version (page `v` stripped), `tool_count == len(package.tools)`, `prompt_count == len(package.prompt_titles)`, page tools == package tools (missing and extra named), each page group equals `GROUPS[group] & package.tools`, each sidebar group count equals the size of its group, page prompt titles == package titles (missing and extra named).

- [ ] **Step 4: Run** `uv run pytest tests/test_check_facts.py -v`, then `LANDING_NETWORK=1 uv run pytest -m network -v` and `uv run python -m tools.check_facts`. Expected: PASS and exit code 0.

- [ ] **Step 5: Commit** `git add -A && git commit -m "Check page facts against the published package"`.

### Task 9: Social image and favicons

**Files:**
- Create: `tools/render_images.py`, `tests/test_images.py`, `assets/og.png`, `favicon.png`, `apple-touch-icon.png`

**Interfaces:**
- Produces: `render_favicons(tile: Path, out_dir: Path) -> None` (Pillow, LANCZOS) and `render_og(site_url: str, out: Path) -> None` (Playwright, viewport 1200×630, `reduced_motion="reduce"`, screenshot of the top of the page with the nav hidden by an injected style).

- [ ] **Step 1: Write the failing test** `tests/test_images.py`:

```python
@pytest.mark.parametrize("path,size", [("assets/og.png", (1200, 630)), ("favicon.png", (32, 32)), ("apple-touch-icon.png", (180, 180))])
def test_image_sizes(path, size):
    assert Image.open(path).size == size
```

- [ ] **Step 2: Run** `uv run pytest tests/test_images.py -v`. Expected: FAIL.

- [ ] **Step 3: Implement `tools/render_images.py`** (`main() -> int` starts the same static server as the test fixture, calls both functions with `assets/icon/icon-tile-512.png`) and run `uv run python -m tools.render_images`. Look at `assets/og.png`: the H1 and the skyline must be visible and not cut.

- [ ] **Step 4: Run** `uv run pytest -v`. Expected: all PASS (Task 1 head test now resolves real files).

- [ ] **Step 5: Commit** `git add -A && git commit -m "Add social image and favicons"`.

### Task 10: CI and Pages workflows

**Files:**
- Create: `.github/workflows/check.yml`, `.github/workflows/pages.yml`, `lighthouserc.json`, `lychee.toml`, `tests/e2e/test_requests.py`

**Interfaces:**
- Consumes: `tools.check_facts.main`, the test suite.
- Produces: `check.yml` callable with `workflow_call`; `pages.yml` deploys `_site/` (only `index.html`, `css/`, `js/`, `assets/`, `favicon.png`, `apple-touch-icon.png`).

- [ ] **Step 1: Write the failing test** `tests/e2e/test_requests.py`:

```python
ALLOWED_EXTERNAL = ("goatcounter.com",)

def test_no_unexpected_requests_and_no_cookies(page, site_url):
    hosts = []
    page.on("request", lambda r: hosts.append(urlparse(r.url).hostname))
    page.goto(site_url)
    page.locator("footer").scroll_into_view_if_needed()
    page.wait_for_load_state("networkidle")
    external = {h for h in hosts if h not in ("127.0.0.1", "localhost")}
    assert all(h.endswith(ALLOWED_EXTERNAL) for h in external), external
    assert page.context.cookies() == []
```

- [ ] **Step 2: Run** `uv run pytest tests/e2e/test_requests.py -v`. Expected: PASS already (guard for later changes; keep it).

- [ ] **Step 3: Write `check.yml`**: triggers `push`, `pull_request`, `schedule` (`0 6 * * 1`), `workflow_call`; steps: checkout, `astral-sh/setup-uv`, `uv sync`, `uv run playwright install --with-deps chromium`, `uv run pytest -v`, `uv run python -m tools.check_facts`, HTML validation with the Nu validator (`Cyb3r-Jak3/html5validator-action`, root `.`, only `index.html`), `lycheeverse/lychee-action` with `lychee.toml` (check `index.html` and `README.md`; exclude `goatcounter.com`). Pin every action to its current major tag at implementation time.

- [ ] **Step 4: Write `pages.yml`**: on push to `main` and `workflow_dispatch`; job `check` uses `./.github/workflows/check.yml`; job `deploy` (needs `check`, permissions `pages: write`, `id-token: write`, environment `github-pages`) stages `_site/` with `mkdir _site && cp -r index.html css js assets favicon.png apple-touch-icon.png _site/`, then `actions/configure-pages`, `actions/upload-pages-artifact` (path `_site`), `actions/deploy-pages`; job `lighthouse` (needs `deploy`) runs `treosh/lighthouse-ci-action` on the deployed URL with `lighthouserc.json` asserting `categories:accessibility` ≥ 1, `categories:best-practices` ≥ 0.95, `categories:performance` ≥ 0.9 (mobile preset).

- [ ] **Step 5: Validate the workflows** with `uvx --from actionlint-py actionlint` (or `actionlint` if installed). Expected: no errors.

- [ ] **Step 6: Commit** `git add -A && git commit -m "Add CI checks and the Pages deployment"`.

### Task 11: Analytics

Blocked on the user's GoatCounter site code; ask for it, never create an account.

**Files:**
- Create: `js/vendor/count.js`
- Modify: `index.html`, footer line

- [ ] **Step 1: Ask the user** for the GoatCounter code (the `<code>` in `https://<code>.goatcounter.com`).

- [ ] **Step 2: Self-host the script**: save `https://gc.zgo.at/count.js` as `js/vendor/count.js` (record its date in `README.md`), add `<script data-goatcounter="https://<code>.goatcounter.com/count" async src="js/vendor/count.js"></script>` before `</body>`, add `data-goatcounter-click` to "Zainstaluj za darmo" (`cta-install`), "Pobierz" (`nav-download`) and the GitHub links (`github-nav`, `github-plan`), and add the footer line "Liczymy wejścia bez cookies (GoatCounter)."

- [ ] **Step 3: Run** `uv run pytest -v`. Expected: all PASS (`test_requests` allows `*.goatcounter.com`; GoatCounter does not count on localhost).

- [ ] **Step 4: Commit** `git commit -am "Add cookieless analytics"`.

### Task 12: Publish

Each outward step needs the user's explicit OK at that moment.

- [ ] **Step 1: With the user's OK**, create the public repository: `gh repo create MBratkowski/deweloperuch-mcp-site --public --source . --remote origin --description "Landing page of deweloperuch-mcp"`, push `main`, enable Pages with the Actions source: `gh api -X POST repos/MBratkowski/deweloperuch-mcp-site/pages -f build_type=workflow`.

- [ ] **Step 2: Watch the first run** of `pages.yml` with `gh run watch`. Expected: `check`, `deploy` and `lighthouse` green.

- [ ] **Step 3: Verify the live page** at `https://mbratkowski.github.io/deweloperuch-mcp-site/` in a browser: it matches the canvas artboard (page v3) at 1440 px and has no horizontal scroll at 360 px; the demo plays; copy works in Safari, Chrome and Firefox (manual check, spec §12.2); no console errors; the network panel shows only same-origin requests and GoatCounter.

- [ ] **Step 4: Report** to the user: the URL, the Lighthouse scores, and the follow-ups outside this repository (a link from the server README; the old icon in the server repository).
