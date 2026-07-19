# Kyn.ist Agent Studio

**Define typed Actions, connect AI through versioned Agents, Prompts, and Skills,
compose executable Flows, and operate every Run from authoritative evidence.**

**[Open the live Studio](https://buildweek.kyn.ist/app/)** ·
**[Inspect the source](https://github.com/cakbudak/kyn-agent-studio)**

Kyn.ist Agent Studio is a standalone OpenAI Build Week 2026 projection of one
part of Kyn.ist: its agent execution and maintenance discipline. It is a real,
configurable workflow product—not a prescribed click-through and not a frontend
simulation.

The Studio deliberately does **not** publish Kyn.ist's private Ainou layer,
Parts/Entities, Bricks/Packs/Frames, CE, production connectors, or internal data
model. Instead, it exposes a bounded flat SQLite derivation with enough real
functionality to build, run, inspect, approve, diagnose, repair, and rerun agent
work.

## What you can build

- **Define Actions** with strict input/output JSON Schemas and up to twelve named
  outcomes. Built-in execution kinds are AI, template, transform, delay,
  condition, multi-branch router, assertion, Human approval, and an idempotent
  workspace data store.
- **Build Flows visually** on a full node canvas. Drag pinned Action, Agent, or
  published Flow versions, connect exact outcome ports, map
  input/literal/predecessor data, set retry and error policy, then publish an
  immutable successor version.
- Attach real **webhook and interval triggers**. A trigger always pins the Flow
  version current when it is created. Model-backed trigger Runs wait safely for
  the visitor's browser-owned key instead of persisting a credential.
- Create **Versioned Agents, Prompts, and Skills**. Skills grant exact callable
  Action version IDs; model text cannot widen authority.
- **Operate Runs live** through the same graph, with current-node state, bounded
  attempts/backoff, cancellation, Steps, model calls, Action receipts,
  approvals, effects, and hash-linked events.
- Pause a live graph at a real Human approval, commit an attributable decision,
  then resume the same pinned Run.
- Rerun a terminal execution as a linked child without rewriting its parent.
- On any supported blocked Run, use the integrated **maintenance loop**:
  code-owned causal evidence → constrained Agent explanation → bounded repair →
  human revision fence → immutable Action/Flow successors → linked proof Run.

The seeded `Agent-reviewed launch` Flow is one editable example:

```text
AI Action → condition → Human approval → sandbox effect
                └ false → deterministic needs-work Action
```

You can ignore it and create a deterministic Flow, a new Agent stack, or your
own mixed graph from scratch.

## Three-minute judge path

1. Open the live Studio and create an isolated workspace.
2. Go to **Actions** and create a Template Action. Its schemas and configuration
   are visible and editable.
3. Go to **Flow Studio**, create a visual Flow, drag another capability onto the canvas,
   connect it, inspect typed mappings and retry policy, then publish v1.
4. Add a webhook trigger and invoke the one-time URL, or start the Flow manually.
   The Run is pinned before execution and becomes visible while the bounded worker
   is active.
5. Open **Settings**, paste your own OpenAI API key, and save it for this
   browser tab.
6. Start `Agent-reviewed launch`. The official OpenAI SDK executes its pinned
   Agent/Prompt/Skill and the Run pauses at Human approval.
7. Inspect Steps, model calls, receipts, and events. Approve the pending request;
   the graph resumes and writes exactly one idempotent SQLite sandbox effect.
8. Edit the Flow on canvas and publish v2. Existing Runs still render and retain
   their exact v1 graph.
9. Create a Data Store Action with `write_enabled:false`, put it in a Flow, and
   run it. From that blocked Run, execute Diagnose → Propose → Approve → Proof.
   The parent stays blocked with zero effects; its linked child runs Flow v2 and
   commits exactly one effect.

## Browser-owned OpenAI credential

The application has no operator-key fallback.

1. The visitor saves a key in browser `sessionStorage` for the current tab.
2. The browser attaches it only to a same-origin command forecast to call a model.
3. The server creates an ephemeral official `OpenAI` SDK client for that operation.
4. The key is never written to SQLite, events, receipts, responses, logs, or Git.
5. Only safe provider metadata—response ID, model, status, usage, hashes, and
   request ID—is committed.

The server ignores `OPENAI_API_KEY` from `.env`, even if the surrounding host has
one. Deterministic Actions and Flows never require a credential.

OpenAI's [API authentication guidance](https://developers.openai.com/api/reference/overview#authentication)
recommends keeping standard API keys out of browser code. This anonymous Build
Week BYOK mode is therefore an explicit visitor-owned trade-off: use a
restricted, temporary project key, never a production credential, and clear it
before sharing or leaving the tab.

## Runtime contract

### Definition plane

Actions, Automation Flows, Agents, Prompts, and Skills are versioned and
fingerprinted. A Flow version pins its complete transitive resource set. Version
rows are immutable at the SQLite layer.

### Execution plane

A Run is created with one fully pinned Flow version before worker/provider I/O,
then validates input, maps data into Steps, and invokes every graph capability
through the same Action path. Attempts are bounded to three with explicit retry
codes and backoff. Terminal states are absorbing. A rerun creates a linked new
Run; it never reopens its parent.

Events form a per-Run SHA-256 chain. Action receipts, model calls, approval
decisions, and sandbox effects are append-only. External OpenAI I/O never occurs
inside a SQLite write transaction.

### Authority plane

An AI Action pins an Agent. The Agent pins one Prompt and a set of Skills. Skills
can grant only exact Action versions whose kinds are safe for model invocation.
The runtime intersects those grants with its static public execution surface and
uses strict OpenAI function schemas. A model response is data, never authority.

### Maintenance plane

The Studio derives a deterministic causal candidate from authoritative receipts,
lets a pinned diagnostician explain only that candidate and its owned event IDs,
validates one allow-listed repair operation, and applies it only through a human
hash/revision compare-and-swap. Applying creates immutable Action and Flow
successors. A linked proof child—not model prose—establishes the changed outcome.

## Flat SQLite projection

This repository contains conventional product tables—not Kyn.ist's internal
ontology:

```text
workspaces
prompts → prompt_versions
skills → skill_versions
agents → agent_versions
actions → action_versions
automation_flows → automation_flow_versions
                 → automation_trigger_bindings
automation_runs → automation_run_steps
                → automation_events
                → automation_model_calls
                → automation_action_receipts
                → automation_approval_requests → automation_approval_decisions
                → automation_effects
                → automation_diagnoses → automation_repair_proposals
                                       → automation_repair_decisions
```

SQLite WAL and short `BEGIN IMMEDIATE` transactions serialize mutations. Database
triggers enforce immutable definition/evidence rows, legal Run and Step
transitions, terminal absorption, and optimistic revision fences.

## Deliberate public boundary

The only general write-capable Action appends an idempotent row to the isolated
workspace sandbox. The public runtime has no shell execution, filesystem access,
arbitrary HTTP, arbitrary MCP registration, production deployment authority, or
tenant-wide secret store.

This boundary is not a mock. It is the smallest safe environment in which real
agent behavior, authority, orchestration, observation, approval, and maintenance
can all be judged without open-sourcing the private Kyn.ist stack.

## Run locally

Requirements: Python 3.11+ and a modern browser.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python serve.py
```

Open <http://127.0.0.1:4173/app/>. Enter an OpenAI key in the browser Settings
view only when you want to execute a model-backed command.

Optional non-secret settings:

| Variable | Default | Purpose |
| --- | --- | --- |
| `OPENAI_MODEL` | `gpt-5.6` | allow-listed model used by seeded Agent versions |
| `KYN_DATABASE_PATH` | `var/kyn-agent-studio.sqlite3` | flat SQLite database path |
| `KYN_WORKSPACE_MODEL_CALL_LIMIT` | `24` | recorded model-call budget per workspace |
| `KYN_PUBLIC_MODEL_CALLS_PER_HOUR` | `120` | global public model-action forecast budget |

## Same-origin API

`serve.py` serves both the browser application and `/api/v1`. Public workspace
authority stays in an opaque hashed `HttpOnly`, `SameSite=Strict`,
`Secure`-on-HTTPS cookie. Mutations require matching Origin and Fetch Metadata.
Bodies, schemas, graph size, model turns, workspace usage, address/global usage,
and concurrent model actions are bounded.

Important Studio routes:

```text
POST /api/v1/studio/actions
POST /api/v1/studio/actions/:id/versions
POST /api/v1/studio/flows
POST /api/v1/studio/flows/:id/versions
POST /api/v1/studio/flows/:id/triggers
POST /api/v1/studio/triggers/:id/state
POST /api/v1/studio/flows/:id/runs:enqueue
POST /api/v1/hooks/:one-time-secret
GET  /api/v1/studio/runs/:id
POST /api/v1/studio/runs/:id:continue
POST /api/v1/studio/runs/:id:cancel
POST /api/v1/studio/approvals/:id/decisions
POST /api/v1/studio/runs/:id/reruns
POST /api/v1/studio/runs/:id/diagnoses
POST /api/v1/studio/diagnoses/:id/repairs
POST /api/v1/studio/repairs/:id/apply
POST /api/v1/studio/repairs/:id/proof

POST /api/v1/prompts
POST /api/v1/prompts/:id/versions
POST /api/v1/skills
POST /api/v1/skills/:id/versions
POST /api/v1/agents
POST /api/v1/agents/:id/versions
```

## Verification

Run the backend, HTTP, database-invariant, security, and browser-state contracts:

```bash
.venv/bin/python scripts/verify.py
```

Run the full product journey in Chromium:

```bash
node scripts/browser_verify.mjs \
  --report evidence/browser/agent-studio-report.json \
  --artifacts evidence/browser
```

The browser verifier exercises the real same-origin HTTP and SQLite stack through
workspace creation; Action, Prompt, Skill, and Agent authoring; multi-output Flow
composition; deterministic execution; canvas successor publication; reusable
subflow execution; webhook activation; asynchronous AI execution;
approval/resume; live graph evidence; and integrated maintenance.
Provider-shaped deterministic responses are used locally; the same journey can be
run against the deployed OpenAI-backed service. The committed
[`evidence/browser/agent-studio-report.json`](evidence/browser/agent-studio-report.json)
and [`evidence/live/agent-studio-report.json`](evidence/live/agent-studio-report.json)
are generated from one runner. The current local journey passes 30/30 checks; the public HTTPS report is
also 30/30 against the deployed official-SDK runtime and is archived with eight
screenshots under `evidence/live/`.

## Codex provenance

The primary Build Week Codex thread is:
`019f7621-5200-7400-9242-920cb718d09a`.

The repository preserves implementation and adversarial review as forward-only
history. No reset, history rewrite, or hidden source import was used.

## Repository map

```text
app/          compiled self-hosted browser assets (no CDN/runtime Node dependency)
src/          React workbench source and Flow canvas
backend/      flat stores, typed contracts, graph runtimes, tools, HTTP API
deploy/       hardened same-origin reverse proxy and service contracts
docs/         runtime, trust-boundary, product, and quality contracts
evidence/     sanitized browser and model proof
scripts/      verification and browser journey runners
submission/   Build Week submission material
tests/        runtime, HTTP, database, isolation, security, and UI contracts
serve.py      single composition root
```

MIT — see [LICENSE](LICENSE).
