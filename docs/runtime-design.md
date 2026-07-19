# Runtime design: Kyn.ist Agent Studio public cut

Status: binding implementation design  
Date: 2026-07-19

## 1. Design boundary

This cut preserves Kyn.ist's public execution contracts without copying its
private ontology. OpenAI produces untrusted model output; the control plane owns
all authority, routing, evidence, and effects. SQLite is a conventional flat
product store for this standalone application.

`serve.py` is the composition root. HTTP handlers validate the transport and
call one `ControlPlane`; no handler or browser command authors runtime evidence.

```text
browser ──same origin──> HTTP API ──> ControlPlane ──> StudioStore ──> SQLite
                                            │
                                            ├──> bounded worker ──> StudioRuntime
                                            │                         ├── local executors
                                            │                         └── OpenAI Responses SDK
                                            ├──> trigger scheduler
                                            └──> diagnosis / repair policy
```

## 2. Flat SQLite projection

The active Studio schema uses explicit product nouns; there is no generic
object, part, entity, brick, frame, or edge table.

| Table family | Purpose |
| --- | --- |
| `workspaces` | anonymous isolated authority boundary and usage counters |
| `prompts` / `prompt_versions` | stable identity plus immutable template versions |
| `skills` / `skill_versions` | immutable instructions and exact authority grants |
| `agents` / `agent_versions` | model, Prompt, Skill, and effective Action pins |
| `actions` / `action_versions` | typed capabilities and immutable executor contracts |
| `automation_flows` / `automation_flow_versions` | stable Flow plus immutable visual DAG versions |
| `automation_trigger_bindings` | version-pinned webhook and interval activation |
| `automation_runs` / `automation_run_steps` | pinned execution and node attempts |
| `automation_events` | ordered append-only hash chain per Run |
| `automation_model_calls` | safe Responses metadata and request/output hashes |
| `automation_action_receipts` | one authoritative result per Action attempt |
| `automation_approval_requests` / `automation_approval_decisions` | pause/resume command boundary |
| `automation_effects` | idempotent workspace-local data effects |
| `automation_diagnoses` | grounded fault analysis with owned event citations |
| `automation_repair_proposals` / `automation_repair_decisions` | fenced successor command and evidence |

Bounded JSON columns contain schemas, graph manifests, mappings, or event
payloads where a scalar projection would add no query value. They do not encode
Kyn.ist's internal polymorphic structures. Database triggers make definition
versions and evidence append-only and reject illegal lifecycle transitions.

## 3. Resource composition

An Action version declares one statically coded executor, strict input/output
schemas, effect level, configuration, optional Agent pin, and a fingerprint.
The nine logical kinds are `ai`, `template`, `transform`, `delay`, `condition`,
`router`, `assert`, `approval`, and `data_store`.

An Agent version pins one Prompt and bounded Skills. A Skill may grant exact
callable Action version IDs. Effective authority is intersected with the
runtime's static callable kinds; database content cannot register Python code,
and AI or approval Actions cannot become nested model tools.

A Flow version pins the complete graph: input/output schemas, public outcomes,
start node, Action/Agent/Flow resource versions, canvas positions, mappings,
retry/backoff/error settings, routes, and transitive fingerprints. Validation
rejects cycles, unreachable nodes, ambiguous outcomes, reads from
non-predecessor Steps, transitive subflow cycles, and unbounded graph size.

## 4. Publication and triggers

Publishing a new Flow creates immutable v1. Editing a published graph performs
an optimistic revision compare-and-swap and creates an immutable successor.
Runs and triggers keep their original Flow-version pin.

A webhook binding returns its raw secret once and stores only its SHA-256 hash
and short hint. A schedule stores a bounded interval and validated input. The
schedule pump atomically claims due bindings before creating Runs. Trigger
retries use server idempotency keys and cannot mutate a prior terminal Run.
Enable/disable commands use a dedicated optimistic configuration revision;
ordinary fire timestamps do not invalidate an operator command.

Deterministic trigger Runs execute immediately. Model-backed triggers cannot
possess a server-side visitor key, so they durably prepare a `created` Run and
append `run.credential_required`. The workspace operator later continues the
same pinned Run with a browser-held credential.

