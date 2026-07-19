# Repository instructions

These rules apply to the whole repository.

## Product boundary

- This is the standalone Build Week cut of the Kyn.ist agent runtime and Flight Recorder.
- It must create, execute, record, diagnose, repair, approve, and rerun a real agent flow.
- OpenAI is the model transport. Kyn owns orchestration, tool dispatch, evidence, repair,
  approval, and replay truth.
- The runtime may use Python's standard library, SQLite, and `OPENAI_API_KEY`; it must not
  import Kynist, Appiyon, Kynllm, Ainou, PostgreSQL, or their database schemas.
- The public tool surface is a bounded local sandbox. It must not expose shell, filesystem,
  arbitrary network, or production-write authority.
- Never describe fixture, fake-client, or sandbox state as a production integration.

## Data architecture

- SQLite is a flat, product-facing projection. It must not reproduce Parts, Entities,
  Bricks, Frames, graph storage, or another generic ontology.
- Agents, prompts, skills, and flows are explicit resources with immutable versions.
- Every run pins one immutable flow version and therefore exact agent, prompt, and skill
  versions. A repair creates a successor flow version; it never edits history.
- Runs, ordered events, model-call summaries, tool receipts, diagnoses, repairs, approvals,
  and sandbox effects are durable rows.
- Events and resource-version rows are append-only. Terminal run states are absorbing.
- A hash-linked event ledger is authoritative for the standalone cut; best-effort telemetry
  is not evidence.

## Runtime architecture

- `serve.py` is the composition root for the static application and `/api/v1` HTTP API.
- `backend.service.ControlPlane` is the only product mutation path. HTTP handlers stay thin.
- No SQLite write transaction may remain open during OpenAI or other external I/O.
- OpenAI Responses function calls are schema-validated and intersected with the exact tools
  granted by the run's pinned skills. Model prose never grants authority.
- Diagnosis may cite only events from its run. Repair may touch only flow-version paths
  allowed by the pinned repair policy.
- Repair application requires proposal hash, expected flow revision, explicit acknowledgement,
  actor, and reason. The write is revision-fenced and idempotent.
- Deterministic fake model clients are test seams only. A release claim of live agentics
  requires a sanitized real-model proof.

## Security and quality

- `OPENAI_API_KEY` stays server-side, is never returned, logged, committed, or stored in SQLite.
- Mutations require a same-origin request and an isolated workspace cookie. Enforce body,
  model-turn, tool-call, per-workspace, per-address, and global cost bounds.
- Validate JSON strictly, render dynamic values with safe DOM APIs, redact secret-like data,
  and preserve restrictive response headers.
- WCAG 2.2 AA is the target. Keyboard, focus, reduced motion, narrow viewport, loading,
  empty, error, stale-revision, replay, and reset/new-workspace paths are required.
- Verification must include positive, negative, boundary, concurrency/fencing, mutation,
  real-model, and browser journeys. Green UI tests alone do not prove runtime truth.

## Git mandate: forward only

Do not reset, revert, stash, rebase, amend, squash, switch branches, or rewrite history.
Work on the active branch. At each stable state, stage every change with `git add -A` and
create a new commit.
