import React, { useEffect, useMemo, useState } from "react";
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow
} from "@xyflow/react";
import { api, commandId } from "../api.js";
import { Icon } from "../icons.jsx";
import {
  STATUS_TONE,
  exampleForSchema,
  formatTime,
  graphNodeLabel,
  layoutGraph,
  nodeOutcomes,
  parseJson,
  resourceForNode,
  runNodeState,
  shortId,
  titleCase,
  versionForNode
} from "../lib.js";
import {
  Badge,
  Button,
  EmptyState,
  Field,
  IconButton,
  JsonField,
  KeyValue,
  Modal,
  PageHeader,
  Segmented,
  StatusBadge
} from "./ui.jsx";

const RUN_NODE_TYPES = { runNode: RunGraphNode };

export default function RunsWorkbench({ snapshot, refresh, mutate, busy }) {
  const runs = snapshot.studio.runs;
  const [selectedId, setSelectedId] = useState(runs[0]?.id ?? null);
  const [tab, setTab] = useState("summary");
  const [showStart, setShowStart] = useState(false);
  const [approval, setApproval] = useState(null);
  const [repair, setRepair] = useState(null);
  const selected = runs.find((run) => run.id === selectedId) ?? runs[0] ?? null;

  useEffect(() => {
    if (runs.some((run) => run.id === selectedId)) return;
    setSelectedId(runs[0]?.id ?? null);
  }, [runs, selectedId]);

  useEffect(() => {
    if (!selected || !["created", "running"].includes(selected.status)) return undefined;
    let stopped = false;
    const poll = async () => {
      try { if (!stopped) await refresh(); } finally { if (!stopped) timer = setTimeout(poll, 900); }
    };
    let timer = setTimeout(poll, 650);
    return () => { stopped = true; clearTimeout(timer); };
  }, [refresh, selected?.id, selected?.status]);

  const selectRun = (id) => { setSelectedId(id); setTab("summary"); };
  const currentFlow = selected ? snapshot.studio.flows.find((flow) => flow.id === selected.flow_id) : null;
  const pinnedFlowVersion = currentFlow?.versions.find((version) => version.id === selected?.flow_version_id);
  const requiresModel = Boolean(pinnedFlowVersion?.requires_model);

  const continueRun = async () => mutate(() => api(`/api/v1/studio/runs/${selected.id}:continue`, { method: "POST", keyMode: requiresModel ? "required" : "optional", body: {} }), { success: "Run worker resumed" });
  const cancelRun = async () => mutate(() => api(`/api/v1/studio/runs/${selected.id}:cancel`, { method: "POST", body: { actor: "studio-operator", reason: "Cancelled explicitly from the Run operations console." } }), { success: "Run cancelled with evidence" });
  const rerun = async () => {
    const child = await mutate(() => api(`/api/v1/studio/runs/${selected.id}/reruns`, { method: "POST", keyMode: requiresModel ? "required" : "optional", body: { input: selected.input, idempotency_key: commandId("rerun") } }), { success: "Linked rerun created" });
    if (child) setSelectedId(child.id);
  };

  return (
    <section className="runs-page">
      <PageHeader eyebrow="Authoritative operations console" title="Runs" description="The Run—not a trace—is the source of truth: pinned graph, Steps, receipts, model summaries, approvals, effects, diagnoses, successor repairs, and linked proof work." actions={<><Button tone="quiet" icon="redo" onClick={refresh}>Refresh</Button><Button tone="primary" icon="play" onClick={() => setShowStart(true)}>Start Run</Button></>} />
      <div className="runs-workbench">
        <aside className="run-list" aria-label="Runs">
          <header><span>{runs.length} Runs</span><Badge tone="neutral">SQLite truth</Badge></header>
          <div className="run-list-scroll">
            {runs.map((run) => {
              const flow = snapshot.studio.flows.find((item) => item.id === run.flow_id);
              return <button key={run.id} type="button" className={`run-list-item ${selected?.id === run.id ? "is-active" : ""}`} onClick={() => selectRun(run.id)}><span className={`run-state-dot tone-${STATUS_TONE[run.status] ?? "neutral"}`} /><span><strong>{flow?.name ?? "Unknown Flow"}</strong><small>{shortId(run.id)} · {formatTime(run.created_at)}</small><em>{run.relation_kind !== "root" ? `${titleCase(run.relation_kind)} · ` : ""}Flow v{run.flow_version}</em></span><StatusBadge status={run.status} /></button>;
            })}
            {!runs.length ? <EmptyState icon="run" title="No Runs yet" description="Start a Flow to pin its definition and create authoritative execution evidence." action={<Button tone="primary" icon="play" onClick={() => setShowStart(true)}>Start first Run</Button>} /> : null}
          </div>
        </aside>
        <main className="run-detail">
          {selected ? <>
            <header className="run-detail-header">
              <div><p className="panel-kicker">{currentFlow?.name ?? selected.flow_id}</p><h2>{shortId(selected.id, 13)}</h2><div className="run-meta"><StatusBadge status={selected.status} /><span>Flow v{selected.flow_version}</span><span>{selected.relation_kind}</span><span>{shortId(selected.correlation_id)}</span></div></div>
              <div className="run-actions">
                {selected.status === "created" ? <Button tone="primary" icon="play" onClick={continueRun} disabled={busy}>Continue</Button> : null}
                {["created", "running", "waiting_approval"].includes(selected.status) ? <Button tone="danger" onClick={cancelRun} disabled={busy}>Cancel</Button> : null}
                {["completed", "blocked", "failed", "cancelled"].includes(selected.status) ? <Button tone="default" icon="redo" onClick={rerun} disabled={busy}>Linked rerun</Button> : null}
              </div>
            </header>
            <RunGraph snapshot={snapshot} run={selected} onSelectChild={selectRun} />
            {selected.pending_approval ? <ApprovalCallout run={selected} onDecision={(approved) => setApproval({ request: selected.pending_approval, approved })} /> : null}
            <Segmented value={tab} onChange={setTab} label="Run evidence sections" items={[
              { value: "summary", label: "Summary" },
              { value: "steps", label: "Steps", count: selected.steps.length },
              { value: "timeline", label: "Timeline", count: selected.events.length },
              { value: "model", label: "OpenAI", count: selected.model_calls.length },
              { value: "receipts", label: "Receipts", count: selected.action_receipts.length },
              { value: "effects", label: "Effects", count: selected.effects.length },
              { value: "maintenance", label: "Maintenance", count: selected.diagnosis ? 1 : 0 }
            ]} />
            <div className="run-tab-content">
              {tab === "summary" ? <RunSummary snapshot={snapshot} run={selected} onSelectRun={selectRun} /> : null}
              {tab === "steps" ? <Steps run={selected} /> : null}
              {tab === "timeline" ? <Timeline run={selected} /> : null}
              {tab === "model" ? <ModelCalls run={selected} /> : null}
              {tab === "receipts" ? <Receipts run={selected} /> : null}
              {tab === "effects" ? <Effects run={selected} /> : null}
              {tab === "maintenance" ? <Maintenance snapshot={snapshot} run={selected} mutate={mutate} busy={busy} onRepair={setRepair} onSelectRun={selectRun} /> : null}
            </div>
          </> : <EmptyState icon="run" title="Select a Run" description="Pinned execution state and evidence will appear here." />}
        </main>
      </div>
      {showStart ? <StartRunModal snapshot={snapshot} mutate={mutate} onClose={() => setShowStart(false)} onStarted={(run) => { setShowStart(false); setSelectedId(run.id); }} /> : null}
      {approval ? <ApprovalModal run={selected} material={approval} mutate={mutate} onClose={() => setApproval(null)} /> : null}
      {repair ? <RepairModal run={selected} proposal={repair} mutate={mutate} onClose={() => setRepair(null)} /> : null}
    </section>
  );
}

