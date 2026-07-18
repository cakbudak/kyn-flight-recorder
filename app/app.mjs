import {
  ContractError,
  applyCommand,
  createInitialState,
  createSessionRecord,
  findNode,
  previewCommand,
  restoreSession,
  selectNode
} from "./core.mjs";

const FIXTURE_URL = "./data/demo-run.json";
const SESSION_KEY = "kyn.flight-recorder.session.v1";
const TRACE_SIZE_LIMIT = 1024 * 1024;
const VALID_VIEWS = new Set(["run", "replay", "audit", "about"]);
const STATUS_CLASSES = [
  "status-blocked",
  "status-completed",
  "status-healthy",
  "status-pending",
  "status-waiting"
];
const NODE_SYMBOLS = {
  trigger: "IN",
  agent_step: "AI",
  tool_call: "TL",
  approval_gate: "!",
  effect: "FX",
  terminal: "✓",
  queue_lease: "Q"
};
const DECISION_EVENT_PREFIXES = ["approval.", "command.", "tool.effect."];

let activeFixture = null;
let state = null;
let activeView = "run";
let replayFilter = "all";
let toastTimer = null;
let dialogReturnFocus = null;
let restoreDialogFocus = true;

function byId(id) {
  const element = document.getElementById(id);
  if (!element) {
    throw new Error(`Missing required element: #${id}`);
  }
  return element;
}

function setText(id, value) {
  byId(id).textContent = value ?? "";
}

