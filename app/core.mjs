import TRACE_SCHEMA from "../schema/kyn-flight-trace-v1.schema.json" with { type: "json" };

const RUN_STATUSES = new Set(["blocked", "completed"]);
const NODE_STATUSES = new Set(["blocked", "completed", "healthy", "pending", "waiting"]);
const EDGE_STATUSES = new Set(["blocked", "healthy", "pending", "traversed"]);
const REQUIRED_REDACTION_KEYS = new Set(["authorization", "password", "secret", "token"]);

export class ContractError extends Error {
  constructor(code, message, detail = {}) {
    super(message);
    this.name = "ContractError";
    this.code = code;
    this.detail = detail;
  }
}

function isRecord(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function clone(value) {
  return structuredClone(value);
}

function addIssue(issues, path, message) {
  issues.push({ path, message });
}

function requireString(issues, value, path) {
  if (typeof value !== "string" || value.trim() === "") {
    addIssue(issues, path, "must be a non-empty string");
  }
}

function requireInteger(issues, value, path) {
  if (!Number.isInteger(value)) {
    addIssue(issues, path, "must be an integer");
  }
}

function hasUniqueValues(values) {
  return new Set(values).size === values.length;
}

function appendPath(path, key) {
  return path ? `${path}.${key}` : key;
}

function schemaTypeMatches(value, type) {
  if (type === "object") return isRecord(value);
  if (type === "array") return Array.isArray(value);
  if (type === "integer") return Number.isInteger(value);
  if (type === "null") return value === null;
  return typeof value === type;
}

function resolveSchemaReference(reference) {
  if (typeof reference !== "string" || !reference.startsWith("#/")) {
    throw new Error(`Unsupported schema reference: ${reference}`);
  }
  return reference
    .slice(2)
    .split("/")
    .map((part) => part.replaceAll("~1", "/").replaceAll("~0", "~"))
    .reduce((current, part) => current?.[part], TRACE_SCHEMA);
}

function isRfc3339DateTime(value) {
  if (typeof value !== "string") return false;
  const match = value.match(
    /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|[+-](\d{2}):(\d{2}))$/
  );
  if (!match) return false;
  const [, yearText, monthText, dayText, hourText, minuteText, secondText, zoneHour, zoneMinute] = match;
  const year = Number(yearText);
  const month = Number(monthText);
  const day = Number(dayText);
  const daysInMonth = new Date(Date.UTC(year, month, 0)).getUTCDate();
  return (
    month >= 1 && month <= 12 &&
    day >= 1 && day <= daysInMonth &&
    Number(hourText) <= 23 &&
    Number(minuteText) <= 59 &&
    Number(secondText) <= 59 &&
    (zoneHour === undefined || (Number(zoneHour) <= 23 && Number(zoneMinute) <= 59)) &&
    !Number.isNaN(Date.parse(value))
  );
}

