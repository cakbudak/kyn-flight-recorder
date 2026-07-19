# Verification evidence

The active evidence set proves **Kyn.ist Agent Studio**: user-defined Actions and
Flows, version-pinned Agent resources, webhook activation, executable Runs,
Human approval, linked reruns, and integrated bounded maintenance. Superseded Recorder artifacts are removed
from the active tree and remain recoverable in Git history.

## Deterministic full-stack browser proof

`browser/agent-studio-report.json` records the 24-check Chromium journey against
the real Python server, same-origin HTTP API, control plane, flat SQLite stores,
Action dispatcher, visual canvas, trigger path, approvals, integrated
maintenance, and browser UI. Only provider responses come from a deterministic
provider-shaped seam.

Expected screenshots:

- `browser/01-agent-studio.png`
- `browser/02-action-contract.png`
- `browser/02-visual-flow-builder.png`
- `browser/02-waiting-approval.png`
- `browser/03-run-evidence.png`
- `browser/04-repair-approved.png`
- `browser/05-repair-proven.png`
- `browser/06-mobile-studio.png`

## Public HTTPS + real-model proof

[`live/agent-studio-report.json`](live/agent-studio-report.json) runs the same
24-check browser journey through `https://buildweek.kyn.ist`: Cloudflare, the
same-origin proxy, persistent service, flat SQLite runtime, and actual OpenAI
Responses calls. The service has no operator key. The verifier enters the key
through Configuration, and Chromium is launched without `OPENAI_API_KEY` in its
environment.

The committed proof shows:

- a user-defined deterministic Flow runs without a key;
- a real GPT‑5.6 AI Action emits strict typed output;
- the graph pauses at Human approval with zero effects;
- approval resumes to exactly one sandbox effect;
- a linked child Run owns an independent valid event chain;
- Prompt, Skill, and Agent creation work in the browser;
- integrated maintenance proves its blocked-parent/successor-child outcome.

Screenshots:

- [`live/01-agent-studio.png`](live/01-agent-studio.png)
- [`live/02-action-contract.png`](live/02-action-contract.png)
- [`live/02-visual-flow-builder.png`](live/02-visual-flow-builder.png)
- [`live/02-waiting-approval.png`](live/02-waiting-approval.png)
- [`live/03-run-evidence.png`](live/03-run-evidence.png)
- [`live/04-repair-approved.png`](live/04-repair-approved.png)
- [`live/05-repair-proven.png`](live/05-repair-proven.png)
- [`live/06-mobile-studio.png`](live/06-mobile-studio.png)

## Reproduce

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/verify.py
npm ci
node scripts/browser_verify.mjs \
  --report evidence/browser/agent-studio-report.json \
  --artifacts evidence/browser

# Configured external service; the key is entered through the browser UI:
OPENAI_API_KEY=... node scripts/browser_verify.mjs \
  --base-url https://buildweek.kyn.ist \
  --report evidence/live/agent-studio-report.json \
  --artifacts evidence/live
```

## Sanitization

Committed reports contain statuses, safe IDs, counts, model usage metadata, and
limited browser/server diagnostics. They must not contain the API key, cookies,
authorization headers, raw model requests/responses, full prompts, database token
hashes, provider raw errors, or hidden reasoning.
