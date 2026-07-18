import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  ContractError,
  applyCommand,
  createInitialState,
  createSessionRecord,
  eventsForSource,
  findNode,
  previewCommand,
  redactForDisplay,
  restoreSession,
  selectNode,
  validateFixture
} from "../app/core.mjs";

const fixturePath = new URL("../app/data/demo-run.json", import.meta.url);

function fixture() {
  return JSON.parse(readFileSync(fixturePath, "utf8"));
}

function authorization(overrides = {}) {
  return {
    actor: "build-week-judge",
    reason: "Evidence is verified and the synthetic scope is bounded.",
    acknowledged: true,
    ...overrides
  };
}

function expectCode(code, operation) {
  assert.throws(operation, (error) => {
    assert.ok(error instanceof ContractError);
    assert.equal(error.code, code);
    return true;
  });
}

test("the signed fixture satisfies the v1 contract", () => {
  const verdict = validateFixture(fixture());
  assert.deepEqual(verdict, { ok: true, issues: [] });
});

test("unsupported schema versions fail closed", () => {
  const candidate = fixture();
  candidate.schema_version = "2.0";
  const verdict = validateFixture(candidate);
  assert.equal(verdict.ok, false);
  assert.ok(verdict.issues.some((issue) => issue.path === "schema_version"));
});

test("unknown run and node states fail closed", () => {
  const candidate = fixture();
  candidate.run.status = "mysterious";
  candidate.nodes[0].status = "maybe";
  const verdict = validateFixture(candidate);
  assert.equal(verdict.ok, false);
  assert.ok(verdict.issues.some((issue) => issue.path === "run.status"));
  assert.ok(verdict.issues.some((issue) => issue.path === "nodes[0].status"));
});

test("foreign correlation ids are rejected", () => {
  const candidate = fixture();
  candidate.events[2].correlation_id = "corr_foreign";
  const verdict = validateFixture(candidate);
  assert.equal(verdict.ok, false);
  assert.ok(verdict.issues.some((issue) => issue.path === "events[2].correlation_id"));
});

test("dangling graph edges are rejected", () => {
  const candidate = fixture();
  candidate.edges[0].to = "missing_node";
  const verdict = validateFixture(candidate);
  assert.equal(verdict.ok, false);
  assert.ok(verdict.issues.some((issue) => issue.path === "edges[0]"));
});

test("event sequence gaps and duplicate ids are rejected", () => {
  const candidate = fixture();
  candidate.events[1].sequence = 8;
  candidate.events[1].id = candidate.events[0].id;
  const verdict = validateFixture(candidate);
  assert.equal(verdict.ok, false);
  assert.ok(verdict.issues.some((issue) => issue.path === "events.*.id"));
  assert.ok(verdict.issues.some((issue) => issue.path === "events.*.sequence"));
});

test("standalone traces cannot claim an external effect", () => {
  const candidate = fixture();
  candidate.run.impact.external_effect = true;
  const verdict = validateFixture(candidate);
  assert.equal(verdict.ok, false);
  assert.ok(verdict.issues.some((issue) => issue.path === "run.impact.external_effect"));
});

test("required secret classes cannot be removed from redaction", () => {
  const candidate = fixture();
  candidate.redaction.keys = candidate.redaction.keys.filter((key) => key !== "token");
  const verdict = validateFixture(candidate);
  assert.equal(verdict.ok, false);
  assert.ok(verdict.issues.some((issue) => issue.message.includes("token")));
});

test("secret-bearing keys are redacted recursively", () => {
  const redacted = redactForDisplay(
    {
      token: "top-level",
      nested: {
        claim_token: "nested",
        safe: "visible",
        children: [{ api_key: "array-value" }]
      }
    },
    fixture().redaction
  );
  assert.equal(redacted.token, "•••••••• (redacted)");
  assert.equal(redacted.nested.claim_token, "•••••••• (redacted)");
  assert.equal(redacted.nested.children[0].api_key, "•••••••• (redacted)");
  assert.equal(redacted.nested.safe, "visible");
});

test("initial state never carries raw credential fixture values", () => {
  const state = createInitialState(fixture());
  const serialized = JSON.stringify(state);
  assert.equal(serialized.includes("SYNTHETIC_VALUE"), false);
  assert.equal(serialized.includes("REDACTED_BY_FIXTURE"), false);
  assert.ok(serialized.includes("•••••••• (redacted)"));
});