function validateSchemaValue(value, schema, path, issues) {
  if (schema.$ref) {
    const resolved = resolveSchemaReference(schema.$ref);
    if (!resolved) {
      throw new Error(`Schema reference does not resolve: ${schema.$ref}`);
    }
    validateSchemaValue(value, resolved, path, issues);
    return;
  }

  if (Array.isArray(schema.oneOf)) {
    const matches = schema.oneOf.filter((candidate) => {
      const branchIssues = [];
      validateSchemaValue(value, candidate, path, branchIssues);
      return branchIssues.length === 0;
    });
    if (matches.length !== 1) {
      addIssue(issues, path || "$", "must match exactly one allowed shape");
    }
    return;
  }

  if (Object.hasOwn(schema, "const") && !Object.is(value, schema.const)) {
    addIssue(issues, path || "$", `must equal ${JSON.stringify(schema.const)}`);
    return;
  }
  if (Array.isArray(schema.enum) && !schema.enum.some((entry) => Object.is(value, entry))) {
    addIssue(issues, path || "$", "contains an unsupported value");
    return;
  }
  if (schema.type && !schemaTypeMatches(value, schema.type)) {
    addIssue(issues, path || "$", `must be ${schema.type}`);
    return;
  }

  if (typeof value === "string") {
    if (Number.isInteger(schema.minLength) && [...value].length < schema.minLength) {
      addIssue(issues, path || "$", `must contain at least ${schema.minLength} character`);
    }
    if (schema.format === "date-time" && !isRfc3339DateTime(value)) {
      addIssue(issues, path || "$", "must be an RFC 3339 date-time");
    }
  }

  if (typeof value === "number" && Number.isFinite(schema.minimum) && value < schema.minimum) {
    addIssue(issues, path || "$", `must be at least ${schema.minimum}`);
  }

  if (Array.isArray(value)) {
    if (Number.isInteger(schema.minItems) && value.length < schema.minItems) {
      addIssue(issues, path || "$", `must contain at least ${schema.minItems} item`);
    }
    if (schema.uniqueItems) {
      const serialized = value.map((entry) => JSON.stringify(entry));
      if (!hasUniqueValues(serialized)) {
        addIssue(issues, path || "$", "items must be unique");
      }
    }
    if (schema.items) {
      value.forEach((entry, index) => {
        validateSchemaValue(entry, schema.items, `${path}[${index}]`, issues);
      });
    }
  }

  if (isRecord(value)) {
    const keys = Object.keys(value);
    if (Number.isInteger(schema.minProperties) && keys.length < schema.minProperties) {
      addIssue(issues, path || "$", `must contain at least ${schema.minProperties} property`);
    }
    for (const required of schema.required ?? []) {
      if (!Object.hasOwn(value, required)) {
        addIssue(issues, appendPath(path, required), "is required");
      }
    }
    const declared = schema.properties ?? {};
    for (const [key, entry] of Object.entries(value)) {
      const entryPath = appendPath(path, key);
      if (Object.hasOwn(declared, key)) {
        validateSchemaValue(entry, declared[key], entryPath, issues);
      } else if (schema.additionalProperties === false) {
        addIssue(issues, entryPath, "is not allowed");
      } else if (isRecord(schema.additionalProperties)) {
        validateSchemaValue(entry, schema.additionalProperties, entryPath, issues);
      }
    }
  }
}

function validateExternalEffectClaims(issues, value, path = "") {
  if (Array.isArray(value)) {
    value.forEach((entry, index) => validateExternalEffectClaims(issues, entry, `${path}[${index}]`));
    return;
  }
  if (!isRecord(value)) return;
  for (const [key, entry] of Object.entries(value)) {
    const entryPath = appendPath(path, key);
    if (key === "external_effect" && entry !== false) {
      addIssue(issues, entryPath, "standalone evidence must declare false");
    }
    validateExternalEffectClaims(issues, entry, entryPath);
  }
}

function validateEvents(issues, events, path, correlationId, minimumSequence = 1) {
  if (!Array.isArray(events)) {
    addIssue(issues, path, "must be an array");
    return;
  }

  const ids = [];
  const sequences = [];
  events.forEach((event, index) => {
    const eventPath = `${path}[${index}]`;
    if (!isRecord(event)) {
      addIssue(issues, eventPath, "must be an object");
      return;
    }
    requireString(issues, event.id, `${eventPath}.id`);
    requireInteger(issues, event.sequence, `${eventPath}.sequence`);
    requireString(issues, event.occurred_at, `${eventPath}.occurred_at`);
    requireString(issues, event.source, `${eventPath}.source`);
    requireString(issues, event.type, `${eventPath}.type`);
    requireString(issues, event.summary, `${eventPath}.summary`);
    if (event.correlation_id !== correlationId) {
      addIssue(issues, `${eventPath}.correlation_id`, "must match run.correlation_id");
    }
    ids.push(event.id);
    sequences.push(event.sequence);
  });

  if (!hasUniqueValues(ids)) {
    addIssue(issues, `${path}.*.id`, "event ids must be unique");
  }
  if (!hasUniqueValues(sequences)) {
    addIssue(issues, `${path}.*.sequence`, "event sequences must be unique");
  }
  const sorted = [...sequences].sort((left, right) => left - right);
  sorted.forEach((sequence, index) => {
    if (sequence !== minimumSequence + index) {
      addIssue(issues, `${path}.*.sequence`, `must be contiguous from ${minimumSequence}`);
    }
  });
}

