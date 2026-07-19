import {
  isActiveRun,
  latestStepForNode,
  maintenancePhase
} from "./state.mjs";

const OPENAI_KEY_SLOT = "kyn.openai.api-key.v1";

const state = {
  health: null,
  snapshot: null,
  busy: false,
  view: "overview",
  selectedFlowId: null,
  selectedRunId: null,
  selectedRunNodeId: null,
  runTab: "steps",
  resourceTab: "agents",
  lastError: null,
  flowDraft: null,
  selectedBuilderNodeId: null,
  connectFromNodeId: null,
  lastWebhook: null
};

const ACTION_PRESETS = {
  template: {
    name: "Greeting formatter",
    slug: "greeting-formatter",
    description: "Render a deterministic greeting from validated input.",
    input: objectSchema({ name: { type: "string", minLength: 1, maxLength: 200 } }, ["name"]),
    output: objectSchema({ text: { type: "string" } }, ["text"]),
    config: { template: "Hello {{name}}" }
  },
  transform: {
    name: "Normalize intake",
    slug: "normalize-intake",
    description: "Map validated fields into a stable downstream contract.",
    input: objectSchema({ value: { type: "string", minLength: 1, maxLength: 4000 } }, ["value"]),
    output: objectSchema(
      { normalized: { type: "string" }, source: { type: "string" } },
      ["normalized", "source"]
    ),
    config: {
      operation: "map",
      mappings: {
        normalized: { source: "input", path: "value" },
        source: { source: "literal", value: "agent-studio" }
      }
    }
  },
  delay: {
    name: "Bounded delay",
    slug: "bounded-delay",
    description: "Pause a worker for a bounded interval before passing input through.",
    input: objectSchema({ value: { type: "string", maxLength: 4000 } }, ["value"]),
    output: objectSchema({ value: { type: "string", maxLength: 4000 } }, ["value"]),
    config: { milliseconds: 250 }
  },
  condition: {
    name: "Score gate",
    slug: "score-gate",
    description: "Route a Flow from a validated numeric score.",
    input: objectSchema({ score: { type: "number", minimum: 0, maximum: 1 } }, ["score"]),
    output: objectSchema(
      { matched: { type: "boolean" }, actual: { type: "number" } },
      ["matched", "actual"]
    ),
    config: { path: "score", operator: "gte", value: 0.75 }
  },
  assert: {
    name: "Contract assertion",
    slug: "contract-assertion",
    description: "Block a Run when an explicit data contract is not satisfied.",
    input: objectSchema({ score: { type: "number", minimum: 0, maximum: 1 } }, ["score"]),
    output: objectSchema(
      { passed: { type: "boolean" }, actual: { type: "number" } },
      ["passed", "actual"]
    ),
    config: {
      path: "score",
      operator: "gte",
      value: 0.75,
      message: "The readiness score is below the approved threshold."
    }
  },
  approval: {
    name: "Human decision",
    slug: "human-decision",
    description: "Pause a Run until an attributable human decision is committed.",
    input: objectSchema({ summary: { type: "string", maxLength: 2000 } }, ["summary"]),
    output: objectSchema(
      { approved: { type: "boolean" }, reason: { type: "string" } },
      ["approved", "reason"]
    ),
    config: { message_template: "Approve this result? {{summary}}" }
  },
  data_store: {
    name: "Append workspace record",
    slug: "append-workspace-record",
    description: "Create one idempotent record inside the isolated SQLite workspace.",
    input: objectSchema({ record: { type: "string", maxLength: 4000 } }, ["record"]),
    output: objectSchema(
      { effect_id: { type: "string" }, collection: { type: "string" } },
      ["effect_id", "collection"]
    ),
    config: {
      operation: "append_record",
      collection: "custom-records",
      write_enabled: true
    }
  }
};

class ApiError extends Error {
  constructor(status, payload) {
    const material = payload?.error ?? {};
    super(material.message ?? `Request failed with status ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.code = material.code ?? "request_failed";
    this.detail = material.detail ?? null;
  }
}

function objectSchema(properties, required = Object.keys(properties)) {
  return {
    type: "object",
    properties,
    required,
    additionalProperties: false
  };
}

function byId(id) {
  const element = document.getElementById(id);
  if (!element) throw new Error(`Missing required element: #${id}`);
  return element;
}

function make(tag, className = "", textValue) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (textValue !== undefined) element.textContent = String(textValue);
  if (tag === "button") element.type = "button";
  return element;
}

function setText(id, value) {
  byId(id).textContent = value ?? "";
}

function short(value, size = 15) {
  const material = String(value ?? "");
  return material.length > size ? `${material.slice(0, size)}…` : material;
}

function titleCase(value) {
  return String(value ?? "")
    .replaceAll("_", " ")
    .replaceAll(".", " · ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function timeLabel(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "unknown";
  return date.toLocaleString([], {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit"
  });
}

function jsonText(value) {
  return JSON.stringify(value, null, 2);
}

function parseJsonField(id, label) {
  try {
    return JSON.parse(byId(id).value);
  } catch {
    throw new Error(`${label} must be valid JSON.`);
  }
}

function parseJsonValue(value, label) {
  try {
    return JSON.parse(value);
  } catch {
    throw new Error(`${label} must be valid JSON.`);
  }
}

function studio() {
  return state.snapshot?.studio ?? { actions: [], flows: [], runs: [] };
}

function openAIKey() {
  try {
    return sessionStorage.getItem(OPENAI_KEY_SLOT) ?? "";
  } catch {
    return "";
  }
}

async function api(
  path,
  { method = "GET", body, modelAction = false } = {}
) {
  if (!path.startsWith("/api/")) {
    throw new Error("The browser client allows only same-origin runtime API paths.");
  }
  const options = {
    method,
    credentials: "same-origin",
    headers: { Accept: "application/json" }
  };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  if (modelAction) {
    const key = openAIKey();
    if (!key) {
      setView("config");
      throw new ApiError(401, {
        error: {
          code: "openai_key_required",
          message: "Configure your OpenAI API key in this browser tab before running a model action."
        }
      });
    }
    options.headers["X-OpenAI-API-Key"] = key;
  }
  const response = await fetch(path, options);
  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new ApiError(response.status, {
      error: { code: "invalid_response", message: "The runtime returned invalid JSON." }
    });
  }
  if (!response.ok) throw new ApiError(response.status, payload);
  return payload.data;
}

async function runtimeHealth() {
  const response = await fetch("/healthz", {
    credentials: "same-origin",
    headers: { Accept: "application/json" }
  });
  const payload = await response.json();
  if (!response.ok) throw new ApiError(response.status, payload);
  return payload;
}

function announce(message) {
  setText("live-region", "");
  window.setTimeout(() => setText("live-region", message), 20);
}

let toastTimer = null;
function toast(message) {
  const element = byId("toast");
  element.textContent = message;
  element.hidden = false;
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => {
    element.hidden = true;
  }, 2800);
}

function clearError() {
  state.lastError = null;
  byId("error-panel").hidden = true;
}

function showError(error) {
  state.lastError = error;
  setText("error-title", error.code ? titleCase(error.code) : "Request failed");
  setText("error-message", error.message ?? "The operation could not be completed.");
  byId("error-panel").hidden = false;
  announce(`Error: ${error.message}`);
}

function setBusy(value) {
  state.busy = value;
  document.body.dataset.busy = String(value);
  document.querySelectorAll("button[type='submit'], .button-primary").forEach((button) => {
    if (!value || !button.closest("[hidden]")) button.disabled = value;
  });
  byId("main-content").setAttribute("aria-busy", String(value));
}

async function operation(label, work, successMessage, after) {
  if (state.busy) return null;
  clearError();
  setBusy(true);
  announce(label);
  try {
    const result = await work();
    await refreshWorkspace();
    if (after) after(result);
    render();
    toast(successMessage);
    announce(successMessage);
    return result;
  } catch (error) {
    showError(error instanceof Error ? error : new Error("Unknown runtime failure"));
    return null;
  } finally {
    setBusy(false);
    if (state.snapshot) renderCurrentView();
  }
}

async function refreshWorkspace() {
  state.snapshot = await api("/api/v1/workspace");
  if (!state.selectedFlowId || !studio().flows.some((flow) => flow.id === state.selectedFlowId)) {
    state.selectedFlowId = studio().flows[0]?.id ?? null;
  }
  if (!state.selectedRunId || !studio().runs.some((run) => run.id === state.selectedRunId)) {
    state.selectedRunId = studio().runs[0]?.id ?? null;
  }
}

async function createWorkspace() {
  await operation(
    "Creating an isolated SQLite workspace…",
    async () => {
      const created = await api("/api/v1/workspaces", { method: "POST", body: {} });
      state.snapshot = created.snapshot;
      state.selectedFlowId = created.snapshot.studio.flows[0]?.id ?? null;
      state.selectedRunId = null;
      state.view = "overview";
      return created;
    },
    "Agent Studio workspace is ready"
  );
}

async function bootstrap() {
  const [healthResult, workspaceResult] = await Promise.allSettled([
    runtimeHealth(),
    api("/api/v1/workspace")
  ]);
  if (healthResult.status === "fulfilled") state.health = healthResult.value;
  if (workspaceResult.status === "fulfilled") {
    state.snapshot = workspaceResult.value;
    state.selectedFlowId = studio().flows[0]?.id ?? null;
    state.selectedRunId = studio().runs[0]?.id ?? null;
  } else if (!(workspaceResult.reason instanceof ApiError && workspaceResult.reason.status === 401)) {
    state.lastError = workspaceResult.reason;
  }
  byId("loading-state").hidden = true;
  render();
  if (state.lastError) showError(state.lastError);
}

function setView(view) {
  state.view = view;
  if (view === "runs") state.runTab = "steps";
  renderSurface();
  if (state.snapshot) renderCurrentView();
  if (view === "config") {
    byId("openai-api-key").value = openAIKey();
    window.setTimeout(() => byId("openai-api-key").focus(), 0);
  }
  history.replaceState(null, "", `#${view}`);
  byId("main-content").focus({ preventScroll: true });
}

function render() {
  renderHealth();
  renderKeyStatus();
  renderSurface();
  if (state.snapshot) {
    renderNavigationCounts();
    renderOverview();
    renderActions();
    renderFlows();
    renderRuns();
    renderResources();
  }
}

function renderHealth() {
  const wrapper = byId("runtime-health");
  const light = wrapper.querySelector(".status-light");
  const label = wrapper.querySelector("span:last-child");
  light.className = "status-light";
  if (!state.health) {
    light.classList.add("is-warning");
    label.textContent = "Runtime unavailable";
    return;
  }
  light.classList.add("is-ready");
  label.textContent = "SQLite runtime ready";
}

function renderKeyStatus() {
  const configured = Boolean(openAIKey());
  byId("open-config").classList.toggle("is-configured", configured);
  setText("key-status-label", configured ? "OpenAI configured" : "Configure OpenAI");
  setText("config-key-badge", configured ? "Configured for tab" : "Not configured");
  byId("config-key-badge").className = `status-badge ${configured ? "status-completed" : "status-waiting_approval"}`;
}

