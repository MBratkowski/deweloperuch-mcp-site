# Agent factory: design

Date: 2026-10-18. Status: approved in brainstorming, awaiting review of this document.

A closed quality loop for the published site (landing and docs on GitHub Pages): four agents find a defect, file it with evidence, fix it with a regression test and before/after images, review the fix independently and merge it, then confirm the fix on the live site. GitHub (issues, labels, pull requests, rulesets) is the only state.

## Decisions

Made with the owner during brainstorming:

| Question | Decision |
|---|---|
| Runtime | GitHub Actions; the agents run Claude Code through `anthropics/claude-code-action`. |
| Merge autonomy | Shadow mode first (the merger says what it would do, the owner merges), then automatic merging once the data allows it. |
| What QA may report | Measurable defects, plus findings from an AI review of screenshots only when the agent proves them with a failing reproduction; unproven ones are suggestions that are never fixed automatically. |
| Pace and limits | Nightly and after every Pages deployment. At most 3 new issues per run, 2 fix pull requests open at once, 2 fix attempts per issue, 5 factory merges per 24 hours, 2 QA agent runs per day (`qa_agent_runs_per_day`). |
| Architecture | An event-driven state machine: four workflows, labels as state. The merger is policy as code with no language model. |

## Facts this design rests on

Checked on 2026-10-18:

- The live site serves `1f104d1` while `main` is `8b6ec7b`: the Pages run for the merge of #44 failed in `tests/e2e/test_docs.py::test_toc_tracks_anchor_navigation_and_history[chromium-no-preference]` (a 5 s `wait_for_function` timeout) after the same commit passed Check. A flaky test is blocking deployment. The deployed SHA is readable from the Deployments API (`environment=github-pages`).
- One account (`MBratkowski`) authors every issue and pull request. GitHub does not let an author approve their own pull request, and events caused by `GITHUB_TOKEN` (pushes, pull requests, reviews, merges) start no workflows, except `workflow_dispatch` and `repository_dispatch`.
- The repository already allows Actions to approve pull requests (`can_approve_pull_request_reviews: true`); `main` has no protection; auto-merge is off.
- `validate.yml` (called by Check and Pages) already gates every commit: pytest with Chromium, visual baselines in the pinned image `mcr.microsoft.com/playwright:v1.64.0-noble@sha256:06a9939e…`, Firefox and WebKit smoke tests, Nu, lychee, Lighthouse. New visual baselines can be rendered only in that image (`docs/visual-tests.md`); the owner's Mac has no Docker.
- Polish page copy lives in the templates and also in renderers (`tools/html_helpers.py`, `tools/page_fragments.py`, `tools/docs_renderer.py`, `tools/docs_content.py`, `tools/page_config.py`) and in `js/site.js`. A copy guard therefore compares output, not file names.
- Check runs on every push to any branch (`on: push` without a branch filter).

## Architecture

### Identities

| Role | Identity | Reason |
|---|---|---|
| QA, Fixer, Merger | GitHub App `deweloperuch-factory`. Each job mints its own installation token with `actions/create-github-app-token`, limited to the permissions it needs. | Pushes, pull requests and merges made by an App start Check and Pages; `GITHUB_TOKEN` would not. |
| Reviewer | `github-actions[bot]` through `GITHUB_TOKEN` | It is not the pull request author, so its approval satisfies the ruleset. |

The App has contents, pull requests and issues (read and write), and actions, checks, deployments and metadata (read). It never has the `workflows` permission, so GitHub rejects any push from it that changes `.github/workflows/`.

**Agents propose, code disposes.** No agent holds the App token or a write token. Each agent writes its output to files (issue drafts, a commit on a local branch with a pull request body, a review verdict with comments). A deterministic step after it validates that output against the rules of this document and only then files, pushes or posts it with the right token.

### Ruleset on `main`