export function validateFixture(input) {
  const structuralIssues = [];
  validateSchemaValue(input, TRACE_SCHEMA, "", structuralIssues);
  if (structuralIssues.length > 0) {
    return { ok: false, issues: structuralIssues };
  }

  const issues = [];
  validateExternalEffectClaims(issues, input);

  if (input.schema_version !== "1.0") {
    addIssue(issues, "schema_version", "unsupported schema version; expected 1.0");
  }

  if (!isRecord(input.fixture)) {
    addIssue(issues, "fixture", "must be an object");
  } else {
    requireString(issues, input.fixture.id, "fixture.id");
    if (input.fixture.classification !== "synthetic_demo") {
      addIssue(issues, "fixture.classification", "must be synthetic_demo");
    }
  }

  if (!isRecord(input.run)) {
    addIssue(issues, "run", "must be an object");
  } else {
    requireString(issues, input.run.id, "run.id");
    requireString(issues, input.run.correlation_id, "run.correlation_id");
    requireInteger(issues, input.run.revision, "run.revision");
    if (!RUN_STATUSES.has(input.run.status)) {
      addIssue(issues, "run.status", "must be blocked or completed");
    }
    if (!isRecord(input.run.diagnosis)) {
      addIssue(issues, "run.diagnosis", "must be an object");
    } else {
      requireString(issues, input.run.diagnosis.title, "run.diagnosis.title");
      requireString(issues, input.run.diagnosis.summary, "run.diagnosis.summary");
      requireString(issues, input.run.diagnosis.cause_node_id, "run.diagnosis.cause_node_id");
    }
    if (!isRecord(input.run.impact) || input.run.impact.external_effect !== false) {
      addIssue(issues, "run.impact.external_effect", "standalone fixture must declare false");
    }
  }

  if (!Array.isArray(input.nodes) || input.nodes.length === 0) {
    addIssue(issues, "nodes", "must contain at least one node");
  } else {
    const nodeIds = [];
    input.nodes.forEach((node, index) => {
      const path = `nodes[${index}]`;
      if (!isRecord(node)) {
        addIssue(issues, path, "must be an object");
        return;
      }
      requireString(issues, node.id, `${path}.id`);
      requireString(issues, node.title, `${path}.title`);
      requireString(issues, node.source, `${path}.source`);
      requireString(issues, node.kind, `${path}.kind`);
      requireInteger(issues, node.order, `${path}.order`);
      if (!NODE_STATUSES.has(node.status)) {
        addIssue(issues, `${path}.status`, "contains an unknown node status");
      }
      if (node.lane !== "main" && node.lane !== "guardrail") {
        addIssue(issues, `${path}.lane`, "must be main or guardrail");
      }
      nodeIds.push(node.id);
    });
    if (!hasUniqueValues(nodeIds)) {
      addIssue(issues, "nodes.*.id", "node ids must be unique");
    }

    const nodeSet = new Set(nodeIds);
    if (isRecord(input.run?.diagnosis) && !nodeSet.has(input.run.diagnosis.cause_node_id)) {
      addIssue(issues, "run.diagnosis.cause_node_id", "must reference an existing node");
    }

    if (!Array.isArray(input.edges)) {
      addIssue(issues, "edges", "must be an array");
    } else {
      const edgeIds = [];
      input.edges.forEach((edge, index) => {
        const path = `edges[${index}]`;
        if (!isRecord(edge)) {
          addIssue(issues, path, "must be an object");
          return;
        }
        requireString(issues, edge.id, `${path}.id`);
        requireString(issues, edge.relation, `${path}.relation`);
        if (!nodeSet.has(edge.from) || !nodeSet.has(edge.to)) {
          addIssue(issues, path, "edge endpoints must reference existing nodes");
        }
        if (!EDGE_STATUSES.has(edge.status)) {
          addIssue(issues, `${path}.status`, "contains an unknown edge status");
        }
        edgeIds.push(edge.id);
      });
      if (!hasUniqueValues(edgeIds)) {
        addIssue(issues, "edges.*.id", "edge ids must be unique");
      }
    }
  }

  const correlationId = input.run?.correlation_id;
  validateEvents(issues, input.events, "events", correlationId, 1);

  if (!isRecord(input.intervention)) {
    addIssue(issues, "intervention", "must be an object");
  } else {
    const command = input.intervention;
    requireString(issues, command.command_id, "intervention.command_id");
    requireString(issues, command.idempotency_key, "intervention.idempotency_key");
    if (command.type !== "approve_tool_call") {
      addIssue(issues, "intervention.type", "only approve_tool_call is supported");
    }
    if (command.allowed_from !== "blocked") {
      addIssue(issues, "intervention.allowed_from", "must be blocked");
    }
    if (command.expected_revision !== input.run?.revision) {
      addIssue(issues, "intervention.expected_revision", "must match run.revision");
    }
    if (!isRecord(command.resolution)) {
      addIssue(issues, "intervention.resolution", "must be an object");
    } else {
      if (command.resolution.new_revision !== command.expected_revision + 1) {
        addIssue(issues, "intervention.resolution.new_revision", "must advance exactly one revision");
      }
      const nextSequence = Array.isArray(input.events) ? input.events.length + 1 : 1;
      validateEvents(
        issues,
        command.resolution.events,
        "intervention.resolution.events",
        correlationId,
        nextSequence
      );
      if (command.resolution.node_updates?.terminal?.status !== "completed") {
        addIssue(issues, "intervention.resolution.node_updates.terminal.status", "must be completed");
      }
    }
  }

  if (!isRecord(input.redaction) || !Array.isArray(input.redaction.keys)) {
    addIssue(issues, "redaction.keys", "must be an array");
  } else {
    const normalizedKeys = new Set(input.redaction.keys.map((key) => String(key).toLowerCase()));
    for (const requiredKey of REQUIRED_REDACTION_KEYS) {
      if (!normalizedKeys.has(requiredKey)) {
        addIssue(issues, "redaction.keys", `must include ${requiredKey}`);
      }
    }
    requireString(issues, input.redaction.replacement, "redaction.replacement");
  }

  return { ok: issues.length === 0, issues };
}

