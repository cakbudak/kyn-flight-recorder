import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  SUCCESS_ERROR,
  clone,
  defaultMapping,
  exampleForSchema,
  graphNodeLabel,
  layoutGraph,
  nodeOutcomes,
  parseJson,
  resourceForNode,
  slugify,
  titleCase,
  uniqueNodeId,
  versionForNode
} from "../lib.js";
import {
  Badge,
  Button,
  EmptyState,
  Field,
  IconButton,
  JsonField,
  Modal
} from "./ui.jsx";

const NODE_TYPES = { kynNode: KynNode };
const EDGE_DEFAULTS = {
  type: "smoothstep",
  markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16 },
  style: { strokeWidth: 1.8 }
};

export default function FlowStudio(props) {
  return <ReactFlowProvider><FlowStudioInner {...props} /></ReactFlowProvider>;
}

function FlowStudioInner({ snapshot, mutate, busy, setView }) {
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
  const history = useRef({ past: [], future: [] });
  const { screenToFlowPosition, fitView } = useReactFlow();
  const [canvasNodes, setCanvasNodes, onNodesChangeBase] = useNodesState([]);
  const [canvasEdges, setCanvasEdges, onEdgesChangeBase] = useEdgesState([]);

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
    const timer = setTimeout(() => fitView({ padding: 0.2, duration: 220 }), 180);
    return () => clearTimeout(timer);
  }, [fitView, inspectorOpen, paletteOpen]);

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
    history.current = { past: [], future: [] };
    requestAnimationFrame(() => fitView({ padding: 0.22, duration: 280 }));
  }, [fitView, flows]);

  const createNew = useCallback(() => {
    setSelectedFlowId(null);
    setDraft(flowDraft(null));
    setSelectedNodeId(null);
    setDirty(false);
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
        start_node_id: removed.includes(current.start_node_id) ? current.nodes.find((node) => !removed.includes(node.id))?.id ?? "" : current.start_node_id
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
    requestAnimationFrame(() => fitView({ padding: 0.2, duration: 320 }));
  }, [fitView, replaceDraft]);

  const save = useCallback(async () => {
    try {
      if (!draft.nodes.length) throw new Error("Add at least one Action, Agent, or Flow node before publishing.");
      if (!draft.start_node_id) throw new Error("Choose a start node before publishing.");
      const body = {
        name: draft.name,
        description: draft.description,
        input_schema: parseJson(draft.input_schema_text, "Flow input schema"),
        output_schema: parseJson(draft.output_schema_text, "Flow output schema"),
        outcomes: draft.outcomes,
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
      }
    } catch (error) {
      await mutate(() => Promise.reject(error), { refreshAfter: false, success: "" });
    }
  }, [draft, mutate]);

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
              connectionLineStyle={{ stroke: "#c9ff73", strokeWidth: 2 }}
              deleteKeyCode={["Backspace", "Delete"]}
              fitView
              fitViewOptions={{ padding: 0.22 }}
              minZoom={0.18}
              maxZoom={1.8}
              snapToGrid
              snapGrid={[16, 16]}
              proOptions={{ hideAttribution: true }}
            >
              <Background gap={24} size={1.2} color="#262a31" />
              <Controls position="bottom-left" showInteractive={false} />
              <MiniMap position="bottom-right" pannable zoomable nodeColor={(node) => node.data.color} maskColor="rgba(8,10,13,.76)" />
              <Panel position="top-left" className="canvas-hint"><span>{draft.nodes.length} nodes</span><span>{draft.routes.length} routes</span><span>{draft.outcomes.length} Flow outputs</span></Panel>
            </ReactFlow>
          ) : (
            <EmptyCanvas onAddFirst={() => addNode(palette.actions[0] ?? palette.agents[0] ?? palette.flows[0])} />
          )}
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
          onStarted={() => { setShowRun(false); setView("runs"); }}
        />
      ) : null}
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
    start_node_id: flow.version.start_node_id,
    nodes: normalizedNodes,
    routes: clone(flow.version.routes)
  };
}

