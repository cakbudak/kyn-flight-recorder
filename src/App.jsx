import React, { useCallback, useEffect, useMemo, useState } from "react";
import { api, browserKey, health } from "./api.js";
import { Icon } from "./icons.jsx";
import { shortId, topLevelRuns } from "./lib.js";
import { Badge, Button, IconButton, Spinner, ThemeToggle } from "./components/ui.jsx";
import FlowStudio from "./components/FlowStudio.jsx";
import ResourceWorkbench from "./components/ResourceWorkbench.jsx";
import RunsWorkbench, { BrakeRefusal } from "./components/RunsWorkbench.jsx";
import Overview from "./components/Overview.jsx";
import Comparisons from "./components/Comparisons.jsx";
import CapabilityForge from "./components/CapabilityForge.jsx";
import Documentation from "./components/Documentation.jsx";
import Settings from "./components/Settings.jsx";
import ContextWorkbench from "./components/ContextWorkbench.jsx";
import BoardRooms from "./components/BoardRooms.jsx";

const NAVIGATION = [
  { id: "overview", label: "Overview", icon: "home" },
  { id: "studio", label: "Flow Studio", icon: "flow" },
  { id: "boardrooms", label: "BoardRooms", icon: "boardroom", count: (snapshot) => (snapshot.studio.boardrooms ?? []).length },
  { id: "context", label: "Context & Memory", icon: "context", count: (snapshot) => (snapshot.studio.knowledge_sources ?? []).length },
  { id: "actions", label: "Actions", icon: "action", count: (snapshot) => snapshot.studio.actions.length },
  { id: "agents", label: "Agents", icon: "agent", count: (snapshot) => snapshot.agents.length },
  { id: "prompts", label: "Prompts", icon: "prompt", count: (snapshot) => snapshot.prompts.length },
  { id: "skills", label: "Skills", icon: "skill", count: (snapshot) => snapshot.skills.length },
  { id: "runs", label: "Runs", icon: "run", count: (snapshot) => topLevelRuns(snapshot.studio.runs).length },
  { id: "forge", label: "Capability Forge", icon: "skill", count: (snapshot) => (snapshot.studio.skill_candidates ?? []).length },
  { id: "comparisons", label: "Comparisons", icon: "compare", count: (snapshot) => (snapshot.studio.comparisons ?? []).length },
  { id: "docs", label: "Documentation", icon: "docs" }
];

