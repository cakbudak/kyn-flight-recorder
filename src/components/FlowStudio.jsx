import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useThemeTokens } from "../theme.js";
import {
  Background,
  ConnectionLineType,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Panel,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useEdgesState,
  useNodesState,
  useReactFlow
} from "@xyflow/react";
import { api, commandId } from "../api.js";
import { Icon } from "../icons.jsx";
import {
  EMPTY_SCHEMA,
  FAN_OUT_OUTCOMES,
  SUCCESS_ERROR,
  clone,
  defaultMapping,
  exampleForSchema,
  graphNodeLabel,
  layoutGraph,
  nodeOutcomes,
  parseJson,
  resourceForNode,
  slugDraft,
  slugify,
  titleCase,
  uniqueNodeId,
  versionForNode
} from "../lib.js";
import {
  Badge,
  Button,
  CitedRuns,
  DefinitionList,
  EmptyState,
  Field,
  IconButton,
  JsonField,
  Modal
} from "./ui.jsx";

const NODE_TYPES = { kynNode: KynNode, fanOutNode: FanOutNode };
const EDGE_DEFAULTS = {
  type: "smoothstep",
  markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16 },
  style: { strokeWidth: 1.8 }
};
const MAX_ACCEPTANCE_CRITERIA = 8;
const JUDGE_PROMPT_VARIABLES = new Set([
  "acceptance_criteria", "run_evidence", "candidate_json", "evidence_json"
]);
const EVIDENCE_KINDS = [
  { id: "step", label: "Completed Step", hint: "The node completed rather than merely starting." },
  { id: "receipt", label: "Successful Action receipt", hint: "A pinned Action invocation succeeded." },
  { id: "effect", label: "Committed effect", hint: "A writing Action minted an idempotent effect." },
  { id: "approval", label: "Human approval", hint: "A human explicitly approved at this node." }
];

// Graph chrome is set in JSX rather than CSS, so it reads the same tokens
// the stylesheet uses instead of carrying a second colour list.
const GRAPH_TOKENS = [
  "graph-dot", "minimap-mask", "accent-text",
  "tone-success-solid", "tone-ai-solid", "tone-warning-solid",
  "tone-danger-solid", "tone-cyan-solid", "tone-blue-solid"
];

export default function FlowStudio(props) {
  return <ReactFlowProvider><FlowStudioInner {...props} /></ReactFlowProvider>;
}

