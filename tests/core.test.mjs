import assert from "node:assert/strict";
import test from "node:test";

import {
  APPROVAL_DEMO_BRIEF,
  TERMINAL_RUN_STATUSES,
  completionAdjudication,
  exampleForSchema,
  isActiveRun,
  latestStepForNode,
  maintenancePhase,
  selectedStudioRun
} from "../src/lib.js";

function run(overrides = {}) {
  return {
    id: "run_root",
    parent_run_id: null,
    flow_version: 1,
    status: "blocked",
    diagnosis: null,
    repair: null,
    steps: [],
    ...overrides
  };
}

test("run liveness is derived from the authoritative terminal set", () => {
  assert.deepEqual(TERMINAL_RUN_STATUSES, ["completed", "blocked", "failed", "cancelled"]);
  assert.equal(isActiveRun(run({ status: "running" })), true);
  assert.equal(isActiveRun(run({ status: "waiting_approval" })), true);
  assert.equal(isActiveRun(run({ status: "completed" })), false);
  assert.equal(isActiveRun(null), false);
});

test("the latest attempt for a graph node is selected without mutation", () => {
  const source = run({
    steps: [
      { id: "step_1", node_id: "analyze", attempt: 1 },
      { id: "step_2", node_id: "other", attempt: 1 },
      { id: "step_3", node_id: "analyze", attempt: 2 }
    ]
  });
  const before = JSON.stringify(source);
  assert.equal(latestStepForNode(source, "analyze").id, "step_3");
  assert.equal(latestStepForNode(source, "missing"), null);
  assert.equal(JSON.stringify(source), before);
});

test("maintenance advances only through diagnosis, proposal, application, and proof", () => {
  const root = run();
  assert.equal(maintenancePhase(root), "failed");
  root.diagnosis = { id: "diag_1" };
  assert.equal(maintenancePhase(root), "diagnosed");
  root.repair = { id: "repair_1", status: "proposed" };
  assert.equal(maintenancePhase(root), "proposed");
  root.repair = { id: "repair_1", status: "applied", applied_flow_version: 2 };
  assert.equal(maintenancePhase(root), "applied");
  const proof = run({ id: "run_proof", parent_run_id: root.id, relation_kind: "proof", flow_version: 2, status: "completed" });
  assert.equal(maintenancePhase(root, [root, proof]), "proven");
});

test("an unrelated or failed child cannot prove a repair", () => {
  const root = run({
    diagnosis: { id: "diag_1" },
    repair: { id: "repair_1", status: "applied", applied_flow_version: 2 }
  });
  const unrelated = run({ id: "run_other", parent_run_id: "another", flow_version: 2, status: "completed" });
  const failed = run({ id: "run_failed", parent_run_id: root.id, flow_version: 2, status: "failed" });
  assert.equal(maintenancePhase(root, [root, unrelated, failed]), "applied");
});

test("run selection uses the Studio projection and honors an explicit ID", () => {
  const root = run();
  const child = run({ id: "run_child", status: "completed" });
  const snapshot = { studio: { runs: [child, root] } };
  assert.equal(selectedStudioRun(snapshot), child);
  assert.equal(selectedStudioRun(snapshot, root.id), root);
  assert.equal(selectedStudioRun(snapshot, "foreign"), child);
  assert.equal(selectedStudioRun({}, "foreign"), null);
});

test("healthy runs do not expose a maintenance workflow", () => {
  assert.equal(maintenancePhase(run({ status: "completed" })), "not-required");
  assert.equal(maintenancePhase(null), "unavailable");
});

test("the stop-seam verdict is read off the ledger and never derived in the browser", () => {
  const refused = { type: "completion.refused", payload: { admitted: false, unevidenced: ["c1"], criteria: [] } };
  assert.equal(completionAdjudication(run({ events: [{ type: "run.started", payload: {} }] })), null);
  assert.equal(completionAdjudication(run()), null);
  assert.equal(completionAdjudication(null), null);
  const source = run({ events: [{ type: "completion.admitted", payload: { admitted: true } }, refused] });
  assert.equal(completionAdjudication(source), refused.payload);
  // Admission is the server's verdict, not a count of surviving anchors: a
  // payload that says refused stays refused however its criteria read.
  assert.equal(completionAdjudication(run({ events: [{ type: "completion.refused", payload: { admitted: false, unevidenced: [], criteria: [{ criterion_id: "c1", holds: true, surviving: ["effect_1"], discarded: [] }] } }] })).admitted, false);
});

test("the seeded AI Run example states every readiness boundary explicitly", () => {
  const example = exampleForSchema({
    type: "object",
    properties: { brief: { type: "string" } },
    required: ["brief"],
    additionalProperties: false
  });
  assert.equal(example.brief, APPROVAL_DEMO_BRIEF);
  for (const contract of [
    "Target audience:",
    "Typed input contract:",
    "Typed output contract:",
    "Deterministic decision boundary:",
    "Human authority boundary:",
    "Bounded effect scope:",
    "Inspectable evidence:",
    "Measurable success condition:"
  ]) {
    assert.match(example.brief, new RegExp(contract));
  }
});
