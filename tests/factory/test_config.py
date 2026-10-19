"""The factory configuration file matches the design spec and rejects bad values."""

import json
from pathlib import Path

import pytest

from tools.factory.config import (
    CONFIG_PATH,
    ConfigError,
    Detectors,
    Limits,
    load_config,
)


def test_config_matches_the_spec() -> None:
    config = load_config()
    assert config.limits == Limits(3, 1, 2, 2, 5, 300, 2)
    assert config.matrix.engines == ("chromium", "firefox", "webkit")
    assert config.matrix.widths == (
        320, 375, 390, 414, 719, 721, 759, 761, 768, 1024, 1280, 1440, 1920,
    )
    assert config.matrix.height == 900
    assert config.matrix.breakpoints == (720, 760)
    assert config.matrix.mode_engines == ("chromium",)
    assert config.matrix.mode_widths == (320, 768, 1440)
    assert config.matrix.modes == {
        "no-js": {"java_script_enabled": False},
        "reduced-motion": {"reduced_motion": "reduce"},
        "short": {"height": 500},
    }
    assert config.matrix.menu_widths == (320, 375, 390, 414)
    assert config.matrix.keyboard_widths == (375, 1440)
    assert config.detectors == Detectors(8, 24, 44, 14)
    assert config.models == {
        "qa": "claude-sonnet-5-5",
        "fixer": "claude-sonnet-5-5",
        "reviewer": "claude-opus-5-5",
    }
    assert config.max_turns == {"qa": 40, "fixer": 80, "reviewer": 40}
    assert config.site.live_url == "https://mbratkowski.github.io/deweloperuch-mcp-site/"
    assert config.site.pages == {"landing": "", "docs": "docs/"}
    assert config.site.repo == "MBratkowski/deweloperuch-mcp-site"
    assert "MBratkowski" in config.trusted_logins


def test_fence_lists() -> None:
    fence = load_config().fence
    assert fence.allowed == (
        "css/**", "js/**", "tools/templates/**", "tools/html_helpers.py",
        "tools/page_fragments.py", "tools/docs_renderer.py", "tools/docs_content.py",
        "tools/page_config.py", "tools/build_docs.py", "tests/**", "index.html",
        "docs/index.html", "assets/images.json",
    )
    assert fence.forbidden == (
        ".github/**", "CLAUDE.md", "data/**", "tools/capture_demo.py",
        "tools/sync_server.py", "tools/check_facts.py", "tools/factory/**",
        "pyproject.toml", "uv.lock", "lighthouserc.json", "lychee.toml",
        "docs/superpowers/**", "assets/fonts/**", "assets/icon/**",
        "tests/visual/comparison.py",
    )
    assert fence.generated == ("index.html", "docs/index.html", "assets/images.json")


def test_trusted_adds_the_app_bot_login() -> None:
    config = load_config()
    assert config.trusted("factory-app") == frozenset({"MBratkowski", "factory-app[bot]"})
    assert config.trusted(None) == frozenset({"MBratkowski"})


def _write_variant(tmp_path: Path, mutate) -> Path:
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    mutate(data)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_invalid_config_names_the_field(tmp_path: Path) -> None:
    unsorted = _write_variant(tmp_path, lambda d: d["matrix"].update(widths=[375, 320]))
    with pytest.raises(ConfigError, match=r"matrix\.widths"):
        load_config(unsorted)

    missing = _write_variant(tmp_path, lambda d: d["limits"].pop("merges_per_day"))
    with pytest.raises(ConfigError, match=r"limits\.merges_per_day"):
        load_config(missing)