function FlowStudioInner({ snapshot, mutate, busy, setView, focusRun, startComparison, focusFlowId, onFocusFlowHandled }) {
  const graph = useThemeTokens(GRAPH_TOKENS);
  const flows = snapshot.studio.flows;
  const [selectedFlowId, setSelectedFlowId] = useState(flows[0]?.id ?? null);
  const selectedFlow = flows.find((flow) => flow.id === selectedFlowId) ?? flows[0] ?? null;
  const [draft, setDraft] = useState(() => flowDraft(selectedFlow));
  const [dirty, setDirty] = useState(false);
  const [selectedNodeId, setSelectedNodeId] = useState(null);
  const [paletteQuery, setPaletteQuery] = useState("");
  const [paletteSection, setPaletteSection] = useState("actions");
  const [showRun, setShowRun] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(true);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [advisory, setAdvisory] = useState(null);
  const history = useRef({ past: [], future: [] });
  const { screenToFlowPosition, fitView } = useReactFlow();
  const [canvasNodes, setCanvasNodes, onNodesChangeBase] = useNodesState([]);
  const [canvasEdges, setCanvasEdges, onEdgesChangeBase] = useEdgesState([]);

  const fitGraph = useCallback((padding, duration) => fitView({
    padding,
    duration,
    // A phone cannot make a multi-node graph readable and show every node at
    // once. Prefer legible nodes with deliberate pan/zoom over a deceptive
    // thumbnail that technically fits but cannot be inspected or connected.
    minZoom: window.matchMedia("(max-width: 760px)").matches ? 0.72 : 0.18,
    maxZoom: 1.8
  }), [fitView]);

  useEffect(() => {
    if (selectedFlowId === null) return;
    if (flows.some((flow) => flow.id === selectedFlowId)) return;
    const fallback = flows[0] ?? null;
    setSelectedFlowId(fallback?.id ?? null);
    setDraft(flowDraft(fallback));
  }, [flows, selectedFlowId]);

  useEffect(() => {
    const hydrated = hydrateNodes(snapshot, draft);
    setCanvasNodes(hydrated);
    setCanvasEdges(hydrateEdges(draft));
  }, [snapshot, draft.nodes, draft.routes, draft.start_node_id, setCanvasEdges, setCanvasNodes]);

  useEffect(() => {
    const timer = setTimeout(() => fitGraph(0.2, 220), 180);
    return () => clearTimeout(timer);
  }, [fitGraph, inspectorOpen, paletteOpen]);

  const replaceDraft = useCallback((next, { record = true } = {}) => {
    setDraft((current) => {
      const material = typeof next === "function" ? next(clone(current)) : next;
      if (record) {
        history.current.past = [...history.current.past.slice(-39), clone(current)];
        history.current.future = [];
      }
      return material;
    });
    setDirty(true);
  }, []);

  const undo = useCallback(() => {
    const prior = history.current.past.at(-1);
    if (!prior) return;
    setDraft((current) => {
      history.current.future = [clone(current), ...history.current.future.slice(0, 39)];
      history.current.past = history.current.past.slice(0, -1);
      return prior;
    });
    setDirty(true);
  }, []);

  const redo = useCallback(() => {
    const next = history.current.future[0];
    if (!next) return;
    setDraft((current) => {
      history.current.past = [...history.current.past.slice(-39), clone(current)];
      history.current.future = history.current.future.slice(1);
      return next;
    });
    setDirty(true);
  }, []);

  useEffect(() => {
    const onKey = (event) => {
      const target = event.target;
      if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement) return;
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "z") {
        event.preventDefault();
        if (event.shiftKey) redo(); else undo();
      }
      if ((event.key === "Delete" || event.key === "Backspace") && selectedNodeId) {
        event.preventDefault();
        removeNode(selectedNodeId, replaceDraft, setSelectedNodeId);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [redo, replaceDraft, selectedNodeId, undo]);

  const loadFlow = useCallback((flowId) => {
    const flow = flows.find((item) => item.id === flowId) ?? null;
    setSelectedFlowId(flow?.id ?? null);
    setDraft(flowDraft(flow));
    setSelectedNodeId(null);
    setDirty(false);
    setAdvisory(null);
    history.current = { past: [], future: [] };
    requestAnimationFrame(() => fitGraph(0.22, 280));
  }, [fitGraph, flows]);

  useEffect(() => {
    if (!focusFlowId || !flows.some((flow) => flow.id === focusFlowId)) return;
    loadFlow(focusFlowId);
    onFocusFlowHandled?.();
  }, [flows, focusFlowId, loadFlow, onFocusFlowHandled]);

  const createNew = useCallback(() => {
    setSelectedFlowId(null);
    setDraft(flowDraft(null));
    setSelectedNodeId(null);
    setDirty(false);
    setAdvisory(null);
    history.current = { past: [], future: [] };
  }, []);

  const palette = useMemo(() => paletteResources(snapshot, selectedFlowId), [snapshot, selectedFlowId]);
  const filteredPalette = useMemo(
    () => palette[paletteSection].filter((item) => {
      const material = `${item.name} ${item.description ?? ""} ${item.kind ?? ""}`.toLowerCase();
      return material.includes(paletteQuery.toLowerCase());
    }),
    [palette, paletteQuery, paletteSection]
  );

  const addNode = useCallback((material, position = null) => {
    const version = material.version;
    const nodeId = uniqueNodeId(material.slug, draft.nodes);
    const preceding = draft.nodes.at(-1);
    if (material.type === "fan_out") {
      const candidates = concurrentMemberOptions(snapshot).filter((item) => item.resource.id !== draft.id);
      const compatiblePair = firstCompatiblePair(candidates);
      const members = compatiblePair.map((item, index) => ({
        id: index === 0 ? "perspective-a" : "perspective-b",
        type: item.type,
        version_id: item.version.id
      }));
      const inputSchema = compatiblePair[0]?.inputSchema ?? EMPTY_SCHEMA;
      const node = {
        id: nodeId,
        type: "fan_out",
        version_id: "fanout-v1",
        input_mapping: defaultMapping(inputSchema),
        members,
        barrier: {
          mode: "quorum",
          quorum: 2,
          verdict_path: "verdict",
          affirmative_values: ["commit"],
          on_member_error: "isolate"
        },
        position: position ?? { x: 120 + draft.nodes.length * 70, y: 110 + draft.nodes.length * 45 },
        settings: { max_attempts: 1, backoff_seconds: 0, retry_on: ["provider_failure"], on_error: "fail" }
      };
      replaceDraft((current) => ({
        ...current,
        nodes: [...current.nodes, node],
        start_node_id: current.start_node_id || node.id,
        input_schema_text: current.nodes.length === 0 && compatiblePair.length
          ? JSON.stringify(inputSchema, null, 2)
          : current.input_schema_text,
        output_schema_text: current.nodes.length === 0
          ? JSON.stringify(version.output_schema, null, 2)
          : current.output_schema_text,
        outcomes: current.nodes.length === 0 ? clone(FAN_OUT_OUTCOMES) : current.outcomes
      }));
      setSelectedNodeId(node.id);
      setInspectorOpen(true);
      return;
    }
    const predecessorVersion = preceding ? versionForNode(snapshot, preceding) : null;
    const schema = material.type === "agent"
      ? agentInputSchema(snapshot, version)
      : version.input_schema;
    const outputSchema = material.type === "agent"
      ? { type: "object", properties: { text: { type: "string" } }, required: ["text"], additionalProperties: false }
      : version.output_schema;
    const node = {
      id: nodeId,
      type: material.type,
      version_id: version.id,
      input_mapping: defaultMapping(schema, preceding ? { id: preceding.id, output_schema: predecessorVersion?.output_schema } : null),
      position: position ?? { x: 120 + draft.nodes.length * 70, y: 110 + draft.nodes.length * 45 },
      settings: { max_attempts: 1, backoff_seconds: 0, retry_on: ["provider_failure"], on_error: "fail" }
    };
    replaceDraft((current) => ({
      ...current,
      nodes: [...current.nodes, node],
      start_node_id: current.start_node_id || node.id,
      input_schema_text: current.nodes.length === 0
        ? JSON.stringify(schema, null, 2)
        : current.input_schema_text,
      output_schema_text: current.nodes.length === 0
        ? JSON.stringify(outputSchema ?? EMPTY_SCHEMA, null, 2)
        : current.output_schema_text,
      outcomes: current.nodes.length === 0
        ? clone(version.outcomes ?? SUCCESS_ERROR)
        : current.outcomes
    }));
    setSelectedNodeId(node.id);
    setInspectorOpen(true);
  }, [draft.nodes, replaceDraft, snapshot]);

  const onDrop = useCallback((event) => {
    event.preventDefault();
    const raw = event.dataTransfer.getData("application/kyn-node");
    if (!raw) return;
    const reference = JSON.parse(raw);
    const material = palette[reference.section].find((item) => item.version.id === reference.versionId);
    if (material) addNode(material, screenToFlowPosition({ x: event.clientX, y: event.clientY }));
  }, [addNode, palette, screenToFlowPosition]);

  const onConnect = useCallback((connection) => {
    if (!connection.source || !connection.target || !connection.sourceHandle) return;
    if (connection.source === connection.target) return;
    replaceDraft((current) => {
      const duplicate = current.routes.some((route) => route.from === connection.source && route.outcome === connection.sourceHandle);
      if (duplicate || createsCycle(current.routes, connection.source, connection.target)) return current;
      return {
        ...current,
        routes: [...current.routes, { from: connection.source, to: connection.target, outcome: connection.sourceHandle }]
      };
    });
  }, [replaceDraft]);

  const onNodesChange = useCallback((changes) => {
    onNodesChangeBase(changes.filter((change) => change.type !== "remove"));
    const removed = changes.filter((change) => change.type === "remove").map((change) => change.id);
    if (removed.length) {
      replaceDraft((current) => ({
        ...current,
        nodes: current.nodes.filter((node) => !removed.includes(node.id)),
        routes: current.routes.filter((route) => !removed.includes(route.from) && !removed.includes(route.to)),
        start_node_id: removed.includes(current.start_node_id) ? current.nodes.find((node) => !removed.includes(node.id))?.id ?? "" : current.start_node_id,
        acceptance_criteria: current.acceptance_criteria.map((criterion) => ({
          ...criterion,
          node_ids: criterion.node_ids.filter((nodeId) => !removed.includes(nodeId))
        }))
      }));
      setSelectedNodeId(null);
    }
  }, [onNodesChangeBase, replaceDraft]);

  const onNodeDragStop = useCallback((_, node) => {
    replaceDraft((current) => ({
      ...current,
      nodes: current.nodes.map((item) => item.id === node.id ? { ...item, position: { x: Math.round(node.position.x), y: Math.round(node.position.y) } } : item)
    }));
  }, [replaceDraft]);

  const onEdgesChange = useCallback((changes) => {
    onEdgesChangeBase(changes.filter((change) => change.type !== "remove"));
    const removed = new Set(changes.filter((change) => change.type === "remove").map((change) => change.id));
    if (removed.size) {
      replaceDraft((current) => ({ ...current, routes: current.routes.filter((route) => !removed.has(edgeId(route))) }));
    }
  }, [onEdgesChangeBase, replaceDraft]);

  const autoLayout = useCallback(() => {
    replaceDraft((current) => ({ ...current, nodes: layoutGraph(current.nodes, current.routes) }));
    requestAnimationFrame(() => fitGraph(0.2, 320));
  }, [fitGraph, replaceDraft]);

  const save = useCallback(async () => {
    try {
      if (!draft.nodes.length) throw new Error("Add at least one Action, Agent, or Flow node before publishing.");
      if (!draft.start_node_id) throw new Error("Choose a start node before publishing.");
      for (const node of draft.nodes.filter((candidate) => candidate.type === "fan_out")) {
        if (!Array.isArray(node.members) || node.members.length < 2 || node.members.length > 8) throw new Error(`Fan-out ${node.id} needs two to eight members.`);
        if (node.members.some((member) => !member.id)) throw new Error(`Every member in ${node.id} needs an ID.`);
        if (new Set(node.members.map((member) => member.id)).size !== node.members.length) throw new Error(`Fan-out ${node.id} member IDs must be unique.`);
        if (new Set(node.members.map((member) => `${member.type}:${member.version_id}`)).size !== node.members.length) throw new Error(`Fan-out ${node.id} must pin distinct target versions.`);
        const memberSchemas = node.members.map((member) => fanOutInputSchema(snapshot, { members: [member] }));
        if (new Set(memberSchemas.map(schemaKey)).size !== 1) throw new Error(`Every member in ${node.id} must accept the identical input contract.`);
        if (!node.barrier?.affirmative_values?.length || !node.barrier.verdict_path?.trim()) throw new Error(`Fan-out ${node.id} needs a verdict path and at least one affirmative value.`);
        if (node.barrier.mode === "all" && node.barrier.quorum !== node.members.length) throw new Error(`The all-members barrier in ${node.id} must require every member.`);
        if (node.barrier.quorum < 1 || node.barrier.quorum > node.members.length) throw new Error(`Fan-out ${node.id} has an impossible quorum.`);
      }
      if (draft.acceptance_criteria.some((criterion) => !criterion.id || !criterion.statement.trim())) throw new Error("Every completion criterion needs an ID and an observable promise.");
      if (new Set(draft.acceptance_criteria.map((criterion) => criterion.id)).size !== draft.acceptance_criteria.length) throw new Error("Completion criterion IDs must be unique.");
      if (draft.acceptance_criteria.some((criterion) => !criterion.node_ids.length)) throw new Error("Every completion criterion needs at least one evidence site.");
      if (draft.acceptance_criteria.length && !draft.judge_agent_version_id) throw new Error("Choose an independent Goal-Judge before publishing a completion contract.");
      if (draft.acceptance_criteria.some((criterion) => new Set(criterion.node_ids).size !== criterion.node_ids.length)) throw new Error("A completion criterion cannot name the same evidence site twice.");
      if (draft.acceptance_criteria.some((criterion) => criterion.node_ids.some((nodeId) => {
        const node = draft.nodes.find((candidate) => candidate.id === nodeId);
        return !node || !nodeCanMintEvidence(snapshot, node, criterion.evidence_kind);
      }))) throw new Error("Every completion evidence site must still be capable of minting its declared kind.");
      if (draft.acceptance_criteria.length) {
        const selectedJudge = judgeVersionOptions(snapshot, castAgentVersions(snapshot, draft.nodes))
          .find((judge) => judge.id === draft.judge_agent_version_id);
        if (!selectedJudge?.compatible || !selectedJudge.independent) throw new Error("Choose a compatible Goal-Judge that is independent of every Agent cast by this Flow.");
      }
      const body = {
        name: draft.name,
        description: draft.description,
        input_schema: parseJson(draft.input_schema_text, "Flow input schema"),
        output_schema: parseJson(draft.output_schema_text, "Flow output schema"),
        outcomes: draft.outcomes,
        acceptance_criteria: draft.acceptance_criteria,
        judge_agent_version_id: draft.acceptance_criteria.length ? draft.judge_agent_version_id || null : null,
        start_node_id: draft.start_node_id,
        nodes: draft.nodes,
        routes: draft.routes
      };
      const result = await mutate(
        () => draft.isNew
          ? api("/api/v1/studio/flows", { method: "POST", body: { ...body, slug: draft.slug } })
          : api(`/api/v1/studio/flows/${draft.id}/versions`, { method: "POST", body: { ...body, expected_revision: draft.expected_revision } }),
        { success: draft.isNew ? "Flow v1 published" : `Flow v${draft.expected_revision + 1} published` }
      );
      if (result) {
        setSelectedFlowId(result.id);
        setDraft(flowDraft(result));
        setDirty(false);
        history.current = { past: [], future: [] };
        // The publish already succeeded. Advisories only describe what three
        // independent Flows proved about this shape; they gate nothing.
        const matched = result.advisories ?? [];
        setAdvisory(matched.length ? { version: result.current_version, principles: matched } : null);
      }
    } catch (error) {
      await mutate(() => Promise.reject(error), { refreshAfter: false, success: "" });
    }
  }, [draft, mutate, snapshot]);

  const selectedNode = draft.nodes.find((node) => node.id === selectedNodeId) ?? null;

  return (
    <section className="flow-studio" aria-label="Visual Flow Studio">
      <header className="studio-toolbar">
        <div className="flow-picker">
          <label htmlFor="flow-select">Flow</label>
          <select id="flow-select" value={selectedFlowId ?? "new"} onChange={(event) => event.target.value === "new" ? createNew() : loadFlow(event.target.value)}>
            {flows.map((flow) => <option key={flow.id} value={flow.id}>{flow.name} · v{flow.current_version}</option>)}
            {!selectedFlowId ? <option value="new">Untitled Flow</option> : null}
          </select>
          <Badge tone={draft.isNew ? "neutral" : "success"}>{draft.isNew ? "Draft" : `Published v${draft.version}`}</Badge>
          {dirty ? <Badge tone="warning" dot>Unsaved</Badge> : null}
        </div>
        <div className="toolbar-actions">
          <IconButton icon="action" label={paletteOpen ? "Hide node library" : "Show node library"} className={paletteOpen ? "is-active" : ""} onClick={() => setPaletteOpen((value) => !value)} />
          <IconButton icon="settings" label={inspectorOpen ? "Hide inspector" : "Show inspector"} className={inspectorOpen ? "is-active" : ""} onClick={() => setInspectorOpen((value) => !value)} />
          <IconButton icon="undo" label="Undo" disabled={!history.current.past.length} onClick={undo} />
          <IconButton icon="redo" label="Redo" disabled={!history.current.future.length} onClick={redo} />
          <Button tone="quiet" icon="layout" onClick={autoLayout} disabled={!draft.nodes.length}>Auto layout</Button>
          <Button tone="default" icon="plus" onClick={createNew}>New Flow</Button>
          <Button tone="default" icon="play" onClick={() => setShowRun(true)} disabled={draft.isNew || dirty}>Run</Button>
          {/* Comparing needs a published, model-backed version to pin: a
              deterministic Flow has no brain to vary and the runtime refuses
              it, so the surface refuses it first rather than inviting the
              refusal. */}
          <Button
            tone="default"
            icon="compare"
            onClick={() => startComparison(selectedFlowId)}
            disabled={draft.isNew || dirty || !selectedFlow?.version?.requires_model}
            title={selectedFlow?.version?.requires_model ? "Compare models on this pinned version" : "Only a model-backed Flow can be compared"}
          >
            Compare models
          </Button>
          <Button tone="primary" icon="save" onClick={save} disabled={busy || !dirty || !draft.nodes.length}>{draft.isNew ? "Publish Flow" : "Publish successor"}</Button>
        </div>
      </header>

      <div className={`studio-workbench ${paletteOpen ? "has-palette" : ""} ${inspectorOpen ? "has-inspector" : ""}`}>
        {paletteOpen ? <Palette
          section={paletteSection}
          setSection={setPaletteSection}
          query={paletteQuery}
          setQuery={setPaletteQuery}
          resources={filteredPalette}
          counts={Object.fromEntries(Object.entries(palette).map(([key, items]) => [key, items.length]))}
          onAdd={addNode}
        /> : null}
        <div className="canvas-shell" onDrop={onDrop} onDragOver={(event) => { event.preventDefault(); event.dataTransfer.dropEffect = "copy"; }}>
          {draft.nodes.length ? (
            <ReactFlow
              nodes={canvasNodes}
              edges={canvasEdges}
              nodeTypes={NODE_TYPES}
              defaultEdgeOptions={EDGE_DEFAULTS}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onNodeDragStop={onNodeDragStop}
              onNodeClick={(_, node) => { setSelectedNodeId(node.id); setInspectorOpen(true); }}
              onPaneClick={() => setSelectedNodeId(null)}
              onConnect={onConnect}
              isValidConnection={(connection) => connection.source !== connection.target && Boolean(connection.sourceHandle)}
              connectionLineType={ConnectionLineType.SmoothStep}
              connectionLineStyle={{ stroke: graph["accent-text"], strokeWidth: 2 }}
              deleteKeyCode={["Backspace", "Delete"]}
              fitView
              fitViewOptions={{
                padding: 0.22,
                minZoom: window.matchMedia("(max-width: 760px)").matches ? 0.72 : 0.18
              }}
              minZoom={0.18}
              maxZoom={1.8}
              snapToGrid
              snapGrid={[16, 16]}
              proOptions={{ hideAttribution: true }}
            >
              <Background gap={24} size={1.2} color={graph["graph-dot"]} />
              <Controls position="bottom-left" showInteractive={false} />
              <MiniMap position="bottom-right" pannable zoomable nodeColor={(node) => graph[node.data.color]} maskColor={graph["minimap-mask"]} />
              <Panel position="top-left" className="canvas-hint"><span>{draft.nodes.length} nodes</span><span>{draft.routes.length} routes</span><span>{draft.outcomes.length} Flow outputs</span></Panel>
            </ReactFlow>
          ) : (
            <EmptyCanvas onAddFirst={() => addNode(palette.actions[0] ?? palette.agents[0] ?? palette.flows[0])} />
          )}
          {advisory ? <PublishAdvisory advisory={advisory} onDismiss={() => setAdvisory(null)} onSelectRun={focusRun} /> : null}
        </div>
        {inspectorOpen ? <Inspector
          draft={draft}
          node={selectedNode}
          snapshot={snapshot}
          replaceDraft={replaceDraft}
          setSelectedNodeId={setSelectedNodeId}
          onCollapse={() => setInspectorOpen(false)}
        /> : null}
      </div>
      {showRun && selectedFlow ? (
        <StartFlowModal
          flow={selectedFlow}
          mutate={mutate}
          onClose={() => setShowRun(false)}
          onStarted={(run) => { setShowRun(false); focusRun(run.id); }}
        />
      ) : null}
    </section>
  );
}

/** What the workspace already learned about the shape that was just published.
 *
 * Deliberately calmer than `BrakeRefusal`. The brake is danger-toned because it
 * refused a Run; this refused nothing — the Flow is published, it will run, and
 * dismissing this panel is optional. Every word here has to keep that true.
 */
function PublishAdvisory({ advisory, onDismiss, onSelectRun }) {
  const { version, principles } = advisory;
  const nodeCount = new Set(principles.flatMap((principle) => principle.node_ids)).size;
  return (
    <section className="publish-advisory" role="status" aria-labelledby="publish-advisory-title">
      <header>
        <span className="advisory-icon"><Icon name="skill" size={22} /></span>
        <div>
          <p className="panel-kicker">Distilled principle · advisory, not a refusal</p>
          <h2 id="publish-advisory-title">Published{version ? ` as v${version}` : ""}. This Flow will run.</h2>
          <p>
            Nothing was blocked and nothing needs your acknowledgement. It is shown because independent Flows
            in this workspace already failed the same structural way, and the rule they distilled matches{" "}
            <strong>{nodeCount} node{nodeCount === 1 ? "" : "s"}</strong> of what you just published. Only the
            ratification brake ever refuses a Run, and only on the one pinned path three Runs proved.
          </p>
        </div>
        <IconButton icon="close" label="Dismiss the publish advisory" onClick={onDismiss} />
      </header>
      {principles.map((principle) => (
        <article key={principle.signature}>
          <header>
            <Badge tone="blue" dot>Advisory</Badge>
            <strong>{principle.node_ids.join(", ")}</strong>
            <code>{principle.error_code}</code>
            <span className="dead-end-count"><b>{principle.distinct_flows}</b> distinct Flows</span>
          </header>
          <p>{principle.statement}</p>
          <DefinitionList items={[
            ["Matched nodes in this draft", principle.node_ids.join(", ")],
            ["Executor kind", principle.executor_kind],
            ["Declared predicate", principle.policy_marker],
            ["Signature", <code key="signature">{principle.signature.slice(0, 24)}…</code>]
          ]} />
          <CitedRuns
            label={`Distilled from ${principle.citing_run_ids.length} prior Runs · open any of them`}
            ids={principle.citing_run_ids}
            onSelectRun={onSelectRun}
          />
        </article>
      ))}
    </section>
  );
}

function flowDraft(flow) {
  if (!flow) {
    return {
      id: null,
      isNew: true,
      expected_revision: null,
      version: null,
      name: "Untitled Flow",
      slug: "untitled-flow",
      description: "Describe the operational job this Flow owns.",
      input_schema_text: JSON.stringify({ type: "object", properties: { value: { type: "string" } }, required: ["value"], additionalProperties: false }, null, 2),
      output_schema_text: JSON.stringify(EMPTY_SCHEMA, null, 2),
      outcomes: clone(SUCCESS_ERROR),
      acceptance_criteria: [],
      judge_agent_version_id: null,
      start_node_id: "",
      nodes: [],
      routes: []
    };
  }
  const fallbackLayout = new Map(
    layoutGraph(flow.version.nodes, flow.version.routes).map((node) => [node.id, node.position])
  );
  const normalizedNodes = flow.version.nodes.map((node) => ({
    ...clone(node),
    position: node.position ?? fallbackLayout.get(node.id),
    settings: node.settings ?? {
      max_attempts: 1,
      backoff_seconds: 0,
      retry_on: ["provider_failure"],
      on_error: "fail"
    }
  }));
  return {
    id: flow.id,
    isNew: false,
    expected_revision: flow.revision,
    version: flow.version.version,
    name: flow.name,
    slug: flow.slug,
    description: flow.description,
    input_schema_text: JSON.stringify(flow.version.input_schema, null, 2),
    output_schema_text: JSON.stringify(flow.version.output_schema ?? EMPTY_SCHEMA, null, 2),
    outcomes: clone(flow.version.outcomes ?? SUCCESS_ERROR),
    acceptance_criteria: clone(flow.version.acceptance_criteria ?? []),
    judge_agent_version_id: flow.version.judge_agent_version_id ?? null,
    start_node_id: flow.version.start_node_id,
    nodes: normalizedNodes,
    routes: clone(flow.version.routes)
  };
}

function paletteResources(snapshot, selectedFlowId) {
  return {
    control: [{
      id: "fanout-v1",
      type: "fan_out",
      slug: "parallel-fan-out",
      name: "Parallel fan-out + barrier",
      description: "Dispatch 2–8 independent pinned members concurrently, then compute quorum and dissent in code.",
      kind: "fan_out",
      version: {
        id: "fanout-v1",
        version: 1,
        input_schema: EMPTY_SCHEMA,
        output_schema: {
          type: "object",
          properties: { members: { type: "object" }, barrier: { type: "object" } },
          required: ["members", "barrier"],
          additionalProperties: false
        },
        outcomes: FAN_OUT_OUTCOMES
      }
    }],
    actions: snapshot.studio.actions.map((action) => ({
      id: action.id, type: "action", slug: action.slug, name: action.name,
      description: action.description, kind: action.version.kind, version: action.version
    })),
    agents: snapshot.agents.map((agent) => ({
      id: agent.id, type: "agent", slug: agent.slug, name: agent.name,
      description: agent.version.instructions, kind: "agent", version: agent.version
    })),
    flows: snapshot.studio.flows.filter((flow) => flow.id !== selectedFlowId).map((flow) => ({
      id: flow.id, type: "flow", slug: flow.slug, name: flow.name,
      description: flow.description, kind: "subflow", version: flow.version
    }))
  };
}

function Palette({ section, setSection, query, setQuery, resources, counts, onAdd }) {
  return (
    <aside className="node-palette" aria-label="Node library">
      <header><div><p className="panel-kicker">Node library</p><h2>Capabilities</h2></div><Badge tone="neutral">Drag or add</Badge></header>
      <label className="search-box"><Icon name="search" size={16} /><input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Find a capability…" aria-label="Search node library" /></label>
      <div className="palette-tabs" role="tablist" aria-label="Capability type">
        {["control", "actions", "agents", "flows"].map((item) => (
          <button key={item} type="button" role="tab" aria-selected={section === item} className={section === item ? "is-active" : ""} onClick={() => setSection(item)}>{titleCase(item)}<span>{counts[item]}</span></button>
        ))}
      </div>
      <div className="palette-list">
        {resources.length ? resources.map((resource) => (
          <button
            type="button"
            className="palette-card"
            key={resource.version.id}
            draggable
            onDragStart={(event) => {
              event.dataTransfer.setData("application/kyn-node", JSON.stringify({ section, versionId: resource.version.id }));
              event.dataTransfer.effectAllowed = "copy";
            }}
            onClick={() => onAdd(resource)}
          >
            <span className={`node-symbol symbol-${resource.kind}`}><Icon name={resource.type === "fan_out" ? "parallel" : resource.type === "action" ? "action" : resource.type === "agent" ? "agent" : "flow"} size={16} /></span>
            <span><strong>{resource.name}</strong><small>{titleCase(resource.kind)} · v{resource.version.version}</small></span>
            <Icon name="plus" size={15} />
          </button>
        )) : <EmptyState icon={section === "control" ? "parallel" : section === "flows" ? "flow" : section === "agents" ? "agent" : "action"} title="Nothing matches" description="Change the search or create the resource from its registry." />}
      </div>
      <footer><Icon name="lock" size={14} /><span>Nodes pin immutable versions when you publish.</span></footer>
    </aside>
  );
}

function EmptyCanvas({ onAddFirst }) {
  return (
    <div className="empty-canvas">
      <div className="empty-canvas-grid" aria-hidden="true" />
      <span className="empty-canvas-icon"><Icon name="flow" size={30} /></span>
      <h2>Compose the job on the canvas</h2>
      <p>Drag an Action, Agent, or published Flow from the library. Each declared outcome becomes its own connectable port.</p>
      <Button tone="primary" icon="plus" onClick={onAddFirst}>Add the first capability</Button>
      <small>64 nodes · 192 routes · 12 outputs per node · 4 nested Flow levels</small>
    </div>
  );
}

function KynNode({ data, selected }) {
  const inputCount = Math.max(1, data.inputs.length);
  const outputCount = Math.max(1, data.outcomes.length);
  const portStart = 148;
  const portGap = 30;
  const height = portStart + (Math.max(inputCount, outputCount) - 1) * portGap + 30;
  return (
    <article className={`kyn-node ${selected ? "is-selected" : ""} ${data.isStart ? "is-start" : ""}`} style={{ minHeight: height }}>
      <header>
        <span className={`node-symbol symbol-${data.kind}`}><Icon name={data.type === "action" ? "action" : data.type === "agent" ? "agent" : "flow"} size={16} /></span>
        <div><strong>{data.label}</strong><small>{titleCase(data.kind)} · v{data.version}</small></div>
        {data.isStart ? <Badge tone="success">Start</Badge> : null}
      </header>
      <p>{data.subtitle}</p>
      <div className="node-contract"><span>{data.inputCount} in</span><span>{data.outputCount} out</span><span>{data.effect}</span></div>
      {(data.inputs.length ? data.inputs : [{ id: "in:default", label: "input" }]).map((input, index) => {
        const top = portStart + index * portGap;
        return <React.Fragment key={input.id}><Handle type="target" id={input.id} position={Position.Left} className="kyn-handle target-handle" style={{ top }} /><span className="port-label port-label-in" style={{ top: top - 9 }}>{input.label}</span></React.Fragment>;
      })}
      {data.outcomes.map((outcome, index) => {
        const top = portStart + index * portGap;
        return <React.Fragment key={outcome.id}><span className={`port-label port-label-out tone-${outcome.tone}`} style={{ top: top - 9 }}>{outcome.label}</span><Handle type="source" id={outcome.id} position={Position.Right} className={`kyn-handle source-handle tone-${outcome.tone}`} style={{ top }} /></React.Fragment>;
      })}
    </article>
  );
}

function FanOutNode({ data, selected }) {
  const portStart = 178;
  const portGap = 30;
  const height = portStart + (Math.max(data.inputs.length || 1, data.outcomes.length) - 1) * portGap + 34;
  return (
    <article className={`kyn-node fan-out-node ${selected ? "is-selected" : ""} ${data.isStart ? "is-start" : ""}`} style={{ minHeight: height }}>
      <header>
        <span className="node-symbol symbol-fan_out"><Icon name="parallel" size={17} /></span>
        <div><strong>{data.label}</strong><small>Parallel composition · fanout-v1</small></div>
        {data.isStart ? <Badge tone="success">Start</Badge> : null}
      </header>
      <div className="fan-out-members" aria-label={`${data.members.length} parallel members`}>
        {data.members.slice(0, 5).map((member) => (
          <span key={member.id} title={member.label}>
            <Icon name={member.type === "action" ? "action" : member.type === "agent" ? "agent" : "flow"} size={12} />
            <b>{member.id}</b>
          </span>
        ))}
        {data.members.length > 5 ? <span><b>+{data.members.length - 5}</b></span> : null}
        {!data.members.length ? <em>Choose 2–8 independent members</em> : null}
      </div>
      <div className="fan-out-barrier">
        <span><Icon name="lock" size={12} />{data.barrier.mode === "all" ? "all members" : `quorum ${data.barrier.quorum}/${data.members.length || "?"}`}</span>
        <span>{data.barrier.on_member_error === "isolate" ? "errors isolated" : "fail fast"}</span>
      </div>
      {(data.inputs.length ? data.inputs : [{ id: "in:default", label: "input" }]).map((input, index) => {
        const top = portStart + index * portGap;
        return <React.Fragment key={input.id}><Handle type="target" id={input.id} position={Position.Left} className="kyn-handle target-handle" style={{ top }} /><span className="port-label port-label-in" style={{ top: top - 9 }}>{input.label}</span></React.Fragment>;
      })}
      {data.outcomes.map((outcome, index) => {
        const top = portStart + index * portGap;
        return <React.Fragment key={outcome.id}><span className={`port-label port-label-out tone-${outcome.tone}`} style={{ top: top - 9 }}>{outcome.label}</span><Handle type="source" id={outcome.id} position={Position.Right} className={`kyn-handle source-handle tone-${outcome.tone}`} style={{ top }} /></React.Fragment>;
      })}
    </article>
  );
}

function hydrateNodes(snapshot, draft) {
  return draft.nodes.map((node) => {
    const resource = resourceForNode(snapshot, node);
    const version = versionForNode(snapshot, node);
    const incoming = draft.routes.filter((route) => route.to === node.id).map((route) => ({ id: `in:${route.from}:${route.outcome}`, label: route.outcome }));
    const outcomes = nodeOutcomes(snapshot, node);
    const kind = node.type === "fan_out" ? "fan_out" : node.type === "action" ? (version?.kind ?? "action") : node.type === "agent" ? "agent" : "subflow";
    const inputSchema = node.type === "fan_out" ? fanOutInputSchema(snapshot, node) : node.type === "agent" ? agentInputSchema(snapshot, version) : version?.input_schema;
    const outputSchema = node.type === "agent" ? { properties: { text: {} } } : version?.output_schema;
    const memberLookup = new Map(concurrentMemberOptions(snapshot).map((item) => [`${item.type}:${item.version.id}`, item]));
    return {
      id: node.id,
      type: node.type === "fan_out" ? "fanOutNode" : "kynNode",
      position: node.position,
      selected: false,
      data: {
        label: resource?.name ?? node.id,
        subtitle: resource?.description ?? version?.instructions ?? "Pinned capability",
        type: node.type,
        kind,
        version: version?.version ?? "?",
        outcomes,
        inputs: incoming,
        inputCount: Object.keys(inputSchema?.properties ?? {}).length,
        outputCount: Object.keys(outputSchema?.properties ?? {}).length,
        effect: version?.effect_level ?? (node.type === "agent" ? "model" : "linked run"),
        color: colorForKind(kind),
        isStart: draft.start_node_id === node.id,
        members: (node.members ?? []).map((member) => ({
          ...member,
          label: memberLookup.get(`${member.type}:${member.version_id}`)?.label ?? member.id
        })),
        barrier: node.barrier ?? { mode: "quorum", quorum: 2, on_member_error: "isolate" }
      }
    };
  });
}

function hydrateEdges(draft) {
  return draft.routes.map((route) => ({
    id: edgeId(route),
    source: route.from,
    target: route.to,
    sourceHandle: route.outcome,
    targetHandle: `in:${route.from}:${route.outcome}`,
    label: route.outcome,
    ...EDGE_DEFAULTS,
    className: route.outcome === "error" || route.outcome === "rejected" ? "edge-danger" : ""
  }));
}

function edgeId(route) { return `${route.from}:${route.outcome}:${route.to}`; }

function colorForKind(kind) {
  if (kind === "fan_out") return "tone-ai-solid";
  if (kind === "ai" || kind === "agent") return "tone-ai-solid";
  if (kind === "approval") return "tone-warning-solid";
  if (kind === "router" || kind === "condition" || kind === "assert") return "tone-cyan-solid";
  if (kind === "data_store" || kind === "sandbox") return "tone-danger-solid";
  if (kind === "subflow") return "tone-blue-solid";
  return "tone-success-solid";
}

function Inspector({ draft, node, snapshot, replaceDraft, setSelectedNodeId, onCollapse }) {
  return (
    <aside className="node-inspector" aria-label={node ? `Inspector for ${node.id}` : "Flow inspector"}>
      {node ? (
        node.type === "fan_out" ? (
          <FanOutInspector node={node} draft={draft} snapshot={snapshot} replaceDraft={replaceDraft} onRename={(nextId) => { if (draft.nodes.some((item) => item.id === nextId && item.id !== node.id)) return false; renameNode(node.id, nextId, replaceDraft); setSelectedNodeId(nextId); return true; }} onClose={() => { setSelectedNodeId(null); onCollapse(); }} onRemove={() => removeNode(node.id, replaceDraft, setSelectedNodeId)} />
        ) : (
          <NodeInspector node={node} draft={draft} snapshot={snapshot} replaceDraft={replaceDraft} onRename={(nextId) => { if (draft.nodes.some((item) => item.id === nextId && item.id !== node.id)) return false; renameNode(node.id, nextId, replaceDraft); setSelectedNodeId(nextId); return true; }} onClose={() => { setSelectedNodeId(null); onCollapse(); }} onRemove={() => removeNode(node.id, replaceDraft, setSelectedNodeId)} />
        )
      ) : (
        <FlowInspector draft={draft} snapshot={snapshot} replaceDraft={replaceDraft} />
      )}
    </aside>
  );
}

function FlowInspector({ draft, snapshot, replaceDraft }) {
  return (
    <>
      <header className="inspector-header"><div><p className="panel-kicker">Flow contract</p><h2>{draft.name}</h2></div><Badge tone={draft.isNew ? "neutral" : "success"}>{draft.isNew ? "new" : `v${draft.version}`}</Badge></header>
      <div className="inspector-scroll">
        <section className="inspector-section">
          <h3>Identity</h3>
          <Field label="Name"><input value={draft.name} onChange={(event) => replaceDraft((current) => ({ ...current, name: event.target.value, slug: current.isNew ? slugify(event.target.value) : current.slug }))} /></Field>
          <Field label="Slug" hint={draft.isNew ? "Stable API identifier" : "Slug is immutable after v1."}><input value={draft.slug} disabled={!draft.isNew} onChange={(event) => replaceDraft((current) => ({ ...current, slug: slugDraft(event.target.value) }))} onBlur={(event) => replaceDraft((current) => ({ ...current, slug: slugify(event.target.value) }))} /></Field>
          <Field label="Purpose"><textarea rows="3" value={draft.description} onChange={(event) => replaceDraft((current) => ({ ...current, description: event.target.value }))} /></Field>
        </section>
        <section className="inspector-section">
          <h3>Public outcomes <Badge tone="neutral">{draft.outcomes.length}/12</Badge></h3>
          <p className="section-help">A terminal node outcome can become the outcome of this Flow when its ID matches.</p>
          <OutcomeEditor outcomes={draft.outcomes} onChange={(outcomes) => replaceDraft((current) => ({ ...current, outcomes }))} />
        </section>
        <CompletionContractEditor draft={draft} snapshot={snapshot} replaceDraft={replaceDraft} />
        <section className="inspector-section schema-section">
          <h3>Typed boundary</h3>
          <JsonField label="Flow input schema" value={draft.input_schema_text} onChange={(value) => replaceDraft((current) => ({ ...current, input_schema_text: value }))} rows={9} hint="Every Run input is validated before it is queued." />
          <JsonField label="Flow output schema" value={draft.output_schema_text} onChange={(value) => replaceDraft((current) => ({ ...current, output_schema_text: value }))} rows={9} hint="Every terminal output must satisfy this union contract." />
        </section>
        {!draft.isNew ? <FlowVersions flow={snapshot.studio.flows.find((flow) => flow.id === draft.id)} /> : null}
      </div>
    </>
  );
}

function CompletionContractEditor({ draft, snapshot, replaceDraft }) {
  const castAgents = useMemo(() => castAgentVersions(snapshot, draft.nodes), [draft.nodes, snapshot]);
  const judges = useMemo(() => judgeVersionOptions(snapshot, castAgents), [castAgents, snapshot]);
  const selectableJudges = judges.filter((judge) => judge.compatible && judge.independent);
  const selectedJudge = judges.find((judge) => judge.id === draft.judge_agent_version_id);

  const updateCriterion = (criterionIndex, updater) => replaceDraft((current) => ({
    ...current,
    acceptance_criteria: current.acceptance_criteria.map((criterion, index) =>
      index === criterionIndex ? updater(criterion, current) : criterion
    )
  }));

  const addCriterion = () => replaceDraft((current) => {
    if (current.acceptance_criteria.length >= MAX_ACCEPTANCE_CRITERIA) return current;
    const id = uniqueCriterionId(current.acceptance_criteria);
    const firstNode = current.nodes[0]?.id;
    return {
      ...current,
      judge_agent_version_id: current.judge_agent_version_id ?? selectableJudges[0]?.id ?? null,
      acceptance_criteria: [...current.acceptance_criteria, {
        id,
        statement: "The declared work completed at an explicitly named site.",
        evidence_kind: "step",
        node_ids: firstNode ? [firstNode] : []
      }]
    };
  });

  const removeCriterion = (criterionIndex) => replaceDraft((current) => {
    const criteria = current.acceptance_criteria.filter((_, index) => index !== criterionIndex);
    return {
      ...current,
      acceptance_criteria: criteria,
      judge_agent_version_id: criteria.length ? current.judge_agent_version_id : null
    };
  });

  return <section className="inspector-section completion-contract-editor">
    <h3>Completion contract <Badge tone={draft.acceptance_criteria.length ? "ai" : "neutral"}>{draft.acceptance_criteria.length}/{MAX_ACCEPTANCE_CRITERIA}</Badge></h3>
    <div className="stop-seam-note">
      <Icon name="lock" size={16} />
      <p><strong>“Finished” is a claim, not a terminal state.</strong><span>A pinned Goal-Judge must nominate runtime evidence for every promise. Code independently resolves the anchors before this Flow may become completed.</span></p>
    </div>
    {draft.acceptance_criteria.length ? <Field
      label="Independent Goal-Judge"
      required
      hint="The exact Agent, Prompt, Skills, and model version are pinned. Agents cast by this graph cannot judge it."
    >
      <select value={draft.judge_agent_version_id ?? ""} onChange={(event) => replaceDraft((current) => ({ ...current, judge_agent_version_id: event.target.value || null }))}>
        <option value="">Choose a Judge Agent version…</option>
        {judges.map((judge) => <option key={judge.id} value={judge.id} disabled={!judge.compatible || !judge.independent}>
          {judge.name} · v{judge.version} · {judge.model}{!judge.compatible ? " · incompatible Prompt" : !judge.independent ? " · cast by this Flow" : ""}
        </option>)}
      </select>
    </Field> : null}
    {draft.acceptance_criteria.length && draft.judge_agent_version_id && (!selectedJudge?.compatible || !selectedJudge?.independent)
      ? <p className="field-error">The selected Judge is no longer eligible: its Prompt is incompatible or this graph now casts the same Agent version.</p>
      : null}
    <div className="criterion-editor-list">
      {draft.acceptance_criteria.map((criterion, index) => {
        const eligibleNodes = draft.nodes.filter((node) => nodeCanMintEvidence(snapshot, node, criterion.evidence_kind));
        const missingSites = !criterion.node_ids.length;
        const duplicateId = draft.acceptance_criteria.some((candidate, candidateIndex) => candidateIndex !== index && candidate.id === criterion.id);
        return <article className={`criterion-editor ${missingSites || duplicateId ? "is-invalid" : ""}`} key={`criterion-editor-${index}`}>
          <header><span>{index + 1}</span><strong>{criterion.statement || "Untitled completion promise"}</strong><IconButton icon="trash" label={`Remove criterion ${criterion.id}`} onClick={() => removeCriterion(index)} /></header>
          <Field label="Criterion ID" required hint="Stable identifier written into every adjudication event.">
            <input value={criterion.id} aria-invalid={duplicateId || !criterion.id} onChange={(event) => updateCriterion(index, (current) => ({ ...current, id: slugDraft(event.target.value) }))} onBlur={(event) => updateCriterion(index, (current) => ({ ...current, id: slugify(event.target.value) }))} />
          </Field>
          {duplicateId ? <p className="field-error">Criterion IDs must be unique.</p> : null}
          <Field label="Promise" required hint="State the observable work this Flow must actually have performed.">
            <textarea rows="3" value={criterion.statement} onChange={(event) => updateCriterion(index, (current) => ({ ...current, statement: event.target.value }))} />
          </Field>
          <Field label="Admissible evidence" required hint={EVIDENCE_KINDS.find((kind) => kind.id === criterion.evidence_kind)?.hint}>
            <select value={criterion.evidence_kind} onChange={(event) => updateCriterion(index, (current, whole) => {
              const evidenceKind = event.target.value;
              const eligible = whole.nodes.filter((node) => nodeCanMintEvidence(snapshot, node, evidenceKind));
              const retained = current.node_ids.filter((nodeId) => eligible.some((node) => node.id === nodeId));
              return { ...current, evidence_kind: evidenceKind, node_ids: retained.length ? retained : eligible[0] ? [eligible[0].id] : [] };
            })}>
              {EVIDENCE_KINDS.map((kind) => <option key={kind.id} value={kind.id}>{kind.label}</option>)}
            </select>
          </Field>
          <fieldset className="evidence-sites">
            <legend>Evidence sites <b aria-hidden="true">*</b></legend>
            <p>Any selected site may carry this promise; every selected site must be capable of minting the chosen evidence.</p>
            {eligibleNodes.map((node) => <label key={node.id}>
              <input type="checkbox" checked={criterion.node_ids.includes(node.id)} onChange={(event) => updateCriterion(index, (current) => ({
                ...current,
                node_ids: event.target.checked
                  ? [...current.node_ids, node.id]
                  : current.node_ids.filter((nodeId) => nodeId !== node.id)
              }))} />
              <span><strong>{graphNodeLabel(snapshot, node)}</strong><small>{node.id}</small></span>
            </label>)}
            {!eligibleNodes.length ? <p className="field-error">No node in this draft can mint {criterion.evidence_kind} evidence.</p> : null}
            {missingSites && eligibleNodes.length ? <p className="field-error">Choose at least one evidence site.</p> : null}
          </fieldset>
        </article>;
      })}
    </div>
    <Button tone="quiet" icon="plus" onClick={addCriterion} disabled={!draft.nodes.length || draft.acceptance_criteria.length >= MAX_ACCEPTANCE_CRITERIA}>Add completion criterion</Button>
    {!draft.acceptance_criteria.length ? <p className="section-help">Optional. Without criteria the Flow completes normally and performs zero Goal-Judge calls.</p> : null}
    {draft.acceptance_criteria.length && !selectableJudges.length ? <p className="field-error">Create an independent Agent whose Prompt uses only acceptance/evidence variables before publishing this contract.</p> : null}
  </section>;
}

function uniqueCriterionId(criteria) {
  const used = new Set(criteria.map((criterion) => criterion.id));
  let index = criteria.length + 1;
  while (used.has(`completion-${index}`)) index += 1;
  return `completion-${index}`;
}

function judgeVersionOptions(snapshot, castAgents) {
  const prompts = new Map(snapshot.prompts.flatMap((prompt) => prompt.versions).map((version) => [version.id, version]));
  return snapshot.agents.flatMap((agent) => agent.versions.map((version) => {
    const prompt = prompts.get(version.prompt_version_id);
    const compatible = Boolean(prompt) && (prompt.variables ?? []).every((variable) => JUDGE_PROMPT_VARIABLES.has(variable));
    return {
      id: version.id,
      name: agent.name,
      slug: agent.slug,
      version: version.version,
      model: version.model,
      compatible,
      independent: !castAgents.has(version.id)
    };
  })).sort((left, right) => {
    const leftDedicated = left.slug === "completion-goal-judge" ? 0 : 1;
    const rightDedicated = right.slug === "completion-goal-judge" ? 0 : 1;
    return leftDedicated - rightDedicated || left.name.localeCompare(right.name) || right.version - left.version;
  });
}

function castAgentVersions(snapshot, nodes) {
  const cast = new Set();
  const visitedFlows = new Set();
  const visit = (node) => {
    if (node.type === "fan_out") {
      (node.members ?? []).forEach((member) => visit({
        type: member.type,
        version_id: member.version_id
      }));
      return;
    }
    const version = versionForNode(snapshot, node);
    if (!version) return;
    if (node.type === "agent") cast.add(node.version_id);
    if (node.type === "action" && version.agent_version_id) cast.add(version.agent_version_id);
    if (node.type === "flow" && !visitedFlows.has(version.id)) {
      visitedFlows.add(version.id);
      if (version.judge_agent_version_id) cast.add(version.judge_agent_version_id);
      (version.nodes ?? []).forEach(visit);
    }
  };
  nodes.forEach(visit);
  return cast;
}

function nodeCanMintEvidence(snapshot, node, evidenceKind) {
  if (evidenceKind === "step") return true;
  if (node.type === "fan_out") {
    if (evidenceKind !== "receipt") return false;
    return (node.members ?? []).some((member) => member.type === "action");
  }
  if (node.type !== "action") return false;
  const version = versionForNode(snapshot, node);
  if (!version) return false;
  if (evidenceKind === "receipt") return true;
  if (evidenceKind === "approval") return version.kind === "approval";
  return ["data_store", "sandbox"].includes(version.kind) && version.config?.write_enabled !== false;
}

function FanOutInspector({ node, draft, snapshot, replaceDraft, onRename, onClose, onRemove }) {
  const [nodeIdDraft, setNodeIdDraft] = useState(node.id);
  useEffect(() => setNodeIdDraft(node.id), [node.id]);
  const options = useMemo(
    () => concurrentMemberOptions(snapshot).filter((item) => item.resource.id !== draft.id),
    [draft.id, snapshot]
  );
  const lookup = useMemo(
    () => new Map(options.map((item) => [`${item.type}:${item.version.id}`, item])),
    [options]
  );
  const inputSchema = fanOutInputSchema(snapshot, node);
  const schemaFingerprint = schemaKey(inputSchema);
  const compatible = options.filter((item) => schemaKey(item.inputSchema) === schemaFingerprint);
  const duplicateIds = new Set(
    (node.members ?? []).filter((member, index, members) => members.some((candidate, candidateIndex) => candidateIndex !== index && candidate.id === member.id)).map((member) => member.id)
  );
  const duplicateTargets = new Set(
    (node.members ?? []).filter((member, index, members) => members.some((candidate, candidateIndex) => candidateIndex !== index && candidate.type === member.type && candidate.version_id === member.version_id)).map((member) => `${member.type}:${member.version_id}`)
  );
  const updateNode = (updater) => replaceDraft((current) => ({
    ...current,
    nodes: current.nodes.map((item) => item.id === node.id ? updater(item) : item)
  }));

  const replaceTarget = (memberIndex, targetKey) => {
    const selected = options.find((item) => `${item.type}:${item.version.id}` === targetKey);
    if (!selected) return;
    updateNode((current) => {
      let members = current.members.map((member, index) => index === memberIndex
        ? { ...member, type: selected.type, version_id: selected.version.id }
        : member);
      if (memberIndex === 0) {
        const pool = options.filter((item) => schemaKey(item.inputSchema) === schemaKey(selected.inputSchema));
        const used = new Set([targetKey]);
        members = members.map((member, index) => {
          if (index === 0) return member;
          const key = `${member.type}:${member.version_id}`;
          if (pool.some((item) => `${item.type}:${item.version.id}` === key) && !used.has(key)) {
            used.add(key);
            return member;
          }
          const replacement = pool.find((item) => !used.has(`${item.type}:${item.version.id}`));
          if (!replacement) return member;
          const replacementKey = `${replacement.type}:${replacement.version.id}`;
          used.add(replacementKey);
          return { ...member, type: replacement.type, version_id: replacement.version.id };
        });
      }
      const nextSchema = selected.inputSchema;
      return {
        ...current,
        members,
        input_mapping: memberIndex === 0 ? defaultMapping(nextSchema) : current.input_mapping
      };
    });
  };

  const addMember = () => updateNode((current) => {
    if (current.members.length >= 8) return current;
    const used = new Set(current.members.map((member) => `${member.type}:${member.version_id}`));
    const candidate = compatible.find((item) => !used.has(`${item.type}:${item.version.id}`));
    if (!candidate) return current;
    const memberId = uniqueMemberId(current.members);
    const members = [...current.members, { id: memberId, type: candidate.type, version_id: candidate.version.id }];
    return {
      ...current,
      members,
      barrier: {
        ...current.barrier,
        quorum: current.barrier.mode === "all" ? members.length : Math.min(current.barrier.quorum, members.length)
      }
    };
  });

  const removeMember = (memberIndex) => updateNode((current) => {
    if (current.members.length <= 2) return current;
    const members = current.members.filter((_, index) => index !== memberIndex);
    return {
      ...current,
      members,
      barrier: {
        ...current.barrier,
        quorum: current.barrier.mode === "all" ? members.length : Math.min(current.barrier.quorum, members.length)
      }
    };
  });

  return (
    <>
      <header className="inspector-header"><div><p className="panel-kicker">Parallel control</p><h2>Fan-out + barrier</h2></div><IconButton icon="close" label="Close fan-out inspector" onClick={onClose} /></header>
      <div className="inspector-scroll fan-out-inspector">
        <section className="inspector-section node-identity">
          <div className="resource-lock"><span className="node-symbol symbol-fan_out"><Icon name="parallel" size={17} /></span><div><strong>Code-owned concurrency primitive</strong><small>fanout-v1 · pinned members</small></div></div>
          <Field label="Node ID" hint="Stable evidence site for the parent Step."><input value={nodeIdDraft} onChange={(event) => setNodeIdDraft(event.target.value)} onBlur={() => { const next = slugify(nodeIdDraft); if (next && next !== node.id) { if (!onRename(next)) setNodeIdDraft(node.id); } else setNodeIdDraft(node.id); }} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); event.currentTarget.blur(); } }} /></Field>
          <label className="check-row"><input type="radio" name="start-node" checked={draft.start_node_id === node.id} onChange={() => replaceDraft((current) => ({ ...current, start_node_id: node.id }))} /><span><strong>Start node</strong><small>Dispatch every member from the same validated input.</small></span></label>
        </section>

        <section className="inspector-section fan-out-composition">
          <div className="section-heading"><div><h3>Independent members</h3><p>Two to eight distinct immutable targets with one identical input contract.</p></div><Badge tone="ai">{node.members.length}/8</Badge></div>
          <div className="fanout-member-list">
            {node.members.map((member, index) => {
              const targetKey = `${member.type}:${member.version_id}`;
              const entry = lookup.get(targetKey);
              const memberOptions = index === 0
                ? options.filter((candidate) => options.filter((peer) => schemaKey(peer.inputSchema) === schemaKey(candidate.inputSchema)).length >= node.members.length)
                : compatible;
              return <article className={`fanout-member-editor ${duplicateIds.has(member.id) || duplicateTargets.has(targetKey) || !entry ? "is-invalid" : ""}`} key={`member-${index}`}>
                <header><span>{String(index + 1).padStart(2, "0")}</span><strong>{entry?.label ?? "Unavailable pinned target"}</strong><IconButton icon="trash" label={`Remove member ${member.id}`} disabled={node.members.length <= 2} onClick={() => removeMember(index)} /></header>
                <Field label="Member ID" required><input value={member.id} aria-invalid={duplicateIds.has(member.id)} onChange={(event) => updateNode((current) => ({ ...current, members: current.members.map((candidate, candidateIndex) => candidateIndex === index ? { ...candidate, id: slugDraft(event.target.value) } : candidate) }))} onBlur={(event) => updateNode((current) => ({ ...current, members: current.members.map((candidate, candidateIndex) => candidateIndex === index ? { ...candidate, id: slugify(event.target.value) } : candidate) }))} /></Field>
                <Field label="Pinned target" required hint={entry ? `${titleCase(entry.type)} · immutable v${entry.version.version}` : "This version is no longer eligible for parallel execution."}>
                  <select value={targetKey} aria-invalid={!entry || duplicateTargets.has(targetKey)} onChange={(event) => replaceTarget(index, event.target.value)}>
                    {!entry ? <option value={targetKey}>Unavailable · {member.version_id}</option> : null}
                    {memberOptions.map((candidate) => <option key={`${candidate.type}:${candidate.version.id}`} value={`${candidate.type}:${candidate.version.id}`}>{candidate.label}</option>)}
                  </select>
                </Field>
              </article>;
            })}
          </div>
          {duplicateIds.size ? <p className="field-error">Member IDs must be unique lowercase slugs.</p> : null}
          {duplicateTargets.size ? <p className="field-error">Every member must pin a distinct target version.</p> : null}
          <Button tone="quiet" icon="plus" onClick={addMember} disabled={node.members.length >= 8 || !compatible.some((item) => !node.members.some((member) => member.type === item.type && member.version_id === item.version.id))}>Add compatible member</Button>
        </section>

        <section className="inspector-section">
          <h3>Deterministic barrier</h3>
          <p className="section-help">Models contribute records. Code counts votes, preserves dissent, and owns the route.</p>
          <div className="field-grid two">
            <Field label="Barrier mode"><select value={node.barrier.mode} onChange={(event) => updateNode((current) => ({ ...current, barrier: { ...current.barrier, mode: event.target.value, quorum: event.target.value === "all" ? current.members.length : Math.min(current.barrier.quorum, current.members.length) } }))}><option value="quorum">Quorum</option><option value="all">All members</option></select></Field>
            <Field label="Affirmative votes"><input type="number" min="1" max={node.members.length} disabled={node.barrier.mode === "all"} value={node.barrier.quorum} onChange={(event) => updateNode((current) => ({ ...current, barrier: { ...current.barrier, quorum: Math.max(1, Math.min(current.members.length, Number(event.target.value) || 1)) } }))} /></Field>
          </div>
          <Field label="Verdict path" hint="Dot path declared by every member output schema."><input value={node.barrier.verdict_path} onChange={(event) => updateNode((current) => ({ ...current, barrier: { ...current.barrier, verdict_path: event.target.value } }))} placeholder="verdict" /></Field>
          <Field label="Affirmative values" hint="Comma-separated exact string values; for example commit, approve."><input value={node.barrier.affirmative_values.join(", ")} onChange={(event) => updateNode((current) => ({ ...current, barrier: { ...current.barrier, affirmative_values: event.target.value.split(",").map((value) => value.trim()).filter(Boolean).slice(0, 8) } }))} /></Field>
          <Field label="Member failure"><select value={node.barrier.on_member_error} onChange={(event) => updateNode((current) => ({ ...current, barrier: { ...current.barrier, on_member_error: event.target.value } }))}><option value="isolate">Isolate and expose failure</option><option value="fail_fast">Fail parent after evidence</option></select></Field>
        </section>

        <section className="inspector-section">
          <h3>Shared input mapping <Badge tone="neutral">{Object.keys(inputSchema.properties ?? {}).length}</Badge></h3>
          <p className="section-help">The same mapped object is copied to every independent member.</p>
          {Object.entries(inputSchema.properties ?? {}).map(([name, schema]) => <MappingRow key={name} name={name} schema={schema} mapping={node.input_mapping[name]} node={node} draft={draft} onChange={(mapping) => updateNode((current) => ({ ...current, input_mapping: { ...current.input_mapping, [name]: mapping } }))} />)}
          {!Object.keys(inputSchema.properties ?? {}).length ? <p className="muted">Choose compatible members to establish the shared input contract.</p> : null}
        </section>

        <section className="inspector-section">
          <h3>Barrier routes <Badge tone="neutral">{FAN_OUT_OUTCOMES.length}</Badge></h3>
          <div className="outcome-route-list">
            {FAN_OUT_OUTCOMES.map((outcome) => {
              const route = draft.routes.find((item) => item.from === node.id && item.outcome === outcome.id);
              return <label key={outcome.id} className={`outcome-route tone-${outcome.tone}`}><span><i /><strong>{outcome.label}</strong><small>{outcome.id}</small></span><select value={route?.to ?? ""} onChange={(event) => setOutcomeRoute(node.id, outcome.id, event.target.value, replaceDraft)}><option value="">End Flow</option>{draft.nodes.filter((candidate) => candidate.id !== node.id).map((candidate) => <option key={candidate.id} value={candidate.id}>{graphNodeLabel(snapshot, candidate)} · {candidate.id}</option>)}</select></label>;
            })}
          </div>
        </section>

        <section className="inspector-section contract-preview">
          <h3>Concurrency guarantees</h3>
          <div className="fanout-guarantees"><p><Icon name="parallel" size={15} /><span><strong>Real parallel dispatch</strong>Each member receives its own operation session and child Step.</span></p><p><Icon name="lock" size={15} /><span><strong>One parent attempt</strong>The barrier is never silently retried as a group.</span></p><p><Icon name="activity" size={15} /><span><strong>Inspectable dissent</strong>Success, failure, abstention, and vote evidence survive the join.</span></p></div>
          <details><summary>Shared input schema</summary><pre>{JSON.stringify(inputSchema, null, 2)}</pre></details>
        </section>
        <div className="danger-zone"><Button tone="danger" icon="trash" onClick={onRemove}>Remove fan-out</Button><p>This changes only the draft. Published versions remain immutable.</p></div>
      </div>
    </>
  );
}

