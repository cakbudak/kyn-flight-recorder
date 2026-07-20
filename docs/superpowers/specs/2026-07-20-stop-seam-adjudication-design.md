# Stop-seam adjudication — design

Date: 2026-07-20 · Status: design, pre-implementation

## Why this exists

Every agent runtime lets the model decide when it is finished. The loop ends
because the model emitted something that parsed as done. This is the one place
where almost every system on the market hands a model real authority, and it is
invisible precisely because it looks so ordinary.

This product's thesis is that model text is data and never authority. Applied to
the most consequential sentence an agent ever produces — *I am finished* — the
thesis says that sentence is evidence, not proof.

## The trap, stated before the design

Adding a judge model naively moves the authority up one level instead of removing
it. The worker's claim stops being trusted and the judge's claim takes its place.
A reviewer who spots that has dismantled the feature in one sentence, and they
would be right to.

So the design constraint is not "add a judge". It is:

> Add a judge whose approval is worthless on its own.

## This is not a new idea in this codebase

The move already exists here, in the diagnosis path, and this design is that same
move applied at the one seam where it is missing.

`explain_diagnosis` does not ask a model what went wrong and believe it. Code
computes the candidate fault class and the candidate evidence set
deterministically; the model may only *narrow and narrate* what code already
produced, under a strict schema; and the store then independently re-verifies the
model's citations against ground truth before persisting, refusing anything that
cites outside its own Run.

Completion is the seam where that discipline was never applied. Applying it there
is consistency, not novelty, and consistency is the stronger claim.

## Vocabulary

Canonical names are taken from the stack's language authority rather than coined
here, so the public cut and the private runtime keep one meaning. Terms new to
this feature were defined in that authority first.

- **Goal-Judge** — the adversarial completion checker at the stop seam.
- **stop seam** — the point at which work claims to be finished.
- **task gate** — the deterministic, LLM-free check over run-owned truth.
- **acceptance criterion** — a named, declared condition, pinned to a Flow
  version, carrying an id, a prose statement, and the admissible evidence kind.
- **evidence anchor** — a pointer from a judgement to exactly one run-owned
  evidence record, by its stable id. Deliberately *not* called a citation: in this
  stack a citation points into the knowledge graph and is checked against
  grounding signals. An anchor points into run evidence and is resolved against
  the run database. Two registers, one metaphor, kept apart on purpose.
- **anchor resolution** — the deterministic filtering of anchors.
- **`completion_unevidenced`** — the error code of a Run whose completion claim
  was not covered by resolved anchors.

## The design

### 1. Declaration — acceptance criteria on the Flow version

Criteria are Flow-level, following the existing `outcomes` precedent exactly: a
named list, normalized in `contracts.py`, threaded into the version material so it
enters the version fingerprint. That buys immutability and pinning for free, and
it means changing a criterion publishes a successor version — which is also what
keeps the existing dead-end escape hatch working.

Zero criteria is the default and means the feature is inert: no judge call, no
model spend, no behaviour change for every Flow that exists today.

Each criterion declares an **evidence kind** from a closed vocabulary, each kind
bound to a collection and a state predicate:

| kind | resolves against | admissible state |
| --- | --- | --- |
| `effect` | run effects | any (an effect only exists if it happened) |
| `receipt` | action receipts | `outcome = succeeded` |
| `approval` | approval decisions | `approved = true` |
| `step` | run steps | `status = completed` |

A criterion also pins the **`node_id`** whose work must evidence it.

**This was a correction, and the reason is worth keeping.** The first version of
this spec had a criterion declare only a kind. Review caught what that means: an
`effect` criterion would be satisfied by *any* effect the Run wrote — any node,
any collection, any payload. The resolver would still filter fabricated anchors
and foreign-Run anchors, but it could not filter **irrelevance**, so a criterion
reading "the report was published" would be satisfied by an unrelated sandbox
write. Filtering fabrication while admitting irrelevance is not a contract; it is
the appearance of one.

Pinning the node makes every anchor attributable. `receipt` and `step` carry
`node_id` directly; an `approval` anchor is the *decision* id and attributes
through its request; an `effect` attributes through its step. Anchors are resolved
against the node the criterion named, not against the Run at large.

Refusal precedence is fixed and ordered: existence, then ownership, then kind,
then state. A foreign-Run record of the wrong kind reports `anchor_foreign_run`,
because the record was never this Run's to reason about in the first place.

### 2. Publication guards — two deterministic refusals

**Unsatisfiable contract.** A criterion whose evidence kind no node in the pinned
graph can ever mint is refused at publication:

> A Flow may not declare a contract its own pinned graph cannot possibly satisfy.

An `effect` criterion needs a node whose pinned Action can write; an `approval`
criterion needs a human-approval node. This is decidable from the pinned node set
without running anything, so it costs no Runs and needs no three-strike wait.

**Self-adjudication.** The judge Agent version may not be an Agent version pinned
by any node of the Flow. Independence is a property of the casting, not of the
prompt. Nobody grades their own homework.

### 3. The stop seam

