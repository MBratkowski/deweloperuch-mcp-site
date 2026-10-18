"""One-time repository setup for the factory (runbook phase 0), run by the owner by hand.

Without --apply every subcommand prints what it would change and writes nothing to GitHub.
"""

import argparse
import html
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from tools.factory.config import load_config
from tools.factory.github import GitHub, GitHubError

ENVIRONMENT = "factory"
RULESET_NAME = "main (factory)"
MANIFEST_PATH = Path(__file__).resolve().parents[2] / ".github" / "factory" / "app-manifest.json"
GITHUB_ACTIONS_APP_ID = 15368
ADMIN_ROLE_ID = 5

LABELS: dict[str, tuple[str, str]] = {
    "factory": ("0e8a16", "Opened or touched by the factory"),
    "factory:found": ("d93f0b", "Filed by QA with a failing reproduction; waiting for a fixer"),
    "factory:suggestion": ("fbca04", "AI review finding without a reproduction; never fixed automatically"),
    "factory:ready": ("0e8a16", "Handed to the fixer; honoured only when set by the owner or the App"),
    "factory:fixing": ("1d76db", "A fixer attempt is in progress"),
    "factory:merge-ready": ("5319e7", "Reviewed and waiting for the merger"),
    "factory:merged": ("6f42c1", "Fixed and merged by the factory"),
    "factory:regressed": ("b60205", "A merged fix stopped holding on main"),
    "factory:flaky": ("c5def5", "Reproduces only intermittently"),
    "factory:needs-human": ("e99695", "The factory gave up; the owner must decide"),
    "factory:revert": ("b60205", "Reverts a factory merge"),
    "S1": ("b60205", "Severity 1: the page is broken for most visitors"),
    "S2": ("d93f0b", "Severity 2: a major defect in a common case"),
    "S3": ("fbca04", "Severity 3: a minor defect"),
    "S4": ("c2e0c6", "Severity 4: cosmetic"),
    "regression": ("b60205", "Worked before and no longer does"),
}


def ruleset_payload(check_names: Sequence[str]) -> dict:
    return {
        "name": RULESET_NAME,
        "target": "branch",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
        "bypass_actors": [
            {"actor_id": ADMIN_ROLE_ID, "actor_type": "RepositoryRole", "bypass_mode": "always"}
        ],
        "rules": [
            {
                "type": "pull_request",
                "parameters": {
                    "required_approving_review_count": 1,
                    "dismiss_stale_reviews_on_push": True,
                    "require_code_owner_review": False,
                    "require_last_push_approval": False,
                    "required_review_thread_resolution": False,
                },
            },
            {
                "type": "required_status_checks",
                "parameters": {
                    "strict_required_status_checks_policy": True,
                    "required_status_checks": [
                        {"context": name, "integration_id": GITHUB_ACTIONS_APP_ID}
                        for name in check_names
                    ],
                },
            },
            {"type": "non_fast_forward"},
            {"type": "deletion"},
        ],
    }


def manifest_form(manifest: dict) -> str:
    value = html.escape(json.dumps(manifest), quote=True)
    return (
        "<!doctype html>\n<meta charset=\"utf-8\">\n<title>Create the factory App</title>\n"
        '<form action="https://github.com/settings/apps/new" method="post">\n'
        f'  <input type="hidden" name="manifest" value="{value}">\n'
        '  <button type="submit">Create the GitHub App</button>\n'
        "</form>\n"
    )


def required_check_names(gh: GitHub, sha: str) -> list[str]:
    """Job names of the Check workflow run at `sha` (Pages and Sync runs are ignored)."""
    runs = gh.request("GET", f"/repos/{gh.repo}/actions/runs", params={"head_sha": sha, "per_page": 100})
    names: set[str] = set()
    for run in runs["workflow_runs"]:
        if run["name"] != "Check":
            continue
        jobs = gh.request("GET", f"/repos/{gh.repo}/actions/runs/{run['id']}/jobs", params={"per_page": 100})
        names.update(job["name"] for job in jobs["jobs"])
    return sorted(names)


def write_pem(slug: str, pem: str) -> Path:
    directory = Path.home() / ".config" / "deweloperuch-factory"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{slug}.pem"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(pem)
    path.chmod(0o600)
    return path


def _client(repo: str) -> GitHub:
    return GitHub(repo)


def cmd_labels(gh: GitHub, apply: bool) -> None:
    existing = {label["name"] for label in gh.paginate(f"/repos/{gh.repo}/labels", {"per_page": 100})}
    for name, (colour, description) in LABELS.items():
        body = {"color": colour, "description": description}
        if name in existing:
            print(f"update label {name}")
            if apply:
                gh.request("PATCH", f"/repos/{gh.repo}/labels/{name}", body)
        else:
            print(f"create label {name}")
            if apply:
                gh.request("POST", f"/repos/{gh.repo}/labels", {"name": name, **body})


