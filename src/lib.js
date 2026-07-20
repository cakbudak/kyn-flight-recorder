export const EMPTY_SCHEMA = {
  type: "object",
  properties: {},
  required: [],
  additionalProperties: false
};

export const VALUE_SCHEMA = {
  type: "object",
  properties: { value: { type: "string" } },
  required: ["value"],
  additionalProperties: false
};

export const TEXT_SCHEMA = {
  type: "object",
  properties: { text: { type: "string" } },
  required: ["text"],
  additionalProperties: false
};

export const SUCCESS_ERROR = [
  { id: "success", label: "Success", description: "Contract completed.", tone: "success" },
  { id: "error", label: "Error", description: "Execution failed closed.", tone: "danger" }
];

export const APPROVAL_DEMO_BRIEF = [
  "Target audience: OpenAI Build Week judges assessing a public agent-workflow product.",
  "Typed input contract: one object with a required brief string and no additional properties.",
  "Typed output contract: one object with a required summary string, score number from 0 to 1, and risks array of strings, with no additional properties.",
  "Deterministic decision boundary: score at or above 0.75 routes to human approval; a lower score routes to needs-work.",
  "Human authority boundary: only the named operator may approve continuation, and no effect occurs before that recorded decision.",
  "Bounded effect scope: approval permits exactly one idempotent append to the isolated workspace-evidence SQLite collection, with no production connector.",
  "Inspectable evidence: every pinned definition, model attempt, Step, outcome, tool receipt, approval, effect, and hash-linked event remains queryable; failed Runs and prior versions remain immutable.",
  "Measurable success condition: schema-valid output, score at or above 0.75, a recorded operator approval, exactly one successful effect receipt, and a valid event chain; otherwise the Run cannot be called successful."
].join(" ");

export const ACTION_PRESETS = {
  template: {
    label: "Template",
    description: "Render deterministic text from validated input.",
    input_schema: { type: "object", properties: { name: { type: "string" } }, required: ["name"], additionalProperties: false },
    output_schema: TEXT_SCHEMA,
    outcomes: SUCCESS_ERROR,
    config: { template: "Hello {{name}}" }
  },
  ai: {
    label: "AI",
    description: "Invoke a pinned Agent through OpenAI Responses with strict output.",
    input_schema: { type: "object", properties: { brief: { type: "string" } }, required: ["brief"], additionalProperties: false },
    output_schema: {
      type: "object",
      properties: { summary: { type: "string" }, score: { type: "number" }, risks: { type: "array", items: { type: "string" } } },
      required: ["summary", "score", "risks"],
      additionalProperties: false
    },
    outcomes: SUCCESS_ERROR,
    config: { max_tool_calls: 2, reasoning_effort: "medium" }
  },
  transform: {
    label: "Transform",
    description: "Map fields without arbitrary code.",
    input_schema: VALUE_SCHEMA,
    output_schema: { type: "object", properties: { normalized: { type: "string" } }, required: ["normalized"], additionalProperties: false },
    outcomes: SUCCESS_ERROR,
    config: { operation: "map", mappings: { normalized: { source: "input", path: "value" } } }
  },
  condition: {
    label: "Condition",
    description: "Route over one bounded comparison.",
    input_schema: VALUE_SCHEMA,
    output_schema: { type: "object", properties: { matched: { type: "boolean" }, actual: { type: "string" } }, required: ["matched", "actual"], additionalProperties: false },
    outcomes: [
      { id: "true", label: "True", description: "Condition matched.", tone: "success" },
      { id: "false", label: "False", description: "Condition did not match.", tone: "warning" },
      { id: "error", label: "Error", description: "Comparison failed.", tone: "danger" }
    ],
    config: { path: "value", operator: "equals", value: "ready" }
  },
  router: {
    label: "Router",
    description: "Expose several named outcomes from ordered rules.",
    input_schema: VALUE_SCHEMA,
    output_schema: { type: "object", properties: { outcome: { type: "string", enum: ["priority", "standard", "fallback"] }, actual: { type: "string" } }, required: ["outcome", "actual"], additionalProperties: false },
    outcomes: [
      { id: "priority", label: "Priority", description: "Priority rule matched.", tone: "ai" },
      { id: "standard", label: "Standard", description: "Standard rule matched.", tone: "success" },
      { id: "fallback", label: "Fallback", description: "No rule matched.", tone: "warning" },
      { id: "error", label: "Error", description: "Routing failed.", tone: "danger" }
    ],
    config: {
      branches: [
        { outcome: "priority", path: "value", operator: "equals", value: "priority" },
        { outcome: "standard", path: "value", operator: "equals", value: "standard" }
      ],
      fallback_outcome: "fallback"
    }
  },
  delay: {
    label: "Delay",
    description: "Wait for a bounded number of milliseconds.",
    input_schema: VALUE_SCHEMA,
    output_schema: VALUE_SCHEMA,
    outcomes: SUCCESS_ERROR,
    config: { milliseconds: 250 }
  },
  assert: {
    label: "Assert",
    description: "Fail closed when a typed invariant is false.",
    input_schema: VALUE_SCHEMA,
    output_schema: { type: "object", properties: { passed: { type: "boolean" }, actual: { type: "string" } }, required: ["passed", "actual"], additionalProperties: false },
    outcomes: SUCCESS_ERROR,
    config: { path: "value", operator: "equals", value: "ready", message: "The value is not ready." }
  },
  approval: {
    label: "Human approval",
    description: "Pause the durable Run for an explicit decision.",
    input_schema: VALUE_SCHEMA,
    output_schema: { type: "object", properties: { approved: { type: "boolean" }, reason: { type: "string" } }, required: ["approved", "reason"], additionalProperties: false },
    outcomes: [
      { id: "approved", label: "Approved", description: "Human authorized continuation.", tone: "success" },
      { id: "rejected", label: "Rejected", description: "Human rejected continuation.", tone: "warning" },
      { id: "error", label: "Error", description: "Decision could not be recorded.", tone: "danger" }
    ],
    config: { message_template: "Approve {{value}}?" }
  },
  data_store: {
    label: "Data store",
    description: "Append one idempotent record to the isolated workspace store.",
    input_schema: VALUE_SCHEMA,
    output_schema: { type: "object", properties: { effect_id: { type: "string" }, collection: { type: "string" } }, required: ["effect_id", "collection"], additionalProperties: false },
    outcomes: SUCCESS_ERROR,
    config: { operation: "append_record", collection: "records", write_enabled: true }
  }
};

