"""Shared page fragments, with the landing/docs presentation variants preserved."""

import json
from pathlib import Path

from tools import page_config as config
from tools.html_helpers import e, render_template
from tools.release import ROOT, BuildError

SHARED_FRAGMENTS = ("head", "nav_brand", "nav_toggle", "nav_actions", "footer_notice")
GITHUB_ICON = ('<svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
               'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l2.6 5.6 6.1.7-4.5 4.2 1.2 6L12 16.6 6.6 19.5l1.2-6L3.3 9.3l6.1-.7z"></path></svg>')


def load_shared_templates(root: Path = ROOT) -> dict[str, str]:
    templates = {}
    for name in SHARED_FRAGMENTS:
        path = f"tools/templates/shared/{name}.html"
        try:
            templates[name] = (root / path).read_text(encoding="utf-8").strip()
        except OSError as error:
            raise BuildError(f"{path}: {error}") from error
    return templates


def structured_data() -> str:
    site = config.SITE
    data = {
        "@context": "https://schema.org", "@type": "SoftwareApplication", "name": config.PACKAGE,
        "description": "Serwer MCP z danymi o rynku mieszkań w Polsce: ceny ofertowe deweloperów, ceny transakcyjne z RCN, statystyki GUS i NBP.",
        "applicationCategory": "DeveloperApplication", "operatingSystem": "macOS, Linux",
        "license": "https://opensource.org/licenses/MIT", "offers": {"@type": "Offer", "price": "0", "priceCurrency": "PLN"},
        "url": site.base_url, "sameAs": [site.repository_url, site.pypi_url],
    }
    payload = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    return f'<script type="application/ld+json">\n{payload}\n</script>\n'


def page_fragments(page_name: str, templates: dict[str, str] | None = None) -> dict[str, str]:
    templates = load_shared_templates() if templates is None else templates
    site, page = config.SITE, config.PAGES[page_name]
    prefix, landing = page.asset_prefix, page_name == "landing"
    values = {
        "head": dict(
            title=e(page.title), description=e(page.description), theme_color=e(site.theme_color),
            canonical=e(site.base_url + page.path), og_title=e(page.og_title), og_description=e(page.og_description),
            og_image=e(site.base_url + "assets/og.png"), asset_prefix=e(prefix), stylesheet=e(page.stylesheet),
            font_preloads="\n".join(f'<link rel="preload" href="{e(prefix)}assets/fonts/{e(font)}" as="font" type="font/woff2" crossorigin>' for font in page.font_preloads),
            analytics_script_url=e(site.analytics_script_url), analytics_website_id=e(site.analytics_website_id),
            analytics_domains=e(site.analytics_domains), structured_data=structured_data() if landing else "",
        ),
        "nav_brand": dict(
            home_href="#" if landing else e(prefix), asset_prefix=e(prefix),
            image_attributes='class="brand-icon"' if landing else 'width="26" height="30"',
        ),
        "nav_toggle": {},
        "nav_actions": dict(
            nav_class="pill-nav pill-nav--actions" if landing else "pill-nav",
            nav_label="Pobieranie i kod" if landing else "Linki zewnętrzne",
            github_attributes='data-umami-event="nav-github" ' if landing else "",
            github_icon=GITHUB_ICON + "\n" if landing else "", repo=e(site.repository_url),
            releases=e(site.releases_url), download_event="nav-download" if landing else "docs-nav-download",
        ),
        "footer_notice": {},
    }
    return {
        name: render_template(templates[name], values[name], name=f"tools/templates/shared/{name}.html")
        for name in SHARED_FRAGMENTS
    }
