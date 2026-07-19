# Quality-gate matrix

Date: 2026-07-19
Standalone maturity: verified demo  
Submission maturity: repository/demo publication complete; video and Devpost steps remain

| Gate | Result | Proof | Honest boundary |
| --- | --- | --- | --- |
| G1 UX/accessibility | PASS | 38/38 local and public-origin Chromium checks; independent Playwright journey; five axe states with zero violations; keyboard, focus, reduced motion, 390 × 520 dialog stress | physical screen-reader pass not run |
| G2 contract | PASS | 25 schema/state-machine assertions; runtime imports the v1 JSON Schema, then enforces semantic invariants; invalid imports fail closed | arbitrary trace authenticity not proven |
| G3 security | PASS for demo scope | public CSP/headers, no HTML sinks, redaction, no writes, traversal/listing negatives, one-origin browser inventory | key-name redaction is not DLP |
| G4 data/privacy | PASS | synthetic fixture; memory-only import; session-only receipt; visible reset; no telemetry | explicit GPT review is an external call |
| G5 reliability | PASS | revision fence, exact transition, idempotency, terminal absorption, fixture-bound rehydrate | browser storage is not durable audit storage |
| G6 performance | PASS on reference | first meaningful render below 1 s; no runtime package/build/network dependency | not a broad device benchmark |
| G7 operations | PASS | HTTPS health endpoint; run/correlation/revision/source visible; append-only replay and receipt | synthetic evidence only; no live Kynist backend |
| G8 agent boundary | PASS | effect boundary and model/tool/approval/queue causality explicit; no model/tool runtime | no claim of live Kynist integration |
| G9 proof | PASS | 28 Python + 25 Node + 38 local + 38 public-origin browser checks; Playwright cross-check; screenshots, accessibility audit, and sanitized GPT-5.6 review | external review is bounded to the synthetic packet, not runtime proof |
| G10 release | PASS publicly | credential-free anonymous clone of `de9d0b6`: 28 Python + 25 Node + 38 browser checks and clean worktree; logged-out README and screenshot asset visible; versioned origin at `7f700ac` | final evidence-only successor commit gets one last anonymous rehearsal |

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
- `evidence/live-browser-verification.json`: the identical 38 checks against the
  public HTTPS origin.
- `evidence/live-playwright-verification.json`: independent visible-control
  Playwright cross-check against the public HTTPS origin.
- `evidence/screenshots/`: generated blocked, receipt, and mobile states.
- `evidence/accessibility-verification.md`: tools, states, contrast math, residual.
- `evidence/gpt-5.6-review.json`: sanitized structured GPT-5.6 result and usage.
- `evidence/gpt-5.6-review.md`: human-readable interpretation and boundary.
- `evidence/clean-clone-verification.md`: isolated local-clone rehearsal.
- Git history: new-work and Codex collaboration chronology.
