# Context-to-decision runtime: SmartRead, Memory, and BoardRoom

Date: 2026-07-21  
Status: implementation contract  
Surface: standalone public Kyn.ist Agent Studio

## Outcome

The public product will demonstrate one coherent, inspectable agentic loop:

1. a user imports bounded text into a private workspace;
2. SmartRead turns the pinned document version into cited, line-addressable context;
3. Knowledge search and human-approved Memory recall make that context available to
   exactly granted Agents or ordinary downstream Flow mappings;
4. a deterministic cited-context Action can combine the current immutable source
   envelope and active promoted Memory before a nested Flow;
5. a generic Flow fan-out executes independent participants concurrently;
6. a deterministic barrier computes quorum and preserves dissent;
7. a pinned editor Agent synthesises only the completed participant records;
8. an optional human approval gates any write Action;
9. Steps, receipts, model calls, citations, approvals, and effects remain visible in
   the existing hash-linked Run ledger.

This is a public projection of Kyn.ist's product capability. It deliberately does
not reproduce the private Parts/Entities, Bricks/Packs/Frames, Ainou, CE, or
BoardRoom-OD implementation.

## Product promise and ceiling

The promise is not “a chatbot with four buttons.” A user can define Actions, grant
them through Skills, compose and version Flows, import source material, inspect and
recall context, form an editable council, run it, route every declared outcome,
approve effects, and inspect the exact evidence afterwards.

The ceiling is explicit:

- no server-filesystem reader;
- no shell or arbitrary-network tool;
- no production write connector;
- no hidden workspace-wide Agent authority;
- no automatic promotion of model-authored Memory;
- no claim that this flat SQLite projection is the private Kyn.ist knowledge graph;
- no claim that a BoardRoom preset is the private organisational-dynamics engine.

## One motor, three new primitives

### 1. Knowledge source and version

A Knowledge Source is a user-owned container. A Knowledge Source Version is an
immutable text snapshot with a SHA-256 fingerprint and deterministic line index.
Source updates publish successors; they never overwrite a version already cited by
a Run or Memory.

Supported import in this cut: UTF-8 plain text, Markdown, JSON, YAML-like text, and
source code pasted or uploaded through the browser. The backend accepts text and a
display filename, never a server path.

Hard limits:

- 256 KiB UTF-8 per version;
- 10,000 lines per version;
- 200 Knowledge Sources per workspace;
- 100 result passages per request;
- citations always identify source, version, fingerprint, and inclusive line span.

### 2. SmartRead and recall Actions

These are normal immutable Action versions. They use the existing Action invocation
path and mint the existing receipt and Step evidence. They are read-only and may be
model-called only through a pinned Skill grant.

`smart_read` modes:

| Mode | Required input | Deterministic result |
| --- | --- | --- |
| `glance` | `source_version_id` | metadata, first informative lines, headings |
| `outline` | `source_version_id` | heading/symbol outline with citations |
| `focus` | version plus `line_start`, optional `line_end` | bounded exact line window |
| `grep` | version plus literal `query` | bounded matching windows |
| `full` | `source_version_id` | entire version only when within the Action limit |

`knowledge_search` searches immutable Knowledge passages using deterministic,
explainable term scoring. It returns no synthetic answer; every result is a cited
text passage.

`memory_recall` searches only promoted, active Memories. A result cites both the
Memory and its source Run/events. Candidates and rejected Memories never enter
recall.

When their output schema declares it, all three context executors also return a
bounded `context` string assembled by code from the same cited records. This is
not a model summary. A seeded deterministic template Action combines current
Knowledge and promoted-Memory envelopes under separate labels.

From one cited SmartRead result the Context workbench may construct, but never
auto-publish, this ordinary editable Flow draft:

```text
SmartRead (pinned source/version/read policy)
  → active Memory recall
  → deterministic cited-context handoff
  → published BoardRoom subflow
```

The first Run may recall nothing. Once evidence from a completed,
ledger-verified Run passes the existing quarantine, deterministic qualification,
and exact-fingerprint Human promotion, the same pinned Flow automatically
recalls it on its next Run. It cannot write and consume its own candidate inside
one Run because source completion is a hard Memory admission precondition.

### 3. Fan-out/barrier Flow node

Fan-out is a Flow composition kind, not an Action executor. It therefore cannot
bypass the one Action invocation path.

The node shape is:

```json
{
  "id": "council",
  "type": "fan_out",
  "version_id": "fanout-v1",
  "input_mapping": {
    "brief": {"source": "input", "path": "brief"}
  },
  "members": [
    {"id": "product", "type": "action", "version_id": "actv_..."},
    {"id": "risk", "type": "agent", "version_id": "agv_..."}
  ],
  "barrier": {
    "mode": "quorum",
    "quorum": 2,
    "verdict_path": "verdict",
    "affirmative_values": ["commit"],
    "on_member_error": "isolate"
  },
  "position": {"x": 440, "y": 180},
  "settings": {
    "max_attempts": 1,
    "backoff_seconds": 0,
    "retry_on": [],
    "on_error": "fail"
  }
}
```

Contract:

- two to eight members, each with a unique lowercase id;
- member types are `action`, `agent`, or `flow` and pin an immutable version;
- every member receives an immutable copy of the same validated mapped input;
- member target input schemas must all accept that mapped input;
- each member executes through the existing target invocation function;
- execution uses a bounded worker pool and thread-local idle SQLite connections;
- no SQLite write transaction is held across target execution or provider I/O;
- a parent Fan-out Step owns child member Steps through `parent_step_id` and
  `member_id`; the existing event ledger records member start and finish;
- the barrier is code-owned and deterministic; an LLM does not count quorum;
- `all` produces `success`, `partial`, or `error`;
- `quorum` produces `converged`, `review`, or `error`;
- member errors are either isolated and included in output or fail fast, as pinned;
- every member result, error, verdict, duration, and model-call evidence survives;
- cancellation of already-started provider calls is not claimed.

The output contract is stable:

```json
{
  "members": {
    "product": {
      "status": "completed",
      "outcome": "success",
      "output": {"verdict": "commit", "analysis": "..."},
      "error": null,
      "step_id": "astep_..."
    }
  },
  "barrier": {
    "mode": "quorum",
    "expected": 3,
    "completed": 3,
    "failed": 0,
    "affirmative": 2,
    "quorum": 2,
    "converged": true,
    "dissenting_members": ["risk"]
  }
}
```

## BoardRoom is configuration, not a second runtime

“BoardRoom” is a creation experience and editable Flow template. It publishes:

- independent participant AI Actions with strict structured outputs;
- exact Prompts, Agents, and Skills;
- a fan-out/barrier node;
- an editor AI Action which consumes the participant map and must retain dissent;
- optional approval and write nodes chosen by the user.

The participant schema includes `verdict`, `analysis`, `recommendations`, `risks`,
and `citations`. Verdict is one of `commit`, `challenge`, or `abstain`. The editor
does not vote and cannot erase dissent; its output contains `decision`,
`consensus`, `dissent`, `open_questions`, and `citations`.

Presets such as Launch Council or Incident Council are only initial configuration.
After creation, every participant, model, Prompt, Skill, tool grant, quorum,
mapping, route, and downstream node remains editable in the normal workbenches.

## Memory lifecycle

Memory is a governed product record, not hidden conversation state.

1. A user selects one completed, ledger-verified source Run.
2. A pinned distiller Agent may propose one candidate using only supplied Run and
   event material.
3. Code validates that every cited event belongs to the source Run and recomputes
   the source snapshot hash.
4. The candidate remains quarantined and grants no authority.
5. A human promotes or rejects the exact candidate fingerprint with actor, reason,
   and acknowledgement.
6. Promotion creates one immutable Memory version. Retirement later creates an
   append-only state record; it does not rewrite history.

Model-written Memory therefore cannot silently poison future context. Directly
authored Memory uses the same candidate and decision path but records `human` as
the author kind and still requires source citations.

## Flat SQLite projection

New product-facing tables:

- `knowledge_sources`
- `knowledge_source_versions`
- `knowledge_passages`
- `memory_distillation_model_calls`
- `memory_candidates`
- `memory_candidate_qualifications`
- `memory_candidate_decisions`
- `memories`
- `memory_versions`
- `memory_state_events`

Existing Step storage gains nullable `parent_step_id` and `member_id`. Existing
node-type checks gain `fan_out`. No private ontology names or relations appear in
the schema.

All version, candidate, qualification, decision, and state-event rows are immutable
or append-only through SQLite triggers. Source containers may advance a
`current_version`; prior versions remain immutable.

## HTTP and authority