function keyIsSensitive(key, sensitiveKeys) {
  const normalized = key.toLowerCase();
  return sensitiveKeys.some(
    (candidate) => normalized === candidate || normalized.endsWith(`_${candidate}`)
  );
}

export function redactForDisplay(value, redaction) {
  const sensitiveKeys = redaction.keys.map((key) => String(key).toLowerCase());
  const replacement = redaction.replacement;

  function visit(current) {
    if (Array.isArray(current)) {
      return current.map(visit);
    }
    if (!isRecord(current)) {
      return current;
    }
    return Object.fromEntries(
      Object.entries(current).map(([key, entry]) => [
        key,
        keyIsSensitive(key, sensitiveKeys) ? replacement : visit(entry)
      ])
    );
  }

  return visit(value);
}

export function createInitialState(fixture) {
  const verdict = validateFixture(fixture);
  if (!verdict.ok) {
    throw new ContractError("INVALID_FIXTURE", "The demo fixture failed closed.", {
      issues: verdict.issues
    });
  }

  const safeFixture = redactForDisplay(clone(fixture), fixture.redaction);
  return {
    schema_version: safeFixture.schema_version,
    fixture: safeFixture.fixture,
    run: safeFixture.run,
    nodes: safeFixture.nodes,
    edges: safeFixture.edges,
    events: safeFixture.events,
    intervention: safeFixture.intervention,
    selected_node_id: safeFixture.run.diagnosis.cause_node_id,
    command: {
      phase: "available",
      receipt: null
    }
  };
}

function assertCommandAvailable(state) {
  const command = state.intervention;
  if (state.command.receipt) {
    return;
  }
  if (state.run.status === "completed") {
    throw new ContractError("TERMINAL_ABSORBS", "A completed run cannot accept another command.");
  }
  if (state.run.status !== command.allowed_from) {
    throw new ContractError(
      "ILLEGAL_SOURCE_STATE",
      `Command requires ${command.allowed_from}; run is ${state.run.status}.`
    );
  }
  if (state.run.revision !== command.expected_revision) {
    throw new ContractError(
      "REVISION_CONFLICT",
      `Command expected revision ${command.expected_revision}; run is ${state.run.revision}.`
    );
  }
}

export function previewCommand(state) {
  assertCommandAvailable(state);
  if (state.command.receipt) {
    return {
      command_id: state.intervention.command_id,
      duplicate: true,
      receipt: clone(state.command.receipt)
    };
  }
  return {
    command_id: state.intervention.command_id,
    type: state.intervention.type,
    expected_revision: state.intervention.expected_revision,
    actor: state.intervention.actor,
    scope: state.intervention.scope,
    external_effect: false,
    preview: clone(state.intervention.preview)
  };
}

