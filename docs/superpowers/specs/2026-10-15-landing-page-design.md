# deweloperuch-mcp landing page: design

Status: draft for review (2026-10-15)
Visual source of truth: Claude Design canvas https://claude.ai/artifact/CGQj1v9utFsHLV3CdtScuV, page "v3 · noc nad miastem", artboard `Noc.dc.html`.

## 1. Goal

A one-page marketing site for [deweloperuch-mcp](https://github.com/MBratkowski/deweloperuch-mcp) that looks and reads like the landing page of a paid SaaS product, while the product itself is free and open source. A visitor should understand within 10 seconds what it is and who it is for, and reach a working installation in at most two clicks.

Conversions, in order: installation (Claude Desktop extension, `uvx`, skill), a visit to the GitHub repository.

## 2. Decisions

| # | Decision | Source |
|---|---|---|
| L1 | SaaS-style page, free product; the pricing slot becomes a single "0 zł, na zawsze" plan card | user, 2026-10-15 |
| L2 | Audience: everyone (home buyers, market professionals, people building with AI); a "Dla kogo" section with three personas | user |
| L3 | Polish copy only | user |
| L4 | Separate repository `MBratkowski/deweloperuch-mcp-site`, published with GitHub Pages | user |
| L5 | Design work happens in Claude Design | user |
| L6 | Visual direction "Noc nad miastem" (v3): deweloperuch.pl palette and Poppins, a modern macOS-like software look, layout inspired by openchamber.dev | user picked v3 after rejecting v1 and v2 |
| L7 | Copy speaks about "AI" generically, not "Claude"; Claude Desktop and Claude Code are named only where installation facts require it | user |
| L8 | Official project icon (hard-hat mascot with "MCP+CLI") used with the consent of the deweloperuch.pl authors | user |
| L9 | Every shell command on the page has a copy button | user |
| L10 | Animated demo built from real tool results, captured once and dated | user |
| L11 | Cookieless analytics, no consent banner: Umami Cloud (the user's existing account, GitHub sign-in) instead of GoatCounter; events via `data-umami-event` | user, 2026-10-15 |
| L12 | Plain static HTML, CSS and vanilla JS, no build step; consistency with the package is enforced by a CI check | user |
| L13 | The official icon is excluded from the MIT licence: "all rights reserved, used with permission", stated in `LICENSE` and the README | user, spec review |
| L14 | Persona examples use neutral wording, no real developer names | user, spec review |

## 3. Honesty and brand rules

- No invented testimonials, logos, user counts, star counts or timings. Social proof only from verifiable facts: the MCP Registry listing, PyPI, the MIT licence, numbers that the CI check reads from the package (13 tools, 4 prompts, current version).
- The deweloperuch.pl wordmark and logo are not used. The page borrows the service's palette, typeface and colour-per-data-type convention, and uses the project's own official icon (L8).
- A visible statement that the project is unofficial and runs with the authors' consent appears in the hero meta line, the FAQ and the footer.
- Every number in the demo comes from a real tool response and carries its capture date.
- Statements about compatibility stay precise: the server is a local (stdio) MCP server for macOS and Linux; remote-only MCP clients and Windows are not supported (FAQ says so).

## 4. Visual system

Taken from the canvas artboard; the implementation turns these into CSS custom properties.

**Colour**

| Token | Value | Use |
|---|---|---|
| `--bg` | `#0C0A14` | page background |
| `--surface` | `#120E1C` | cards, app window |
| `--surface-sunken` | `#0F0B18` | sidebars, inspector |
| `--bubble` | `#2A2140` | chat bubbles |
| `--border` | `rgba(255,255,255,0.08)` | hairlines |
| `--text` | `#F1EEF7` | primary text |
| `--text-2` | `#D3CDE2` | text on the sky, list items |
| `--muted` | `#A7A1B8` | secondary text (contrast ≥ 7:1 on `--bg`) |
| `--comment` | `#8E879F` | terminal comments |
| `--accent` | `#34D399` | CTA background (text `#052E21`), eyebrows |
| `--accent-text` | `#6EE7B7` | links and success states |
| `--offers` / `--offers-text` | `#A78BFA` / `#C4B5FD` | asking prices (deweloperuch.pl purple) |
| `--rcn` / `--rcn-text` | `#FACC15` / `#FDE68A` | RCN transaction prices (gold) |
| `--official` / `--official-text` | `#38BDF8` / `#7DD3FC` | GUS and NBP (blue) |

Hero art: sky gradient `#0C0A14 → #1C1140 → #2D1B5C → #3B2475`, skyline layers `#25174C` and `#170E30`, cranes `#140C2A`, lit windows `#FACC15` / `#FDE68A`, crane beacon `#F87171`, green aurora and violet glow as soft radial gradients.

**Type**: Poppins 600/700 for headings and CTAs; the system UI stack (`-apple-system, BlinkMacSystemFont, "SF Pro Text", "Helvetica Neue", system-ui, sans-serif`) for body text; JetBrains Mono 400/500 for eyebrows, labels, code and numbers 01/02/03. Fonts are self-hosted as `woff2` (Latin and Latin Extended subsets), no Google Fonts requests.

**Shape**: pills `999px`, cards `16px`, plan card `24px`, app window `14px`, terminals `10px`. Glass surfaces use `backdrop-filter: blur(16–20px) saturate(160%)` over translucent `rgba(18,12,32,0.55)`.

## 5. Page structure and copy

Copy below is the approved Polish product text from the canvas; quoted literally.

1. **Navigation**: two glass pills. Left: icon, "Dane", "Dla kogo", "Instalacja", "FAQ", "Dokumentacja". Right: "GitHub", "Pobierz".
2. **Hero** over the illustrated night city (inline SVG: stars, aurora, two cranes with a blinking beacon, lit windows):
   - mono pill "v0.1.2 · MIT · w oficjalnym MCP Registry →" (version from the check, §8);
   - H1 "Każdy ruch rynku mieszkań." with the second line "W rozmowie z AI." in `--accent`;
   - lead "Serwer MCP, który daje Twojemu asystentowi AI ceny ofertowe deweloperów z historią zmian, ceny transakcyjne z rejestru RCN oraz statystyki GUS i NBP. Za darmo i open source.";
   - CTA "Zainstaluj za darmo" (to `#instalacja`), and a copyable pill "albo w terminalu: `uvx deweloperuch-mcp`";
   - the note "Nieoficjalny projekt, działa za zgodą autorów deweloperuch.pl." under it (§3).
3. **App window** (overlapping the bottom of the art): title "Asystent AI · Mieszkania w Krakowie", sidebar with the 4 prompts and the tool groups with counts (5 / 4 / 1 / 3), the conversation (animated, §6), and the call inspector with the real arguments and result fields of `search_offers`.
4. **Dane**: "Trzy rodzaje cen. Nigdy pomieszane." Rows 01 Ofertowe (purple), 02 Transakcyjne (gold), 03 Oficjalne (blue); each row lists its tools with one-line Polish descriptions, plus the helper tools. Tool grouping follows the server documentation (`docs/mcp/README.md`).
5. **Dla kogo**: "Jedno źródło. Trzy sposoby pytania." Cards "Kupujesz mieszkanie." (prompt "Oceń ofertę"), "Pracujesz z rynkiem." ("Porównaj miasta"), "Budujesz z AI." ("Prześwietl dewelopera"), each with an example question as a chat bubble. Examples stay neutral (L14): the third one is "Co buduje ten deweloper i w których miastach ma aktywne oferty?".
6. **Instalacja**: "Działa tam, gdzie już pracujesz." Three cards: Claude Desktop ("Bez terminala.", link to the latest release `.mcpb`), Claude Code and other MCP clients ("Jedna komenda.", terminal with `claude mcp add deweloperuch -- uvx deweloperuch-mcp`), terminal and agents ("CLI i skill.", `npx skills add MBratkowski/deweloperuch-mcp` and `uvx --from deweloperuch-mcp deweloperuch --help`). Note: macOS and Linux only.
7. **Zasady**: "Grzeczny klient." Four facts: read only; one request per second shared by all processes, plus a cache; stops itself after 401, 403, 429 or a captcha; runs with the authors' consent and an honest User-Agent.
8. **FAQ**: "Zanim zainstalujesz." Six native `<details>` items: official or not, cost, which AI, Windows, data source and freshness, not investment advice. The first is open.
9. **Final CTA**: large mascot, "Zapytaj o swoje mieszkanie.", plan card "Jedyny plan · 0 zł na zawsze" with four checks and the two buttons.
10. **Footer**: icon, disclaimer ("Nieoficjalny projekt społeczności, niepowiązany z deweloperuch.pl, GUS ani NBP; działa za zgodą autorów deweloperuch.pl. Dane bez gwarancji poprawności; to nie jest porada inwestycyjna. Licencja MIT."), link columns Produkt / Zasoby / Projekt, and a one-line analytics note.

## 6. Interactions

**Copy buttons** (every command: hero, three in Instalacja). `navigator.clipboard.writeText`; on success the label changes from "Kopiuj" to "Skopiowano" in `--accent-text` for 1.8 s, announced through an `aria-live="polite"` span. When the Clipboard API is missing or rejects, the command text gets selected and the label says "Zaznaczono, skopiuj ręcznie". Buttons are rendered `hidden` and revealed by JS, so a no-JS page never shows a dead button. Real `<button>` elements, at least 44×44 px.

**Animated demo** (app window, plays once when at least 40% of it enters the viewport):
1. The question types into the composer (about 30 characters per second), then becomes the user bubble.
2. The tool chip appears as "search_offers · w toku" with a pulsing dot; the inspector fills its argument lines one by one (80 ms apart).
3. The chip turns "gotowe"; the result lines appear in the inspector.
4. The answer sentence fades in, table rows appear with a 120 ms stagger, then the source line with the capture date.

The HTML ships the final state, so the page is complete without JS and for crawlers. With `prefers-reduced-motion: reduce` nothing animates. The same rule stops the star twinkle and the beacon blink.

**FAQ**: native `<details>`/`<summary>`, the "+" marker rotates on open (CSS only).

**Analytics**: GoatCounter page views plus click events on "Zainstaluj za darmo", "Pobierz", each copy button and the GitHub links (`data-goatcounter-click`). No cookies, no local storage.

## 7. Demo data

- `tools/capture_demo.py` runs the package CLI once: `uvx --from deweloperuch-mcp deweloperuch … --json`, through the package's own shared limiter, cache and STOP. It is run by hand when the demo should be refreshed, never in CI and never in a loop.
- The chosen query must give a top 3 from at least two developers, so the demo does not read as an advert for one company. The current mockup (Kraków, 40–55 m², up to 800 000 zł, sorted by price per m²) returns three units of one investment by one developer, so the final query is picked at capture time (at most a few requests).
- Output: `data/demo.json` with `captured_at` (ISO date), the `question`, the city in the locative for the window title, `inspector_args` (the tool arguments) and the fields the page shows (`total`, `returned`, `truncated`, three rows of offer name, investment, developer, area, price, price per m², and the `list_url`).
- `tools/update_demo.py` renders `data/demo.json` into the marked demo block of `index.html` (between `<!-- demo:start -->` and `<!-- demo:end -->`). The CI check fails when the block and the JSON differ.

## 8. Consistency checks (CI)

`tools/check_facts.py`, run on every push and weekly:

- starts the published server (`uvx deweloperuch-mcp`) over stdio with the MCP Python SDK and reads `tools/list` and `prompts/list`. It calls no tool, so it sends no request to deweloperuch.pl; the plan must confirm this from the server code before relying on it;
- reads the latest version from the PyPI JSON API;
- compares them with the facts marked in `index.html` by `data-fact` attributes: version, tool count, the tool names in the Dane section and the counts per group, the prompt titles (the install commands are checked by the page tests: every copy button copies exactly the command it shows);
- checks the demo block against `data/demo.json`.

Also in CI: HTML validation, an internal and external link check, and a Lighthouse run with these thresholds: accessibility 100, best practices ≥ 95, performance ≥ 90 (mobile).

## 9. Technical design

```
index.html              the page (final demo state inlined)
css/site.css            tokens, layout, components
js/site.js              copy buttons, demo animation, motion toggles (target < 8 KB)
assets/icon/            source.webp, icon-light.png, icon-dark.png, icon-tile-512.png
assets/fonts/           Poppins and JetBrains Mono woff2 + OFL licence files
assets/og.png           1200×630 social image rendered from the hero
favicon.png, apple-touch-icon.png   from icon-tile-512.png
data/demo.json          captured demo data
tools/                  capture_demo.py, update_demo.py, check_facts.py, render_images.py
docs/design/noc.dc.html export of the approved canvas artboard (design reference)
.github/workflows/      pages.yml (deploy), check.yml (§8)
README.md, LICENSE
```

- Relative URLs only, so the site works under `/deweloperuch-mcp-site/` on GitHub Pages and later under a custom domain.
- Head: `lang="pl"`, title "deweloperuch-mcp: dane o rynku mieszkań w rozmowie z AI", meta description, canonical, Open Graph and Twitter tags with `assets/og.png`, JSON-LD `SoftwareApplication` (price 0 PLN, operating systems macOS and Linux, licence MIT, links to GitHub and PyPI).
- Responsive: one fluid layout; the app window's three panes wrap into a column under about 900 px; the nav pills wrap; long commands scroll inside their row; tables scroll inside their box. No horizontal page scroll at 360 px.
- Accessibility: WCAG 2.1 AA contrast (checked tokens above), visible focus ring in `--accent`, landmarks (`header`, `nav`, `main`, `footer`), decorative SVG `aria-hidden`, a meaningful `alt` only where the icon carries meaning.
- No third-party requests except GoatCounter.

## 10. Publishing

- Public repository `MBratkowski/deweloperuch-mcp-site`, created only after the user confirms. Pages deploy from `main` with `actions/deploy-pages`. Address: `mbratkowski.github.io/deweloperuch-mcp-site`.
- GoatCounter site code: created by the user (an account cannot be created on their behalf).
- A link to the landing page from the server README is a separate change in the server repository.

## 11. Service etiquette

The rules of the server project still apply to anything here that touches deweloperuch.pl: requests only through the package (CLI or MCP server) and its shared limiter, as few as the task needs, never in CI, never crawling; on STOP, stop and report.

## 12. Acceptance criteria

1. The page matches the v3 artboard at 1440 px and works without horizontal scroll at 360 px.
2. All copy buttons copy the exact command on Safari, Chrome and Firefox; the fallback selects the text.
3. The demo plays once in view, is static with reduced motion and complete without JS.
4. `check_facts.py` passes against the current PyPI release; changing a marked fact on the page makes it fail.
5. Lighthouse thresholds from §8 are met on the deployed page.
6. No requests leave the page except GoatCounter (checked in the browser's network panel).
7. The demo shows its capture date and comes from `data/demo.json`.

## 13. Out of scope

English version, blog, documentation pages, testimonials (there are none yet), a custom domain, light theme, changes to the server repository (README link, swapping the icon there).

## 14. Open questions

1. Custom domain later (would change canonical URLs and the OG image URL).

Resolved at spec review (2026-10-15): the icon licence (L13) and neutral persona examples (L14).