function NodeInspector({ node, draft, snapshot, replaceDraft, onRename, onClose, onRemove }) {
  const resource = resourceForNode(snapshot, node);
  const version = versionForNode(snapshot, node);
  const outcomes = nodeOutcomes(snapshot, node);
  const inputSchema = node.type === "agent" ? agentInputSchema(snapshot, version) : version?.input_schema ?? EMPTY_SCHEMA;
  const outputSchema = node.type === "agent" ? { type: "object", properties: { text: { type: "string" } }, required: ["text"], additionalProperties: false } : version?.output_schema ?? EMPTY_SCHEMA;
  const [nodeIdDraft, setNodeIdDraft] = useState(node.id);
  useEffect(() => setNodeIdDraft(node.id), [node.id]);
  const updateNode = (updater) => replaceDraft((current) => ({ ...current, nodes: current.nodes.map((item) => item.id === node.id ? updater(item) : item) }));
  return (
    <>
      <header className="inspector-header"><div><p className="panel-kicker">Node inspector</p><h2>{resource?.name ?? node.id}</h2></div><IconButton icon="close" label="Close node inspector" onClick={onClose} /></header>
      <div className="inspector-scroll">
        <section className="inspector-section node-identity">
          <div className="resource-lock"><span className={`node-symbol symbol-${version?.kind ?? node.type}`}><Icon name={node.type === "action" ? "action" : node.type === "agent" ? "agent" : "flow"} size={17} /></span><div><strong>{titleCase(node.type)} · immutable v{version?.version}</strong><small>{version?.id}</small></div></div>
          <Field label="Node ID" hint="Used by mappings and Run evidence. Commit with Enter or by leaving the field."><input value={nodeIdDraft} onChange={(event) => setNodeIdDraft(event.target.value)} onBlur={() => { const next = slugify(nodeIdDraft); if (next && next !== node.id) { if (!onRename(next)) setNodeIdDraft(node.id); } else setNodeIdDraft(node.id); }} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); event.currentTarget.blur(); } }} /></Field>
          <label className="check-row"><input type="radio" name="start-node" checked={draft.start_node_id === node.id} onChange={() => replaceDraft((current) => ({ ...current, start_node_id: node.id }))} /><span><strong>Start node</strong><small>The first capability invoked by a Run.</small></span></label>
        </section>
        <section className="inspector-section">
          <h3>Input mapping <Badge tone="neutral">{Object.keys(inputSchema.properties ?? {}).length}</Badge></h3>
          <p className="section-help">No ambient context. Every field names its source.</p>
          {Object.entries(inputSchema.properties ?? {}).map(([name, schema]) => (
            <MappingRow key={name} name={name} schema={schema} mapping={node.input_mapping[name]} node={node} draft={draft} onChange={(mapping) => updateNode((current) => ({ ...current, input_mapping: { ...current.input_mapping, [name]: mapping } }))} />
          ))}
          {!Object.keys(inputSchema.properties ?? {}).length ? <p className="muted">This node accepts no input fields.</p> : null}
        </section>
        <section className="inspector-section">
          <h3>Outcome routes <Badge tone="neutral">{outcomes.length}</Badge></h3>
          <p className="section-help">Every port is independent. “End Flow” makes that outcome terminal.</p>
          <div className="outcome-route-list">
            {outcomes.map((outcome) => {
              const route = draft.routes.find((item) => item.from === node.id && item.outcome === outcome.id);
              return (
                <label key={outcome.id} className={`outcome-route tone-${outcome.tone}`}>
                  <span><i /> <strong>{outcome.label}</strong><small>{outcome.id}</small></span>
                  <select value={route?.to ?? ""} onChange={(event) => setOutcomeRoute(node.id, outcome.id, event.target.value, replaceDraft)}>
                    <option value="">End Flow</option>
                    {draft.nodes.filter((candidate) => candidate.id !== node.id).map((candidate) => <option key={candidate.id} value={candidate.id}>{graphNodeLabel(snapshot, candidate)} · {candidate.id}</option>)}
                  </select>
                </label>
              );
            })}
          </div>
        </section>
        <section className="inspector-section">
          <h3>Operational policy</h3>
          <div className="field-grid two">
            <Field label="Max attempts"><input type="number" min="1" max="3" value={node.settings.max_attempts} onChange={(event) => updateNode((current) => ({ ...current, settings: { ...current.settings, max_attempts: Number(event.target.value) } }))} /></Field>
            <Field label="Backoff seconds"><input type="number" min="0" max="5" step="0.25" value={node.settings.backoff_seconds} onChange={(event) => updateNode((current) => ({ ...current, settings: { ...current.settings, backoff_seconds: Number(event.target.value) } }))} /></Field>
          </div>
          <Field label="After handled error"><select value={node.settings.on_error} onChange={(event) => updateNode((current) => ({ ...current, settings: { ...current.settings, on_error: event.target.value } }))}><option value="fail">Fail the Run</option><option value="continue">Follow the error port</option></select></Field>
          <label className="check-row compact"><input type="checkbox" checked={node.settings.retry_on.includes("provider_failure")} onChange={(event) => updateNode((current) => ({ ...current, settings: { ...current.settings, retry_on: event.target.checked ? ["provider_failure"] : [] } }))} /><span><strong>Retry provider failures</strong><small>Never retries authority decisions silently.</small></span></label>
        </section>
        <section className="inspector-section contract-preview">
          <h3>Version contract</h3>
          <details><summary>Input schema</summary><pre>{JSON.stringify(inputSchema, null, 2)}</pre></details>
          <details><summary>Output schema</summary><pre>{JSON.stringify(outputSchema, null, 2)}</pre></details>
          {node.type === "agent" ? <AgentPins snapshot={snapshot} version={version} /> : null}
        </section>
        <div className="danger-zone"><Button tone="danger" icon="trash" onClick={onRemove}>Remove node</Button><p>This changes only the draft. Published versions remain immutable.</p></div>
      </div>
    </>
  );
}

