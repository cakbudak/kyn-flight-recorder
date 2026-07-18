# Clean-clone verification

Date: 2026-07-18  
Commit under test: `108b031`
Source: local Git clone with `--no-local` into a temporary directory outside the workspace

## Procedure

1. Clone the committed repository without hard-linking local objects.
2. Run `python3 scripts/verify.py` inside the clone.
3. Run `node scripts/browser_verify.mjs` inside the clone.
4. Require an empty `git status --short` after verification.

## Result

| Gate | Result |
| --- | --- |
| Python server/static/GPT evidence contract tests | 28/28 PASS |
| JavaScript schema/state-machine tests | 25/25 PASS |
| Chromium desktop/mobile journey | 38/38 PASS |
| First meaningful render in rehearsal | 138.2 ms |
| Clone worktree after verification | clean |

Verdict: **PASS**. The committed project can be cloned, verified, and exercised
without package installation, build, migration, account, secret, or the parent
Kynist repository.

An earlier rehearsal of the preceding fix commit exposed an intermittent delayed
mobile viewport reflow. That failed proof was corrected forward in `108b031`; the
table above records the clean rerun of that exact correction commit.

This proves the local Git artifact. The final submission checklist separately
requires the same rehearsal from the selected remote URL after publication.
