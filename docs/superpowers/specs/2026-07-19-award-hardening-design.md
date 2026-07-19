# Award hardening design — Kyn.ist Agent Studio

Date: 2026-07-19
Status: approved, in implementation

## Purpose

The public Build Week cut is technically disciplined but has one broken flagship
view, one over-claimed capability, and no demonstration of the property that most
distinguishes it. This design closes all three and adds the capability that
answers the question judges actually ask: *what happens on the five-hundredth
Run, not the first?*

## Boundary rule (non-negotiable)

Everything here is **written independently against the public flat SQLite
projection**. No module, schema, migration, or query is imported or copied from
the private stack. What is reused is *vocabulary*, per the stack-wide
Vocabulary-First rule — the concept names below are the canonical CE names, so
the public cut and the private stack do not drift apart.

Canonical vocabulary adopted (source: CE vocabulary authority,
`/opt/ce/handover/featureflow-mesh/`, papers 02/04/05, homonym pin paper 08 §W):

| Term | Meaning here |
| --- | --- |
| `dead_end` | A durable, fingerprinted record that one exact approach failed. |
| `VETOES` | The relation `dead_end ──VETOES──▶ (flow_version, node)`. Mesh sense only. |
| `ratification_state` | `proposed` → `confirmed` → `canonical`. |
| `check_brake` | Read-only verdict. The brake pulls only on a `canonical` match. |

Homonym pin paper 08 §W is respected: this is the Mesh `VETOES` predicate, never
the Council `veto` vote value. The bare word `veto` is not used as an identifier.

## Investment 1 — Run graph renders

**Defect.** `src/components/RunsWorkbench.jsx:166` mounts a fully controlled
`<ReactFlow nodes={} edges={}>` with no `onNodesChange` / `onEdgesChange`. In
React Flow v12, node dimension measurements flow back through `onNodesChange`;
without it, measurements are discarded on every re-render. `RunsWorkbench.jsx:60`
polls every 900 ms and reconstructs the node array each tick, so `fitView`
computes bounds from unmeasured nodes and the viewport resolves empty.
`FlowStudio.jsx:337` passes the handler and renders correctly.

**Measured consequence (corrected after reproduction).** The initial reading of
this defect overstated it. A controlled experiment on one page, same session,
same five-node Flow, isolates the real blast radius:

```text
CONTROL[FlowStudio,    HAS onNodesChange]: rf_node=5, minimap_node=5
SUBJECT[RunsWorkbench,  NO onNodesChange]: rf_node=5, minimap_node=0
```

The canvas keeps rendering: node screen positions come from the explicit
`position` field of the pinned flow graph and need no measurement, and the
viewport transform is computed once at mount while measurements briefly exist,
then persists. The **MiniMap is deterministically broken** because it re-derives
from live `measured` dimensions that each poll discards.

`evidence/live/05-waiting-approval.png` and `06-run-evidence.png` are
nonetheless genuinely blank. Those were generated against the deployed public
HTTPS host with real model calls, where a Run sits in `waiting_approval` for
many seconds under real network latency while polling. That is the condition
under which the mount-timing race resolves to degenerate bounds, and it is the
path a judge uses. It could not be induced locally in eight attempts including
20x CPU throttling.

**Assertion choice.** The regression guard asserts **MiniMap node count**, not
canvas node count. Canvas count passes today and would guard nothing; MiniMap
count fails before the fix and passes after, so the guard has teeth.

**Fix.** Hold run graph nodes/edges in React Flow state, apply changes through
the official handlers, and derive from the polled snapshot without discarding
measurement state. Node identity must be stable across polls.

**Why it survived.** None of the 30 browser checks assert that the Run graph
renders nodes. The authoring canvas is asserted; the Run graph is not. The fix
ships with that assertion.

## Investment 2 — The ratification brake

**Thesis.** Most agent systems have a memory of what they did. This one has a
memory of what did not work, and that memory has veto power.

**Mechanism.** Deterministic. No model participates in any part of it.

1. On a terminal `failed` or `blocked` Run, derive a fingerprint over the exact
   failed approach:
   `sha256(canonical_json({flow_version_id, node_id, error_code, normalized_detail}))`.
   Normalization strips volatile substrings (ids, timestamps, digits) so the same
   fault recurs to the same fingerprint.
2. Append one `dead_end_evidence` row citing the Run. Append-only; never updated.
3. `ratification_state` is **derived**, never stored as mutable state:
   - `≥1` distinct Run → `proposed`
   - `≥2` distinct Runs → `confirmed`
   - `≥3` distinct Runs → `canonical`
   Counting is over *distinct* Runs, so one Run retried cannot ratify anything.