function MappingRow({ name, schema, mapping, node, draft, onChange }) {
  const source = mapping?.source ?? "input";
  const predecessors = draft.nodes.filter((candidate) => candidate.id !== node.id);
  return (
    <div className="mapping-row">
      <div className="mapping-name"><strong>{name}</strong><span>{schema.type ?? "any"}</span></div>
      <select aria-label={`Source for ${name}`} value={source} onChange={(event) => {
        const next = event.target.value;
        if (next === "literal") onChange({ source: "literal", value: exampleForSchema(schema, name) });
        else if (next === "step") onChange({ source: "step", node_id: predecessors[0]?.id ?? "", path: name });
        else onChange({ source: "input", path: name });
      }}>
        <option value="input">Flow input</option><option value="step">Earlier Step</option><option value="literal">Literal</option>
      </select>
      {source === "step" ? <div className="mapping-source-detail"><select aria-label={`Step for ${name}`} value={mapping?.node_id ?? ""} onChange={(event) => onChange({ ...mapping, node_id: event.target.value })}>{predecessors.map((candidate) => <option key={candidate.id} value={candidate.id}>{candidate.id}</option>)}</select><input aria-label={`Output path for ${name}`} value={mapping?.path ?? name} onChange={(event) => onChange({ ...mapping, path: event.target.value })} placeholder="output.path" /></div> : source === "literal" ? <input aria-label={`Literal for ${name}`} value={typeof mapping?.value === "string" ? mapping.value : JSON.stringify(mapping?.value ?? "")} onChange={(event) => onChange({ source: "literal", value: event.target.value })} /> : <input aria-label={`Flow input path for ${name}`} value={mapping?.path ?? name} onChange={(event) => onChange({ source: "input", path: event.target.value })} placeholder="input.path" />}
    </div>
  );
}

