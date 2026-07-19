# Verification evidence

The active evidence set proves **Kyn.ist Agent Studio**: user-defined Actions and
Flows, version-pinned Agent resources, executable Runs, Human approval, linked
reruns, and the bounded Repair Lab. Superseded Recorder artifacts are removed
from the active tree and remain recoverable in Git history.

## Deterministic full-stack browser proof

`browser/agent-studio-report.json` records the 21-check Chromium journey against
the real Python server, same-origin HTTP API, control plane, flat SQLite stores,
Action dispatcher, approvals, Repair Lab, and browser UI. Only provider responses
come from a deterministic provider-shaped seam.

Expected screenshots:

- `browser/01-agent-studio.png`
- `browser/02-waiting-approval.png`
- `browser/03-run-evidence.png`
- `browser/04-repair-proven.png`
- `browser/05-mobile-studio.png`

## Real-model proof

`real-model/agent-studio-report.json` runs the same browser journey against an
actual service that has no operator key. The verifier enters the key through the
Configuration UI, and Chromium itself is launched without `OPENAI_API_KEY` in its
environment.

The expected proof is:

- a user-defined deterministic Flow runs without a key;
- a real GPT‑5.6 AI Action emits strict typed output;
- the graph pauses at Human approval with zero effects;
- approval resumes to exactly one sandbox effect;
- a linked child Run owns an independent valid event chain;
- Prompt, Skill, and Agent creation work in the browser;
- Repair Lab proves its before/after outcome.

## Public HTTPS proof

`live/agent-studio-report.json` repeats the same checks through
`https://buildweek.kyn.ist`, including the edge proxy, nginx, persistent service,
Secure/HttpOnly workspace cookie, SQLite, and official OpenAI SDK.

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