export const STATUS_TONE = {
  created: "neutral",
  running: "ai",
  waiting_approval: "warning",
  completed: "success",
  blocked: "danger",
  failed: "danger",
  cancelled: "neutral"
};

export const TERMINAL_RUN_STATUSES = ["completed", "blocked", "failed", "cancelled"];

export function isActiveRun(run) {
  return Boolean(run && !TERMINAL_RUN_STATUSES.includes(run.status));
}

export function latestStepForNode(run, nodeId) {
  return (run?.steps ?? []).filter((step) => step.node_id === nodeId).at(-1) ?? null;
}

export function selectedStudioRun(snapshot, explicitId = null) {
  const runs = snapshot?.studio?.runs ?? [];
  return runs.find((run) => run.id === explicitId) ?? runs[0] ?? null;
}

export function maintenancePhase(run, runs = []) {
  if (!run) return "unavailable";
  if (!["blocked", "failed"].includes(run.status)) return "not-required";
  if (!run.diagnosis) return "failed";
  if (!run.repair) return "diagnosed";
  if (run.repair.status !== "applied") return "proposed";
  const proof = runs.some((candidate) =>
    candidate.parent_run_id === run.id &&
    candidate.relation_kind === "proof" &&
    candidate.flow_version === run.repair.applied_flow_version &&
    candidate.status === "completed"
  );
  return proof ? "proven" : "applied";
}

export const COMPLETION_EVENT_TYPES = ["completion.admitted", "completion.refused"];

/** The stop-seam verdict this Run carries, or null when none was recorded.
 *
 * The server decides admission; this only locates the event it wrote. The last
 * one wins because the ledger is append-only and a Run may only ever reach the
 * seam once — reading the last keeps a replayed ledger showing its verdict
 * rather than an earlier one. A Flow that declares no criteria records nothing,
 * which is the default, so an absent event is inertness and not a failure.
 */
export function completionAdjudication(run) {
  return (run?.events ?? []).filter((event) => COMPLETION_EVENT_TYPES.includes(event.type)).at(-1)?.payload ?? null;
}

export function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

export function titleCase(value = "") {
  return value.replaceAll("_", " ").replaceAll("-", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function shortId(value = "", length = 9) {
  const pieces = value.split("_");
  return pieces.length > 1 ? `${pieces[0]}_${pieces.at(-1).slice(-length)}` : value.slice(-length);
}

export function formatTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(date);
}

export function parseJson(value, label) {
  try {
    return JSON.parse(value);
  } catch {
    throw new Error(`${label} must be valid JSON.`);
  }
}

export function slugify(value) {
  return value.toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 64);
}

