# Agent factory runbook

Spec: `docs/superpowers/specs/2026-10-18-agent-factory-design.md`. This file is extended phase by phase; it covers phase 0, the one-time repository setup done by the owner, the first dry run of the QA workflow, the fixer and the reviewer.

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
   - Fixer dry run: check whether the action writes its `github_token` into `.git/config` (the fixer keeps `.git` to commit). Add a temporary step right after the agent step that prints only a count, never the value: `grep -ciE 'extraheader|x-access-token' .git/config || true`. A count above 0 means the read-only `GITHUB_TOKEN` sits where the agent's git commands can read it; record it under "Known residual risk".
   - If `prove` reports "uncommitted changes" that are files the action or Claude Code left in the workspace rather than the fixer's work, add their directory to the exclude list in `tools/factory/fix.py` (`prepare`, `_exclude`).
5. "--json-schema was provided but Claude did not return structured_output" means the agent ended without output that matches `.github/factory/schemas/drafts.json` (for example out of turns); the run's log and `max_turns.qa` in `config.json` are the places to look.

## The fixer

`factory-fix.yml` runs when the owner or the App labels an issue `factory:ready`, and on `workflow_dispatch` (`issue`, `attempt`) for a later attempt. Its jobs: `guard` (the mode, who set the label, the attempt within `limits.attempts_per_issue`) → `fix` (the Playwright image: `fix prepare` checks out `fix/<N>-<slug>`, or the open pull request's branch on a later attempt, and writes `factory-out/context.md`; the agent writes a failing test and the fix and commits them; `fix prove` refuses uncommitted changes, runs the new tests on a worktree of the base, where they must fail, and on a worktree of the head, where they must pass, then the full suite on the head; `fix before-after` renders the issue's cells on both commits; the commits leave as a git bundle) → `publish` (a fresh checkout of `main` with the App token: fetches the bundle, builds one commit of its own from the fix's tree, on the base or on the pull request's head, with `deweloperuch-factory` as author and a message rendered from the report, runs the fence and the secret checks on that commit, pushes it, opens or updates the pull request labelled `factory`, publishes the images under `prs/<number>/<sha>/` on the evidence branch and moves the issue from `factory:ready` to `factory:fixing`). The agent's own commits, authors and messages never leave the job.

- Every attempt leaves a comment with `<!-- factory:attempt=K -->` on the issue; the guard counts only those by the owner or the App. When a step after the push fails (the pull request, the labels, the images), publish still leaves that comment, naming the pushed commit, before the job fails.
- A failed attempt (the fix job failed, a hard fence reason, a proof that is not red then green) dispatches the next attempt with `GITHUB_TOKEN`, which the publish job holds with `actions: write` for that alone: the App has `actions: read`, and no reviewer exists before a pull request does. After the last attempt, when the fixer reports `not_reproducible` or `needs_human`, when its new test passes on the base, when a flaky test never fails on the base, or when anything carries secret-shaped text (in the fix job's own scan too), the issue gets `factory:needs-human` and an open pull request goes back to draft with a summary.
- Report text never closes or links an issue (`Fixes #N`, issue URLs) and mentions nobody; the pull request says `Refs #N`.
- Soft fence reasons (existing tests changed, test code that needs a person, a baseline changed without a stated reason) still open the pull request, labelled `factory:needs-human` and listed under "Needs a human".
- To stop the factory on one issue, remove `factory:ready` and `factory:fixing` (or add `factory:needs-human`): the guard and the publish job then leave it alone.

## The reviewer

`factory-review.yml` runs when Check completes (`workflow_run`), and by hand with `gh workflow run factory-review.yml --ref main -f pr=<number>`, which routes again on Check's latest pull request run of the head. Its jobs: `route` (read-only: ignore unless the pull request is the App's, from this repository, labelled `factory`, open and not a draft, its head is the commit Check ran on and its body's `factory:head` marker names that head) → `act` when Check failed (the fixer's next attempt with `GITHUB_TOKEN`, or after the last attempt `factory:needs-human` and the pull request back to draft), or → `evidence` → `review` → `post` when Check passed.

