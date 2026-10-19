# Agent factory runbook

Spec: `docs/superpowers/specs/2026-10-18-agent-factory-design.md`. It covers phase 0 (the one-time repository setup done by the owner), the first dry run of the QA workflow, the fixer, the reviewer and the merger as they run, then the owner's operations (modes, labels, the status issue, acceptance, the evidence branch) and where each job runs.

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
8. **Allow squash merges only for factory pull requests.** In the repository settings (Pull Requests), keep squash merging as the only allowed merge method for `main`. The reconciliation and verify assume one squash commit per pull request (the merge commit GitHub reports is the commit that carries the fix); a merge commit or a rebase merge would break both.

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

`factory-review.yml` runs when Check completes (`workflow_run`), and by hand with `gh workflow run factory-review.yml --ref main -f pr=<number>`, which routes again on Check's latest pull request run of the head. Its jobs: `route` (read-only: ignore unless the pull request is the App's, from this repository, labelled `factory` and not `factory:needs-human`, open and not a draft, its head is the commit Check ran on and its body's `factory:head` marker names that head, or a commit that head updates with `main` (GitHub's "Update branch": a merge commit, or several in a row, whose first parent leads back to the marked head and whose second parent is on `main`, read with git from `main`'s checkout, which is deepened for that), and `github-actions[bot]` has not reviewed that head yet, so no evidence job runs for a reviewed head) → `act` when Check failed (the fixer's next attempt with `GITHUB_TOKEN`, or after the last attempt `factory:needs-human` and the pull request back to draft), or → `evidence` → `review` → `post` when Check passed, and `hand-over` when one of those three failed. Every git command of every job runs without hooks: the workflow sets `core.hooksPath=/dev/null` through `GIT_CONFIG_COUNT`.

- `evidence` (the Playwright image, no environment, `contents: read`, no token in any step) works in three phases, each uploaded before the next starts. The decisive phase runs no Python from the pull request or from an agent: the fence and the copy guard from `main`'s code; the base commit's `tests/e2e` and `tests/visual` in their own pytest sessions, on a worktree of the base and on a worktree of the base with only the head's site files and baselines (every test that passed on the base must pass against the head site; the ones that do not run once more in a fresh head-site session, and a pass there is accepted and listed as "flaked once"); the issue's browser reproduction (its probe groups) on a local build of the head (it must be gone); the probe on the head and the base around the issue's page (no new finding). Only after the decisive upload the script phase runs the issue's reproduction script, when it has one, on a local build of the head (it must say NOT REPRODUCED), and only after that upload the proof phase runs the head's own new tests: red on the base, green on the head, or 50 throttled runs for a flaky test. A revert gets the exact-revert check only.
- The evidence job's 180 minutes budget four test sessions and the rerun at 15 minutes each (`SESSION_TIMEOUT_SECONDS` in `tools/factory/review_evidence.py`), the browser checks, the script and the proof; the arithmetic is in the workflow.
- `review` (environment `factory`, a read-only `GITHUB_TOKEN`) first writes the change out as data with `main`'s code (`review prepare`: `factory-in/review/diff.patch`, `log.txt`, `changed.json` the head's version of each added or modified file under `files/<path>` and the base's version of each modified or deleted file under `base/<path>`, read as git objects through `git cat-file --batch` with no hook, external diff or textconv filter; symbolic links, submodules, files over 5 MB, files that could not be written (the errno name is the note) and any path with a component named `CLAUDE.md`, `CLAUDE.local.md`, `AGENTS.md` or `.claude` in any case are listed, never written; the same names are removed from Check's `visual-results`, and the fence refuses such a path with `harness file: <path>`), then deletes `.git`, then runs the reviewer agent with `reviewer.md`. The agent has Read, Glob and Grep inside the workspace and no shell at all (`Bash` is denied outright): it reads the prepared change, the evidence and Check's `visual-results`, edits nothing, and returns a verdict (`schemas/verdict.json`).
- `post` holds the write scopes of a review (`pull-requests`, `issues`, `actions`) and runs nothing but `review post`: it routes again, refuses to post when `github-actions[bot]` already reviewed this head (and then dispatches nothing), re-reads the evidence, checks the verdict's text (no hidden marker, closing keyword, issue link or secret shape; mentions neutralised; names the head chose and detail from the artifacts in code spans), posts the review as `github-actions[bot]` for the head the evidence tested, and dispatches `factory-merge.yml` after an approval or the fixer's next attempt after requested changes. It refuses an approval when the evidence is not green, a checklist item is false or the verdict names another commit, and then hands the issue to a person.
- `hand-over` runs only when `evidence`, `review` or `post` failed or was cancelled (a crash, a timeout, an agent without a verdict). With `GITHUB_TOKEN` (`pull-requests` write, `issues` read; the job runs under `always()` with a condition on the three results) and nothing from the pull request, it routes again and, while the review of that head is still due, leaves one comment on the pull request naming the failed jobs and the run. The factory does not retry: read the run, then dispatch the review again or label the pull request `factory:needs-human`. If `post` failed after the review was posted, a second review of that head is refused, so dispatch the merger or the fixer's next attempt by hand.
- The evidence and the verdict reach later jobs as artifacts downloaded by id, so an artifact uploaded later under the same name cannot replace them.
- Dry run: dispatch the review of a factory pull request by hand and read the `evidence` job's log (the decisive reasons, the test sessions and the rerun under `sessions/`) before trusting a verdict. Check whether `actions/download-artifact` nests the by-id downloads in a directory named after the artifact; `post` accepts both layouts, the prompt's paths assume the flat one (`merge-multiple: true`). Check that the reviewer agent works without `.git`, as the QA agent does.