function titleCase(value) {
  return String(value)
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function statusLabel(status) {
  return status === "waiting" ? "Awaiting" : titleCase(status);
}

function formatTime(value, includeMilliseconds = false) {
  if (!value) {
    return "Not observed";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "Invalid timestamp";
  }
  const fraction = includeMilliseconds ? `.${String(date.getUTCMilliseconds()).padStart(3, "0")}` : "";
  return `${String(date.getUTCHours()).padStart(2, "0")}:${String(date.getUTCMinutes()).padStart(2, "0")}:${String(date.getUTCSeconds()).padStart(2, "0")}${fraction}Z`;
}

function formatFieldValue(value) {
  if (value === null || value === undefined) {
    return "—";
  }
  if (typeof value === "boolean") {
    return value ? "true" : "false";
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
}

function makeDefinitionRow(label, value) {
  const row = document.createElement("div");
  const term = document.createElement("dt");
  const description = document.createElement("dd");
  term.textContent = label;
  description.textContent = formatFieldValue(value);
  row.append(term, description);
  return row;
}

function setStatusBadge(element, status) {
  element.classList.remove(...STATUS_CLASSES);
  element.classList.add(`status-${status}`);
  element.textContent = statusLabel(status);
}

function readStoredSession() {
  const serialized = sessionStorage.getItem(SESSION_KEY);
  if (!serialized) {
    return null;
  }
  try {
    return JSON.parse(serialized);
  } catch {
    sessionStorage.removeItem(SESSION_KEY);
    return null;
  }
}

function persistSession() {
  const record = createSessionRecord(state);
  if (record) {
    sessionStorage.setItem(SESSION_KEY, JSON.stringify(record));
  } else {
    sessionStorage.removeItem(SESSION_KEY);
  }
}

function hideSystemStates() {
  byId("loading-state").hidden = true;
  byId("empty-state").hidden = true;
  byId("error-state").hidden = true;
}

function hideAllViews() {
  document.querySelectorAll("[data-view-panel]").forEach((panel) => {
    panel.hidden = true;
  });
}

function showEmptyState() {
  hideAllViews();
  byId("loading-state").hidden = true;
  byId("error-state").hidden = true;
  byId("empty-state").hidden = false;
  byId("empty-state").querySelector("h1")?.focus?.();
}

function showError(error) {
  hideAllViews();
  byId("loading-state").hidden = true;
  byId("empty-state").hidden = true;
  byId("error-state").hidden = false;

  const summary = error instanceof Error ? error.message : "The trace could not be loaded.";
  setText("error-summary", summary);
  const list = byId("error-list");
  list.replaceChildren();
  const issues = error instanceof ContractError && Array.isArray(error.detail?.issues)
    ? error.detail.issues
    : [{ path: "$", message: "Input was not accepted." }];
  issues.slice(0, 12).forEach((issue) => {
    const item = document.createElement("li");
    item.textContent = `${issue.path}: ${issue.message}`;
    list.append(item);
  });
  if (issues.length > 12) {
    const item = document.createElement("li");
    item.textContent = `${issues.length - 12} additional issues omitted.`;
    list.append(item);
  }
  byId("error-state").querySelector("h1")?.focus();
}

function viewFromHash() {
  const candidate = location.hash.replace(/^#/, "");
  return VALID_VIEWS.has(candidate) ? candidate : "run";
}

function navigate(view, { updateHash = true, focus = false } = {}) {
  const nextView = VALID_VIEWS.has(view) ? view : "run";
  activeView = nextView;

  document.querySelectorAll("[data-view-panel]").forEach((panel) => {
    panel.hidden = panel.dataset.viewPanel !== nextView;
  });
  document.querySelectorAll("[data-view]").forEach((button) => {
    const selected = button.dataset.view === nextView;
    button.classList.toggle("is-active", selected);
    if (selected) {
      button.setAttribute("aria-current", "page");
    } else {
      button.removeAttribute("aria-current");
    }
  });

  if (updateHash && location.hash !== `#${nextView}`) {
    history.pushState(null, "", `#${nextView}`);
  }
  document.title = `${titleCase(nextView)} · Kyn.ist Flight Recorder`;
  if (focus) {
    byId("main-content").focus({ preventScroll: true });
  }
}

function createGraphNode(node) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "graph-node";
  button.dataset.nodeId = node.id;
  button.dataset.status = node.status;
  button.setAttribute("aria-pressed", String(node.id === state.selected_node_id));
  button.setAttribute(
    "aria-label",
    `${node.title}. ${statusLabel(node.status)}. ${node.subtitle}. Select to inspect evidence.`
  );
  if (node.id === state.selected_node_id) {
    button.classList.add("is-selected");
  }

  const symbol = document.createElement("span");
  symbol.className = "node-symbol";
  symbol.textContent = NODE_SYMBOLS[node.kind] ?? "·";
  symbol.setAttribute("aria-hidden", "true");

  const copy = document.createElement("span");
  copy.className = "node-copy";
  const title = document.createElement("strong");
  title.textContent = node.title;
  const subtitle = document.createElement("small");
  subtitle.textContent = node.subtitle;
  copy.append(title, subtitle);

  const source = document.createElement("span");
  source.className = "node-source";
  source.textContent = node.source;
  button.append(symbol, copy, source);

  button.addEventListener("click", () => selectGraphNode(node.id));
  button.addEventListener("keydown", handleGraphKeydown);
  return button;
}

function handleGraphKeydown(event) {
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
    return;
  }
  const buttons = [...byId("causal-graph").querySelectorAll(".graph-node")];
  const currentIndex = buttons.indexOf(event.currentTarget);
  if (currentIndex < 0) {
    return;
  }
  event.preventDefault();
  let nextIndex = currentIndex;
  if (event.key === "ArrowLeft") nextIndex = Math.max(0, currentIndex - 1);
  if (event.key === "ArrowRight") nextIndex = Math.min(buttons.length - 1, currentIndex + 1);
  if (event.key === "Home") nextIndex = 0;
  if (event.key === "End") nextIndex = buttons.length - 1;
  const nextNodeId = buttons[nextIndex].dataset.nodeId;
  selectGraphNode(nextNodeId);
  const renderedButton = [...byId("causal-graph").querySelectorAll(".graph-node")]
    .find((button) => button.dataset.nodeId === nextNodeId);
  renderedButton?.focus();
}

function createGraphEdge(edge) {
  const container = document.createElement("div");
  container.className = "graph-edge";
  container.dataset.status = edge?.status ?? "pending";
  container.setAttribute("aria-hidden", "true");
  const label = document.createElement("span");
  label.className = "graph-edge-label";
  label.textContent = edge?.relation ?? "next";
  container.append(label);
  return container;
}

function renderGraph() {
  const graph = byId("causal-graph");
  graph.replaceChildren();
  const mainNodes = state.nodes
    .filter((node) => node.lane === "main")
    .sort((left, right) => left.order - right.order);

  const lane = document.createElement("div");
  lane.className = "graph-main-lane";
  lane.setAttribute("role", "list");
  mainNodes.forEach((node, index) => {
    const wrapper = document.createElement("div");
    wrapper.setAttribute("role", "listitem");
    wrapper.append(createGraphNode(node));
    lane.append(wrapper);
    if (index < mainNodes.length - 1) {
      const nextNode = mainNodes[index + 1];
      const edge = state.edges.find((candidate) => candidate.from === node.id && candidate.to === nextNode.id);
      lane.append(createGraphEdge(edge));
    }
  });
  graph.append(lane);

  const queueNode = state.nodes.find((node) => node.lane === "guardrail");
  if (queueNode) {
    const guardrail = document.createElement("div");
    guardrail.className = "graph-guardrail";
    const queueButton = createGraphNode(queueNode);
    queueButton.classList.add("graph-node-guardrail");
    const explanation = document.createElement("div");
    explanation.className = "guardrail-explanation";
    const lead = document.createElement("strong");
    lead.textContent = "Retry ruled out";
    const detail = document.createElement("span");
    detail.textContent = "The live lease proves the worker is healthy; the approval boundary is the causal blocker.";
    explanation.append(lead, detail);
    guardrail.append(queueButton, explanation);
    graph.append(guardrail);
  }
}

function selectGraphNode(nodeId) {
  try {
    state = selectNode(state, nodeId);
    renderGraph();
    renderInspector();
  } catch (error) {
    showToast(error.message, "error");
  }
}

function renderInspector() {
  const node = findNode(state);
  if (!node) {
    return;
  }
  setText("inspector-title", node.title);
  setText("inspector-evidence", node.evidence);
  setText("inspector-source", node.source);
  setText("inspector-time", formatTime(node.observed_at, true));
  setText("inspector-icon", NODE_SYMBOLS[node.kind] ?? "·");
  setStatusBadge(byId("inspector-status"), node.status);

  const fields = byId("inspector-fields");
  fields.replaceChildren();
  Object.entries(node.fields ?? {}).forEach(([key, value]) => {
    fields.append(makeDefinitionRow(titleCase(key), value));
  });

  const action = byId("inspector-action");
  if (state.command.receipt) {
    action.textContent = "View command receipt";
  } else if (node.id === "queue") {
    action.textContent = "Inspect approval command";
  } else {
    action.textContent = "Preview legal command";
  }
}

function createEventItem(event) {
  const item = document.createElement("li");
  item.className = "event-item";
  const sequence = document.createElement("span");
  sequence.className = "event-sequence";
  sequence.textContent = String(event.sequence).padStart(2, "0");
  const copy = document.createElement("div");
  copy.className = "event-copy";
  const summary = document.createElement("strong");
  summary.textContent = event.summary;
  const meta = document.createElement("span");
  meta.textContent = `${formatTime(event.occurred_at)} · ${event.source}`;
  copy.append(summary, meta);
  item.append(sequence, copy);
  return item;
}

function renderRecentEvents() {
  const list = byId("recent-events");
  list.replaceChildren();
  state.events.slice(-3).forEach((event) => list.append(createEventItem(event)));
}

function eventMatchesFilter(event) {
  const isDecision = DECISION_EVENT_PREFIXES.some((prefix) => event.type.startsWith(prefix));
  if (replayFilter === "decisions") return isDecision;
  if (replayFilter === "runtime") return !isDecision;
  return true;
}

function renderReplay() {
  setText("replay-title", state.run.correlation_id);
  setText("replay-fixture-id", state.fixture.id);
  const visibleEvents = state.events.filter(eventMatchesFilter);
  setText("replay-event-count", `${visibleEvents.length} events`);
  const list = byId("replay-events");
  list.replaceChildren();

  visibleEvents.forEach((event) => {
    const row = document.createElement("li");
    row.className = "replay-row";
    row.dataset.status = event.status;

    const time = document.createElement("time");
    time.className = "replay-time";
    time.dateTime = event.occurred_at;
    time.textContent = formatTime(event.occurred_at, true);
    const dot = document.createElement("span");
    dot.className = "replay-dot";
    dot.setAttribute("aria-hidden", "true");
    const content = document.createElement("div");
    content.className = "replay-content";
    const type = document.createElement("strong");
    type.textContent = event.type;
    const summary = document.createElement("span");
    summary.textContent = event.summary;
    const source = document.createElement("code");
    source.textContent = event.source;
    content.append(type, summary, source);
    const sequence = document.createElement("span");
    sequence.className = "replay-sequence";
    sequence.textContent = `#${String(event.sequence).padStart(3, "0")}`;
    row.append(time, dot, content, sequence);
    list.append(row);
  });
}

function renderAudit() {
  const commandFields = byId("audit-command-fields");
  commandFields.replaceChildren(
    makeDefinitionRow("Command ID", state.intervention.command_id),
    makeDefinitionRow("Expected revision", state.intervention.expected_revision),
    makeDefinitionRow("Allowed state", state.intervention.allowed_from),
    makeDefinitionRow("Actor", state.intervention.actor),
    makeDefinitionRow("Scope", state.intervention.scope),
    makeDefinitionRow("Idempotency", state.intervention.idempotency_key)
  );

  const receipt = state.command.receipt;
  byId("receipt-empty").hidden = Boolean(receipt);
  byId("receipt-content").hidden = !receipt;
  const action = byId("audit-action");
  if (receipt) {
    action.textContent = "Receipt acknowledged";
    action.disabled = true;
    const receiptFields = byId("receipt-fields");
    receiptFields.replaceChildren(
      makeDefinitionRow("Receipt", receipt.receipt_id),
      makeDefinitionRow("Command", receipt.command_id),
      makeDefinitionRow("Run", receipt.run_id),
      makeDefinitionRow("Correlation", receipt.correlation_id),
      makeDefinitionRow("Actor", receipt.actor),
      makeDefinitionRow("Reason", receipt.reason),
      makeDefinitionRow("Applied", formatTime(receipt.applied_at, true)),
      makeDefinitionRow("Revision", `${receipt.from_revision} → ${receipt.to_revision}`),
      makeDefinitionRow("External effect", receipt.external_effect)
    );
  } else {
    action.textContent = "Review intervention";
    action.disabled = false;
  }

  setText("audit-ledger-count", `${state.events.length} rows`);
  const body = byId("audit-table-body");
  body.replaceChildren();
  state.events.forEach((event) => {
    const row = document.createElement("tr");
    const values = [
      String(event.sequence).padStart(3, "0"),
      formatTime(event.occurred_at, true),
      event.source,
      event.type
    ];
    values.forEach((value) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.append(cell);
    });
    const statusCell = document.createElement("td");
    const badge = document.createElement("span");
    badge.className = "status-badge";
    setStatusBadge(badge, event.status);
    statusCell.append(badge);
    row.append(statusCell);
    body.append(row);
  });
}

