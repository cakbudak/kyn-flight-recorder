# Controlled model comparison — design

Date: 2026-07-20
Status: approved, in implementation

## The claim

Anyone can run a prompt against several models and show a table. That is a
demo, not evidence: nothing proves the comparison was fair.

This runtime can prove it. One immutable pinned Flow version, one input, N
models, N sibling Runs — and the `flow_version_id` is byte-identical across all
of them, so every Action, Agent, Prompt, Skill and schema in the graph is
provably the same. **The version pinning that already exists is what turns a
demo into a controlled experiment.**

### The claim is inverted on purpose

The obvious framing — *model A beats model B* — is the wrong one, and this
stack's own measurement is why. The model lever was refuted twice: a strong
model on identical prompts produced the same correct answers and the *same*
gate refusals as a weak one, and on a judge matrix stronger models did not
reduce over-refusal at all — one made it worse. Over-refusal was structural.

So a leaderboard here would contradict the evidence that already exists, and it
invites exactly the questions a leaderboard cannot survive: variance, holdout,
provider fallback.

The claim instead is:

> **Every guard held on every brain. The scaffolding, not the model, produced
> the safety property — and here is the cost spread across brains that produced
> identical behaviour.**

That is more surprising, much harder to attack, and it turns the existing
ablation suite into the other half of one argument: **ablation shows what breaks
when a guard is removed; the sweep shows it does not break when the brain is
swapped.** Two axes, one substrate.

The scoreboard therefore leads with agreement — same routed outcome, same guard
behaviour — and only then reports the token and latency spread.

## The one deliberate exception, and how it is contained

Varying the model means the pinned Agent version would differ, which would mean
a different Flow version, which would destroy the control. So the model is
varied by an explicit per-Run **override** instead.

An override is a hole in "everything is pinned", and it is treated as one:

| Guard | Rule |
| --- | --- |
| Origin | Settable **only** by the comparison command. No normal Run path accepts it. |
| Membership | Must be in `SUPPORTED_MODELS`. Anything else is refused. |
| Record | Written on the Run row *and* appended to the hash-linked event chain. |
| Visibility | The Run is `relation_kind = "comparison"` and badged in the UI. It can never be mistaken for a pinned production Run. |
| Blast radius | Only the model changes. Prompt, Skills, schemas, routes, retry policy and effect policy are the pinned ones. |

A Run carrying an override is honest about it everywhere it appears. That is the
difference between a recorded deviation and a silent one.

## Two integrity gates, without which this is theatre

**Verify the model that actually answered.** A silent provider fallback destroys
the comparison completely and is invisible unless asserted. Every sibling
compares the model in the provider *response* against the model requested. A
mismatch invalidates that sibling and marks the comparison unusable. A missing
model or missing usage is an error, never a zero.

**Do not claim controls that are not enforced.** "Every non-model variable is
pinned" is false as a blanket statement: sampling controls such as temperature
and seed are not enforceable through this invocation surface. The comparison
payload therefore carries the distinction as a field, not as a footnote:

| Enforced and verified | Not controllable here |
| --- | --- |
| pinned Flow version id, and through it every Action, Agent, Prompt, Skill, schema and route | `temperature` |
| the input, by fingerprint recomputed per sibling from the stored Run row | `top_p` |
| the model that actually answered, by response assertion on every model call | `seed` |
| | provider-side sampling — identical calls are not guaranteed to agree |
| | provider-side routing — serving stack, hardware and capacity are not observable from a response |

Each uncontrolled variable is named with its reason in the payload itself, not
summarised as a single "nondeterminism" bucket, so a reader can check the claim
against the list rather than against a sentence in this document.

Claiming an unsupported control is the fastest way to make an honest experiment
dishonest.

## Repetitions and the noise band

A single run per model is noise rendered as a finding — in this stack the same
configuration swung 0.929 to 0.786 between two runs of an identical setup.

So a comparison runs each model `repetitions` times, retains every raw run
rather than only an aggregate, and reports **population** variance — population
because these repetitions are the whole set of observations the command made,
not a sample of a larger pool it is estimating.

The noise band is **its own spread on identical input and configuration**. It is
derived from the within-model repetitions rather than from a separate
calibration pass: repetitions of one model hold everything constant — same
pinned version, same input, same brain — so whatever they disagree by is the
harness, not the model. The band is the widest such disagreement, and it costs
no extra model calls to obtain. Any difference between models smaller than that
band is reported as `within_noise` — explicitly, as a non-result. Only a
difference larger than the band may be called `signal`.

