# Kyn.ist Agent Studio

**Define typed Actions, connect AI through versioned Agents, Prompts, and Skills,
compose executable Flows, and operate every Run from authoritative evidence.**

**[Open the live Studio](https://buildweek.kyn.ist/app/)** ·
**[Inspect the source](https://github.com/cakbudak/kyn-agent-studio)**

**Model text is data. It is never authority.**

An Agent here can choose an Action, but the Skill grant, the exact Action
version, the strict schema, the runtime validation, and the effect policy decide
whether anything happens. A failed Run cannot be edited into a success. A repair
cannot rewrite its parent. And a mistake made three times independently stops
being available.

Agent Studio is a standalone OpenAI Build Week 2026 cut of one discipline inside
Kyn.ist: its agent execution, ratification, and maintenance contracts. It is a
configurable workflow product—not a prescribed click-through and not a frontend
simulation.

## What you can build

- **Define Actions** with strict input/output JSON Schemas and up to twelve named
  outcomes. Built-in execution kinds are AI, template, transform, delay,
  condition, multi-branch router, assertion, Human approval, and an idempotent
  workspace data store.
- **Build Flows visually** on a full node canvas. Drag pinned Action, Agent, or
  published Flow versions, connect exact outcome ports, map
  input/literal/predecessor data, set retry and error policy, then publish an
  immutable successor version.
- Define what **“finished” actually means** for a Flow. Up to eight acceptance
  promises pin admissible evidence kinds and exact graph sites, plus an
  independent versioned Goal-Judge. The model may nominate anchors and explain
  its claim; only deterministic resolution against Run-owned Steps, receipts,
  approvals, and effects may admit completion.
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
- Watch the runtime **ratify its own dead ends**. A structural failure recurring
  across three independent Runs becomes `canonical` and is refused before a
  fourth Run is created—deterministically, with citations, and with no model in
  the loop.
- See the same evidence **distilled into a stated principle** when three
  *different* Flows fail the same declared way. A principle only ever advises:
  it appears while you are authoring, publishing still succeeds, and the brake
  remains the only thing that refuses anything. Warn early, refuse late.
- Close the learning loop in the **Capability Forge**. Select one completed,
  ledger-verified model Step; a pinned Agent from a different logical Agent
  resource proposes a strict,
  cited behavioral Skill candidate. Code replays eight provenance, integrity,
  independence, fingerprint, and zero-authority gates. Only an acknowledged
  human decision may publish the exact instructions as an immutable,
  authority-free Skill v1. No Agent or Flow changes silently.
- Run a **controlled cross-model sweep**. One immutable pinned Flow version, one
  input, several models. Before provider I/O, the complete expected model ×
  repetition × Run set is committed as a hash-linked manifest; every sibling
  then pins a byte-identical `flow_version_id`. An incomplete, rewritten, or
  silently aliased sweep is unusable rather than presentable. The question is
  whether the scaffolding behaves the same on every brain, not which brain is
  best.
- Work in **light or dark**. The theme follows your system by default and the
  choice is yours for the tab.

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
4. Add a webhook trigger and invoke its URL (the secret is shown once; the URL itself stays usable), or start the Flow manually.
   The Run is pinned before execution and becomes visible while the bounded worker
   is active.
5. Open **Settings**, paste your own OpenAI API key, and save it for this
   browser tab.
6. Start `Agent-reviewed launch`. The official OpenAI SDK executes its pinned
   Agent/Prompt/Skill and the Run pauses at Human approval.
7. Inspect Steps, model calls, receipts, and events. Approve the pending request;
   the graph resumes and writes exactly one idempotent SQLite sandbox effect.
8. Open **Capability Forge**, select that completed model Step, choose an
   independent distiller Agent, and create a quarantined candidate. Run the 8/8
   provenance gate, then human-promote it to Skill v1. The source Run and every
   Agent remain unchanged.
9. Edit the Flow on canvas and publish v2. Existing Runs still render and retain
   their exact v1 graph.
10. Create a Data Store Action with `write_enabled:false`, put it in a Flow, and
   run it. From that blocked Run, execute Diagnose → Propose → Approve → Proof.
   The parent stays blocked with zero effects; its linked child runs Flow v2 and
   commits exactly one effect.
11. Open `Contracted evidence publication` in **Flow Studio**. Add an acceptance
    promise, choose its evidence kind and eligible graph site, and publish the
    successor. Run the same pinned version once through the branch that skips
    the declared write and once through the branch that reaches it: the first is
    refused at the stop seam, the second is admitted with resolved anchors.
12. Open **Comparisons**. The control panel shows the pre-I/O sibling manifest,
    immutable Flow/input fingerprints, the model actually returned by the
    provider, and every variable this surface does not claim to control.

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

### Completion plane

A Flow version may pin up to eight acceptance criteria. Each criterion states an
observable promise, one closed evidence kind (`step`, `receipt`, `approval`, or
`effect`), and one or more graph sites capable of minting it. Publication
refuses impossible contracts and refuses a Goal-Judge Agent that the judged
graph itself casts, including through nested subflows.

At the terminal seam, the pinned Judge receives its immutable Agent, Prompt, and
Skill instructions plus a redacted view of the Run's actual evidence, bounded to
96 KiB before provider I/O.
Its assessment, reasons, and anchor nominations are recorded explicitly as a
**non-authoritative model claim**. Code then resolves every nominated ID against
the Run's own records, declared kind, declared site, and successful state. If
any promise carries no surviving anchor, the Run records
`completion_unevidenced` and never enters `completed`. A Flow with no declared
criteria takes the ordinary completion path and makes zero Judge calls.

### Maintenance plane

The Studio derives a deterministic causal candidate from authoritative receipts,
lets a pinned diagnostician explain only that candidate and its owned event IDs,
validates one allow-listed repair operation, and applies it only through a human
hash/revision compare-and-swap. Applying creates immutable Action and Flow
successors. A linked proof child—not model prose—establishes the changed outcome.

### Ratification plane

Most agent systems have a memory of what they did. This one has a memory of what
did not work, and that memory has veto power.

When a Run terminates blocked or failed **in a ratifiable fault class**, the
runtime mints append-only evidence of the exact approach that failed—the pinned
Flow version, the node, the error code, and a normalized fault detail. Nothing
increments a mutable counter. `ratification_state` is **derived** by counting
distinct citing Runs:

```text
1 independent Run   → proposed    recorded; execution unaffected
2 independent Runs  → confirmed   visible in the Run surface
3 independent Runs  → canonical   check_brake refuses the Flow version
```

#### What is allowed to ratify

Counting is only half the rule. Only a **structural** defect may be counted—one
where the reason for the failure is a property of the pinned definition, so
repeating that definition cannot succeed whatever data arrives. The membership
rule is a declared table (`RATIFIABLE_FAULTS` and `NON_RATIFIABLE_FAULTS` in
`backend/contracts.py`), exposed on every `check_brake` verdict as
`fault_classes` so it can be audited rather than inferred:

```text
ratifies      a Data Store Action whose pinned `write_enabled` policy denies
              its own declared bounded write
never         an assertion rejecting bad input — the gate doing its job; its
              message is static, so distinct bad inputs share one fingerprint
never         a transient provider fault — a property of the moment, not the
              path, and already a retry-policy concern
never         a schema rejection of this Run's data, an inherited subflow
              refusal, or anything the table does not name
```

Anything unrecognised **fails closed and does not ratify**. A brake that fires
wrongly is worse than one that fires rarely.

#### Scope of the refusal

At `canonical`, a further Run of that **pinned Flow version** is refused *before
it is created*: no Run row, no Step, no event, no effect. The refusal cites the
prior Run IDs, and every citation resolves to a hash-linked event that already
existed.

The scope is the Flow version, not a traversal path, and that is deliberate.
Which nodes a Run visits is decided by data that does not exist until the Run
executes, so no pre-execution check can know the path. Admitting the Run and
braking mid-traversal would forfeit the stronger guarantee—no Run row, no Step,
no effect—so a canonical dead end on any node of a version refuses every
candidate of that version.

No model participates in any part of this. It is a count over append-only rows,
so an Agent cannot argue its way past it.

#### Distilled principles

A dead end refuses one exact pinned path. A **principle** is the generalization:
when three *different* Flows fail the same declared way, the runtime states the
rule it has learned.

The quorum is distinct Flows, not distinct Runs. Repetition inside one Flow is
already the brake's job, and letting one Flow mint a workspace-wide rule would
let a single loud failure speak for everyone.

A principle **never refuses anything**. It surfaces while you are authoring—
publishing a matching Flow still succeeds, and the Flow still runs—because being
wrong there costs a reader two seconds instead of blocking real work. Warn early,
refuse late.

Its honest ceiling is stated in the product itself and derived from the shipped
vocabulary rather than asserted: the mechanism groups any declared predicate over
any executor kind, but `POLICY_MARKERS` currently recognises exactly one
predicate, so that is the whole of what this system can say. A failure carrying
no recognised predicate produces no signature and never distils, by construction.

#### Capability Forge

Principles generalize deterministic executor failures; they are not converted
into Skills. Doing so would attach model-facing prose to a configuration-level
predicate and pretend it changed behavior. The Forge starts somewhere stricter:
one completed model-backed Step in a Run whose full event ledger verifies.

Before provider I/O, code freezes the source Flow/Step/model-call pins, source
Agent and Skill fingerprints, validated input/output, terminal state, and a
bounded ledger excerpt. A different pinned Agent receives that envelope through
one tool-free, strict-schema Responses call and must cite only event IDs inside
it. The proposal is stored in quarantine with its own fingerprint and zero
tool/Action authority.

Qualification is deterministic and makes no model call. It replays the terminal
source, full ledger chain, model-Step ownership, pre-I/O source snapshot,
citation subset, candidate fingerprint, independent-Agent fence, and zero
authority delta. Passing proves provenance—not improved performance. A human may
then acknowledge the exact fingerprint and publish one immutable Skill v1. The
Skill is not attached anywhere automatically: an operator must create a successor
Agent and prove its outcome in a new Run. Rejection is append-only too; nothing
is erased to make the learning history look cleaner.

Publishing a repaired successor Flow version produces a new `flow_version_id`
and therefore a new fingerprint. Fixing the problem always clears the brake; only
repeating it unchanged is refused. The brake is a memory, not a trap.

A braked subflow does not escape its parent: the parent Run terminates `blocked`
with `brake_engaged`, its Step closes, and a `subflow.brake_engaged` event
carries the refusal's citations into the parent's own ledger. The parent proved
nothing new, so that inherited refusal never ratifies a second dead end.

### Comparison plane

Anyone can run a prompt against several models and show a table. Nothing in that
proves the comparison was fair.

A comparison here runs one immutable pinned Flow version across several models.
Before the first provider call, the runtime prepares the complete sibling set
and appends one hash-linked manifest naming every expected model, repetition,
and Run ID. Every sibling then pins a **byte-identical `flow_version_id`**, so
every Action, Agent, Prompt, Skill, schema and route is provably the same and
the only recorded delta is the model. A crash cannot shrink the requested
experiment into a smaller one that looks complete; a missing or altered sibling
makes the derived result unusable. The manifest plus version pinning is what
turns a table into a controlled experiment.

The claim is deliberately not a ranking. The headline is **invariance** — same
routed outcome, same terminal status, same guard behaviour across brains — with
token and latency spread as the footnote. Ablation shows what breaks when a
guard is removed; a sweep shows nothing breaks when the brain is swapped.

Varying the model is the one deliberate hole in "everything is pinned", so it is
contained: the override is settable only by the comparison command, must be in
the supported set, is written on the Run row *and* into the hash-linked chain
next to the pinned model it replaced, and marks the Run `relation_kind =
"comparison"`. A sweep is its own evidence class and can never be a baseline —
a baseline is model-pinned by definition.

Two gates keep it from being theatre.

**The model that actually answered is verified.** This is not hypothetical. On
the live provider, requesting `gpt-5.6` returns `gpt-5.6-sol`. Without that check
a sweep would compare a model against itself and report perfect agreement — the
best-looking and most worthless result the system could produce. A mismatch
marks the comparison unusable, and the surface says so above every number.

**Controls that are not enforced are not claimed.** Sampling controls are not
settable through this bounded invocation surface, so the payload names what is
enforced-and-verified separately from what is not controllable here — each with
its reason, as data rather than prose. Claiming an unenforced control is the
fastest way to make an honest experiment dishonest.

The instrument measures itself before it weighs anything. Repetitions of one
model hold everything constant, so whatever they disagree by is the harness, not
the brain: that spread is the noise band. The total model-call forecast includes
every requested repetition and is charged before the comparison begins.
Differences below it report as `within_noise`, never as findings. And where a
model disagrees with *itself* across its own repetitions, cross-model invariance
is not stated at all — picking the run that agreed would manufacture the result.

No dollar figure is printed. Tokens and latency are what the provider reports,
so tokens and latency are what this reports; a price table would be stale the
week it ships, and a wrong cost number is worse than none.

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
                → automation_dead_end_evidence
```

SQLite WAL and short `BEGIN IMMEDIATE` transactions serialize mutations. Database
triggers enforce immutable definition/evidence rows, legal Run and Step
transitions, terminal absorption, and optimistic revision fences.

## What this is a projection of

Agent Studio is a standalone cut of one discipline inside Kyn.ist: how agent work
is authorized, executed, evidenced, and maintained. It runs from a clean clone
with Python and a browser. Nothing here calls back to a private service.

The mechanisms are real, and each is a projection of a deeper substrate:

| Here | In the private stack |
| --- | --- |
| Ratification over repeated Runs | Ratification over a knowledge graph with constraint-trigger invariants, per-producer trust cells, and quorum distillation across independent sources |
| Skill grants pinning exact Action versions | A capability catalog with per-operation maturity and live-proof ratification gating |
| One attributable human approval | Risk-classed operations with separation of duties and crypto-shreddable decision reasons |
| A hash-linked event ledger | The same ledger joined to a causal projection and saved graph lenses over one structure graph |
| Authorable acceptance contracts with an independent Goal-Judge | Goal/stop conditions joined to task and drift gates over the full intelligence runtime |
| Pre-I/O cross-model manifests and invariance checks | The model-agnostic “Switch-the-Brain” evaluation axis in KynBench |

The public cut is bounded on purpose. The only general write-capable Action
appends an idempotent row to an isolated workspace sandbox; there is no shell,
filesystem, arbitrary HTTP, arbitrary MCP registration, production authority, or
tenant-wide secret store. That is the smallest environment in which real agent
behavior, authority, orchestration, observation, approval, ratification, and
maintenance can all be judged at once.

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
| `KYN_WORKSPACE_MODEL_CALL_LIMIT` | `24` (deployed: `36`, so a cross-model sweep fits) | recorded model-call budget per workspace |
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
POST /api/v1/hooks/:webhook-secret
GET  /api/v1/studio/runs/:id
POST /api/v1/studio/runs/:id:continue
POST /api/v1/studio/runs/:id:cancel
POST /api/v1/studio/approvals/:id/decisions
POST /api/v1/studio/runs/:id/reruns
POST /api/v1/studio/runs/:id/diagnoses
POST /api/v1/studio/diagnoses/:id/repairs
POST /api/v1/studio/repairs/:id/apply
POST /api/v1/studio/repairs/:id/proof
POST /api/v1/studio/skill-candidates
POST /api/v1/studio/skill-candidates/:id/qualifications
POST /api/v1/studio/skill-candidates/:id/promotion
POST /api/v1/studio/skill-candidates/:id/rejection
POST /api/v1/studio/flows/:id/comparisons
GET  /api/v1/studio/comparisons
GET  /api/v1/studio/comparisons/:id

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

Run the maximum supported 64-node release-host and Chromium load gates:

```bash
.venv/bin/python scripts/verify.py --performance
```

The committed load proof executes twenty complete 64-node Runs (197
hash-linked events each), snapshots the accumulated workspace thirty times,
renders the same 64-node/63-edge graph in Chromium, and exercises Fit View. On
the release host, complete deterministic Runs measured 404.844 ms p95 and
snapshots 246.661 ms p95—each below its declared threshold (2000 ms and 400 ms)
and without model calls, overflow, failed requests, or page errors.

Each Run projection recomputes its full event chain from event material—197
SHA-256 hashes on the maximum graph—rather than trusting that links merely join.
The measured release-host result includes that authoritative ledger verdict and
remains well inside both gates. See
[`evidence/performance-report.json`](evidence/performance-report.json) and
[`evidence/editor-performance-report.json`](evidence/editor-performance-report.json).

The browser verifier exercises the real same-origin HTTP and SQLite stack through
workspace creation; Action, Prompt, Skill, and Agent authoring; multi-output Flow
composition; deterministic execution; canvas successor publication; reusable
subflow execution; webhook activation; asynchronous AI execution;
approval/resume; live graph evidence; dead-end ratification and brake refusal;
authorable completion contracts with refuse/admit evidence; pre-I/O comparison
manifests; and integrated maintenance.
The same journey also distils a completed model Step through a different logical
Agent, proves all eight Capability Forge gates, and human-promotes an
authority-free Skill without changing any Agent or Flow.
Provider-shaped deterministic responses are used locally; the same journey can be
run against the deployed OpenAI-backed service. The current local journey passes
**45/45** checks, including keyboard focus containment, 3,430 rendered contrast
samples across all eleven workbenches in both themes, and a legible pannable 390 px
graph; see
[`evidence/browser/agent-studio-report.json`](evidence/browser/agent-studio-report.json).

The same journey passes **45/45** against the deployed public origin at
`https://buildweek.kyn.ist` with real model calls, real provider latency, and no
scripted responses; see
[`evidence/live/agent-studio-report.json`](evidence/live/agent-studio-report.json)
and the archived screenshots under `evidence/live/`. That proof includes the
real Capability Forge path. Its one refused HTTP response is the brake's own
asserted 409.

Prove the guards are load-bearing rather than taking the green suite on trust:

```bash
.venv/bin/python scripts/verify.py --ablation
```

Each ablation takes a product function's own source, deletes exactly one named
guard, and asserts a documented product-level violation becomes reachable. Nine
of eleven ablations are load-bearing. Two are reported **redundant** because the
same safety property remains independently enforced: the standalone terminal-
absorption trigger is covered by the transition-shape trigger, and the Judge's
early anti-fabrication check is covered again by deterministic anchor resolution.
The suite reports that rather than dressing either one up.

Ablation is test-local. No path reachable from `serve.py` or the HTTP API can
disable a guard; a public deployment ships no switch for its own authority gate.

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
