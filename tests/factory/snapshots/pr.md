Refs #7

## Cause

The terminal block has a fixed width of 640 px.

## Change

Replace the fixed width with a max-width of 100%.

## Proof

Each new test ran on a worktree of the base with only its test file taken from the head, then on a worktree of the head.

| Test | Base `aaaaaaa` (must fail) | Head `bbbbbbb` (must pass) |
|---|---|---|
| `tests/e2e/test_terminal.py::test_terminal_fits` | failed: 1 failed in 0.40s | passed: 1 passed in 0.38s |

Full suite on the head (`uv run pytest --browser chromium -q`): passed, `214 passed in 96.12s`.

<details>
<summary>Output</summary>

Base, `tests/e2e/test_terminal.py::test_terminal_fits`

```text
E   AssertionError: scrollWidth 640 > clientWidth 375
1 failed in 0.40s
```

Head, `tests/e2e/test_terminal.py::test_terminal_fits`

```text
1 passed in 0.38s
```

</details>

## Before and after

| Cell | Before | After | Diff |
|---|---|---|---|
| `landing-chromium-375-default` | ![before](https://raw.test/prs/31/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb/before/landing-chromium-375-default.png) | ![after](https://raw.test/prs/31/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb/after/landing-chromium-375-default.png) | ![diff](https://raw.test/prs/31/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb/diff/landing-chromium-375-default.png) |

## Changed baselines

- `tests/visual/baselines/landing-mobile.png`: The prompt now wraps at 375 px.

## Changed files

- `css/site.css`
- `tests/e2e/test_terminal.py`
- `tests/visual/baselines/landing-mobile.png`

6 changed lines, not counting PNG files and the generated pages.

## Needs a human

The fence found changes a person must review before this merges:

- existing tests changed: tests/e2e/test_existing.py

## Attempt

Attempt 1 of 2.

<!-- factory:markers -->
<!-- factory:attempt=1 -->
<!-- factory:tests=WyJ0ZXN0cy9lMmUvdGVzdF90ZXJtaW5hbC5weTo6dGVzdF90ZXJtaW5hbF9maXRzIl0= -->
