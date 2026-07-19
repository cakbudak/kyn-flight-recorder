# Verification evidence

The active evidence set proves the real closed-loop runtime, not the superseded static demo.

## Deterministic full-stack browser proof

[`browser/closed-loop-report.json`](browser/closed-loop-report.json) records 21/21 passing
checks. It starts the real Python HTTP server, API, control plane, flat SQLite store, strict
tools, and UI. Only the provider responses are supplied by a deterministic provider-shaped
test seam.

Screenshots:

- [`browser/01-compose.png`](browser/01-compose.png)
- [`browser/02-blocked.png`](browser/02-blocked.png)
- [`browser/03-proven-repair.png`](browser/03-proven-repair.png)
- [`browser/04-mobile-proof.png`](browser/04-mobile-proof.png)

## Real gpt-5.6 proof

[`real-model/closed-loop-report.json`](real-model/closed-loop-report.json) is the identical
21-check Chromium journey against a server configured with the OpenAI Responses API and
gpt-5.6. It proves provider compatibility plus the complete result:

- root run: `blocked`, flow v1, zero effects;
- diagnosis: exact two owned evidence events;
- repair: one allow-listed patch at expected revision 1;
- approval: acknowledged human command;
- child run: `completed`, flow v2, one sandbox effect;
- both predecessor chains valid in the safe API projection;
- no console error, failed browser request, cross-origin browser runtime request, or mobile
  document overflow.

Screenshots:

- [`real-model/01-compose.png`](real-model/01-compose.png)
- [`real-model/02-blocked.png`](real-model/02-blocked.png)
- [`real-model/03-proven-repair.png`](real-model/03-proven-repair.png)
- [`real-model/04-mobile-proof.png`](real-model/04-mobile-proof.png)

## Reproduce

```bash
python3 scripts/verify.py --browser

# Configured deployment; invokes OpenAI and creates a new isolated lab:
node scripts/browser_verify.mjs --base-url https://buildweek.kyn.ist
```

## Sanitization

The committed reports contain safe UI assertions, ids, hashes, counts, statuses, and limited
Chromium diagnostics. They do not contain the OpenAI API key, cookies, raw provider request or
response bodies, full prompts, authorization headers, database token hashes, or hidden
reasoning.

The old static-fixture evidence was removed in a forward commit because it no longer
represented the submission architecture.