4. `check_brake` runs before Run enqueue. It is read-only and returns a verdict.
   It refuses **only** when a `canonical` dead_end matches the exact
   `(flow_version_id, node_id)` path the candidate would traverse.
5. A refused Run is not created. Zero Steps, zero effects. The refusal cites the
   three prior Run IDs and their hash-linked events.

**Escape hatch.** A repair that publishes a successor Flow version produces a new
`flow_version_id`, therefore a new fingerprint, therefore no brake. Fixing the
problem always clears the brake; only repeating it unchanged is refused. This is
the property that makes the brake safe rather than a trap.

**Surface.** The Run detail shows a dead_end panel with state, distinct-Run
count, and links to the citing events. The refusal is returned as a typed error
the Studio renders with its citations.

**Invariants.** `dead_end_evidence` is append-only, enforced by trigger like
every other evidence table. `(dead_end_fingerprint, run_id)` is UNIQUE so one Run
cannot inflate a count.

## Investment 3 — Generalized repair space

**Defect.** `backend/studio_store.py:2554` hardcodes the only repair this system
can ever propose:

```python
patch = [{"op": "replace", "path": "/config/write_enabled", "value": True}]
```

Everything else raises `"diagnosis has no bounded automatic repair"`. The
fencing around it is production-grade; the thing being fenced is one boolean.
README and devpost describe this as "diagnose, repair, and rerun", which
over-claims.

**Fix.** Introduce a declarative repair policy per Action kind: an allow-list of
JSON-Pointer paths, permitted operations, and value constraints. Proposal
computes a patch *against the policy* instead of returning a literal. The
existing proposal-hash, dual revision fence, acknowledgement, and post-condition
recheck are unchanged — they now fence a real space instead of a single point.

The legacy engine (`backend/runtime.py:621`) already validates model-proposed
patches against `repair_policy.allowed_paths` / `allowed_operations`. That is the
better design and it is re-expressed in the v4 path. No code is copied; the
mechanism is rebuilt to the v4 contracts.

**Bound.** A repair may still only touch paths the pinned policy allows, may
still only widen configuration within declared value constraints, and still
produces immutable Action and Flow successors. Generalizing the space does not
loosen a single fence.

## Investment 4 — Guard ablation suite

**Thesis.** Every team demonstrates that their system works. This demonstrates
*why* it works, by proving each guard is load-bearing.

For each guard, the suite disables exactly that guard in an isolated harness and
asserts that one specific, documented violation becomes reachable. A guard whose
ablation changes nothing is decorative and the suite fails.

| Guard | Site | Violation reachable when ablated |
| --- | --- | --- |
| Skill authority intersection | `studio_runtime.py:1323` | Model invokes an ungranted Action |
| Tool-call budget | `studio_runtime.py:1287` | Unbounded tool turns |
| Evidence citation subset | `service.py:1146` | Diagnosis cites another Run's evidence |
| Repair revision fence | `studio_store.py:2654` | Stale proposal applies |
| Terminal absorption trigger | `schema.py:622` | Terminal Run mutates |
| Event hash chain | `contracts.py:475` | Tampered chain verifies |
| Ratification brake | Investment 2 | Canonical dead end re-executes |

**Safety.** This is a repository verification artifact, run as
`scripts/verify.py --ablation`. It is **not** a runtime switch and ships no
ablation path reachable from the deployed public service. Ablation is performed
by test-local substitution inside the harness only. Adding a live "disable the
authority gate" control to a public deployment would be indefensible.

## What is deliberately not built

- No live ablation toggle in the public application (see above).
- No knowledge graph, node/edge substrate, trust cells, or governance council.
  The brake here ratifies over Run repetition; the private stack ratifies the
  same way over a knowledge graph with constraint-trigger invariants, per-producer
  trust cells, and quorum distillation across independent sources. That sentence
  is the honest boundary statement, and it is all the boundary statement needed.

## Verification

Work is complete only when all of the following pass and the output is recorded:

- `.venv/bin/python scripts/verify.py` — full Python, HTTP, DB, security, static
- `node scripts/browser_verify.mjs` — product journey, now including the Run
  graph node assertion and a brake refusal check
- `.venv/bin/python scripts/verify.py --performance` — 64-node gates
- `.venv/bin/python scripts/verify.py --ablation` — every guard load-bearing
- Regenerated `evidence/` screenshots showing a rendered Run graph

Green tests alone do not prove runtime truth. Screenshots must show the fix.

## Git

Forward only. No reset, revert, stash, rebase, amend, squash, or branch switch.
`git add -A` and a new commit at each stable state.