- A pull request is required, with 1 approval; a new push dismisses earlier approvals.
- Required status checks: every job of `Validate this commit` (tests, visual, browser smoke, generated, HTML, links, Lighthouse); the branch must be up to date.
- Repository admins may bypass, so the owner still merges their own work by hand. The App has no bypass and cannot push to `main`.

### State (labels)

On issues:

| Label | Meaning |
|---|---|
| `factory:found` | Filed by QA with a failing reproduction; waiting for a fixer slot. |
| `factory:suggestion` | AI-review finding without a reproduction. Never fixed automatically. |
| `factory:ready` | Handed to the fixer. Honoured only when set by the owner or the App. |
| `factory:fixing` | A fixer branch or pull request exists. |
| `factory:merged` | The fix is on `main`; waiting for verification on the live site. |
| `factory:regressed` | Live verification failed after a merge; a revert pull request is open. |
| `factory:flaky` | A test failed and passed on the same SHA. Set together with `factory:found`; fixed like any other defect, with statistical proof (see the fixer). |
| `factory:needs-human` | Outside the fence, attempts exhausted, copy change, not reproducible, or no reproduction the factory can run. The factory stops touching it. |

Severity labels `S1` (broken function), `S2` (accessibility barrier), `S3` (visual defect), `S4` (polish) sit beside the existing `bug` and `accessibility` labels.

On pull requests: `factory` (every factory pull request), `factory:merge-ready` (shadow verdict: would merge), `factory:revert`.

### Flow

```
QA (nightly, after each Pages deployment, manual)
 ├─ verify: issues labelled factory:merged → rerun their reproduction on the live site
 │     ├─ gone       → comment with a live "after" image, close the issue
 │     └─ reproduces → factory:regressed, open a revert pull request
 ├─ explore: probe the matrix → findings → dedup → issues (factory:found / factory:suggestion)
 ├─ flakes: scan CI history → factory:flaky issues
 └─ dispatch: promote factory:found → factory:ready while fewer than 2 are in progress
       (factory:flaky first while main is red, then by severity, then oldest; deploy-stale never)

Fixer (issues: labeled factory:ready; workflow_dispatch for a second attempt)
 → branch fix/<N>-<slug> from origin/main → failing test → fix → passing → pull request "Refs #N"

Check (unchanged) on the pull request
 → workflow_run → factory-review.yml
     ├─ Check failed  → dispatch the fixer (attempt 2) or factory:needs-human
     └─ Check passed  → review
            ├─ REQUEST_CHANGES → dispatch the fixer (attempt 2) or factory:needs-human
            └─ APPROVE (for the head SHA) → workflow_dispatch factory-merge.yml

Merger (workflow_dispatch; hourly sweep), FACTORY_MODE = off | dry | shadow | auto
 ├─ shadow: comment with the condition table and verdict, label factory:merge-ready when it would merge
 └─ auto:   squash-merge with the App token → Pages → QA verify
```

Pull requests say `Refs #N`, not `Fixes #N`: an issue closes only after QA confirms the fix on the live site.

The reviewer starts the merger with `workflow_dispatch` because an approval made with `GITHUB_TOKEN` triggers no `pull_request_review` workflow. The hourly sweep catches anything a lost event left behind.

### Workflows

| File | Triggers | Runs |
|---|---|---|
| `factory-qa.yml` | `schedule` (nightly), `workflow_run` of Pages on `main` (success only), `workflow_dispatch` (`dry_run`, `simulate_failed_verify`) | probe, QA agent, verify, flakes, dispatch, status |
| `factory-fix.yml` | `issues: labeled` (`factory:ready`), `workflow_dispatch` (`issue`, `attempt`) | fixer agent |
| `factory-review.yml` | `workflow_run` of Check (pull request events only), `workflow_dispatch` (`pr`) | evidence job without secrets, then the reviewer agent |
| `factory-merge.yml` | `workflow_dispatch` (`pr`), `schedule` (hourly) | merge policy |

