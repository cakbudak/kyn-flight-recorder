# Quality-gate matrix

Date: 2026-07-18  
Standalone maturity: verified demo  
Submission maturity: blocked only on external GPT-5.6 execution, publication,
Codex feedback ID, and video/upload steps

| Gate | Result | Proof | Honest boundary |
| --- | --- | --- | --- |
| G1 UX/accessibility | PASS | 30/30 Chromium checks; four axe states with zero violations; keyboard, focus, reduced motion, 390 px | physical screen-reader pass not run |
| G2 contract | PASS | 22 pure state-machine assertions; v1 structural + semantic contract; invalid imports fail closed | arbitrary trace authenticity not proven |
| G3 security | PASS for demo scope | CSP/headers, no HTML sinks, redaction, no writes, traversal/listing negatives, local network inventory | key-name redaction is not DLP |
| G4 data/privacy | PASS | synthetic fixture; memory-only import; session-only receipt; visible reset; no telemetry | explicit GPT review is an external call |
| G5 reliability | PASS | revision fence, exact transition, idempotency, terminal absorption, fixture-bound rehydrate | browser storage is not durable audit storage |
| G6 performance | PASS on reference | first meaningful render 459.7 ms; no runtime package/build/network dependency | not a broad device benchmark |
| G7 operations | PASS | run/correlation/revision/source visible; append-only replay and receipt | local synthetic evidence only |
| G8 agent boundary | PASS | effect boundary and model/tool/approval/queue causality explicit; no model/tool runtime | no claim of live Kynist integration |
| G9 proof | PASS | 24 Python + 22 Node + 30 browser checks; screenshots and accessibility audit | GPT API result pending |
| G10 release | PENDING clean clone | one-command entry, MIT license, forward-only commits, no install | final clone rehearsal follows documentation commit |

## Reproduce

```bash
python3 scripts/verify.py
node scripts/browser_verify.mjs
python3 scripts/gpt56_review.py --dry-run
```

The first command has no third-party dependencies beyond Node for the JavaScript
suite. The browser command additionally needs Node 20+ and a Chromium-family
binary. The GPT dry run has no network access; the non-dry run is a distinct
submission evidence gate and requires `OPENAI_API_KEY`.

## Evidence inventory

- `evidence/browser-verification.json`: named browser assertions and timing.
- `evidence/screenshots/`: generated blocked, receipt, and mobile states.
- `evidence/accessibility-verification.md`: tools, states, contrast math, residual.
- `evidence/gpt-5.6-review.pending.md`: exact status of external model proof.
- Git history: new-work and Codex collaboration chronology.