// Editing a slug is not the same operation as finalizing one. In particular,
// trimming a trailing hyphen on every keypress turns `risk-review` into
// `riskreview`: the separator disappears before the next letter arrives. Keep
// a syntactically safe partial value while the field owns focus; callers use
// `slugify` on blur or submission to close the identifier.
export function slugDraft(value) {
  return value.toLowerCase().replace(/[^a-z0-9-]+/g, "-").replace(/-{2,}/g, "-").replace(/^-+/g, "").slice(0, 64);
}

export function exampleForSchema(schema, name = "value") {
  if (!schema || typeof schema !== "object") return null;
  if (Array.isArray(schema.enum) && schema.enum.length) return schema.enum[0];
  if (schema.type === "object") {
    const result = {};
    for (const [key, property] of Object.entries(schema.properties ?? {})) {
      if ((schema.required ?? []).includes(key)) result[key] = exampleForSchema(property, key);
    }
    return result;
  }
  if (schema.type === "array") return [];
  if (schema.type === "boolean") return true;
  if (schema.type === "integer" || schema.type === "number") return 1;
  return name === "brief"
    ? APPROVAL_DEMO_BRIEF
    : name === "name" ? "Ada" : name === "value" ? "ready" : `example-${name}`;
}

export function resourceForNode(snapshot, node) {
  if (!node) return null;
  if (node.type === "action") {
    return snapshot.studio.actions.find((item) => item.versions.some((version) => version.id === node.version_id));
  }
  if (node.type === "agent") {
    return snapshot.agents.find((item) => item.versions.some((version) => version.id === node.version_id));
  }
  return snapshot.studio.flows.find((item) => item.versions.some((version) => version.id === node.version_id));
}

export function versionForNode(snapshot, node) {
  const resource = resourceForNode(snapshot, node);
  return resource?.versions.find((version) => version.id === node.version_id) ?? null;
}

export function nodeOutcomes(snapshot, node) {
  const version = versionForNode(snapshot, node);
  if (node?.type === "agent") return SUCCESS_ERROR;
  return version?.outcomes ?? SUCCESS_ERROR;
}

export function graphNodeLabel(snapshot, node) {
  return resourceForNode(snapshot, node)?.name ?? node.id;
}

export function defaultMapping(schema, predecessor = null) {
  const mapping = {};
  for (const key of schema?.required ?? []) {
    if (predecessor && predecessor.output_schema?.properties?.[key]) {
      mapping[key] = { source: "step", node_id: predecessor.id, path: key };
    } else {
      mapping[key] = { source: "input", path: key };
    }
  }
  return mapping;
}

export function uniqueNodeId(base, nodes) {
  const root = slugify(base) || "node";
  const ids = new Set(nodes.map((node) => node.id));
  if (!ids.has(root)) return root;
  let suffix = 2;
  while (ids.has(`${root}-${suffix}`)) suffix += 1;
  return `${root}-${suffix}`;
}

export function layoutGraph(nodes, routes) {
  if (!nodes.length) return [];
  const incoming = new Map(nodes.map((node) => [node.id, 0]));
  const outgoing = new Map(nodes.map((node) => [node.id, []]));
  for (const route of routes) {
    incoming.set(route.to, (incoming.get(route.to) ?? 0) + 1);
    outgoing.get(route.from)?.push(route.to);
  }
  const queue = nodes.filter((node) => incoming.get(node.id) === 0).map((node) => node.id);
  const level = new Map(queue.map((id) => [id, 0]));
  while (queue.length) {
    const current = queue.shift();
    for (const target of outgoing.get(current) ?? []) {
      level.set(target, Math.max(level.get(target) ?? 0, (level.get(current) ?? 0) + 1));
      incoming.set(target, incoming.get(target) - 1);
      if (incoming.get(target) === 0) queue.push(target);
    }
  }
  const rows = new Map();
  return nodes.map((node, index) => {
    const column = level.get(node.id) ?? index;
    const row = rows.get(column) ?? 0;
    rows.set(column, row + 1);
    return { ...node, position: { x: 100 + column * 340, y: 90 + row * 280 } };
  });
}

export function runNodeState(run, nodeId) {
  const attempts = (run?.steps ?? []).filter((step) => step.node_id === nodeId);
  return attempts.at(-1)?.status ?? (run?.current_node_id === nodeId ? "running" : "idle");
}
