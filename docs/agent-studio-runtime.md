# Kyn.ist Agent Studio — public runtime cut

This repository is an executable, clean-room projection of Kyn.ist's agent
workflow and maintenance capabilities. It is intentionally not a copy of the
private Kyn stack.

## Product promise

A visitor can create and operate a real automation system:

1. **Actions** declare typed input/output contracts over bounded executors.
2. **Prompts** declare templates and their exact variables.
3. **Skills** carry instructions and exact Action-version authority grants.
4. **Agents** pin one model, Prompt version, and Skill versions.
5. **Knowledge + SmartRead** admit immutable source versions and retrieve exact
   cited passages by intent without a model call.
6. **Flows** arrange pinned Actions, Agents, published Flows, or parallel fan-out on a visual
   acyclic graph with mappings, named outcomes, retry settings, and canvas
   positions.
7. **BoardRooms** generate editable multi-agent Flows whose members run
   independently before a code-owned join and downstream Human gate.
8. **Triggers** start a pinned Flow version manually, by secret webhook, or on a
   bounded interval.
9. **Runs** expose live node state, attempts, parent/member Steps, receipts, model calls, approvals,
   effects, hash-linked events, and linked reruns.
10. **Governed Memory** turns cited completed-Run evidence into quarantined,
    qualified, Human-promoted, provenance-bearing recall.
11. **Completion contracts** bind observable promises to exact evidence kinds
   and graph sites, then let an independent pinned Judge nominate anchors that
   code resolves against Run-owned records.
12. **Ratification and principles** derive refusal or advisory state from
   independent failure evidence rather than mutable counters or model prose.
13. **Comparisons** pin the complete sibling manifest before provider I/O and
    derive controlled invariance from the manifested Runs.
14. **Capability Forge** turns one completed model Step into a quarantined,
    provenance-qualified, human-promoted authority-free Skill version.
15. **Maintenance** turns a supported failure into owned evidence, a bounded
   successor proposal, a human decision, and a linked proof Run.

The seeded `Agent-reviewed launch` graph is an editable use case, not a tour.
Visitors can ignore it and build any graph supported by the public Action
surface.

## Deliberately bounded Action surface

| Kind | Behaviour | External authority |
| --- | --- | --- |
| `ai` | Runs a pinned Agent through OpenAI Responses; may call only exact Actions granted by pinned Skills | OpenAI only, visitor BYOK |
| `template` | Renders a declared template from validated input | none |
| `transform` | Maps input paths or literals into a declared output schema | none |
| `delay` | Waits 0–5000 ms and passes validated input through | none |
| `condition` | Evaluates one declared comparison and emits `true` or `false` | none |
| `router` | Evaluates up to ten ordered rules and emits one declared branch or fallback outcome | none |
| `assert` | Blocks when one declared comparison fails | none |
| `approval` | Persists a request and pauses until an attributable human decision | human decision |
| `data_store` | Appends one idempotent record in a named workspace-local collection | local SQLite only |
| `smart_read` | Reads an admitted immutable source version by glance/outline/focus/grep/full mode and returns exact citations | admitted Knowledge only |
| `knowledge_search` | Deterministically ranks literal terms across current admitted passages | admitted Knowledge only |
| `memory_recall` | Searches only active Human-promoted Memory and returns source-Run provenance | active Memory only |

There is no arbitrary shell, filesystem, URL fetch, MCP server registration,
secret store, production connector, or database-configured code. The only
network authority is the official OpenAI SDK for an explicitly model-backed
operation.

## Visual Flow definition

A Flow version contains:

- strict JSON Schema subsets for Run input and terminal output;
- one to sixty-four uniquely named nodes;
- one explicit start node;
- immutable Action, Agent, child Flow, or fan-out member version pins;
- an `{x, y}` position for every canvas node;
- mappings from Run input, a reachable predecessor Step, or a literal;
- bounded attempts, backoff, retryable codes, and error policy per node;
- one to twelve declared public Flow outcomes and per-node routes selected by
  the exact outcome IDs owned by each capability; and
- zero to eight acceptance promises, each pinning a `step`, `receipt`,
  `approval`, or `effect` to one or more capable graph sites, plus an independent
  immutable Goal-Judge Agent version when any promise is declared; and
- a complete transitive resource-pin and fingerprint set.