## The merger

`factory-merge.yml` runs when the reviewer dispatches it after an approval, every hour at minute 5 (`--sweep`: every open pull request labelled `factory`, then the reconciliation below), and by hand with `gh workflow run factory-merge.yml --ref main -f pr=<number>`. One job (`concurrency: factory-merge`, environment `factory`, a checkout of `main`) mints the App token and runs `python -m tools.factory.merge`, policy as code with no model and nothing from the pull request: the fence is recomputed from `main`'s code over the head, fetched as git objects into `refs/factory/merge-head` and never checked out.

- The conditions, each a row of the verdict:
  - the mode;
  - the App's pull request, labelled `factory`, from this repository, open, not a draft, on `main`, either from `fix/<N>-…` with issue `#N` labelled `factory:fixing` and its body's `factory:head` naming the head (or a commit the head updates with `main`, as the reviewer accepts it), or from `revert/<N>` labelled `factory:revert` with issue `#N` labelled `factory:regressed`;
  - the latest review by `github-actions[bot]` approves the head, with no request for changes after it or standing from the owner, a member or a collaborator (GitHub's `author_association`; a stranger's request for changes does not block);
  - every required check of the rules of `main` (`GET /rules/branches/main`) succeeded on the head, in every run of it;
  - the fence, the copy guard and the size hold with no review reason, diffed against the merge base of `main` and the head; for a revert, which removes the fix's own test, this row is instead the exact-revert check: the head undoes exactly the squash merge named by the issue's trusted `factory:merged` marker;
  - the branch is up to date with `main`;
  - the last Pages run on `main` succeeded (a flaky test's fix and a revert are exempt);
  - fewer than `limits.merges_per_day` factory merges by the App in 24 hours (reverts are exempt);
  - neither the pull request nor a fix's issue is labelled `factory:needs-human` (a revert's issue always is).