def cmd_app_form(output: Path) -> None:
    manifest = json.loads(MANIFEST_PATH.read_text())
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(manifest_form(manifest))
    print(f"wrote {output}; open it, submit, and copy the code from the redirect URL")


def cmd_app_convert(code: str) -> None:
    result = GitHub("-").request("POST", f"/app-manifests/{code}/conversions")
    slug = result["slug"]
    path = write_pem(slug, result["pem"])
    repo = load_config().site.repo
    print(f"id: {result['id']}\nslug: {slug}\nclient_id: {result['client_id']}")
    print(f"private key written to {path} (mode 600)")
    print("next, run:")
    print(f"  gh secret set FACTORY_APP_PRIVATE_KEY --env {ENVIRONMENT} --repo {repo} < {path}")
    print(f"  gh variable set FACTORY_APP_CLIENT_ID --repo {repo} --body {result['client_id']}")
    print(f"  gh variable set FACTORY_APP_SLUG --repo {repo} --body {slug}")


def cmd_environment(gh: GitHub, apply: bool) -> None:
    print(f"create environment {ENVIRONMENT} with a custom branch policy allowing only main")
    if not apply:
        return
    base = f"/repos/{gh.repo}/environments/{ENVIRONMENT}"
    gh.request("PUT", base, {"deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True}})
    policies = gh.request("GET", f"{base}/deployment-branch-policies")
    if not any(p["name"] == "main" for p in (policies or {}).get("branch_policies", [])):
        gh.request("POST", f"{base}/deployment-branch-policies", {"name": "main", "type": "branch"})


def cmd_variables(gh: GitHub, apply: bool, client_id: str | None, slug: str | None) -> None:
    values = {"FACTORY_MODE": "off", "FACTORY_APP_CLIENT_ID": client_id, "FACTORY_APP_SLUG": slug}
    for name, value in values.items():
        if value is None:
            print(f"skip {name}: pass --client-id and --slug (printed by app-convert)")
            continue
        print(f"set variable {name}={value}")
        if not apply:
            continue
        path = f"/repos/{gh.repo}/actions/variables"
        try:
            gh.request("PATCH", f"{path}/{name}", {"name": name, "value": value})
        except GitHubError as error:
            if error.status != 404:
                raise
            gh.request("POST", path, {"name": name, "value": value})


def cmd_ruleset(gh: GitHub, apply: bool) -> None:
    runs = gh.request(
        "GET",
        f"/repos/{gh.repo}/actions/workflows/check.yml/runs",
        params={"branch": "main", "status": "success", "per_page": 1},
    )
    if not runs["workflow_runs"]:
        sys.exit("no successful Check run on main to read the check names from")
    sha = runs["workflow_runs"][0]["head_sha"]
    names = required_check_names(gh, sha)
    payload = ruleset_payload(names)
    print(f"required checks read from {sha}:")
    print(json.dumps(payload, indent=2))
    if not apply:
        return
    existing = [r for r in gh.request("GET", f"/repos/{gh.repo}/rulesets") or [] if r["name"] == RULESET_NAME]
    if existing:
        gh.request("PUT", f"/repos/{gh.repo}/rulesets/{existing[0]['id']}", payload)
    else:
        gh.request("POST", f"/repos/{gh.repo}/rulesets", payload)
    print("ruleset applied")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.factory.setup_repo")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("labels", "environment", "variables", "ruleset"):
        p = sub.add_parser(name)
        p.add_argument("--apply", action="store_true", help="make the changes; default is a dry run")
        if name == "variables":
            p.add_argument("--client-id")
            p.add_argument("--slug")
    form = sub.add_parser("app-form")
    form.add_argument("--output", type=Path, default=Path.home() / ".config" / "deweloperuch-factory" / "app-form.html")
    convert = sub.add_parser("app-convert")
    convert.add_argument("code")
    args = parser.parse_args(argv)
    try:
        if args.command == "app-form":
            cmd_app_form(args.output)
        elif args.command == "app-convert":
            cmd_app_convert(args.code)
        else:
            gh = _client(load_config().site.repo)
            if args.command == "labels":
                cmd_labels(gh, args.apply)
            elif args.command == "environment":
                cmd_environment(gh, args.apply)
            elif args.command == "variables":
                cmd_variables(gh, args.apply, args.client_id, args.slug)
            else:
                cmd_ruleset(gh, args.apply)
    except GitHubError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
