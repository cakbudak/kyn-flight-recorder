# Kyn.ist Agent Studio — product contract

Date frozen: 2026-07-21
Maturity: `implemented; deterministic full-stack journey verified`

## Promise

Kyn.ist Agent Studio is a configurable public projection of Kyn's agent and
automation runtime. A visitor can:

1. define typed, versioned Actions;
2. define versioned Prompts, Skills, and Agents;
3. import immutable Knowledge and retrieve exact cited passages through
   SmartRead or deterministic search;
4. compose pinned Action, Agent, reusable Flow, and generic parallel fan-out
   nodes on a visual acyclic canvas with arbitrary named outcomes;
5. generate an editable multi-agent BoardRoom with code-owned quorum and
   downstream Human authority;
6. attach manual, secret-webhook, and interval triggers;
7. execute deterministic and OpenAI-backed Runs through a bounded worker;
8. inspect authoritative parent/member Steps, events, model calls, receipts,
   approvals, effects, barrier results, and dissent;
9. pause and resume at an immutable Human approval;
10. rerun terminal work as a linked child;
11. declare evidence-bound completion criteria with an independent Goal-Judge;
12. ratify repeated structural dead ends and distil cross-Flow advisories;
13. compare one pinned scaffold across models from a pre-I/O sibling manifest;
14. distil a completed model Step into a quarantined, provenance-qualified,
    human-promoted authority-free Skill;
15. promote exact cited Run evidence into governed, provenance-bearing Memory;
    and
16. maintain any supported blocked Run through evidence → diagnosis → bounded
    repair → approval → successor → linked proof.

The seeded launch Flow is one editable use case. It is not a prescribed journey.

## Public boundary

This repository is not the whole Kyn.ist production stack. It is an independently
implemented public cut using one Python HTTP process, the official OpenAI Python
SDK, a flat SQLite database, bounded built-in Action executors, and compiled
self-hosted React workbench assets.

It excludes Ainou, CE, Appiyon and Kynllm internals, Parts/Entities,
Bricks/Packs/Frames, the production graph/queue/connectors, arbitrary MCP, shell,
filesystem, arbitrary network access, and production-write authority.

## First-class definitions

### Action

An Action version declares kind, strict input schema, strict output schema, one
to twelve named outcomes, configuration, effect level, optional Agent pin, and
fingerprint. Direct graph nodes and model-requested Actions use one invocation
path.

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

An Automation Flow version pins input/output schemas, public outcomes, one start
node, Action/Agent/Flow nodes, canvas positions, explicit input mappings,
retry/backoff/error settings, outcome routes, optional acceptance criteria, one
independent Goal-Judge Agent version, and all transitive resource fingerprints.
Graphs are bounded, reachable, and acyclic. A Flow node creates a linked child
Run instead of flattening its evidence into the parent.

A `fan_out` node pins two to eight distinct Action, Agent, or Flow members with
one identical mapped input contract plus an all/quorum barrier, affirmative
value set, and isolate/fail-fast member error policy. Its code-derived output
contains every member record, completed/failed counts, convergence, and
dissenting-member IDs.

### Knowledge and Memory

Knowledge source versions and their passages are immutable and fingerprinted.
SmartRead accepts only a source-version ID and one bounded mode; it never accepts
a path or URL. Memory candidates are append-only quarantined proposals over
exact events from one completed, ledger-verified Run. Qualification is
deterministic; promotion/rejection requires a Human acknowledgement of the exact
candidate fingerprint. Only active promoted Memory versions enter recall.

## Run contract

- Input is validated before a Run advances.
- A Run pins one immutable Flow version before external I/O.
- Webhook and schedule bindings pin the Flow version active at trigger creation.
- Trigger enable/disable is guarded by an optimistic configuration revision.
- Each node attempt creates a durable Step.
- Each fan-out member creates a durable child Step linked to its parent fan-out
  Step; each worker uses its own operation session.
- Each Action attempt creates a durable receipt.
- OpenAI calls record safe metadata and hashes, never raw credentials.
- Approval is a real non-terminal pause with one immutable attributable decision.
- `completed`, `blocked`, `failed`, and `cancelled` are absorbing.
- Rerun creates a linked Run with the same pinned Flow version unless a distinct
  successor is explicitly selected by a maintenance operation.
- Subflow execution creates a linked child Run with a parent Step and shared
  correlation ID; a terminal child outcome resumes or fails the waiting parent.
- A declared completion contract is checked at the terminal seam. The Judge's
  semantic assessment is retained as a non-authoritative claim; only anchors
  resolved against Run-owned records of the declared kind, site, and state may
  carry a criterion. One unevidenced promise prevents `completed`.
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
| `router` | up to ten ordered comparisons with named branch and fallback outcomes | none |
| `assert` | block on one failed declared comparison | none |
| `approval` | pause and await immutable Human decision | human |
| `data_store` | append one idempotent workspace-local effect | local SQLite |
| `smart_read` | return bounded exact text plus immutable source citations | admitted Knowledge only |
| `knowledge_search` | rank literal terms across current admitted passages | admitted Knowledge only |
| `memory_recall` | return active promoted Memory with source-Run provenance | active Memory only |

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
- A Goal-Judge cannot be an Agent version cast by the Flow it judges, including
  transitively through subflows.
- A model comparison pins its complete expected sibling set before provider I/O;
  missing manifests, missing siblings, model aliases, or unverified ledgers make
  it unusable.
- A Skill candidate requires a completed, ledger-verified source Run and a
  distiller belonging to a different logical Agent resource—not merely another
  version of the source Agent. Candidates, qualifications, and decisions are
  append-only and candidates carry zero authority.
- Candidate qualification proves provenance only. Promotion creates a Skill v1
  but changes no Agent, Action, Flow, or Run pin.
- Fan-out members have identical mapped input contracts, distinct immutable
  targets, and may neither pause nor mint effects. Code owns join counts and
  routes; an editor cannot manufacture quorum or delete member evidence.
- SmartRead/search cannot escape admitted Knowledge. A Memory candidate cannot
  enter recall before qualification and exact-fingerprint Human promotion.

## Capability Forge contract

```text
completed model Step + verified Run ledger
  → pre-I/O source snapshot
  → different logical Agent resource, no tools, strict cited output
  → immutable quarantined candidate, zero authority
  → 8 deterministic provenance/authority gates
  → acknowledged human decision
  → immutable Skill v1 or append-only rejection
```

The source may teach one narrow behavioral instruction; it cannot prove broad
performance. A promoted Skill affects no work until an operator explicitly pins
it into a successor Agent and proves that successor in a new Run.

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
| stop seam | Browser authors promises/Judge; unsupported evidence refuses completion |
| ratify | Three independent structural failures refuse the unchanged Flow version before Run creation |
| compare | Pre-I/O manifest, identical Flow/input, returned-model and ledger checks hold |
| forge | Independent distillation, 8/8 provenance gates, zero authority, and human Skill promotion hold |
| context | SmartRead/search resolve only immutable admitted versions and return exact citations |
| deliberate | Concurrent member Steps, code-owned quorum/failure state, and surviving dissent hold |
| memory | Quarantine, qualification, fingerprint decision, active-only recall, and retirement hold |
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