function OutcomeEditor({ outcomes, onChange }) {
  const update = (index, field, value) => onChange(outcomes.map((item, itemIndex) => itemIndex === index ? { ...item, [field]: field === "id" ? slugify(value) : value } : item));
  return (
    <div className="outcome-editor">
      {outcomes.map((outcome, index) => (
        <div className="outcome-edit-row" key={`${outcome.id}-${index}`}>
          <i className={`tone-${outcome.tone}`} />
          <input aria-label={`Outcome ${index + 1} label`} value={outcome.label} onChange={(event) => update(index, "label", event.target.value)} />
          <input aria-label={`Outcome ${index + 1} ID`} value={outcome.id} disabled={outcome.id === "error"} onChange={(event) => update(index, "id", event.target.value)} />
          <select aria-label={`Outcome ${index + 1} tone`} value={outcome.tone} onChange={(event) => update(index, "tone", event.target.value)}><option value="neutral">Neutral</option><option value="success">Success</option><option value="warning">Warning</option><option value="danger">Danger</option><option value="ai">AI</option></select>
          <IconButton icon="trash" label={`Remove ${outcome.label}`} disabled={outcome.id === "error" || outcomes.length <= 2} onClick={() => onChange(outcomes.filter((_, itemIndex) => itemIndex !== index))} />
        </div>
      ))}
      <Button tone="quiet" icon="plus" disabled={outcomes.length >= 12} onClick={() => onChange([...outcomes.slice(0, -1), { id: `outcome-${outcomes.length}`, label: `Outcome ${outcomes.length}`, description: "", tone: "neutral" }, outcomes.at(-1)])}>Add output</Button>
    </div>
  );
}

