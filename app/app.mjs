import {
  PHASE_ORDER,
  childRunFor,
  phaseFor,
  rootRunFor,
  selectedRunFor
} from "./state.mjs";

const state = {
  health: null,
  snapshot: null,
  busy: false,
  selectedRunId: null,
  selectedEventId: null,
  lastError: null
};

class ApiError extends Error {
  constructor(status, payload) {
    const error = payload?.error ?? {};
    super(error.message ?? `Request failed with status ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.code = error.code ?? "request_failed";
    this.detail = error.detail ?? null;
  }
}

function byId(id) {
  const element = document.getElementById(id);
  if (!element) throw new Error(`Missing required element: #${id}`);
  return element;
}

function setText(id, value) {
  byId(id).textContent = value ?? "";
}

function short(value, length = 12) {
  const text = String(value ?? "");
  return text.length > length ? `${text.slice(0, length)}…` : text;
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
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function make(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

async function api(path, { method = "GET", body } = {}) {
  const options = {
    method,
    credentials: "same-origin",
    headers: { Accept: "application/json" }
  };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  let payload = {};
  try {
    payload = await response.json();
  } catch {
    throw new ApiError(response.status, {
      error: { code: "invalid_response", message: "The runtime returned an invalid response." }
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
  let payload = {};
  try {
    payload = await response.json();
  } catch {
    throw new ApiError(response.status, {
      error: { code: "invalid_response", message: "The runtime health response was invalid." }
    });
  }
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

function showError(error) {
  state.lastError = error;
  setText("error-code", error.code ? titleCase(error.code) : "Request failed");
  setText("error-message", error.message ?? "The operation could not be completed.");
  byId("error-panel").hidden = false;
  byId("error-panel").scrollIntoView({ behavior: "auto", block: "nearest" });
  announce(`Error: ${error.message}`);
}

function clearError() {
  state.lastError = null;
  byId("error-panel").hidden = true;
}

function rootRun() {
  return rootRunFor(state.snapshot);
}

function childRun(root = rootRun()) {
  return childRunFor(state.snapshot, root);
}

function currentFlow() {
  return state.snapshot?.flows?.[0] ?? null;
}

function currentPhase() {
  return phaseFor(state.snapshot);
}

function setBusy(busy, label = "") {
  state.busy = busy;
  document.body.dataset.busy = String(busy);
  byId("primary-action").disabled = busy;
  byId("new-workspace").disabled = busy;
  byId("create-lab").disabled = busy;
  byId("main-content").setAttribute("aria-busy", String(busy));
  if (busy && label) {
    setText("primary-action", label);
    announce(label);
  }
}

async function operation(label, work, successMessage) {
  if (state.busy) return;
  clearError();
  setBusy(true, label);
  try {
    const result = await work();
    await refreshWorkspace();
    if (result?.id && state.snapshot.runs.some((run) => run.id === result.id)) {
      state.selectedRunId = result.id;
      state.selectedEventId = null;
    }
    render();
    toast(successMessage);
    announce(successMessage);
  } catch (error) {
    showError(error instanceof Error ? error : new Error("Unknown runtime error"));
  } finally {
    setBusy(false);
    renderPrimaryAction();
  }
}

async function createWorkspace() {
  await operation(
    "Creating versioned lab…",
    async () => {
      const bootstrap = await api("/api/v1/workspaces", { method: "POST", body: {} });
      state.snapshot = bootstrap.snapshot;
      state.selectedRunId = null;
      state.selectedEventId = null;
    },
    "Isolated agent lab created"
  );
}

async function refreshWorkspace() {
  state.snapshot = await api("/api/v1/workspace");
  const root = rootRun();
  const child = childRun(root);
  if (!state.selectedRunId) state.selectedRunId = child?.id ?? root?.id ?? null;
}

async function bootstrap() {
  const [healthResult, workspaceResult] = await Promise.allSettled([
    runtimeHealth(),
    api("/api/v1/workspace")
  ]);
  if (healthResult.status === "fulfilled") state.health = healthResult.value;
  if (workspaceResult.status === "fulfilled") state.snapshot = workspaceResult.value;
  else if (!(workspaceResult.reason instanceof ApiError && workspaceResult.reason.status === 401)) {
    state.lastError = workspaceResult.reason;
  }
  byId("loading-state").hidden = true;
  render();
  if (state.lastError) showError(state.lastError);
}

function renderHealth() {
  const container = byId("runtime-health");
  const light = container.querySelector(".status-light");
  const label = container.querySelector("span:last-child");
  if (!light || !label) return;
  light.classList.remove("is-loading", "is-ready", "is-warning");
  if (!state.health) {
    light.classList.add("is-warning");
    label.textContent = "Runtime unavailable";
  } else if (state.health.openai_configured) {
    light.classList.add("is-ready");
    label.textContent = "SQLite ready · OpenAI ready";
  } else {
    light.classList.add("is-warning");
    label.textContent = "SQLite ready · API key missing";
  }
}

function render() {
  renderHealth();
  const hasWorkspace = Boolean(state.snapshot);
  byId("onboarding").hidden = hasWorkspace;
  byId("runtime").hidden = !hasWorkspace;
  byId("new-workspace").hidden = !hasWorkspace;
  if (!hasWorkspace) return;

  const flow = currentFlow();
  if (!flow) {
    showError(new Error("The workspace has no flow."));
    return;
  }
  const version = flow.version;
  const executor = state.snapshot.agents.find((agent) => agent.version.role === "executor");
  setText("flow-name", flow.name);
  setText("flow-version", `Flow v${version.version} · rev ${flow.revision}`);
  setText("flow-goal", version.request.goal);
  setText("workspace-id", short(state.snapshot.workspace.id, 18));
  setText("flow-model", executor?.version.model ?? "unknown");
  setText("artifact-name", version.request.artifact);
  setText("requested-environment", titleCase(version.request.environment));

  const allowed = byId("allowed-environments");
  allowed.replaceChildren();
  version.policy.allowed_environments.forEach((environment) => {
    allowed.append(make("span", "token", environment));
  });
  const mismatch = !version.policy.allowed_environments.includes(version.request.environment);
  setText("expected-outcome", mismatch ? "Policy denial" : "Sandbox success");
  byId("expected-outcome").className = mismatch ? "danger-text" : "success-text";

  renderPhase();
  renderPrimaryAction();
  renderActivity();
  renderDiagnosisAndRepair();
  renderResources();
  renderComparison();
  renderLedger();
}

function renderPhase() {
  const phase = currentPhase();
  const displayedPhase = phase === "failed" ? "blocked" : phase;
  const currentIndex = PHASE_ORDER.indexOf(displayedPhase);
  document.querySelectorAll("[data-phase]").forEach((item) => {
    const itemIndex = PHASE_ORDER.indexOf(item.dataset.phase);
    item.classList.toggle("is-current", itemIndex === currentIndex);
    item.classList.toggle("is-complete", itemIndex < currentIndex);
    if (itemIndex === currentIndex) item.setAttribute("aria-current", "step");
    else item.removeAttribute("aria-current");
  });
  const captions = {
    ready: "Ready to execute",
    blocked: "Failure recorded · diagnosis next",
    diagnosed: "Cause proven · repair next",
    repair: "Proposal ready · human fence next",
    applied: "Flow v2 created · rerun next",
    proven: "Outcome changed · proof complete",
    failed: "Runtime failure recorded · inspect evidence"
  };
  setText("phase-caption", captions[phase]);
}

function primaryActionContract() {
  const phase = currentPhase();
  const root = rootRun();
  const flow = currentFlow();
  if (phase === "ready") {
    return {
      label: "Run real agent flow",
      explainer: "Three Responses calls and two strict local tools will create the first evidence set.",
      busy: "Agent executing via OpenAI…",
      success: "Run blocked with an authoritative policy-denial receipt",
      action: () => api(`/api/v1/flows/${flow.id}/runs`, { method: "POST", body: {} })
    };
  }
  if (phase === "blocked") {
    return {
      label: "Diagnose from evidence",
      explainer: "The forensic agent can cite only the policy and denial events owned by this run.",
      busy: "Forensic agent analyzing evidence…",
      success: "Evidence-grounded diagnosis accepted",
      action: () => api(`/api/v1/runs/${root.id}/diagnoses`, { method: "POST", body: {} })
    };
  }
  if (phase === "diagnosed") {
    return {
      label: "Propose bounded repair",
      explainer: "The repairer may change one allow-listed manifest path and cannot apply it.",
      busy: "Repair agent proposing one patch…",
      success: "Bounded repair proposal created",
      action: () => api(`/api/v1/diagnoses/${root.diagnosis.id}/repairs`, { method: "POST", body: {} })
    };
  }
  if (phase === "repair") {
    return {
      label: "Review human approval fence",
      explainer: "Proposal hash, expected revision, actor, reason, and acknowledgement are all required.",
      action: () => openApprovalDialog()
    };
  }
  if (phase === "applied") {
    return {
      label: "Rerun against flow v2",
      explainer: "A new child run preserves the failed run and proves whether the repair changed the effect.",
      busy: "Child agent rerun executing…",
      success: "Child rerun completed with a real sandbox effect",
      action: () => api(`/api/v1/runs/${root.id}/rerun`, { method: "POST", body: {} })
    };
  }
  if (phase === "failed") {
    return {
      label: "Inspect failed run evidence",
      explainer: "The provider or runtime failed before the expected policy outcome; this run is terminal and remains inspectable.",
      action: () => document.querySelector("#ledger-section")?.scrollIntoView({ behavior: "auto" })
    };
  }
  return {
    label: "Closed loop proven",
    explainer: "The blocked v1 run and completed v2 child run remain independently inspectable.",
    action: () => document.querySelector("#runs-section")?.scrollIntoView({ behavior: "auto" })
  };
}

function renderPrimaryAction() {
  if (!state.snapshot) return;
  const contract = primaryActionContract();
  const button = byId("primary-action");
  button.replaceChildren(document.createTextNode(state.busy ? "Working…" : contract.label));
  if (!state.busy) button.append(make("span", "", "→"));
  button.disabled = state.busy;
  setText("action-explainer", contract.explainer);

  const root = rootRun();
  const child = childRun(root);
  const status = child?.status ?? root?.status ?? "ready";
  const badge = byId("run-status");
  badge.className = `status-badge status-${status}`;
  badge.textContent = titleCase(status);
}

async function runPrimaryAction() {
  const contract = primaryActionContract();
  if (!contract.busy) {
    contract.action();
    return;
  }
  await operation(contract.busy, contract.action, contract.success);
}

function renderActivity() {
  const root = rootRun();
  const child = childRun(root);
  const active = child ?? root;
  setText("activity-count", `${active?.events.length ?? 0} events`);
  if (!active) {
    setText("activity-title", "No run yet");
    setText("activity-summary", "The flow, three agents, three prompts, and three skills are immutable and pinned.");
  } else if (child?.status === "completed") {
    setText("activity-title", "Repair proven by child run");
    setText("activity-summary", "The same sandbox tool created one durable effect under flow v2.");
  } else if (root.status === "blocked") {
    setText("activity-title", "Tool boundary stopped the effect");
    setText("activity-summary", "Production was requested, but the pinned v1 allow-list contained only staging.");
  } else {
    setText("activity-title", titleCase(active.status));
    setText("activity-summary", active.error_code ?? "The agent run reached an authoritative terminal state.");
  }

  const receiptFor = (name) => active?.tool_receipts.find((receipt) => receipt.tool_name === name);
  const modelDone = Boolean(active?.model_calls.length);
  const inspect = receiptFor("inspect_release_policy");
  const stage = receiptFor("stage_release");
  const terminal = Boolean(active?.finished_at);
  const activity = {
    model: modelDone ? "recorded" : "waiting",
    inspect: inspect?.outcome ?? "waiting",
    stage: stage?.outcome ?? "waiting",
    terminal: terminal ? active.status : "waiting"
  };
  document.querySelectorAll("[data-activity]").forEach((item) => {
    const value = activity[item.dataset.activity];
    item.dataset.state = value;
    const small = item.querySelector("small");
    if (small) small.textContent = value;
  });
}

function renderDiagnosisAndRepair() {
  const root = rootRun();
  const diagnosis = root?.diagnosis;
  const repair = root?.repair;
  byId("diagnosis-panel").hidden = !diagnosis;
  byId("repair-panel").hidden = !repair;
  if (diagnosis) {
    setText("diagnosis-summary", diagnosis.summary);
    setText("diagnosis-retry", diagnosis.why_not_retry);
    setText("diagnosis-class", titleCase(diagnosis.fault_class));
    setText("diagnosis-confidence", titleCase(diagnosis.confidence));
    setText("diagnosis-citations", `${diagnosis.evidence_event_ids.length} owned events`);
  }
  if (repair) {
    setText("repair-summary", repair.summary);
    setText("repair-path", repair.patch[0].path);
    setText("repair-revision", repair.expected_flow_revision);
    setText("repair-risk", titleCase(repair.risk));
    setText("patch-add", `+ ${JSON.stringify(repair.patch[0].value)}`);
  }
}

function resourceCard(resource, kind) {
  const version = resource.version;
  const card = make("article", "resource-card");
  const heading = make("div", "resource-card-heading");
  const title = make("strong", "", resource.name);
  const versionLabel = make("span", "version-chip", `v${version.version}`);
  heading.append(title, versionLabel);
  card.append(heading);

  if (kind === "prompt") {
    card.append(make("p", "", `${version.variables.length} declared variables`));
  } else if (kind === "skill") {
    card.append(make("p", "", version.allowed_tools.length ? version.allowed_tools.join(" · ") : "No tool authority"));
  } else {
    card.append(make("p", "", `${titleCase(version.role)} · ${version.model}`));
  }
  const fingerprint = make("code", "fingerprint", short(version.fingerprint, 18));
  fingerprint.title = version.fingerprint;
  card.append(fingerprint);
  return card;
}

function renderResources() {
  const groups = [
    ["prompts", "prompt-list", "prompt-count", "prompt"],
    ["skills", "skill-list", "skill-count", "skill"],
    ["agents", "agent-list", "agent-count", "agent"]
  ];
  groups.forEach(([key, listId, countId, kind]) => {
    const resources = state.snapshot[key];
    setText(countId, resources.length);
    const list = byId(listId);
    list.replaceChildren(...resources.map((resource) => resourceCard(resource, kind)));
  });
}

function renderRunCard(container, run, label) {
  container.replaceChildren();
  if (!run) {
    const empty = make("div", "run-card-empty");
    empty.append(make("span", "", label === "Before" ? "01" : "02"));
    empty.append(make("p", "", label === "Before" ? "Failed run will remain here after the repair." : "Child rerun will prove the changed outcome."));
    container.append(empty);
    return;
  }
  const header = make("header", "run-card-header");
  const copy = make("div", "");
  copy.append(make("small", "", label), make("strong", "mono", short(run.id, 20)));
  const badge = make("span", `status-badge status-${run.status}`, titleCase(run.status));
  header.append(copy, badge);
  const metrics = make("dl", "run-metrics");
  [
    ["Flow", `v${run.flow_version}`],
    ["Events", run.events.length],
    ["Model calls", run.model_calls.length],
    ["Tool effects", run.sandbox_effects.length]
  ].forEach(([term, value]) => {
    const item = make("div", "");
    item.append(make("dt", "", term), make("dd", "", String(value)));
    metrics.append(item);
  });
  const foot = make("footer", "run-card-foot");
  foot.append(make("span", "", label === "Before" ? "Preserved evidence" : "Linked child run"));
  foot.append(make("code", "", short(run.flow_fingerprint, 16)));
  container.append(header, metrics, foot);
}

function renderComparison() {
  const root = rootRun();
  const child = childRun(root);
  renderRunCard(byId("before-run-card"), root, "Before repair");
  renderRunCard(byId("after-run-card"), child, "After repair");
  if (child?.status === "completed") {
    setText("comparison-caption", "Same correlation · new immutable version · outcome changed");
  } else if (root) {
    setText("comparison-caption", "The failed v1 run will never be rewritten.");
  } else {
    setText("comparison-caption", "Run the flow to create the first evidence set.");
  }
}

function selectedRun() {
  return selectedRunFor(state.snapshot, state.selectedRunId);
}

function renderLedger() {
  const root = rootRun();
  const child = childRun(root);
  const switcher = byId("run-switcher");
  switcher.replaceChildren();
  [root, child].filter(Boolean).forEach((run, index) => {
    const button = make("button", "ledger-switch", index === 0 ? "Failed run" : "Child rerun");
    button.type = "button";
    button.dataset.runId = run.id;
    button.classList.toggle("is-active", selectedRun()?.id === run.id);
    button.setAttribute("aria-pressed", String(selectedRun()?.id === run.id));
    button.addEventListener("click", () => {
      state.selectedRunId = run.id;
      state.selectedEventId = null;
      renderLedger();
    });
    switcher.append(button);
  });

  const run = selectedRun();
  const list = byId("event-list");
  list.replaceChildren();
  if (!run) {
    list.append(make("li", "event-empty", "No event exists until a real run begins."));
    renderEvidence(null);
    return;
  }
  if (!state.selectedRunId) state.selectedRunId = run.id;
  if (!state.selectedEventId && run.events.length) state.selectedEventId = run.events.at(-1).id;
  run.events.forEach((event) => {
    const item = make("li", "event-row");
    const button = make("button", "", "");
    button.type = "button";
    button.dataset.eventId = event.id;
    button.classList.toggle("is-selected", event.id === state.selectedEventId);
    button.setAttribute("aria-pressed", String(event.id === state.selectedEventId));
    button.setAttribute("aria-label", `Event ${event.sequence}: ${titleCase(event.type)}`);
    button.append(
      make("span", "mono event-sequence", String(event.sequence).padStart(2, "0")),
      make("span", "event-type", titleCase(event.type)),
      make("span", "event-actor", event.actor_id ? short(event.actor_id, 16) : titleCase(event.actor_type)),
      make("code", "event-hash", short(event.event_hash, 12))
    );
    button.addEventListener("click", () => {
      state.selectedEventId = event.id;
      renderLedger();
    });
    item.append(button);
    list.append(item);
  });
  renderEvidence(run.events.find((event) => event.id === state.selectedEventId) ?? run.events.at(-1));
}

function renderEvidence(event) {
  if (!event) {
    setText("evidence-title", "Choose an event");
    setText("evidence-id", "—");
    setText("evidence-prev", "—");
    setText("evidence-hash", "—");
    setText("evidence-payload", "Select a ledger row to inspect its committed payload.");
    return;
  }
  setText("evidence-title", titleCase(event.type));
  setText("evidence-id", event.id);
  setText("evidence-prev", event.prev_hash);
  setText("evidence-hash", event.event_hash);
  setText("evidence-payload", JSON.stringify(event.payload, null, 2));
}

function openApprovalDialog() {
  const repair = rootRun()?.repair;
  if (!repair) return;
  setText("dialog-revision", `${repair.expected_flow_revision} → ${repair.expected_flow_revision + 1}`);
  setText("dialog-proposal-hash", repair.proposal_hash);
  byId("approval-acknowledged").checked = false;
  byId("approval-dialog").showModal();
  byId("approval-actor").focus();
}

async function submitApproval(event) {
  event.preventDefault();
  const repair = rootRun()?.repair;
  if (!repair) return;
  const actor = byId("approval-actor").value;
  const reason = byId("approval-reason").value;
  const acknowledged = byId("approval-acknowledged").checked;
  if (!byId("approval-form").reportValidity()) return;
  byId("approval-dialog").close();
  await operation(
    "Applying revision-fenced repair…",
    () => api(`/api/v1/repairs/${repair.id}/apply`, {
      method: "POST",
      body: {
        proposal_hash: repair.proposal_hash,
        expected_flow_revision: repair.expected_flow_revision,
        actor,
        reason,
        acknowledged
      }
    }),
    "Human approval created immutable flow v2"
  );
}

byId("create-lab").addEventListener("click", createWorkspace);
byId("new-workspace").addEventListener("click", createWorkspace);
byId("primary-action").addEventListener("click", runPrimaryAction);
byId("dismiss-error").addEventListener("click", clearError);
byId("approval-form").addEventListener("submit", submitApproval);
byId("close-dialog").addEventListener("click", () => byId("approval-dialog").close());
byId("cancel-approval").addEventListener("click", () => byId("approval-dialog").close());
byId("approval-dialog").addEventListener("click", (event) => {
  if (event.target === byId("approval-dialog")) byId("approval-dialog").close();
});

bootstrap().catch((error) => {
  byId("loading-state").hidden = true;
  byId("onboarding").hidden = false;
  showError(error instanceof Error ? error : new Error("Runtime bootstrap failed"));
});
