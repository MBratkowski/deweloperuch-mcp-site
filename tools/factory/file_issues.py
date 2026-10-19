"""File the validated drafts as GitHub issues: dedup, publish evidence, create. Holds the App token."""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import re
import sys
import tempfile
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from tools.factory import evidence as evidence_module
from tools.factory import check_drafts
from tools.factory.ci_facts import deployed_sha, main_sha
from tools.factory.config import Config, load_config
from tools.factory.evidence import EvidenceError, redact
from tools.factory.findings import Group
from tools.factory.github import GitHub, GitHubError
from tools.factory.identity import bot_email
from tools.factory.issues import (
    SENTINEL,
    SEVERITIES,
    Draft,
    DraftError,
    Suggestion,
    Where,
    _check_plain,
    canonical_groups,
    dedup,
    fingerprints_in,
    load_drafts,
    rebuild_groups,
    render_issue,
)

ACCESSIBILITY_CHECKS = frozenset({"axe", "target-size", "focus"})
SUGGESTION_LABEL = "factory:suggestion"
SITE = "site"
_NOT_WORD = re.compile(r"[\W_]+")
DRY_RUN_URL = "(dry run)"
IMAGE_NAMES = ("viewport", "crop")

PublishEvidence = Callable[[Mapping[str, Path]], dict[str, str]]


def _run_id() -> str:
    return os.environ.get("GITHUB_RUN_ID") or "local"


def _emit(title: str, body: str) -> None:
    text = f"# {title}\n\n{body}\n---\n"
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(text)
    else:
        print(text)


def _groups(draft: Draft) -> list[Group]:
    return [Group.from_json(data) for data in draft.reproduction.groups]


def _labels(draft: Draft, groups: Sequence[Group], regression: bool) -> list[str]:
    labels = ["factory:found", draft.severity]
    accessible = any(g.check in ACCESSIBILITY_CHECKS for g in groups)
    labels.append("accessibility" if accessible else "bug")
    if any(g.check == "flaky" for g in groups):
        labels.append("factory:flaky")
    if regression:
        labels.append("regression")
    return labels


def _where(groups: Sequence[Group], config: Config, live_sha: str, main_sha_: str) -> Where:
    pages = list(dict.fromkeys(g.page for g in groups)) or ["see the reproduction script"]
    modes = sorted({g.mode for g in groups}) or ["default"]
    path = config.site.pages.get(pages[0], "") if groups else ""
    return Where(
        pages=pages,
        engines=sorted({e for g in groups for e in g.engines}),
        widths=sorted({w for g in groups for w in g.widths}),
        mode=", ".join(modes),
        live_sha=live_sha,
        main_sha=main_sha_,
        url=config.site.live_url + path,
    )


def _artifact_dirs(probe_out: Path, fingerprint: str) -> Path | None:
    found = sorted(p for p in probe_out.rglob(fingerprint) if p.is_dir() and p.parent.name == "groups")
    return found[0] if found else None


def _script_path(draft: Draft) -> Path | None:
    """The draft's script on disk, resolved against the repo root as check_drafts does."""
    script = draft.reproduction.script
    if script is None:
        return None
    path = check_drafts.ROOT / script
    return path if path.is_file() else None


def _unsafe_script(draft: Draft) -> str | None:
    """Why the draft's script must not be published (a marker or secret-shaped text), or None."""
    path = _script_path(draft)
    if path is None:
        return None
    try:
        _check_plain(path.read_text(encoding="utf-8"), "script")
    except DraftError as error:
        return error.message
    except UnicodeDecodeError:
        return "is not UTF-8 text"
    return None


def _evidence_files(draft: Draft, probe_out: Path, run_id: str) -> dict[str, tuple[str, Path]]:
    """Display name -> (published path, local file) for the images and the script."""
    files: dict[str, tuple[str, Path]] = {}
    many = len(draft.fingerprints) > 1
    for fingerprint in draft.fingerprints:
        folder = _artifact_dirs(probe_out, fingerprint)
        if folder is None:
            continue
        for name in IMAGE_NAMES:
            source = folder / f"{name}.png"
            if source.is_file():
                label = f"{fingerprint}-{name}.png" if many else f"{name}.png"
                files[label] = (f"issues/{fingerprint}/{run_id}/{name}.png", source)
    script = _script_path(draft)
    if script is not None:
        name = (draft.reproduction.script or "").rsplit("/", 1)[-1]
        files[name] = (f"repros/{draft.fingerprints[0]}.py", script)
    return files


