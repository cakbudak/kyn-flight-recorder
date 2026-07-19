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

- **Define Actions** with strict input/output JSON Schemas. Built-in execution
  kinds are AI, template, condition, Human approval, and idempotent sandbox
  effect.
- **Build Flows** as explicit acyclic graphs of pinned Action and Agent versions.
  Every node declares input mappings; every branch declares an outcome route.
- Create **Versioned Agents, Prompts, and Skills**. Skills grant exact callable
  Action version IDs; model text cannot widen authority.
- **Observe Runs** through Steps, model calls, Action receipts, approvals,
  effects, and hash-linked events.
- Pause a live graph at a real Human approval, commit an attributable decision,
  then resume the same pinned Run.
- Rerun a terminal execution as a linked child without rewriting its parent.
- Use the **Repair Lab** to execute a complete failure → evidence-owned diagnosis
  → bounded repair → human revision fence → child rerun proof loop.

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
3. Go to **Flows**, create a Flow, select the Action version, and map the Flow
   input into its node.
4. Start the deterministic Flow. It runs without an OpenAI credential and
   produces a validated output plus immutable receipt and event chain.
5. Open **Configuration**, paste your own OpenAI API key, and save it for this
   browser tab.
6. Start `Agent-reviewed launch`. The official OpenAI SDK executes its pinned
   Agent/Prompt/Skill and the Run pauses at Human approval.
7. Inspect Steps, model calls, receipts, and events. Approve the pending request;
   the graph resumes and writes exactly one idempotent SQLite sandbox effect.
8. Rerun it. The child pins the same Flow version and keeps the parent intact.
9. Open **Repair Lab** for the deeper closed loop: real tool denial, cited causal
   diagnosis, bounded repair proposal, revision-fenced approval, and linked proof.

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

## Runtime contract

### Definition plane

Actions, Automation Flows, Agents, Prompts, and Skills are versioned and
fingerprinted. A Flow version pins its complete transitive resource set. Version
rows are immutable at the SQLite layer.

### Execution plane

A Run pins one Flow version, validates input, maps data into Steps, and invokes
every graph capability through the same Action path. Terminal states are
absorbing. A retry or rerun creates a linked new Run.

Events form a per-Run SHA-256 chain. Action receipts, model calls, approval
decisions, and sandbox effects are append-only. External OpenAI I/O never occurs
inside a SQLite write transaction.

### Authority plane

An AI Action pins an Agent. The Agent pins one Prompt and a set of Skills. Skills
can grant only exact Action versions whose kinds are safe for model invocation.
The runtime intersects those grants with its static public execution surface and
uses strict OpenAI function schemas. A model response is data, never authority.

### Maintenance plane

The general Studio preserves failed Runs and supports linked reruns. The included
Repair Lab additionally derives a deterministic candidate from authoritative
receipts, requires the diagnostician to cite the complete owned evidence set,
validates one allow-listed repair operation, and applies it only through a human
hash/revision compare-and-swap.

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
automation_runs → automation_run_steps
                → automation_events
                → automation_model_calls
                → automation_action_receipts
                → automation_approval_requests → automation_approval_decisions
                → automation_effects

Repair Lab:
flows → flow_versions → runs → events | model_calls | tool_receipts
                              → diagnoses → repairs → repair_approvals
                              → sandbox_releases
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

Open <http://127.0.0.1:4173/app/>. Enter an OpenAI key in the browser
Configuration view only when you want to execute a model-backed command.

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
POST /api/v1/studio/flows
POST /api/v1/studio/flows/:id/runs
GET  /api/v1/studio/runs/:id
POST /api/v1/studio/approvals/:id/decisions
POST /api/v1/studio/runs/:id/reruns

POST /api/v1/prompts
POST /api/v1/skills
POST /api/v1/agents
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
workspace creation, Action definition, Flow composition, deterministic execution,
AI execution, approval/resume, evidence inspection, linked rerun, and Repair Lab.
Provider-shaped deterministic responses are used locally; the same journey can be
run against the deployed OpenAI-backed service. The committed
[`evidence/browser/agent-studio-report.json`](evidence/browser/agent-studio-report.json)
and [`evidence/live/agent-studio-report.json`](evidence/live/agent-studio-report.json)
both pass 21/21; the latter is the public HTTPS journey with real GPT-5.6 calls.

## Codex provenance

The primary Build Week Codex thread is:
`019f7621-5200-7400-9242-920cb718d09a`.

The repository preserves implementation and adversarial review as forward-only
history. No reset, history rewrite, or hidden source import was used.

## Repository map

```text
app/          dependency-free Agent Studio browser client
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
