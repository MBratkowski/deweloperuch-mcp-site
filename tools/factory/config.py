"""Typed access to .github/factory/config.json, the single source of factory limits."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CONFIG_PATH = Path(__file__).resolve().parents[2] / ".github" / "factory" / "config.json"

# FACTORY_MODE, the repository variable (R54). Every factory job runs only for a mode on its
# allow-list, so an unset or unknown value runs nothing: QA in dry, shadow and auto (dry is a dry run:
# nothing is written to GitHub), the fixer, the reviewer and the merger in shadow and auto. Setup
# creates the variable as `off`.
MODES = ("off", "dry", "shadow", "auto")
QA_MODES = ("dry", "shadow", "auto")
WRITE_MODES = ("shadow", "auto")


class ConfigError(ValueError):
    """The configuration file is missing a field or holds an invalid value."""


@dataclass(frozen=True)
class Limits:
    issues_per_run: int
    suggestions_per_run: int
    fixes_in_progress: int
    attempts_per_issue: int
    merges_per_day: int
    max_changed_lines: int
    # QA runs per UTC day whose agent (the triage job) succeeded; later runs skip the agent (M11).
    qa_agent_runs_per_day: int


@dataclass(frozen=True)
class Site:
    live_url: str
    pages: dict[str, str]
    repo: str


@dataclass(frozen=True)
class Matrix:
    engines: tuple[str, ...]
    widths: tuple[int, ...]
    height: int
    breakpoints: tuple[int, ...]
    mode_engines: tuple[str, ...]
    mode_widths: tuple[int, ...]
    modes: dict[str, dict]
    menu_widths: tuple[int, ...]
    keyboard_widths: tuple[int, ...]


@dataclass(frozen=True)
class Detectors:
    engine_diff_px: float
    target_min_px: int
    mobile_nav_target_min_px: int
    flaky_window_days: int


@dataclass(frozen=True)
class Fence:
    allowed: tuple[str, ...]
    forbidden: tuple[str, ...]
    generated: tuple[str, ...]


@dataclass(frozen=True)
class Config:
    limits: Limits
    site: Site
    matrix: Matrix
    detectors: Detectors
    fence: Fence
    models: dict[str, str]
    max_turns: dict[str, int]
    trusted_logins: tuple[str, ...]

    def trusted(self, app_slug: str | None) -> frozenset[str]:
        """Logins whose labels and issues the factory trusts, plus the App's bot login."""
        logins = set(self.trusted_logins)
        if app_slug:
            logins.add(f"{app_slug}[bot]")
        return frozenset(logins)


def _get(section: dict[str, Any], name: str, path: str) -> Any:
    if name not in section:
        raise ConfigError(f"missing field {path}.{name}")
    return section[name]


def _section(root: dict[str, Any], name: str) -> dict[str, Any]:
    value = root.get(name)
    if not isinstance(value, dict):
        raise ConfigError(f"missing section {name}")
    return value


def _ints(section: dict[str, Any], name: str, path: str, *, sorted_: bool = False) -> tuple[int, ...]:
    value = _get(section, name, path)
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, int) and not isinstance(item, bool) for item in value)
    ):
        raise ConfigError(f"{path}.{name} must be a non-empty list of integers")
    if sorted_ and (value != sorted(value) or len(set(value)) != len(value)):
        raise ConfigError(f"{path}.{name} must be strictly ascending")
    return tuple(value)


def _strings(section: dict[str, Any], name: str, path: str) -> tuple[str, ...]:
    value = _get(section, name, path)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"{path}.{name} must be a list of strings")
    return tuple(value)


def _number(section: dict[str, Any], name: str, path: str, *, positive: bool = True) -> Any:
    value = _get(section, name, path)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or (positive and value <= 0):
        raise ConfigError(f"{path}.{name} must be a positive number")
    return value


def _mapping(root: dict[str, Any], name: str, kind: type) -> dict[str, Any]:
    value = root.get(name)
    if not isinstance(value, dict) or not value:
        raise ConfigError(f"{name} must be a non-empty object")
    if not all(isinstance(item, kind) for item in value.values()):
        raise ConfigError(f"{name} values must be {kind.__name__}")
    return value


def load_config(path: Path = CONFIG_PATH) -> Config:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ConfigError(f"cannot read {path}: {error}") from error

    limits = _section(raw, "limits")
    site = _section(raw, "site")
    matrix = _section(raw, "matrix")
    detectors = _section(raw, "detectors")
    fence = _section(raw, "fence")

    modes = _get(matrix, "modes", "matrix")
    if not isinstance(modes, dict) or not all(isinstance(v, dict) for v in modes.values()):
        raise ConfigError("matrix.modes must map names to objects")
    pages = _get(site, "pages", "site")
    if not isinstance(pages, dict) or not all(isinstance(v, str) for v in pages.values()):
        raise ConfigError("site.pages must map names to paths")

    return Config(
        limits=Limits(**{
            name: _number(limits, name, "limits") for name in Limits.__dataclass_fields__
        }),
        site=Site(
            live_url=_get(site, "live_url", "site"),
            pages=dict(pages),
            repo=_get(site, "repo", "site"),
        ),
        matrix=Matrix(
            engines=_strings(matrix, "engines", "matrix"),
            widths=_ints(matrix, "widths", "matrix", sorted_=True),
            height=_number(matrix, "height", "matrix"),
            breakpoints=_ints(matrix, "breakpoints", "matrix", sorted_=True),
            mode_engines=_strings(matrix, "mode_engines", "matrix"),
            mode_widths=_ints(matrix, "mode_widths", "matrix", sorted_=True),
            modes=dict(modes),
            menu_widths=_ints(matrix, "menu_widths", "matrix", sorted_=True),
            keyboard_widths=_ints(matrix, "keyboard_widths", "matrix", sorted_=True),
        ),
        detectors=Detectors(**{
            name: _number(detectors, name, "detectors") for name in Detectors.__dataclass_fields__
        }),
        fence=Fence(**{
            name: _strings(fence, name, "fence") for name in Fence.__dataclass_fields__
        }),
        models=_mapping(raw, "models", str),
        max_turns=_mapping(raw, "max_turns", int),
        trusted_logins=_strings(raw, "trusted_logins", "config"),
    )