def _reproduce(draft: Draft, groups: Sequence[Group], live_url: str) -> tuple[str, str | None]:
    script = draft.reproduction.script
    if script is not None:
        path = _script_path(draft)
        source = path.read_text(encoding="utf-8") if path is not None else None
        return f"uv run python -I {script} --base-url {live_url}", source
    if groups and all(g.check in check_drafts.CI_CHECKS for g in groups):
        lines = ["uv run python -m tools.factory.ci_facts --out ci-out"]
        lines += [f"grep {fp} ci-out/findings.json" for fp in draft.fingerprints]
        lines += [f"# {g.note}" for g in groups if g.note]
        return "\n".join(lines), None
    data = draft.reproduction.groups
    shown = data if len(data) != 1 else data[0]
    command = f"uv run python -m tools.factory.probe --target {live_url} --reproduce group.json"
    return command, json.dumps(shown, indent=2, sort_keys=True)


def _publish(
    files: Mapping[str, tuple[str, Path]], publish_evidence: PublishEvidence, dry_run: bool
) -> dict[str, str]:
    if not files:
        return {}
    if dry_run:
        return {name: DRY_RUN_URL for name in files}
    urls = publish_evidence({path: source for path, source in files.values()})
    return {name: urls[path] for name, (path, _) in files.items()}


def normalised_title(title: str) -> str:
    """The words of a title, case-folded, without punctuation or extra spaces."""
    return " ".join(_NOT_WORD.sub(" ", unicodedata.normalize("NFKC", title).casefold()).split())


def suggestion_fingerprint(page: str, title: str) -> str:
    """A suggestion's fingerprint (D34): a short hash of its page and its normalised title, so the
    same idea seen again, worded with other capitals or punctuation, is the same suggestion."""
    return hashlib.sha256(f"suggestion\0{page}\0{normalised_title(title)}".encode()).hexdigest()[:12]


def suggestion_page(suggestion: Suggestion, config: Config, canonical: Mapping[str, dict[str, Any]]) -> str:
    """The page a suggestion is about, from its screenshot: a probe group's folder `groups/<fp>/`
    names its group's page, a shot or contact sheet `<page>-…png` its page; "site" otherwise."""
    parts = Path(suggestion.screenshot).parts
    for index, part in enumerate(parts[:-1]):
        if part == "groups" and parts[index + 1] in canonical:
            page = canonical[parts[index + 1]].get("page")
            return page if isinstance(page, str) and page in config.site.pages else SITE
    name = parts[-1] if parts else ""
    for page in sorted(config.site.pages, key=len, reverse=True):
        if name.startswith(f"{page}-"):
            return page
    return SITE


def known_suggestion(fingerprint: str, issues: Sequence[dict[str, Any]], trusted: frozenset[str]) -> int | None:
    """The suggestion issue (open or closed, by a trusted author) that already carries `fingerprint`."""
    for issue in issues:
        if "pull_request" in issue or (issue.get("user") or {}).get("login") not in trusted:
            continue
        if SUGGESTION_LABEL not in {label["name"] for label in issue.get("labels", [])}:
            continue
        if fingerprint in fingerprints_in(issue.get("body") or ""):
            return int(issue["number"])
    return None


def render_suggestion(
    suggestion: Suggestion, image_url: str | None, fingerprint: str | None = None
) -> tuple[str, str]:
    """A small body: the problem and, when there is one, a screenshot; then the marker block with the
    suggestion's fingerprint, by which a later run recognises it (D34)."""
    for field in ("title", "problem"):
        _check_plain(getattr(suggestion, field), f"suggestion.{field}")
    if image_url is not None:
        _check_plain(image_url, "suggestion.image")
    parts = [f"## Problem\n\n{suggestion.problem}"]
    if image_url is not None:
        parts.append(f"## Evidence\n\n![screenshot]({image_url})")
    body = "\n\n".join(parts) + "\n"
    if fingerprint is not None:
        body += f"\n{SENTINEL}\n<!-- factory:fp={fingerprint} -->\n"
    return suggestion.title, body


def _suggestion_image(suggestion: Suggestion, probe_out: Path) -> Path | None:
    """The screenshot only when it is a .png that resolves inside probe_out."""
    root = probe_out.resolve()
    candidate = Path(suggestion.screenshot)
    for path in (candidate, probe_out / candidate) if candidate.is_absolute() else (probe_out / candidate,):
        resolved = path.resolve()
        if resolved.suffix == ".png" and resolved.is_file() and resolved.is_relative_to(root):
            return resolved
    return None


