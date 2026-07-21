import React, { useEffect, useMemo, useState } from "react";
import { api, commandId } from "../api.js";
import { Icon } from "../icons.jsx";
import { formatTime, shortId, slugDraft, slugify, titleCase } from "../lib.js";
import { Badge, Button, EmptyState, Field, IconButton, Modal, PageHeader } from "./ui.jsx";

const DEFAULT_PARTICIPANTS = [
  {
    id: "product",
    name: "Product Steward",
    perspective: "User value, product coherence, and whether the proposed outcome solves the named job.",
    instructions: "Challenge vague value claims. Separate attractive presentation from executable utility.",
    reasoning_effort: "medium",
    max_tool_calls: 0,
    allowed_action_version_ids: []
  },
  {
    id: "risk",
    name: "Risk Challenger",
    perspective: "Failure modes, unsupported claims, authority leaks, and evidence that could falsify the proposal.",
    instructions: "Prefer a material dissent over artificial consensus. Name missing evidence precisely.",
    reasoning_effort: "high",
    max_tool_calls: 0,
    allowed_action_version_ids: []
  },
  {
    id: "operations",
    name: "Runtime Operator",
    perspective: "Repeatability, observability, bounded execution, recovery, and operational proof.",
    instructions: "Test whether the decision can be replayed and maintained from persisted evidence.",
    reasoning_effort: "medium",
    max_tool_calls: 0,
    allowed_action_version_ids: []
  }
];

export default function BoardRooms({ snapshot, mutate, busy, openFlow, focusRun, initialContext = "", onContextConsumed }) {
  const rooms = snapshot.studio.boardrooms ?? [];
  const [selectedId, setSelectedId] = useState(rooms[0]?.id ?? null);
  const [building, setBuilding] = useState(!rooms.length);
  const [runRoom, setRunRoom] = useState(null);
  const selected = rooms.find((room) => room.id === selectedId) ?? rooms[0] ?? null;

  useEffect(() => {
    if (rooms.some((room) => room.id === selectedId)) return;
    setSelectedId(rooms[0]?.id ?? null);
  }, [rooms, selectedId]);
  useEffect(() => {
    if (!initialContext) return;
    const target = selected ?? rooms[0] ?? null;
    if (target) {
      setRunRoom({ room: target, context: initialContext });
      onContextConsumed?.();
    } else {
      // Keep the parent-owned context intact while the operator publishes the
      // first room. The refreshed projection re-enters this effect and opens
      // the real Run dialog with the same cited envelope.
      setBuilding(true);
    }
  }, [initialContext, onContextConsumed, rooms, selected]);

  return <section className="boardrooms-page">
    <PageHeader
      eyebrow="Independent deliberation · deterministic authority"
      title="BoardRooms"
      description="Create a real multi-agent decision Flow: perspectives run concurrently without seeing each other, code owns quorum, an editor preserves dissent, and optional approval and writes stay downstream of the barrier."
      actions={<Button tone="primary" icon="plus" onClick={() => setBuilding(true)}>New BoardRoom</Button>}
    />
    <div className="boardroom-principles">
      <article><Icon name="parallel" size={20} /><div><strong>Independent by construction</strong><p>Each member is a pinned child Step with its own provider call and operation session.</p></div></article>
      <article><Icon name="lock" size={20} /><div><strong>Quorum is code</strong><p>The model cannot promote agreement, hide a failed voter, or authorize an effect.</p></div></article>
      <article><Icon name="activity" size={20} /><div><strong>Dissent survives synthesis</strong><p>Raw member records, barrier result, editor output, receipts, and ledger remain inspectable.</p></div></article>
      <article><Icon name="flow" size={20} /><div><strong>It is an ordinary Flow</strong><p>Open the generated graph, edit any node, and publish forward-only successors.</p></div></article>
    </div>

    {building ? <BoardRoomBuilder snapshot={snapshot} mutate={mutate} busy={busy} onCancel={() => setBuilding(false)} onCreated={(result) => { setBuilding(false); setSelectedId(result.flow.id); }} /> : <div className="boardroom-workbench">
      <aside className="boardroom-list" aria-label="BoardRooms"><header><div><p className="panel-kicker">Executable councils</p><h2>Rooms</h2></div><Badge tone="neutral">{rooms.length}</Badge></header>{rooms.map((room) => <button key={room.id} type="button" className={selected?.id === room.id ? "is-active" : ""} onClick={() => setSelectedId(room.id)}><span className="resource-avatar avatar-boardroom"><Icon name="boardroom" size={18} /></span><span><strong>{room.name}</strong><small>{room.members.length} members · quorum {room.barrier.quorum}</small></span><Badge tone={room.approval_mode === "human" ? "warning" : "ai"}>v{room.revision}</Badge></button>)}{!rooms.length ? <EmptyState icon="boardroom" title="No BoardRooms yet" description="Create one guided template, then treat it as a normal editable Flow." /> : null}</aside>
      <main className="boardroom-detail">{selected ? <BoardRoomDetail room={selected} runs={snapshot.studio.runs} openFlow={openFlow} onRun={() => setRunRoom({ room: selected, context: "" })} focusRun={focusRun} /> : <EmptyState icon="boardroom" title="Create an executable BoardRoom" description="The factory publishes every Prompt, Skill, Agent, Action, and Flow version it needs—nothing is hidden in a UI-only object." action={<Button tone="primary" icon="plus" onClick={() => setBuilding(true)}>Build BoardRoom</Button>} />}</main>
    </div>}
    {runRoom ? <RunBoardRoomModal room={runRoom.room} initialContext={runRoom.context} mutate={mutate} onClose={() => setRunRoom(null)} onStarted={(run) => { setRunRoom(null); focusRun(run.id); }} /> : null}
  </section>;
}