function renderDialogPreview() {
  const preview = previewCommand(state);
  const fields = byId("dialog-preview");
  fields.replaceChildren();
  preview.preview.forEach((item) => fields.append(makeDefinitionRow(item.label, item.value)));
  setText("dialog-actor", preview.actor);
  setText(
    "dialog-revision",
    `${preview.expected_revision} → ${state.intervention.resolution.new_revision}`
  );
}

function renderHeaderAndDiagnosis() {
  setText("header-agent", state.run.agent.name);
  setText("header-run-id", state.run.id);
  setText("header-goal", state.run.goal);
  setStatusBadge(byId("header-status"), state.run.status);
  setText("diagnosis-eyebrow", state.run.diagnosis.eyebrow);
  setText("diagnosis-title", state.run.diagnosis.title);
  setText("diagnosis-summary", state.run.diagnosis.summary);
  setText("diagnosis-next", state.run.diagnosis.next_action);
  setText("graph-correlation", state.run.correlation_id);
  byId("graph-correlation").classList.add("mono");

  const card = document.querySelector(".diagnosis-card");
  card?.classList.toggle("is-resolved", state.run.status === "completed");
  const action = byId("open-intervention");
  action.firstChild.textContent = state.command.receipt ? "View receipt " : "Review intervention ";
}

