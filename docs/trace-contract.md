# Authoritative ledger contract

Kyn.ist Agent Studio accepts validated client-authored Flow definitions, but never
client-authored runtime evidence or a trace fixture. Evidence is created only by
the control plane and committed to flat SQLite rows as work happens.

## Run envelope

A run pins these fields before model I/O:

- workspace, flow, immutable flow-version id/version/fingerprint;
- optional parent run id and stable correlation id;
- starting revision and `running` state;
- timestamps and bounded terminal error code.

A rerun is a new row with `parent_run_id`. It never edits the failed run. Only
`running → blocked|completed|failed` is legal; terminal states are absorbing in a database
trigger.

## Event chain

Events are ordered by `(run_id, sequence)` with a unique constraint. Sequence starts at 1.
Each event contains:

| Field | Contract |
| --- | --- |
| `id` | server-generated stable event id |
| `run_id` | owning run; cross-run citations are invalid |
| `sequence` | contiguous per-run integer |
| `type` | bounded runtime vocabulary such as `agent.started` or `tool.denied` |
| `actor_type` / `actor_id` | runtime, agent version, tool, or human actor |
| `payload` | bounded canonical JSON after secret-key redaction |
| `prev_hash` | fixed genesis marker or prior event hash |
| `event_hash` | SHA-256 commitment to all material event fields and `prev_hash` |
| `created_at` | server UTC timestamp included in the commitment |

Database triggers reject update and delete on events. `verify_event_chain` recomputes
sequence, predecessor, and event hashes independently from API projections.

## Material evidence rows

Events are the readable ordered ledger. The following explicit Repair Lab tables
carry the material receipts that its events reference:

- `model_calls`: agent version, role, provider response id, model, status, safe usage, and
  request/response hashes—never raw prompts or provider bodies;
- `tool_receipts`: call id, validated/redacted arguments, outcome/error, result, effect kind,
  event id, and idempotency key;
- `diagnoses`: deterministic fault class, accepted explanation, exact owned evidence ids,
  confidence, retry argument, and supported repair path;
- `repairs`: one canonical patch, risk, proposal hash, expected revision, and status;
- `repair_approvals`: proposal hash, expected revision, actor, reason, acknowledgement, and
  applied flow-version id;
- `sandbox_releases`: successful local effect linked to run and flow version.

The configurable Studio uses parallel explicit tables with the same evidence
discipline: `automation_run_steps`, `automation_model_calls`,
`automation_action_receipts`, `automation_approval_requests` plus immutable
decisions, and `automation_effects`. Those records are linked from
`automation_events`; the browser cannot submit any of them directly.

An event cannot truthfully claim a sandbox effect without the corresponding receipt/effect
row committed by the same tool transaction.

## Diagnosis evidence rule

Code derives the supported candidate from receipts before a model is asked to explain it.
For the seeded failure, the only accepted evidence is:

1. the successful `inspect_release_policy` receipt event; and
2. the `stage_release` denial with `error_code=policy_mismatch`.

The structured diagnosis must return that exact evidence-id set. Missing, additional,
foreign-run, or invented ids fail closed.

## Repair evidence rule

The proposal hash commits to diagnosis id, flow id, expected revision, and patch. Apply
requires the exact hash and revision plus human actor, reason, and acknowledgement. One
transaction inserts the approval, inserts flow v2, advances the flow revision, marks the
repair applied, and appends approval/version events. A stale or altered command has no
partial effect.

## Public projection

The same-origin API returns safe nested projections for UI inspection; it does not expose
workspace token hashes, raw secrets, raw OpenAI requests/responses, authorization headers,
or unrestricted SQL access. API nesting is a presentation convenience, not an internal
Parts/Entities/Bricks/Frames or graph storage model.
