# Authoritative evidence contract

Kyn.ist Agent Studio accepts client-authored definitions and commands, but never
client-authored runtime evidence or fixture traces. Evidence is produced by the
control plane and committed to flat SQLite rows while work happens.

## Run envelope

Before worker or model I/O, an Automation Run pins:

- workspace, Flow identity, immutable Flow-version ID/version/fingerprint;
- the pinned graph and its transitive resource fingerprints;
- validated input, idempotency key, and starting node;
- optional parent Run and stable correlation ID; and
- starting revision, `created` status, and timestamps.

The worker advances `created → running`. Approval may pause
`running → waiting_approval → running`. `completed`, `blocked`, `failed`, and
`cancelled` are absorbing database states. A rerun or maintenance proof is a new
row with `parent_run_id`; it never edits the parent.

## Event chain

Events are ordered by unique `(run_id, sequence)`, beginning at one. Each event
contains:

| Field | Contract |
| --- | --- |
| `id` | server-generated stable event ID |
| `run_id` | owning Run; cross-Run citations are invalid |
| `sequence` | contiguous per-Run integer |
| `type` | bounded vocabulary such as `run.queued`, `step.completed`, or `maintenance.diagnosed` |
| `actor_type` / `actor_id` | runtime, Action, Agent version, or human actor |
| `payload` | bounded canonical JSON after secret-key redaction |
| `prev_hash` | fixed genesis marker or prior event hash |
| `event_hash` | SHA-256 commitment to all material fields and `prev_hash` |
| `occurred_at` | server UTC timestamp included in the commitment |

Database triggers reject update and delete. `verify_event_chain` independently
recomputes sequence, predecessor, and event hashes from the API projection.

## Material evidence rows

Events form the readable ledger. Dedicated immutable rows carry the material
receipts that events reference:

- `automation_run_steps`: node, pinned target, attempt, input/output, outcome,
  safe error, and timing;
- `automation_action_receipts`: validated input/output, authoritative outcome,
  error code, target Action version, and idempotency key;
- `automation_model_calls`: Agent version, provider response ID, model, status,
  safe usage, hashes, and request ID—never raw keys or provider bodies;
- `automation_approval_requests` and `automation_approval_decisions`: paused
  context plus one attributable immutable decision;
- `automation_effects`: idempotent workspace-local record linked to its Run,
  Step, Action version, and collection;
- `automation_diagnoses`: code-owned fault class, constrained explanation,
  confidence, failed Step/Action, and exact owned evidence IDs; and
- `automation_repair_proposals` and `automation_repair_decisions`: exact patch,
  proposal hash, dual revision fence, actor, reason, acknowledgement, and applied
  successor IDs.

The browser cannot submit any of these records directly. An event cannot
truthfully claim an effect without the corresponding Action receipt and effect
row committed by the executor.

## Trigger evidence rule

A trigger binding pins an immutable Flow-version ID. Its raw webhook secret is
returned once; only a hash and short hint persist. Triggered model Flows create a
Run before any credential is available and append `run.credential_required`.
That state proves activation without pretending model work occurred. Continuing
uses the same Run and definition pin.

## Diagnosis evidence rule

Code selects the failed Step and derives a supported causal candidate from its
Action contract, receipt, and Run events. The optional model diagnostician sees
only that packet. Structured Output may cite only supplied event IDs; empty,
foreign, or invented citations fail closed. The model cannot choose a broader
fault class or repair path.

## Repair evidence rule

The proposal hash commits to diagnosis, Flow, Action, expected Flow revision,
expected Action version, and canonical patch. Apply requires that exact hash,
both current revisions, actor, reason, and explicit acknowledgement. One
transaction creates immutable Action and Flow successors, advances stable
version pointers, stores the decision, marks the proposal applied, and appends
events. A stale or altered command has no partial effect.

Proof is a linked child Run pinned to the applied Flow successor. Only its Step,
receipt, event, and effect evidence proves the changed outcome; model prose and
the proposal itself do not.

## Public projection

The same-origin API returns safe nested projections for UI inspection. It never
returns workspace token hashes, raw webhook secrets after creation, OpenAI keys,
raw provider requests/responses, authorization headers, or unrestricted SQL.
Nesting is presentation convenience, not an internal Parts/Entities or
Bricks/Frames model.
