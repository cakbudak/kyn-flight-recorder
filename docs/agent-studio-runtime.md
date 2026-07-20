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
5. **Flows** arrange pinned Actions, Agents, or published Flows on a visual
   acyclic graph with mappings, named outcomes, retry settings, and canvas
   positions.
6. **Triggers** start a pinned Flow version manually, by secret webhook, or on a
   bounded interval.
7. **Runs** expose live node state, attempts, receipts, model calls, approvals,
   effects, hash-linked events, and linked reruns.
8. **Completion contracts** bind observable promises to exact evidence kinds
   and graph sites, then let an independent pinned Judge nominate anchors that
   code resolves against Run-owned records.
9. **Ratification and principles** derive refusal or advisory state from
   independent failure evidence rather than mutable counters or model prose.
10. **Comparisons** pin the complete sibling manifest before provider I/O and
    derive controlled invariance from the manifested Runs.
11. **Maintenance** turns a supported failure into owned evidence, a bounded
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

There is no arbitrary shell, filesystem, URL fetch, MCP server registration,
secret store, production connector, or database-configured code. The only
network authority is the official OpenAI SDK for an explicitly model-backed
operation.

## Visual Flow definition

A Flow version contains:

- strict JSON Schema subsets for Run input and terminal output;
- one to sixty-four uniquely named nodes;
- one explicit start node;
- immutable Action, Agent, or child Flow version pins;
- an `{x, y}` position for every canvas node;
- mappings from Run input, a reachable predecessor Step, or a literal;
- bounded attempts, backoff, retryable codes, and error policy per node;
- one to twelve declared public Flow outcomes and per-node routes selected by
  the exact outcome IDs owned by each capability; and
- zero to eight acceptance promises, each pinning a `step`, `receipt`,
  `approval`, or `effect` to one or more capable graph sites, plus an independent
  immutable Goal-Judge Agent version when any promise is declared; and
- a complete transitive resource-pin and fingerprint set.

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