- Shadow: one comment per head with the table, the verdict and `<!-- factory:verdict=merge|block sha=<head> -->`, and `factory:merge-ready` while it would merge. Nothing is said before `github-actions[bot]` reviewed the head, and nothing is written on a pull request that is no longer open. Later runs on the same head never add a comment: they edit that one in place when the verdict or the set of failed rows changes (a block at the approval that has cleared since, a "would merge" withdrawn after a request for changes or a take-over), leave it when only the values moved, and set or remove `factory:merge-ready` every run. The status issue's agreement reads the edited verdict. A branch behind `main` gets a `block` verdict that says it would be updated; in shadow mode you update it, and the review of the new head proceeds.
- Auto: a squash merge of exactly the evaluated head (`sha`), titled from the pull request through the same safe-title check as the fixer and with the message `Refs #N` (never the description), so the issue stays open until QA verifies the fix live. The issue then gets the `factory:merged` comment marker verify reads, and only after it moves from `factory:fixing` to `factory:merged`; the branch is deleted and dispatch promotes the next issues. A revert is merged and its branch deleted; its issue stays with you. A branch behind `main` is updated (`expected_head_sha`) instead, and Check and the review run again on the new head. If GitHub refuses the update with 422, the pull request is read again: a head that moved is a logged race; a head that did not move, or `mergeable_state` `dirty`, is a conflict with `main`. A conflict (also seen before the update) labels the pull request and its issue `factory:needs-human`, takes `factory:fixing` off the issue and posts one fixed comment, so the WIP slot frees and a person resolves it. A blocked pull request gets nothing written; the job log has the table.
- Harmless races end the run green with a log line: the head moved since it was read, GitHub's 409 on the merge (the head moved) or 422 on the update of the branch (the head moved, or the branch no longer merges cleanly with `main`: then update it by hand). A later run decides.
- Reconciliation (the sweep, in shadow and auto mode): the App's `fix/<N>-…` pull requests labelled `factory` and closed in the last 7 days whose issue `#N` is still open and labelled `factory:fixing`. Merged by anyone (your merge in shadow mode, or an auto merge whose follow-up failed) and without a trusted `factory:merged` marker naming that pull request: the marker of its merge commit is posted first, then `factory:merged` replaces `factory:fixing` and dispatch promotes, so the WIP slot frees and verify takes the issue up. Closed unmerged: `factory:needs-human` replaces `factory:fixing`, unless an open factory pull request is a later attempt for the same issue. Merges are reconciled before closed attempts, and a second sweep finds nothing to do. If the marker was posted but the labels failed (an interrupted follow-up), the sweep finishes it while the issue is still `factory:fixing` and no open factory pull request exists for it: `factory:merged` replaces `factory:fixing` and dispatch promotes. Only pull requests into `main` are reconciled. The shadow comment is edited only when the App wrote it; if only an owner's comment carries a verdict for the head, the merger posts its own once. "Update branch" commits (R44) must also be clean merges: each tree equals `git merge-tree --write-tree` of its parents (git 2.38 or later; otherwise refused).
- Dry run (shadow, after the first approval): check that the App token reads the rules of `main` (metadata: read comes with every installation token; an empty list blocks every pull request with "no required status checks") and that the required contexts match the check run names of Check on the head. Before switching to `auto`, check that the App's merge satisfies the ruleset (the approval of `github-actions[bot]`, the required checks, the branch up to date).

## Operations

### Modes and the kill switch

`FACTORY_MODE` is a repository variable with three values. Phase 0 creates it as `off`. Every factory job checks it first.

```
gh variable set FACTORY_MODE --body off      # kill switch
gh variable set FACTORY_MODE --body shadow
gh variable set FACTORY_MODE --body auto
```

| Mode | QA | Fixer | Reviewer | Merger |
|---|---|---|---|---|
| `off` | every job is skipped | skipped | skipped | skipped |
| `shadow` | runs, files issues, verifies, promotes, keeps the status issue | runs and opens pull requests | runs and posts reviews | writes a verdict comment and sets `factory:merge-ready`; never merges, never updates a branch. You merge or close. The sweep still reconciles pull requests you merged or closed. |
| `auto` | as in shadow | as in shadow | as in shadow | squash-merges the evaluated head, updates a branch that is behind `main`, merges reverts, reconciles |