function RunGraph({ snapshot, run }) {
  const fallbackLayout = new Map(
    layoutGraph(run.flow_graph.nodes, run.flow_graph.routes).map((node) => [node.id, node.position])
  );
  const nodes = run.flow_graph.nodes.map((node) => {
    const state = runNodeState(run, node.id);
    return {
      id: node.id,
      type: "runNode",
      position: node.position ?? fallbackLayout.get(node.id),
      data: {
        label: graphNodeLabel(snapshot, node),
        kind: node.type === "action" ? versionForNode(snapshot, node)?.kind : node.type,
        state,
        outcomes: nodeOutcomes(snapshot, node),
        inputs: run.flow_graph.routes.filter((route) => route.to === node.id).map((route) => `in:${route.from}:${route.outcome}`),
        attempts: run.steps.filter((step) => step.node_id === node.id).length,
        isStart: run.flow_graph.start_node_id === node.id
      }
    };
  });
  const edges = run.flow_graph.routes.map((route) => ({
    id: `${route.from}:${route.outcome}:${route.to}`,
    source: route.from,
    target: route.to,
    sourceHandle: route.outcome,
    targetHandle: `in:${route.from}:${route.outcome}`,
    label: route.outcome,
    type: "smoothstep",
    markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14 },
    animated: run.current_node_id === route.to && run.status === "running",
    className: route.outcome === "error" || route.outcome === "rejected" ? "edge-danger" : ""
  }));
  return <div className="run-graph"><ReactFlow nodes={nodes} edges={edges} nodeTypes={RUN_NODE_TYPES} nodesDraggable={false} nodesConnectable={false} elementsSelectable fitView fitViewOptions={{ padding: .3 }} minZoom={.2} maxZoom={1.4} proOptions={{ hideAttribution: true }}><Background gap={22} size={1} color="#272b32" /><Controls showInteractive={false} position="bottom-left" /><MiniMap pannable zoomable position="bottom-right" maskColor="rgba(8,10,13,.78)" nodeColor={(node) => stateColor(node.data.state)} /></ReactFlow><div className="run-graph-label"><Badge tone="neutral"><Icon name="lock" size={12} />Pinned Flow v{run.flow_version}</Badge><LedgerState run={run} /></div></div>;
}