test("initial state is isolated from later fixture mutations", () => {
  const source = fixture();
  const state = createInitialState(source);
  source.run.goal = "mutated after validation";
  source.nodes[0].title = "mutated";
  assert.notEqual(state.run.goal, source.run.goal);
  assert.notEqual(state.nodes[0].title, source.nodes[0].title);
});

test("preview is side-effect free and exposes no external effect", () => {
  const state = createInitialState(fixture());
  const before = JSON.stringify(state);
  const preview = previewCommand(state);
  assert.equal(preview.external_effect, false);
  assert.equal(preview.expected_revision, 7);
  assert.equal(JSON.stringify(state), before);
});

test("authorization requires the pinned actor", () => {
  const state = createInitialState(fixture());
  expectCode("ACTOR_MISMATCH", () =>
    applyCommand(state, authorization({ actor: "another-operator" }))
  );
});

test("authorization requires a bounded reason", () => {
  const state = createInitialState(fixture());
  expectCode("INVALID_REASON", () => applyCommand(state, authorization({ reason: "too short" })));
  expectCode("INVALID_REASON", () => applyCommand(state, authorization({ reason: "x".repeat(281) })));
});

test("authorization requires local-simulation acknowledgement", () => {
  const state = createInitialState(fixture());
  expectCode("ACK_REQUIRED", () =>
    applyCommand(state, authorization({ acknowledged: false }))
  );
});

test("revision conflicts fail before any transition", () => {
  const state = createInitialState(fixture());
  state.run.revision = 8;
  const before = JSON.stringify(state);
  expectCode("REVISION_CONFLICT", () => applyCommand(state, authorization()));
  assert.equal(JSON.stringify(state), before);
});

test("the legal command advances exactly one revision and appends evidence", () => {
  const initial = createInitialState(fixture());
  const result = applyCommand(initial, authorization());
  assert.equal(result.duplicate, false);
  assert.equal(result.state.run.status, "completed");
  assert.equal(result.state.run.revision, 8);
  assert.equal(result.state.events.length, 9);
  assert.deepEqual(result.state.events.map((event) => event.sequence), [1, 2, 3, 4, 5, 6, 7, 8, 9]);
  assert.equal(findNode(result.state, "approval").fields.decision, "approved");
  assert.equal(findNode(result.state, "effect").fields.executed, true);
  assert.equal(findNode(result.state, "terminal").status, "completed");
  assert.equal(result.receipt.from_revision, 7);
  assert.equal(result.receipt.to_revision, 8);
  assert.equal(result.receipt.external_effect, false);
  assert.equal(initial.run.status, "blocked", "the input state remains immutable");
});

test("a duplicate command returns the existing receipt without another event", () => {
  const once = applyCommand(createInitialState(fixture()), authorization());
  const twice = applyCommand(once.state, authorization({ reason: "A different duplicate reason is ignored." }));
  assert.equal(twice.duplicate, true);
  assert.equal(twice.state, once.state);
  assert.deepEqual(twice.receipt, once.receipt);
  assert.equal(twice.state.events.length, 9);
});

test("a terminal without an owned receipt absorbs new commands", () => {
  const state = createInitialState(fixture());
  state.run.status = "completed";
  expectCode("TERMINAL_ABSORBS", () => applyCommand(state, authorization()));
});

test("session records rehydrate the one legal transition", () => {
  const applied = applyCommand(createInitialState(fixture()), authorization()).state;
  const record = createSessionRecord(applied);
  const restored = restoreSession(fixture(), record);
  assert.equal(restored.run.status, "completed");
  assert.equal(restored.events.length, 9);
  assert.deepEqual(restored.command.receipt, applied.command.receipt);
});

test("session records are fixture-bound", () => {
  const initial = createInitialState(fixture());
  const record = {
    version: 1,
    fixture_id: "another-fixture",
    schema_version: initial.schema_version,
    command_id: initial.intervention.command_id,
    ...authorization()
  };
  expectCode("SESSION_MISMATCH", () => restoreSession(fixture(), record));
});

test("node selection and source filtering remain explicit", () => {
  const initial = createInitialState(fixture());
  const selected = selectNode(initial, "queue");
  assert.equal(findNode(selected).id, "queue");
  assert.equal(eventsForSource(selected, "queue_engine.lease").length, 1);
  expectCode("UNKNOWN_NODE", () => selectNode(initial, "missing"));
});
