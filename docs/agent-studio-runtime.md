# Kyn.ist Agent Studio — public runtime cut

This repository is an executable, clean-room projection of Kyn's public product
capabilities. It is intentionally not a copy of the private Kyn stack.

## Product promise

A visitor can define five versioned resources and use them together:

1. **Actions** declare typed input/output contracts over a bounded executor.
2. **Prompts** declare templates and their exact variables.
3. **Skills** grant instructions and an explicit Action allow-list.
4. **Agents** pin one model, Prompt version, and Skill versions.
5. **Flows** pin an acyclic graph of Action and Agent nodes, mappings, and routes.

Starting a Flow creates a real Run. The Run pins the complete Flow fingerprint,
records each Step and Action invocation, writes a hash-linked event ledger, and
records sanitized model-call metadata. Approval nodes pause the Run; an immutable
human decision resumes or blocks it. A rerun is a linked new Run against an
explicitly selected immutable Flow version.

The existing evidence-bound diagnose → repair → approve → rerun loop remains an
included template. It proves Kyn's closed-loop maintenance discipline, while the
Studio proves that the runtime is configurable rather than a prescribed four-click
demo.

## Deliberately bounded Action surface

The public cut supports only declarative executors whose effects can be defended on
an unauthenticated Build Week deployment:

| Kind | Behaviour | External authority |
| --- | --- | --- |
| `ai` | Runs a pinned Agent through OpenAI Responses; may call only Actions granted by pinned Skills | OpenAI only, visitor BYOK |
| `template` | Deterministically renders an exact template from validated input | none |
| `condition` | Evaluates one declared comparison and exposes `true`/`false` routes | none |
| `approval` | Creates an immutable approval request and pauses the Run | human decision |
| `sandbox` | Appends one idempotent local effect row | local SQLite only |

There is no arbitrary shell, filesystem, URL fetch, MCP server, secret store, or
production connector in the public deployment. Database rows configure known
executors; they never register Python code.

## Flow definition

A Flow version contains:

- a JSON Schema subset for Run input;
- at most twelve uniquely named nodes;
- one explicit start node;
- nodes that pin an Action version or Agent version;
- explicit mappings whose sources are Run input, a completed predecessor Step, or
  a literal;
- directed routes selected by `success`, `true`, `false`, `approved`, or
  `rejected` outcomes;
- an acyclic graph validated before publication.

Definitions are immutable. Publishing a changed definition creates a successor
version and advances the Flow revision exactly once.

## Run lifecycle

```text
created → running → completed
                  ↘ blocked
                  ↘ failed
                  ↘ waiting_approval → running
                                     ↘ blocked
```

`completed`, `blocked`, `failed`, and `cancelled` are absorbing. A terminal Run is
never reopened or silently upgraded. Maintenance creates a linked new Run with its
own pinned definition and evidence chain.

Every node attempt produces a Step. Every state-changing operation produces an
event with the previous event hash and its own SHA-256 hash. The event ledger is
authoritative; UI state and best-effort telemetry are not.

## OpenAI boundary

The browser stores the visitor's key in `sessionStorage`, sends it only in the
`X-OpenAI-API-Key` header on same-origin model operations, and can clear it at any
time. The server constructs an official `openai.OpenAI` client for that operation,
uses the Responses API with `store=false`, discards the client afterward, and never
writes the key to SQLite.

Custom function tools use strict JSON Schema. Model-requested Actions are
intersected with the exact Action versions granted by the Agent's pinned Skills and
then pass through the same Action invocation path as direct Flow nodes.

## Excluded private layers

This cut does not contain or reconstruct Ainou, CE, Appiyon, Kynllm internals,
Parts/Entities, Bricks/Packs/Frames, the production queue, the production graph,
or their schemas. SQLite contains only explicit product-facing tables. Similar
principles are demonstrated through a clean-room implementation; private code and
private ontology remain private.
