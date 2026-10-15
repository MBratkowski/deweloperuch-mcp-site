# deweloperuch-mcp-site

The landing page of deweloperuch-mcp. Spec: `docs/superpowers/specs/2026-10-15-landing-page-design.md`; plan: `docs/superpowers/plans/2026-10-15-landing-page.md`; design reference: `docs/design/noc.dc.html` and the Claude Design canvas linked from the spec.

- Code, comments, docs and commit messages are English; page copy is Polish and comes from the spec.
- Copy says "AI", not "Claude"; Claude Desktop and Claude Code appear only in the installation section and the FAQ.
- No invented numbers, testimonials or user counts; demo numbers come from `data/demo.json` and carry their capture date. `data/demo.json` is written only by `tools/capture_demo.py` (by hand, one request) and the demo window only by `python3 -m tools.update_demo`; never edit either by hand.
- Server facts (version, counts, tool groups and lists, the docs reference) are generated: `data/server/` from the pinned release by `uv run python -m tools.sync_server`, then `docs/index.html` and the marked blocks of `index.html` by `python3 tools/build_docs.py`. Never edit them by hand.
- deweloperuch.pl is reached only through the deweloperuch-mcp package (CLI or server) and its shared limiter: as few requests as the task needs, by hand, never from tests or CI, never crawling. On STOP (CLI exit code 3) stop and report.
- The pages make no third-party request except the Umami analytics script and its beacon (cloud.umami.is, gateway.umami.is).
- Commit only on the user's instruction.