function RunGraphNode({ data }) {
  const outcomes = data.outcomes ?? [];
  return <article className={`run-graph-node state-${data.state}`} style={{ minHeight: Math.max(108, 64 + outcomes.length * 22) }}><header><span className={`run-node-light tone-${STATUS_TONE[data.state] ?? "neutral"}`} /><div><strong>{data.label}</strong><small>{titleCase(data.kind)}{data.attempts ? ` · ${data.attempts} attempt${data.attempts === 1 ? "" : "s"}` : ""}</small></div>{data.isStart ? <Badge tone="neutral">start</Badge> : null}</header><StatusBadge status={data.state === "idle" ? "created" : data.state} />{(data.inputs.length ? data.inputs : ["in:default"]).map((id, index) => <Handle key={id} type="target" id={id} position={Position.Left} style={{ top: 58 + index * 22 }} className="kyn-handle target-handle" />)}{outcomes.map((outcome, index) => <Handle key={outcome.id} type="source" id={outcome.id} position={Position.Right} style={{ top: 58 + index * 22 }} className={`kyn-handle source-handle tone-${outcome.tone}`} />)}</article>;
}

function LedgerState({ run }) {
  const valid = run.events.every((event, index) => index === 0 || event.prev_hash === run.events[index - 1].event_hash) && run.events.every((event, index) => event.sequence === index + 1);
  return <Badge tone={valid ? "success" : "danger"}><Icon name={valid ? "check" : "warning"} size={12} />{valid ? `${run.events.length} hash-linked events` : "Ledger mismatch"}</Badge>;
}

function ApprovalCallout({ run, onDecision }) {
  const request = run.pending_approval;
  return <section className="approval-callout"><span className="approval-icon"><Icon name="lock" size={22} /></span><div><p className="panel-kicker">Human gate · Step {shortId(request.step_id)}</p><h3>{request.message}</h3><p>The Run is durably paused. No downstream capability or effect runs until a named human records a reason.</p></div><div><Button tone="danger" onClick={() => onDecision(false)}>Reject</Button><Button tone="primary" icon="check" onClick={() => onDecision(true)}>Approve and resume</Button></div></section>;
}