function renderCounts() {
  setText("run-count", "1");
  setText("audit-count", String(state.events.length));
}

function renderAll() {
  hideSystemStates();
  renderHeaderAndDiagnosis();
  renderGraph();
  renderInspector();
  renderRecentEvents();
  renderReplay();
  renderAudit();
  renderCounts();
  navigate(activeView, { updateHash: false });
  document.body.dataset.runStatus = state.run.status;
  performance.mark("kyn-render-complete");
}

function openIntervention(event) {
  if (state.command.receipt) {
    navigate("audit", { focus: true });
    const receiptPanel = byId("receipt-panel");
    receiptPanel.tabIndex = -1;
    receiptPanel.focus();
    return;
  }
  try {
    renderDialogPreview();
  } catch (error) {
    showToast(error.message, "error");
    return;
  }
  const dialog = byId("intervention-dialog");
  dialogReturnFocus = event?.currentTarget instanceof HTMLElement
    ? event.currentTarget
    : document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
  restoreDialogFocus = true;
  dialog.dataset.instant = event?.detail === 0 ? "true" : "false";
  byId("intervention-reason").value = "";
  byId("intervention-reason").removeAttribute("aria-invalid");
  byId("reason-error").hidden = true;
  byId("simulation-acknowledgement").checked = false;
  dialog.showModal();
  requestAnimationFrame(() => byId("intervention-reason").focus());
}

