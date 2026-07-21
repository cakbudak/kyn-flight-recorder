# Capability Forge design

Date: 2026-07-21

## Purpose

Agent Studio already preserves authoritative execution evidence, refuses a
ratified structural dead end, and distils deterministic failure principles. The
missing success-side loop was a governed way to turn one real model-backed Step
into a reusable behavioral capability without calling that process autonomous
improvement or silently changing runtime authority.

The Capability Forge implements this narrow loop:

```text
completed model Step + verified Run ledger
  → code-owned bounded source snapshot
  → strict tool-free proposal by a different logical Agent
  → immutable authority-free quarantine
  → deterministic provenance qualification
  → explicit Human promotion or rejection
  → immutable Skill v1, assigned nowhere automatically
```

## Claim boundary

Qualification proves provenance and integrity. It does not prove that the
candidate improves performance, generalizes beyond its source observation, or
should be assigned to an Agent.

A promoted Skill grants no tools and no Action versions. To evaluate behavior,
an operator must explicitly publish a successor Agent that pins the Skill and
compare a new Run. That later operation is outside the Forge decision.

## Source eligibility

The source must be:

- a terminal `completed` Run;
- backed by a fully verified event ledger;
- one `completed` model call belonging to that Run;
- attached to one `completed` Step;
- resolvable to the exact pinned source Agent, Prompt, and Skills.

Failed, blocked, running, foreign-workspace, non-model, and ledger-invalid work
cannot source a candidate.

Before external I/O, code freezes a bounded envelope containing the Run and Flow
pins, selected Step, source model-call hashes and usage, source Agent material,
validated input/output, terminal outcome, and at most 24 relevant ledger events.
Its hash is the pre-I/O source snapshot.

## Distillation contract

The proposing Agent must belong to a different logical Agent resource from the
source. A successor version of the source Agent is not independent. The service
checks this before provider I/O, the database checks it again before candidate
insertion, and qualification replays the same identity boundary.

The OpenAI Responses call is:

- made with the visitor's browser-tab credential;
- `store:false`;
- tool-free with `tool_choice:"none"`;
- bounded to one call and 1,200 output tokens;
- high reasoning effort;
- constrained by strict JSON Schema;
- required to cite one to twelve supplied ledger event IDs.

Provider I/O occurs outside SQLite write transactions. A safe append-only call
receipt is recorded even when response parsing or citation validation fails.
Raw prompts, raw provider errors, credentials, and hidden reasoning are not
persisted.

## Quarantine

A candidate stores only:

- immutable source and distiller version references;
- the immutable model-call receipt reference;
- proposed name, instructions, rationale, and cited event IDs;
- the source snapshot hash;
- a fingerprint over all candidate material.

There are no candidate authority columns. The projection reports zero tools and
zero callable Action versions because quarantine has no representation capable
of granting either.

## Deterministic qualification

Qualification performs no model call. It appends one verdict across eight gates:

1. source Run is terminal and completed;
2. source event ledger verifies;
3. source model call and Step are completed and related;
4. the current source snapshot equals the pre-I/O snapshot;
5. every citation is inside the supplied ledger envelope;
6. the candidate fingerprint recomputes exactly;
7. source and distiller belong to different logical Agent resources;
8. candidate authority delta is zero.

The verdict is idempotent and append-only. A failed gate blocks promotion but
does not delete or rewrite the candidate.

## Human decision

Promotion requires an actor, a reason, and explicit acknowledgement of the exact
candidate boundary. It creates one normal immutable Skill v1 whose instructions
match the candidate byte-for-byte and whose tool and Action grants are empty.
It then appends a decision bound to the candidate fingerprint and qualification.

Rejection also requires actor, reason, and acknowledgement. It appends a terminal
decision and preserves the candidate and all source evidence.

Neither decision mutates an Agent, Flow, Action, Prompt, Run, or prior Skill.

## Flat persistence

The standalone SQLite projection adds four explicit product tables:

- `skill_distillation_model_calls`;
- `skill_candidates`;
- `skill_candidate_qualifications`;
- `skill_candidate_decisions`.

All are append-only through SQLite triggers. Foreign keys keep every record
workspace-owned and bound to existing immutable versions and Run evidence. This
is intentionally a simple tabular public projection, not a copy of private Kyn
storage or orchestration internals.

## Rejected alternatives

### Automatic Agent mutation

Rejected because a model proposal would become an authority-bearing behavior
change without a separate Human decision or successor proof.

### Direct Principle-to-Skill conversion

Rejected because a deterministic executor predicate describes a configuration
failure, not demonstrated model behavior. Turning it into model-facing prose
would be a category error presented as learning.

### Version-ID-only independence

Rejected because v2 of the source Agent is still the same logical principal.
Independence is enforced across Agent resource IDs.

### Performance scoring during qualification

Rejected because one source observation cannot establish generalization or
improvement. The Forge proves lineage; later controlled Runs may test behavior.

### Presentation-only mission control

Rejected because a new dashboard without a real persistence, provider, authority,
and decision contract would add polish without product capability.
