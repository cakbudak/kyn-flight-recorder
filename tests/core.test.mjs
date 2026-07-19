import assert from "node:assert/strict";
import test from "node:test";

import {
  PHASE_ORDER,
  childRunFor,
  phaseFor,
  rootRunFor,
  selectedRunFor
} from "../app/state.mjs";

function run(overrides = {}) {
  return {
    id: "run_root",
    parent_run_id: null,
    status: "blocked",
    diagnosis: null,
    repair: null,
    events: [],
    ...overrides
  };
}

function snapshot(...runs) {
  return { runs };
}

test("the UI phase contract starts with a composed flow", () => {
  assert.equal(phaseFor(snapshot()), "ready");
  assert.deepEqual(PHASE_ORDER, ["ready", "blocked", "diagnosed", "repair", "applied", "proven"]);
});

test("a blocked run advances only through evidence, proposal, and approval", () => {
  const root = run();
  const state = snapshot(root);
  assert.equal(phaseFor(state), "blocked");

  root.diagnosis = { id: "diag_1" };
  assert.equal(phaseFor(state), "diagnosed");

  root.repair = { id: "rpr_1", status: "proposed" };
  assert.equal(phaseFor(state), "repair");

  root.repair.status = "applied";
  assert.equal(phaseFor(state), "applied");
});

test("only a completed linked child proves the changed outcome", () => {
  const root = run({
    repair: { id: "rpr_1", status: "applied" }
  });
  const unrelated = run({
    id: "run_unrelated",
    parent_run_id: "run_other",
    status: "completed"
  });
  assert.equal(phaseFor(snapshot(unrelated, root)), "applied");

  const child = run({
    id: "run_child",
    parent_run_id: root.id,
    status: "completed"
  });
  assert.equal(childRunFor(snapshot(child, root), root), child);
  assert.equal(phaseFor(snapshot(child, root)), "proven");
});

test("provider failures never masquerade as policy failures", () => {
  assert.equal(phaseFor(snapshot(run({ status: "failed" }))), "failed");
  const root = run({ repair: { id: "rpr_1", status: "applied" } });
  const failedChild = run({
    id: "run_child",
    parent_run_id: root.id,
    status: "failed"
  });
  assert.equal(phaseFor(snapshot(failedChild, root)), "failed");
});

test("run selection is explicit and otherwise prefers the linked child", () => {
  const root = run();
  const child = run({
    id: "run_child",
    parent_run_id: root.id,
    status: "completed"
  });
  const state = snapshot(child, root);
  assert.equal(rootRunFor(state), root);
  assert.equal(selectedRunFor(state), child);
  assert.equal(selectedRunFor(state, root.id), root);
  assert.equal(selectedRunFor(state, "foreign"), child);
});

test("phase derivation never mutates the server projection", () => {
  const state = snapshot(run({ diagnosis: { id: "diag_1" } }));
  const before = JSON.stringify(state);
  assert.equal(phaseFor(state), "diagnosed");
  assert.equal(JSON.stringify(state), before);
});