The runtime has exactly one place where a Run becomes `completed`, and the
transition is guarded by terminal absorption: once `completed`, the status can
never change again. So adjudication must happen *before* that transition, while
the Run is still running. It also must not annotate after the fact — a post-hoc
judgement could only ever comment on a completion it was too late to prevent.

Order of operations at the seam, with no criteria declared short-circuiting at
step 1:

1. If the pinned Flow version declares no criteria, complete as today.
2. Code assembles the **candidate anchor set** deterministically: this Run's
   effects, succeeded receipts, approved decisions, and completed steps.
3. The Goal-Judge is called with the criteria and the candidate set, prompted
   adversarially — which criteria are *unevidenced*, what was claimed but not
   performed — under a strict schema, recorded as model evidence exactly like the
   diagnosis call.
4. **Narrowing gate.** Every anchor returned must be a subset of the candidate
   set code handed over. Anything else is a contract violation, not a warning.
5. **Resolution gate,** independently, in the store: each anchor must exist,
   belong to this Run, be of an admissible kind for its criterion, and be in a
   state that can carry the claim.
6. Completion is admitted only if every declared criterion holds at least one
   surviving anchor. Otherwise the Run fails with `completion_unevidenced`.

Gates 4 and 5 are deliberately redundant. Gate 4 is the narrow check against what
code offered; gate 5 is the independent check against ground truth. The diagnosis
path is double-gated the same way, for the same reason: one gate can be edited by
someone who does not know the other exists.

**The redundancy can collapse silently, so it is pinned here.** The evidence
bundle handed to the resolver must be a *lookup table over the Run's records*, not
the pre-filtered candidate set. If the seam passes the candidate set as the
bundle, gate 4 becomes a tautology and the redundancy disappears without anybody
deleting a line — and worse, the ownership check becomes unfalsifiable, which
would make the foreign-Run ablation impossible to write. Gate 4 belongs in the
seam as an explicit subset check against what code offered; gate 5 resolves
against everything the Run actually has.

### 4. The asymmetry, as a stated property

The judge may only ever **narrow**, never widen.

- It may refuse a completion for reasons the runtime does not check. Refusal is
  the safe direction and is allowed unconditionally.
- It cannot admit a completion the evidence does not already support.

A judge that fabricates an evidence id cannot admit a completion. A judge that is
over-eager, miscalibrated, or outright compromised is inert in the dangerous
direction, because its generosity is filtered by a deterministic resolver working
over material a model cannot write. Models emit text; receipts, effects and events
are minted by the runtime.

### 5. Not ratifiable, and why

A first instinct was that repeated completion refusal is a structural defect and
therefore ratifiable by the brake. The product's own precedent disproves it.
`assertion_rejected` is refused ratification for exactly this shape: a gate can
reject three different bad inputs, sharing one fingerprint, and ratifying that
would brake a Flow that valid input can still pass.

A criterion can go unevidenced because *this* Run's input never reached the work.
That is a property of the data, not of the definition. So
`completion_unevidenced` joins `NON_RATIFIABLE_FAULTS` with its reason recorded,
consistent with the precedent rather than contradicting it. The structural case is
caught earlier and better by the publication guard.

No in-Run retry loop is introduced. A refused Run is terminal; a second attempt is
a linked rerun, which the product already supports.

## Verification plan

- Unit: the resolver is a pure module; each refusal code proved independently.
- Unit: publication refuses an unsatisfiable contract and self-adjudication.
- Integration: a Run with one unmet criterion fails `completion_unevidenced` and
  no `completed` transition occurs; the same Flow with the work performed
  completes with every criterion anchored.
- Ablation over the resolver's **site** check: an effect minted at another node
  satisfies a criterion pinned elsewhere. **Measured load-bearing.**
- Ablation over the resolver's **state** check: a Step the database records as
  failed evidences success. **Measured load-bearing.**
- Ablation over the seam's **anti-fabrication** gate. **Measured redundant, and
  this spec was wrong about it.** An earlier draft called it "the load-bearing
  one". Measurement disagreed: with the gate deleted a fabricated anchor does
  reach the resolver, but the resolver's existence check refuses it and the Run
  still never completes. What the gate uniquely buys is *diagnosis* — with it a
  Run reports `contract_violation` and an invented citation is legible as one;
  without it the same Run reports the blander `completion_unevidenced`, where a
  fabricating judge is indistinguishable from an honest empty-handed one. That
  is worth keeping, and it is not what "load-bearing" means. The claim is
  corrected here rather than quietly dropped, because a verification plan that
  predicts the wrong result and is then edited to match is worth less than one
  that records having been wrong.

Both product-level violations are stated in one sentence: *a Run reports
`completed` while a declared acceptance criterion is unmet.*
- Journey: refuse-then-admit, both visible in the ledger, hash-chained.

## What this deliberately does not do

- It does not let the judge write, approve, repair, or resume anything.
- It does not introduce a second execution engine, a retry loop, or a new terminal
  status.
- It does not claim the judge is correct. It claims the judge cannot be
  dangerously wrong, which is a smaller and provable claim.