function paletteResources(snapshot, selectedFlowId) {
  return {
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
        {["actions", "agents", "flows"].map((item) => (
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
            <span className={`node-symbol symbol-${resource.kind}`}><Icon name={resource.type === "action" ? "action" : resource.type === "agent" ? "agent" : "flow"} size={16} /></span>
            <span><strong>{resource.name}</strong><small>{titleCase(resource.kind)} · v{resource.version.version}</small></span>
            <Icon name="plus" size={15} />
          </button>
        )) : <EmptyState icon={section === "flows" ? "flow" : section === "agents" ? "agent" : "action"} title="Nothing matches" description="Change the search or create the resource from its registry." />}
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
  const portStart = 116;
  const portGap = 28;
  const height = portStart + (Math.max(inputCount, outputCount) - 1) * portGap + 28;
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
        return <React.Fragment key={input.id}><Handle type="target" id={input.id} position={Position.Left} className="kyn-handle target-handle" style={{ top }} /><span className="port-label port-label-in" style={{ top: top - 8 }}>{input.label}</span></React.Fragment>;
      })}
      {data.outcomes.map((outcome, index) => {
        const top = portStart + index * portGap;
        return <React.Fragment key={outcome.id}><span className={`port-label port-label-out tone-${outcome.tone}`} style={{ top: top - 8 }}>{outcome.label}</span><Handle type="source" id={outcome.id} position={Position.Right} className={`kyn-handle source-handle tone-${outcome.tone}`} style={{ top }} /></React.Fragment>;
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
    const kind = node.type === "action" ? (version?.kind ?? "action") : node.type === "agent" ? "agent" : "subflow";
    const inputSchema = node.type === "agent" ? agentInputSchema(snapshot, version) : version?.input_schema;
    const outputSchema = node.type === "agent" ? { properties: { text: {} } } : version?.output_schema;
    return {
      id: node.id,
      type: "kynNode",
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
        isStart: draft.start_node_id === node.id
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
  if (kind === "ai" || kind === "agent") return "#a892ff";
  if (kind === "approval") return "#f6ca6a";
  if (kind === "router" || kind === "condition" || kind === "assert") return "#70d8d0";
  if (kind === "data_store" || kind === "sandbox") return "#ff8d72";
  if (kind === "subflow") return "#7fb1ff";
  return "#c9ff73";
}

function Inspector({ draft, node, snapshot, replaceDraft, setSelectedNodeId, onCollapse }) {
  return (
    <aside className="node-inspector" aria-label={node ? `Inspector for ${node.id}` : "Flow inspector"}>
      {node ? (
        <NodeInspector node={node} draft={draft} snapshot={snapshot} replaceDraft={replaceDraft} onRename={(nextId) => { if (draft.nodes.some((item) => item.id === nextId && item.id !== node.id)) return false; renameNode(node.id, nextId, replaceDraft); setSelectedNodeId(nextId); return true; }} onClose={() => { setSelectedNodeId(null); onCollapse(); }} onRemove={() => removeNode(node.id, replaceDraft, setSelectedNodeId)} />
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
          <Field label="Slug" hint={draft.isNew ? "Stable API identifier" : "Slug is immutable after v1."}><input value={draft.slug} disabled={!draft.isNew} onChange={(event) => replaceDraft((current) => ({ ...current, slug: slugify(event.target.value) }))} /></Field>
          <Field label="Purpose"><textarea rows="3" value={draft.description} onChange={(event) => replaceDraft((current) => ({ ...current, description: event.target.value }))} /></Field>
        </section>
        <section className="inspector-section">
          <h3>Public outcomes <Badge tone="neutral">{draft.outcomes.length}/12</Badge></h3>
          <p className="section-help">A terminal node outcome can become the outcome of this Flow when its ID matches.</p>
          <OutcomeEditor outcomes={draft.outcomes} onChange={(outcomes) => replaceDraft((current) => ({ ...current, outcomes }))} />
        </section>
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
    try {
      const result = await mutate(
        () => api(`/api/v1/studio/flows/${flow.id}/runs:enqueue`, {
          method: "POST",
          keyMode: flow.version.requires_model ? "required" : "optional",
          body: { input: parseJson(input, "Run input"), idempotency_key: commandId("manual-run") }
        }),
        { success: "Run pinned and queued" }
      );
      if (result) onStarted(result);
    } catch { /* mutate renders the bounded error */ }
  };
  return <Modal title={`Run ${flow.name}`} description={`The Run will pin Flow v${flow.current_version} and all transitive resource versions.`} onClose={onClose}><form className="modal-form" onSubmit={submit}><JsonField label="Run input" value={input} onChange={setInput} rows={12} hint={flow.version.requires_model ? "This Flow will use the OpenAI key held in this tab." : "This Flow is deterministic and does not need an OpenAI key."} /><div className="modal-actions"><Button tone="quiet" type="button" onClick={onClose}>Cancel</Button><Button tone="primary" icon="play" type="submit">Pin and start Run</Button></div></form></Modal>;
}

function agentInputSchema(snapshot, version) {
  const prompt = snapshot.prompts.flatMap((resource) => resource.versions).find((item) => item.id === version?.prompt_version_id);
  return { type: "object", properties: Object.fromEntries((prompt?.variables ?? []).map((name) => [name, { type: "string" }])), required: prompt?.variables ?? [], additionalProperties: false };
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
      routes: current.routes.map((route) => ({ ...route, from: route.from === oldId ? nextId : route.from, to: route.to === oldId ? nextId : route.to }))
    };
  });
}

function removeNode(nodeId, replaceDraft, setSelectedNodeId) {
  replaceDraft((current) => {
    const nodes = current.nodes.filter((node) => node.id !== nodeId);
    return { ...current, nodes, routes: current.routes.filter((route) => route.from !== nodeId && route.to !== nodeId), start_node_id: current.start_node_id === nodeId ? nodes[0]?.id ?? "" : current.start_node_id };
  });
  setSelectedNodeId(null);
}