export default function App() {
  const [snapshot, setSnapshot] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [runtimeHealthy, setRuntimeHealthy] = useState(null);
  const [error, setError] = useState(null);
  const [toast, setToast] = useState("");
  const [view, setViewState] = useState(() => {
    const requested = location.hash.replace("#", "");
    return NAVIGATION.some((item) => item.id === requested) || requested === "settings" ? requested : "studio";
  });
  const [keyRevision, setKeyRevision] = useState(0);
  const [focusRunId, setFocusRunId] = useState(null);
  const [focusFlowId, setFocusFlowId] = useState(null);
  const [comparisonFlowId, setComparisonFlowId] = useState(null);
  const [boardroomContext, setBoardroomContext] = useState("");
  const [contextComposition, setContextComposition] = useState(null);

  const setView = useCallback((next) => {
    setViewState(next);
    history.replaceState(null, "", `#${next}`);
    requestAnimationFrame(() => document.getElementById("main-content")?.focus({ preventScroll: true }));
  }, []);

  const refresh = useCallback(async () => {
    const data = await api("/api/v1/workspace");
    setSnapshot(data);
    return data;
  }, []);

  useEffect(() => {
    let active = true;
    Promise.allSettled([health(), api("/api/v1/workspace")]).then(([healthResult, workspaceResult]) => {
      if (!active) return;
      setRuntimeHealthy(healthResult.status === "fulfilled");
      if (workspaceResult.status === "fulfilled") setSnapshot(workspaceResult.value);
      setLoading(false);
    });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!toast) return undefined;
    const timer = setTimeout(() => setToast(""), 3600);
    return () => clearTimeout(timer);
  }, [toast]);

  const mutate = useCallback(async (
    work,
    { success = "Saved", refreshAfter = true, preserveError = false } = {}
  ) => {
    setBusy(true);
    if (!preserveError) setError(null);
    try {
      const result = await work();
      if (refreshAfter) await refresh();
      if (success) setToast(success);
      return result;
    } catch (caught) {
      setToast("");
      setError({ code: caught.code ?? "operation_failed", message: caught.message, detail: caught.detail });
      if (caught.code === "openai_key_required") setView("settings");
      return null;
    } finally {
      setBusy(false);
    }
  }, [refresh, setView]);

  const createWorkspace = useCallback(() => mutate(
    () => api("/api/v1/workspaces", { method: "POST", body: {} }),
    { success: "Isolated Studio workspace ready", refreshAfter: false }
  ).then((result) => {
    if (result?.snapshot) {
      setSnapshot(result.snapshot);
      setView("studio");
    }
  }), [mutate, setView]);

  // A cited Run is evidence, so citing one anywhere must be able to open it.
  const focusRun = useCallback((runId) => {
    setFocusRunId(runId);
    setView("runs");
  }, [setView]);

  // Comparing is a Flow Studio verb but a Comparisons surface. Carrying the
  // Flow across means the operator never has to re-pick what they were
  // already looking at.
  const startComparison = useCallback((flowId) => {
    setComparisonFlowId(flowId);
    setView("comparisons");
  }, [setView]);

  const openFlow = useCallback((flowId) => {
    setFocusFlowId(flowId);
    setView("studio");
  }, [setView]);

  const useContextInBoardRoom = useCallback((context) => {
    setBoardroomContext(context);
    setView("boardrooms");
  }, [setView]);

  const composeContextFlow = useCallback((composition) => {
    setContextComposition(composition);
    setView("studio");
  }, [setView]);

  const onKeyChanged = useCallback(() => setKeyRevision((value) => value + 1), []);
  const keyConfigured = useMemo(() => Boolean(browserKey()), [keyRevision]);

  if (loading) {
    return <div className="boot-screen"><Brand /><Spinner label="Opening the local control plane…" /></div>;
  }

  if (!snapshot) {
    return (
      <Onboarding
        busy={busy}
        error={error}
        runtimeHealthy={runtimeHealthy}
        onCreate={createWorkspace}
      />
    );
  }

  const shared = { snapshot, refresh, mutate, busy, setView, focusRun, startComparison, openFlow };
  let content;
  if (view === "studio") content = <FlowStudio {...shared} focusFlowId={focusFlowId} onFocusFlowHandled={() => setFocusFlowId(null)} contextComposition={contextComposition} onContextCompositionHandled={() => setContextComposition(null)} />;
  else if (view === "boardrooms") content = <BoardRooms {...shared} initialContext={boardroomContext} onContextConsumed={() => setBoardroomContext("")} />;
  else if (view === "context") content = <ContextWorkbench {...shared} onUseInBoardRoom={useContextInBoardRoom} onComposeFlow={composeContextFlow} />;
  else if (view === "comparisons") content = (
    <Comparisons
      {...shared}
      comparisonFlowId={comparisonFlowId}
      onComparisonRequestHandled={() => setComparisonFlowId(null)}
    />
  );
  else if (view === "forge") content = <CapabilityForge {...shared} />;
  else if (view === "actions") content = <ResourceWorkbench {...shared} kind="actions" />;
  else if (view === "agents") content = <ResourceWorkbench {...shared} kind="agents" />;
  else if (view === "prompts") content = <ResourceWorkbench {...shared} kind="prompts" />;
  else if (view === "skills") content = <ResourceWorkbench {...shared} kind="skills" />;
  else if (view === "runs") content = <RunsWorkbench {...shared} focusRunId={focusRunId} />;
  else if (view === "docs") content = <Documentation setView={setView} />;
  else if (view === "settings") content = (
    <Settings
      {...shared}
      keyConfigured={keyConfigured}
      onKeyChanged={onKeyChanged}
      onNewWorkspace={createWorkspace}
    />
  );
  else content = <Overview {...shared} />;

  return (
    <div className={`app-shell view-${view}`} aria-busy={busy}>
      <a className="skip-link" href="#main-content">Skip to workspace</a>
      <Topbar
        runtimeHealthy={runtimeHealthy}
        workspaceId={snapshot.workspace?.id ?? snapshot.workspace_id}
        keyConfigured={keyConfigured}
        onSettings={() => setView("settings")}
      />
      <div className="shell-body">
        <Sidebar snapshot={snapshot} view={view} setView={setView} />
        <main id="main-content" className="workspace-main" tabIndex="-1">
          {error?.code === "brake_engaged" ? (
            <BrakeRefusal detail={error.detail} onDismiss={() => setError(null)} />
          ) : error ? (
            <div className="error-banner" role="alert">
              <Icon name="warning" size={18} />
              <div><strong>{error.code.replaceAll("_", " ")}</strong><span>{error.message}</span></div>
              <IconButton icon="close" label="Dismiss error" onClick={() => setError(null)} />
            </div>
          ) : null}
          {content}
        </main>
      </div>
      {busy ? <div className="operation-indicator"><Spinner label="Committing operation…" /></div> : null}
      {toast ? <div className="toast" role="status"><Icon name="check" size={16} />{toast}</div> : null}
    </div>
  );
}

function Brand() {
  return (
    <div className="brand" aria-label="Kyn.ist Agent Studio">
      <span className="brand-mark" aria-hidden="true">K</span>
      <span><strong>Kyn.ist</strong><small>Agent Studio</small></span>
    </div>
  );
}

