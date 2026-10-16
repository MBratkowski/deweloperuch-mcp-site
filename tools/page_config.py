"""Shared site settings and installation commands for both generated pages."""

import json
import shlex
from dataclasses import dataclass

PACKAGE = "deweloperuch-mcp"


@dataclass(frozen=True)
class SiteConfig:
    base_url: str = "https://mbratkowski.github.io/deweloperuch-mcp-site/"
    repository_url: str = "https://github.com/MBratkowski/deweloperuch-mcp"
    pypi_url: str = "https://pypi.org/project/deweloperuch-mcp/"
    analytics_script_url: str = "https://cloud.umami.is/script.js"
    analytics_website_id: str = "cdcef87a-abd4-4be6-970c-bef26c1152b6"
    analytics_domains: str = "mbratkowski.github.io"
    theme_color: str = "#0C0A14"

    @property
    def releases_url(self) -> str:
        return self.repository_url + "/releases/latest"


@dataclass(frozen=True)
class InstallationCommands:
    server_command: str = "uvx"
    server_args: tuple[str, ...] = (PACKAGE,)
    install_uv: str = "curl -LsSf https://astral.sh/uv/install.sh | sh"
    install_uv_homebrew: str = "brew install uv"
    check_uv: str = "uvx --version"
    install_skill: str = "npx skills add MBratkowski/deweloperuch-mcp"
    install_skill_cli: str = "uvx --from deweloperuch-mcp deweloperuch skill install"
    cli_help: str = "uvx --from deweloperuch-mcp deweloperuch --help"
    cli_install: str = "uv tool install deweloperuch-mcp"
    stop_status: str = "uvx --from deweloperuch-mcp deweloperuch stop status"
    stop_clear: str = "uvx --from deweloperuch-mcp deweloperuch stop clear"

    @property
    def run_server(self) -> str:
        return shlex.join((self.server_command, *self.server_args))

    @property
    def add_server(self) -> str:
        return "claude mcp add deweloperuch -- " + self.run_server

    @property
    def server_config(self) -> str:
        return json.dumps({"command": self.server_command, "args": list(self.server_args)}, ensure_ascii=False)

    @property
    def client_config(self) -> str:
        # Keep the compact argument list readable in the terminal illustration.
        return ('{\n  "mcpServers": {\n    "deweloperuch": {\n'
                f'      "command": {json.dumps(self.server_command)},\n'
                f'      "args": {json.dumps(list(self.server_args), ensure_ascii=False)}\n'
                '    }\n  }\n}')


@dataclass(frozen=True)
class PageConfig:
    path: str
    title: str
    description: str
    og_title: str
    og_description: str
    stylesheet: str
    font_preloads: tuple[str, ...] = ("poppins-latin-700-normal.woff2",)

    @property
    def asset_prefix(self) -> str:
        return "../" * len([part for part in self.path.split("/") if part])


SITE = SiteConfig()
COMMANDS = InstallationCommands()
PAGES = {
    "landing": PageConfig(
        path="", title="deweloperuch-mcp: dane o rynku mieszkań w rozmowie z AI",
        description="Serwer MCP, który daje Twojemu asystentowi AI ceny ofertowe deweloperów, ceny transakcyjne z RCN oraz statystyki GUS i NBP. Za darmo i open source.",
        og_title="deweloperuch-mcp: każdy ruch rynku mieszkań w rozmowie z AI",
        og_description="Ceny ofertowe deweloperów z historią zmian, ceny transakcyjne z RCN i statystyki GUS i NBP w Twoim asystencie AI. Za darmo i open source.",
        stylesheet="landing.css",
        font_preloads=("poppins-latin-700-normal.woff2", "poppins-latin-ext-700-normal.woff2"),
    ),
    "docs": PageConfig(
        path="docs/", title="Dokumentacja · deweloperuch-mcp",
        description="Jak zainstalować deweloperuch-mcp krok po kroku i jak z niego korzystać: instalacja z obrazkami, przykładowe pytania, narzędzia, prompty, CLI i rozwiązywanie problemów.",
        og_title="Dokumentacja deweloperuch-mcp",
        og_description="Instalacja krok po kroku, przykładowe pytania, narzędzia, prompty i rozwiązywanie problemów.",
        stylesheet="docs.css",
    ),
}


def agent_prompt() -> str:
    """The same installation instructions in both pages, derived from the commands."""
    return f"""Zainstaluj mi serwer MCP {PACKAGE} (dane o rynku mieszkań w Polsce z deweloperuch.pl, projekt: {SITE.repository_url}).

1. Sprawdź, czy mam macOS albo Linuxa. Windows nie jest obsługiwany: jeśli to Windows, przerwij i powiedz mi o tym.
2. Sprawdź, czy mam program uv ({COMMANDS.check_uv}). Jeśli nie, zainstaluj go oficjalnym skryptem: {COMMANDS.install_uv}
3. Sprawdź, czy serwer się uruchamia: {COMMANDS.run_server} --help
4. Dodaj serwer do aplikacji, której używam:
   - Claude Code: {COMMANDS.add_server}
   - Claude Desktop: w pliku claude_desktop_config.json (na macOS w ~/Library/Application Support/Claude/) dopisz w sekcji mcpServers wpis "deweloperuch": {COMMANDS.server_config}. Zachowaj istniejące wpisy i najpierw zrób kopię pliku.
   - Inna aplikacja: dodaj lokalny serwer MCP z poleceniem {COMMANDS.server_command} i argumentem {shlex.join(COMMANDS.server_args)}.
   Zanim zmienisz jakikolwiek plik konfiguracyjny, pokaż mi, co dokładnie zmienisz, i zapytaj o zgodę.
5. Nie wysyłaj w ramach instalacji żadnych zapytań do deweloperuch.pl.
6. Na koniec powiedz mi, czy muszę ponownie uruchomić aplikację, i podaj przykładowe pytanie na start."""
