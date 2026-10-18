"""Re-run every draft's reproduction on the live site and on a build of main; no write token."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable, Collection, Mapping, Sequence
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
UNKNOWN = "unknown fingerprint"

RunScript = Callable[[str, str], int]
RunGroup = Callable[[Group, str], int]


REPRODUCED = re.compile(r"^REPRODUCED: .+$", re.MULTILINE)
NOT_REPRODUCED = re.compile(r"^NOT REPRODUCED$", re.MULTILINE)


def run_script(script: str, url: str) -> int:
    """Run an agent's reproduction script in isolated mode.

    1 only for exit 1 plus a `REPRODUCED:` line, 0 only for exit 0 plus `NOT REPRODUCED`;
    2 for anything else (other codes, a traceback, a missing line, a timeout, a bad path).
    """
    if not SCRIPT_PATTERN.fullmatch(script) or ".." in script:
        return 2
    try:
        result = subprocess.run(
            [sys.executable, "-I", script, "--base-url", url],
            cwd=ROOT,
            timeout=SCRIPT_TIMEOUT_SECONDS,
            check=False,
            capture_output=True,
            text=True,
        )
    except (subprocess.TimeoutExpired, OSError):
        return 2
    stdout, stderr = result.stdout or "", result.stderr or ""
    if "Traceback (most recent call last)" in stderr:
        return 2
    if result.returncode == 1 and REPRODUCED.search(stdout) and not NOT_REPRODUCED.search(stdout):
        return 1
    if result.returncode == 0 and NOT_REPRODUCED.search(stdout) and not REPRODUCED.search(stdout):
        return 0
    return 2


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


def canonical_groups(*findings: Path | None) -> dict[str, dict[str, Any]]:
    """This run's groups by fingerprint, from findings.json files (the live probe's, the CI facts)."""
    groups: dict[str, dict[str, Any]] = {}
    for path in findings:
        if path is not None:
            for group in json.loads(path.read_text(encoding="utf-8")).get("groups", []):
                groups[group["fingerprint"]] = group
    return groups


def rebuild_groups(
    raw_drafts: Sequence[dict[str, Any]], canonical: Mapping[str, dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[Dropped]]:
    """Detector drafts get this run's groups for their fingerprints, never the agent's copies.

    A fingerprint that is in neither findings file drops the draft. Script drafts carry no groups.
    """
    rebuilt: list[dict[str, Any]] = []
    dropped: list[Dropped] = []
    for raw in raw_drafts:
        reproduction = raw["reproduction"]
        if reproduction["kind"] == "script":
            rebuilt.append(raw)
            continue
        unknown = [fp for fp in raw["fingerprints"] if fp not in canonical]
        if unknown:
            dropped.append(Dropped(unknown[0], UNKNOWN))
            continue
        groups = [canonical[fp] for fp in dict.fromkeys(raw["fingerprints"])]
        rebuilt.append({**raw, "reproduction": {**reproduction, "groups": groups}})
    return rebuilt, dropped


def build_validated(
    incoming: dict[str, Any],
    live_url: str,
    main_url: str,
    run_script: RunScript,
    run_group: RunGroup,
    ci_fingerprints: Collection[str] | None,
    suggestions_per_run: int,
    *,
    canonical: Mapping[str, dict[str, Any]],
) -> dict[str, Any]:
    """The validated.json payload: surviving draft dicts, capped suggestions, all dropped.

    Only the probe's own groups reach it: the agent's group objects are data it could have edited.
    """
    load_drafts(incoming)
    raw_drafts, unknown = rebuild_groups(incoming["drafts"], canonical)
    drafts, _, _ = load_drafts({**incoming, "drafts": raw_drafts})
    kept, new_dropped = check(drafts, live_url, main_url, run_script, run_group, ci_fingerprints)
    survivors = {id(d) for d in kept}
    return {
        "drafts": [raw for draft, raw in zip(drafts, raw_drafts) if id(draft) in survivors],
        "suggestions": incoming["suggestions"][:suggestions_per_run],
        "dropped": [
            *incoming["dropped"],
            *({"fingerprint": d.fingerprint, "reason": d.reason} for d in [*unknown, *new_dropped]),
        ],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tools.factory.check_drafts")
    parser.add_argument("--drafts", type=Path, required=True, help="the QA agent's drafts.json")
    parser.add_argument("--live", required=True, help="base URL of the live site")
    parser.add_argument("--main-root", type=Path, required=True, help="checkout of main to build and serve")
    parser.add_argument("--out", type=Path, required=True, help="validated.json to write")
    parser.add_argument(
        "--probe-findings", type=Path, required=True, help="findings.json of this run's live probe"
    )
    parser.add_argument("--ci-findings", type=Path, help="findings.json from this run's ci_facts")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = load_config()
    try:
        incoming = json.loads(args.drafts.read_text(encoding="utf-8"))
        load_drafts(incoming)
        canonical = canonical_groups(args.probe_findings, args.ci_findings)
        ci_fingerprints = set(canonical_groups(args.ci_findings)) if args.ci_findings else None
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
                canonical=canonical,
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