function BoardRoomBuilder({ snapshot, mutate, busy, onCancel, onCreated }) {
  const models = snapshot.studio.supported_models ?? ["gpt-5.6"];
  const grantable = useMemo(() => snapshot.studio.actions.flatMap((action) => action.versions.map((version) => ({ action, version }))).filter(({ version }) => ["template", "condition", "router", "transform", "assert", "smart_read", "knowledge_search", "memory_recall"].includes(version.kind) || (["sandbox", "data_store"].includes(version.kind) && version.config?.write_enabled === false)), [snapshot]);
  const [form, setForm] = useState({
    name: "Launch Decision Room",
    slug: "launch-decision-room",
    purpose: "Reach an evidence-bound launch decision while preserving independent dissent and operational constraints.",
    participants: DEFAULT_PARTICIPANTS.map((item, index) => ({ ...item, model: models[index % models.length] })),
    editor: { name: "Dissent Editor", model: models[0], instructions: "Synthesize the completed participant records without erasing material disagreement, failed members, open questions, or citations.", reasoning_effort: "high" },
    quorum: 2,
    error_policy: "isolate",
    approval_mode: "human",
    write_collection: null
  });
  const patch = (key, value) => setForm((current) => ({ ...current, [key]: value }));
  const updateParticipant = (index, updater) => setForm((current) => ({ ...current, participants: current.participants.map((participant, participantIndex) => participantIndex === index ? updater(participant) : participant) }));
  const addParticipant = () => setForm((current) => {
    if (current.participants.length >= 8) return current;
    const index = current.participants.length + 1;
    return { ...current, participants: [...current.participants, { id: `perspective-${index}`, name: `Perspective ${index}`, perspective: "Name the distinct lens this participant owns.", instructions: "Review independently, retain uncertainty, and cite only supplied context.", model: models[(index - 1) % models.length], reasoning_effort: "medium", max_tool_calls: 0, allowed_action_version_ids: [] }] };
  });
  const removeParticipant = (index) => setForm((current) => {
    if (current.participants.length <= 2) return current;
    const participants = current.participants.filter((_, participantIndex) => participantIndex !== index);
    return { ...current, participants, quorum: Math.min(current.quorum, participants.length) };
  });
  const submit = async (event) => {
    event.preventDefault();
    const body = { ...form, write_collection: form.write_collection || null };
    const result = await mutate(() => api("/api/v1/studio/boardrooms", { method: "POST", body }), { success: `${form.name} published as an editable Flow` });
    if (result) onCreated(result);
  };
  const targetKeys = form.participants.map((participant) => participant.id);
  const duplicateIds = new Set(targetKeys.filter((id, index) => targetKeys.indexOf(id) !== index));

  return <form className="boardroom-builder" onSubmit={submit}>
    <header><div><p className="panel-kicker">Guided Flow factory</p><h2>Design an independent decision room</h2><p>The factory creates visible, versioned resources in the normal registries. After publication, the result is edited and executed by the same generic Flow runtime.</p></div><div><Button tone="quiet" onClick={onCancel}>Cancel</Button><Button tone="primary" icon="save" type="submit" disabled={busy || duplicateIds.size > 0}>Publish BoardRoom</Button></div></header>
    <div className="boardroom-builder-layout">
      <main>
        <section className="builder-section"><div className="form-section-title"><span>01</span><div><h3>Decision contract</h3><p>Name the job, not the meeting. Every participant receives the same brief and cited context.</p></div></div><div className="field-grid two"><Field label="Name" required><input required value={form.name} onChange={(event) => { patch("name", event.target.value); patch("slug", slugify(event.target.value)); }} /></Field><Field label="Slug"><input required value={form.slug} onChange={(event) => patch("slug", slugDraft(event.target.value))} onBlur={(event) => patch("slug", slugify(event.target.value))} /></Field></div><Field label="Purpose" required><textarea required rows="3" value={form.purpose} onChange={(event) => patch("purpose", event.target.value)} /></Field></section>
        <section className="builder-section"><div className="form-section-title"><span>02</span><div><h3>Independent perspectives</h3><p>Two to eight Agents run concurrently. They cannot inspect one another before the barrier.</p></div></div><div className="participant-editor-list">{form.participants.map((participant, index) => <article className={`participant-editor ${duplicateIds.has(participant.id) ? "is-invalid" : ""}`} key={`participant-${index}`}><header><span>{String(index + 1).padStart(2, "0")}</span><div><strong>{participant.name || `Perspective ${index + 1}`}</strong><small>{participant.model} · {participant.reasoning_effort} reasoning</small></div><IconButton icon="trash" label={`Remove ${participant.name}`} disabled={form.participants.length <= 2} onClick={() => removeParticipant(index)} /></header><div className="field-grid two"><Field label="Member ID" error={duplicateIds.has(participant.id) ? "IDs must be unique." : null}><input value={participant.id} onChange={(event) => updateParticipant(index, (current) => ({ ...current, id: slugDraft(event.target.value) }))} onBlur={(event) => updateParticipant(index, (current) => ({ ...current, id: slugify(event.target.value) }))} /></Field><Field label="Name"><input value={participant.name} onChange={(event) => updateParticipant(index, (current) => ({ ...current, name: event.target.value }))} /></Field></div><Field label="Owned perspective"><textarea rows="2" value={participant.perspective} onChange={(event) => updateParticipant(index, (current) => ({ ...current, perspective: event.target.value }))} /></Field><Field label="Behavioral instructions"><textarea rows="3" value={participant.instructions} onChange={(event) => updateParticipant(index, (current) => ({ ...current, instructions: event.target.value }))} /></Field><div className="field-grid three"><Field label="Model"><select value={participant.model} onChange={(event) => updateParticipant(index, (current) => ({ ...current, model: event.target.value }))}>{models.map((model) => <option key={model} value={model}>{model}</option>)}</select></Field><Field label="Reasoning"><select value={participant.reasoning_effort} onChange={(event) => updateParticipant(index, (current) => ({ ...current, reasoning_effort: event.target.value }))}><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option></select></Field><Field label="Tool-call budget"><input type="number" min="0" max="4" value={participant.max_tool_calls} onChange={(event) => updateParticipant(index, (current) => ({ ...current, max_tool_calls: Math.max(0, Math.min(4, Number(event.target.value) || 0)) }))} /></Field></div>{grantable.length ? <details className="participant-grants"><summary><span><Icon name="skill" size={15} />Callable Action grants</span><Badge tone={participant.allowed_action_version_ids.length ? "ai" : "neutral"}>{participant.allowed_action_version_ids.length}</Badge></summary><p>Only read/model-safe exact versions are offered. Approval and effect Actions cannot enter parallel members.</p><div>{grantable.map(({ action, version }) => <label key={version.id}><input type="checkbox" checked={participant.allowed_action_version_ids.includes(version.id)} onChange={(event) => updateParticipant(index, (current) => { const grants = event.target.checked ? [...current.allowed_action_version_ids, version.id] : current.allowed_action_version_ids.filter((id) => id !== version.id); return { ...current, allowed_action_version_ids: grants, max_tool_calls: grants.length ? Math.max(1, current.max_tool_calls) : 0 }; })} /><span><strong>{action.name}</strong><small>{titleCase(version.kind)} · v{version.version}</small></span></label>)}</div></details> : <p className="section-help"><Icon name="lock" size={14} />No callable read Actions exist yet. Participants remain tool-free.</p>}</article>)}</div><Button tone="quiet" icon="plus" onClick={addParticipant} disabled={form.participants.length >= 8}>Add perspective</Button></section>
        <section className="builder-section"><div className="form-section-title"><span>03</span><div><h3>Dissent-preserving editor</h3><p>One final model call sees all completed member records plus the code-owned barrier; it cannot change quorum.</p></div></div><div className="field-grid two"><Field label="Editor name"><input value={form.editor.name} onChange={(event) => patch("editor", { ...form.editor, name: event.target.value })} /></Field><Field label="Model"><select value={form.editor.model} onChange={(event) => patch("editor", { ...form.editor, model: event.target.value })}>{models.map((model) => <option key={model} value={model}>{model}</option>)}</select></Field></div><Field label="Synthesis instructions"><textarea rows="4" value={form.editor.instructions} onChange={(event) => patch("editor", { ...form.editor, instructions: event.target.value })} /></Field><Field label="Reasoning effort"><select value={form.editor.reasoning_effort} onChange={(event) => patch("editor", { ...form.editor, reasoning_effort: event.target.value })}><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option></select></Field></section>
      </main>
      <aside className="boardroom-governance"><div className="form-section-title"><span>04</span><div><h3>Authority & join</h3><p>Code-owned settings, visible before resources are published.</p></div></div><div className="barrier-preview"><header><Icon name="parallel" size={18} /><strong>{form.participants.length} concurrent members</strong></header><div>{form.participants.map((participant) => <span key={participant.id}><i />{participant.id || "missing-id"}</span>)}</div><footer><Icon name="lock" size={15} />join at quorum {form.quorum}/{form.participants.length}</footer></div><div className="field-grid two"><Field label="Quorum"><input type="number" min="1" max={form.participants.length} value={form.quorum} onChange={(event) => patch("quorum", Math.max(1, Math.min(form.participants.length, Number(event.target.value) || 1)))} /></Field><Field label="Member failure"><select value={form.error_policy} onChange={(event) => patch("error_policy", event.target.value)}><option value="isolate">Isolate + expose</option><option value="fail_fast">Fail after evidence</option></select></Field></div><fieldset className="authority-choice"><legend>After synthesis</legend><label className={form.approval_mode === "none" ? "is-checked" : ""}><input type="radio" name="approval-mode" checked={form.approval_mode === "none"} onChange={() => setForm((current) => ({ ...current, approval_mode: "none", write_collection: null }))} /><span><strong>Return synthesis</strong><small>No pause and no effect.</small></span></label><label className={form.approval_mode === "human" ? "is-checked" : ""}><input type="radio" name="approval-mode" checked={form.approval_mode === "human"} onChange={() => patch("approval_mode", "human")} /><span><strong>Require human decision</strong><small>Both approval and rejection resume to an explicit result.</small></span></label></fieldset>{form.approval_mode === "human" ? <Field label="Optional bounded write" hint="Leave empty for approval without an effect."><input value={form.write_collection ?? ""} onChange={(event) => patch("write_collection", slugDraft(event.target.value) || null)} placeholder="approved-decisions" /></Field> : null}<div className="factory-manifest"><h3>Publication manifest</h3>{[[form.participants.length + 1, "Prompts"], [form.participants.length + 1, "Skills"], [form.participants.length + 1, "Agents"], [form.participants.length + 2 + (form.approval_mode === "human" ? 2 : 0) + (form.write_collection ? 1 : 0), "Actions"], [1, "editable Flow"]].map(([count, label]) => <p key={label}><strong>{count}</strong><span>{label}</span></p>)}<small>Every artifact is immutable and remains editable through its normal registry.</small></div><Button className="builder-publish" tone="primary" icon="save" type="submit" disabled={busy || duplicateIds.size > 0}>Publish complete room</Button></aside>
    </div>
  </form>;
}

