# Quality gates

Date: 2026-07-19

| Gate | Evidence | Status |
| --- | --- | --- |
| compose | seeded workspace contains 3 prompts, 3 skills, 3 agents, and 1 pinned flow | PASS |
| execute | real gpt-5.6 emits strict `inspect_release_policy` and `stage_release` calls | PASS |
| record | model summaries, receipts, effects, ordered events, and hashes persist in SQLite | PASS |
| diagnose | exact two-event ownership/candidate checks; foreign evidence negative test | PASS |
| repair | one allow-listed replace; unsafe path negative test | PASS |
| approve | proposal hash + revision + actor + reason + acknowledgement; stale/altered tests | PASS |
| rerun | linked v2 child completes; v1 stays blocked; effect counts are 0 → 1 | PASS |
| database | flat explicit tables; no Parts/Entities/Bricks/Frames/nodes/edges; immutable triggers | PASS |
| tool safety | static registry, exact arguments, unknown-tool rejection, idempotent sandbox effect | PASS |
| isolation | HttpOnly workspace cookie, same-origin mutation, cross-workspace 404, body limit | PASS |
| cost | turn/tool/output, per-workspace/address/global, and model concurrency bounds | PASS |
| browser | desktop + 390 px, reduced motion, named AX controls, no overflow/console/request failure | PASS |
| real model | identical 21-check Chromium journey against configured gpt-5.6 runtime | PASS |
| public HTTPS | deployment and public full-loop rerun | PENDING until current backend commit is deployed |
| assistive tech | physical screen-reader pass | NOT RUN |

## Reproduction

```bash
python3 scripts/verify.py
node scripts/browser_verify.mjs
```

The deterministic browser run uses a provider-shaped test seam but the real HTTP API,
control plane, SQLite store, tools, UI, and Chromium. The separate
[`evidence/real-model/closed-loop-report.json`](../evidence/real-model/closed-loop-report.json)
was produced by the same runner against live gpt-5.6 calls.

Green UI checks alone are not accepted as runtime proof. Database invariants, negative
contract tests, authoritative receipts, real-model compatibility, and the linked effect
comparison are all required.