A fan-out node adds two to eight distinct Action/Agent/Flow members with one
identical mapped input contract, an all/quorum barrier, a declared verdict path
and affirmative set, and isolate/fail-fast member error handling. Member targets
cannot pause or mint effects. Nested fan-out is outside this public cut.

Publication rejects cycles, unreachable nodes, duplicate outcomes, impossible
data reads, schema mismatches, transitive subflow cycles, nesting beyond four
levels, more than 192 routes, and more than 200 expanded nodes. A subflow runs as
an evidence-linked child with the same correlation ID and separate Steps,
receipts, events, effects, and outcome. Editing a published graph creates a
successor version and advances its optimistic revision exactly once. Existing
Runs retain the graph they originally pinned.

## Trigger contract

- **Manual:** validates operator-provided JSON and enqueues a pinned Run.
- **Webhook:** generates a one-time secret URL; only its hash is stored. The
  binding keeps the Flow version active at creation time.
- **Schedule:** accepts a 5–10,080 minute interval and bounded validated input.

Bindings can be disabled or re-enabled through an optimistic trigger revision
fence. Execution timestamps do not advance that configuration revision, so a
busy schedule cannot starve an operator's state command.

Deterministic trigger Runs execute immediately. A model-backed trigger cannot
carry a server credential, so it creates a durable `created` Run plus
`run.credential_required` evidence. A workspace operator may continue that exact
Run with a key held in their browser tab.

## Run lifecycle

```text
created → running → completed
                  ↘ blocked
                  ↘ failed
                  ↘ cancelled
                  ↘ waiting_approval → running
                                     ↘ blocked
                                     ↘ cancelled
```

Every node attempt produces a Step. Retries remain separate attempts and are
bounded by the pinned node settings. `completed`, `blocked`, `failed`, and
`cancelled` are absorbing database states. A rerun is a linked new Run; it never
reopens or upgrades its parent.

## Context and SmartRead

Knowledge import copies bounded UTF-8 content into an immutable workspace source
version and precomputes conventional flat passage rows. It accepts text content,
not a server path or remote URL. Every citation contains the source ID/version,
filename, full source fingerprint, and exact line range.

SmartRead declares intent before volume:

- `glance`: opening window plus headings;
- `outline`: bounded structural lines;
- `focus`: an exact range of at most 160 lines;
- `grep`: bounded literal matches with context; and
- `full`: the whole source only below the configured 96 KiB bound.

Deterministic Knowledge search ranks literal term coverage over current admitted
passages. SmartRead, search, and Memory recall also exist as ordinary Actions.
Their strict results may expose a bounded `context` envelope that retains exact
source, fingerprint, line, candidate, and source-Run provenance for downstream
Flow mapping; older Action versions without that field remain valid. A seeded
deterministic handoff Action combines the current-source and active-Memory
envelopes without inference.

The Context workbench can turn one cited SmartRead result into an ordinary
editable four-node draft:

```text
SmartRead (exact source/read policy)
  → governed Memory recall (active versions only)
  → deterministic cited-context handoff
  → published BoardRoom Flow
```

The draft is not auto-published and every pin, mapping, route, and node remains
visible in Flow Studio. A Run before promotion may recall no Memory. After a
completed, ledger-verified source Run is qualified and Human-promoted through
the normal Memory lifecycle, the same pinned Flow automatically carries the
matching Memory and its provenance into the next Run. Same-Run Memory writing is
deliberately impossible: a candidate requires terminal source evidence first.

Context Actions may also enter model work through exact Skill grants and the
normal receipt path.

## Parallel fan-out and BoardRooms

The fan-out parent Step is persisted before dispatch. Every member then executes
concurrently through its own operation session and creates a child Step linked by
`parent_step_id` and `member_id`. External model I/O occurs without an open
SQLite write transaction. After every started worker records its terminal
evidence, code computes completed/failed counts, affirmative votes, convergence,
and dissenting IDs and appends a barrier event.

The BoardRoom factory publishes ordinary Prompt, Skill, Agent, AI Action, editor
Action, result/approval/write Actions, and one generic Flow. Participants never
see peer output. The final editor receives canonical completed member records
only after the join and cannot revise barrier truth. Approval and optional one
bounded write remain downstream nodes with explicit approval and rejection
routes. Opening the room in Flow Studio edits the same Flow—there is no parallel
hidden runtime or BoardRoom database table.

## Governed Memory

