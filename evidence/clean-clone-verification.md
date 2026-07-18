# Clean-clone verification

Date: 2026-07-18  
Commit under test: `78c9b77`  
Source: local Git clone with `--no-local` into an automatically deleted temporary directory

## Procedure

1. Clone the committed repository without hard-linking local objects.
2. Run `python3 scripts/verify.py` inside the clone.
3. Run `node scripts/browser_verify.mjs` inside the clone.
4. Require an empty `git status --short` after verification.

## Result

| Gate | Result |
| --- | --- |
| Python server/static/GPT evidence contract tests | 25/25 PASS |
| JavaScript state-machine tests | 22/22 PASS |
| Chromium desktop/mobile journey | 30/30 PASS |
| First meaningful render in rehearsal | 150.7 ms |
| Clone worktree after verification | clean |

Verdict: **PASS**. The committed project can be cloned, verified, and exercised
without package installation, build, migration, account, secret, or the parent
Kynist repository.

This proves the local Git artifact. The final submission checklist separately
requires the same rehearsal from the selected remote URL after publication.
