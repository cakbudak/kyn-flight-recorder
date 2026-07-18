# Kyn Flight Trace v1 contract

Version: `1.0`  
Classification supported by this cut: `synthetic_demo`  
Structural authority: `schema/kyn-flight-trace-v1.schema.json`
Semantic authority: `app/core.mjs::validateFixture`

The portable trace is a declaration of evidence, graph causality, and one legal
local transition. It is not a transport for executable code and it does not make
an imported claim true merely because the structure is valid.

## Structural envelope

| Field | Required | Meaning |
| --- | --- | --- |
| `schema_version` | yes | Exact supported version, currently `1.0` |
| `fixture` | yes | Stable sample identity, timestamp, and `synthetic_demo` classification |
| `run` | yes | Run identity, revision, state, diagnosis, goal, and effect declaration |
| `nodes[]` | yes | Ordered causal evidence in `main` or `guardrail` lanes |
| `edges[]` | yes | Directed, named relations between node ids |
| `events[]` | yes | Correlated, contiguous, append-only observed events |
| `intervention` | yes | The single revision-fenced command and deterministic resolution |
| `redaction` | yes | Sensitive key classes and display replacement |

The interoperable structural envelope lives at
[`schema/kyn-flight-trace-v1.schema.json`](../schema/kyn-flight-trace-v1.schema.json).
The runtime imports that exact file and evaluates its local references, required
fields, closed objects, types, constants/enums, formats, bounds, and collection
constraints before semantic validation. The runtime validator additionally owns
cross-field checks that JSON Schema alone does not express.

## Semantic invariants

Validation fails closed unless all of these hold:

1. The schema version is exactly `1.0` and classification is `synthetic_demo`.
2. Run status is `blocked` or `completed`; standalone impact declares
   `external_effect: false`.
3. Node and edge ids are unique, and every edge endpoint exists.
4. The diagnosis cause references an existing node.
5. Every event correlation id equals `run.correlation_id`.
6. Event ids and sequences are unique; sequences are contiguous from `1`.
7. Only `approve_tool_call` is accepted, from `blocked`.
8. `intervention.expected_revision` equals `run.revision`.
9. The resolution advances exactly one revision and its event sequence continues
   directly after observed events.
10. The resolution terminal becomes `completed`.
11. Redaction includes authorization, password, secret, and token classes. The
    bundled fixture adds API key, claim token, credential, and cookie classes.
12. No nested free-form evidence object may smuggle an `external_effect` value
    other than `false`.

## Command contract

Preview takes current state and returns a command projection without changing
state. Authorization requires the fixture-pinned actor, a trimmed reason from 12
through 280 characters, and explicit acknowledgement that the effect is a local
simulation. Apply rechecks the source state and revision before it changes any
state.

The receipt binds:

- command and idempotency ids;
- run and correlation ids;
- actor and reason;
- previous and new revisions;
- completion timestamp and external-effect declaration.

A duplicate command id returns the existing receipt and cannot append another
event. A terminal without an owned receipt rejects further commands.

## Import and rendering boundary

- Maximum import size is 1 MiB.
- The file is parsed as JSON, structurally validated against the bundled schema,
  then semantically validated before state creation.
- Sensitive keys are redacted recursively before the accepted object reaches the
  renderer.
- Dynamic values use `textContent`; HTML parsing sinks and dynamic code execution
  are absent.
- Imported traces live in page memory only. Only the fixture-bound local command
  receipt may enter session storage.
- Reset deletes the receipt and reloads the canonical bundled fixture.

## Evolution

Breaking semantics require a new `schema_version` and a new validator path. A v1
consumer must reject, not guess at, an unknown version. New optional presentation
metadata may be introduced only when it cannot change graph identity, event
ordering, effect claims, or command authorization.