function FlowVersions({ flow }) {
  if (!flow) return null;
  return <section className="inspector-section"><h3>Version history <Badge tone="neutral">{flow.versions.length}</Badge></h3><div className="version-list">{flow.versions.map((version) => <article key={version.id}><span>v{version.version}</span><div><strong>{version.nodes.length} nodes · {version.routes.length} routes</strong><small>{version.fingerprint.slice(0, 18)}…</small></div>{version.version === flow.current_version ? <Badge tone="success">current</Badge> : null}</article>)}</div></section>;
}

function AgentPins({ snapshot, version }) {
  if (!version) return null;
  const prompt = snapshot.prompts.flatMap((item) => item.versions.map((candidate) => ({ resource: item, version: candidate }))).find((item) => item.version.id === version.prompt_version_id);
  const skills = snapshot.skills.flatMap((item) => item.versions.map((candidate) => ({ resource: item, version: candidate }))).filter((item) => version.skill_version_ids.includes(item.version.id));
  return <div className="agent-pins"><p><strong>Model</strong><span>{version.model}</span></p><p><strong>Prompt</strong><span>{prompt?.resource.name ?? "Missing"} v{prompt?.version.version}</span></p><p><strong>Skills</strong><span>{skills.map((item) => `${item.resource.name} v${item.version.version}`).join(", ") || "No skills"}</span></p><p><strong>Effective tools</strong><span>{version.effective_tools.join(", ") || "None"}</span></p></div>;
}