function RunSummary({ snapshot, run, onSelectRun }) {
  const flow = snapshot.studio.flows.find((item) => item.id === run.flow_id);
  return <div className="run-summary-grid"><section className="evidence-card"><header><h3>Execution contract</h3><Badge tone="neutral">immutable</Badge></header><dl><div><dt>Flow</dt><dd>{flow?.name ?? run.flow_id}</dd></div><div><dt>Version</dt><dd>v{run.flow_version}</dd></div><div><dt>Fingerprint</dt><dd><code>{run.flow_fingerprint.slice(0, 24)}…</code></dd></div><div><dt>Outcome</dt><dd>{run.outcome ?? "pending"}</dd></div><div><dt>Correlation</dt><dd><code>{shortId(run.correlation_id, 14)}</code></dd></div><div><dt>Relation</dt><dd>{titleCase(run.relation_kind)}</dd></div></dl></section><section className="evidence-card"><header><h3>Evidence inventory</h3><Badge tone="success">durable</Badge></header><div className="evidence-metrics"><article><strong>{run.steps.length}</strong><span>Steps</span></article><article><strong>{run.events.length}</strong><span>Events</span></article><article><strong>{run.model_calls.length}</strong><span>Model calls</span></article><article><strong>{run.action_receipts.length}</strong><span>Receipts</span></article><article><strong>{run.effects.length}</strong><span>Effects</span></article><article><strong>{run.children.length}</strong><span>Children</span></article></div></section><section className="evidence-card span-two"><header><h3>Input and output</h3><span>{run.finished_at ? `Finished ${formatTime(run.finished_at)}` : `Started ${formatTime(run.started_at ?? run.created_at)}`}</span></header><div className="io-grid"><div><p className="panel-kicker">Validated input</p><KeyValue data={run.input} /></div><div><p className="panel-kicker">Current output</p><KeyValue data={run.output ?? { status: run.status }} /></div></div></section>{run.error_code ? <section className="evidence-card error-card span-two"><header><h3><Icon name="warning" size={17} />Run failure</h3><Badge tone="danger">{run.error_code}</Badge></header><p>{run.error_message}</p></section> : null}{run.parent_run_id || run.children.length ? <section className="evidence-card span-two"><header><h3>Run lineage</h3><Badge tone="ai">{run.relation_kind}</Badge></header><div className="lineage-list">{run.parent_run_id ? <button type="button" onClick={() => onSelectRun(run.parent_run_id)}><Icon name="undo" size={16} /><span><strong>Parent Run</strong><small>{shortId(run.parent_run_id, 14)}</small></span><Badge tone="neutral">open</Badge></button> : null}{run.children.map((child) => <button key={child.id} type="button" onClick={() => onSelectRun(child.id)}><Icon name="redo" size={16} /><span><strong>{titleCase(child.relation_kind)} child</strong><small>{shortId(child.id, 14)}</small></span><StatusBadge status={child.status} /></button>)}</div></section> : null}</div>;
}

function Steps({ run }) {
  if (!run.steps.length) return <EmptyState icon="run" title="No Step has started" description="The Run is pinned and queued, but the worker has not invoked a node." />;
  return <div className="step-list">{run.steps.map((step, index) => <article key={step.id}><div className="step-rail"><span>{index + 1}</span><i /></div><div className="step-body"><header><div><strong>{step.node_id}</strong><small>{titleCase(step.node_type)} · attempt {step.attempt} · {shortId(step.target_version_id)}</small></div><StatusBadge status={step.status} /></header><dl><div><dt>Started</dt><dd>{formatTime(step.started_at)}</dd></div><div><dt>Finished</dt><dd>{formatTime(step.finished_at)}</dd></div><div><dt>Outcome</dt><dd>{step.route_outcome ?? "—"}</dd></div></dl><details><summary>Input / output</summary><div className="io-grid"><KeyValue data={step.input} /><KeyValue data={step.output} /></div></details>{step.error_code ? <p className="step-error"><Icon name="warning" size={15} />{step.error_code}: {step.error_message}</p> : null}</div></article>)}</div>;
}

function Timeline({ run }) {
  return <div className="timeline-list">{run.events.map((event) => <article key={event.id}><span>{String(event.sequence).padStart(2, "0")}</span><i /><div><header><strong>{event.type}</strong><time>{formatTime(event.occurred_at)}</time></header><p>{event.actor_type}{event.actor_id ? ` · ${shortId(event.actor_id)}` : ""}</p><details><summary>Evidence payload and hash</summary><KeyValue data={event.payload} /><code>{event.event_hash}</code></details></div></article>)}</div>;
}

