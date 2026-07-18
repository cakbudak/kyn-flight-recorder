# Product contract

Date frozen: 2026-07-18  
Maturity: `designed`

## Promise

Kyn.ist Flight Recorder lets a developer answer three questions about an
autonomous agent run without reconstructing logs by hand:

1. Where is the run stuck?
2. What evidence caused that state?
3. What controlled intervention can move it forward, and who authorized it?

The three-minute demo follows one causal thread from an agent step through a
tool call, queue item, approval boundary, intervention, and append-only audit
receipt.

## Standalone boundary

The submission is a hermetic demonstration surface, not a claim that the whole
Kynist production stack is embedded here. It uses:

- one static web application;
- one deterministic, versioned JSON fixture;
- browser-local ephemeral state for the intervention rehearsal;
- a Python standard-library server with no installation step.

It does not use a database, authentication provider, external API, LLM call,
secret, telemetry service, or network dependency. Closing or resetting the demo
returns it to the signed fixture state.

## Core user journey

1. Open the seeded run `run_01JY7KYN9X4N`.
2. Read the causal diagnosis: a write-capable tool call is paused at an approval
   boundary; its queue lease is healthy, so retrying the worker would be wrong.
3. Inspect each graph node and its correlated evidence.
4. Preview the only legal command for the current revision.
5. Enter an operator reason and authorize the intervention.
6. Observe the command receipt, resumed transitions, completed run, and new audit
   entries without losing the original evidence.
7. Reset deterministically for the next judge.

## State and intervention invariants

- Fixture identity and graph edges never mutate.
- `blocked` can advance only through the declared `approve_tool_call` command.
- A command must match run id, current revision, allowed source state, actor, and
  non-empty reason.
- Preview has no effect.
- Apply is idempotent for one command id.
- Terminal `completed` is absorbing until explicit demo reset.
- Audit entries are append-only within a demo session.
- Every rendered event carries the same correlation id.
- Values classified as secrets are redacted before entering the DOM.

## Quality gates

| Gate | Acceptance criterion | Evidence target |
| --- | --- | --- |
| G1 UX | Complete, empty, invalid-fixture, and reset paths; keyboard-only journey; WCAG 2.2 AA | browser journey + manual checklist |
| G2 contract | Fixture schema version and command contract are explicit; invalid data fails closed | standard-library contract tests |
| G3 security | No secrets/network writes; command validation and DOM-safe rendering | threat model + negative tests |
| G4 data | Fixture-only, synthetic, localStorage/session state deletable by reset | data note + reset test |
| G5 reliability | Idempotency, revision fence, terminal absorption, deterministic reset | state-machine tests |
| G6 performance | First meaningful render under 1 s locally; interaction under 100 ms on reference machine | browser measurement |
| G7 operation | Correlation, source class, timestamps, and receipts visible | UI assertions |
| G8 agentics | Demo explains the agent boundary but performs no model/tool execution | scope label in UI and README |
| G9 proof | Positive, negative, boundary, browser, and accessibility evidence | test report |
| G10 release | One-command run; no migration; forward-only Git history | clean-clone rehearsal |

## Explicit non-goals

- Connecting to a live Kynist deployment in this cut.
- Executing real tools or privileged writes.
- Multi-tenant authentication or authorization.
- Replacing production logs, traces, queues, or audit storage.
- Claiming that simulated transitions are production-live.

## Build Week provenance

This standalone repository and its core functionality are new work created after
the OpenAI Build Week start. The repository history is the chronology. The final
submission must add the Codex Session ID returned by `/feedback` for the project
thread in which the majority of the core functionality was built.

Official event references:

- <https://openai.com/build-week/>
- <https://openai.devpost.com/>
- <https://openai.devpost.com/rules>