function closeIntervention({ restoreFocus = true } = {}) {
  const dialog = byId("intervention-dialog");
  if (dialog.open) {
    restoreDialogFocus = restoreFocus;
    dialog.close();
  }
}

function scheduleDialogFocusVisibility(candidate = document.activeElement) {
  const dialog = byId("intervention-dialog");
  const scrollBody = dialog.querySelector(".dialog-body");
  const target = candidate instanceof HTMLElement
    ? candidate.closest(".check-row") ?? candidate
    : null;
  if (
    !dialog.open ||
    !(scrollBody instanceof HTMLElement) ||
    !(target instanceof HTMLElement) ||
    !scrollBody.contains(target)
  ) return;

  let remainingFrames = 3;
  const alignTarget = () => {
    if (!dialog.open || !scrollBody.isConnected || !target.isConnected) return;
    const bodyRect = scrollBody.getBoundingClientRect();
    const targetRect = target.getBoundingClientRect();
    const inset = 16;
    if (targetRect.top < bodyRect.top + inset) {
      scrollBody.scrollTop -= bodyRect.top + inset - targetRect.top;
    } else if (targetRect.bottom > bodyRect.bottom - inset) {
      scrollBody.scrollTop += targetRect.bottom - (bodyRect.bottom - inset);
    }
    remainingFrames -= 1;
    if (remainingFrames > 0) requestAnimationFrame(alignTarget);
  };
  requestAnimationFrame(alignTarget);
}

