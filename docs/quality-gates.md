# Quality gates

Date: 2026-07-21

The active gate set belongs to **Kyn.ist Agent Studio**. Earlier prototype
reports are superseded and are not accepted as evidence for this product surface.

| Gate | Evidence | Status |
| --- | --- | --- |
| define | browser creates a strict-schema Action and immutable version | PASS |
| compose | browser creates a Flow with a pinned Action version and explicit input mapping | PASS |
| route | Router exposes four named ports and the Run records the exact selected outcome | PASS |
| reuse | published Flow executes as an evidence-linked child Run with a shared correlation ID | PASS |
| execute | user-defined deterministic Flow completes without an OpenAI credential | PASS |
| AI contract | AI Action pins Agent → Prompt + Skills; strict final schema and exact Action tools share one Responses request | PASS |
| stateless tools | `store:false` tool turns preserve provider reasoning items and encrypted reasoning context while removing response-only replay metadata | PASS |
| observe | Steps, model attempts, receipts, approvals, effects, and hash-linked events persist in SQLite | PASS |
| approve | Run pauses before effect; attributable human decision resumes the same pinned graph | PASS |
| rerun | terminal parent remains immutable; linked child pins the same Flow version and owns its evidence | PASS |
| trigger | webhook and schedule bindings pin immutable Flow versions and create real Runs | PASS |
| stop seam | authorable promises pin kind/site/Judge; unsupported completion refuses and supported completion admits on the identical Flow version | PASS |
| judge authority | Judge output is retained as a claim; fabricated, foreign-Run, wrong-kind, wrong-site, and wrong-state anchors cannot admit | PASS |
| ratification | three independent structural failures refuse the unchanged Flow version before a fourth Run is created | PASS |
| principles | only three distinct Flows distil an advisory; advice never blocks | PASS |
| Capability Forge | completed model Step → different logical Agent resource as strict distiller → 8/8 deterministic provenance/authority gates → acknowledged immutable Skill v1 | PASS |
| comparison | expected siblings are manifested pre-I/O; model alias, missing evidence, or broken ledger makes the result unusable | PASS |
| repair | failed Run proves owned diagnosis → bounded successor → human dual-revision fence → linked changed outcome | PASS |
| SmartRead | immutable admitted source → intent-bounded read → exact version/fingerprint/line citation; no path fetch or model summary | PASS |
| Flow-native context | cited SmartRead result → complete editable four-node draft → strict source/Memory envelopes → nested BoardRoom Run; promoted Memory enters the next unchanged Flow Run automatically | PASS |
| parallel BoardRoom | three independent member calls → separately persisted child Steps → configured 2/3 barrier computes the actual verdict distribution and preserves dissent → one pinned editor | PASS |
| BoardRoom editing | generated room opens as an ordinary full Flow; pinned targets/quorum/routes publish v2 while prior Runs retain v1 and published member IDs remain stable downstream schema keys | PASS |
| governed Memory | completed verified Run → cited quarantine → deterministic qualification → exact-fingerprint human promotion → provenance-bearing recall | PASS |
| provider failure | failed OpenAI attempt is append-only evidence with safe code/parameter/request ID, never raw provider text | PASS |
| database | flat explicit product tables, immutable version/evidence triggers, legal transition and revision fences | PASS |
| authority | Skill grants exact Action-version IDs; model prose cannot widen authority or create effects | PASS |
| isolation | opaque HttpOnly workspace cookie, same-origin mutations, cross-workspace 404, bounded bodies and usage | PASS |
| credential | browser-tab `sessionStorage` only; server has no operator-key fallback and never persists the key | PASS |
| browser | desktop + legible pannable 390 px graph, keyboard-contained dialogs, reduced motion, named controls, no overflow, failed request, or console error | PASS |
| contrast | 4,310 visible text samples across all thirteen workbenches in light/dark; minimum 4.70:1 | PASS |
| maximum graph | 64 nodes/63 routes: 20 complete Runs, 30 loaded snapshots, 64-node Chromium render and Fit View remain below release thresholds | PASS |
| real model | real GPT-5.6 analysis pauses, approves, and commits exactly one bounded SQLite effect | PASS |
| public HTTPS | full real-model Studio journey through the deployed origin | PASS |
| assistive tech | physical screen-reader pass | NOT RUN |

## Current verification

```text
291 Python runtime/database/HTTP/security/UI tests:       PASS
  9 pure browser-state tests:                            PASS
 55 Chromium full-stack Studio checks:                   PASS
 55 public HTTPS + real GPT-5.6 checks:                   PASS
  7 maximum-graph Chromium load checks:                  PASS
  0 npm audit vulnerabilities:                           PASS
```

The maximum-graph gate measures the product's declared 64-node limit, rather
than a reduced sample. Twenty deterministic Runs produced 64 completed Steps
and 197 valid hash-linked events each. On the current release host, Run p95 was
397.676 ms against a 2,000 ms limit and loaded-workspace snapshot p95 was
181.487 ms against a 400 ms limit. Chromium rendered 64 nodes and 63 edges in
207.247 ms and completed Fit View in 111.048 ms, with zero document overflow,
failed requests, or page errors.

The public real-model proof executed the official Python SDK with a per-operation
browser key, produced a strict typed analysis, crossed a deterministic condition,
stopped at Human approval with zero effects, then resumed to exactly one
idempotent SQLite sandbox effect. Its controlled failure then used a real GPT-5.6
diagnostician constrained to a code-owned causal candidate and Run-owned event
IDs before publishing and proving a successor. A separate real forced two-turn
SDK run proved function-call output round-tripping with `store:false`, strict
final Structured Output, and the same tool definitions on both turns. That run
also exposed and permanently covered the provider response-only `status` replay
field returned by GPT-5.6.

The expanded live stop-seam proof first exposed two distinct operational
failures and then passed after both were covered by RED/GREEN contracts. A known
Goal-Judge provider failure had escaped the terminal seam and been flattened to
`worker_failure`; it now retains `provider_failure` and its failed model-attempt
evidence. Separately, the HTTP model-call forecast counted graph nodes but not a
Flow-level Goal-Judge, so it discarded the browser-owned client before a
judge-only Flow ran. The forecast now charges the terminal Judge once, including
when that Judge is reached through a pinned subflow. The public refuse/admit pair
uses one real Judge call per Run and passes on the identical Flow version.

## Reproduction

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/verify.py --performance
npm ci
node scripts/browser_verify.mjs
```

The local browser run uses provider-shaped deterministic responses while keeping
the real HTTP API, control plane, SQLite stores, Action dispatcher, triggers,
approvals, integrated maintenance, and Chromium UI. The committed public report was generated by the
same runner against `https://buildweek.kyn.ist` and the official OpenAI service.

The local and deployed 55-check journeys prove the full Capability Forge path in the
browser: a completed source model Step, a different logical distiller Agent, one
strict tool-free Responses call, eight deterministic provenance/authority checks, and
an acknowledged Skill v1 publication with zero changed Agent versions. The
deployed real-model pass cited its source-ledger events and audited 4,318 visible
text samples across all thirteen workbenches with the same 4.70:1 minimum.

Green UI checks alone are not runtime proof. Database invariants, negative
authority tests, provider-shaped multi-turn tests, authoritative receipts, a real
OpenAI call, and before/after linked-Run evidence are required together.