A Memory proposal cites up to twenty exact events from one completed,
ledger-verified source Run. Human proposals and strict tool-free Agent
distillations both enter immutable quarantine and are excluded from recall.
Deterministic qualification replays candidate authority, citation ownership,
ledger integrity, unchanged source snapshot, and source completion.

Only a Human command carrying actor, reason, acknowledgement, slug, and the exact
candidate fingerprint may promote the candidate. Recall searches active promoted
Memory versions and returns the source candidate, Run, evidence event IDs, and
fingerprint. Rejection and retirement append records; they never erase the
proposal or promoted version.

## Goal/stop seam

“Finished” is a claim at this boundary, not a state transition. The independent
pinned Goal-Judge receives its Agent/Prompt/Skill contract and a bounded,
redacted view of actual Step, receipt, approval, and effect material. It returns
one structured assessment, reason, and anchor nomination per declared promise.
That output is recorded as a non-authoritative model claim.

The serialized evidence question is bounded to 96 KiB before provider I/O. A
larger Run fails closed at the stop seam instead of depending on a provider's
context or request-size behavior.

The runtime then narrows every nomination against immutable records: same Run,
declared evidence kind, declared graph site, and admitted state. It may remove an
anchor; it may never invent or infer one. If any promise has no surviving anchor,
the Run records `completion_unevidenced`, retains all work it actually performed,
and never becomes `completed`. No criteria means no Judge call and no behavior
change.

## Controlled model comparison

The comparison command validates the entire forecast, prepares every sibling
Run, and appends one hash-linked manifest naming the expected model × repetition
× Run-ID set before the first provider request. Derived scoreboards verify the
manifest, each ledger, identical Flow and input fingerprints, and the model name
actually returned by the provider. Missing or rewritten evidence makes the
record unusable. The result states scaffold invariance and measured noise; it is
never promoted to a model ranking or baseline.

## Capability Forge

The Forge accepts one completed model call on one completed, ledger-verified
Run. Before a second model call, code freezes the selected Step, source Agent and
Skill fingerprints, validated input/output, model-call hashes, terminal state,
and a bounded event excerpt. The distiller must belong to a different logical
Agent resource—not merely another immutable version—and receives no tools. Its strict result contains behavioral
instructions, a narrow rationale, and event IDs drawn only from that envelope.

The candidate is append-only and has no authority fields. A separate
deterministic qualification replays eight properties: terminal source, complete
hash chain, source model-Step ownership, pre-I/O snapshot, citation subset,
candidate fingerprint, independent distiller, and zero tool/Action authority.
This proves lineage, not performance. Human promotion then creates one normal
immutable Skill v1 with the exact candidate instructions and zero authority.
No Agent, Action, Flow, or Run is changed; use requires an explicit later
successor and a new Run. Rejection is append-only too.

## OpenAI boundary

The browser stores the visitor's key in `sessionStorage`, attaches it only to a
same-origin operation forecast to call a model, and can clear it at any time.
The server constructs an ephemeral official `openai.OpenAI` client, calls the
Responses API with `store=false`, records only safe provider metadata and hashes,
then discards the client. It never reads an operator `OPENAI_API_KEY` fallback.

Custom tools use strict schemas. Model-requested Actions are intersected with the
exact Action versions granted by the Agent's pinned Skills and then traverse the
same executor, receipt, validation, and effect path as direct Flow nodes. Model
text cannot route the graph, grant authority, approve, apply a repair, or create
an effect by assertion.

## Integrated maintenance

For supported blocked or failed Actions, code first derives a causal candidate
from the terminal Step, receipt, and owned event IDs. A pinned diagnostician may
explain only that candidate through a strict Structured Output. The repair
service then derives one allowlisted patch against exact Action and Flow
revisions. Application requires a human actor, reason, acknowledgement, proposal
hash, and both revision fences.

Applying a repair creates successor Action and Flow versions. Proof executes one
idempotent linked child per proposal on the successor. The terminal parent, its
zero or partial effects, and all prior evidence remain immutable.

## Excluded private layers

This cut does not contain or reconstruct Ainou, CE, Appiyon, Kynllm internals,
Parts/Entities, Bricks/Packs/Frames, the production queue, the production graph,
or their schemas. SQLite contains conventional product tables only. The public
runtime demonstrates the contracts through an independent implementation while
Kyn.ist's private architecture and economic model remain private.