function BoardRoomDetail({ room, runs, openFlow, onRun, focusRun }) {
  const roomRuns = runs.filter((run) => run.flow_id === room.flow_id);
  return <>
    <header className="boardroom-detail-header"><div><p className="panel-kicker">{room.slug} · Flow v{room.revision}</p><h2>{room.name}</h2><p>{room.purpose}</p></div><div><Button tone="quiet" icon="flow" onClick={() => openFlow(room.flow_id)}>Edit exact Flow</Button><Button tone="primary" icon="play" onClick={onRun}>Start deliberation</Button></div></header>
    <div className="boardroom-runtime-strip"><span><strong>{room.members.length}</strong> parallel members</span><span><strong>{room.model_call_forecast}</strong> forecast model calls</span><span><strong>{room.barrier.mode}</strong> {room.barrier.quorum}/{room.members.length}</span><span><strong>{room.approval_mode}</strong> authority gate</span></div>
    <section className="boardroom-map" aria-label="BoardRoom execution map"><div className="member-orbit"><span className="orbit-input"><Icon name="context" size={17} />same brief + context</span>{room.members.map((member, index) => <article key={member.id} style={{ "--member-index": index }}><span>{String(index + 1).padStart(2, "0")}</span><Icon name={member.type === "agent" ? "agent" : member.type === "flow" ? "flow" : "action"} size={17} /><div><strong>{member.label}</strong><small>{member.id} · {member.model ?? titleCase(member.type)}</small></div></article>)}</div><i aria-hidden="true">→</i><article className="barrier-node"><Icon name="lock" size={20} /><strong>Code barrier</strong><small>{room.barrier.quorum} × {room.barrier.affirmative_values.join(" / ")} at <code>{room.barrier.verdict_path}</code></small><span>{room.barrier.on_member_error === "isolate" ? "failures visible" : "fail closed"}</span></article><i aria-hidden="true">→</i><article className="editor-node"><Icon name="agent" size={20} /><strong>Dissent editor</strong><small>sees member records after join</small></article>{room.approval_mode === "human" ? <><i aria-hidden="true">→</i><article className="approval-node"><Icon name="lock" size={20} /><strong>Human gate</strong><small>{room.write_collection ? `then one ${room.write_collection} write` : "approval or rejection result"}</small></article></> : null}</section>
    <section className="boardroom-proof"><header><div><p className="panel-kicker">What the runtime proves</p><h3>Not a group-chat transcript</h3></div><Badge tone="success">ledger-backed</Badge></header><div><article><strong>Isolation</strong><p>Participant prompts contain no peer outputs. Calls dispatch through separate worker sessions.</p></article><article><strong>Barrier ownership</strong><p>Affirmative counts, failed members, convergence, and dissenting IDs are derived in code.</p></article><article><strong>Authority placement</strong><p>Member targets may neither pause nor mint effects; a visible downstream node owns those acts.</p></article><article><strong>Forward evolution</strong><p>The room is the ordinary Flow shown in Studio. Editing publishes a successor while old Runs retain pins.</p></article></div></section>
    <section className="boardroom-runs"><header><div><p className="panel-kicker">Operational evidence</p><h3>Room Runs</h3></div><Badge tone="neutral">{roomRuns.length}</Badge></header>{roomRuns.slice(0, 6).map((run) => <button type="button" key={run.id} onClick={() => focusRun(run.id)}><span><strong>{shortId(run.id, 17)}</strong><small>{formatTime(run.created_at)} · pinned Flow v{run.flow_version}</small></span><Badge tone={run.status === "completed" ? "success" : run.status === "waiting_approval" ? "warning" : "neutral"}>{titleCase(run.status)}</Badge></button>)}{!roomRuns.length ? <EmptyState icon="run" title="No deliberations yet" description="Start with a concrete brief and cited context. The full parent and member evidence will appear here." /> : null}</section>
  </>;
}

