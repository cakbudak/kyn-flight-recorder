# Kyn.ist Agent Studio — product contract

Date frozen: 2026-07-19  
Maturity: `implemented; deterministic full-stack journey verified`

## Promise

Kyn.ist Agent Studio is a configurable public projection of Kyn's agent and
automation runtime. A visitor can:

1. define typed, versioned Actions;
2. define versioned Prompts, Skills, and Agents;
3. compose pinned Action/Agent nodes on a visual acyclic Flow canvas;
4. attach manual, secret-webhook, and interval triggers;
5. execute deterministic and OpenAI-backed Runs through a bounded worker;
6. inspect authoritative Steps, events, model calls, receipts, approvals, and
   effects;
7. pause and resume at an immutable Human approval;
8. rerun terminal work as a linked child; and
9. maintain any supported blocked Run through evidence → diagnosis → bounded
   repair → approval → successor → linked proof.

The seeded launch Flow is one editable use case. It is not a prescribed journey.

## Public boundary

This repository is not the whole Kyn.ist production stack. It is an independently
implemented public cut using one Python HTTP process, the official OpenAI Python
SDK, a flat SQLite database, bounded built-in Action executors, and a
dependency-free browser client.

It excludes Ainou, CE, Appiyon and Kynllm internals, Parts/Entities,
Bricks/Packs/Frames, the production graph/queue/connectors, arbitrary MCP, shell,
filesystem, arbitrary network access, and production-write authority.

## First-class definitions

### Action

An Action version declares kind, strict input schema, strict output schema,
configuration, effect level, optional Agent pin, and fingerprint. Direct graph
nodes and model-requested Actions use one invocation path.

### Prompt

A Prompt version is an immutable template with exact declared variables.
Rendering rejects missing, extra, or malformed variables.

### Skill

A Skill version combines instructions with exact authority: legacy local-tool
names and/or public Action version IDs. Database content cannot register code.

### Agent

An Agent version pins model, role instructions, one Prompt version, and bounded
Skill versions. Its effective Action set is derived from those pins.

### Flow

An Automation Flow version pins input schema, one start node, Action/Agent nodes,
canvas positions, explicit input mappings, retry/backoff/error settings, outcome
routes, and all transitive resource fingerprints. Graphs are bounded, reachable,
and acyclic.

## Run contract

- Input is validated before a Run advances.
- A Run pins one immutable Flow version before external I/O.
- Webhook and schedule bindings pin the Flow version active at trigger creation.
- Trigger enable/disable is guarded by an optimistic configuration revision.
- Each node attempt creates a durable Step.
- Each Action attempt creates a durable receipt.
- OpenAI calls record safe metadata and hashes, never raw credentials.
- Approval is a real non-terminal pause with one immutable attributable decision.
- `completed`, `blocked`, `failed`, and `cancelled` are absorbing.
- Rerun creates a linked Run with the same pinned Flow version unless a distinct
  successor is explicitly selected by a maintenance operation.
- Events are ordered and hash-linked per Run.

## OpenAI credential contract

- The visitor enters the key in the browser.
- The browser stores it in `sessionStorage` for the current tab.
- It is attached only to same-origin commands forecast to call a model.
- The server constructs an ephemeral official SDK client for that operation.
- `OPENAI_API_KEY` is not loaded from server environment or `.env`.
- The credential is never persisted, logged, rendered, or returned.
- Requests use Responses with `store=false`, bounded output, strict Structured
  Outputs, and strict custom functions where applicable.

## Action kinds

| Kind | Contract | Authority |
| --- | --- | --- |
| `ai` | execute pinned Agent/Prompt/Skills; optional strict Action calls | visitor's OpenAI account |
| `template` | deterministic declared-variable rendering | none |
| `transform` | declarative input/literal mapping into a strict output | none |
| `delay` | bounded 0–5000 ms pause and pass-through | none |
| `condition` | one typed comparison and explicit branch outcome | none |
| `assert` | block on one failed declared comparison | none |
| `approval` | pause and await immutable Human decision | human |
| `data_store` | append one idempotent workspace-local effect | local SQLite |

## Invariants

- Definition versions, events, model calls, receipts, approvals, and effects are
  immutable at the database layer.
- External I/O never occurs inside a SQLite write transaction.
- Model output is untrusted data; code owns validation, routing, authority, and
  effects.
- A Skill grants exact Action versions; invocation is rejected outside that
  intersection.
- Flow mappings can read only Run input, literals, or reachable predecessor Steps.
- Stale revisions and illegal state transitions fail without partial writes.
- Idempotency keys cannot duplicate a Run command, receipt, or sandbox effect.
- Workspace IDs never bypass opaque-cookie ownership checks.
- Secret-like payload fields are rejected or redacted before evidence persistence.

## Integrated maintenance contract

Maintenance is part of each supported failed Run, not a separate scripted demo:

```text
blocked/failed Action receipt
  → deterministic causal candidate
  → model explanation with exact owned event citations
  → one allowlisted Action repair proposal
  → human proposal-hash + Action/Flow revision fences
  → successor Action and Flow versions
  → linked proof Run with changed authoritative outcome
```

The model cannot invent evidence, apply its proposal, or rewrite the failed Run.

## Quality gates

| Gate | Acceptance criterion |
| --- | --- |
| define | Browser creates Action, Prompt, Skill, Agent, and Flow definitions |
| execute | Deterministic and real Responses-backed Flows produce validated Runs |
| observe | Steps, calls, receipts, events, approvals, and effects are inspectable |
| approve | Run pauses and resumes/blocks only from immutable Human command |
| trigger | webhook and interval bindings pin a definition and create real Runs |
| maintain | Run diagnosis is evidence-owned and repair is bounded/fenced |
| rerun | Child is linked, parent remains unchanged, idempotency prevents duplicates |
| safety | BYOK, same-origin, isolation, bounds, no arbitrary tools or secret persistence |
| database | Flat tables, immutability triggers, legal transitions, no private ontology |
| browser | Desktop/mobile/reduced-motion/accessibility/error/network assertions pass |
| live | Sanitized real-model journey passes through public HTTPS deployment |

## Build Week provenance

The forward-only Git history is the implementation chronology. The primary Codex
thread is `019f7621-5200-7400-9242-920cb718d09a`.

Official transport references:

- <https://developers.openai.com/api/docs/guides/function-calling>
- <https://developers.openai.com/api/docs/guides/structured-outputs>
- <https://developers.openai.com/api/reference/resources/responses/methods/create>