def file(
    validated: dict[str, Any],
    probe_out: Path,
    gh: GitHub,
    config: Config,
    trusted: frozenset[str],
    dry_run: bool,
    publish_evidence: PublishEvidence,
) -> list[int]:
    """Create the issues; returns their numbers (none in a dry run).

    validated.json comes from a job that ran the agent's scripts, so each detector draft gets its
    groups again from this run's findings under `probe_out`; the groups in the file are ignored.
    """
    load_drafts(validated)
    canonical = canonical_groups(*sorted(probe_out.rglob("findings.json")))
    raw_drafts, unknown = rebuild_groups(validated["drafts"], canonical)
    for skipped in unknown:
        print(f"warning: skipping {skipped.fingerprint}: {skipped.reason}", file=sys.stderr)
    drafts, suggestions, _ = load_drafts({**validated, "drafts": raw_drafts})
    repo = config.site.repo
    path = f"/repos/{repo}/issues"
    issues = gh.paginate(path, {"state": "all"})
    live_sha, main_sha_ = deployed_sha(gh), main_sha(gh)
    ordered = sorted(drafts, key=lambda d: SEVERITIES.index(d.severity))
    run_id = _run_id()
    numbers: list[int] = []
    filed: set[str] = set()
    count = 0

    for draft in ordered:
        if count >= config.limits.issues_per_run:
            break
        if draft.reproduction.script is not None and _script_path(draft) is None:
            print(
                f"warning: skipping {draft.fingerprints[0]}: {draft.reproduction.script} is missing",
                file=sys.stderr,
            )
            continue
        # Checked before anything is published: the evidence branch is public.
        unsafe = _unsafe_script(draft)
        if unsafe is not None:
            print(
                f"warning: skipping {draft.fingerprints[0]}: {draft.reproduction.script} {unsafe}; not published",
                file=sys.stderr,
            )
            continue
        fingerprints = set(draft.fingerprints)
        if fingerprints & filed:
            continue
        verdict = dedup(fingerprints, issues, trusted)
        if verdict.decision == "skip":
            continue
        regression = verdict.decision == "regression"
        if regression:
            line = f"Regression of #{verdict.related}."
            draft = dataclasses.replace(draft, problem=f"{line}\n\n{draft.problem}")
        groups = _groups(draft)
        files = _evidence_files(draft, probe_out, run_id)
        urls = _publish(files, publish_evidence, dry_run)
        where = _where(groups, config, live_sha, main_sha_)
        command, source = _reproduce(draft, groups, config.site.live_url)
        title, body = render_issue(draft, where, urls, command, source)
        count += 1
        filed |= fingerprints
        if dry_run:
            _emit(title, body)
            continue
        labels = _labels(draft, groups, regression)
        created = gh.request("POST", path, {"title": title, "body": body, "labels": labels})
        numbers.append(created["number"])

    suggested = 0
    for suggestion in suggestions:
        if suggested >= config.limits.suggestions_per_run:
            break
        fingerprint = suggestion_fingerprint(suggestion_page(suggestion, config, canonical), suggestion.title)
        # D34: the same suggestion, open or closed, is never filed twice.
        known = known_suggestion(fingerprint, issues, trusted)
        if known is not None or fingerprint in filed:
            print(f"suggestion {fingerprint} is already #{known or 'filed in this run'}; skipped", file=sys.stderr)
            continue
        image = _suggestion_image(suggestion, probe_out)
        urls = {}
        if image is not None:
            published = f"suggestions/{run_id}/{image.name}"
            urls = _publish({"screenshot": (published, image)}, publish_evidence, dry_run)
        title, body = render_suggestion(suggestion, urls.get("screenshot"), fingerprint)
        suggested += 1
        filed.add(fingerprint)
        if dry_run:
            _emit(title, body)
            continue
        created = gh.request(
            "POST", path, {"title": title, "body": body, "labels": [SUGGESTION_LABEL]}
        )
        numbers.append(created["number"])
    return numbers


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tools.factory.file_issues")
    parser.add_argument("--validated", type=Path, required=True, help="validated.json from check_drafts")
    parser.add_argument(
        "--probe-out", type=Path, required=True,
        help="probe artifact root: groups/<fp>/ evidence and this run's findings.json files",
    )
    parser.add_argument("--dry-run", action="store_true", help="print the issues, change nothing")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = load_config()
    token = os.environ.get("GH_TOKEN", "")
    repo = config.site.repo
    trusted = config.trusted(os.environ.get("FACTORY_APP_SLUG"))
    email = ""

    def publish_evidence(files: Mapping[str, Path]) -> dict[str, str]:
        with tempfile.TemporaryDirectory() as workdir:
            return evidence_module.publish(
                f"https://x-access-token:{token}@github.com/{repo}.git",
                files,
                f"Evidence for run {_run_id()}",
                f"https://raw.githubusercontent.com/{repo}",
                Path(workdir),
                email=email,
            )

    try:
        validated = json.loads(args.validated.read_text(encoding="utf-8"))
        gh = GitHub(repo, token or None)
        if not args.dry_run:  # a real run commits evidence: only under the App bot's own address
            email = bot_email(gh)
        numbers = file(validated, args.probe_out, gh, config, trusted, args.dry_run, publish_evidence)
    except (GitHubError, EvidenceError) as error:
        print(f"file issues failed: {redact(str(error))}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as error:
        print(f"file issues failed: {redact(str(error))}", file=sys.stderr)
        return 2
    print(" ".join(f"#{n}" for n in numbers) if numbers else "no issues created")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
