import React, { useEffect, useId, useMemo, useRef, useState } from "react";
import { useThemeTokens } from "../theme.js";
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  useEdgesState,
  useNodesState
} from "@xyflow/react";
import { api, commandId } from "../api.js";
import { Icon } from "../icons.jsx";
import {
  STATUS_TONE,
  completionAdjudication,
  exampleForSchema,
  formatTime,
  graphNodeLabel,
  layoutGraph,
  nodeOutcomes,
  parseJson,
  resourceForNode,
  runListRows,
  runNodeState,
  shortId,
  titleCase,
  topLevelRuns,
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
  KeyValue,
  Modal,
  PageHeader,
  Segmented,
  StatusBadge
} from "./ui.jsx";

const RUN_NODE_TYPES = { runNode: RunGraphNode };
const RATIFICATION_ORDER = ["proposed", "confirmed", "canonical"];
const RATIFICATION_TONE = { proposed: "neutral", confirmed: "warning", canonical: "danger" };
const RATIFICATION_MEANING = {
  proposed: "One Run reproduced this exact approach. It is recorded and it does not brake — an honest second attempt is never refused.",
  confirmed: "Two independent Runs reproduced it. It still does not brake; one further independent reproduction ratifies it.",
  canonical: "Three independent Runs reproduced it. This pinned Flow version is now refused before any Run row exists — which branch a Run would take is not knowable before it runs, so the version is the scope. Publishing a successor changes the fingerprint and clears the brake."
};

// The refusal codes and their prose belong to the resolver, and the payload
// carries that prose with every discarded anchor, so nothing here restates a
// reason. These are scan labels over the server's own sentences — plus the one
// distinction inside them a reader must not miss: five codes are the judge
// pointing at something the runtime would not take, and one is the runtime
// admitting it could not attribute a record it holds.
const ANCHOR_REFUSAL_LABEL = {
  anchor_unresolvable: "No such record",
  anchor_foreign_run: "Another Run's record",
  anchor_kind_inadmissible: "Not the declared kind",
  anchor_node_mismatch: "Not a declared site",
  anchor_node_unattributable: "This runtime could not attribute it",
  anchor_state_mismatch: "State cannot carry the claim"
};
// Blame is assigned code by code and never by default. A refusal code this
// console has not been taught renders neutrally on the resolver's own reason,
// because guessing whose fault it is would be the exact mis-attribution this
// panel exists to expose.
const ANCHOR_FAULT = {
  anchor_unresolvable: "judge",
  anchor_foreign_run: "judge",
  anchor_kind_inadmissible: "judge",
  anchor_node_mismatch: "judge",
  anchor_state_mismatch: "judge",
  anchor_node_unattributable: "runtime"
};
const ANCHOR_FAULT_LABEL = { judge: "Judge claim refused", runtime: "Runtime defect", unclassified: "Anchor refused" };
// A judge claim is refused with the same tinted badge every other refusal in
// this product wears. The runtime fault deliberately wears none: it sits on the
// alarm surface with its own mark, because a badge on that surface stacks two
// tints and, measured, drops its own label to 4.32:1 on light.
const ANCHOR_FAULT_TONE = { judge: "warning" };
// Only the runtime fault carries a second paragraph, and the asymmetry is the
// point: a refused judge claim is the ordinary case, stated once above the list
// so five identical restatements do not bury the one refusal that is ours.
const ANCHOR_FAULT_MEANING = {
  runtime: "This one is not judge noise. Attributing a record to the node that minted it is this runtime's job, and it failed: a record that may well evidence the claim could never be matched against a declared site. Read it as a defect report against this system, not against the judge."
};
// One code names something an operator can go and look at, so it says so. The
// rest are answered in full by the reason the resolver already published.
const ANCHOR_REFUSAL_ACT = {
  anchor_node_mismatch: "Read the pinned Flow at the declared sites above: they are the only nodes whose work this criterion accepts."
};

