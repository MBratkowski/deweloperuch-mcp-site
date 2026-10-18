# Agent factory runbook

Spec: `docs/superpowers/specs/2026-10-18-agent-factory-design.md`. This file is extended phase by phase; this part covers phase 0, the one-time repository setup done by the owner.

Every setup subcommand is a dry run unless you pass `--apply`. Run them from the repository root with `GH_TOKEN` set to a token that may administer the repository (for example `export GH_TOKEN=$(gh auth token)`).

## Phase 0: repository setup

1. **Create the GitHub App.**
   - `python3 -m tools.factory.setup_repo app-form` writes `~/.config/deweloperuch-factory/app-form.html` from `.github/factory/app-manifest.json`.
   - Open the file in a browser and submit it. GitHub asks you to confirm the App name, then redirects to the repository with `?code=...` in the URL. Copy the code (it expires after one hour).
   - `python3 -m tools.factory.setup_repo app-convert CODE` exchanges the code for the App credentials. It prints the App id, slug and client id, writes the private key to `~/.config/deweloperuch-factory/<slug>.pem` (mode 600) and prints the `gh secret set` and `gh variable set` commands for the next steps. It never prints the key.
   - The manifest deliberately has no `workflows` permission, so GitHub rejects any push from the App that touches `.github/workflows/`.
2. **Install the App on this repository only** (App settings, Install App, Only select repositories).
3. **Store the secrets in the `factory` environment.**
   - Create the environment first: `python3 -m tools.factory.setup_repo environment --apply` (custom deployment branch policy, `main` only).
   - `gh secret set FACTORY_APP_PRIVATE_KEY --env factory < ~/.config/deweloperuch-factory/<slug>.pem`
   - `claude setup-token`, then `gh secret set CLAUDE_CODE_OAUTH_TOKEN --env factory` and paste the token.
4. **Set the variables:** `python3 -m tools.factory.setup_repo variables --client-id <client_id> --slug <slug> --apply`. This sets `FACTORY_MODE=off`, `FACTORY_APP_CLIENT_ID` and `FACTORY_APP_SLUG`.
5. **Create the labels:** `python3 -m tools.factory.setup_repo labels --apply`.
6. **Review the ruleset, then apply it.**
   - `python3 -m tools.factory.setup_repo ruleset` prints the ruleset `main (factory)` it would create, with the required check names read from the latest successful Check run on `main`.
   - Run `ruleset --apply` only after you approve the printed ruleset. Repository admins keep the bypass, so you can still merge your own work.
7. **Confirm the reviewer approval counts (spec risk).** Open a test pull request and let `github-actions[bot]` approve it. Check that the approval satisfies the "1 approval" rule. If it does not, the fallback is a second GitHub App used only as the reviewer.

## Known limitation

Sync pull requests are opened with `GITHUB_TOKEN`, so they still need the owner's admin bypass to merge; the factory does not merge them.