All mutations remain methods on `ControlPlane` and require the existing workspace
cookie/origin checks. Proposed endpoints:

- `POST /api/v1/studio/knowledge-sources`
- `POST /api/v1/studio/knowledge-sources/{id}/versions`
- `GET /api/v1/studio/knowledge-sources/{id}`
- `POST /api/v1/studio/knowledge/search`
- `POST /api/v1/studio/knowledge/smart-read`
- `POST /api/v1/studio/memory-candidates`
- `POST /api/v1/studio/memory-candidates/{id}/qualifications`
- `POST /api/v1/studio/memory-candidates/{id}/promotion`
- `POST /api/v1/studio/memory-candidates/{id}/rejection`
- `POST /api/v1/studio/memories/search`
- `POST /api/v1/studio/memories/{id}/retirement`
- `POST /api/v1/studio/boardrooms`

The browser-owned OpenAI key remains in `sessionStorage` and is forwarded only for
requests that actually invoke a model. It is never persisted or logged.

## Interface

### Context workbench

A new top-level Context workbench has two tabs:

- Knowledge: import/update sources, inspect immutable versions, use SmartRead modes,
  copy exact citations, and create a Flow Action from the query;
- Memory: inspect quarantined candidates, provenance checks, decisions, active
  memories, and cited recall results.

### BoardRoom workbench

The creation form starts with a useful Launch Council but is not a one-click black
box. It exposes:

- title and purpose;
- two to eight editable participants;
- participant role, model, Prompt instructions, Skills, and exact Action grants;
- independent/shared context visibility;
- barrier mode, quorum, verdict path, and error policy;
- editor Agent and downstream approval choice.

Creation ends with “Open editable Flow,” not “Run magic.” The full-size Flow Studio
renders the fan-out node as one composition node with separately inspectable member
ports/rows; it does not draw overlapping pseudo-edges for internal participants.

The UI uses restrained motion, explicit focus states, no `transition: all`, no
scale-from-zero, and honours `prefers-reduced-motion`.

## Product-quality gates

| Gate | Evidence required |
| --- | --- |
| G1 semantics | One Action invocation path; fan-out is Flow composition; Memory candidates grant no authority |
| G2 tests | Unit, HTTP, concurrency, migration, security, browser, and negative-contract tests |
| G3 performance | bounded 2–8 worker pool; SmartRead/search limits; existing 64-node gates remain green |
| G4 observability | parent/member Steps, barrier event, citations, model calls, receipts, decisions, ledger verification |
| G5 security | workspace isolation, origin/cookie protection, BYOK only, no paths/shell/arbitrary network, size limits |
| G6 documentation | live Context and BoardRoom documentation plus README/API contract |
| G7 UX | editable resources, useful empty states, keyboard flow, responsive layouts, visible errors/progress |
| G8 regression | full existing suite and old-database migration fixture stay green |
| G9 durability | immutable versions, append-only governance, WAL-safe concurrency, restart-readable Runs |
| G10 release | clean clone, build, real-browser journey, real OpenAI proof, GitHub and HTTPS smoke checks |

## Negative tests that must exist

- SmartRead cannot accept an absolute path, traversal string, cross-workspace version,
  oversized source, invalid range, regex, or unbounded full read.
- An Agent cannot call SmartRead, Knowledge search, or Memory recall unless the exact
  Action version is granted by a pinned Skill.
- A candidate cannot cite another workspace, an unverified Run, an invented event,
  or a changed snapshot.
- A candidate cannot be recalled, and promotion cannot proceed without a passing
  qualification and exact fingerprint acknowledgement.
- A fan-out cannot have one or nine members, duplicate ids, mutable targets,
  incompatible schemas, impossible quorum, unsupported member kinds, or nested
  fan-out disguised as an Action.
- A failed member is visible and cannot be counted affirmative.
- Sequential execution fails a timing proof with two blocking test transports;
  true overlap must be observed.
- A concurrent event append cannot duplicate a sequence or break the hash chain.
- The editor cannot be the deterministic barrier and quorum cannot depend on model
  prose.
- Old workspaces and old Flow versions remain byte-identical and runnable.

## Release proof

The release is complete only when one Chromium journey performs the complete loop
from imported brief to cited SmartRead, governed Memory, concurrent BoardRoom,
editor synthesis, human approval, effect, and verified evidence ledger. A separate
live OpenAI run must show real provider response ids while the stored database and
logs remain free of the browser key.