function RunBoardRoomModal({ room, initialContext, mutate, onClose, onStarted }) {
  const [brief, setBrief] = useState("Decide whether this proposal is ready to ship. Name the evidence for the decision, material dissent, unresolved questions, and the next bounded action.");
  const [context, setContext] = useState(initialContext || "No external context supplied. Treat every unsupported material claim as uncertain.");
  const submit = async (event) => {
    event.preventDefault();
    const result = await mutate(() => api(`/api/v1/studio/flows/${room.flow_id}/runs:enqueue`, { method: "POST", keyMode: "required", body: { input: { brief, context }, idempotency_key: commandId("boardroom") } }), { success: "BoardRoom pinned and queued" });
    if (result) onStarted(result);
  };
  return <Modal title={`Run ${room.name}`} description={`${room.members.length} independent participants + one editor · ${room.model_call_forecast} forecast model calls · pinned Flow ${shortId(room.flow_version_id, 16)}`} onClose={onClose} width="760px"><form className="modal-form boardroom-run-form" onSubmit={submit}><div className="run-contract-note"><Icon name="parallel" size={18} /><p><strong>Every member receives this exact pair.</strong><span>They execute concurrently and cannot see peer output until the code-owned join has completed.</span></p></div><Field label="Decision brief" required><textarea required rows="6" value={brief} onChange={(event) => setBrief(event.target.value)} /></Field><Field label="Cited context" required hint="SmartRead citations can be transferred here directly; unsupported prose remains visible as unsupported."><textarea required rows="10" value={context} onChange={(event) => setContext(event.target.value)} spellCheck="false" /></Field><div className="run-cost-preview"><span><Icon name="agent" size={15} />{room.model_call_forecast} bounded OpenAI Responses calls</span><span><Icon name="lock" size={15} />key from this tab only</span><span><Icon name="timeline" size={15} />parent + member Steps</span></div><div className="modal-actions"><Button tone="quiet" onClick={onClose}>Cancel</Button><Button tone="primary" icon="play" type="submit">Pin and start deliberation</Button></div></form></Modal>;
}
