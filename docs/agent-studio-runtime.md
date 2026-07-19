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
5. **Flows** arrange pinned Actions or Agents on a visual acyclic graph with
   mappings, outcomes, retry settings, and canvas positions.
6. **Triggers** start a pinned Flow version manually, by secret webhook, or on a
   bounded interval.
7. **Runs** expose live node state, attempts, receipts, model calls, approvals,
   effects, hash-linked events, and linked reruns.
8. **Maintenance** turns a supported failure into owned evidence, a bounded
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
| `assert` | Blocks when one declared comparison fails | none |
| `approval` | Persists a request and pauses until an attributable human decision | human decision |
| `data_store` | Appends one idempotent record in a named workspace-local collection | local SQLite only |

There is no arbitrary shell, filesystem, URL fetch, MCP server registration,
secret store, production connector, or database-configured code. The only
network authority is the official OpenAI SDK for an explicitly model-backed
operation.

## Visual Flow definition

A Flow version contains:

- a strict JSON Schema subset for Run input;
- at most twelve uniquely named nodes;
- one explicit start node;
- immutable Action or Agent version pins;
- an `{x, y}` position for every canvas node;
- mappings from Run input, a reachable predecessor Step, or a literal;
- bounded attempts, backoff, retryable codes, and error policy per node;
- outcome routes selected by `success`, `true`, `false`, `approved`, or
  `rejected`; and
- a complete transitive resource-pin and fingerprint set.

Publication rejects cycles, unreachable nodes, duplicate outcomes, impossible
data reads, and schema mismatches. Editing a published graph creates a successor
version and advances its optimistic revision exactly once. Existing Runs retain
the graph they originally pinned.

## Trigger contract

- **Manual:** validates operator-provided JSON and enqueues a pinned Run.
- **Webhook:** generates a one-time secret URL; only its hash is stored. The
  binding keeps the Flow version active at creation time.
- **Schedule:** accepts a 1–10,080 minute interval and bounded validated input.

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
