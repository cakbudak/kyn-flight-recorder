export const PHASE_ORDER = ["ready", "blocked", "diagnosed", "repair", "applied", "proven"];

export function rootRunFor(snapshot) {
  return snapshot?.runs?.find((run) => !run.parent_run_id) ?? null;
}

export function childRunFor(snapshot, root = rootRunFor(snapshot)) {
  return root
    ? snapshot?.runs?.find((run) => run.parent_run_id === root.id) ?? null
    : null;
}

export function phaseFor(snapshot) {
  const root = rootRunFor(snapshot);
  const child = childRunFor(snapshot, root);
  if (child?.status === "completed") return "proven";
  if (!root) return "ready";
  if (root.status === "failed" || child?.status === "failed") return "failed";
  if (root.repair?.status === "applied") return "applied";
  if (root.repair?.status === "proposed") return "repair";
  if (root.diagnosis) return "diagnosed";
  return "blocked";
}

export function selectedRunFor(snapshot, selectedRunId = null) {
  const runs = snapshot?.runs ?? [];
  return (
    runs.find((run) => run.id === selectedRunId) ??
    childRunFor(snapshot) ??
    rootRunFor(snapshot)
  );
}