function ModelCalls({ run }) {
  if (!run.model_calls.length) return <EmptyState icon="agent" title="No OpenAI call" description="This Run path was deterministic or has not reached a model-backed capability." />;
  return <div className="evidence-table model-table"><header><span>Response</span><span>Model</span><span>Status</span><span>Usage</span><span>Created</span></header>{run.model_calls.map((call) => <article key={call.id}><code>{shortId(call.provider_response_id ?? call.id)}</code><strong>{call.model}</strong><Badge tone={call.status === "completed" ? "success" : "danger"}>{call.status}</Badge><span>{call.usage?.total_tokens ?? 0} tokens</span><time>{formatTime(call.created_at)}</time><details><summary>Safe response summary</summary><KeyValue data={call.response_summary ?? call} /></details></article>)}</div>;
}

function Receipts({ run }) {
  if (!run.action_receipts.length) return <EmptyState icon="action" title="No Action receipt" description="No Action invocation has completed on this path." />;
  return <div className="receipt-grid">{run.action_receipts.map((receipt) => <article key={receipt.id}><header><span className={`resource-avatar avatar-${receipt.outcome}`}><Icon name="action" size={16} /></span><div><strong>{receipt.node_id}</strong><small>{shortId(receipt.action_version_id)} · attempt {receipt.attempt}</small></div><Badge tone={receipt.outcome === "succeeded" ? "success" : receipt.outcome === "waiting_approval" ? "warning" : "danger"}>{receipt.outcome}</Badge></header><dl><div><dt>Receipt</dt><dd><code>{shortId(receipt.id, 13)}</code></dd></div><div><dt>Idempotency</dt><dd><code>{receipt.idempotency_key.slice(0, 18)}…</code></dd></div><div><dt>Error</dt><dd>{receipt.error_code ?? "—"}</dd></div></dl><details><summary>Validated input and result</summary><div className="io-grid"><KeyValue data={receipt.input} /><KeyValue data={receipt.output} /></div></details></article>)}</div>;
}

function Effects({ run }) {
  if (!run.effects.length) return <EmptyState icon="lock" title="No committed effect" description="The Run has not written to its isolated, bounded workspace store." />;
  return <div className="receipt-grid">{run.effects.map((effect) => <article key={effect.id}><header><span className="resource-avatar avatar-data_store"><Icon name="lock" size={16} /></span><div><strong>{effect.collection}</strong><small>Bounded SQLite effect</small></div><Badge tone="success">committed</Badge></header><dl><div><dt>Effect</dt><dd><code>{shortId(effect.id, 13)}</code></dd></div><div><dt>Action pin</dt><dd><code>{shortId(effect.action_version_id)}</code></dd></div></dl><KeyValue data={effect.payload} /></article>)}</div>;
}

