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

## Publishing

GitHub Pages serves the `main` branch root.

## Licence

MIT for the code (see `LICENSE`). The icon in `assets/icon/` is not covered by the MIT licence: all rights reserved, used with permission of the deweloperuch.pl authors.

This is an unofficial community project, not affiliated with deweloperuch.pl, GUS or NBP.