Every factory job first checks the repository variable `FACTORY_MODE`; an explicit allow-list decides whether it runs. `off` runs nothing; `dry` runs QA as a dry run only (no GitHub writes; the fixer, the reviewer and the merger do not run); the fixer, the reviewer, the merger and QA's writing jobs run only in `shadow` and `auto`. An unset or unknown value runs nothing, like `off`. Each workflow has a `concurrency` group (per issue for the fixer, per pull request for review and merge, one for QA).

`check.yml` gains `branches-ignore: [factory-evidence]` under `push`.

### Configuration

`.github/factory/config.json` holds the limits, the probe matrix, the fence (allowed and forbidden paths), the line limit and the model for each agent. The agent prompts live in `.github/factory/prompts/` (`qa.md`, `fixer.md`, `reviewer.md`). Both sit under `.github/`, which the fence forbids, so no agent can loosen its own rules. The mode is the repository variable `FACTORY_MODE`, so the owner can stop or promote the factory without a commit.

## QA agent

Two halves: a deterministic probe (`tools/factory/`, Python and Playwright, covered by tests) and a Claude agent that triages and reviews screenshots.

### Target

The probe renders the live site first: that is what visitors get. Before anything is filed, the same finding must reproduce on a local build of `origin/main` (`python3 -m tools.package_site`, served on 127.0.0.1). A finding on the live site that does not reproduce on `main` is already fixed and waiting for deployment, and is not filed.

When the deployed SHA differs from `origin/main` and the last Pages run on `main` failed, QA files one `deploy-stale` finding (S1) that links the failing run. It is never handed to the fixer; it closes itself when the live SHA matches `main` again.

The probe intercepts every request the way `tests/e2e/conftest.py::keep_offline` does, but lets the Pages host through: Umami gets an empty stub (nightly runs must not count as visits) and every other host is aborted and recorded. deweloperuch.pl is never requested.

### Matrix

From `config.json`, about 100 renders per run:

- Chromium, Firefox and WebKit × widths 320, 375, 390, 414, 719, 721, 759, 761, 768, 1024, 1280, 1440, 1920 × landing and docs, at a height of 900. The widths 719/721 and 759/761 sit one pixel either side of the stylesheet's breakpoints (720 and 760 px).
- Chromium only, at 320, 768 and 1440, on both pages: JavaScript disabled, `reduced_motion="reduce"`, and a short window (height 500).
- Interactions: the mobile menu opened at 320–414; full keyboard traversal at 375 and 1440.

### Detectors

Each detector has a check id, runs in every cell of the matrix it applies to, and returns findings with a selector, a measurement and the cell.

| Check id | Measures | Basis |
|---|---|---|
| `overflow` | Visible elements past the right edge that no scrolling box holds (the `WIDE_ELEMENTS` logic of `tests/e2e/test_pages.py`) | WCAG 1.4.10 |
| `clipped-text` | Text elements whose content is larger than a box with `overflow: hidden` | WCAG 1.4.10 |
| `overlap` | Interactive elements whose boxes intersect each other or a text box | — |
| `axe` | axe-core violations with the WCAG 2.x A and AA tags, one finding per rule and node | WCAG |
| `target-size` | Interactive targets smaller than 24 × 24 CSS px; smaller than 44 × 44 in the mobile navigation, as the existing smoke test requires | WCAG 2.5.8 |
| `focus` | A focusable element without a visible ring (the `FOCUS_PROBLEMS` logic); a focused element hidden under the sticky header; a scrollable region keyboard focus cannot reach | WCAG 2.4.7, 2.4.11, 2.1.1 |
| `console` | Console errors and uncaught page errors | — |
| `network` | Failed requests; requests to any host other than the site and Umami | `CLAUDE.md` |
| `anchors` | Internal links whose `#id` has no target | — |
| `images` | Images with `naturalWidth` 0 | — |
| `engine-diff` | Boxes of landmarks and headings that differ between engines by more than a configured number of pixels at the same width | — |
| `flaky` | From CI history (not the browser): a job that failed and passed on the same SHA in Check or Pages within 14 days, with the failing test ids from the log | — |
| `deploy-stale` | Deployed SHA differs from `main` after a failed Pages run | — |

