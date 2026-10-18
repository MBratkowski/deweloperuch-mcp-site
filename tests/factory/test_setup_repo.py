import json
from pathlib import Path

from tests.factory.fakes import FakeGitHub
from tools.factory import setup_repo
from tools.factory.setup_repo import LABELS, manifest_form, required_check_names, ruleset_payload

MANIFEST = Path(__file__).resolve().parents[2] / ".github" / "factory" / "app-manifest.json"
CHECK = "Validate this commit / Tests (pytest, Playwright)"

EXPECTED_LABELS = {
    "factory:found", "factory:suggestion", "factory:ready", "factory:fixing",
    "factory:merged", "factory:regressed", "factory:flaky", "factory:needs-human",
    "factory", "factory:merge-ready", "factory:revert",
    "S1", "S2", "S3", "S4", "regression",
}


def test_labels_cover_the_spec():
    assert set(LABELS) == EXPECTED_LABELS
    for colour, description in LABELS.values():
        assert len(colour) == 6 and description


def test_ruleset_payload():
    payload = ruleset_payload([CHECK])
    assert payload["name"] == "main (factory)"
    assert payload["target"] == "branch"
    assert payload["enforcement"] == "active"
    assert payload["conditions"]["ref_name"]["include"] == ["~DEFAULT_BRANCH"]
    assert payload["bypass_actors"] == [
        {"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}
    ]
    rules = {rule["type"]: rule for rule in payload["rules"]}
    pull = rules["pull_request"]["parameters"]
    assert pull["required_approving_review_count"] == 1
    assert pull["dismiss_stale_reviews_on_push"] is True
    checks = rules["required_status_checks"]["parameters"]
    assert checks["strict_required_status_checks_policy"] is True
    assert checks["required_status_checks"] == [{"context": CHECK, "integration_id": 15368}]
    assert "non_fast_forward" in rules and "deletion" in rules


def test_manifest_has_no_workflows_permission():
    manifest = json.loads(MANIFEST.read_text())
    assert manifest["default_permissions"] == {
        "contents": "write", "pull_requests": "write", "issues": "write",
        "actions": "read", "checks": "read", "deployments": "read", "metadata": "read",
    }
    assert manifest["hook_attributes"] == {"active": False}
    assert manifest["default_events"] == []


def test_manifest_form_posts_escaped_json():
    html = manifest_form({"name": 'a "quoted" <app>'})
    assert 'action="https://github.com/settings/apps/new"' in html
    assert 'method="post"' in html
    assert 'name="manifest"' in html
    assert "&quot;name&quot;" in html
    assert '"name"' not in html
    assert "<app>" not in html


def test_required_check_names_reads_the_check_workflow():
    repo = "MBratkowski/deweloperuch-mcp-site"
    gh = FakeGitHub({
        ("GET", f"/repos/{repo}/actions/runs"): {"workflow_runs": [
            {"id": 1, "name": "Check", "conclusion": "success"},
            {"id": 2, "name": "Pages", "conclusion": "success"},
            {"id": 3, "name": "Sync", "conclusion": "success"},
        ]},
        ("GET", f"/repos/{repo}/actions/runs/1/jobs"): {"jobs": [
            {"name": "Validate this commit / Visual baselines (pinned Linux Chromium)"},
            {"name": CHECK},
        ]},
        ("GET", f"/repos/{repo}/actions/runs/2/jobs"): {"jobs": [{"name": "Deploy the site files"}]},
    })
    gh.repo = repo
    assert required_check_names(gh, "abc") == sorted([
        CHECK, "Validate this commit / Visual baselines (pinned Linux Chromium)"
    ])
    assert all(method == "GET" for method, _, _ in gh.calls)
    assert not any("/jobs" in path and "/2/" in path for _, path, _ in gh.calls)


def test_app_convert_pem_goes_under_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    path = setup_repo.write_pem("slug", "SECRET")
    assert path == tmp_path / ".config" / "deweloperuch-factory" / "slug.pem"
    assert path.read_text() == "SECRET"
    assert path.stat().st_mode & 0o777 == 0o600