function validateAuthorization(state, authorization) {
  const actor = typeof authorization?.actor === "string" ? authorization.actor.trim() : "";
  const reason = typeof authorization?.reason === "string" ? authorization.reason.trim() : "";
  if (actor !== state.intervention.actor) {
    throw new ContractError("ACTOR_MISMATCH", "The command actor does not match the preview.");
  }
  if (reason.length < 12 || reason.length > 280) {
    throw new ContractError("INVALID_REASON", "Reason must contain 12–280 characters.");
  }
  if (authorization?.acknowledged !== true) {
    throw new ContractError("ACK_REQUIRED", "The local-simulation acknowledgement is required.");
  }
  return { actor, reason };
}

function updateNodeFields(node) {
  if (node.id === "approval") {
    return { ...node.fields, decision: "approved" };
  }
  if (node.id === "effect") {
    return { ...node.fields, executed: true };
  }
  if (node.id === "terminal") {
    return { ...node.fields, current: "completed" };
  }
  return node.fields;
}

export function applyCommand(state, authorization) {
  assertCommandAvailable(state);
  if (state.command.receipt) {
    return { state, receipt: clone(state.command.receipt), duplicate: true };
  }

  const { actor, reason } = validateAuthorization(state, authorization);
  const command = state.intervention;
  const resolution = command.resolution;
  const receipt = {
    receipt_id: `receipt_${command.command_id.slice(4)}`,
    command_id: command.command_id,
    idempotency_key: command.idempotency_key,
    run_id: state.run.id,
    correlation_id: state.run.correlation_id,
    actor,
    reason,
    applied_at: resolution.completed_at,
    from_revision: command.expected_revision,
    to_revision: resolution.new_revision,
    external_effect: false
  };

  const nodes = state.nodes.map((node) => {
    const update = resolution.node_updates[node.id];
    if (!update) {
      return clone(node);
    }
    return {
      ...clone(node),
      ...clone(update),
      fields: updateNodeFields(node)
    };
  });

  const edges = state.edges.map((edge) => ({
    ...clone(edge),
    status: resolution.edge_updates[edge.id] ?? edge.status
  }));

  const appendedEvents = resolution.events.map((event) => {
    const copy = clone(event);
    if (copy.type === "approval.resolved") {
      copy.summary = `${actor} authorized the simulated command`;
      copy.detail = { ...copy.detail, actor, reason };
    }
    return copy;
  });

  const nextState = {
    ...clone(state),
    run: {
      ...clone(state.run),
      status: "completed",
      revision: resolution.new_revision,
      updated_at: resolution.completed_at,
      duration_ms: Date.parse(resolution.completed_at) - Date.parse(state.run.started_at),
      diagnosis: clone(resolution.diagnosis)
    },
    nodes,
    edges,
    events: [...state.events.map(clone), ...appendedEvents],
    selected_node_id: "terminal",
    command: {
      phase: "applied",
      receipt
    }
  };

  return { state: nextState, receipt: clone(receipt), duplicate: false };
}

export function createSessionRecord(state) {
  if (!state.command.receipt) {
    return null;
  }
  return {
    version: 1,
    fixture_id: state.fixture.id,
    schema_version: state.schema_version,
    command_id: state.command.receipt.command_id,
    actor: state.command.receipt.actor,
    reason: state.command.receipt.reason,
    acknowledged: true
  };
}

export function restoreSession(fixture, sessionRecord) {
  const initial = createInitialState(fixture);
  if (sessionRecord === null || sessionRecord === undefined) {
    return initial;
  }
  if (!isRecord(sessionRecord) || sessionRecord.version !== 1) {
    throw new ContractError("INVALID_SESSION", "Stored demo state has an unsupported shape.");
  }
  if (
    sessionRecord.fixture_id !== initial.fixture.id ||
    sessionRecord.schema_version !== initial.schema_version ||
    sessionRecord.command_id !== initial.intervention.command_id
  ) {
    throw new ContractError("SESSION_MISMATCH", "Stored demo state belongs to another fixture.");
  }
  return applyCommand(initial, sessionRecord).state;
}

export function selectNode(state, nodeId) {
  if (!state.nodes.some((node) => node.id === nodeId)) {
    throw new ContractError("UNKNOWN_NODE", `Unknown node: ${nodeId}`);
  }
  return { ...state, selected_node_id: nodeId };
}

export function findNode(state, nodeId = state.selected_node_id) {
  return state.nodes.find((node) => node.id === nodeId) ?? null;
}

export function eventsForSource(state, source) {
  return state.events.filter((event) => event.source === source).map(clone);
}