function keepDialogFocusVisible(event) {
  scheduleDialogFocusVisibility(event.target);
}

function handleInterventionSubmit(event) {
  event.preventDefault();
  const reasonInput = byId("intervention-reason");
  const acknowledgement = byId("simulation-acknowledgement");
  const reason = reasonInput.value.trim();
  const reasonError = byId("reason-error");

  if (reason.length < 12 || reason.length > 280) {
    reasonInput.setAttribute("aria-invalid", "true");
    reasonError.textContent = "Enter a reason between 12 and 280 characters.";
    reasonError.hidden = false;
    reasonInput.focus();
    return;
  }
  if (!acknowledgement.checked) {
    acknowledgement.focus();
    acknowledgement.reportValidity();
    return;
  }

  try {
    const result = applyCommand(state, {
      actor: state.intervention.actor,
      reason,
      acknowledged: true
    });
    state = result.state;
    persistSession();
    closeIntervention({ restoreFocus: false });
    renderAll();
    navigate("audit", { focus: true });
    showToast(result.duplicate ? "Existing receipt returned; no duplicate applied." : "Command acknowledged. Revision advanced to 8.");
    const receiptPanel = byId("receipt-panel");
    receiptPanel.tabIndex = -1;
    receiptPanel.focus({ preventScroll: true });
  } catch (error) {
    const message = error instanceof Error ? error.message : "The command failed closed.";
    reasonError.textContent = message;
    reasonError.hidden = false;
    showToast(message, "error");
  }
}

function resetCurrentTrace({ announce = true } = {}) {
  if (!activeFixture) {
    return;
  }
  state = createInitialState(activeFixture);
  sessionStorage.removeItem(SESSION_KEY);
  replayFilter = "all";
  document.querySelectorAll("[data-filter]").forEach((button) => {
    const selected = button.dataset.filter === "all";
    button.classList.toggle("is-active", selected);
    button.setAttribute("aria-pressed", String(selected));
  });
  renderAll();
  navigate("run", { focus: true });
  if (announce) {
    showToast("Demo reset to the signed fixture state.");
  }
}