export default function RunsWorkbench({ snapshot, refresh, mutate, busy, focusRunId = null }) {
  const runs = snapshot.studio.runs;
  const orchestrations = useMemo(() => topLevelRuns(runs), [runs]);
  const listRows = useMemo(() => runListRows(runs), [runs]);
  const [selectedId, setSelectedId] = useState(orchestrations[0]?.id ?? runs[0]?.id ?? null);
  const [tab, setTab] = useState("summary");
  const [showStart, setShowStart] = useState(false);
  const [approval, setApproval] = useState(null);
  const [repair, setRepair] = useState(null);
  const honouredFocus = useRef(null);
  const selected = runs.find((run) => run.id === selectedId) ?? runs[0] ?? null;

  useEffect(() => {
    if (runs.some((run) => run.id === selectedId)) return;
    setSelectedId(orchestrations[0]?.id ?? runs[0]?.id ?? null);
  }, [orchestrations, runs, selectedId]);

  // A citation elsewhere in the product asked for one exact Run. Honour it once
  // per request: the polling refresh re-runs this effect every 900 ms, and a
  // request that re-applied itself would fight the operator's own selection.
  useEffect(() => {
    if (!focusRunId || honouredFocus.current === focusRunId) return;
    if (!runs.some((run) => run.id === focusRunId)) return;
    honouredFocus.current = focusRunId;
    setSelectedId(focusRunId);
    setTab("summary");
  }, [focusRunId, runs]);

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
  const adjudication = completionAdjudication(selected);
  const requiresModel = Boolean(pinnedFlowVersion?.requires_model);

  const continueRun = async () => mutate(() => api(`/api/v1/studio/runs/${selected.id}:continue`, { method: "POST", keyMode: requiresModel ? "required" : "optional", body: {} }), { success: "Run worker resumed" });
  const cancelRun = async () => mutate(() => api(`/api/v1/studio/runs/${selected.id}:cancel`, { method: "POST", body: { actor: "studio-operator", reason: "Cancelled explicitly from the Run operations console." } }), { success: "Run cancelled with evidence" });
  const rerun = async () => {
    const child = await mutate(() => api(`/api/v1/studio/runs/${selected.id}/reruns`, { method: "POST", keyMode: requiresModel ? "required" : "optional", body: { input: selected.input, idempotency_key: commandId("rerun") } }), { success: "Linked rerun created" });
    if (child) setSelectedId(child.id);
  };

  return (
    <section className="runs-page">
      <PageHeader eyebrow="Authoritative operations console" title="Runs" description="One start creates one top-level orchestration. Reusable Subflows retain their own linked execution evidence inside it: pinned graph, Steps, receipts, model summaries, approvals, effects, diagnoses, repairs, and proofs." actions={<><Button tone="quiet" icon="redo" onClick={refresh}>Refresh</Button><Button tone="primary" icon="play" onClick={() => setShowStart(true)}>Start Run</Button></>} />
      <div className="runs-workbench">
        <aside className="run-list" aria-label="Runs">
          <header><span><strong>{orchestrations.length} {orchestrations.length === 1 ? "Orchestration" : "Orchestrations"}</strong><small>{runs.length} durable execution {runs.length === 1 ? "record" : "records"}</small></span><Badge tone="neutral">SQLite truth</Badge></header>
          <div className="run-list-scroll">
            {listRows.map(({ run, depth }) => {
              const flow = snapshot.studio.flows.find((item) => item.id === run.flow_id);
              const linked = depth > 0;
              const childCount = run.children?.length ?? 0;
              return <button key={run.id} type="button" style={{ "--run-depth": Math.min(depth, 4) }} className={`run-list-item ${linked ? "is-linked" : "is-orchestration"} ${selected?.id === run.id ? "is-active" : ""}`} aria-label={`${linked ? `${titleCase(run.relation_kind)} execution` : "Orchestration"}: ${flow?.name ?? "Unknown Flow"}`} onClick={() => selectRun(run.id)}><span className={`run-state-dot tone-${STATUS_TONE[run.status] ?? "neutral"}`} /><span><strong>{flow?.name ?? "Unknown Flow"}</strong><small>{shortId(run.id)} · {formatTime(run.created_at)}</small><em>{linked ? `↳ ${titleCase(run.relation_kind)} execution` : `Orchestration · ${childCount} linked`} · Flow v{run.flow_version}</em></span><StatusBadge status={run.status} /></button>;
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
            {selected.dead_ends?.length ? <DeadEndCallout run={selected} onSelectRun={selectRun} /> : null}
            {adjudication ? <CompletionAdjudication run={selected} adjudication={adjudication} /> : null}
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

function reconcileGraph(previous, next) {
  const existing = new Map(previous.map((item) => [item.id, item]));
  const merged = next.map((item) => {
    const prior = existing.get(item.id);
    if (!prior) return item;
    return Object.keys(item).every((key) => JSON.stringify(prior[key]) === JSON.stringify(item[key])) ? prior : { ...prior, ...item };
  });
  return merged.length === previous.length && merged.every((item, index) => item === previous[index]) ? previous : merged;
}

function RunGraph({ snapshot, run }) {
  const derivedNodes = useMemo(() => {
    const fallbackLayout = new Map(
      layoutGraph(run.flow_graph.nodes, run.flow_graph.routes).map((node) => [node.id, node.position])
    );
    return run.flow_graph.nodes.map((node) => ({
      id: node.id,
      type: "runNode",
      position: node.position ?? fallbackLayout.get(node.id),
      data: {
        label: graphNodeLabel(snapshot, node),
        kind: node.type === "action" ? versionForNode(snapshot, node)?.kind : node.type,
        state: runNodeState(run, node.id),
        outcomes: nodeOutcomes(snapshot, node),
        inputs: run.flow_graph.routes.filter((route) => route.to === node.id).map((route) => `in:${route.from}:${route.outcome}`),
        attempts: run.steps.filter((step) => step.node_id === node.id).length,
        isStart: run.flow_graph.start_node_id === node.id
      }
    }));
  }, [snapshot, run]);
  const derivedEdges = useMemo(() => run.flow_graph.routes.map((route) => ({
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
  })), [run.flow_graph.routes, run.current_node_id, run.status]);
  const graph = useThemeTokens(RUN_GRAPH_TOKENS);
  const [nodes, setNodes, onNodesChange] = useNodesState(derivedNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(derivedEdges);
  useEffect(() => { setNodes((previous) => reconcileGraph(previous, derivedNodes)); }, [derivedNodes, setNodes]);
  useEffect(() => { setEdges((previous) => reconcileGraph(previous, derivedEdges)); }, [derivedEdges, setEdges]);
  return <div className="run-graph"><ReactFlow nodes={nodes} edges={edges} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} nodeTypes={RUN_NODE_TYPES} nodesDraggable={false} nodesConnectable={false} elementsSelectable fitView fitViewOptions={{ padding: .3 }} minZoom={.2} maxZoom={1.4} proOptions={{ hideAttribution: true }}><Background gap={22} size={1} color={graph["graph-dot"]} /><Controls showInteractive={false} position="bottom-left" /><MiniMap pannable zoomable position="bottom-right" maskColor={graph["minimap-mask"]} nodeColor={(node) => graph[stateColor(node.data.state)]} /></ReactFlow><div className="run-graph-label"><Badge tone="neutral"><Icon name="lock" size={12} />Pinned Flow v{run.flow_version}</Badge><LedgerState run={run} /></div></div>;
}

function RunGraphNode({ data }) {
  const outcomes = data.outcomes ?? [];
  return <article className={`run-graph-node state-${data.state}`} style={{ minHeight: Math.max(120, 80 + outcomes.length * 24) }}><header><span className={`run-node-light tone-${STATUS_TONE[data.state] ?? "neutral"}`} /><div><strong>{data.label}</strong><small>{titleCase(data.kind)}{data.attempts ? ` · ${data.attempts} attempt${data.attempts === 1 ? "" : "s"}` : ""}</small></div>{data.isStart ? <Badge tone="neutral">start</Badge> : null}</header><StatusBadge status={data.state === "idle" ? "created" : data.state} />{(data.inputs.length ? data.inputs : ["in:default"]).map((id, index) => <Handle key={id} type="target" id={id} position={Position.Left} style={{ top: 66 + index * 24 }} className="kyn-handle target-handle" />)}{outcomes.map((outcome, index) => <Handle key={outcome.id} type="source" id={outcome.id} position={Position.Right} style={{ top: 66 + index * 24 }} className={`kyn-handle source-handle tone-${outcome.tone}`} />)}</article>;
}

function LedgerState({ run }) {
  // The server recomputes every event hash from its material; the browser can
  // only see that the links join up, which a rewritten payload would survive.
  const linked = run.events.every((event, index) => (index === 0 || event.prev_hash === run.events[index - 1].event_hash) && event.sequence === index + 1);
  const valid = run.ledger_verified === undefined ? linked : run.ledger_verified && linked;
  return <Badge tone={valid ? "success" : "danger"} title={valid ? "Every event hash recomputed from its material and re-linked" : "The recorded chain does not match its material"}><Icon name={valid ? "check" : "warning"} size={12} />{valid ? `${run.events.length} verified events` : "Ledger mismatch"}</Badge>;
}

function approvalMessageParts(message) {
  const text = String(message ?? "").trim();
  const match = text.match(/^(.+?[.!?])(?:\s+)([\s\S]+)$/);
  return match ? { headline: match[1], detail: match[2] } : { headline: text, detail: "" };
}

function ApprovalRationale({ detail }) {
  if (!detail) return null;
  return <details className="approval-rationale"><summary>Read the complete request rationale</summary><p>{detail}</p></details>;
}

function ApprovalCallout({ run, onDecision }) {
  const request = run.pending_approval;
  const copy = approvalMessageParts(request.message);
  return <section className="approval-callout"><span className="approval-icon"><Icon name="lock" size={22} /></span><div><p className="panel-kicker">Human gate · Step {shortId(request.step_id)}</p><h3>{copy.headline}</h3><p>The Run is durably paused. No downstream capability or effect runs until a named human records a reason.</p><ApprovalRationale detail={copy.detail} /></div><div className="approval-actions"><Button tone="danger" onClick={() => onDecision(false)}>Reject</Button><Button tone="primary" icon="check" onClick={() => onDecision(true)}>Approve and resume</Button></div></section>;
}

function deadEndTone(state) { return RATIFICATION_TONE[state] ?? "neutral"; }

function DeadEndCallout({ run, onSelectRun }) {
  const records = run.dead_ends;
  const strongest = RATIFICATION_ORDER[records.reduce((worst, record) => Math.max(worst, RATIFICATION_ORDER.indexOf(record.ratification_state)), 0)];
  return <section className={`dead-end-callout tone-${deadEndTone(strongest)}`} aria-labelledby="dead-end-title">
    <header><span className="dead-end-icon"><Icon name="warning" size={22} /></span><div><p className="panel-kicker">Ratification brake · derived evidence</p><h3 id="dead-end-title">{records.length} dead end{records.length === 1 ? "" : "s"} cite{records.length === 1 ? "s" : ""} this Run</h3><p>A dead end is not a log line. It is a fingerprint over one exact failed approach — pinned Flow version, node, error code, normalized detail — and it VETOES that path. Its ratification state is never stored: it is recomputed by counting the <em>distinct</em> Runs that independently reproduced it, so repetition ratifies it and nothing else can.</p></div><Badge tone={deadEndTone(strongest)} dot>{titleCase(strongest)}</Badge></header>
    <ol className="dead-end-list">{records.map((record) => <li key={record.fingerprint}>
      <header><Badge tone={deadEndTone(record.ratification_state)} dot>{titleCase(record.ratification_state)}</Badge><strong>{record.node_id}</strong><code>{record.error_code}</code><span className="dead-end-count"><b>{record.distinct_runs}</b> distinct Run{record.distinct_runs === 1 ? "" : "s"}</span></header>
      <p>{RATIFICATION_MEANING[record.ratification_state]}</p>
      <DefinitionList items={[
        ["Fingerprint", <code key="fingerprint">{record.fingerprint.slice(0, 24)}…</code>],
        ["Pinned Flow version", <code key="flow-version">{shortId(record.flow_version_id, 14)}</code>],
        ["Normalized detail", record.normalized_detail],
        ["First cited", formatTime(record.first_cited_at)],
        ["Last cited", formatTime(record.last_cited_at)]
      ]} />
      <CitedRuns label={`Citing Runs · ${record.citing_run_ids.length}`} ids={record.citing_run_ids} currentRunId={run.id} onSelectRun={onSelectRun} />
    </li>)}</ol>
  </section>;
}

/** The stop-seam verdict, and above all the anchors the runtime discarded.
 *
 * The panel exists for the discarded anchors. A judge that approves a
 * completion proves nothing on its own, and the only way to see that in the
 * product is to watch the runtime cite the judge's own anchors back and refuse
 * them — so a discarded anchor gets the same card weight as a criterion, in
 * both directions: an admitted completion that discarded anchors on the way is
 * exactly as interesting as a refused one.
 *
 * Whether a criterion holds is read off the event, never recomputed. The
 * server resolved it against records the browser cannot see.
 */
function CompletionAdjudication({ run, adjudication }) {
  const criteria = adjudication.criteria ?? [];
  const unevidenced = adjudication.unevidenced ?? [];
  const judgeClaim = adjudication.judge_claim ?? null;
  const claimsByCriterion = new Map((judgeClaim?.criteria ?? []).map((claim) => [claim.criterion_id, claim]));
  const discarded = criteria.reduce((total, criterion) => total + (criterion.discarded?.length ?? 0), 0);
  const tone = adjudication.admitted ? "success" : "danger";
  return <section className={`completion-callout tone-${tone}`} aria-labelledby="completion-adjudication-title">
    <header>
      <span className="completion-icon"><Icon name={adjudication.admitted ? "check" : "lock"} size={22} /></span>
      <div>
        <p className="panel-kicker">Stop seam · {criteria.length} declared acceptance criteri{criteria.length === 1 ? "on" : "a"}</p>
        <h3 id="completion-adjudication-title">{adjudication.admitted ? "Completion admitted on resolved evidence" : `Completion refused · ${unevidenced.length} declared promise${unevidenced.length === 1 ? "" : "s"} went unevidenced`}</h3>
        {adjudication.admitted
          ? <p>A judge said this work was finished, and that sentence admitted nothing. Every declared criterion holds at least one anchor the runtime resolved independently against this Run's own records. {discarded ? <>{discarded} anchor{discarded === 1 ? " it cited was" : "s it cited were"} still discarded on the way — those are below, and they are the point.</> : "Every anchor it cited resolved."}</p>
          : <p><strong>Nothing crashed.</strong> The pinned Flow version declares what finishing means here, and {unevidenced.map((id, index) => <span key={id}>{index ? ", " : ""}<code>{id}</code></span>)} carried no anchor this runtime would accept — so the work was stopped rather than called finished, and every Step, receipt and effect it did produce is still below, untouched.{run.error_code ? <> The Run records <code>{run.error_code}</code>.</> : null}</p>}
      </div>
      <Badge tone={tone} dot>{adjudication.admitted ? "Admitted" : "Refused"}</Badge>
    </header>
    {judgeClaim ? <aside className="judge-claim">
      <header><Badge tone="ai" dot>Model claim · non-authoritative</Badge><code>{shortId(judgeClaim.agent_version_id, 14)}</code></header>
      <p>{judgeClaim.assessment}</p>
      <small>The model may explain and nominate anchors. Only deterministic resolution against runtime-minted records admits completion.</small>
    </aside> : null}
    {discarded ? <p className="completion-lead">Every anchor below was cited by the judge itself. An anchor is a claim and never authority: only the ones that survived resolution against this Run's own records carry a criterion, and the runtime discarded {discarded === 1 ? "the one" : `${discarded} of them`} for the named reason.</p> : null}
    <ol className="criterion-list">{criteria.map((criterion) => <Criterion key={criterion.criterion_id} criterion={criterion} judgeClaim={claimsByCriterion.get(criterion.criterion_id)} />)}</ol>
  </section>;
}

function Criterion({ criterion, judgeClaim }) {
  const surviving = criterion.surviving ?? [];
  const discarded = criterion.discarded ?? [];
  return <li className={criterion.holds ? "is-held" : "is-unevidenced"}>
    <header><Badge tone={criterion.holds ? "success" : "danger"} dot>{criterion.holds ? "Evidenced" : "Unevidenced"}</Badge><strong>{criterion.statement}</strong><code>{criterion.criterion_id}</code></header>
    <DefinitionList items={[
      ["Admissible evidence", titleCase(criterion.evidence_kind)],
      ["Declared sites", (criterion.declared_sites ?? []).join(", ")],
      ["Anchors that survived", String(surviving.length)],
      ["Anchors discarded", String(discarded.length)]
    ]} />
    {judgeClaim ? <div className="criterion-judge-claim"><span>Judge reasoning · claim only</span><p>{judgeClaim.reason}</p></div> : null}
    {surviving.length
      ? <AnchorRoster label={`Surviving anchors · ${surviving.length}`} ids={surviving} />
      : <p className="criterion-empty">No anchor survived resolution, so this criterion carries nothing.</p>}
    {discarded.length ? <ul className="anchor-refusal-list" aria-label={`Discarded anchors for ${criterion.criterion_id}`}>{discarded.map((anchor) => <DiscardedAnchor key={anchor.anchor_id} anchor={anchor} />)}</ul> : null}
  </li>;
}

function AnchorRoster({ label, ids }) {
  const headingId = useId();
  return <div className="anchor-roster"><p className="panel-kicker" id={headingId}>{label}</p><ul aria-labelledby={headingId}>{ids.map((id) => <li key={id}><code>{shortId(id, 14)}</code></li>)}</ul></div>;
}

/** One anchor the judge cited and the resolver threw out, with its own reason.
 *
 * The reason is the resolver's published prose, rendered verbatim, so the
 * console cannot drift into a second vocabulary for the same refusal.
 */
function DiscardedAnchor({ anchor }) {
  const fault = ANCHOR_FAULT[anchor.refusal] ?? "unclassified";
  return <li className={`anchor-refusal fault-${fault}`}>
    <header>{fault === "runtime"
      ? <span className="anchor-fault-mark"><Icon name="warning" size={14} />{ANCHOR_FAULT_LABEL[fault]}</span>
      : <Badge tone={ANCHOR_FAULT_TONE[fault]} dot>{ANCHOR_FAULT_LABEL[fault]}</Badge>}<strong>{ANCHOR_REFUSAL_LABEL[anchor.refusal] ?? titleCase(anchor.refusal)}</strong><code>{anchor.refusal}</code><span className="anchor-refusal-id">Anchor <code>{shortId(anchor.anchor_id, 14)}</code></span></header>
    <p>{anchor.reason}</p>
    {ANCHOR_FAULT_MEANING[fault] ? <p className="anchor-refusal-fault">{ANCHOR_FAULT_MEANING[fault]}</p> : null}
    {ANCHOR_REFUSAL_ACT[anchor.refusal] ? <p className="anchor-refusal-fault">{ANCHOR_REFUSAL_ACT[anchor.refusal]}</p> : null}
  </li>;
}

export function BrakeRefusal({ detail, onDismiss }) {
  const matches = detail?.matches?.length ? detail.matches : [detail].filter((item) => item?.node_id);
  return <section className="brake-refusal" role="alert" aria-labelledby="brake-refusal-title">
    <header><span className="brake-icon"><Icon name="lock" size={22} /></span><div><p className="panel-kicker">Ratification brake engaged · HTTP 409</p><h2 id="brake-refusal-title">The Run was refused before it was created</h2><p><strong>No Run, no Step, no effect was created.</strong> The brake is a read-only verdict evaluated before enqueue, so nothing was written and there is nothing to roll back.</p></div>{onDismiss ? <IconButton icon="close" label="Dismiss the brake refusal" onClick={onDismiss} /> : null}</header>
    {matches.map((match) => <article key={match.fingerprint}>
      <header><Badge tone={deadEndTone(match.ratification_state)} dot>{titleCase(match.ratification_state ?? "canonical")}</Badge><strong>{match.node_id}</strong><code>{match.error_code}</code><span className="dead-end-count"><b>{match.distinct_runs}</b> distinct Run{match.distinct_runs === 1 ? "" : "s"}</span></header>
      <p>A <strong>{match.ratification_state}</strong> dead end already proves node <strong>{match.node_id}</strong> fails this way on the pinned Flow version <code>{shortId(match.flow_version_id, 14)}</code>. Publishing a successor Flow version produces a new fingerprint and clears the brake; repeating this identical approach does not.</p>
      <CitedRuns label={`Refused on the evidence of ${match.citing_run_ids?.length ?? 0} prior Runs`} ids={match.citing_run_ids ?? []} />
    </article>)}
  </section>;
}

function ParallelEvidence({ run, step }) {
  const records = step.output?.members ?? {};
  const memberSteps = run.steps.filter((candidate) => candidate.parent_step_id === step.id);
  const memberIds = [...new Set([...Object.keys(records), ...memberSteps.map((candidate) => candidate.member_id).filter(Boolean)])];
  const barrier = step.output?.barrier ?? null;
  const dissent = new Set(barrier?.dissenting_members ?? []);
  const event = run.events.find((candidate) => candidate.type === "fan_out.barrier_reached" && candidate.payload?.step_id === step.id)
    ?? run.events.find((candidate) => candidate.type === "fan_out.barrier_reached");
  return <section className="parallel-evidence evidence-card span-two">
    <header><div><p className="panel-kicker">Parallel execution evidence · {shortId(step.id, 13)}</p><h3><Icon name="parallel" size={17} />Independent members → code-owned barrier</h3></div><Badge tone={barrier?.converged ? "success" : barrier ? "warning" : "neutral"} dot>{barrier?.converged ? "Quorum reached" : barrier ? "Review route" : "Joining"}</Badge></header>
    <div className="parallel-proof-metrics">
      <article><strong>{barrier?.completed ?? memberSteps.filter((member) => member.status === "completed").length}/{barrier?.expected ?? memberIds.length}</strong><span>completed</span></article>
      <article><strong>{barrier?.affirmative ?? "—"}</strong><span>affirmative</span></article>
      <article><strong>{barrier?.dissenting_members?.length ?? 0}</strong><span>dissenting</span></article>
      <article><strong>{barrier?.failed ?? memberSteps.filter((member) => member.status === "failed").length}</strong><span>failed</span></article>
    </div>
    <div className="parallel-proof-flow">
      <div className="parallel-member-grid">{memberIds.map((memberId, index) => {
        const memberStep = memberSteps.find((candidate) => candidate.member_id === memberId);
        const record = records[memberId] ?? {};
        const output = record.output ?? memberStep?.output ?? {};
        const verdict = output.verdict ?? memberStep?.route_outcome ?? record.status ?? memberStep?.status ?? "pending";
        const status = record.status ?? memberStep?.status ?? "created";
        return <article key={memberId} className={dissent.has(memberId) ? "is-dissent" : ""}><header><span>{String(index + 1).padStart(2, "0")}</span><strong>{memberId}</strong><StatusBadge status={status} /></header><div><Badge tone={status === "failed" ? "danger" : dissent.has(memberId) ? "warning" : "success"}>{titleCase(String(verdict))}</Badge><code>{shortId(memberStep?.target_version_id ?? "unresolved", 12)}</code></div><footer><Icon name="lock" size={12} />isolated member Step</footer></article>;
      })}</div>
      <span className="parallel-join-arrow" aria-hidden="true">→</span>
      <article className="parallel-barrier-proof"><span className="resource-avatar avatar-success"><Icon name="lock" size={19} /></span><div><p className="panel-kicker">Deterministic join</p><strong>{barrier?.mode === "quorum" ? `Quorum ${barrier.quorum}/${barrier.expected}` : titleCase(barrier?.mode ?? "pending")}</strong><small>{barrier ? `${barrier.affirmative} affirmative · ${barrier.failed} failed` : "Awaiting member evidence"}</small></div>{barrier?.dissenting_members?.length ? <p><Icon name="warning" size={13} />Dissent: <strong>{barrier.dissenting_members.join(", ")}</strong></p> : <p><Icon name="check" size={13} />No recorded dissent</p>}</article>
    </div>
    <footer><span><Icon name="timeline" size={13} />{event ? `barrier event #${event.sequence}` : "barrier event pending"}</span><span><Icon name="agent" size={13} />{memberSteps.length} separately persisted member Steps</span><span><Icon name="lock" size={13} />members cannot pause or mint effects</span></footer>
  </section>;
}

function RunSummary({ snapshot, run, onSelectRun }) {
  const flow = snapshot.studio.flows.find((item) => item.id === run.flow_id);
  return <div className="run-summary-grid">{run.steps.filter((step) => step.node_type === "fan_out" && step.member_id === null).map((step) => <ParallelEvidence key={step.id} run={run} step={step} />)}<section className="evidence-card"><header><h3>Execution contract</h3><Badge tone="neutral">immutable</Badge></header><dl><div><dt>Flow</dt><dd>{flow?.name ?? run.flow_id}</dd></div><div><dt>Version</dt><dd>v{run.flow_version}</dd></div><div><dt>Fingerprint</dt><dd><code>{run.flow_fingerprint.slice(0, 24)}…</code></dd></div><div><dt>Outcome</dt><dd>{run.outcome ?? "pending"}</dd></div><div><dt>Correlation</dt><dd><code>{shortId(run.correlation_id, 14)}</code></dd></div><div><dt>Relation</dt><dd>{titleCase(run.relation_kind)}</dd></div></dl></section><section className="evidence-card"><header><h3>Evidence inventory</h3><Badge tone="success">durable</Badge></header><div className="evidence-metrics"><article><strong>{run.steps.length}</strong><span>Steps</span></article><article><strong>{run.events.length}</strong><span>Events</span></article><article><strong>{run.model_calls.length}</strong><span>Model calls</span></article><article><strong>{run.action_receipts.length}</strong><span>Receipts</span></article><article><strong>{run.effects.length}</strong><span>Effects</span></article><article><strong>{run.children.length}</strong><span>Children</span></article></div></section><section className="evidence-card span-two"><header><h3>Input and output</h3><span>{run.finished_at ? `Finished ${formatTime(run.finished_at)}` : `Started ${formatTime(run.started_at ?? run.created_at)}`}</span></header><div className="io-grid"><div><p className="panel-kicker">Validated input</p><KeyValue data={run.input} /></div><div><p className="panel-kicker">Current output</p><KeyValue data={run.output ?? { status: run.status }} /></div></div></section>{run.error_code ? <section className="evidence-card error-card span-two"><header><h3><Icon name="warning" size={17} />Run failure</h3><Badge tone="danger">{run.error_code}</Badge></header><p>{run.error_message}</p></section> : null}{run.parent_run_id || run.children.length ? <section className="evidence-card span-two"><header><h3>Run lineage</h3><Badge tone="ai">{run.relation_kind}</Badge></header><div className="lineage-list">{run.parent_run_id ? <button type="button" onClick={() => onSelectRun(run.parent_run_id)}><Icon name="undo" size={16} /><span><strong>Parent Run</strong><small>{shortId(run.parent_run_id, 14)}</small></span><Badge tone="neutral">open</Badge></button> : null}{run.children.map((child) => <button key={child.id} type="button" onClick={() => onSelectRun(child.id)}><Icon name="redo" size={16} /><span><strong>{titleCase(child.relation_kind)} child</strong><small>{shortId(child.id, 14)}</small></span><StatusBadge status={child.status} /></button>)}</div></section> : null}</div>;
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
    let refused = false;
    const run = await mutate(() => api(`/api/v1/studio/flows/${flow.id}/runs:enqueue`, { method: "POST", keyMode: flow.version.requires_model ? "required" : "optional", body: { input: payload, idempotency_key: commandId("manual-run") } }).catch((error) => { refused = error.code === "brake_engaged"; throw error; }), { success: "Run pinned and queued" });
    if (run) onStarted(run);
    else if (refused) onClose();
  };
  return <Modal title="Start a Run" description="Choose a published Flow. The runtime pins the complete transitive definition before any provider call." onClose={onClose}><form className="modal-form" onSubmit={submit}><Field label="Flow"><select value={flowId} onChange={(event) => setFlowId(event.target.value)}>{flows.map((item) => <option key={item.id} value={item.id}>{item.name} · v{item.current_version}{item.version.requires_model ? " · OpenAI" : " · deterministic"}</option>)}</select></Field>{flow ? <div className="flow-run-summary"><span><strong>{flow.version.nodes.length}</strong> nodes</span><span><strong>{flow.version.routes.length}</strong> routes</span><span><strong>{flow.version.outcomes.length}</strong> outcomes</span><span><strong>{flow.version.requires_model ? "OpenAI" : "None"}</strong> model transport</span></div> : null}<JsonField label="Run input" value={input} onChange={setInput} rows={12} hint="Validated against the published Flow input schema." /><div className="modal-actions"><Button tone="quiet" type="button" onClick={onClose}>Cancel</Button><Button tone="primary" icon="play" type="submit">Pin and start</Button></div></form></Modal>;
}

function ApprovalModal({ run, material, mutate, onClose }) {
  const [actor, setActor] = useState("build-week-operator");
  const [reason, setReason] = useState(material.approved ? "I reviewed the pinned context and authorize this bounded continuation." : "The pinned context does not justify the requested continuation.");
  const copy = approvalMessageParts(material.request.message);
  const submit = async (event) => { event.preventDefault(); const result = await mutate(() => api(`/api/v1/studio/approvals/${material.request.id}/decisions`, { method: "POST", keyMode: "optional", body: { approved: material.approved, actor, reason } }), { success: material.approved ? "Approval recorded; Run resumed" : "Rejection recorded; Run followed its declared route" }); if (result) onClose(); };
  return <Modal title={material.approved ? "Approve and resume" : "Reject this continuation"} description={`Decision for ${shortId(run.id)} · ${material.request.node_id}`} onClose={onClose}><form className="modal-form" onSubmit={submit}><div className={`decision-summary ${material.approved ? "is-approved" : "is-rejected"}`}><Icon name={material.approved ? "check" : "warning"} size={21} /><div><strong>{copy.headline}</strong><p>The actor and reason become append-only Run evidence.</p><ApprovalRationale detail={copy.detail} /></div></div><Field label="Actor"><input required value={actor} onChange={(event) => setActor(event.target.value)} /></Field><Field label="Decision reason" hint="Minimum 12 characters"><textarea required minLength="12" rows="5" value={reason} onChange={(event) => setReason(event.target.value)} /></Field><div className="modal-actions"><Button tone="quiet" type="button" onClick={onClose}>Cancel</Button><Button tone={material.approved ? "primary" : "danger"} type="submit">Record {material.approved ? "approval" : "rejection"}</Button></div></form></Modal>;
}

function RepairModal({ run, proposal, mutate, onClose }) {
  const [actor, setActor] = useState("workflow-maintainer");
  const [reason, setReason] = useState("The cited authority denial proves this exact bounded successor is required.");
  const [acknowledged, setAcknowledged] = useState(false);
  const submit = async (event) => { event.preventDefault(); const result = await mutate(() => api(`/api/v1/studio/repairs/${proposal.id}/apply`, { method: "POST", body: { proposal_hash: proposal.proposal_hash, expected_flow_revision: proposal.expected_flow_revision, expected_action_version: proposal.expected_action_version, actor, reason, acknowledged } }), { success: "Successor Action and Flow versions published" }); if (result) onClose(); };
  return <Modal title="Review bounded repair" description="This publishes successors. It never edits the failed Run or its pinned definitions." onClose={onClose}><form className="modal-form" onSubmit={submit}><div className="repair-review"><p className="panel-kicker">Allowlisted patch</p><KeyValue data={proposal.patch} /><dl><div><dt>Expected Flow revision</dt><dd>{proposal.expected_flow_revision}</dd></div><div><dt>Expected Action version</dt><dd>{proposal.expected_action_version}</dd></div><div><dt>Proposal hash</dt><dd><code>{proposal.proposal_hash.slice(0, 24)}…</code></dd></div></dl></div><Field label="Maintainer"><input required value={actor} onChange={(event) => setActor(event.target.value)} /></Field><Field label="Reason"><textarea required minLength="20" rows="4" value={reason} onChange={(event) => setReason(event.target.value)} /></Field><label className="check-row"><input type="checkbox" required checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} /><span><strong>I approve this exact successor patch</strong><small>The failed Run remains unchanged; proof requires a linked child Run.</small></span></label><div className="modal-actions"><Button tone="quiet" type="button" onClick={onClose}>Cancel</Button><Button tone="primary" icon="save" type="submit" disabled={!acknowledged}>Publish successors</Button></div></form></Modal>;
}

// Graph chrome is set in JSX rather than CSS, so it reads the same tokens
// the stylesheet uses instead of carrying a second colour list.
const RUN_GRAPH_TOKENS = [
  "graph-dot", "minimap-mask", "muted",
  "tone-success-solid", "tone-ai-solid", "tone-warning-solid", "tone-danger-solid"
];

function stateColor(state) {
  return {
    completed: "tone-success-solid",
    running: "tone-ai-solid",
    waiting_approval: "tone-warning-solid",
    blocked: "tone-danger-solid",
    failed: "tone-danger-solid"
  }[state] ?? "muted";
}