function renderSurface() {
  const hasWorkspace = Boolean(state.snapshot);
  const standaloneView = !hasWorkspace && ["config", "docs"].includes(state.view);
  document.body.classList.toggle("has-workspace", hasWorkspace);
  byId("sidebar").hidden = !hasWorkspace;
  byId("onboarding").hidden = hasWorkspace || standaloneView;
  byId("workspace-surface").hidden = !hasWorkspace && !standaloneView;
  setText(
    "workspace-label",
    hasWorkspace ? `workspace / ${short(state.snapshot.workspace.id, 23)}` : "No workspace"
  );
  document.querySelectorAll("[data-view-panel]").forEach((panel) => {
    panel.hidden = panel.dataset.viewPanel !== state.view;
  });
  document.querySelectorAll("[data-view]").forEach((button) => {
    const active = button.dataset.view === state.view;
    button.classList.toggle("is-active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
}

function renderCurrentView() {
  if (state.view === "actions") renderActions();
  else if (state.view === "flows") renderFlows();
  else if (state.view === "runs") renderRuns();
  else if (state.view === "resources") renderResources();
  else if (state.view === "overview") renderOverview();
}

function renderNavigationCounts() {
  setText("nav-action-count", studio().actions.length);
  setText("nav-flow-count", studio().flows.length);
  setText("nav-run-count", studio().runs.length);
}

function renderOverview() {
  setText("metric-actions", studio().actions.length);
  setText("metric-flows", studio().flows.length);
  setText("metric-runs", studio().runs.length);
  setText("metric-agents", state.snapshot.agents.length);
  const list = byId("overview-runs");
  list.replaceChildren();
  const recent = studio().runs.slice(0, 5);
  if (!recent.length) {
    list.append(make("p", "run-table-empty", "No Run yet. Start the example or build your own Flow."));
    return;
  }
  recent.forEach((run) => list.append(runTableRow(run)));
}

function runTableRow(run) {
  const flow = studio().flows.find((item) => item.id === run.flow_id);
  const button = make("button", "run-row");
  button.dataset.selectRun = run.id;
  const identity = make("span");
  identity.append(make("strong", "", flow?.name ?? "Unknown Flow"), make("code", "", short(run.id, 22)));
  const correlation = make("span");
  correlation.append(make("small", "", "Correlation"), make("code", "", short(run.correlation_id, 18)));
  button.append(
    identity,
    correlation,
    statusBadge(run.status),
    make("small", "", timeLabel(run.created_at))
  );
  return button;
}

function statusBadge(status) {
  const badge = make("span", `status-badge status-${String(status).replace(/[^a-z_]/g, "")}`, titleCase(status));
  return badge;
}

function renderActions() {
  const filter = byId("action-filter").value.trim().toLowerCase();
  const actions = studio().actions.filter((action) => {
    const haystack = `${action.name} ${action.slug} ${action.description} ${action.version.kind}`.toLowerCase();
    return haystack.includes(filter);
  });
  const list = byId("action-list");
  list.replaceChildren();
  actions.forEach((action) => list.append(actionCard(action)));
  if (!actions.length) list.append(emptyState("No Action matches this filter."));
}

function actionCard(action) {
  const version = action.version;
  const card = make("article", "definition-card");
  const head = make("div", "definition-card-head");
  head.append(
    make("span", `kind-chip kind-${version.kind}`, titleCase(version.kind)),
    make("span", "version-chip", `v${version.version}`)
  );
  const title = make("h2", "", action.name);
  const description = make("p", "", action.description);
  const facts = make("dl");
  const inputs = Object.keys(version.input_schema.properties).length;
  const outputs = Object.keys(version.output_schema.properties).length;
  facts.append(
    definitionFact("Input fields", inputs),
    definitionFact("Output fields", outputs),
    definitionFact("Effect", titleCase(version.effect_level)),
    definitionFact("Created by", titleCase(version.created_by))
  );
  const foot = make("div", "definition-card-foot");
  const code = make("code", "", version.fingerprint);
  code.title = version.fingerprint;
  foot.append(code, make("span", "effect-chip", version.agent_version_id ? "Agent pinned" : "Local runtime"));
  card.append(head, title, description, facts, foot);
  return card;
}

function definitionFact(term, value) {
  const wrapper = make("div");
  wrapper.append(make("dt", "", term), make("dd", "", value));
  return wrapper;
}

function emptyState(message) {
  const wrapper = make("div", "empty-state");
  wrapper.append(make("p", "", message));
  return wrapper;
}

function renderFlows() {
  const flows = studio().flows;
  byId("flows-view")
    .querySelector(".split-workspace")
    .classList.toggle("is-builder-active", Boolean(state.flowDraft));
  if (!state.flowDraft && !flows.some((flow) => flow.id === state.selectedFlowId)) {
    state.selectedFlowId = flows[0]?.id ?? null;
  }
  const list = byId("flow-list");
  list.replaceChildren();
  const libraryHead = make("div", "flow-library-head");
  libraryHead.append(
    make("span", "section-kicker", "Published automation"),
    make("small", "", `${flows.length} immutable ${flows.length === 1 ? "Flow" : "Flows"}`)
  );
  list.append(libraryHead);
  flows.forEach((flow) => {
    const button = make("button", "selection-button");
    button.dataset.selectFlow = flow.id;
    button.classList.toggle(
      "is-selected",
      !state.flowDraft && flow.id === state.selectedFlowId
    );
    button.append(
      make("strong", "", flow.name),
      make("small", "", `${flow.version.nodes.length} nodes · ${flow.version.requires_model ? "AI" : "deterministic"}`),
      make("code", "", `v${flow.version.version} / ${short(flow.version.fingerprint, 13)}`)
    );
    list.append(button);
  });
  const template = make("button", `selection-button template-entry${state.flowDraft?.mode === "create" ? " is-selected" : ""}`);
  template.dataset.newFlow = "blank";
  template.append(
    make("strong", "", "+ New visual Flow"),
    make("small", "", "Start from a typed Action and build on canvas")
  );
  list.append(template);
  const flow = flows.find((item) => item.id === state.selectedFlowId);
  renderFlowInspector(flow);
}

function renderFlowInspector(flow) {
  const inspector = byId("flow-inspector");
  inspector.replaceChildren();
  if (state.flowDraft) {
    renderFlowDraft(inspector, state.flowDraft);
    return;
  }
  if (!flow) {
    inspector.append(emptyState("Create an Action, then compose your first Flow."));
    return;
  }
  const head = make("header", "inspector-head");
  const copy = make("div");
  copy.append(make("p", "section-kicker", `Flow · ${flow.slug}`), make("h2", "", flow.name), make("p", "", flow.description));
  const actions = make("div", "inspector-actions flow-head-actions");
  const triggerButton = make("button", "button button-quiet", "Add trigger");
  triggerButton.dataset.addTrigger = flow.id;
  const templateButton = make("button", "button button-quiet", "Use as template");
  templateButton.dataset.cloneFlow = flow.id;
  const editButton = make("button", "button button-quiet", `Edit as v${flow.version.version + 1}`);
  editButton.dataset.editFlow = flow.id;
  const runButton = make("button", "button button-primary", "Start Run");
  runButton.dataset.runFlow = flow.id;
  actions.append(triggerButton, templateButton, editButton, runButton);
  head.append(copy, actions);
  const facts = make("dl", "inspector-facts");
  facts.append(
    definitionFact("Version", `v${flow.version.version}`),
    definitionFact("Revision", flow.revision),
    definitionFact("Nodes", flow.version.nodes.length),
    definitionFact("Model path", flow.version.requires_model ? "Yes · BYOK" : "No")
  );
  const workspace = make("div", "visual-builder is-published");
  workspace.append(
    buildGraphCanvas(
      {
        start_node_id: flow.version.start_node_id,
        nodes: flow.version.nodes,
        routes: flow.version.routes
      },
      { context: `flow-${flow.id}` }
    ),
    publishedFlowSidebar(flow)
  );
  inspector.append(head, facts, workspace);
}

function cloneJson(value) {
  return JSON.parse(JSON.stringify(value));
}

function nodeDefaults(node, index) {
  return {
    ...cloneJson(node),
    position: cloneJson(node.position ?? {
      x: 110 + (index % 3) * 280,
      y: 105 + Math.floor(index / 3) * 190
    }),
    settings: cloneJson(node.settings ?? {
      max_attempts: 1,
      backoff_seconds: 0,
      retry_on: ["provider_failure"],
      on_error: "fail"
    })
  };
}

function draftFromFlow(flow, mode = "edit") {
  const copyMode = mode === "create";
  const suffix = studio().flows.length + 1;
  return {
    mode,
    flowId: copyMode ? null : flow.id,
    expectedRevision: copyMode ? null : flow.revision,
    name: copyMode ? `${flow.name} copy` : flow.name,
    slug: copyMode
      ? uniqueSlug(`${flow.slug}-copy-${suffix}`, studio().flows.map((item) => item.slug))
      : flow.slug,
    description: copyMode
      ? `Adapted from ${flow.name} v${flow.version.version}.`
      : flow.description,
    inputSchema: cloneJson(flow.version.input_schema),
    start_node_id: flow.version.start_node_id,
    nodes: flow.version.nodes.map(nodeDefaults),
    routes: cloneJson(flow.version.routes),
    baseVersion: flow.version.version
  };
}

function startNewFlow() {
  const newestFirst = [...studio().actions].reverse();
  const userComposers = newestFirst.filter(
    (action) =>
      action.version.created_by !== "bootstrap" &&
      ["transform", "template"].includes(action.version.kind)
  );
  const preferred =
    userComposers[0] ??
    newestFirst.find((action) => action.version.kind === "transform") ??
    newestFirst.find((action) => action.version.kind === "template") ??
    newestFirst[0];
  if (!preferred) {
    setView("actions");
    showError(new Error("Create at least one Action before composing a Flow."));
    return;
  }
  const suffix = studio().flows.length + 1;
  const nodeId = uniqueNodeId(preferred.slug, []);
  state.flowDraft = {
    mode: "create",
    flowId: null,
    expectedRevision: null,
    name: `Operations workflow ${suffix}`,
    slug: uniqueSlug(
      `operations-workflow-${suffix}`,
      studio().flows.map((item) => item.slug)
    ),
    description: "A visual, versioned automation built from typed capabilities.",
    inputSchema: cloneJson(preferred.version.input_schema),
    start_node_id: nodeId,
    nodes: [
      nodeDefaults(
        {
          id: nodeId,
          type: "action",
          version_id: preferred.version.id,
          input_mapping: defaultDraftMapping("action", preferred.version.id, null),
          position: { x: 160, y: 170 }
        },
        0
      )
    ],
    routes: [],
    baseVersion: 0
  };
  state.selectedBuilderNodeId = nodeId;
  state.connectFromNodeId = null;
  state.view = "flows";
  renderFlows();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function renderFlowDraft(inspector, draft) {
  const head = make("header", "inspector-head draft-head");
  const copy = make("div");
  copy.append(
    make("p", "section-kicker", draft.mode === "edit" ? `Successor version · v${draft.baseVersion + 1}` : "Unpublished Flow"),
    make("h2", "", draft.name),
    make("p", "", "Arrange capabilities, connect emitted outcomes, map typed data, then publish one immutable graph version.")
  );
  const actions = make("div", "inspector-actions");
  const discard = make("button", "button button-quiet", "Discard draft");
  discard.dataset.discardFlowDraft = "true";
  const publish = make(
    "button",
    "button button-primary",
    draft.mode === "edit" ? `Publish v${draft.baseVersion + 1}` : "Publish Flow v1"
  );
  publish.dataset.publishFlowDraft = "true";
  actions.append(discard, publish);
  head.append(copy, actions);

  const workspace = make("div", "visual-builder is-editing");
  workspace.append(
    builderPalette(),
    buildGraphCanvas(
      {
        start_node_id: draft.start_node_id,
        nodes: draft.nodes,
        routes: draft.routes
      },
      { editable: true, context: "draft" }
    ),
    builderInspector(draft)
  );
  inspector.append(head, workspace);
}

function builderPalette() {
  const palette = make("aside", "builder-palette");
  const heading = make("div", "builder-panel-head");
  heading.append(make("span", "section-kicker", "Node palette"), make("strong", "", "Capabilities"));
  palette.append(heading, make("p", "palette-help", "Drag onto the canvas or click to add."));
  const groups = [
    ["AI", ["ai"]],
    ["Logic", ["template", "transform", "delay", "condition", "assert"]],
    ["Human", ["approval"]],
    ["Data", ["data_store", "sandbox"]]
  ];
  groups.forEach(([label, kinds]) => {
    const group = make("section", "palette-group");
    group.append(make("p", "palette-label", label));
    studio().actions
      .filter((action) => kinds.includes(action.version.kind))
      .forEach((action) => group.append(paletteItem(action, "action")));
    if (group.children.length > 1) palette.append(group);
  });
  const agents = make("section", "palette-group");
  agents.append(make("p", "palette-label", "Agents"));
  state.snapshot.agents.forEach((agent) => agents.append(paletteItem(agent, "agent")));
  palette.append(agents);
  return palette;
}

function paletteItem(resource, type) {
  const button = make("button", "palette-item");
  const kind = type === "agent" ? "agent" : resource.version.kind;
  button.draggable = true;
  button.dataset.paletteType = type;
  button.dataset.paletteVersion = resource.version.id;
  button.append(
    make("span", `palette-icon node-${kind}`, nodeKindLabel(kind)),
    make("span", "", resource.name),
    make("small", "", type === "agent" ? resource.version.model : titleCase(kind))
  );
  return button;
}

function buildGraphCanvas(graph, { editable = false, context = "graph", run = null } = {}) {
  const panel = make("section", "graph-panel");
  const toolbar = make("div", "graph-toolbar");
  const title = make("div", "graph-toolbar-title");
  title.append(
    make("span", "status-light is-ready"),
    make("strong", "", run ? "Live execution graph" : editable ? "Draft canvas" : "Published graph"),
    make("small", "", `${graph.nodes.length} nodes · ${graph.routes.length} routes`)
  );
  const legend = make("div", "graph-legend");
  if (state.connectFromNodeId && editable) {
    legend.append(make("span", "connect-hint", `Connect ${state.connectFromNodeId} → choose a target`));
  } else {
    legend.append(
      make("span", "", "● start"),
      make("span", "", run ? "Live Step state" : "Outcome-labelled edges")
    );
  }
  const tools = make("div", "graph-toolbar-tools");
  tools.append(legend);
  if (editable) {
    const settings = make("button", "button button-quiet button-compact", "Flow settings");
    settings.dataset.flowSettings = "true";
    tools.append(settings);
  }
  toolbar.append(title, tools);

  const scroller = make("div", "graph-scroller");
  const surface = make("div", "graph-surface");
  surface.dataset.graphSurface = editable ? "draft" : context;
  surface.setAttribute("role", "application");
  surface.setAttribute(
    "aria-label",
    editable ? "Editable automation graph" : "Automation graph"
  );
  const nodes = graph.nodes.map(nodeDefaults);
  const width = Math.max(980, ...nodes.map((node) => node.position.x + 330));
  const height = Math.max(520, ...nodes.map((node) => node.position.y + 230));
  surface.style.width = `${width}px`;
  surface.style.height = `${height}px`;
  surface.append(graphWireLayer(graph, nodes, width, height, context));
  nodes.forEach((node) => surface.append(graphNodeCard(graph, node, { editable, run })));
  if (editable) {
    surface.addEventListener("dragover", (event) => {
      event.preventDefault();
      event.dataTransfer.dropEffect = "copy";
    });
    surface.addEventListener("drop", (event) => {
      event.preventDefault();
      const material = event.dataTransfer.getData("application/x-kyn-node");
      if (!material) return;
      const [type, versionId] = material.split(":", 2);
      const bounds = surface.getBoundingClientRect();
      addBuilderNode(type, versionId, {
        x: Math.max(30, Math.round(event.clientX - bounds.left - 110)),
        y: Math.max(30, Math.round(event.clientY - bounds.top - 55))
      });
    });
  }
  scroller.append(surface);
  panel.append(toolbar, scroller);
  return panel;
}

function graphWireLayer(graph, nodes, width, height, context) {
  const namespace = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(namespace, "svg");
  svg.classList.add("graph-wires");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("aria-hidden", "true");
  const markerId = `arrow-${context.replace(/[^a-z0-9]/gi, "-")}`;
  const defs = document.createElementNS(namespace, "defs");
  const marker = document.createElementNS(namespace, "marker");
  marker.setAttribute("id", markerId);
  marker.setAttribute("viewBox", "0 0 10 10");
  marker.setAttribute("refX", "9");
  marker.setAttribute("refY", "5");
  marker.setAttribute("markerWidth", "6");
  marker.setAttribute("markerHeight", "6");
  marker.setAttribute("orient", "auto-start-reverse");
  const arrow = document.createElementNS(namespace, "path");
  arrow.setAttribute("d", "M 0 0 L 10 5 L 0 10 z");
  marker.append(arrow);
  defs.append(marker);
  svg.append(defs);
  const positions = new Map(nodes.map((node) => [node.id, node.position]));
  graph.routes.forEach((route) => {
    const source = positions.get(route.from);
    const target = positions.get(route.to);
    if (!source || !target) return;
    const startX = source.x + 224;
    const startY = source.y + 67;
    const endX = target.x;
    const endY = target.y + 67;
    const bend = Math.max(70, Math.abs(endX - startX) * 0.48);
    const path = document.createElementNS(namespace, "path");
    path.setAttribute("d", `M ${startX} ${startY} C ${startX + bend} ${startY}, ${endX - bend} ${endY}, ${endX} ${endY}`);
    path.setAttribute("marker-end", `url(#${markerId})`);
    path.classList.add("graph-wire", `outcome-${route.outcome}`);
    const label = document.createElementNS(namespace, "text");
    label.setAttribute("x", String((startX + endX) / 2));
    label.setAttribute("y", String((startY + endY) / 2 - 8));
    label.textContent = route.outcome;
    label.classList.add("graph-wire-label");
    svg.append(path, label);
  });
  return svg;
}

function graphNodeCard(graph, node, { editable, run }) {
  const resource = resourceForNode(node);
  const kind = nodeKind(node);
  const live = runNodeState(run, node.id);
  const card = make(
    "article",
    `graph-node kind-${kind}${node.id === graph.start_node_id ? " is-start" : ""}${live ? ` is-${live}` : ""}${state.selectedBuilderNodeId === node.id && editable ? " is-selected" : ""}`
  );
  card.style.left = `${node.position.x}px`;
  card.style.top = `${node.position.y}px`;
  card.dataset.graphNode = node.id;
  if (editable) card.dataset.dragNode = node.id;
  const head = make("div", "graph-node-head");
  head.append(
    make("span", `node-kind node-${kind}`, nodeKindLabel(kind)),
    make("span", "graph-node-version", resource ? `v${resource.version.version}` : "pinned")
  );
  const body = make("button", "graph-node-body");
  if (editable) body.dataset.selectBuilderNode = node.id;
  if (run) body.dataset.selectRunNode = node.id;
  body.append(
    make("strong", "", resource?.name ?? titleCase(node.id)),
    make("small", "", node.id),
    make("code", "", short(node.version_id, 19))
  );
  const foot = make("div", "graph-node-foot");
  const status = live ?? (node.id === graph.start_node_id ? "start" : titleCase(kind));
  foot.append(make("span", "", titleCase(status)));
  if (node.settings?.max_attempts > 1) {
    foot.append(make("span", "retry-chip", `${node.settings.max_attempts} attempts`));
  }
  card.append(head, body, foot);
  if (editable) {
    const input = make("button", "graph-port graph-port-in");
    input.dataset.connectTo = node.id;
    input.setAttribute("aria-label", `Connect into ${node.id}`);
    const output = make("button", "graph-port graph-port-out");
    output.dataset.connectFrom = node.id;
    output.setAttribute("aria-label", `Connect from ${node.id}`);
    card.append(input, output);
  }
  return card;
}

function nodeKindLabel(kind) {
  const labels = {
    ai: "AI",
    agent: "AG",
    template: "TX",
    transform: "MAP",
    delay: "⏱",
    condition: "IF",
    assert: "✓?",
    approval: "H",
    data_store: "DB",
    sandbox: "DB"
  };
  return labels[kind] ?? String(kind).slice(0, 2).toUpperCase();
}

function nodeKind(node) {
  return node.type === "agent"
    ? "agent"
    : actionForVersion(node.version_id)?.version.kind ?? "action";
}

function resourceForNode(node) {
  return node.type === "agent"
    ? agentForVersion(node.version_id)
    : actionForVersion(node.version_id);
}

function runNodeState(run, nodeId) {
  if (!run) return null;
  const latest = latestStepForNode(run, nodeId);
  if (latest) return latest.status;
  if (run.current_node_id === nodeId) {
    return ["created", "waiting_approval"].includes(run.status) ? "queued" : "running";
  }
  return "pending";
}

function publishedFlowSidebar(flow) {
  const side = make("aside", "builder-inspector published-inspector");
  const heading = make("div", "builder-panel-head");
  heading.append(make("span", "section-kicker", "Operations"), make("strong", "", "Triggers + evidence"));
  side.append(heading);
  const triggers = studio().triggers.filter((trigger) => trigger.flow_id === flow.id);
  const triggerSection = make("section", "inspector-section");
  triggerSection.append(make("h3", "", "Inbound triggers"));
  if (!triggers.length) {
    triggerSection.append(make("p", "inspector-empty", "No trigger yet. Runs can still be started manually."));
  }
  triggers.forEach((trigger) => {
    const card = make("div", "trigger-card");
    const control = make(
      "button",
      "button button-quiet button-compact trigger-state-control",
      trigger.enabled ? "Disable" : "Enable"
    );
    control.dataset.toggleTrigger = trigger.id;
    card.append(
      make("span", `node-kind node-${trigger.trigger_type === "webhook" ? "transform" : "delay"}`, trigger.trigger_type === "webhook" ? "WH" : "⏱"),
      make("strong", "", trigger.name),
      make("small", "", `${trigger.enabled ? "Active" : "Disabled"} · ${trigger.trigger_type === "webhook" ? `••••••${trigger.token_hint}` : `Every ${trigger.config.interval_minutes} min`}`),
      make("code", "", trigger.last_fired_at ? `Last ${timeLabel(trigger.last_fired_at)}` : "Never fired"),
      control
    );
    triggerSection.append(card);
  });
  if (state.lastWebhook?.flow_id === flow.id) {
    const reveal = make("div", "webhook-reveal");
    reveal.append(
      make("strong", "", "Copy this URL now"),
      make("p", "", "The secret is not recoverable after this page state is replaced."),
      make("code", "", `${location.origin}${state.lastWebhook.hook_path}`)
    );
    const copy = make("button", "button button-quiet button-full", "Copy webhook URL");
    copy.dataset.copyText = `${location.origin}${state.lastWebhook.hook_path}`;
    reveal.append(copy);
    triggerSection.append(reveal);
  }
  const evidence = make("section", "inspector-section version-evidence");
  evidence.append(
    make("h3", "", "Immutable contract"),
    definitionFact("Flow version ID", short(flow.version.id, 24)),
    definitionFact("Fingerprint", short(flow.version.fingerprint, 24)),
    definitionFact("Pinned resources", flow.version.pinned_resources.length),
    definitionFact("Created by", titleCase(flow.version.created_by))
  );
  side.append(triggerSection, evidence);
  return side;
}

function builderInspector(draft) {
  const side = make("aside", "builder-inspector");
  const node = draft.nodes.find((item) => item.id === state.selectedBuilderNodeId);
  if (!node) {
    side.append(flowSettingsEditor(draft));
    return side;
  }
  const resource = resourceForNode(node);
  const heading = make("div", "builder-panel-head");
  heading.append(
    make("span", "section-kicker", "Node inspector"),
    make("strong", "", resource?.name ?? node.id)
  );
  side.append(heading);
  const identity = make("section", "inspector-section node-identity");
  identity.append(make("p", "", `${titleCase(nodeKind(node))} · pinned ${short(node.version_id, 24)}`));
  const nodeId = make("label", "mini-field");
  nodeId.append(make("span", "", "Node ID"));
  const idInput = make("input");
  idInput.value = node.id;
  idInput.pattern = "[a-z][a-z0-9-]*";
  idInput.maxLength = 64;
  idInput.addEventListener("change", () => renameDraftNode(node.id, idInput.value));
  nodeId.append(idInput);
  identity.append(nodeId);
  if (draft.start_node_id !== node.id) {
    const makeStart = make("button", "button button-quiet button-full", "Set as start node");
    makeStart.dataset.makeStartNode = node.id;
    identity.append(makeStart);
  } else {
    identity.append(make("p", "start-contract", "● Entry point for every Run"));
  }
  side.append(identity, mappingEditor(draft, node), retryEditor(node), routeEditor(draft, node));
  const remove = make("button", "button button-danger button-full inspector-remove", "Remove node");
  remove.dataset.removeBuilderNode = node.id;
  remove.disabled = draft.nodes.length === 1;
  side.append(remove);
  return side;
}

function flowSettingsEditor(draft) {
  const wrapper = make("div");
  const heading = make("div", "builder-panel-head");
  heading.append(make("span", "section-kicker", "Flow settings"), make("strong", "", "Definition"));
  wrapper.append(heading);
  [
    ["Name", "name", "text"],
    ["Slug", "slug", "text"],
    ["Description", "description", "text"]
  ].forEach(([label, key, type]) => {
    const field = make("label", "mini-field");
    field.append(make("span", "", label));
    const input = make("input");
    input.type = type;
    input.value = draft[key];
    input.dataset.flowSetting = key;
    input.disabled = draft.mode === "edit" && key !== "description";
    input.addEventListener("input", () => {
      draft[key] = input.value;
      if (key === "name") byId("flow-inspector").querySelector(".draft-head h2").textContent = input.value;
    });
    field.append(input);
    wrapper.append(field);
  });
  const schema = make("label", "mini-field advanced-field");
  schema.append(make("span", "", "Flow input JSON Schema"));
  const textarea = make("textarea", "code-input");
  textarea.rows = 12;
  textarea.value = jsonText(draft.inputSchema);
  textarea.dataset.flowSetting = "input-schema";
  textarea.addEventListener("change", () => {
    try {
      draft.inputSchema = parseJsonValue(textarea.value, "Flow input JSON Schema");
      clearError();
    } catch (error) {
      showError(error);
    }
  });
  schema.append(textarea);
  wrapper.append(
    make("p", "inspector-help", "Click a node to edit mappings and execution policy."),
    schema
  );
  return wrapper;
}

function mappingEditor(draft, node) {
  const section = make("section", "inspector-section mapping-editor");
  section.append(make("h3", "", "Input mapping"));
  const schema = nodeInputSchema(node);
  Object.entries(schema.properties ?? {}).forEach(([target, propertySchema]) => {
    const mapping = node.input_mapping[target] ?? {
      source: "literal",
      value: exampleForSchema(propertySchema, target)
    };
    node.input_mapping[target] = mapping;
    const row = make("div", "mapping-row");
    row.append(make("strong", "", target));
    const kind = make("select");
    ["input", "step", "literal"].forEach((value) => {
      const option = make("option", "", titleCase(value));
      option.value = value;
      kind.append(option);
    });
    kind.value = mapping.source;
    kind.addEventListener("change", () => {
      if (kind.value === "input") node.input_mapping[target] = { source: "input", path: target };
      else if (kind.value === "step") {
        const predecessor = draft.nodes.find((item) => item.id !== node.id);
        node.input_mapping[target] = {
          source: "step",
          node_id: predecessor?.id ?? draft.start_node_id,
          path: target
        };
      } else {
        node.input_mapping[target] = {
          source: "literal",
          value: exampleForSchema(propertySchema, target)
        };
      }
      renderFlowInspector(studio().flows.find((item) => item.id === state.selectedFlowId));
    });
    row.append(kind, mappingValueEditor(draft, node, target, mapping));
    section.append(row);
  });
  if (!Object.keys(schema.properties ?? {}).length) {
    section.append(make("p", "inspector-empty", "This node accepts no mapped fields."));
  }
  return section;
}

function mappingValueEditor(draft, node, target, mapping) {
  if (mapping.source === "step") {
    const wrapper = make("div", "mapping-step-fields");
    const source = make("select");
    draft.nodes.filter((item) => item.id !== node.id).forEach((item) => {
      const option = make("option", "", item.id);
      option.value = item.id;
      source.append(option);
    });
    source.value = mapping.node_id;
    source.addEventListener("change", () => { mapping.node_id = source.value; });
    const path = make("input");
    path.value = mapping.path;
    path.placeholder = "output.path";
    path.addEventListener("input", () => { mapping.path = path.value; });
    wrapper.append(source, path);
    return wrapper;
  }
  const input = make("input");
  input.value = mapping.source === "literal" ? jsonText(mapping.value) : mapping.path;
  input.placeholder = mapping.source === "literal" ? "JSON literal" : "input.path";
  input.addEventListener("change", () => {
    if (mapping.source === "literal") {
      try {
        mapping.value = parseJsonValue(input.value, `Literal mapping for ${target}`);
      } catch (error) {
        showError(error);
      }
    } else {
      mapping.path = input.value;
    }
  });
  return input;
}

function retryEditor(node) {
  const section = make("section", "inspector-section retry-editor");
  section.append(make("h3", "", "Failure policy"));
  const grid = make("div", "compact-grid");
  const attempts = make("label", "mini-field");
  attempts.append(make("span", "", "Attempts"));
  const attemptsInput = make("input");
  attemptsInput.type = "number";
  attemptsInput.min = "1";
  attemptsInput.max = "3";
  attemptsInput.value = node.settings.max_attempts;
  attemptsInput.addEventListener("change", () => { node.settings.max_attempts = Number(attemptsInput.value); });
  attempts.append(attemptsInput);
  const backoff = make("label", "mini-field");
  backoff.append(make("span", "", "Backoff seconds"));
  const backoffInput = make("input");
  backoffInput.type = "number";
  backoffInput.min = "0";
  backoffInput.max = "5";
  backoffInput.step = "0.25";
  backoffInput.value = node.settings.backoff_seconds;
  backoffInput.addEventListener("change", () => { node.settings.backoff_seconds = Number(backoffInput.value); });
  backoff.append(backoffInput);
  grid.append(attempts, backoff);
  const onError = make("label", "mini-field");
  onError.append(make("span", "", "After final error"));
  const errorSelect = make("select");
  [["fail", "Fail Run"], ["continue", "Follow error route"]].forEach(([value, label]) => {
    const option = make("option", "", label);
    option.value = value;
    errorSelect.append(option);
  });
  errorSelect.value = node.settings.on_error;
  errorSelect.addEventListener("change", () => { node.settings.on_error = errorSelect.value; });
  onError.append(errorSelect);
  const retry = make("label", "checkbox-field compact-check");
  const retryInput = make("input");
  retryInput.type = "checkbox";
  retryInput.checked = node.settings.retry_on.includes("provider_failure");
  retryInput.addEventListener("change", () => {
    node.settings.retry_on = retryInput.checked ? ["provider_failure"] : [];
  });
  retry.append(retryInput, make("span", "", "Retry provider failures"));
  section.append(grid, onError, retry);
  return section;
}

function routeEditor(draft, node) {
  const section = make("section", "inspector-section route-editor");
  section.append(make("h3", "", "Outgoing routes"));
  const routes = draft.routes.filter((route) => route.from === node.id);
  routes.forEach((route) => {
    const row = make("div", "route-editor-row-visual");
    const outcome = make("select");
    outcomeCandidatesForNode(node).forEach((value) => {
      const option = make("option", "", value);
      option.value = value;
      outcome.append(option);
    });
    if (![...outcome.options].some((option) => option.value === route.outcome)) {
      const option = make("option", "", route.outcome);
      option.value = route.outcome;
      outcome.append(option);
    }
    outcome.value = route.outcome;
    outcome.addEventListener("change", () => {
      if (draft.routes.some((item) => item !== route && item.from === node.id && item.outcome === outcome.value)) {
        showError(new Error(`Outcome ${outcome.value} is already connected from ${node.id}.`));
        outcome.value = route.outcome;
        return;
      }
      route.outcome = outcome.value;
      renderFlows();
    });
    row.append(outcome, make("span", "", "→"), make("strong", "", route.to));
    const remove = make("button", "icon-button", "×");
    remove.dataset.removeRoute = `${route.from}:${route.outcome}:${route.to}`;
    remove.setAttribute("aria-label", `Remove route to ${route.to}`);
    row.append(remove);
    section.append(row);
  });
  if (!routes.length) section.append(make("p", "inspector-empty", "No outgoing route. Success completes the Run here."));
  return section;
}

function nodeInputSchema(node) {
  if (node.type === "action") {
    return actionForVersion(node.version_id)?.version.input_schema ?? objectSchema({});
  }
  const agent = agentForVersion(node.version_id);
  const prompt = state.snapshot.prompts.find(
    (item) => item.version.id === agent?.version.prompt_version_id
  );
  return objectSchema(
    Object.fromEntries((prompt?.version.variables ?? []).map((name) => [name, { type: "string" }]))
  );
}

function nodeOutputSchema(node) {
  return node.type === "action"
    ? actionForVersion(node.version_id)?.version.output_schema ?? objectSchema({})
    : objectSchema({ text: { type: "string" } }, ["text"]);
}

function defaultDraftMapping(type, versionId, predecessor) {
  const node = { type, version_id: versionId };
  const fields = nodeInputSchema(node).properties ?? {};
  const flowProperties = state.flowDraft?.inputSchema.properties ?? fields;
  const previousOutput = predecessor ? nodeOutputSchema(predecessor).properties ?? {} : {};
  return Object.fromEntries(Object.entries(fields).map(([name, schema]) => {
    if (name in flowProperties) return [name, { source: "input", path: name }];
    if (predecessor && name in previousOutput) {
      return [name, { source: "step", node_id: predecessor.id, path: name }];
    }
    return [name, { source: "literal", value: exampleForSchema(schema, name) }];
  }));
}

function uniqueNodeId(base, nodes) {
  const normalized = String(base).toLowerCase().replace(/[^a-z0-9-]/g, "-").replace(/^-+|-+$/g, "") || "step";
  const existing = nodes.map((node) => node.id);
  if (!existing.includes(normalized)) return normalized;
  let suffix = 2;
  while (existing.includes(`${normalized}-${suffix}`)) suffix += 1;
  return `${normalized}-${suffix}`;
}

function addBuilderNode(type, versionId, position = null) {
  const draft = state.flowDraft;
  if (!draft || draft.nodes.length >= 12) {
    showError(new Error("A Flow can contain at most twelve nodes."));
    return;
  }
  const resource = type === "agent" ? agentForVersion(versionId) : actionForVersion(versionId);
  if (!resource) return;
  const predecessor = draft.nodes.find((node) => node.id === state.selectedBuilderNodeId) ?? draft.nodes.at(-1);
  const id = uniqueNodeId(resource.slug, draft.nodes);
  const fallbackPosition = predecessor
    ? { x: Math.min(3700, predecessor.position.x + 285), y: predecessor.position.y }
    : { x: 160, y: 170 };
  const node = nodeDefaults(
    {
      id,
      type,
      version_id: versionId,
      input_mapping: defaultDraftMapping(type, versionId, predecessor),
      position: position ?? fallbackPosition
    },
    draft.nodes.length
  );
  draft.nodes.push(node);
  if (predecessor) connectBuilderNodes(predecessor.id, id, false);
  else draft.start_node_id = id;
  state.selectedBuilderNodeId = id;
  renderFlows();
}

function outcomeCandidatesForNode(node) {
  const kind = nodeKind(node);
  if (kind === "condition") return ["true", "false", "error"];
  if (kind === "approval") return ["approved", "rejected", "error"];
  return ["success", "error"];
}

function graphReaches(routes, source, target) {
  const pending = [source];
  const seen = new Set();
  while (pending.length) {
    const current = pending.pop();
    if (current === target) return true;
    if (seen.has(current)) continue;
    seen.add(current);
    routes.filter((route) => route.from === current).forEach((route) => pending.push(route.to));
  }
  return false;
}

function connectBuilderNodes(sourceId, targetId, shouldRender = true) {
  const draft = state.flowDraft;
  if (!draft || sourceId === targetId) return;
  if (graphReaches(draft.routes, targetId, sourceId)) {
    state.connectFromNodeId = null;
    showError(new Error("That connection would create a cycle. Flows are explicit DAGs."));
    if (shouldRender) renderFlows();
    return;
  }
  const source = draft.nodes.find((node) => node.id === sourceId);
  const used = new Set(draft.routes.filter((route) => route.from === sourceId).map((route) => route.outcome));
  const outcome = outcomeCandidatesForNode(source).find((candidate) => !used.has(candidate));
  if (!outcome) {
    showError(new Error(`${sourceId} has no unassigned outcome. Edit or remove an outgoing route first.`));
    return;
  }
  draft.routes.push({ from: sourceId, to: targetId, outcome });
  state.connectFromNodeId = null;
  if (shouldRender) renderFlows();
}

function renameDraftNode(oldId, nextId) {
  const draft = state.flowDraft;
  const normalized = nextId.trim();
  if (!/^[a-z][a-z0-9-]*$/.test(normalized) || draft.nodes.some((node) => node.id === normalized && node.id !== oldId)) {
    showError(new Error("Node IDs must be unique lowercase slugs."));
    renderFlows();
    return;
  }
  const node = draft.nodes.find((item) => item.id === oldId);
  if (!node) return;
  node.id = normalized;
  draft.routes.forEach((route) => {
    if (route.from === oldId) route.from = normalized;
    if (route.to === oldId) route.to = normalized;
  });
  draft.nodes.forEach((item) => Object.values(item.input_mapping).forEach((mapping) => {
    if (mapping.source === "step" && mapping.node_id === oldId) mapping.node_id = normalized;
  }));
  if (draft.start_node_id === oldId) draft.start_node_id = normalized;
  state.selectedBuilderNodeId = normalized;
  renderFlows();
}

async function publishFlowDraft() {
  const draft = state.flowDraft;
  if (!draft) return;
  const common = {
    input_schema: cloneJson(draft.inputSchema),
    start_node_id: draft.start_node_id,
    nodes: cloneJson(draft.nodes),
    routes: cloneJson(draft.routes)
  };
  const path = draft.mode === "edit"
    ? `/api/v1/studio/flows/${draft.flowId}/versions`
    : "/api/v1/studio/flows";
  const body = draft.mode === "edit"
    ? { expected_revision: draft.expectedRevision, ...common }
    : {
      name: draft.name,
      slug: draft.slug,
      description: draft.description,
      ...common
    };
  await operation(
    "Validating graph, mappings, authority, and version pins…",
    () => api(path, { method: "POST", body }),
    draft.mode === "edit" ? `Flow v${draft.baseVersion + 1} published` : "Flow v1 published",
    (created) => {
      state.flowDraft = null;
      state.selectedBuilderNodeId = null;
      state.connectFromNodeId = null;
      state.selectedFlowId = created.id;
      state.view = "flows";
    }
  );
}

function actionForVersion(versionId) {
  return studio().actions.find((action) => action.version.id === versionId) ?? null;
}

function agentForVersion(versionId) {
  return state.snapshot?.agents.find((agent) => agent.version.id === versionId) ?? null;
}

function renderRuns() {
  const runs = studio().runs;
  if (!runs.some((run) => run.id === state.selectedRunId)) {
    state.selectedRunId = runs[0]?.id ?? null;
  }
  const list = byId("run-list");
  list.replaceChildren();
  runs.forEach((run) => {
    const flow = studio().flows.find((item) => item.id === run.flow_id);
    const button = make("button", "selection-button run-selection");
    button.dataset.selectRun = run.id;
    button.classList.toggle("is-selected", run.id === state.selectedRunId);
    button.append(
      make("strong", "", flow?.name ?? "Unknown Flow"),
      make("small", "", `${run.steps.length} Steps · ${timeLabel(run.created_at)}`),
      make("code", "", short(run.id, 23)),
      statusBadge(run.status)
    );
    list.append(button);
  });
  if (!runs.length) list.append(emptyState("No Automation Run yet."));
  renderRunInspector(runs.find((run) => run.id === state.selectedRunId));
}

function renderRunInspector(run) {
  const inspector = byId("run-inspector");
  inspector.replaceChildren();
  if (!run) {
    inspector.append(emptyState("Start a Flow to create an authoritative Run."));
    return;
  }
  const flow = studio().flows.find((item) => item.id === run.flow_id);
  const head = make("header", "run-detail-head");
  const copy = make("div");
  copy.append(
    make("p", "section-kicker", `Run · ${short(run.id, 28)}`),
    make("h2", "", flow?.name ?? "Automation Run"),
    make("p", "", run.parent_run_id ? `Linked child of ${short(run.parent_run_id, 24)}` : "Root Run with an independent correlation and immutable Flow pin.")
  );
  const actions = make("div", "inspector-actions");
  if (run.status === "created") {
    const continuation = make("button", "button button-primary", "Continue with my OpenAI key");
    continuation.dataset.continueRun = run.id;
    actions.append(continuation);
  } else if (isActiveRun(run)) {
    const cancel = make("button", "button button-danger", "Cancel Run");
    cancel.dataset.cancelRun = run.id;
    actions.append(cancel);
  }
  if (["completed", "blocked", "failed", "cancelled"].includes(run.status)) {
    const rerun = make("button", "button button-quiet", "Rerun pinned version");
    rerun.dataset.rerun = run.id;
    actions.append(rerun);
  }
  actions.append(statusBadge(run.status));
  head.append(copy, actions);
  const facts = make("dl", "run-facts");
  facts.append(
    definitionFact("Flow version", `v${run.flow_version}`),
    definitionFact("Revision", run.revision),
    definitionFact("Steps", run.steps.length),
    definitionFact("Model calls", run.model_calls.length),
    definitionFact("Effects", run.effects.length)
  );
  inspector.append(head, facts);
  if (run.pending_approval) inspector.append(approvalCallout(run));
  const graph = run.flow_graph ?? {
    start_node_id: flow?.version.start_node_id,
    nodes: flow?.version.nodes ?? [],
    routes: flow?.version.routes ?? []
  };
  const graphWorkspace = make("div", "run-graph-workspace");
  graphWorkspace.append(
    buildGraphCanvas(graph, { context: `run-${run.id}`, run }),
    runGraphSidebar(run, graph)
  );
  inspector.append(graphWorkspace);
  if (["blocked", "failed"].includes(run.status) || run.diagnosis) {
    inspector.append(runMaintenancePanel(run));
  }
  const tabs = make("div", "run-tabs", "");
  ["steps", "events", "receipts", "model calls", "data"].forEach((label) => {
    const value = label.replace(" ", "_");
    const button = make("button", `run-tab${state.runTab === value ? " is-active" : ""}`, titleCase(label));
    button.dataset.runTab = value;
    button.setAttribute("aria-pressed", String(state.runTab === value));
    tabs.append(button);
  });
  const detail = make("div", "run-detail-section");
  detail.append(runDetailContent(run, state.runTab));
  inspector.append(tabs, detail);
  scheduleRunPoll(run);
}

function runGraphSidebar(run, graph) {
  const side = make("aside", "builder-inspector run-node-inspector");
  const selectedId = state.selectedRunNodeId ?? run.steps.at(-1)?.node_id ?? run.current_node_id ?? graph.start_node_id;
  const node = graph.nodes.find((item) => item.id === selectedId);
  const attempts = run.steps.filter((step) => step.node_id === selectedId);
  const latest = attempts.at(-1);
  const heading = make("div", "builder-panel-head");
  heading.append(
    make("span", "section-kicker", "Selected Step"),
    make("strong", "", node ? titleCase(node.id) : "Run contract")
  );
  side.append(heading);
  if (!node) {
    side.append(make("p", "inspector-empty", "Select a graph node to inspect its attempt evidence."));
    return side;
  }
  const status = make("section", "inspector-section live-step-card");
  status.append(
    statusBadge(latest?.status ?? runNodeState(run, node.id)),
    make("p", "", resourceForNode(node)?.name ?? `Pinned ${node.version_id}`),
    definitionFact("Attempts", attempts.length),
    definitionFact("Target version", short(node.version_id, 22))
  );
  if (latest) {
    status.append(
      definitionFact("Input", short(jsonText(latest.input), 90)),
      definitionFact("Outcome", latest.route_outcome ?? latest.error_code ?? "pending")
    );
    if (latest.error_message) status.append(make("p", "step-error-copy", latest.error_message));
  } else {
    status.append(make("p", "inspector-empty", "This node has not started yet."));
  }
  const contract = make("section", "inspector-section");
  contract.append(
    make("h3", "", "Pinned execution policy"),
    definitionFact("Max attempts", node.settings?.max_attempts ?? 1),
    definitionFact("Backoff", `${node.settings?.backoff_seconds ?? 0}s`),
    definitionFact("On error", node.settings?.on_error ?? "fail")
  );
  side.append(status, contract);
  return side;
}

function runMaintenancePanel(run) {
  const panel = make("section", "maintenance-panel");
  const phase = maintenancePhase(run, studio().runs);
  panel.dataset.maintenancePhase = phase;
  const head = make("div", "maintenance-head");
  const copy = make("div");
  copy.append(
    make("p", "section-kicker", "Evidence-bound maintenance"),
    make("h3", "", "Diagnose → repair → approve → prove"),
    statusBadge(phase)
  );
  const actions = make("div", "inspector-actions");
  if (!run.diagnosis) {
    const diagnose = make("button", "button button-primary", "Diagnose with Agent");
    diagnose.dataset.diagnoseStudioRun = run.id;
    actions.append(diagnose);
  } else if (!run.repair && run.diagnosis.fault_class === "authority_policy") {
    const propose = make("button", "button button-primary", "Propose bounded repair");
    propose.dataset.proposeStudioRepair = run.diagnosis.id;
    actions.append(propose);
  } else if (run.repair.status === "proposed") {
    const approve = make("button", "button button-primary", "Review + approve repair");
    approve.dataset.approveStudioRepair = run.repair.id;
    actions.append(approve);
  } else if (run.repair.status === "applied" && phase !== "proven") {
    const proof = make("button", "button button-primary", "Run proof on repaired version");
    proof.dataset.proveStudioRepair = run.repair.id;
    proof.dataset.parentRun = run.id;
    actions.append(proof);
  }
  head.append(copy, actions);
  panel.append(head);
  const grid = make("div", "maintenance-grid");
  const diagnosis = make("article", `maintenance-stage${run.diagnosis ? " is-complete" : ""}`);
  diagnosis.append(make("span", "maintenance-number", "01"), make("h4", "", "Grounded diagnosis"));
  if (run.diagnosis) {
    diagnosis.append(
      make("strong", "", run.diagnosis.root_cause),
      make("p", "", run.diagnosis.explanation),
      make("small", "", `${Math.round(run.diagnosis.confidence * 100)}% confidence · ${run.diagnosis.evidence_event_ids.length} owned events`)
    );
  } else diagnosis.append(make("p", "", "A pinned diagnostician may explain only code-owned causal evidence."));
  const repair = make("article", `maintenance-stage${run.repair ? " is-complete" : ""}`);
  repair.append(make("span", "maintenance-number", "02"), make("h4", "", "Bounded proposal"));
  if (run.repair) {
    repair.append(
      make("pre", "repair-patch", jsonText(run.repair.patch)),
      make("small", "", `Hash ${short(run.repair.proposal_hash, 20)} · ${titleCase(run.repair.status)}`)
    );
  } else if (run.diagnosis && run.diagnosis.fault_class !== "authority_policy") {
    repair.append(
      make("p", "", "This fault has no automatic public repair. Correct the provider or definition, then create a linked rerun.")
    );
  } else {
    repair.append(make("p", "", "Only a code-owned, allowlisted patch can cross the maintenance boundary."));
  }
  const proof = make("article", `maintenance-stage${run.repair?.status === "applied" ? " is-complete" : ""}`);
  proof.append(make("span", "maintenance-number", "03"), make("h4", "", "Successor + proof"));
  if (run.repair?.status === "applied") {
    const child = studio().runs.find((item) => item.parent_run_id === run.id && item.flow_version === run.repair.applied_flow_version);
    proof.append(
      make("strong", "", `Action v${run.repair.applied_action_version} · Flow v${run.repair.applied_flow_version}`),
      make("p", "", child ? `Proof Run ${titleCase(child.status)} with ${child.effects.length} committed effects.` : "The failed Run is unchanged. Execute a linked child to prove the changed outcome.")
    );
    if (child) {
      const open = make("button", "button button-quiet button-full", "Open proof Run");
      open.dataset.selectRun = child.id;
      proof.append(open);
    }
  } else proof.append(make("p", "", "Approval creates successor versions; only a linked child Run can prove the outcome."));
  grid.append(diagnosis, repair, proof);
  panel.append(grid);
  return panel;
}

function approvalCallout(run) {
  const wrapper = make("div", "approval-callout");
  const copy = make("div");
  copy.append(make("strong", "", "Human approval required"), make("p", "", run.pending_approval.message));
  const actions = make("div", "approval-actions");
  const reject = make("button", "button button-danger", "Reject");
  reject.dataset.approval = run.pending_approval.id;
  reject.dataset.approved = "false";
  const approve = make("button", "button button-primary", "Approve + resume");
  approve.dataset.approval = run.pending_approval.id;
  approve.dataset.approved = "true";
  actions.append(reject, approve);
  wrapper.append(copy, actions);
  return wrapper;
}

function runDetailContent(run, tab) {
  if (tab === "data") {
    const wrapper = make("div");
    wrapper.append(
      labeledJson("Validated input", run.input),
      labeledJson("Terminal output", run.output),
      labeledJson("Sandbox effects", run.effects)
    );
    return wrapper;
  }
  const mapping = {
    steps: run.steps,
    events: run.events,
    receipts: run.action_receipts,
    model_calls: run.model_calls
  };
  const items = mapping[tab] ?? [];
  const list = make("div", `${tab.replace("_calls", "")}-list`);
  if (!items.length) {
    list.append(emptyState(`No ${titleCase(tab)} recorded for this Run.`));
    return list;
  }
  items.forEach((item, index) => list.append(runtimeRow(tab, item, index)));
  return list;
}

function labeledJson(label, value) {
  const section = make("section", "section-block");
  section.append(make("p", "section-kicker", label), make("pre", "json-block", jsonText(value)));
  return section;
}

function runtimeRow(type, item, index) {
  const className = type === "events" ? "ledger-row" : type === "model_calls" ? "model-row" : type === "receipts" ? "receipt-row" : "step-row";
  const row = make("div", className);
  let title = item.node_id ?? item.type ?? item.action_version_id ?? item.model ?? "record";
  let detail = item.status ?? item.outcome ?? item.provider_response_id ?? item.actor_type ?? "committed";
  let tail = item.event_hash ?? item.id;
  row.append(
    make("span", "", String(item.sequence ?? index + 1).padStart(2, "0")),
    make("strong", "", titleCase(title)),
    make("small", "", titleCase(detail)),
    make("code", "", short(tail, 15))
  );
  row.title = jsonText(item);
  return row;
}

function renderResources() {
  document.querySelectorAll("[data-resource-tab]").forEach((button) => {
    const active = button.dataset.resourceTab === state.resourceTab;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-selected", String(active));
  });
  setText("create-resource", `New ${titleCase(state.resourceTab.slice(0, -1))}`);
  const resources = state.snapshot[state.resourceTab] ?? [];
  const list = byId("resource-list");
  list.replaceChildren();
  resources.forEach((resource) => list.append(resourceCard(resource, state.resourceTab)));
  if (!resources.length) list.append(emptyState(`No ${titleCase(state.resourceTab)} yet.`));
}

function resourceCard(resource, kind) {
  const version = resource.version;
  const card = make("article", "resource-definition-card");
  const head = make("div", "resource-card-head");
  head.append(make("span", "kind-chip", kind.slice(0, -1).toUpperCase()), make("span", "version-chip", `v${version.version}`));
  card.append(head, make("h2", "", resource.name));
  if (kind === "agents") {
    card.append(make("p", "", `${titleCase(version.role)} on ${version.model}. Prompt and Skill versions are pinned.`));
    card.append(resourceFacts([
      ["Prompt", short(version.prompt_version_id, 22)],
      ["Skills", version.skill_version_ids.length],
      ["Callable Actions", version.effective_action_version_ids.length],
      ["Fingerprint", short(version.fingerprint, 22)]
    ]));
  } else if (kind === "prompts") {
    card.append(make("p", "", short(version.template, 180)));
    card.append(resourceFacts([
      ["Variables", version.variables.join(", ") || "none"],
      ["Fingerprint", short(version.fingerprint, 22)]
    ]));
  } else {
    card.append(make("p", "", short(version.instructions, 180)));
    card.append(resourceFacts([
      ["Local tools", version.allowed_tools.join(", ") || "none"],
      ["Action grants", version.allowed_action_version_ids.length],
      ["Fingerprint", short(version.fingerprint, 22)]
    ]));
  }
  return card;
}

function resourceFacts(items) {
  const list = make("dl");
  items.forEach(([term, value]) => {
    const wrapper = make("div");
    wrapper.append(make("dt", "", term), make("dd", "", value));
    list.append(wrapper);
  });
  return list;
}

function openActionDialog() {
  populateActionAgents();
  byId("action-kind").value = "template";
  applyActionPreset("template");
  byId("action-dialog").showModal();
  byId("action-name").focus();
}

function populateActionAgents() {
  const select = byId("action-agent");
  select.replaceChildren();
  state.snapshot.agents.forEach((agent) => {
    const option = make("option", "", `${agent.name} · ${agent.version.model} · v${agent.version.version}`);
    option.value = agent.version.id;
    select.append(option);
  });
}

function aiPreset() {
  const selected = byId("action-agent").value;
  const agent = agentForVersion(selected) ?? state.snapshot.agents[0];
  const prompt = state.snapshot.prompts.find(
    (item) => item.version.id === agent?.version.prompt_version_id
  );
  const variables = prompt?.version.variables ?? ["brief"];
  const properties = Object.fromEntries(
    variables.map((variable) => [variable, { type: "string", maxLength: 4000 }])
  );
  return {
    name: "AI analysis",
    slug: `ai-analysis-${studio().actions.length + 1}`,
    description: "Run a pinned Agent, Prompt, and Skills through OpenAI Responses.",
    input: objectSchema(properties, variables),
    output: objectSchema({ summary: { type: "string" } }, ["summary"]),
    config: { max_tool_calls: 2, reasoning_effort: "medium" }
  };
}

function applyActionPreset(kind) {
  const preset = kind === "ai" ? aiPreset() : ACTION_PRESETS[kind];
  if (!preset) return;
  byId("action-agent-field").hidden = kind !== "ai";
  byId("action-name").value = preset.name;
  byId("action-slug").value = uniqueSlug(preset.slug, studio().actions.map((item) => item.slug));
  byId("action-description").value = preset.description;
  byId("action-input-schema").value = jsonText(preset.input);
  byId("action-output-schema").value = jsonText(preset.output);
  byId("action-config").value = jsonText(preset.config);
}

function uniqueSlug(base, existing) {
  if (!existing.includes(base)) return base;
  let suffix = 2;
  while (existing.includes(`${base}-${suffix}`)) suffix += 1;
  return `${base}-${suffix}`;
}

async function submitAction(event) {
  event.preventDefault();
  if (!event.currentTarget.reportValidity()) return;
  const kind = byId("action-kind").value;
  const body = {
    name: byId("action-name").value,
    slug: byId("action-slug").value,
    description: byId("action-description").value,
    kind,
    input_schema: parseJsonField("action-input-schema", "Input JSON Schema"),
    output_schema: parseJsonField("action-output-schema", "Output JSON Schema"),
    config: parseJsonField("action-config", "Action configuration"),
    agent_version_id: kind === "ai" ? byId("action-agent").value : null
  };
  byId("action-dialog").close();
  await operation(
    "Creating immutable Action version…",
    () => api("/api/v1/studio/actions", { method: "POST", body }),
    `${body.name} Action v1 created`,
    () => { state.view = "actions"; }
  );
}

function openRunDialog(flowId = state.selectedFlowId) {
  const select = byId("run-flow");
  select.replaceChildren();
  studio().flows.forEach((flow) => {
    const option = make("option", "", `${flow.name} · v${flow.version.version}`);
    option.value = flow.id;
    select.append(option);
  });
  if (flowId) select.value = flowId;
  updateRunEditor();
  byId("run-dialog").showModal();
  byId("run-input").focus();
}

function updateRunEditor() {
  const flow = studio().flows.find((item) => item.id === byId("run-flow").value);
  if (!flow) return;
  byId("run-input").value = jsonText(exampleForSchema(flow.version.input_schema));
  setText(
    "run-model-note",
    flow.version.requires_model
      ? "This Flow contains a model path. The browser-owned OpenAI key will be attached to this request only."
      : "This Flow is deterministic and runs without an OpenAI credential."
  );
}

function exampleForSchema(schema, property = "") {
  if (schema.enum?.length) return schema.enum[0];
  if (schema.type === "object") {
    return Object.fromEntries(
      Object.entries(schema.properties).map(([name, child]) => [name, exampleForSchema(child, name)])
    );
  }
  if (schema.type === "array") return [exampleForSchema(schema.items, property)];
  if (schema.type === "number" || schema.type === "integer") {
    return Math.max(schema.minimum ?? 0, property.includes("score") ? 0.9 : 1);
  }
  if (schema.type === "boolean") return true;
  if (schema.type === "null") return null;
  if (property === "brief") {
    return (
      "Launch a Build Week preview for judges. A 20–4000 character brief enters a " +
      "pinned GPT-5.6 Agent, which must return summary:string, score:number from 0 to 1, " +
      "and risks:string[]. A deterministic gate requires score >= 0.75. Passing work " +
      "must pause for an attributable human decision; approval may append exactly one " +
      "idempotent row only to this workspace's SQLite approved_launches sandbox. Success " +
      "means the model call, typed Steps, Action receipts, decision, hash-linked events, " +
      "and effect are inspectable, with zero effects before approval."
    );
  }
  if (property === "name") return "Ada";
  if (property === "summary") return "A bounded automation result ready for review.";
  return `Example ${property || "value"}`;
}

async function submitRun(event) {
  event.preventDefault();
  if (!event.currentTarget.reportValidity()) return;
  const flow = studio().flows.find((item) => item.id === byId("run-flow").value);
  const input = parseJsonField("run-input", "Run input");
  byId("run-dialog").close();
  await operation(
    "Pinning Run and handing it to the bounded worker…",
    () => api(`/api/v1/studio/flows/${flow.id}/runs:enqueue`, {
      method: "POST",
      body: { input, idempotency_key: crypto.randomUUID() },
      modelAction: flow.version.requires_model
    }),
    "Authoritative Run queued",
    (run) => {
      state.selectedRunId = run.id;
      state.view = "runs";
      state.runTab = "steps";
    }
  );
}

let runPollTimer = null;
let runPollInFlight = false;

function scheduleRunPoll(run) {
  window.clearTimeout(runPollTimer);
  runPollTimer = null;
  if (
    state.view !== "runs" ||
    !run ||
    !["created", "running"].includes(run.status)
  ) return;
  runPollTimer = window.setTimeout(async () => {
    if (runPollInFlight) return;
    runPollInFlight = true;
    try {
      const updated = await api(`/api/v1/studio/runs/${run.id}`);
      const index = studio().runs.findIndex((item) => item.id === updated.id);
      if (index >= 0) state.snapshot.studio.runs[index] = updated;
      else state.snapshot.studio.runs.unshift(updated);
      renderRuns();
      renderOverview();
      renderNavigationCounts();
    } catch (error) {
      showError(error instanceof Error ? error : new Error("Run polling failed"));
    } finally {
      runPollInFlight = false;
    }
  }, 650);
}

async function continueStudioRun(runId) {
  await operation(
    "Attaching the browser credential and resuming the pinned Run…",
    () => api(`/api/v1/studio/runs/${runId}:continue`, {
      method: "POST",
      body: {},
      modelAction: true
    }),
    "Run resumed on the bounded worker",
    (run) => {
      state.selectedRunId = run.id;
      state.selectedRunNodeId = run.current_node_id;
      state.view = "runs";
    }
  );
}

async function cancelStudioRun(runId) {
  await operation(
    "Committing an attributable cancellation…",
    () => api(`/api/v1/studio/runs/${runId}:cancel`, {
      method: "POST",
      body: {
        actor: "workflow-operator",
        reason: "Cancelled from the live operations console."
      }
    }),
    "Run cancelled with immutable evidence"
  );
}

async function diagnoseStudioRun(runId) {
  await operation(
    "Grounding the diagnostician in code-owned Run evidence…",
    () => api(`/api/v1/studio/runs/${runId}/diagnoses`, {
      method: "POST",
      body: {},
      modelAction: true
    }),
    "Evidence-bound diagnosis committed"
  );
}

async function proposeStudioRepair(diagnosisId) {
  await operation(
    "Computing the allowlisted repair against exact pinned versions…",
    () => api(`/api/v1/studio/diagnoses/${diagnosisId}/repairs`, {
      method: "POST",
      body: {}
    }),
    "Bounded repair proposal committed"
  );
}

function openStudioRepair(proposalId) {
  const run = studio().runs.find((item) => item.repair?.id === proposalId);
  if (!run?.repair) return;
  byId("studio-repair-id").value = proposalId;
  byId("studio-repair-ack").checked = false;
  const preview = byId("studio-repair-patch");
  preview.replaceChildren(
    make("p", "section-kicker", `Exact patch · ${short(run.repair.proposal_hash, 26)}`),
    make("pre", "json-block", jsonText(run.repair.patch)),
    make("p", "field-help", `Expected Flow revision ${run.repair.expected_flow_revision} · Action v${run.repair.expected_action_version}`)
  );
  byId("studio-repair-dialog").showModal();
  byId("studio-repair-actor").focus();
}

async function submitStudioRepair(event) {
  event.preventDefault();
  if (!event.currentTarget.reportValidity()) return;
  const proposalId = byId("studio-repair-id").value;
  const run = studio().runs.find((item) => item.repair?.id === proposalId);
  if (!run?.repair) return;
  const body = {
    proposal_hash: run.repair.proposal_hash,
    expected_flow_revision: run.repair.expected_flow_revision,
    expected_action_version: run.repair.expected_action_version,
    actor: byId("studio-repair-actor").value,
    reason: byId("studio-repair-reason").value,
    acknowledged: byId("studio-repair-ack").checked
  };
  byId("studio-repair-dialog").close();
  await operation(
    "Applying the revision-fenced repair as successor versions…",
    () => api(`/api/v1/studio/repairs/${proposalId}/apply`, {
      method: "POST",
      body
    }),
    "Repair applied as immutable successor versions"
  );
}

async function proveStudioRepair(proposalId, parentRunId) {
  const parent = studio().runs.find((item) => item.id === parentRunId);
  const flow = studio().flows.find((item) => item.id === parent?.flow_id);
  if (!parent) return;
  await operation(
    "Executing a linked proof Run on the repaired Flow version…",
    () => api(`/api/v1/studio/repairs/${proposalId}/proof`, {
      method: "POST",
      body: { input: parent.input, idempotency_key: crypto.randomUUID() },
      modelAction: Boolean(flow?.version.requires_model)
    }),
    "Proof Run committed with a changed outcome",
    (child) => {
      state.selectedRunId = child.id;
      state.selectedRunNodeId = child.current_node_id;
      state.view = "runs";
    }
  );
}

function openTriggerDialog(flowId) {
  const flow = studio().flows.find((item) => item.id === flowId);
  if (!flow) return;
  byId("trigger-flow-id").value = flow.id;
  byId("trigger-name").value = `${flow.name} intake`;
  byId("trigger-type").value = "webhook";
  updateTriggerEditor();
  byId("trigger-dialog").showModal();
  byId("trigger-name").focus();
}

function updateTriggerEditor() {
  const schedule = byId("trigger-type").value === "schedule";
  byId("trigger-schedule-fields").hidden = !schedule;
  const flow = studio().flows.find((item) => item.id === byId("trigger-flow-id").value);
  if (schedule && flow) {
    byId("trigger-input").value = jsonText(exampleForSchema(flow.version.input_schema));
  }
}

async function submitTrigger(event) {
  event.preventDefault();
  if (!event.currentTarget.reportValidity()) return;
  const flowId = byId("trigger-flow-id").value;
  const triggerType = byId("trigger-type").value;
  const body = {
    name: byId("trigger-name").value,
    trigger_type: triggerType,
    config: triggerType === "schedule"
      ? {
        interval_minutes: Number(byId("trigger-interval").value),
        input: parseJsonField("trigger-input", "Scheduled input")
      }
      : {}
  };
  byId("trigger-dialog").close();
  await operation(
    "Pinning trigger to the current immutable Flow version…",
    () => api(`/api/v1/studio/flows/${flowId}/triggers`, {
      method: "POST",
      body
    }),
    `${titleCase(triggerType)} trigger created`,
    (created) => {
      state.lastWebhook = created.hook_path ? { ...created, flow_id: flowId } : null;
      state.selectedFlowId = flowId;
      state.view = "flows";
    }
  );
}

async function toggleStudioTrigger(triggerId) {
  const trigger = studio().triggers.find((item) => item.id === triggerId);
  if (!trigger) return;
  const enabled = !trigger.enabled;
  await operation(
    `${enabled ? "Enabling" : "Disabling"} trigger with revision fence…`,
    () => api(`/api/v1/studio/triggers/${trigger.id}/state`, {
      method: "POST",
      body: {
        enabled,
        expected_revision: trigger.revision
      }
    }),
    `Trigger ${enabled ? "enabled" : "disabled"}`
  );
}

function openApproval(requestId, approved) {
  const run = studio().runs.find((item) => item.pending_approval?.id === requestId);
  if (!run) return;
  byId("approval-request-id").value = requestId;
  byId("approval-value").value = String(approved);
  setText("approval-message", run.pending_approval.message);
  setText("submit-approval", approved ? "Approve and resume" : "Reject and block");
  byId("submit-approval").className = approved ? "button button-primary" : "button button-danger";
  byId("approval-dialog").showModal();
  byId("approval-actor").focus();
}

function continuationRequiresModel(run) {
  if (!run?.current_node_id) return false;
  const flow = studio().flows.find((item) => item.id === run.flow_id);
  if (!flow) return false;
  const nodes = new Map(flow.version.nodes.map((node) => [node.id, node]));
  const adjacency = new Map(flow.version.nodes.map((node) => [node.id, []]));
  flow.version.routes.forEach((route) => adjacency.get(route.from)?.push(route.to));
  const pending = [run.current_node_id];
  const seen = new Set();
  while (pending.length) {
    const nodeId = pending.pop();
    if (seen.has(nodeId)) continue;
    seen.add(nodeId);
    const node = nodes.get(nodeId);
    if (node?.type === "agent") return true;
    if (node?.type === "action" && actionForVersion(node.version_id)?.version.kind === "ai") return true;
    pending.push(...(adjacency.get(nodeId) ?? []));
  }
  return false;
}

async function submitApproval(event) {
  event.preventDefault();
  if (!event.currentTarget.reportValidity()) return;
  const requestId = byId("approval-request-id").value;
  const approved = byId("approval-value").value === "true";
  const run = studio().runs.find((item) => item.pending_approval?.id === requestId);
  const body = {
    approved,
    actor: byId("approval-actor").value,
    reason: byId("approval-reason").value
  };
  byId("approval-dialog").close();
  await operation(
    approved ? "Committing approval and resuming Flow…" : "Committing rejection…",
    () => api(`/api/v1/studio/approvals/${requestId}/decisions`, {
      method: "POST",
      body,
      modelAction: approved && continuationRequiresModel(run)
    }),
    approved ? "Run resumed from immutable approval" : "Run blocked by human decision",
    (updated) => {
      state.selectedRunId = updated.id;
      state.view = "runs";
    }
  );
}

async function rerunStudio(runId) {
  const run = studio().runs.find((item) => item.id === runId);
  const flow = studio().flows.find((item) => item.id === run?.flow_id);
  if (!run || !flow) return;
  await operation(
    "Creating linked child Run…",
    () => api(`/api/v1/studio/runs/${run.id}/reruns`, {
      method: "POST",
      body: { input: run.input, idempotency_key: crypto.randomUUID() },
      modelAction: flow.version.requires_model
    }),
    "Linked child Run created",
    (child) => {
      state.selectedRunId = child.id;
      state.view = "runs";
      state.runTab = "steps";
    }
  );
}

function openResourceDialog() {
  if (state.resourceTab === "prompts") {
    byId("prompt-slug").value = uniqueSlug("brief-analyst", state.snapshot.prompts.map((item) => item.slug));
    byId("prompt-dialog").showModal();
    byId("prompt-name").focus();
  } else if (state.resourceTab === "skills") {
    populateSkillActions();
    byId("skill-slug").value = uniqueSlug("evidence-first-analyst", state.snapshot.skills.map((item) => item.slug));
    byId("skill-dialog").showModal();
    byId("skill-name").focus();
  } else {
    populateAgentResources();
    byId("agent-slug").value = uniqueSlug("brief-analyst-agent", state.snapshot.agents.map((item) => item.slug));
    byId("agent-dialog").showModal();
    byId("agent-name").focus();
  }
}

function populateSkillActions() {
  const choices = byId("skill-action-choices");
  choices.replaceChildren();
  studio().actions.forEach((action) => {
    const label = make("label");
    const input = make("input");
    input.type = "checkbox";
    input.value = action.version.id;
    input.dataset.skillAction = "true";
    label.append(input, make("span", "", `${action.name} · ${titleCase(action.version.kind)}`));
    choices.append(label);
  });
}

function populateAgentResources() {
  const prompts = byId("agent-prompt");
  prompts.replaceChildren();
  state.snapshot.prompts.forEach((prompt) => {
    const option = make("option", "", `${prompt.name} · v${prompt.version.version}`);
    option.value = prompt.version.id;
    prompts.append(option);
  });
  const choices = byId("agent-skill-choices");
  choices.replaceChildren();
  state.snapshot.skills.forEach((skill) => {
    const label = make("label");
    const input = make("input");
    input.type = "checkbox";
    input.value = skill.version.id;
    input.dataset.agentSkill = "true";
    label.append(input, make("span", "", `${skill.name} · v${skill.version.version}`));
    choices.append(label);
  });
}

async function submitPrompt(event) {
  event.preventDefault();
  if (!event.currentTarget.reportValidity()) return;
  const variables = byId("prompt-variables").value.split(",").map((item) => item.trim()).filter(Boolean);
  const body = {
    name: byId("prompt-name").value,
    slug: byId("prompt-slug").value,
    template: byId("prompt-template").value,
    variables
  };
  byId("prompt-dialog").close();
  await operation("Creating Prompt version…", () => api("/api/v1/prompts", { method: "POST", body }), "Prompt v1 created");
}

async function submitSkill(event) {
  event.preventDefault();
  if (!event.currentTarget.reportValidity()) return;
  const body = {
    name: byId("skill-name").value,
    slug: byId("skill-slug").value,
    instructions: byId("skill-instructions").value,
    allowed_tools: [],
    allowed_action_version_ids: [...document.querySelectorAll("[data-skill-action]:checked")].map((input) => input.value)
  };
  byId("skill-dialog").close();
  await operation("Creating Skill authority version…", () => api("/api/v1/skills", { method: "POST", body }), "Skill v1 created");
}

async function submitAgent(event) {
  event.preventDefault();
  if (!event.currentTarget.reportValidity()) return;
  const body = {
    name: byId("agent-name").value,
    slug: byId("agent-slug").value,
    role: byId("agent-role").value,
    model: byId("agent-model").value,
    instructions: byId("agent-instructions").value,
    prompt_version_id: byId("agent-prompt").value,
    skill_version_ids: [...document.querySelectorAll("[data-agent-skill]:checked")].map((input) => input.value)
  };
  byId("agent-dialog").close();
  await operation("Creating pinned Agent version…", () => api("/api/v1/agents", { method: "POST", body }), "Agent v1 created");
}

function saveConfig(event) {
  event.preventDefault();
  const value = byId("openai-api-key").value;
  if (value !== value.trim() || value.length < 20 || value.length > 512 || /\s/.test(value)) {
    showError(new Error("Enter a valid OpenAI API key without whitespace."));
    return;
  }
  try {
    sessionStorage.setItem(OPENAI_KEY_SLOT, value);
  } catch {
    showError(new Error("This browser blocked session storage for the API key."));
    return;
  }
  clearError();
  renderKeyStatus();
  toast("OpenAI key saved for this tab only");
  announce("OpenAI key configured for this tab");
}

function clearConfig() {
  try {
    sessionStorage.removeItem(OPENAI_KEY_SLOT);
  } catch {
    // The UI still clears its visible field when storage access is unavailable.
  }
  byId("openai-api-key").value = "";
  renderKeyStatus();
  toast("OpenAI key cleared from this tab");
}

function closeDialog(id) {
  const dialog = byId(id);
  if (dialog.open) dialog.close();
}

function removeBuilderNode(nodeId) {
  const draft = state.flowDraft;
  if (!draft || draft.nodes.length === 1) return;
  draft.nodes = draft.nodes.filter((node) => node.id !== nodeId);
  draft.routes = draft.routes.filter((route) => route.from !== nodeId && route.to !== nodeId);
  draft.nodes.forEach((node) => {
    Object.entries(node.input_mapping).forEach(([target, mapping]) => {
      if (mapping.source === "step" && mapping.node_id === nodeId) {
        node.input_mapping[target] = {
          source: "literal",
          value: exampleForSchema(nodeInputSchema(node).properties?.[target] ?? { type: "string" }, target)
        };
      }
    });
  });
  if (draft.start_node_id === nodeId) draft.start_node_id = draft.nodes[0].id;
  state.selectedBuilderNodeId = draft.nodes[0]?.id ?? null;
  state.connectFromNodeId = null;
  renderFlows();
}

function removeDraftRoute(material) {
  const [source, outcome, target] = material.split(":");
  state.flowDraft.routes = state.flowDraft.routes.filter(
    (route) => !(route.from === source && route.outcome === outcome && route.to === target)
  );
  renderFlows();
}

function clonePublishedFlow(flowId) {
  const flow = studio().flows.find((item) => item.id === flowId);
  if (!flow) return;
  state.flowDraft = draftFromFlow(flow, "create");
  state.selectedBuilderNodeId = state.flowDraft.start_node_id;
  state.connectFromNodeId = null;
  renderFlows();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function editPublishedFlow(flowId) {
  const flow = studio().flows.find((item) => item.id === flowId);
  if (!flow) return;
  state.flowDraft = draftFromFlow(flow, "edit");
  state.selectedBuilderNodeId = state.flowDraft.start_node_id;
  state.connectFromNodeId = null;
  renderFlows();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

document.addEventListener("dragstart", (event) => {
  const item = event.target instanceof Element
    ? event.target.closest("[data-palette-type][data-palette-version]")
    : null;
  if (!item || !event.dataTransfer) return;
  event.dataTransfer.effectAllowed = "copy";
  event.dataTransfer.setData(
    "application/x-kyn-node",
    `${item.dataset.paletteType}:${item.dataset.paletteVersion}`
  );
});

document.addEventListener("pointerdown", (event) => {
  const target = event.target instanceof Element ? event.target : null;
  const card = target?.closest("[data-drag-node]");
  if (
    !card ||
    event.button !== 0 ||
    target.closest("button, input, select, textarea, a") ||
    !state.flowDraft
  ) return;
  const node = state.flowDraft.nodes.find((item) => item.id === card.dataset.dragNode);
  if (!node) return;
  event.preventDefault();
  const origin = { x: event.clientX, y: event.clientY };
  const start = { ...node.position };
  card.classList.add("is-dragging");
  card.setPointerCapture?.(event.pointerId);
  const move = (moveEvent) => {
    node.position.x = Math.max(0, Math.min(4000, Math.round(start.x + moveEvent.clientX - origin.x)));
    node.position.y = Math.max(0, Math.min(4000, Math.round(start.y + moveEvent.clientY - origin.y)));
    card.style.left = `${node.position.x}px`;
    card.style.top = `${node.position.y}px`;
  };
  const finish = () => {
    card.classList.remove("is-dragging");
    document.removeEventListener("pointermove", move);
    document.removeEventListener("pointerup", finish);
    renderFlows();
  };
  document.addEventListener("pointermove", move);
  document.addEventListener("pointerup", finish, { once: true });
});

document.addEventListener("click", (event) => {
  const target = event.target instanceof Element ? event.target : null;
  if (!target) return;
  const navigation = target.closest("[data-view], [data-view-target], [data-navigate]");
  if (navigation) {
    event.preventDefault();
    setView(navigation.dataset.view ?? navigation.dataset.viewTarget ?? navigation.dataset.navigate);
    return;
  }
  const close = target.closest("[data-close-dialog]");
  if (close) {
    closeDialog(close.dataset.closeDialog);
    return;
  }
  const remove = target.closest("[data-remove-row]");
  if (remove) {
    remove.closest(".editor-row")?.remove();
    return;
  }
  const flowSelection = target.closest("[data-select-flow]");
  if (flowSelection) {
    state.flowDraft = null;
    state.selectedBuilderNodeId = null;
    state.connectFromNodeId = null;
    state.selectedFlowId = flowSelection.dataset.selectFlow;
    renderFlows();
    return;
  }
  const newFlow = target.closest("[data-new-flow]");
  if (newFlow) {
    startNewFlow();
    return;
  }
  const palette = target.closest("[data-palette-type][data-palette-version]");
  if (palette) {
    addBuilderNode(palette.dataset.paletteType, palette.dataset.paletteVersion);
    return;
  }
  const editFlow = target.closest("[data-edit-flow]");
  if (editFlow) {
    editPublishedFlow(editFlow.dataset.editFlow);
    return;
  }
  const cloneFlow = target.closest("[data-clone-flow]");
  if (cloneFlow) {
    clonePublishedFlow(cloneFlow.dataset.cloneFlow);
    return;
  }
  if (target.closest("[data-discard-flow-draft]")) {
    state.flowDraft = null;
    state.selectedBuilderNodeId = null;
    state.connectFromNodeId = null;
    renderFlows();
    return;
  }
  if (target.closest("[data-publish-flow-draft]")) {
    publishFlowDraft();
    return;
  }
  const builderNode = target.closest("[data-select-builder-node]");
  if (builderNode) {
    state.selectedBuilderNodeId = builderNode.dataset.selectBuilderNode;
    state.connectFromNodeId = null;
    renderFlows();
    return;
  }
  if (target.closest("[data-flow-settings]")) {
    state.selectedBuilderNodeId = null;
    state.connectFromNodeId = null;
    renderFlows();
    return;
  }
  const connectFrom = target.closest("[data-connect-from]");
  if (connectFrom) {
    state.connectFromNodeId = connectFrom.dataset.connectFrom;
    renderFlows();
    return;
  }
  const connectTo = target.closest("[data-connect-to]");
  if (connectTo) {
    if (state.connectFromNodeId) {
      connectBuilderNodes(state.connectFromNodeId, connectTo.dataset.connectTo);
    } else {
      state.selectedBuilderNodeId = connectTo.dataset.connectTo;
      renderFlows();
    }
    return;
  }
  const makeStart = target.closest("[data-make-start-node]");
  if (makeStart) {
    state.flowDraft.start_node_id = makeStart.dataset.makeStartNode;
    renderFlows();
    return;
  }
  const removeNode = target.closest("[data-remove-builder-node]");
  if (removeNode) {
    removeBuilderNode(removeNode.dataset.removeBuilderNode);
    return;
  }
  const removeRoute = target.closest("[data-remove-route]");
  if (removeRoute) {
    removeDraftRoute(removeRoute.dataset.removeRoute);
    return;
  }
  const addTrigger = target.closest("[data-add-trigger]");
  if (addTrigger) {
    openTriggerDialog(addTrigger.dataset.addTrigger);
    return;
  }
  const copy = target.closest("[data-copy-text]");
  if (copy) {
    navigator.clipboard.writeText(copy.dataset.copyText).then(
      () => toast("Webhook URL copied"),
      () => showError(new Error("Clipboard access was blocked. Select the URL manually."))
    );
    return;
  }
  const toggleTrigger = target.closest("[data-toggle-trigger]");
  if (toggleTrigger) {
    toggleStudioTrigger(toggleTrigger.dataset.toggleTrigger);
    return;
  }
  const runSelection = target.closest("[data-select-run]");
  if (runSelection) {
    state.selectedRunId = runSelection.dataset.selectRun;
    state.selectedRunNodeId = null;
    state.runTab = "steps";
    setView("runs");
    return;
  }
  const runNode = target.closest("[data-select-run-node]");
  if (runNode) {
    state.selectedRunNodeId = runNode.dataset.selectRunNode;
    renderRuns();
    return;
  }
  const continueRun = target.closest("[data-continue-run]");
  if (continueRun) {
    continueStudioRun(continueRun.dataset.continueRun);
    return;
  }
  const cancelRun = target.closest("[data-cancel-run]");
  if (cancelRun) {
    cancelStudioRun(cancelRun.dataset.cancelRun);
    return;
  }
  const diagnose = target.closest("[data-diagnose-studio-run]");
  if (diagnose) {
    diagnoseStudioRun(diagnose.dataset.diagnoseStudioRun);
    return;
  }
  const propose = target.closest("[data-propose-studio-repair]");
  if (propose) {
    proposeStudioRepair(propose.dataset.proposeStudioRepair);
    return;
  }
  const approveRepair = target.closest("[data-approve-studio-repair]");
  if (approveRepair) {
    openStudioRepair(approveRepair.dataset.approveStudioRepair);
    return;
  }
  const proveRepair = target.closest("[data-prove-studio-repair]");
  if (proveRepair) {
    proveStudioRepair(
      proveRepair.dataset.proveStudioRepair,
      proveRepair.dataset.parentRun
    );
    return;
  }
  const runFlow = target.closest("[data-run-flow]");
  if (runFlow) {
    openRunDialog(runFlow.dataset.runFlow);
    return;
  }
  const approval = target.closest("[data-approval]");
  if (approval) {
    openApproval(approval.dataset.approval, approval.dataset.approved === "true");
    return;
  }
  const rerun = target.closest("[data-rerun]");
  if (rerun) {
    rerunStudio(rerun.dataset.rerun);
    return;
  }
  const runTab = target.closest("[data-run-tab]");
  if (runTab) {
    state.runTab = runTab.dataset.runTab;
    renderRuns();
    return;
  }
  const resourceTab = target.closest("[data-resource-tab]");
  if (resourceTab) {
    state.resourceTab = resourceTab.dataset.resourceTab;
    renderResources();
  }
});

byId("create-workspace").addEventListener("click", createWorkspace);
byId("new-workspace").addEventListener("click", createWorkspace);
byId("onboarding-config").addEventListener("click", () => setView("config"));
byId("open-config").addEventListener("click", () => setView("config"));
byId("dismiss-error").addEventListener("click", clearError);
byId("create-action").addEventListener("click", openActionDialog);
byId("action-filter").addEventListener("input", renderActions);
byId("action-kind").addEventListener("change", (event) => applyActionPreset(event.target.value));
byId("action-agent").addEventListener("change", () => {
  if (byId("action-kind").value === "ai") applyActionPreset("ai");
});
byId("action-form").addEventListener("submit", submitAction);
byId("create-flow").addEventListener("click", startNewFlow);
byId("trigger-type").addEventListener("change", updateTriggerEditor);
byId("trigger-form").addEventListener("submit", submitTrigger);
byId("start-run").addEventListener("click", () => openRunDialog());
byId("run-example").addEventListener("click", () => openRunDialog(studio().flows[0]?.id));
byId("run-flow").addEventListener("change", updateRunEditor);
byId("run-form").addEventListener("submit", submitRun);
byId("approval-form").addEventListener("submit", submitApproval);
byId("create-resource").addEventListener("click", openResourceDialog);
byId("prompt-form").addEventListener("submit", submitPrompt);
byId("skill-form").addEventListener("submit", submitSkill);
byId("agent-form").addEventListener("submit", submitAgent);
byId("studio-repair-form").addEventListener("submit", submitStudioRepair);
byId("config-form").addEventListener("submit", saveConfig);
byId("clear-api-key").addEventListener("click", clearConfig);

document.querySelectorAll("dialog").forEach((dialog) => {
  dialog.addEventListener("click", (event) => {
    if (event.target !== dialog) return;
    const bounds = dialog.getBoundingClientRect();
    const inside =
      event.clientX >= bounds.left &&
      event.clientX <= bounds.right &&
      event.clientY >= bounds.top &&
      event.clientY <= bounds.bottom;
    if (!inside) dialog.close();
  });
});

const initialView = window.location.hash.slice(1);
if (["overview", "actions", "flows", "runs", "resources", "docs", "config"].includes(initialView)) {
  state.view = initialView;
}

bootstrap().catch((error) => {
  byId("loading-state").hidden = true;
  byId("onboarding").hidden = false;
  showError(error instanceof Error ? error : new Error("Runtime bootstrap failed"));
});
