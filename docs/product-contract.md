# Product contract

Date frozen: 2026-07-19  
Maturity: `implemented; local, real-model, and public HTTPS journeys verified`

## Promise

Kyn.ist Flight Recorder turns an agent failure into a bounded, provable recovery loop:

1. Compose a flow from versioned agents, prompts, and skills.
2. Execute that flow against the OpenAI Responses API and real local tools.
3. Record every material decision and effect in an authoritative SQLite event ledger.
4. Diagnose the failure from recorded evidence rather than reconstructed logs.
5. Propose the smallest allow-listed repair.
6. Require a revision-fenced human approval before the repair can change a flow.
7. Rerun against a new immutable version and compare the before/after evidence.

The three-minute judge path is one causal thread from pinned configuration through model
tool calls, a failed sandbox effect, evidence-grounded diagnosis, bounded repair, approval,
successful rerun, and hash-linked receipts.

## Honest standalone boundary

This repository is not the whole Kynist production stack. It is a compact implementation of
the closed-loop contract, built with:

- one Python standard-library HTTP process;
- one flat SQLite database;
- the OpenAI Responses API for model inference;
- a deliberately narrow local sandbox tool surface;
- a dependency-free browser application.

It does not import the internal Kynist ontology or PostgreSQL schema. It has no arbitrary
shell, filesystem, connector, MCP, or production deployment authority. The sample
`stage_release` tool makes a real, durable change only inside the local `sandbox_releases`
table. That is a real tool effect and a safe sandbox—not a production release.

## First-class resources

### Prompt

A prompt is a named, immutable template version with declared variables. Rendering fails
closed on missing or unexpected variables, and the resolved prompt hash is recorded.

### Skill

A skill is a named, immutable instruction version plus an allow-list of local tools. Skill
instructions influence model behavior; the tool allow-list constrains runtime authority.

### Agent

An agent version pins a model, role instructions, one prompt version, and one or more skill
versions. The runtime derives its callable tools from those pinned skills.

### Flow

A flow version pins executor, diagnostician, and repairer agent versions, input, policy, and
repair bounds. A run always points to one immutable flow version. Applying a repair creates
the next version and advances the flow revision atomically.

## Canonical judge journey

The seeded `Release Sentinel` lab intentionally contains one policy defect:
`production` is requested while the flow permits only `staging`.

1. Create an isolated lab workspace. The server creates and displays its prompt, skill,
   agent, and flow versions.
2. Run the flow. The executor agent inspects policy and calls the real local
   `stage_release` tool.
3. The tool rejects the effect at its policy boundary and writes a failed receipt. The run
   becomes `blocked`; no sandbox release exists.
4. Hand off the immutable evidence packet to the diagnostician agent. Structured output is
   accepted only if every cited event belongs to the failed run and matches the deterministic
   fault candidate.
5. Hand off the diagnosis and current manifest to the repairer agent. Its single JSON patch
   must target an allow-listed path and value.
6. Preview and approve the repair with actor, reason, acknowledgement, proposal hash, and
   expected flow revision.
7. Apply atomically. The original flow version remains immutable; a successor version is
   created.
8. Rerun as a child of the failed run. The same real tool now succeeds and creates one
   `sandbox_releases` row.
9. Compare the two runs, their pinned fingerprints, events, receipts, and effect count.

## Invariants

- Resource versions and events cannot be updated or deleted.
- Event sequence is contiguous per run and each event hash commits to its predecessor.
- A run pins one flow version before model I/O begins.
- External model I/O never occurs under an open SQLite write transaction.
- A model can request only a tool; code validates arguments and owns authorization/effects.
- A tool not granted by every applicable pinned capability check is rejected.
- `blocked`, `completed`, and `failed` are absorbing run states.
- Diagnosis evidence ids must exist on the diagnosed run.
- Repair operations are count-, path-, operation-, and value-bounded.
- Applying a repair never mutates an existing flow version.
- A stale expected revision fails without a partial write.
- Repeated application of the same authorized proposal returns the original result.
- A rerun is a new run with `parent_run_id`; history is never rewritten.
- Secrets are absent from model evidence packets, SQLite rows, logs, and API responses.

## Quality gates

| Gate | Acceptance criterion |
| --- | --- |
| G1 create | A user can create a lab containing a real flow, prompt, skill, and agent |
| G2 execute | A real Responses call emits validated local function calls and a durable outcome |
| G3 record | Ordered hash-linked events, model summaries, receipts, pins, and correlation are visible |
| G4 diagnose | Structured diagnosis is restricted to existing evidence and deterministic candidates |
| G5 repair | One allow-listed patch is previewed and applied only through a human revision fence |
| G6 rerun | A child run uses the successor flow version and proves a changed tool outcome |
| G7 safety | Same-origin, isolation, input/cost bounds, no secret persistence, no arbitrary tools |
| G8 reliability | Immutability, terminal absorption, CAS conflict, idempotency, and concurrency tests pass |
| G9 UX | Keyboard, focus, responsive, loading/error/stale paths, and WCAG 2.2 AA checks pass |
| G10 proof | Fake-client RED/GREEN suite, real-model sanitized evidence, and local/public Playwright pass |

## Explicit non-goals

- Mirroring Parts, Entities, Bricks, Frames, or the main Kynist database.
- Shipping the full production queue, MCP, tenant identity, connector, or storage stack.
- Giving public visitors arbitrary code or network execution.
- Letting a model directly authorize or apply a repair.
- Claiming broad superiority over every framework. The measured claim is the closed-loop
  evidence, bounded repair, revision-fenced approval, and rerun proof delivered here.

## Build Week provenance

The repository history is the forward-only chronology. The primary build thread is Codex
Session ID `019f7621-5200-7400-9242-920cb718d09a`.

Official references used for the transport contract:

- <https://developers.openai.com/api/docs/guides/function-calling>
- <https://developers.openai.com/api/docs/guides/structured-outputs>
- <https://developers.openai.com/api/docs/guides/agents/define-agents>
- <https://developers.openai.com/api/docs/guides/tools-skills>