`off` stops new work at once but does not cancel runs already started, and it does not undo anything: open pull requests, labels and comments stay as they are. To stop a run in progress too, cancel it in the Actions tab. To stop one issue only, see "Taking an issue back". `auto` needs the switch rule below; `off` or `shadow` can be set at any time.

### Handing an issue to the factory

- QA files `factory:found` issues with a failing reproduction. The nightly (or after-deploy) QA run promotes them to `factory:ready` itself (as the App) while fewer than `limits.fixes_in_progress` (2) issues are `factory:ready` or `factory:fixing`.
- To hand one over yourself, add `factory:ready` to the issue (`gh issue edit N --add-label factory:ready`). The fixer honours the label only when the owner (`trusted_logins`) or the App set it; the guard checks the latest labelling event's sender. A label from anyone else is ignored, and the issue is never fixed.
- It works the same for issues you wrote yourself. The fixer needs a reproducible, testable problem: say where, at which width and in which browser.
- `factory:suggestion` issues (an AI-review finding without a reproduction) are never promoted; handing one over with `factory:ready` is your call.

### Labels

On issues:

| Label | Meaning | Set by | Next |
|---|---|---|---|
| `factory:found` | A defect with a failing reproduction, waiting for a fixer slot | QA (`file`) | The promotion step adds `factory:ready` when a slot is free: flaky first while `main` is red, then by severity, then oldest. `deploy-stale` is never promoted. |
| `factory:suggestion` | A finding without a reproduction | QA | Nothing. Read it; fix it by hand or close it. |
| `factory:ready` | Handed to the fixer | The promotion step (App) or you | The fixer starts and, once its pull request exists, replaces the label with `factory:fixing`. |
| `factory:fixing` | A fix branch or pull request exists | Fixer (`publish`) | The reviewer and the merger decide; after a merge it becomes `factory:merged`. |
| `factory:merged` | The fix is on `main`; waiting for verification on the live site | Merger (auto) or the sweep (a merge by you) | QA's verify reruns the reproduction on the live site once Pages deployed the merge: gone, so a comment with the live image and the issue is closed; still there, so `factory:regressed`. |
| `factory:regressed` | Live verification failed after a merge | QA (`verify-act`) | A `revert/<N>` pull request is open; the issue also gets `factory:needs-human`. |
| `factory:flaky` | A test failed and passed on the same commit | QA, together with `factory:found` | Fixed like any defect, proven by 50 throttled runs. |
| `factory:needs-human` | The factory stopped touching this issue | Fixer, reviewer, merger, verify | You decide, see below. |

Severity labels `S1` to `S4` sit beside `bug` and `accessibility`.

On pull requests: `factory` (every factory pull request), `factory:merge-ready` (the shadow verdict is "would merge"; set or removed on every merger run), `factory:revert` (a revert), `factory:needs-human` (a soft fence reason or a conflict).

### `factory:needs-human`

It means the factory will not act on this issue or pull request again: the guard and publish skip it, the reviewer routes it out, the merger blocks it, the dispatcher does not count it. Reasons are listed in the last comment or in the pull request's "Needs a human" section: attempts exhausted (`limits.attempts_per_issue`, 2), not reproducible, a change outside the fence or to page copy, a conflict with `main`, a failed review (a crash, no verdict), a failed live verification.

- **Take it back:** fix it yourself on your own branch, or close it. Remove `factory:fixing` and `factory:ready` too if they remain, so the slot frees. A factory pull request you do not want: close it, the sweep then moves its issue to `factory:needs-human`.
- **Return it to the factory:** remove `factory:needs-human`, then add `factory:ready` (as the owner) on an issue, or dispatch the next attempt: `gh workflow run factory-fix.yml --ref main -f issue=N -f attempt=K`. The guard counts the `<!-- factory:attempt=K -->` comments, so after the limit it refuses again; to allow another round, edit or delete those attempt comments, which is your decision. For a pull request, remove the label and mark it ready for review; Check then runs the reviewer again on a new head.
- A revert's issue is always `factory:needs-human`: the revert is the factory's last act, what happens next is yours.

