import {
  PHASE_ORDER,
  childRunFor,
  phaseFor,
  rootRunFor
} from "./state.mjs";

const OPENAI_KEY_SLOT = "kyn.openai.api-key.v1";

const state = {
  health: null,
  snapshot: null,
  busy: false,
  view: "overview",
  selectedFlowId: null,
  selectedRunId: null,
  runTab: "steps",
  resourceTab: "agents",
  lastError: null
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
  sandbox: {
    name: "Append sandbox record",
    slug: "append-sandbox-record",
    description: "Create one idempotent record inside the workspace sandbox.",
    input: objectSchema({ record: { type: "string", maxLength: 4000 } }, ["record"]),
    output: objectSchema(
      { effect_id: { type: "string" }, collection: { type: "string" } },
      ["effect_id", "collection"]
    ),
    config: { operation: "append_record", collection: "custom_records" }
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
    if (!button.closest("[hidden]")) button.disabled = value;
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
    renderRepairLab();
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
  else if (state.view === "repair") renderRepairLab();
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
  if (!flows.some((flow) => flow.id === state.selectedFlowId)) {
    state.selectedFlowId = flows[0]?.id ?? null;
  }
  const list = byId("flow-list");
  list.replaceChildren();
  flows.forEach((flow) => {
    const button = make("button", "selection-button");
    button.dataset.selectFlow = flow.id;
    button.classList.toggle("is-selected", flow.id === state.selectedFlowId);
    button.append(
      make("strong", "", flow.name),
      make("small", "", `${flow.version.nodes.length} nodes · ${flow.version.requires_model ? "AI" : "deterministic"}`),
      make("code", "", `v${flow.version.version} / ${short(flow.version.fingerprint, 13)}`)
    );
    list.append(button);
  });
  const flow = flows.find((item) => item.id === state.selectedFlowId);
  renderFlowInspector(flow);
}

function renderFlowInspector(flow) {
  const inspector = byId("flow-inspector");
  inspector.replaceChildren();
  if (!flow) {
    inspector.append(emptyState("Create an Action, then compose your first Flow."));
    return;
  }
  const head = make("header", "inspector-head");
  const copy = make("div");
  copy.append(make("p", "section-kicker", `Flow · ${flow.slug}`), make("h2", "", flow.name), make("p", "", flow.description));
  const actions = make("div", "inspector-actions");
  const runButton = make("button", "button button-primary", "Start Run");
  runButton.dataset.runFlow = flow.id;
  actions.append(runButton);
  head.append(copy, actions);
  const facts = make("dl", "inspector-facts");
  facts.append(
    definitionFact("Version", `v${flow.version.version}`),
    definitionFact("Revision", flow.revision),
    definitionFact("Nodes", flow.version.nodes.length),
    definitionFact("Model path", flow.version.requires_model ? "Yes · BYOK" : "No")
  );
  const canvas = make("div", "flow-canvas");
  const nodes = make("div", "flow-nodes");
  flow.version.nodes.forEach((node, index) => {
    if (index > 0) nodes.append(make("i", "flow-arrow", "→"));
    nodes.append(flowNodeCard(flow, node));
  });
  canvas.append(nodes);
  const routes = make("div", "route-list");
  if (flow.version.routes.length) {
    flow.version.routes.forEach((route) => {
      routes.append(make("span", "route-chip", `${route.from} · ${route.outcome} → ${route.to}`));
    });
  } else {
    routes.append(make("span", "route-chip", "terminal on success"));
  }
  const fingerprint = make("pre", "json-block", jsonText({
    flow_version_id: flow.version.id,
    fingerprint: flow.version.fingerprint,
    pinned_resources: flow.version.pinned_resources
  }));
  inspector.append(head, facts, canvas, routes, fingerprint);
}

function flowNodeCard(flow, node) {
  const card = make("article", "flow-node");
  card.classList.toggle("is-start", node.id === flow.version.start_node_id);
  let label = "Unknown version";
  let kind = node.type;
  if (node.type === "action") {
    const action = actionForVersion(node.version_id);
    label = action?.name ?? label;
    kind = action?.version.kind ?? kind;
  } else {
    const agent = agentForVersion(node.version_id);
    label = agent?.name ?? label;
    kind = "ai";
  }
  const chip = make("span", `node-kind node-${kind}`, node.type === "agent" ? "AG" : kind.slice(0, 2).toUpperCase());
  card.append(
    chip,
    make("strong", "", node.id),
    make("small", "", label),
    make("code", "", short(node.version_id, 22))
  );
  return card;
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

function renderRepairLab() {
  const flow = state.snapshot.flows[0];
  const root = rootRunFor(state.snapshot);
  const child = childRunFor(state.snapshot, root);
  const phase = phaseFor(state.snapshot);
  const displayed = phase === "failed" ? "blocked" : phase;
  const index = PHASE_ORDER.indexOf(displayed);
  document.querySelectorAll("[data-repair-phase]").forEach((item) => {
    const itemIndex = PHASE_ORDER.indexOf(item.dataset.repairPhase);
    item.classList.toggle("is-current", itemIndex === index);
    item.classList.toggle("is-complete", itemIndex < index);
  });
  setText("repair-phase-badge", titleCase(phase));
  byId("repair-phase-badge").className = `status-badge status-${displayed}`;
  setText("repair-flow-version", `v${flow?.version.version ?? 1}`);
  renderRepairManifest(flow);
  const contract = repairActionContract(phase, flow, root);
  setText("repair-primary-action", contract.label);
  setText("repair-action-help", contract.help);
  byId("repair-primary-action").disabled = state.busy || !contract.action;
  renderRepairEvidence(root, child);
}

function renderRepairManifest(flow) {
  const wrapper = byId("repair-manifest");
  wrapper.replaceChildren();
  if (!flow) return;
  const list = make("dl");
  [
    ["Goal", flow.version.request.goal],
    ["Artifact", flow.version.request.artifact],
    ["Requested", flow.version.request.environment],
    ["Allowed", flow.version.policy.allowed_environments.join(", ")]
  ].forEach(([term, value]) => {
    const row = make("div");
    row.append(make("dt", "", term), make("dd", "", value));
    list.append(row);
  });
  wrapper.append(list);
}

function repairActionContract(phase, flow, root) {
  if (phase === "ready") return {
    label: "Run real agent flow",
    help: "The executor uses OpenAI Responses and strict local tools. The policy denial is authoritative.",
    action: () => api(`/api/v1/flows/${flow.id}/runs`, { method: "POST", body: {}, modelAction: true })
  };
  if (phase === "blocked") return {
    label: "Diagnose from owned evidence",
    help: "The diagnostician may cite only events owned by this terminal Run.",
    action: () => api(`/api/v1/runs/${root.id}/diagnoses`, { method: "POST", body: {}, modelAction: true })
  };
  if (phase === "diagnosed") return {
    label: "Propose bounded repair",
    help: "The repairer can propose one allow-listed manifest operation and cannot apply it.",
    action: () => api(`/api/v1/diagnoses/${root.diagnosis.id}/repairs`, { method: "POST", body: {}, modelAction: true })
  };
  if (phase === "repair") return {
    label: "Open human revision fence",
    help: "Actor, reason, acknowledgement, proposal hash, and expected revision are required.",
    action: () => byId("repair-approval-dialog").showModal()
  };
  if (phase === "applied") return {
    label: "Rerun as linked child",
    help: "The failed parent stays immutable while the child pins Flow v2.",
    action: () => api(`/api/v1/runs/${root.id}/rerun`, { method: "POST", body: {}, modelAction: true })
  };
  if (phase === "failed") return {
    label: "Provider/runtime failure preserved",
    help: "This terminal state is not mislabeled as a policy failure. Start a fresh workspace to retry.",
    action: null
  };
  return {
    label: "Closed loop proven",
    help: "The blocked parent and completed child remain linked and independently inspectable.",
    action: null
  };
}

function renderRepairEvidence(root, child) {
  const wrapper = byId("repair-evidence");
  wrapper.replaceChildren();
  const grid = make("div", "repair-proof-grid");
  grid.append(repairRunCard("Before repair", root), repairRunCard("After repair", child));
  if (root?.diagnosis) {
    const diagnosis = make("article", "diagnosis-card");
    diagnosis.append(
      make("p", "section-kicker", "Evidence-grounded diagnosis"),
      make("h3", "", root.diagnosis.summary),
      make("p", "", `${titleCase(root.diagnosis.fault_class)} · ${root.diagnosis.evidence_event_ids.length} owned event citations · ${titleCase(root.diagnosis.confidence)} confidence`)
    );
    grid.append(diagnosis);
  }
  if (root?.repair) {
    const repair = make("article", "repair-card");
    repair.append(
      make("p", "section-kicker", `Repair · ${titleCase(root.repair.status)}`),
      make("h3", "", root.repair.summary),
      make("p", "", `${root.repair.patch[0].op} ${root.repair.patch[0].path} · expected revision ${root.repair.expected_flow_revision}`)
    );
    grid.append(repair);
  }
  wrapper.append(grid);
}

function repairRunCard(label, run) {
  const card = make("article", "repair-proof-card");
  card.append(make("p", "section-kicker", label));
  if (!run) {
    card.append(make("h3", "", "Not executed"), make("p", "", "The immutable Run will appear here."));
    return card;
  }
  card.append(
    make("h3", "", titleCase(run.status)),
    make("p", "", `${short(run.id, 24)} · ${run.events.length} events · ${run.model_calls.length} model calls · ${run.sandbox_effects.length} effects`)
  );
  return card;
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

function openFlowDialog() {
  const rows = byId("flow-node-rows");
  rows.replaceChildren();
  byId("flow-route-rows").replaceChildren();
  const preferred = studio().actions.find((action) => action.version.kind === "template") ?? studio().actions[0];
  if (!preferred) {
    setView("actions");
    showError(new Error("Create at least one Action before composing a Flow."));
    return;
  }
  const name = `Custom automation ${studio().flows.length + 1}`;
  byId("flow-name").value = name;
  byId("flow-slug").value = uniqueSlug(`custom-automation-${studio().flows.length + 1}`, studio().flows.map((item) => item.slug));
  byId("flow-description").value = "A user-defined versioned Action Flow.";
  byId("flow-input-schema").value = jsonText(preferred.version.input_schema);
  byId("flow-start-node").value = "start";
  addFlowNode({ id: "start", type: "action", versionId: preferred.version.id });
  byId("flow-dialog").showModal();
  byId("flow-name").focus();
}

function addFlowNode({ id = `step-${byId("flow-node-rows").children.length + 1}`, type = "action", versionId = null } = {}) {
  const row = make("div", "editor-row node-editor-row");
  const idInput = make("input");
  idInput.required = true;
  idInput.maxLength = 64;
  idInput.placeholder = "node-id";
  idInput.value = id;
  idInput.dataset.nodeField = "id";
  const typeSelect = make("select");
  typeSelect.dataset.nodeField = "type";
  ["action", "agent"].forEach((value) => {
    const option = make("option", "", titleCase(value));
    option.value = value;
    typeSelect.append(option);
  });
  typeSelect.value = type;
  const versionSelect = make("select");
  versionSelect.dataset.nodeField = "version";
  fillVersionOptions(versionSelect, type, versionId);
  const mapping = make("textarea", "code-input");
  mapping.dataset.nodeField = "mapping";
  mapping.value = jsonText(defaultMapping(type, versionSelect.value));
  mapping.setAttribute("aria-label", "Node input mapping JSON");
  const remove = make("button", "icon-button", "×");
  remove.setAttribute("aria-label", "Remove node");
  remove.dataset.removeRow = "true";
  typeSelect.addEventListener("change", () => {
    fillVersionOptions(versionSelect, typeSelect.value, null);
    mapping.value = jsonText(defaultMapping(typeSelect.value, versionSelect.value));
  });
  versionSelect.addEventListener("change", () => {
    mapping.value = jsonText(defaultMapping(typeSelect.value, versionSelect.value));
  });
  row.append(idInput, typeSelect, versionSelect, mapping, remove);
  byId("flow-node-rows").append(row);
}

function fillVersionOptions(select, type, selected) {
  select.replaceChildren();
  const resources = type === "action" ? studio().actions : state.snapshot.agents;
  resources.forEach((resource) => {
    const option = make("option", "", `${resource.name} · v${resource.version.version}`);
    option.value = resource.version.id;
    select.append(option);
  });
  if (selected) select.value = selected;
}

function defaultMapping(type, versionId) {
  let fields = [];
  if (type === "action") {
    const action = actionForVersion(versionId);
    fields = Object.keys(action?.version.input_schema.properties ?? {});
  } else {
    const agent = agentForVersion(versionId);
    const prompt = state.snapshot.prompts.find((item) => item.version.id === agent?.version.prompt_version_id);
    fields = prompt?.version.variables ?? [];
  }
  return Object.fromEntries(fields.map((field) => [field, { source: "input", path: field }]));
}

function addFlowRoute() {
  const row = make("div", "editor-row route-editor-row");
  const source = make("input");
  source.required = true;
  source.placeholder = "from node";
  source.dataset.routeField = "from";
  const outcome = make("select");
  outcome.dataset.routeField = "outcome";
  ["success", "true", "false", "approved", "rejected"].forEach((value) => {
    const option = make("option", "", value);
    option.value = value;
    outcome.append(option);
  });
  const target = make("input");
  target.required = true;
  target.placeholder = "to node";
  target.dataset.routeField = "to";
  const remove = make("button", "icon-button", "×");
  remove.setAttribute("aria-label", "Remove route");
  remove.dataset.removeRow = "true";
  row.append(source, outcome, target, remove);
  byId("flow-route-rows").append(row);
}

async function submitFlow(event) {
  event.preventDefault();
  if (!event.currentTarget.reportValidity()) return;
  const nodes = [...byId("flow-node-rows").children].map((row, index) => ({
    id: row.querySelector("[data-node-field='id']").value,
    type: row.querySelector("[data-node-field='type']").value,
    version_id: row.querySelector("[data-node-field='version']").value,
    input_mapping: parseJsonValue(
      row.querySelector("[data-node-field='mapping']").value,
      `Node ${index + 1} input mapping`
    )
  }));
  const routes = [...byId("flow-route-rows").children].map((row) => ({
    from: row.querySelector("[data-route-field='from']").value,
    outcome: row.querySelector("[data-route-field='outcome']").value,
    to: row.querySelector("[data-route-field='to']").value
  }));
  const body = {
    name: byId("flow-name").value,
    slug: byId("flow-slug").value,
    description: byId("flow-description").value,
    input_schema: parseJsonField("flow-input-schema", "Flow input JSON Schema"),
    start_node_id: byId("flow-start-node").value,
    nodes,
    routes
  };
  byId("flow-dialog").close();
  await operation(
    "Validating and pinning Flow graph…",
    () => api("/api/v1/studio/flows", { method: "POST", body }),
    `${body.name} Flow v1 created`,
    (created) => {
      state.selectedFlowId = created.id;
      state.view = "flows";
    }
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
    "Executing pinned Flow…",
    () => api(`/api/v1/studio/flows/${flow.id}/runs`, {
      method: "POST",
      body: { input, idempotency_key: crypto.randomUUID() },
      modelAction: flow.version.requires_model
    }),
    "Authoritative Run created",
    (run) => {
      state.selectedRunId = run.id;
      state.view = "runs";
      state.runTab = "steps";
    }
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

async function runRepairPrimary() {
  const flow = state.snapshot.flows[0];
  const root = rootRunFor(state.snapshot);
  const contract = repairActionContract(phaseFor(state.snapshot), flow, root);
  if (!contract.action) return;
  if (phaseFor(state.snapshot) === "repair") {
    contract.action();
    return;
  }
  await operation("Advancing Repair Lab…", contract.action, "Repair Lab advanced from authoritative evidence");
}

async function submitRepairApproval(event) {
  event.preventDefault();
  if (!event.currentTarget.reportValidity()) return;
  const repair = rootRunFor(state.snapshot)?.repair;
  if (!repair) return;
  const body = {
    proposal_hash: repair.proposal_hash,
    expected_flow_revision: repair.expected_flow_revision,
    actor: byId("repair-approval-actor").value,
    reason: byId("repair-approval-reason").value,
    acknowledged: byId("repair-approval-ack").checked
  };
  byId("repair-approval-dialog").close();
  await operation(
    "Applying revision-fenced repair…",
    () => api(`/api/v1/repairs/${repair.id}/apply`, { method: "POST", body }),
    "Repair applied as immutable Flow v2"
  );
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
    state.selectedFlowId = flowSelection.dataset.selectFlow;
    renderFlows();
    return;
  }
  const runSelection = target.closest("[data-select-run]");
  if (runSelection) {
    state.selectedRunId = runSelection.dataset.selectRun;
    state.runTab = "steps";
    setView("runs");
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
byId("create-flow").addEventListener("click", openFlowDialog);
byId("add-flow-node").addEventListener("click", () => addFlowNode());
byId("add-flow-route").addEventListener("click", addFlowRoute);
byId("flow-form").addEventListener("submit", submitFlow);
byId("start-run").addEventListener("click", () => openRunDialog());
byId("run-example").addEventListener("click", () => openRunDialog(studio().flows[0]?.id));
byId("run-flow").addEventListener("change", updateRunEditor);
byId("run-form").addEventListener("submit", submitRun);
byId("approval-form").addEventListener("submit", submitApproval);
byId("create-resource").addEventListener("click", openResourceDialog);
byId("prompt-form").addEventListener("submit", submitPrompt);
byId("skill-form").addEventListener("submit", submitSkill);
byId("agent-form").addEventListener("submit", submitAgent);
byId("repair-primary-action").addEventListener("click", runRepairPrimary);
byId("repair-approval-form").addEventListener("submit", submitRepairApproval);
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
if (["overview", "actions", "flows", "runs", "resources", "repair", "docs", "config"].includes(initialView)) {
  state.view = initialView;
}

bootstrap().catch((error) => {
  byId("loading-state").hidden = true;
  byId("onboarding").hidden = false;
  showError(error instanceof Error ? error : new Error("Runtime bootstrap failed"));
});