function StartFlowModal({ flow, mutate, onClose, onStarted }) {
  const [input, setInput] = useState(JSON.stringify(exampleForSchema(flow.version.input_schema), null, 2));
  const submit = async (event) => {
    event.preventDefault();
    let refused = false;
    try {
      const result = await mutate(
        () => api(`/api/v1/studio/flows/${flow.id}/runs:enqueue`, {
          method: "POST",
          keyMode: flow.version.requires_model ? "required" : "optional",
          body: { input: parseJson(input, "Run input"), idempotency_key: commandId("manual-run") }
        }).catch((error) => { refused = error.code === "brake_engaged"; throw error; }),
        { success: "Run pinned and queued" }
      );
      if (result) onStarted(result);
      else if (refused) onClose();
    } catch { /* mutate renders the bounded error */ }
  };
  return <Modal title={`Run ${flow.name}`} description={`The Run will pin Flow v${flow.current_version} and all transitive resource versions.`} onClose={onClose}><form className="modal-form" onSubmit={submit}><JsonField label="Run input" value={input} onChange={setInput} rows={12} hint={flow.version.requires_model ? "This Flow will use the OpenAI key held in this tab." : "This Flow is deterministic and does not need an OpenAI key."} /><div className="modal-actions"><Button tone="quiet" type="button" onClick={onClose}>Cancel</Button><Button tone="primary" icon="play" type="submit">Pin and start Run</Button></div></form></Modal>;
}

