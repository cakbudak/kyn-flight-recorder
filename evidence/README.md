# Verification evidence

The active evidence set proves **Kyn.ist Agent Studio**: user-defined Actions and
Flows, version-pinned Agent resources, webhook activation, executable Runs,
Human approval, linked reruns, evidence-bound completion, dead-end ratification,
controlled model comparison, integrated bounded maintenance, and evidence-bound
Skill distillation. Superseded
Recorder artifacts are removed from the active tree and remain recoverable in
Git history.

## Deterministic full-stack browser proof

`browser/agent-studio-report.json` records the 45-check Chromium journey against
the real Python server, same-origin HTTP API, control plane, flat SQLite stores,
Action dispatcher, visual canvas, trigger path, approvals, integrated
maintenance, and browser UI. Only provider responses come from a deterministic
provider-shaped seam.

Expected screenshots:

- `browser/01-onboarding.png`
- `browser/02-flow-studio.png`
- `browser/03-action-outputs.png`
- `browser/04-ai-stack.png`
- `browser/05-waiting-approval.png`
- `browser/06-run-evidence.png`
- `browser/07-maintenance-proof.png`
- `browser/08-mobile-workbench.png`
- `browser/09-dead-end-panel.png`
- `browser/10-brake-refusal.png`
- `browser/11-publish-advisory.png`
- `browser/12-workspace-principles.png`
- `browser/13-comparison-controlled.png`
- `browser/14-comparison-unusable.png`
- `browser/14-completion-contract-authoring.png`
- `browser/15-completion-refused.png`
- `browser/16-completion-admitted.png`
- `browser/17-forge-quarantine.png`
- `browser/18-forge-promoted.png`

## Public HTTPS + real-model proof

[`live/agent-studio-report.json`](live/agent-studio-report.json) runs the same
browser journey through `https://buildweek.kyn.ist`: Cloudflare, the
same-origin proxy, persistent service, flat SQLite runtime, and actual OpenAI
Responses calls. The service has no operator key. The verifier enters the key
through Settings, and Chromium is launched without `OPENAI_API_KEY` in its
environment.

The committed proof shows:

- a user-defined deterministic Flow runs without a key;
- a real GPT‑5.6 AI Action emits strict typed output;
- the graph pauses at Human approval with zero effects;
- approval resumes to exactly one sandbox effect;
- a real GPT‑5.6 diagnostician explains only the code-owned failure candidate and cites Run-owned events;
- a linked child Run owns an independent valid event chain;
- Prompt, Skill, and Agent creation work in the browser;
- integrated maintenance proves its blocked-parent/successor-child outcome.
- the Completion Contract editor publishes an immutable Judge/promise/site pin;
- an unsupported completion is refused while the same pinned Flow version is
  admitted when its declared work exists;
- each comparison carries a pre-I/O expected sibling manifest and refuses a
  provider model alias rather than presenting it as a controlled result.
- one completed model Step is distilled by a different logical Agent into an
  authority-free quarantine, passes eight deterministic provenance gates, and
  is Human-promoted into immutable Skill v1 without changing an Agent or Flow.

Screenshots:

- [`live/01-onboarding.png`](live/01-onboarding.png)
- [`live/02-flow-studio.png`](live/02-flow-studio.png)
- [`live/03-action-outputs.png`](live/03-action-outputs.png)
- [`live/04-ai-stack.png`](live/04-ai-stack.png)
- [`live/05-waiting-approval.png`](live/05-waiting-approval.png)
- [`live/06-run-evidence.png`](live/06-run-evidence.png)
- [`live/07-maintenance-proof.png`](live/07-maintenance-proof.png)
- [`live/08-mobile-workbench.png`](live/08-mobile-workbench.png)
- [`live/09-dead-end-panel.png`](live/09-dead-end-panel.png)
- [`live/10-brake-refusal.png`](live/10-brake-refusal.png)
- [`live/11-publish-advisory.png`](live/11-publish-advisory.png)
- [`live/12-workspace-principles.png`](live/12-workspace-principles.png)
- [`live/13-comparison-controlled.png`](live/13-comparison-controlled.png)
- [`live/14-comparison-unusable.png`](live/14-comparison-unusable.png)
- [`live/14-completion-contract-authoring.png`](live/14-completion-contract-authoring.png)
- [`live/15-completion-refused.png`](live/15-completion-refused.png)
- [`live/16-completion-admitted.png`](live/16-completion-admitted.png)
- [`live/17-forge-quarantine.png`](live/17-forge-quarantine.png)
- [`live/18-forge-promoted.png`](live/18-forge-promoted.png)

## Maximum-graph release-host proof

[`performance-report.json`](performance-report.json) records twenty complete
deterministic Runs through all 64 supported nodes plus thirty snapshots of the
accumulated workspace. Every Run owns 64 completed Steps, 197 valid hash-linked
events, and zero provider calls. The current measured values are recorded in the
report rather than copied as an unauditable benchmark claim.

[`editor-performance-report.json`](editor-performance-report.json) records the
same 64-node/63-edge graph in Chromium with 128 independently addressable source
handles, bounded render/Fit View latency, zero overflow, and no page or request
errors. The corresponding full-workbench image is
[`editor-performance-64-node.png`](editor-performance-64-node.png).

## Reproduce

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/verify.py
npm ci
node scripts/browser_verify.mjs \
  --report evidence/browser/agent-studio-report.json \
  --artifacts evidence/browser

.venv/bin/python scripts/performance_verify.py \
  --report evidence/performance-report.json
node scripts/editor_load_verify.mjs \
  --report evidence/editor-performance-report.json \
  --screenshot evidence/editor-performance-64-node.png

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