### The status issue and the switch rule

QA keeps one pinned issue, "Factory status", edited in place at the end of each real run (`dispatch-status`; a dry run does not touch it). It shows issues by state, merges and reverts in the last 7 days, flaky tests, agent runs per day and the shadow agreement.

- **Agreement:** each factory pull request decided in shadow mode (merged or closed) is compared with its last verdict: merged by you means "merge", closed unmerged means "block". The issue counts the agreeing ones among the last 10 decided.
- **Switch rule:** at least 10 decided, at least 9 of the last 10 agree, and no "would merge" that you closed (listed separately as dangerous). When the status issue says the rule holds, you may run `gh variable set FACTORY_MODE --body auto`. Nothing flips it automatically. Before the first switch, run the checks in the merger's dry run.
- Go back to `shadow` at any time; it takes effect on the next run.

### How shadow mode records verdicts

The merger writes one comment per pull request head, with the condition table, the verdict ("would merge" or "would block: ...") and the marker `<!-- factory:verdict=merge|block sha=<head> -->`. Nothing is written before `github-actions[bot]` reviewed the head. Later runs on the same head edit that comment in place when the verdict or the failed rows change; a new head gets a new comment. The status issue reads the edited verdict.

Owner merges are reconciled by the hourly sweep (minute 5): for a closed `fix/<N>-…` pull request of the App whose issue is still `factory:fixing`, a merge gets the `factory:merged` marker comment and label (so verify takes the issue up and the slot frees), and a close without merge gets `factory:needs-human`. Merging a factory pull request by hand therefore needs no follow-up; give the sweep up to an hour, or dispatch it: `gh workflow run factory-merge.yml --ref main`.

## Acceptance

Run in order, with phase 0 done and the workflows on `main`. Steps 2 to 5 are the owner's.

