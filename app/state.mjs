export const TERMINAL_RUN_STATUSES = Object.freeze([
  "completed",
  "blocked",
  "failed",
  "cancelled"
]);

export function isActiveRun(run) {
  return Boolean(run && !TERMINAL_RUN_STATUSES.includes(run.status));
}

export function latestStepForNode(run, nodeId) {
  return run?.steps?.filter((step) => step.node_id === nodeId).at(-1) ?? null;
}

export function maintenancePhase(run, runs = []) {
  if (!run) return "unavailable";
  const proof = runs.find(
    (candidate) =>
      candidate.parent_run_id === run.id &&
      candidate.flow_version === run.repair?.applied_flow_version
  );
  if (proof?.status === "completed") return "proven";
  if (run.repair?.status === "applied") return "applied";
  if (run.repair?.status === "proposed") return "proposed";
  if (run.diagnosis) return "diagnosed";
  if (["blocked", "failed"].includes(run.status)) return "failed";
  return "not-required";
}

export function selectedStudioRun(snapshot, selectedRunId = null) {
  const runs = snapshot?.studio?.runs ?? [];
  return runs.find((run) => run.id === selectedRunId) ?? runs[0] ?? null;
}