function Maintenance({ snapshot, run, mutate, busy, onRepair, onSelectRun }) {
  const diagnosis = run.diagnosis;
  const proposal = run.repair;
  const proofRun = snapshot.studio.runs.find((candidate) =>
    candidate.parent_run_id === run.id &&
    candidate.relation_kind === "proof" &&
    candidate.flow_version === proposal?.applied_flow_version
  );
  const diagnose = () => mutate(() => api(`/api/v1/studio/runs/${run.id}/diagnoses`, { method: "POST", keyMode: "required", body: {} }), { success: "Evidence-bound diagnosis recorded" });
  const propose = () => mutate(() => api(`/api/v1/studio/diagnoses/${diagnosis.id}/repairs`, { method: "POST", body: {} }), { success: "Bounded successor patch proposed" });
  const prove = async () => {
    const child = await mutate(() => api(`/api/v1/studio/repairs/${proposal.id}/proof`, { method: "POST", keyMode: "optional", body: { input: run.input, idempotency_key: commandId("repair-proof") } }), { success: "Linked proof Run completed" });
    if (child) onSelectRun(child.id);
  };
  if (!["blocked", "failed"].includes(run.status) && !diagnosis) return <EmptyState icon="check" title="No maintenance required" description="Maintenance becomes available for failed or authority-blocked Runs. Definitions are never patched in place." />;
  return <div className="maintenance-flow"><header><div><p className="panel-kicker">Forward recovery</p><h3>Evidence → diagnosis → successor → proof</h3><p>The failed Run stays failed. Recovery creates new immutable definitions and a linked child Run.</p></div><div className="maintenance-stages"><span className={diagnosis ? "is-complete" : "is-current"}>1 Diagnose</span><i /><span className={proposal ? "is-complete" : diagnosis ? "is-current" : ""}>2 Propose</span><i /><span className={proposal?.status === "applied" ? "is-complete" : proposal ? "is-current" : ""}>3 Approve</span><i /><span className={proofRun ? "is-complete" : proposal?.status === "applied" ? "is-current" : ""}>4 Prove</span></div></header>{!diagnosis ? <section className="maintenance-card"><span className="stage-number">01</span><div><h4>Diagnose from owned evidence</h4><p>A pinned diagnostician can explain only a code-owned causal candidate and must cite event IDs from this Run.</p></div><Button tone="primary" onClick={diagnose} disabled={busy}>Diagnose Run</Button></section> : <section className="maintenance-card is-complete"><span className="stage-number">01</span><div><h4>{diagnosis.root_cause}</h4><p>{diagnosis.explanation}</p><div className="citation-list">{diagnosis.evidence_event_ids.map((id) => <code key={id}>{shortId(id)}</code>)}</div></div><Badge tone="success">{Math.round(diagnosis.confidence * 100)}% grounded</Badge></section>}{diagnosis && !proposal ? <section className="maintenance-card"><span className="stage-number">02</span><div><h4>Construct allowlisted successor</h4><p>The deterministic repair policy can propose only a bounded path and value. It cannot widen its own authority.</p></div><Button tone="primary" onClick={propose} disabled={busy}>Generate proposal</Button></section> : null}{proposal ? <section className={`maintenance-card ${proposal.status === "applied" ? "is-complete" : ""}`}><span className="stage-number">02</span><div><h4>Successor patch</h4><KeyValue data={proposal.patch} /><p>Proposal hash <code>{proposal.proposal_hash.slice(0, 24)}…</code></p></div>{proposal.status === "proposed" ? <Button tone="primary" onClick={() => onRepair(proposal)}>Review and apply</Button> : <Badge tone="success">Applied</Badge>}</section> : null}{proposal?.status === "applied" ? <section className={`maintenance-card ${proofRun ? "is-complete" : ""}`}><span className="stage-number">04</span><div><h4>Prove the changed outcome</h4><p>Execute the successor in a linked child. Parent evidence and effects remain untouched.</p></div>{proofRun ? <Button tone="quiet" icon="run" onClick={() => onSelectRun(proofRun.id)}>Open proof Run</Button> : <Button tone="primary" icon="play" onClick={prove} disabled={busy}>Run proof</Button>}</section> : null}</div>;
}

function StartRunModal({ snapshot, mutate, onClose, onStarted }) {
  const flows = snapshot.studio.flows;
  const [flowId, setFlowId] = useState(flows[0]?.id ?? "");
  const flow = flows.find((item) => item.id === flowId) ?? flows[0];
  const [input, setInput] = useState(() => JSON.stringify(exampleForSchema(flow?.version.input_schema), null, 2));
  useEffect(() => setInput(JSON.stringify(exampleForSchema(flow?.version.input_schema), null, 2)), [flow?.id]);
  const submit = async (event) => {
    event.preventDefault();
    let payload;
    try { payload = parseJson(input, "Run input"); } catch (error) { await mutate(() => Promise.reject(error), { refreshAfter: false, success: "" }); return; }
    const run = await mutate(() => api(`/api/v1/studio/flows/${flow.id}/runs:enqueue`, { method: "POST", keyMode: flow.version.requires_model ? "required" : "optional", body: { input: payload, idempotency_key: commandId("manual-run") } }), { success: "Run pinned and queued" });
    if (run) onStarted(run);
  };
  return <Modal title="Start a Run" description="Choose a published Flow. The runtime pins the complete transitive definition before any provider call." onClose={onClose}><form className="modal-form" onSubmit={submit}><Field label="Flow"><select value={flowId} onChange={(event) => setFlowId(event.target.value)}>{flows.map((item) => <option key={item.id} value={item.id}>{item.name} · v{item.current_version}{item.version.requires_model ? " · OpenAI" : " · deterministic"}</option>)}</select></Field>{flow ? <div className="flow-run-summary"><span><strong>{flow.version.nodes.length}</strong> nodes</span><span><strong>{flow.version.routes.length}</strong> routes</span><span><strong>{flow.version.outcomes.length}</strong> outcomes</span><span><strong>{flow.version.requires_model ? "OpenAI" : "None"}</strong> model transport</span></div> : null}<JsonField label="Run input" value={input} onChange={setInput} rows={12} hint="Validated against the published Flow input schema." /><div className="modal-actions"><Button tone="quiet" type="button" onClick={onClose}>Cancel</Button><Button tone="primary" icon="play" type="submit">Pin and start</Button></div></form></Modal>;
}

