# Runtime design: Kyn.ist Agent Studio public cut

Status: binding implementation design  
Date: 2026-07-19

## 1. Design boundary

The cut preserves Kyn's contracts without copying Kyn's internal ontology. OpenAI produces
model output; this runtime owns all authority and evidence. SQLite is a transparent
product-facing read model and write model for this standalone application.

The composition root is `serve.py`. HTTP calls one `ControlPlane`; the control plane calls
resource, runtime, diagnosis, and repair services; those services use one `Store`. No HTTP
handler writes SQLite directly.

```text
browser -> same-origin API -> ControlPlane -> Store + StudioStore (SQLite)
                                  |
                                  +-> StudioRuntime -> Action invocation
                                  |       |                |
                                  |       |                +-> bounded local executors
                                  |       +-> Responses SDK + strict granted Actions
                                  |
                                  +-> Repair Lab AgentRuntime -> tool/diagnosis/repair validators
```

## 2. Flat SQLite projection

The schema uses explicit nouns. There is no generic object/part/entity/edge table.

| Table | Purpose |
| --- | --- |
| `workspaces` | anonymous isolated lab boundary and usage counters |
| `prompts` | stable prompt identity and current version |
| `prompt_versions` | immutable template, variables, fingerprint |
| `skills` | stable skill identity and current version |
| `skill_versions` | immutable instructions, allowed tools, fingerprint |
| `agents` | stable agent identity and current version |
| `agent_versions` | immutable model/instructions and prompt/skill pins |
| `flows` | stable flow identity and current revision/version |
| `flow_versions` | immutable agent pins, request, policy, repair policy, fingerprint |
| `runs` | pinned execution, parent/correlation, status, revision, timings |
| `events` | ordered append-only hash chain per run |
| `model_calls` | provider response id, role, model, status, usage and safe hashes |
| `tool_receipts` | validated call, result/error, authority and idempotency evidence |
| `diagnoses` | structured grounded fault analysis and evidence ids |
| `repairs` | bounded patch, proposal hash, fence and approval/application state |
| `repair_approvals` | append-only human command and applied-version receipt |
| `sandbox_releases` | Repair Lab effect created by successful `stage_release` |
| `actions` / `action_versions` | stable typed capabilities and immutable executor contracts |
| `automation_flows` / `automation_flow_versions` | configurable pinned DAG definitions |
| `automation_runs` / `automation_run_steps` | authoritative graph execution and node attempts |
| `automation_events` | ordered append-only hash chain per Automation Run |
| `automation_model_calls` | safe Responses metadata/hashes for Studio Agents |
| `automation_action_receipts` | one authoritative result per Action attempt |
| `automation_approval_requests` / `automation_approval_decisions` | real pause/resume command boundary |
| `automation_effects` | idempotent workspace-local public Action effects |

JSON columns hold bounded arrays or manifest fragments where SQLite has no useful scalar
projection. They do not hide polymorphic internal entities. Database triggers reject updates
and deletes on immutable version and event tables.

## 3. Studio resource composition

An Action version declares a bounded built-in executor plus strict input/output
schemas. An Automation Flow version pins Action or Agent nodes, mappings, routes,
and every transitive fingerprint. Graph validation rejects cycles, unreachable
nodes, ambiguous outcomes, and reads from non-predecessor Steps.

Each Studio Agent version pins exactly one Prompt and bounded Skills. A Skill can
grant exact callable Action version IDs. The effective set is intersected with
the runtime's static callable kinds; approval and AI Actions cannot become nested
model tools.

## 4. Repair Lab resource composition

Each agent version pins exactly one prompt version and a bounded list of skill versions.
The agent's effective tool set is the union of its pinned skills' allow-lists, intersected
with the server's static `ToolRegistry`. A database string can never register executable
code.

Each flow version pins three roles:

- `executor`: performs the requested task through local tools;
- `diagnostician`: explains a deterministic failure candidate using cited event ids;
- `repairer`: proposes one bounded data patch for the diagnosed cause.

The runtime records explicit `agent.started`, `agent.handoff`, and `agent.completed` events
with role and version fingerprint. Handoffs are flow-declared control-plane transitions,
not prose interpreted as a transition.

## 5. Studio execution protocol

1. Validate Flow input against the pinned schema.
2. In a short write, create the Run, pin Flow/resources, append initial events,
   and transition to `running`.
3. Resolve the current node mapping from validated Run input, literals, and
   completed predecessor Step output.
4. Invoke the pinned Action through one path. AI Actions render their pinned
   Agent/Prompt/Skills, call Responses outside all writes, and dispatch only
   strict granted Action tools through that same path.
5. Persist Step result, Action receipt, safe model metadata, and hash-linked
   events. Route only on a declared authoritative outcome.
