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

## Public HTTPS + real-model proof

[`live/agent-studio-report.json`](live/agent-studio-report.json) runs the same
21-check browser journey through `https://buildweek.kyn.ist`: Cloudflare, the
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
- Repair Lab proves its before/after outcome.

Screenshots:

- [`live/01-agent-studio.png`](live/01-agent-studio.png)
- [`live/02-waiting-approval.png`](live/02-waiting-approval.png)
- [`live/03-run-evidence.png`](live/03-run-evidence.png)
- [`live/04-repair-proven.png`](live/04-repair-proven.png)
- [`live/05-mobile-studio.png`](live/05-mobile-studio.png)
- [`live/06-action-builder.png`](live/06-action-builder.png)
- [`live/07-flow-builder.png`](live/07-flow-builder.png)

The last two frames are a separate visual audit of the live creation surfaces.
They show editable strict schemas/configuration and a Flow composer with pinned
Action-or-Agent nodes, explicit mappings, and addable outcome routes.

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