function ApprovalModal({ run, material, mutate, onClose }) {
  const [actor, setActor] = useState("build-week-operator");
  const [reason, setReason] = useState(material.approved ? "I reviewed the pinned context and authorize this bounded continuation." : "The pinned context does not justify the requested continuation.");
  const submit = async (event) => { event.preventDefault(); const result = await mutate(() => api(`/api/v1/studio/approvals/${material.request.id}/decisions`, { method: "POST", keyMode: "optional", body: { approved: material.approved, actor, reason } }), { success: material.approved ? "Approval recorded; Run resumed" : "Rejection recorded; Run blocked" }); if (result) onClose(); };
  return <Modal title={material.approved ? "Approve and resume" : "Reject this continuation"} description={`Decision for ${shortId(run.id)} · ${material.request.node_id}`} onClose={onClose}><form className="modal-form" onSubmit={submit}><div className={`decision-summary ${material.approved ? "is-approved" : "is-rejected"}`}><Icon name={material.approved ? "check" : "warning"} size={21} /><div><strong>{material.request.message}</strong><p>The actor and reason become append-only Run evidence.</p></div></div><Field label="Actor"><input required value={actor} onChange={(event) => setActor(event.target.value)} /></Field><Field label="Decision reason" hint="Minimum 12 characters"><textarea required minLength="12" rows="5" value={reason} onChange={(event) => setReason(event.target.value)} /></Field><div className="modal-actions"><Button tone="quiet" type="button" onClick={onClose}>Cancel</Button><Button tone={material.approved ? "primary" : "danger"} type="submit">Record {material.approved ? "approval" : "rejection"}</Button></div></form></Modal>;
}

function RepairModal({ run, proposal, mutate, onClose }) {
  const [actor, setActor] = useState("workflow-maintainer");
  const [reason, setReason] = useState("The cited authority denial proves this exact bounded successor is required.");
  const [acknowledged, setAcknowledged] = useState(false);
  const submit = async (event) => { event.preventDefault(); const result = await mutate(() => api(`/api/v1/studio/repairs/${proposal.id}/apply`, { method: "POST", body: { proposal_hash: proposal.proposal_hash, expected_flow_revision: proposal.expected_flow_revision, expected_action_version: proposal.expected_action_version, actor, reason, acknowledged } }), { success: "Successor Action and Flow versions published" }); if (result) onClose(); };
  return <Modal title="Review bounded repair" description="This publishes successors. It never edits the failed Run or its pinned definitions." onClose={onClose}><form className="modal-form" onSubmit={submit}><div className="repair-review"><p className="panel-kicker">Allowlisted patch</p><KeyValue data={proposal.patch} /><dl><div><dt>Expected Flow revision</dt><dd>{proposal.expected_flow_revision}</dd></div><div><dt>Expected Action version</dt><dd>{proposal.expected_action_version}</dd></div><div><dt>Proposal hash</dt><dd><code>{proposal.proposal_hash.slice(0, 24)}…</code></dd></div></dl></div><Field label="Maintainer"><input required value={actor} onChange={(event) => setActor(event.target.value)} /></Field><Field label="Reason"><textarea required minLength="20" rows="4" value={reason} onChange={(event) => setReason(event.target.value)} /></Field><label className="check-row"><input type="checkbox" required checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} /><span><strong>I approve this exact successor patch</strong><small>The failed Run remains unchanged; proof requires a linked child Run.</small></span></label><div className="modal-actions"><Button tone="quiet" type="button" onClick={onClose}>Cancel</Button><Button tone="primary" icon="save" type="submit" disabled={!acknowledged}>Publish successors</Button></div></form></Modal>;
}

function stateColor(state) { return { completed: "#c9ff73", running: "#a892ff", waiting_approval: "#f6ca6a", blocked: "#ff8d72", failed: "#ff8d72" }[state] ?? "#657080"; }
