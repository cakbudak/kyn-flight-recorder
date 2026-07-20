# Kyn.ist Agent Studio v4 — product workbench contract

Date: 2026-07-19  
Status: implementation contract

## Outcome

The Build Week cut is a professional agent-work automation product, not a
scripted demonstration. A first-time visitor must be able to discover, define,
connect, publish, run, inspect, maintain, and reuse work without knowing Kyn's
private stack vocabulary.

This repository remains a public projection. It does not contain or reproduce
Ainou, CE, Parts/Entities, Bricks/Packs/Frames, the production queue, or the
private Kyn database model.

## Product requirements

### Definition workbench

- Actions, Prompts, Skills, Agents, and Flows are selectable resources, not
  passive cards.
- Editing a published resource creates an immutable successor version guarded
  by the current version or revision. Existing Runs retain their pins.
- Every Action version declares one to twelve named outcomes. Outcome IDs are
  stable lowercase slugs and are the source-port contract shown on the canvas.
- AI configuration is visible at the point of use: model, Prompt, Skills,
  callable Actions, reasoning effort, tool-call budget, and response schema.
- Advanced JSON remains available, but common creation and mapping paths do not
  require manually discovering a hidden JSON shape.

### Flow workbench

- The canvas owns the available viewport instead of rendering inside a small
  page card.
- It supports pan, zoom, fit, minimap, selection, multi-selection, delete,
  keyboard navigation, auto-layout, undo, redo, and clear source/target handles.
- Every declared outcome receives its own labelled source handle. Connections
  are created from that exact handle and retain the outcome ID.
- Lines use obstacle-conscious orthogonal/smooth-step routing and selected edges
  remain individually identifiable.
- A Flow version can be inserted as a first-class node. The node pins the child
  Flow version and exposes its input contract and declared terminal outcomes.
- Publishing validates graph reachability, cycles, mappings, outcome ownership,
  transitive subflow cycles, version ownership, and bounded size.

### Execution and operations

- One runtime path executes direct Action nodes and Agent-requested Actions.
- A subflow node invokes the same Flow runtime as an evidence-linked child Run;
  it is not copied into a second executor.
- The Run view overlays current, completed, waiting, failed, and skipped state on
  the exact pinned graph and exposes Steps, events, calls, receipts, approvals,
  effects, child Runs, diagnosis, repair, and proof.
- Model output remains untrusted data. Skills grant exact Action versions; prose
  cannot widen authority.
- Terminal Runs remain absorbing. Approval, repair, rerun, and proof are
  attributable forward commands.

## Bounded contracts

| Surface | Budget |
| --- | --- |
| request body | 256 KiB hard cap |
| Flow nodes | 64 per version |
| Flow routes | 192 per version |
| Action outcomes | 12 per version |
| nested subflow depth | 4 |
| total nodes per Run tree | 200 |
| node attempts | 1–3 |
| node backoff | 0–5 seconds |
| model tool calls | 0–4 per AI Action |
| concurrent model operations | 2 per process, 1 per workspace |

Coordinates are finite and bounded. Payloads, schemas, names, prompts, and
instructions retain explicit length limits. No SQLite write transaction spans
OpenAI or another external I/O boundary.

## Data and migration

SQLite remains a flat product-facing projection. Additive columns/tables may
hold outcome contracts, Flow output contracts, and subflow child correlation.
Existing rows receive deterministic compatibility defaults derived from their
immutable executor kind. No existing version or event is rewritten.

The browser source may use build-time UI libraries. The released application is
a self-hosted static bundle served by the existing Python process; it has no CDN
or Node runtime dependency and remains compatible with the restrictive CSP.

## Security

- The browser-owned OpenAI key remains in `sessionStorage`, is sent only to a
  same-origin model command, and is never persisted or returned.
- No operator key fallback, arbitrary shell, filesystem, provider URL, MCP
  registration, or production-write connector is introduced.
- Resource and version IDs are workspace-scoped at every read and mutation.
- Subflow dependency validation rejects cross-workspace pins and recursive
  cycles before publication.
- Rendering uses React text nodes and controlled attributes; no untrusted HTML
  insertion is permitted.
- OWASP ASVS 5.0.0 Level 2 is the review baseline for the exposed web boundary.

## Quality gates

| Gate | Measurable acceptance | Evidence |
| --- | --- | --- |
| G1 UX | task-based first-use journeys complete without prescribed click coordinates; keyboard, focus, reduced motion, narrow viewport, loading, empty, stale, and error states pass | Playwright + manual checklist |
| G2 contracts | valid, invalid, and boundary examples cover outcomes, revisions, mappings, and subflows | Python contract tests |
| G3 security | origin, workspace ownership, BYOK redaction, body caps, cross-workspace pins, recursion, and forbidden authority fail closed | HTTP/database tests + threat model |
| G4 data | new fields have purpose, owner, retention inherited from 24-hour workspace, and workspace reset/expiry path | schema inspection + docs |
| G5 reliability | idempotency, terminal absorption, stale revisions, nested failures, retries, duplicate commands, and concurrent publication are proven | adversarial tests |
| G6 performance | 64-node editor remains interactive; snapshot p95 < 400 ms and deterministic 64-node Run p95 < 2 s on the release host; overload is bounded | benchmark script |
| G7 operations | Run tree and correlation IDs reach every Step, child, event, call, receipt, and effect; health distinguishes SQLite readiness | browser/API proof |
| G8 agentics | goals, Prompt, Skills, tools, budgets, stop/approval conditions, output schema, and human interventions are visible and enforced | fake-provider negatives + sanitized real Responses run |
| G9 proof | positive, negative, boundary, mutation/RED, real seam, and exploratory task journeys pass | verification report |
| G10 release | additive startup migration, clean-clone build, mixed old/new database verify, forward-only commits, GitHub and HTTPS smoke pass | release evidence |

No gate passes merely because an older selector-driven browser script remains
green. The verifier must express user tasks and assert authoritative API/SQLite
effects, not only DOM reachability.
