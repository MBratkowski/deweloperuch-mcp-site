# Agent factory runbook

Spec: `docs/superpowers/specs/2026-10-18-agent-factory-design.md`. This file is extended phase by phase; it covers phase 0, the one-time repository setup done by the owner, and the first dry run of the QA workflow.

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

## First dry run

`factory-qa.yml` runs nightly at 02:30 UTC, after every successful Pages run on `main`, and by hand. Its jobs: `guard` (mode and configuration) → `probe` (the live site and the CI facts) → `verify` → `verify-act`, and `triage` (the QA agent writes drafts and reproduction scripts) → `check` (runs the scripts and the detector reproductions on the live site and on `main`, without secrets) → `file` → `dispatch-status`. A dry run skips `verify-act` and `dispatch-status`, and `file` writes the issue bodies to its job summary instead of GitHub, so it writes nothing to GitHub.

1. With phase 0 done and the workflow on `main`, set the mode: `gh variable set FACTORY_MODE --body shadow`.
2. Dispatch: `gh workflow run factory-qa.yml --ref main -f dry_run=true`, then `gh run watch`.
3. Check the run:
   - `probe` is green. A red probe means the live site did not load completely; nothing after it runs.
   - The `check` job summary ("QA triage") lists the drafts that reproduced on the live site and on `main`, the suggestion, and every dropped finding with its reason. Read the reasons and the drafts for false alarms before the first real run.
   - The `file` job summary shows the issue bodies a real run would file: a `deploy-stale` body (`1f104d1` against `8b6ec7b`, unless Pages has deployed `main` since) and a flaky body for the docs history test.
   - The `drafts` artifact holds the agent's output (`drafts.json`) and its scripts; `validated` holds what survived the check.
4. If the QA agent step fails inside the Playwright container (the spec's container risk):
   - The `triage` job already sets `HOME: /root` for every step, as `validate.yml` does for Firefox. That is the first fallback.
   - If the action still fails, typically while installing Bun, install Bun in a step before it and pass the path. 1.3.14 is the version the pinned action installs; Node.js is in the image.

     ```yaml
     - name: Install Bun
       id: bun
       run: |
         npm install --global bun@1.3.14
         echo "path=$(command -v bun)" >> "$GITHUB_OUTPUT"
     ```

     Then add `path_to_bun_executable: ${{ steps.bun.outputs.path }}` to the action's `with:`.
   - The agent reads only the workspace: `settings` turns on `permissions.blockReadsOutsideWorkingDirectories` (Claude Code 2.1.257 and later), and `--disallowedTools` denies the places a token can sit (`/proc`, `/sys`, `/etc`, `/root`, `/home`, `/github`, `/tmp`, the home directory, `.git`) and git itself. The step before it deletes `.git`, so the action cannot store its token in `.git/config`. If the agent reports refused reads of its own inputs, check that they are under the workspace (`factory-in/`, `factory-out/`, the repository).
   - The agent step sets `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1`. If the agent's probe or fingerprint commands then fail with a sandbox error, install the isolation tools the action installs for that mode in a step before it (`apt-get update && apt-get install -y --no-install-recommends bubblewrap socat`; the container runs as root, without sudo).
   - Git "dubious ownership" messages from the action are expected: the checkout belongs to the runner's user, not to root in the container, and the agent needs no git. Only if they stop the action, add `git config --global --add safe.directory "$GITHUB_WORKSPACE"` in a step before it, as the `verify` job does.
   - Record here what was needed: the fixer and reviewer jobs run the same action in the same image.
5. "--json-schema was provided but Claude did not return structured_output" means the agent ended without output that matches `.github/factory/schemas/drafts.json` (for example out of turns); the run's log and `max_turns.qa` in `config.json` are the places to look.

## Known residual risk

- The pinned `claude-code-action` (v1.0.241) installs Claude Code 2.1.289. The permission check that makes environment-printing forms of `ps` prompt arrives in 2.1.290, so until then the QA agent's `--disallowedTools` denies `ps`, `env`, `printenv`, `set`, `declare`, `export` and `compgen` outright. Re-pin `claude-code-action` to a release that bundles Claude Code 2.1.290 or later once that release is at least two weeks old, and keep the denies.
- The publish-side secret checks (draft text, the groups marker, reproduction scripts, the `drafts` and `validated` artifacts) stop only verbatim tokens and keys. A token the agent encodes first would pass them; the read block and the denies are what keep tokens out of its reach.
- The `check` job runs the agent's scripts as root in the container, and its later steps (the summary, the scan, the upload) run after them. They are best effort against a script that tampers with the job. The job holds no secret and a read-only token, and `file` validates `validated.json` again before filing.

## Known limitation

Sync pull requests are opened with `GITHUB_TOKEN`, so they still need the owner's admin bypass to merge; the factory does not merge them.