function agentInputSchema(snapshot, version) {
  const prompt = snapshot.prompts.flatMap((resource) => resource.versions).find((item) => item.id === version?.prompt_version_id);
  return { type: "object", properties: Object.fromEntries((prompt?.variables ?? []).map((name) => [name, { type: "string" }])), required: prompt?.variables ?? [], additionalProperties: false };
}

function schemaKey(schema) {
  const canonicalize = (value) => {
    if (Array.isArray(value)) return value.map(canonicalize);
    if (value && typeof value === "object") {
      return Object.fromEntries(
        Object.keys(value).sort().map((key) => [key, canonicalize(value[key])])
      );
    }
    return value;
  };
  return JSON.stringify(canonicalize(schema ?? EMPTY_SCHEMA));
}

function uniqueMemberId(members) {
  const used = new Set(members.map((member) => member.id));
  let index = members.length + 1;
  while (used.has(`perspective-${index}`)) index += 1;
  return `perspective-${index}`;
}

function actionVersionById(snapshot, versionId) {
  return snapshot.studio.actions.flatMap((resource) => resource.versions).find((version) => version.id === versionId) ?? null;
}

function agentVersionById(snapshot, versionId) {
  return snapshot.agents.flatMap((resource) => resource.versions).find((version) => version.id === versionId) ?? null;
}

function flowVersionById(snapshot, versionId) {
  return snapshot.studio.flows.flatMap((resource) => resource.versions).find((version) => version.id === versionId) ?? null;
}

function agentActionGrants(snapshot, version) {
  if (Array.isArray(version?.effective_action_version_ids)) return version.effective_action_version_ids;
  const skillIds = new Set(version?.skill_version_ids ?? []);
  return snapshot.skills
    .flatMap((resource) => resource.versions)
    .filter((skill) => skillIds.has(skill.id))
    .flatMap((skill) => skill.allowed_action_version_ids ?? []);
}

function actionCanPauseOrWrite(version) {
  if (!version) return true;
  if (version.kind === "approval") return true;
  return ["data_store", "sandbox"].includes(version.kind) && version.config?.write_enabled !== false;
}

function concurrentTargetIsSafe(snapshot, type, versionId, seenFlows = new Set()) {
  if (type === "action") {
    const version = actionVersionById(snapshot, versionId);
    if (!version || actionCanPauseOrWrite(version)) return false;
    if (version.kind !== "ai" || !version.agent_version_id) return true;
    return concurrentTargetIsSafe(snapshot, "agent", version.agent_version_id, seenFlows);
  }
  if (type === "agent") {
    const version = agentVersionById(snapshot, versionId);
    if (!version) return false;
    return agentActionGrants(snapshot, version).every((actionId) => {
      const granted = actionVersionById(snapshot, actionId);
      return Boolean(granted) && !actionCanPauseOrWrite(granted);
    });
  }
  if (type !== "flow" || seenFlows.has(versionId)) return false;
  const version = flowVersionById(snapshot, versionId);
  if (!version || !version.output_schema) return false;
  const nextSeen = new Set(seenFlows).add(versionId);
  return (version.nodes ?? []).every((node) => node.type !== "fan_out" && concurrentTargetIsSafe(snapshot, node.type, node.version_id, nextSeen));
}

function concurrentMemberOptions(snapshot) {
  const actions = snapshot.studio.actions.flatMap((resource) => resource.versions.map((version) => ({
    resource,
    version,
    type: "action",
    inputSchema: version.input_schema,
    label: `${resource.name} · Action v${version.version}`
  })));
  const agents = snapshot.agents.flatMap((resource) => resource.versions.map((version) => ({
    resource,
    version,
    type: "agent",
    inputSchema: agentInputSchema(snapshot, version),
    label: `${resource.name} · Agent v${version.version}`
  })));
  const flows = snapshot.studio.flows.flatMap((resource) => resource.versions.map((version) => ({
    resource,
    version,
    type: "flow",
    inputSchema: version.input_schema,
    label: `${resource.name} · Flow v${version.version}`
  })));
  return [...actions, ...agents, ...flows]
    .filter((item) => concurrentTargetIsSafe(snapshot, item.type, item.version.id))
    .sort((left, right) => left.type.localeCompare(right.type) || left.resource.name.localeCompare(right.resource.name) || right.version.version - left.version.version);
}

function firstCompatiblePair(options) {
  const groups = new Map();
  for (const option of options) {
    const key = schemaKey(option.inputSchema);
    groups.set(key, [...(groups.get(key) ?? []), option]);
  }
  return [...groups.values()].find((group) => group.length >= 2)?.slice(0, 2) ?? [];
}

function fanOutInputSchema(snapshot, node) {
  const first = node?.members?.[0];
  if (!first) return EMPTY_SCHEMA;
  if (first.type === "agent") return agentInputSchema(snapshot, agentVersionById(snapshot, first.version_id));
  if (first.type === "action") return actionVersionById(snapshot, first.version_id)?.input_schema ?? EMPTY_SCHEMA;
  return flowVersionById(snapshot, first.version_id)?.input_schema ?? EMPTY_SCHEMA;
}

function createsCycle(routes, source, target) {
  const adjacency = new Map();
  for (const route of [...routes, { from: source, to: target }]) {
    if (!adjacency.has(route.from)) adjacency.set(route.from, []);
    adjacency.get(route.from).push(route.to);
  }
  const pending = [target];
  const seen = new Set();
  while (pending.length) {
    const current = pending.pop();
    if (current === source) return true;
    if (seen.has(current)) continue;
    seen.add(current);
    pending.push(...(adjacency.get(current) ?? []));
  }
  return false;
}

function setOutcomeRoute(source, outcome, target, replaceDraft) {
  replaceDraft((current) => {
    const routes = current.routes.filter((route) => !(route.from === source && route.outcome === outcome));
    if (!target || createsCycle(routes, source, target)) return { ...current, routes };
    return { ...current, routes: [...routes, { from: source, to: target, outcome }] };
  });
}

function renameNode(oldId, nextId, replaceDraft) {
  if (!nextId || oldId === nextId) return;
  replaceDraft((current) => {
    if (current.nodes.some((node) => node.id === nextId)) return current;
    return {
      ...current,
      start_node_id: current.start_node_id === oldId ? nextId : current.start_node_id,
      nodes: current.nodes.map((node) => ({
        ...node,
        id: node.id === oldId ? nextId : node.id,
        input_mapping: Object.fromEntries(Object.entries(node.input_mapping).map(([key, mapping]) => [key, mapping.source === "step" && mapping.node_id === oldId ? { ...mapping, node_id: nextId } : mapping]))
      })),
      routes: current.routes.map((route) => ({ ...route, from: route.from === oldId ? nextId : route.from, to: route.to === oldId ? nextId : route.to })),
      acceptance_criteria: current.acceptance_criteria.map((criterion) => ({
        ...criterion,
        node_ids: criterion.node_ids.map((nodeId) => nodeId === oldId ? nextId : nodeId)
      }))
    };
  });
}

function removeNode(nodeId, replaceDraft, setSelectedNodeId) {
  replaceDraft((current) => {
    const nodes = current.nodes.filter((node) => node.id !== nodeId);
    return {
      ...current,
      nodes,
      routes: current.routes.filter((route) => route.from !== nodeId && route.to !== nodeId),
      start_node_id: current.start_node_id === nodeId ? nodes[0]?.id ?? "" : current.start_node_id,
      acceptance_criteria: current.acceptance_criteria.map((criterion) => ({
        ...criterion,
        node_ids: criterion.node_ids.filter((site) => site !== nodeId)
      }))
    };
  });
  setSelectedNodeId(null);
}