**Default repetitions is 1, not 3.** The contract test fixes a two-model
comparison at exactly two model calls, so the command's default cannot be 3
without breaking the control the tests pin. The honesty requirement is met a
different way: at `repetitions = 1` the harness has not measured itself, so
`noise_band.measured` is `false` and every numeric difference is classified
`unmeasured` rather than `signal`. A single-repetition sweep can therefore
report invariance, but it can never report a cost difference as a finding.
Callers who want findings pass `repetitions` explicitly (bounded to 5).

That self-measurement is not overhead. It is the strongest part of the story:
the instrument measures itself before it weighs anything.

## What is measured

Per sibling Run, from authoritative evidence already recorded:

- terminal status
- the routed outcome — did the deterministic gate agree?
- input / output / total tokens as reported by the provider
- wall-clock latency
- committed effect count
- whether the strict output schema validated
- the model the provider reported, checked against the model requested

## What is deliberately not measured

**No dollar figures.** Printing money would require a hardcoded price table that
is stale the week it ships, and a wrong cost number is worse than none. Tokens
and latency are what the provider actually reports, so tokens and latency are
what this reports. A reader who wants currency can multiply by today's price.

This is the same discipline as the ablation suite reporting a guard as redundant
and the principle surface deriving its own ceiling: measure, then say exactly
what was measured.

## Surface

- `POST /api/v1/studio/flows/:id/comparisons` — input, a model list, and an
  optional `repetitions`.
- `GET /api/v1/studio/comparisons` and `GET /api/v1/studio/comparisons/:id`.
- Creates N×R sibling Runs sharing one `comparison_id`, each with its override.
- A scoreboard renders the siblings side by side with the shared
  `flow_version_id` and fingerprint displayed as the proof of control.
- Invariance is the headline: whether every sibling reached the same routed
  outcome and every guard behaved identically is stated first, and the token and
  latency spread is a footnote to it. When siblings *do* route differently the
  scoreboard says so first, because that is the case invariance failed.

Only `comparison_id` and `model_override` are persisted, on `automation_runs`.
The comparison record, its scoreboard, the noise band and `disagreed` are all
derived by query from the siblings — the same discipline as `ratification_state`,
the distilled principles, and the principle ceiling. A derived scoreboard cannot
drift from the Runs it describes, because it is the Runs.

## Evidence class

A cross-model sweep is **its own evidence class and can never be a baseline**. A
baseline is model-pinned by definition, so a model-swapped run is not a baseline
candidate. This is marked structurally, not by convention, so no downstream
reader can mistake a sweep for "the score".

## Bounds

Model comparison spends the visitor's credit N×R times per command, so it is
bounded by the same per-workspace, per-address and global model budgets as any
other model action. The command declares its full forecast up front and the HTTP
layer charges the whole sweep before the first sibling runs, so an unaffordable
comparison is a refusal rather than a half-finished sweep whose remaining
siblings are silently missing. Siblings also register in `_active_studio_runs`
and take the bounded worker slot: the comparison command is not a privileged
execution path.

## Verification

- `tests/test_model_comparison.py` — the control: identical pinned
  `flow_version_id` across siblings, the override in the hash-linked chain,
  containment (`start_studio_run` raises `TypeError`), all-or-nothing refusal,
  and the scoreboard reporting only what the provider returned.
- `tests/test_model_comparison_integrity.py` — response-model verification and
  its three failure modes, the enforced-versus-uncontrolled split, repetitions
  retaining raw runs plus population variance, the noise band classifying a
  difference as `within_noise` / `signal` / `unmeasured`, the evidence-class
  marker, and the HTTP route including its budget refusal.
- The `ADD COLUMN` / table-rebuild migration checked against a database built by
  the previous revision: columns return, pre-existing rows keep NULL, rows are
  preserved byte-identically, `PRAGMA foreign_key_check` and `integrity_check`
  are clean, the three run-status triggers still refuse an illegal transition,
  and a real comparison then runs on the migrated database.
- A browser check that a comparison produces siblings sharing one
  `flow_version_id` and that overrides are visible.
- A real multi-model proof against the deployed host, archived in `evidence/`.

## Vocabulary note

The private blueprint calls this "Switch-the-Brain" and treats `Brain` as an
Agent-Spec axis. That name is **not** adopted here: no such concept exists in
the CE vocabulary authority, and coining a domain concept in the public cut
would create exactly the drift the Vocabulary-First rule prevents. This cut uses
plain descriptive terms. If the concept is to be canonical, it should be
ratified in CE first — that is the stack owner's call, not this repository's.