async function fetchDefaultFixture() {
  const response = await fetch(FIXTURE_URL, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Fixture request failed with HTTP ${response.status}.`);
  }
  return response.json();
}

async function restoreDefaultFixture({ announce = false } = {}) {
  const fixture = await fetchDefaultFixture();
  activeFixture = fixture;
  state = createInitialState(fixture);
  sessionStorage.removeItem(SESSION_KEY);
  renderAll();
  navigate("run", { focus: true });
  if (announce) {
    showToast("Judge fixture restored.");
  }
}

async function handleTraceFile(event) {
  const input = event.currentTarget;
  const file = input.files?.[0];
  input.value = "";
  if (!file) {
    return;
  }
  if (file.size > TRACE_SIZE_LIMIT) {
    showError(new ContractError("TRACE_TOO_LARGE", "Trace exceeds the 1 MiB local import limit."));
    return;
  }
  try {
    const text = await file.text();
    const fixture = JSON.parse(text);
    const nextState = createInitialState(fixture);
    activeFixture = fixture;
    state = nextState;
    sessionStorage.removeItem(SESSION_KEY);
    renderAll();
    navigate("run", { focus: true });
    showToast(`Loaded ${file.name} locally.`);
  } catch (error) {
    showError(error);
  }
}

async function copyCorrelation() {
  try {
    await navigator.clipboard.writeText(state.run.correlation_id);
    showToast("Correlation ID copied.");
  } catch {
    showToast(`Correlation: ${state.run.correlation_id}`);
  }
}

function showToast(message, kind = "success") {
  const region = byId("toast-region");
  region.replaceChildren();
  if (toastTimer) {
    clearTimeout(toastTimer);
  }
  const toast = document.createElement("div");
  toast.className = "toast";
  const mark = document.createElement("span");
  mark.className = "toast-mark";
  mark.textContent = kind === "error" ? "!" : "✓";
  const copy = document.createElement("span");
  copy.textContent = message;
  toast.append(mark, copy);
  region.append(toast);
  toastTimer = setTimeout(() => {
    toast.classList.add("is-leaving");
    setTimeout(() => toast.remove(), 190);
  }, 3200);
}

function bindEvents() {
  document.querySelectorAll("[data-view]").forEach((button) => {
    button.addEventListener("click", () => navigate(button.dataset.view, { focus: true }));
  });
  document.querySelectorAll("[data-go-view]").forEach((button) => {
    button.addEventListener("click", () => navigate(button.dataset.goView, { focus: true }));
  });
  document.querySelectorAll("[data-filter]").forEach((button) => {
    button.addEventListener("click", () => {
      replayFilter = button.dataset.filter;
      document.querySelectorAll("[data-filter]").forEach((candidate) => {
        const selected = candidate === button;
        candidate.classList.toggle("is-active", selected);
        candidate.setAttribute("aria-pressed", String(selected));
      });
      renderReplay();
    });
  });

  byId("trace-file").addEventListener("change", handleTraceFile);
  byId("load-trace").addEventListener("click", () => byId("trace-file").click());
  byId("reset-demo").addEventListener("click", () => resetCurrentTrace());
  byId("restore-fixture-empty").addEventListener("click", () => restoreDefaultFixture({ announce: true }));
  byId("restore-fixture-error").addEventListener("click", () => restoreDefaultFixture({ announce: true }));
  byId("copy-correlation").addEventListener("click", copyCorrelation);
  byId("open-intervention").addEventListener("click", openIntervention);
  byId("inspector-action").addEventListener("click", openIntervention);
  byId("audit-action").addEventListener("click", openIntervention);
  byId("close-dialog").addEventListener("click", closeIntervention);
  byId("cancel-intervention").addEventListener("click", closeIntervention);
  byId("intervention-form").addEventListener("submit", handleInterventionSubmit);
  byId("intervention-dialog").addEventListener("click", (event) => {
    if (event.target === event.currentTarget) {
      closeIntervention();
    }
  });
  byId("intervention-dialog").addEventListener("cancel", () => {
    restoreDialogFocus = true;
  });
  byId("intervention-dialog").addEventListener("close", () => {
    const target = dialogReturnFocus;
    const shouldRestore = restoreDialogFocus;
    dialogReturnFocus = null;
    restoreDialogFocus = true;
    if (shouldRestore && target?.isConnected) {
      requestAnimationFrame(() => target.focus());
    }
  });
  byId("intervention-dialog")
    .querySelector(".dialog-body")
    ?.addEventListener("focusin", keepDialogFocusVisible);
  window.addEventListener("resize", () => scheduleDialogFocusVisibility());
  window.visualViewport?.addEventListener("resize", () => scheduleDialogFocusVisibility());
  byId("intervention-reason").addEventListener("input", (event) => {
    event.currentTarget.removeAttribute("aria-invalid");
    byId("reason-error").hidden = true;
  });
  window.addEventListener("hashchange", () => navigate(viewFromHash(), { updateHash: false, focus: true }));
}

async function bootstrap() {
  bindEvents();
  activeView = viewFromHash();
  const mode = new URLSearchParams(location.search).get("mode");
  if (mode === "empty") {
    showEmptyState();
    return;
  }

  try {
    const fixture = await fetchDefaultFixture();
    if (mode === "error") {
      fixture.run.status = "unknown";
    }
    activeFixture = fixture;
    const stored = readStoredSession();
    try {
      state = restoreSession(fixture, stored);
    } catch (error) {
      sessionStorage.removeItem(SESSION_KEY);
      state = createInitialState(fixture);
      console.warn("Discarded incompatible local demo state.", error);
    }
    renderAll();
  } catch (error) {
    showError(error);
  }
}

bootstrap();