- [ ] **1. QA dry run on the live site.** `gh variable set FACTORY_MODE --body shadow`, then `gh workflow run factory-qa.yml --ref main -f dry_run=true` and `gh run watch`. Expect: `probe` green; the `file` job summary lists at least the `deploy-stale` body (`1f104d1` against `8b6ec7b`, unless Pages has deployed `main` since) and the flaky docs history test; nothing is written to GitHub. Read the list and the dropped findings for false alarms.
- [ ] **2. QA for real.** `gh workflow run factory-qa.yml --ref main`. Expect: at most 3 new issues (`limits.issues_per_run`), each with a reproduction and images linked from the evidence branch, and the "Factory status" issue created and pinned. The promotion step may add `factory:ready` to found issues, which starts the fixer (set `off` first if you want to read the issues before the fixer starts).
- [ ] **3. Three fixes in shadow mode.** Add `factory:ready` to #45, #46 and the flaky-test issue. Expect for each: a `fix/<N>-…` pull request labelled `factory`, saying `Refs #N`, with before and after images; Check green; a review by `github-actions[bot]`; one merger comment with a "would merge" verdict and `factory:merge-ready`. Nothing is merged by the factory. Merge or close each yourself; the next sweep moves the issue to `factory:merged` or `factory:needs-human`.
- [ ] **4. Shadow period.** Keep `shadow` for about 10 decided factory pull requests, until the status issue shows the switch rule holding. Read every "would block" and every disagreement. Expect, after each merge you make, that QA verifies it on the live site and closes the issue.
- [ ] **5. The revert path once.** With a `factory:merged` issue (its fingerprint is in the issue), `gh workflow run factory-qa.yml --ref main -f simulate_failed_verify=<fingerprint>`. Expect: the issue becomes `factory:regressed` and `factory:needs-human`, and a `revert/<N>` pull request labelled `factory` and `factory:revert` goes through Check and the reviewer's exact-revert check; the merger's shadow comment says "would merge". Close it without merging.
- [ ] **6. Auto (owner's decision).** Only when the status issue says the switch rule holds: `gh variable set FACTORY_MODE --body auto`. Expect the next approved pull request to be squash-merged by the App with `Refs #N`, Pages to run, and QA to verify it live.

## Evidence branch

Screenshots and reproduction scripts live on the orphan branch `factory-evidence`, pushed with the App token (`factory-evidence` commit identity); `check.yml` ignores pushes to it.

- Issues: `issues/<fingerprint>/<run id>/…`. Pull requests: `prs/<number>/<sha>/{before,after,diff}/…`.
- Issues and pull requests link the files as `https://raw.githubusercontent.com/MBratkowski/deweloperuch-mcp-site/<evidence commit>/<path>`. The link names the commit, so it never changes and survives later commits, but not a squash that drops the commit: do not squash a commit that an open issue or pull request still links.
- Pruning rule: when the branch is over 200 MB (`git clone --branch factory-evidence` and `du -sh .git`, or the repository size in the settings), squash it to one commit that keeps only the images linked from open issues and pull requests. By hand:
  1. Clone the branch (`git clone --branch factory-evidence --single-branch git@github.com:MBratkowski/deweloperuch-mcp-site.git /tmp/evidence`).
  2. List what open issues and pull requests link: `gh issue list --state open --json number,body,comments` and `gh pr list --state open --json number,body,comments`, grep for `raw.githubusercontent.com/MBratkowski/deweloperuch-mcp-site/` and note each `<commit>/<path>`.
  3. For each noted link, read the file at that commit (`git show <commit>:<path>`) and write it to the same path in a new orphan branch (`git checkout --orphan pruned`, remove everything else).
  4. Commit once and force-push over `factory-evidence`. Links that name the old commits stop resolving unless GitHub still has the objects, so edit each open issue and pull request whose link changed to the new commit. Closed issues and pull requests lose their images; that is the rule.
  5. Check one image in the browser from an open pull request. Nothing automated does this; the factory only adds commits.

## Where things run

Tokens: "read" is the job's own `GITHUB_TOKEN` with read scopes only; "App" is an installation token minted in the job with the listed scopes; "Claude" is `CLAUDE_CODE_OAUTH_TOKEN` (environment `factory`, held by the agent step). Jobs without a `factory` environment hold no secret.

| Workflow | Trigger | Job | Token |
|---|---|---|---|
| `factory-qa.yml` | nightly 02:30 UTC; after a successful Pages run on `main`; by hand (`dry_run`, `simulate_failed_verify`) | `guard`, `probe`, `verify`, `check` | read, no secret |
| | | `triage` (the QA agent) | read and Claude |
| | | `file` | read in a dry run; App `contents`, `issues` write, `actions` and `deployments` read otherwise |
| | | `verify-act` | App `contents`, `issues`, `pull-requests` write, `actions` and `deployments` read |
| | | `dispatch-status` | App `issues` write, `actions` and `pull-requests` read |
| `factory-fix.yml` | `factory:ready` labelled by the owner or the App; by hand or by a failed attempt (`issue`, `attempt`) | `guard` | read, no secret |
| | | `fix` (the fixer agent) | read and Claude |
| | | `publish` | App `contents`, `issues`, `pull-requests` write; `GITHUB_TOKEN` `actions: write` to dispatch the next attempt |
| `factory-review.yml` | Check completed on a pull request; by hand (`pr`) | `route` | read, no secret |
| | | `act` | `GITHUB_TOKEN` `actions`, `issues`, `pull-requests` write (next attempt or `needs-human`) |
| | | `evidence` | read, no secret, no environment |
| | | `review` (the reviewer agent) | read and Claude |
| | | `post` | `GITHUB_TOKEN` `actions`, `issues`, `pull-requests` write (the review and the merger dispatch) |
| | | `hand-over` | `GITHUB_TOKEN` `pull-requests` write |
| `factory-merge.yml` | the reviewer after an approval; hourly at minute 5 (sweep); by hand (`pr`) | `merge` | App `contents`, `issues`, `pull-requests` write; `actions` and `checks` read |

## Known residual risk

- The pinned `claude-code-action` (v1.0.241) installs Claude Code 2.1.289. The permission check that makes environment-printing forms of `ps` prompt arrives in 2.1.290, so until then the QA agent's `--disallowedTools` denies `ps`, `env`, `printenv`, `set`, `declare`, `export` and `compgen` outright. Re-pin `claude-code-action` to a release that bundles Claude Code 2.1.290 or later once that release is at least two weeks old, and keep the denies.
- The publish-side secret checks (draft text, the groups marker, reproduction scripts, the `drafts` and `validated` artifacts) stop only verbatim tokens and keys. A token the agent encodes first would pass them; the read block and the denies are what keep tokens out of its reach.
- The fixer must run the code it writes (its tests, the build), in the job that holds the Claude token for the agent step, as root in the container. `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB` keeps the tokens out of the environment of the agent's commands, the checkout stores no token, and `prove` drops anything credential-like from the environment of what it starts. The read block and the denies stop only the agent's Read, Grep and Glob tools: its allowed Bash commands read files anywhere (`git diff /dev/null <path>` or `git diff --no-index` prints any file, `/proc` included), exactly like the code its tests run. So the agent's real exposure is the same as that code's: the job's own processes as root in the container, and the network. That includes the action's process, which holds the Claude token for the length of the job; the only GitHub token in the job is the read-only `GITHUB_TOKEN`, which expires with the job. Everything the job uploads is scanned first, and `publish` (main's code, no code from the fix) pushes only a commit it built itself and scans that commit's blobs (text and binary) and message, the report, the proof and the images before it pushes; like the other checks, it stops only verbatim tokens. The fixer's inputs (the issue, filed by QA or the owner, and the repository) are owner-controlled, which keeps prompt injection unlikely. Everything the `fix` job writes after the agent step (the proof, the images, the scan) runs in the same container, so it is a claim: the reviewer's evidence job proves red and green again without secrets.
- The reviewer agent runs no command: any command it could run, even a read-only git one, can write files while the action holding the Claude token is alive (`git diff --output=<file>` writes any file, `.git/config` included, and a `diff.external` set there runs on the next diff). It reads only what the prepare step wrote and the workspace, `.git` is gone before it starts, and the review job holds no write token (the write scopes are in `post` and `hand-over`, which run no agent). The verdict is scanned before upload and its text checked again by `post`; like every other check, these stop only verbatim tokens.
- The `evidence` job runs, as root in the container, the QA agent's script (after the decisive evidence has been uploaded) and then the pull request's tests (after the script's result has been uploaded). A process either of them starts that escapes its process group (`setsid`) could still alter the job's later steps: `main`'s checkout, including `tools/factory/secret_scan.py`, and whatever is not uploaded yet (the script's result, the proof). Both are claims: `post` reads the proof's verdicts from its data, the script's own verdict is the only thing the script result can state, and the decisive evidence, uploaded before any of that code ran, decides. The job holds no secret and a read-only token. The behavioural check fails closed after one rerun: a test that passed on the base and fails on the head site twice keeps the evidence red, and the issue reaches a person after the last attempt.
- The `check` job runs the agent's scripts as root in the container. Each script runs in a process group of its own, which is killed when the script ends, but a script that starts a new session itself (`setsid`) escapes that and could still tamper with the job's later steps (the summary, the scan, the upload). The job holds no secret and a read-only token, and `file`, which holds the App token, takes every draft's groups from the probe artifact again and checks all text before filing.

## Known limitation

Sync pull requests are opened with `GITHUB_TOKEN`, so they still need the owner's admin bypass to merge; the factory does not merge them.