function Topbar({ runtimeHealthy, workspaceId, keyConfigured, onSettings }) {
  return (
    <header className="topbar">
      <Brand />
      <div className="topbar-context">
        <Badge tone={runtimeHealthy ? "success" : "danger"} dot>
          {runtimeHealthy ? "Runtime online" : "Runtime unavailable"}
        </Badge>
        <span className="workspace-id"><Icon name="lock" size={14} />{shortId(workspaceId ?? "workspace")}</span>
      </div>
      <div className="topbar-actions">
        <Badge tone="ai">OpenAI Build Week</Badge>
        <ThemeToggle />
        <Button tone="quiet" icon="key" onClick={onSettings}>
          {keyConfigured ? "Key in this tab" : "Configure OpenAI"}
        </Button>
        <a className="icon-button" href="https://github.com/cakbudak/kyn-agent-studio" target="_blank" rel="noreferrer" aria-label="Open source repository">
          <Icon name="external" size={17} />
        </a>
      </div>
    </header>
  );
}

function Sidebar({ snapshot, view, setView }) {
  return (
    <aside className="sidebar" aria-label="Workspace navigation">
      <nav>
        <p className="nav-section">Build and operate</p>
        {NAVIGATION.map((item) => (
          <button
            key={item.id}
            type="button"
            className={`nav-item ${view === item.id ? "is-active" : ""}`}
            onClick={() => setView(item.id)}
            aria-current={view === item.id ? "page" : undefined}
          >
            <Icon name={item.icon} size={18} />
            <span>{item.label}</span>
            {item.count ? <b>{item.count(snapshot)}</b> : null}
          </button>
        ))}
      </nav>
      <div className="sidebar-footer">
        <button type="button" className={`nav-item ${view === "settings" ? "is-active" : ""}`} onClick={() => setView("settings")}>
          <Icon name="settings" size={18} /><span>Settings</span>
        </button>
        <div className="public-boundary">
          <span>K</span>
          <p><strong>Public projection</strong>Flat SQLite · bounded tools · no private Kyn layers</p>
        </div>
      </div>
    </aside>
  );
}

function Onboarding({ busy, error, runtimeHealthy, onCreate }) {
  return (
    <div className="onboarding-shell">
      <header className="onboarding-top"><Brand /><Badge tone={runtimeHealthy ? "success" : "danger"} dot>{runtimeHealthy ? "Runtime ready" : "Runtime offline"}</Badge></header>
      <main className="onboarding-main">
        <section className="onboarding-copy">
          <p className="eyebrow">Executable public projection · OpenAI Build Week</p>
          <h1>Build agent workflows.<br /><em>Operate their truth.</em></h1>
          <p className="onboarding-lede">
            Define typed Actions, Agents, Prompts and Skills. Compose reusable Flows on a real graph.
            Observe every pinned Run, approve effects, diagnose failures, publish a bounded successor,
            and prove the repair without rewriting history.
          </p>
          <div className="onboarding-actions">
            <Button tone="primary" icon="flow" onClick={onCreate} disabled={busy || !runtimeHealthy}>Open an isolated Studio</Button>
            <a className="button button-default" href="https://github.com/cakbudak/kyn-agent-studio" target="_blank" rel="noreferrer"><Icon name="external" size={16} /><span>Inspect source</span></a>
          </div>
          <p className="fine-print"><Icon name="lock" size={14} />No account. Fresh 24-hour workspace. Your OpenAI key stays in this browser tab.</p>
          {error ? <div className="inline-error" role="alert">{error.message}</div> : null}
        </section>
        <SystemPreview />
      </main>
      <footer className="onboarding-footer"><span>Kyn owns orchestration, state, authority and evidence.</span><span>OpenAI is the model transport.</span></footer>
    </div>
  );
}

function SystemPreview() {
  const nodes = [
    ["AI", "Analyze", "Agent + Prompt + Skills", "ai"],
    ["ROUTE", "Decide", "Named outcomes", "warning"],
    ["H", "Authorize", "Durable pause", "success"],
    ["FLOW", "Reuse", "Linked child Run", "neutral"]
  ];
  return (
    <section className="system-preview" aria-label="Kyn Agent Studio system preview">
      <header><span>customer-triage.flow</span><Badge tone="success">Published v7</Badge></header>
      <div className="preview-canvas">
        <svg viewBox="0 0 540 280" aria-hidden="true"><path d="M143 70 C210 70 188 133 255 133M356 133 C420 133 390 207 453 207"/><path d="M143 70 C215 70 196 225 255 225"/></svg>
        {nodes.map(([kind, name, detail, tone], index) => (
          <article key={name} className={`preview-node preview-node-${index + 1}`}>
            <Badge tone={tone}>{kind}</Badge><strong>{name}</strong><small>{detail}</small><i /><i />
          </article>
        ))}
      </div>
      <dl className="preview-facts">
        <div><dt>Definitions</dt><dd>Immutable + pinned</dd></div>
        <div><dt>Execution</dt><dd>Typed + bounded</dd></div>
        <div><dt>Evidence</dt><dd>Hash-linked</dd></div>
        <div><dt>Maintenance</dt><dd>Forward-only proof</dd></div>
      </dl>
    </section>
  );
}