- `evidence` (the Playwright image, no environment, `contents: read`, no token in any step) works in two phases, each uploaded before the next starts. The decisive phase runs no Python from the pull request: the fence and the copy guard from `main`'s code; the base commit's `tests/e2e` and `tests/visual` in their own pytest sessions, on a worktree of the base and on a worktree of the base with only the head's site files and baselines (every test that passed on the base must pass against the head site); the issue's reproduction on a local build of the head (it must be gone); the probe on the head and the base around the issue's page (no new finding). Only then the proof phase runs the head's own new tests: red on the base, green on the head, or 50 throttled runs for a flaky test. A revert gets the exact-revert check only.
- `review` (environment `factory`, a read-only `GITHUB_TOKEN`) runs the reviewer agent with `reviewer.md`: it reads the diff with `git diff`, `git show` and `git log`, the evidence and Check's `visual-results`, edits nothing, and returns a verdict (`schemas/verdict.json`).
- `post` holds the only write scopes of a review (`pull-requests`, `issues`, `actions`) and runs nothing but `review post`: it routes again, re-reads the evidence, checks the verdict's text (no hidden marker, closing keyword, issue link or secret shape; mentions neutralised), posts the review as `github-actions[bot]` for the head the evidence tested, and dispatches `factory-merge.yml` after an approval or the fixer's next attempt after requested changes. It refuses an approval when the evidence is not green, a checklist item is false or the verdict names another commit, and then hands the issue to a person.
- The evidence and the verdict reach later jobs as artifacts downloaded by id, so an artifact uploaded later under the same name cannot replace them.
- Dry run: dispatch the review of a factory pull request by hand and read the `evidence` job's log (the decisive reasons, the two test sessions under `sessions/`) before trusting a verdict. Check whether `actions/download-artifact` nests the by-id downloads in a directory named after the artifact; `post` accepts both layouts, the prompt's paths assume the flat one (`merge-multiple: true`).

## Known residual risk

- The pinned `claude-code-action` (v1.0.241) installs Claude Code 2.1.289. The permission check that makes environment-printing forms of `ps` prompt arrives in 2.1.290, so until then the QA agent's `--disallowedTools` denies `ps`, `env`, `printenv`, `set`, `declare`, `export` and `compgen` outright. Re-pin `claude-code-action` to a release that bundles Claude Code 2.1.290 or later once that release is at least two weeks old, and keep the denies.
- The publish-side secret checks (draft text, the groups marker, reproduction scripts, the `drafts` and `validated` artifacts) stop only verbatim tokens and keys. A token the agent encodes first would pass them; the read block and the denies are what keep tokens out of its reach.
- The fixer must run the code it writes (its tests, the build), in the job that holds the Claude token for the agent step, as root in the container. `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB` keeps the tokens out of the environment of the agent's commands, the checkout stores no token, and `prove` drops anything credential-like from the environment of what it starts. The read block and the denies stop only the agent's Read, Grep and Glob tools: its allowed Bash commands read files anywhere (`git diff /dev/null <path>` or `git diff --no-index` prints any file, `/proc` included), exactly like the code its tests run. So the agent's real exposure is the same as that code's: the job's own processes as root in the container, and the network. That includes the action's process, which holds the Claude token for the length of the job; the only GitHub token in the job is the read-only `GITHUB_TOKEN`, which expires with the job. Everything the job uploads is scanned first, and `publish` (main's code, no code from the fix) pushes only a commit it built itself and scans that commit's blobs (text and binary) and message, the report, the proof and the images before it pushes; like the other checks, it stops only verbatim tokens. The fixer's inputs (the issue, filed by QA or the owner, and the repository) are owner-controlled, which keeps prompt injection unlikely. Everything the `fix` job writes after the agent step (the proof, the images, the scan) runs in the same container, so it is a claim: the reviewer's evidence job proves red and green again without secrets.
- The reviewer keeps `.git` to read the diff, so its allowed `git diff --no-index` can print any file the job can read, including `.git/config` should the action store its read-only `GITHUB_TOKEN` there. The review job holds no write token (the write scopes are in `post`, which runs no agent), the verdict is scanned before upload and its text checked again by `post`; like every other check, these stop only verbatim tokens.
- The `evidence` job runs the pull request's tests and the QA agent's script as root in the container, after the decisive evidence has been uploaded. What the proof phase writes is a claim: `post` reads its verdicts from the data and the decisive evidence decides. The behavioural check fails closed: a test that passed on the base and fails on the head site, flaky or not, keeps the evidence red, and the issue reaches a person after the last attempt.
- The `check` job runs the agent's scripts as root in the container. Each script runs in a process group of its own, which is killed when the script ends, but a script that starts a new session itself (`setsid`) escapes that and could still tamper with the job's later steps (the summary, the scan, the upload). The job holds no secret and a read-only token, and `file`, which holds the App token, takes every draft's groups from the probe artifact again and checks all text before filing.

## Known limitation

Sync pull requests are opened with `GITHUB_TOKEN`, so they still need the owner's admin bypass to merge; the factory does not merge them.
