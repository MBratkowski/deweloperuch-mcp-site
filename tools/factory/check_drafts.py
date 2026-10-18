"""Re-run every draft's reproduction on the live site and on a build of main; no write token."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable, Collection, Sequence
from pathlib import Path
from typing import Any

from tools.factory.config import load_config
from tools.factory.findings import Group
from tools.factory.issues import SCRIPT_PATTERN, Draft, DraftError, Dropped, load_drafts

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_TIMEOUT_SECONDS = 120
# Checks whose defect is a fact about CI, not about a page: verified from the same run's ci_facts.
CI_CHECKS = frozenset({"flaky", "deploy-stale"})

FIXED = "fixed on main, not deployed"
GONE = "does not reproduce on the live site"
ERROR = "reproduction error"
NOT_IN_CI = "not present in this run's CI facts"

RunScript = Callable[[str, str], int]
RunGroup = Callable[[Group, str], int]


def run_script(script: str, url: str) -> int:
    """Run an agent's reproduction script in isolated mode; 2 on timeout or a path outside repros/."""
    if not SCRIPT_PATTERN.fullmatch(script) or ".." in script:
        return 2
    try:
        result = subprocess.run(
            [sys.executable, "-I", script, "--base-url", url],
            cwd=ROOT,
            timeout=SCRIPT_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return 2
    return result.returncode


def _is_ci(draft: Draft) -> bool:
    groups = draft.reproduction.groups
    return bool(groups) and all(g.get("check") in CI_CHECKS for g in groups)


def _outcome(draft: Draft, url: str, scripts: RunScript, groups: RunGroup) -> int:
    """1 when every part of the reproduction finds the defect at `url`, 0 when one does not, 2 on error."""
    reproduction = draft.reproduction
    try:
        if reproduction.kind == "script":
            assert reproduction.script is not None
            return scripts(reproduction.script, url)
        results = [groups(Group.from_json(data), url) for data in reproduction.groups]
    except (ValueError, KeyError, TypeError):
        return 2
    if any(code not in (0, 1) for code in results):
        return 2
    return 1 if all(code == 1 for code in results) else 0


def _reason(
    draft: Draft,
    live_url: str,
    main_url: str,
    scripts: RunScript,
    groups: RunGroup,
    ci_fingerprints: Collection[str] | None,
) -> str | None:
    """Why the draft does not survive, or None when it does."""
    if _is_ci(draft):
        return None if set(draft.fingerprints) <= set(ci_fingerprints or ()) else NOT_IN_CI
    live = _outcome(draft, live_url, scripts, groups)
    if live == 2:
        return ERROR
    if live == 0:
        return GONE
    main = _outcome(draft, main_url, scripts, groups)
    if main == 2:
        return ERROR
    return FIXED if main == 0 else None


def check(
    drafts: Sequence[Draft],
    live_url: str,
    main_url: str,
    run_script: RunScript,
    run_group: RunGroup,
    ci_fingerprints: Collection[str] | None = None,
) -> tuple[list[Draft], list[Dropped]]:
    """A draft survives only when its reproduction exits 1 on the live site and on main."""
    kept: list[Draft] = []
    dropped: list[Dropped] = []
    for draft in drafts:
        reason = _reason(draft, live_url, main_url, run_script, run_group, ci_fingerprints)
        if reason is None:
            kept.append(draft)
        else:
            dropped.append(Dropped(draft.fingerprints[0], reason))
    return kept, dropped


def build_validated(
    incoming: dict[str, Any],
    live_url: str,
    main_url: str,
    run_script: RunScript,
    run_group: RunGroup,
    ci_fingerprints: Collection[str] | None,
    suggestions_per_run: int,
) -> dict[str, Any]:
    """The validated.json payload: surviving draft dicts, capped suggestions, all dropped."""
    drafts, _, _ = load_drafts(incoming)
    kept, new_dropped = check(drafts, live_url, main_url, run_script, run_group, ci_fingerprints)
    survivors = {id(d) for d in kept}
    return {
        "drafts": [raw for draft, raw in zip(drafts, incoming["drafts"]) if id(draft) in survivors],
        "suggestions": incoming["suggestions"][:suggestions_per_run],
        "dropped": [
            *incoming["dropped"],
            *({"fingerprint": d.fingerprint, "reason": d.reason} for d in new_dropped),
        ],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tools.factory.check_drafts")
    parser.add_argument("--drafts", type=Path, required=True, help="the QA agent's drafts.json")
    parser.add_argument("--live", required=True, help="base URL of the live site")
    parser.add_argument("--main-root", type=Path, required=True, help="checkout of main to build and serve")
    parser.add_argument("--out", type=Path, required=True, help="validated.json to write")
    parser.add_argument("--ci-findings", type=Path, help="findings.json from this run's ci_facts")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = load_config()
    try:
        incoming = json.loads(args.drafts.read_text(encoding="utf-8"))
        load_drafts(incoming)
        ci_fingerprints: set[str] | None = None
        if args.ci_findings is not None:
            facts = json.loads(args.ci_findings.read_text(encoding="utf-8"))
            ci_fingerprints = {g["fingerprint"] for g in facts.get("groups", [])}
        from playwright.sync_api import sync_playwright

        from tools.factory.probe import local_site, reproduce

        with sync_playwright() as playwright, local_site(args.main_root) as main_url:
            result = build_validated(
                incoming,
                args.live,
                main_url,
                run_script,
                lambda group, url: reproduce(playwright, group, url),
                ci_fingerprints,
                config.limits.suggestions_per_run,
            )
    except (OSError, ValueError, KeyError, DraftError) as error:
        print(f"check drafts failed: {error}", file=sys.stderr)
        return 2
    except Exception as error:  # a browser or build failure is infrastructure, never a verdict
        print(f"check drafts failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"{len(result['drafts'])} kept, {len(result['dropped'])} dropped -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