`engine-diff` compares geometry, not pixels, so font rasterisation does not produce noise. Its threshold is calibrated in phase 1 on the current site.

The JavaScript of `WIDE_ELEMENTS` and `FOCUS_PROBLEMS` moves to `tools/factory/detectors.py`; `tests/e2e/test_pages.py` imports it from there instead of keeping its own copy. axe-core is vendored in `tools/factory/vendor/` at a pinned version with its licence and injected by the probe; it is never part of the published site.

### Fingerprints and dedup

A fingerprint is a short hash of the check id, the page, the mode, the normalised selector (no `nth-child` indices, no generated ids), the set of engines and the width bands the finding covers (up to 720 px, 721–760 px, from 761 px: the stylesheet's breakpoints). Bands rather than the exact range keep the fingerprint stable when a defect's edge moves by one matrix width. Findings that differ only in adjacent widths merge into one ("320–414 px, WebKit"), so one defect is one issue, and the issue shows the exact range.

Each factory issue carries `<!-- factory:fp=<fingerprint> -->` and the finding as JSON in a hidden block. Before filing, QA searches open and closed issues for the fingerprint:

- open: add nothing (at most a comment if the evidence changed);
- closed as `invalid`, `wontfix` or `duplicate`: never filed again;
- closed after a verified fix: filed again as a new issue labelled `regression` that links the old one.

### The agent: triage and AI review

The agent receives the probe's `findings.json`, contact sheets (per page and engine, one image with every width side by side), the rendering of `docs/design/noc.dc.html` at 1440 px, and the spec.

- **Triage.** It merges findings with one cause into one issue, assigns severity, and writes what is wrong and why it matters. Dropping a detector finding requires a written reason in the run summary.
- **AI review.** For anything that looks wrong but no detector reports (misalignment, inconsistent spacing, a fallback font, a deviation from the design reference), the agent must write a Playwright script that measures the defect and exits non-zero when it is present, then run it on the live site and on the local `main` build. Only a script that fails on both turns the finding into `factory:found`. Otherwise the finding becomes `factory:suggestion` with its screenshot; at most one suggestion per run.

Every issue has a one-command reproduction. For detector findings it is `uv run python -m tools.factory.probe --reproduce <finding JSON> --target <url>`, which reruns one detector in the finding's cells and exits 1 when the defect is there. For AI-review findings it is the agent's script, stored on the evidence branch and quoted in the issue.

The agent writes issue drafts to a file. A deterministic step files them with the App token only when each has a fingerprint not found by dedup, a reproduction whose recorded output shows it failing on the live site and on `main`, and the required sections. At most 3 issues are filed per run, by severity; the rest appear only in the run summary. When the probe itself fails (a browser does not start, the site does not load), the job fails and files nothing.

### Issue format

English, in the style of #45 and #46. The title states the expected behaviour. Sections:

1. **Problem**: one paragraph.
2. **Where**: page, engines, widths, mode; live SHA and `main` SHA; URL.
3. **Reproduce**: the command, and the script in `<details>`.
4. **Actual** and **Expected**, with the measurement.
5. **Why it matters**: the effect on visitors and the WCAG criterion or the line of the spec.
6. **Evidence**: a viewport screenshot with the element outlined in red, a crop, and for engine differences a table per engine.
7. **Acceptance criteria**: the reproduction passes in every listed cell; no new finding in the rest of the matrix for that page.
8. **Likely sources**: files that produce the element (template, stylesheet, renderer), from the selector.

### Evidence branch

Images live on the orphan branch `factory-evidence`, pushed with the App token: `issues/<fingerprint>/<run id>/…` and `prs/<number>/<sha>/{before,after,diff}/…`. Issues and pull requests link them as `https://raw.githubusercontent.com/MBratkowski/deweloperuch-mcp-site/<evidence commit>/<path>`, so a link never changes after it is posted. Screenshots are PNG at device scale 1, cropped where possible.

### Live verification

After each Pages deployment, QA reruns the reproduction of every `factory:merged` issue against the live site, once the deployed SHA contains the merge. If it passes, QA posts the live "after" image and closes the issue. If it still fails, or the probe finds a new S1 or S2 finding on a page the merge changed, QA labels the issue `factory:regressed` and opens a revert pull request (see the merger).

### Status issue

QA keeps one pinned issue, "Factory status", up to date (edited, never re-posted): issues by state, merges and reverts in the last 7 days, flaky tests, agent runs per day, and the shadow agreement described under the merger.

## Fixer

Runs on `factory:ready` (set by the owner or the App; any other sender is ignored) and on `workflow_dispatch` for a second attempt. The job runs in the pinned Playwright image of the visual job, so it can render visual baselines itself.

An issue written by the owner enters the factory only with an attached reproduction: `python -m tools.factory.issues attach --issue N --findings <findings.json> --fp <fp>` appends the marker block QA writes. Without one the fixer declines the issue and labels it `factory:needs-human`.

1. Create `fix/<N>-<slug>` from `origin/main`; label the issue `factory:fixing`.
2. **Red.** Turn the issue's reproduction into a permanent test: a pytest test under `tests/e2e/`, or a new crop in `tests/visual/` for a purely visual defect. Run it on the base commit; it must fail. If it passes, the defect is not reproducible: comment with the output, label `factory:needs-human`, stop.
   For a `factory:flaky` issue the proof is statistical: the existing failing test runs 50 times on the base commit with Chromium's CPU throttled 4× and must fail at least once; after the fix it must pass all 50 runs under the same throttling. If the base never fails, the issue goes to `factory:needs-human` with the run counts.
3. **Cause.** Find the source of the defect in the templates, stylesheets, scripts or renderers, never in the published HTML. Regenerate the pages with `python3 -m tools.build_docs`.
4. **Green.** The new test passes; `uv run pytest` passes; when the appearance changes, render the baselines with `--update-visual-baselines`, run the visual tests again without the flag, and list every changed baseline with the reason.
5. **Before and after.** Render exactly the issue's cells (page × engine × width) on the base and the head commit with the probe; push the images and a diff image to the evidence branch.
6. **Pull request.** The agent commits on the local branch and writes the pull request body to a file. A deterministic step checks the fence, the copy guard and the size limit, then pushes the branch and opens the pull request with the App token, so Check runs. A failed check sends the issue to the next attempt or to `factory:needs-human` without pushing. Title: the issue's expected behaviour. Body: `Refs #N`; the cause; the change; the red and green proof (test id, output on the base SHA and on the head SHA); the before/after table; changed baselines with reasons; the changed files; the attempt number. Labels: `factory`.

Tools: read, edit and write files; shell limited to `uv run pytest`, `python3 -m tools.build_docs`, `python3 -m tools.package_site`, `uv run python -m tools.render_images`, `uv run python -m tools.factory.probe`, `git` without `push`, and `gh issue view`. Never `tools.capture_demo`, `tools.sync_server` or `tools.check_facts`. No web fetch or search. A turn limit and `timeout-minutes: 45`.

Two attempts per issue. The second starts from the reviewer's comments or the failed Check. After the second fails, the pull request goes back to draft with a summary of what was tried, and the issue gets `factory:needs-human`.

## Fence

Checked by the reviewer and enforced again by the merger. A change outside it sends the issue to `factory:needs-human`.

**Allowed:** `css/`, `js/`, `tools/templates/`, the renderers (`tools/html_helpers.py`, `tools/page_fragments.py`, `tools/docs_renderer.py`, `tools/docs_content.py`, `tools/page_config.py`, `tools/build_docs.py`); `index.html`, `docs/index.html` and `assets/images.json` only as written by the build (the `generated` job proves it); new or extended tests under `tests/`; files under `tests/visual/baselines/` with a stated reason.

**Forbidden:** `.github/`, `CLAUDE.md`, `data/`, `tools/capture_demo.py`, `tools/sync_server.py`, `tools/check_facts.py`, `tools/factory/`, `pyproject.toml`, `uv.lock`, `lighthouserc.json`, `lychee.toml`, `docs/superpowers/`, `assets/fonts/`, `assets/icon/`. Removing a test or an assertion, raising a timeout or a tolerance, adding `skip` or `xfail`, or loosening `tests/visual/comparison.py`.

**Copy guard.** Page copy comes from the spec and is never changed by the factory:

- the visible text of `index.html` and `docs/index.html` (text nodes and the `alt`, `aria-label`, `title`, `placeholder` attributes and `<meta name="description">`/Open Graph content) must be identical on the base and the head commit;
- the string literals containing Polish letters in `js/` must be identical on the base and the head commit.

**Size:** at most 300 changed lines, not counting PNG files and the generated `index.html`, `docs/index.html` and `assets/images.json`.

## Reviewer

Runs from `workflow_run` of Check on a pull request labelled `factory`, opened by the App from a branch of this repository. It runs the workflow from `main`, never from the pull request.

Two jobs keep the pull request's code away from secrets:

1. **Evidence** (no secrets, read-only token): check out the base and the head commit; run the new test on both (must fail on base, pass on head; for `factory:flaky`, the 50 throttled runs described under the fixer); run the issue's original reproduction on head (must pass); run the probe on head for the affected page across every width of the affected engines and compare with the base (no new findings); compute the fence and copy guard. Upload everything as an artifact.
2. **Review** (Claude token, `GITHUB_TOKEN` with pull request write): read the issue, the diff, the evidence artifact, and the `visual-results` artifact of Check (`expected.png`, `actual.png`, `diff.png`). It never executes code from the pull request.

The reviewer starts from the issue and the diff and treats the pull request body as claims to check. It uses its own prompt and may run on a different model from the fixer, so their mistakes do not correlate. Rubric:

- the cause is fixed, not the symptom: `!important`, unexplained magic numbers, hiding the element, or `overflow: hidden` that masks an overflow are rejected;
- the evidence job is green: red on base, green on head, original reproduction passes, no new findings nearby;
- no test is weakened; changed baselines match what the before/after images show;
- the fence, the copy guard and `CLAUDE.md` hold; the diff is minimal.

The agent writes its verdict and line comments to a file; a deterministic step posts the review with `GITHUB_TOKEN` for the head SHA the evidence job tested, and refuses to post an approval when the evidence job is not green.

Verdict: `APPROVE` on the head SHA with the rubric as a checklist, then `workflow_dispatch` of `factory-merge.yml`; or `REQUEST_CHANGES` with comments on the lines concerned, then `workflow_dispatch` of `factory-fix.yml` when an attempt is left (otherwise `factory:needs-human`).

When Check fails, the reviewer does not review: it dispatches the fixer's next attempt with the failed jobs, or labels `factory:needs-human`.

## Merger

`tools/factory/merge_policy.py`, deterministic and tested, run by `factory-merge.yml`. It never executes code from the pull request.

### Conditions

All must hold:

1. `FACTORY_MODE` is `shadow` or `auto` (`dry` and `off` run no merger).
2. The pull request is authored by the App, labelled `factory`, from a branch of this repository, not a draft, and either comes from `fix/*` and links an issue labelled `factory:fixing`, or comes from `revert/*`, is labelled `factory:revert` and links an issue labelled `factory:regressed`.
3. The latest review by `github-actions[bot]` is `APPROVE` with `commit_id` equal to the current head SHA, and no review requests changes.
4. Every required check succeeded on the head SHA. The merger never reruns a failed job.
5. The fence, the copy guard and the size limit hold (computed again, not read from the reviewer).
6. The branch is up to date with `main`. If it is behind, the merger updates the branch with the App token and stops; Check and the review run again on the new head.
7. The last Pages run on `main` succeeded. Exceptions: a pull request whose issue is labelled `factory:flaky`, and a revert, may merge while `main` is red. Today this blocks everything except the fix for the flaky test.
8. Fewer than 5 factory merges in the last 24 hours (reverts do not count).

### Modes

- **shadow**: a comment with a table of the conditions (✓/✗ with the value), the verdict ("would merge" or "would block: <conditions>"), and a hidden marker `<!-- factory:verdict=merge|block sha=<head> -->`; label `factory:merge-ready` when it would merge. The owner merges or closes.
- **auto**: squash-merge with the App token (so Pages runs), commit title from the pull request and `Refs #N` in the body; delete the branch; label the issue `factory:merged`; run dispatch.

**Shadow agreement.** For each factory pull request decided in shadow mode, the verdict is compared with the outcome: merged by the owner means merge, closed unmerged means block. The status issue shows agreement over the last 10 decided pull requests, and separately every "would merge" that the owner closed. The owner switches `FACTORY_MODE` to `auto` when at least 9 of 10 agree and no "would merge" was closed. The switch is the owner's; nothing flips it automatically.

### Revert

When live verification fails, QA opens a pull request from `revert/<N>` that reverts the merge commit, labelled `factory` and `factory:revert`. It goes through Check, a review that checks only that the change is an exact revert, and the merger, where the daily cap does not apply. The issue gets `factory:needs-human`; there is no automatic third attempt.

## Security

The repository is public: anyone can open issues and pull requests and comment.

- The fixer acts only on issues whose `factory:ready` label was set by the owner or the App; the dispatcher checks the label event's sender. Comments and pull requests from anyone else are ignored. Pull requests from forks never start factory workflows.
- Issue text is data. The agents have no network tools, so text that tries to instruct them has nowhere to send anything.
- Secrets (`CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token`, or `ANTHROPIC_API_KEY`; the App's private key) live in two Actions environments, both limited to `main`: `factory-agent` holds only `CLAUDE_CODE_OAUTH_TOKEN` and is used only by the agent jobs (triage, fix, review); `factory` holds only the App's private key and is used only by the jobs that mint the App token. An agent job can never read the App's key, and a workflow edited on a `fix/*` branch cannot read either.
- GitHub enforces the hard limits: the App cannot change workflows (no `workflows` permission) and cannot push to `main` (ruleset). The merger checks the fence again before merging.
- The reviewer and the merger never run the pull request's code in a job that holds a secret.
- The probe stubs Umami and requests nothing but the site; deweloperuch.pl is never contacted.
- Cost is bounded by the limits: per day at most 2 QA agent runs, 4 fixer runs (2 in progress × 2 attempts) and as many reviews, each with a turn limit and a job timeout. The status issue shows agent runs per day.

## Testing and proof

**Offline tests** (`tests/factory/`, part of `uv run pytest`):

- Canary pages under `tests/factory/fixtures/canary/`: one planted defect per detector (an overflowing block at 320 px, a button without a focus ring, low contrast, a console error, a dead anchor, a broken image, a target below 24 px, a scrollable block without keyboard access, a layout that differs in WebKit). The probe must report each one with its check id and nothing on `tests/factory/fixtures/clean/`.
- Fingerprints: the same defect across adjacent widths gives one fingerprint; two runs give the same fingerprint; different selectors give different fingerprints.
- Dedup: open, closed-invalid and closed-fixed cases.
- Merge policy: a truth table in which each condition alone fails with its own reason, plus the flaky exception and the revert exemption.
- Fence and copy guard: a changed text node, a changed Polish string in `js/`, a forbidden path, a weakened test, an oversized diff.
- Dispatch: the WIP limit and the sender check.
- The rendered issue and pull request bodies against saved snapshots.

**Workflows:** `actionlint` on the factory workflows; `dry_run` for QA writes the issue bodies to the job summary instead of GitHub.

**Acceptance, in order:**

1. QA with `dry_run` on the live site. It must report at least the stale deployment (`1f104d1` against `8b6ec7b`) and the flaky docs history test. The owner reads the list for false alarms.
2. QA for real: at most 3 issues.
3. The owner labels #45, #46 and the flaky-test issue `factory:ready`; three pull requests go through fixer, reviewer and merger in shadow mode.
4. Shadow period of about 10 pull requests, until the switch rule above is met.
5. The revert path once in shadow mode: QA dispatched with `simulate_failed_verify` opens a revert pull request, which the owner closes.

## Rollout

- **Phase 0 (owner, in the browser):** create and install the App from a manifest prepared in the repository; run `claude setup-token`; create the `factory` and `factory-agent` environments, one secret each; approve the ruleset on `main`, which is then created with `gh api`.
- **Phase 1:** probe, detectors, canaries, fingerprints; QA in `dry_run` (mode `dry`).
- **Phase 2:** QA filing issues, the evidence branch, dedup, flakes, the status issue.
- **Phase 3:** fixer, reviewer, merger in shadow mode; acceptance steps 3–5.
- **Phase 4:** `FACTORY_MODE=auto` when the agreement rule is met.

## New files

```
.github/factory/config.json          limits, matrix, fence, models
.github/factory/prompts/{qa,fixer,reviewer}.md
.github/factory/app-manifest.json    for creating the App
.github/workflows/factory-{qa,fix,review,merge}.yml
tools/factory/probe.py               matrix, detectors, findings.json, screenshots, --reproduce
tools/factory/detectors.py           in-page scripts shared with tests/e2e/test_pages.py
tools/factory/fingerprint.py
tools/factory/issues.py              dedup, issue bodies, filing
tools/factory/evidence.py            push to factory-evidence, return links
tools/factory/deployed.py            deployed SHA from the Deployments API
tools/factory/flakes.py              CI history scan
tools/factory/dispatch.py            WIP-limited promotion, sender check
tools/factory/fence.py               paths, size, weakened tests, copy guard
tools/factory/merge_policy.py
tools/factory/status.py              status issue and shadow agreement
tools/factory/vendor/                axe-core and its licence
tests/factory/                       offline tests and fixtures
```

`tools/factory/` is not published: `tools/package_site.py` packages only the pages, `css/`, `js/` and `assets/`.

## Out of scope

- Copy and design changes: copy comes from the spec, design from the reference; such findings go to `factory:needs-human` or stay suggestions.
- Performance: Lighthouse already gates every commit and measures the live site after each deployment.
- The deweloperuch-mcp package, the server data sync (`sync.yml`) and the demo data.
- Pull requests not opened by the factory.

## Risks to settle during planning

- `claude-code-action` inside a container job: confirm it runs in the pinned Playwright image. Fallback: run on the plain runner and render baselines with `docker run` of the same image, as `docs/visual-tests.md` describes.
- An approval by `github-actions[bot]` satisfying the ruleset: confirm with a test pull request in phase 0. Fallback: a second App for the reviewer.
- The `engine-diff` threshold: calibrate on the current site in phase 1 so the clean site reports nothing.
- Growth of the evidence branch: if it passes 200 MB, squash it and keep only images linked from open issues and pull requests.
- The shadow period may take weeks at a low defect rate; the switch rule waits for 10 decided pull requests regardless.