6. At an Approval Action, persist the request and transition to
   `waiting_approval`. A human decision resumes at the already pinned next node or
   blocks terminally.
7. Complete, block, or fail without reopening a terminal Run. Rerun creates a
   linked child with its own evidence chain.

## 6. Repair Lab execution protocol

1. In a short `BEGIN IMMEDIATE`, create a `running` run pinned to the current flow version
   and append its first events; commit.
2. Resolve prompt and skill text from the pinned versions. Store only hashes and safe
   summaries in events.
3. Call `POST /v1/responses` with `store=false`, strict function schemas,
   `parallel_tool_calls=false`, bounded output, and only effective tools. No DB transaction
   is open.
4. For each `function_call`, parse JSON, validate the exact schema again, enforce skill
   authority, and execute through `ToolRegistry` in a short transaction.
5. Append the response output plus `function_call_output` in memory and call Responses again
   if needed. Cap model turns and tool calls.
6. Complete the run from receipts: a successful sandbox release means `completed`; a policy
   denial means `blocked`; malformed or missing required behavior means `failed`.
7. Atomically append the terminal event and transition. Terminal states cannot transition.

`stage_release` does not contact a deployment system. On success it inserts one idempotent
`sandbox_releases` row. On policy denial it inserts no effect. Both outcomes have receipts.

## 7. Evidence ledger

Every event has `(run_id, sequence)` uniqueness. `event_hash` is SHA-256 over the event's
canonical identity, sequence, timestamp, type, actor, payload, and `prev_hash`. Sequence 1
uses a fixed genesis marker. API projections expose enough fields for an independent verifier
to recompute the chain.

Runs are truth; `model_calls` and `tool_receipts` are material evidence. No event claims an
effect unless the corresponding effect or receipt row commits in the same transaction.

## 8. Diagnosis protocol

Code first derives a bounded candidate from receipts. In the judge case:

```text
fault_class = policy_mismatch
requested = production
allowed = [staging]
repairable_path = /policy/allowed_environments
evidence = policy inspection + denied stage receipt event ids
```

The diagnostician receives only this candidate and a redacted evidence packet. Responses
Structured Outputs must return the exact schema. The validator rejects unknown evidence ids,
another fault class, an unsupported path, or claims outside the packet. A model failure does
not fabricate a diagnosis.

## 9. Repair and approval protocol

The repairer receives the validated diagnosis, current bounded manifest, and allow-list. It
may propose at most one `replace` operation on `/policy/allowed_environments`. Code verifies
that the requested environment is added without removing existing values or changing any
other field.

The proposal hash commits to diagnosis id, flow id, expected revision, and canonical patch.
Apply requires:

- the exact proposal hash;
- current flow revision equal to `expected_flow_revision`;
- a non-empty actor and bounded reason;
- explicit acknowledgement of the sandbox effect;
- proposal status `proposed`.

One `BEGIN IMMEDIATE` performs the compare-and-swap, inserts the new immutable flow version,
advances the stable flow row, marks the proposal applied, and appends the approval evidence.
A repeated identical application returns the prior result. Any stale or altered request fails
without partial state.

The repair loop permits exactly one child per blocked parent. Child creation checks for an
existing row inside `BEGIN IMMEDIATE`, and a partial unique index on non-null `parent_run_id`
enforces the same invariant in SQLite. A repeated rerun returns the existing child before
model I/O, so retries cannot duplicate model calls or sandbox effects.

## 10. Workspace and HTTP boundary

The browser obtains an opaque workspace cookie when it creates a lab. Every row is scoped by
that workspace, directly or through a scoped parent lookup. Identifiers alone do not grant
cross-workspace reads.

Mutation requests must be JSON, same-origin, and below the body limit. The server enforces
per-address and per-workspace request/model budgets plus a process-wide model budget. The API
does not enable CORS. Cookies are `HttpOnly`, `SameSite=Strict`, and `Secure` on HTTPS.

The visitor's API key is held in browser `sessionStorage` and attached only to a
same-origin model command. The server explicitly ignores an operator
`OPENAI_API_KEY`, constructs an ephemeral official SDK client per bounded
operation, and does not persist or log the key. Errors are mapped to stable public
codes; raw provider bodies, authorization headers, prompts, and secret-like values
are never logged.

## 11. Test seams and proof

`ResponsesClient` is injectable. Contract tests use a deterministic fake that emits the same
response item shapes as the official API. Tests must prove failure before implementation or
through controlled mutation for the high-risk invariants.

Release evidence additionally requires one sanitized real `gpt-5.6` journey. The artifact
may retain ids, model name, usage, hashes, event types, and outcomes; it must not contain the
API key, raw hidden reasoning, full prompts, cookies, or provider request headers.