## 5. Execution protocol

1. Resolve and validate the requested immutable Flow version and Run input.
2. In a short write transaction, create a fully pinned `created` Run before any
   worker or provider call and append `run.queued`.
3. The bounded worker advances it to `running`, resolves the current mapping
   from Run input, literals, and completed predecessor output, and creates an
   attempt Step.
4. Invoke the resource through one Action path. AI Actions render their pinned
   Agent/Prompt/Skills, call Responses outside SQLite writes, and dispatch only
   strict granted Action calls through that same path.
5. Commit Step output, Action receipt, safe model metadata, and hash-linked
   events. Retry only codes named in the pinned node settings, up to three
   attempts, with bounded backoff.
6. Route only on an executor-owned outcome. `on_error=continue` follows one
   declared error route; otherwise exhausted errors fail closed. An explicit
   `ActionBlocked` authority denial always blocks the Run.
7. At an Approval Action, persist the request and transition to
   `waiting_approval`. One immutable human decision resumes the already pinned
   graph or blocks it.
8. Complete, block, fail, or cancel without reopening terminal state. A rerun
   creates a linked child with its own evidence chain.

At most two Studio workers execute concurrently per process. The HTTP command
returns the prepared Run, while the operations console polls the authoritative
projection until it reaches a pause or terminal state.

## 6. Evidence ledger

Every event has unique `(run_id, sequence)`. `event_hash` is SHA-256 over the
canonical event identity, sequence, timestamp, type, actor, payload, and
`prev_hash`; sequence one uses a fixed genesis marker. An independent verifier
can recompute the chain from the API projection.

Steps, model calls, Action receipts, decisions, and effects are material rows,
not strings inferred from a log. No event can truthfully claim an effect without
the corresponding receipt and effect committed by the executor. Secret-like
keys are rejected or redacted before evidence persistence.

## 7. Diagnosis and repair protocol

Code first derives a supported causal candidate from the terminal Step, its
Action receipt, and owned events. For example, a `data_store` Action whose pinned
`write_enabled` policy is false yields an `authority_policy` candidate and an
allowlisted path `/config/write_enabled`.

The pinned diagnostician receives only that candidate. Strict Structured Output
must cite a subset of the supplied event IDs; foreign or invented evidence fails
closed. The model never selects the fault class or repair path.

The proposal hash commits to diagnosis, Action, Flow, exact current versions,
and canonical patch. Apply requires the exact hash, both expected revisions, a
human actor and bounded reason, and explicit acknowledgement. One
`BEGIN IMMEDIATE` creates successor Action and Flow versions, records the
decision, and marks the proposal applied. Stale or altered commands have no
partial effect.

Proof creates one idempotent linked child per proposal, pinned to the applied
Flow successor. The parent is not edited. Only the child's authoritative
terminal outcome and effect receipt prove the change.

## 8. Workspace, credential, and HTTP boundary

The browser receives an opaque workspace cookie; only its hash is stored. Every
lookup is workspace-scoped. Mutations require JSON, exact same-origin and Fetch
Metadata checks, bounded bodies, and a valid `HttpOnly`, `SameSite=Strict`,
`Secure`-on-HTTPS cookie. The API enables no CORS.

The OpenAI key lives in browser `sessionStorage` and is attached only to a
same-origin operation whose server-side forecast can call a model. The server
ignores an operator `OPENAI_API_KEY`, constructs an ephemeral official SDK
client, and never persists or logs the credential. Workspace, address, global,
and concurrency budgets bound public model usage.

## 9. Test seams and release proof

The Responses transport is injectable. Contract tests use provider-shaped
deterministic responses while retaining the real HTTP, control-plane, runtime,
and SQLite paths. Chromium verification creates definitions, uses the visual
canvas, fires a webhook, publishes a successor, executes and approves an AI
Flow, inspects live evidence, and completes diagnosis → repair → proof.

Release evidence additionally runs the same browser journey through public HTTPS
with a real visitor credential. Sanitized reports may retain IDs, model, usage,
hashes, event types, and outcomes; they must never contain keys, cookies, raw
provider requests, or hidden reasoning.
