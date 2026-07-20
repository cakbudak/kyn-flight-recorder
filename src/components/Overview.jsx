import React, { useState } from "react";
import { api } from "../api.js";
import { Icon } from "../icons.jsx";
import { exampleForSchema, formatTime, parseJson, shortId, titleCase } from "../lib.js";
import { Badge, Button, CitedRuns, Field, JsonField, Modal, PageHeader, StatusBadge } from "./ui.jsx";

export default function Overview({ snapshot, mutate, setView, focusRun }) {
  const studio = snapshot.studio;
  const [showTrigger, setShowTrigger] = useState(false);
  const [revealedWebhook, setRevealedWebhook] = useState(null);
  const activeRuns = studio.runs.filter((run) => ["created", "running", "waiting_approval"].includes(run.status)).length;
  const failedRuns = studio.runs.filter((run) => ["blocked", "failed"].includes(run.status)).length;
  const toggleTrigger = (trigger) => mutate(
    () => api(`/api/v1/studio/triggers/${trigger.id}/state`, {
      method: "POST",
      body: { enabled: !trigger.enabled, expected_revision: trigger.revision }
    }),
    { success: trigger.enabled ? "Trigger disabled" : "Trigger enabled" }
  );
  return (
    <section className="overview-page">
      <PageHeader eyebrow="Kyn.ist Agent Studio" title="Build the job. Operate its truth." description="A serious public cut of Kyn’s agent stack: configurable capabilities, visual composition, version-pinned execution, explicit authority, authoritative evidence, and forward-only recovery." actions={<><Button tone="quiet" icon="docs" onClick={() => setView("docs")}>Read the contract</Button><Button tone="primary" icon="flow" onClick={() => setView("studio")}>Open Flow Studio</Button></>} />
      <div className="metric-row">
        <Metric icon="action" value={studio.actions.length} label="Actions" detail={`${new Set(studio.actions.map((item) => item.version.kind)).size} executor kinds`} />
        <Metric icon="flow" value={studio.flows.length} label="Flows" detail={`${studio.flows.reduce((total, flow) => total + flow.versions.length, 0)} immutable versions`} />
        <Metric icon="agent" value={snapshot.agents.length} label="Agents" detail={`${snapshot.prompts.length} Prompts · ${snapshot.skills.length} Skills`} />
        <Metric icon="run" value={studio.runs.length} label="Runs" detail={`${activeRuns} live · ${failedRuns} maintainable`} />
      </div>
      <div className="overview-primary-grid">
        <section className="overview-card control-plane-card">
          <header><div><p className="panel-kicker">One control plane</p><h2>From definition to proven recovery</h2></div><Badge tone="success">real runtime</Badge></header>
          <div className="control-plane-sequence">
            {[
              ["01", "Define", "Actions · Agents · Prompts · Skills", "action"],
              ["02", "Compose", "Named ports · mapping · Subflows", "flow"],
              ["03", "Operate", "Runs · approvals · receipts · effects", "run"],
              ["04", "Maintain", "Diagnose · successor · linked proof", "timeline"]
            ].map(([number, title, detail, icon], index) => <React.Fragment key={number}><button type="button" onClick={() => setView(index === 0 ? "actions" : index === 1 ? "studio" : "runs")}><span>{number}</span><i><Icon name={icon} size={20} /></i><strong>{title}</strong><small>{detail}</small></button>{index < 3 ? <em aria-hidden="true">→</em> : null}</React.Fragment>)}
          </div>
          <p className="control-plane-note"><Icon name="lock" size={15} />Every Run pins its complete transitive definition before work starts. A repair creates successors; it cannot rewrite the past.</p>
        </section>
        <section className="overview-card boundary-card">
          <header><div><p className="panel-kicker">Public boundary</p><h2>Small cut. Hard contracts.</h2></div></header>
          <ul>
            <li><Icon name="check" size={16} /><span><strong>Official OpenAI Responses SDK</strong><small>Browser-owned key, strict output, bounded turns and tools.</small></span></li>
            <li><Icon name="check" size={16} /><span><strong>Flat product-facing SQLite</strong><small>No Parts, Entities, Bricks, Frames, Ainou, or CE projection.</small></span></li>
            <li><Icon name="check" size={16} /><span><strong>Static authority surface</strong><small>No shell, arbitrary HTTP, filesystem, or production connector.</small></span></li>
            <li><Icon name="check" size={16} /><span><strong>Durable evidence</strong><small>Immutable versions and hash-linked events—not best-effort traces.</small></span></li>
          </ul>
        </section>
      </div>
      <section className="overview-section">
        <header><div><p className="panel-kicker">Concrete starting points</p><h2>Build more than the seeded example</h2></div><p>The included launch flow is one editable template, not a prescribed tour.</p></header>
        <div className="use-case-grid">
          <UseCase mark="AI → ROUTE → H" title="Evidence-bound review" description="Analyze through a pinned Agent, route over named outputs, pause for a human, then commit one bounded effect." onClick={() => setView("studio")} />
          <UseCase mark="HOOK → MAP → ASSERT" title="Typed intake" description="Accept a secret webhook, normalize the payload, enforce a contract, and inspect exact receipts." onClick={() => setView("studio")} />
          <UseCase mark="FLOW → FLOW" title="Reusable orchestration" description="Publish a Flow, use its immutable version as a typed node, and retain linked child Run evidence." onClick={() => setView("studio")} />
          <UseCase mark="FAIL → FIX → PROOF" title="Forward recovery" description="Diagnose from owned events, approve an allowlisted successor, and prove it in linked work." onClick={() => setView("runs")} />
        </div>
      </section>
      <Principles principles={studio.principles ?? []} markers={studio.policy_markers ?? []} focusRun={focusRun} />
      <div className="overview-bottom-grid">
        <section className="overview-card recent-card">
          <header><div><p className="panel-kicker">Operations</p><h2>Recent Runs</h2></div><Button tone="quiet" onClick={() => setView("runs")}>Open console</Button></header>
          <div className="recent-run-table">
            {studio.runs.slice(0, 6).map((run) => { const flow = studio.flows.find((item) => item.id === run.flow_id); return <button type="button" key={run.id} onClick={() => setView("runs")}><span><strong>{flow?.name ?? "Unknown Flow"}</strong><small>{shortId(run.id)} · {formatTime(run.created_at)}</small></span><em>{titleCase(run.relation_kind)} · v{run.flow_version}</em><StatusBadge status={run.status} /></button>; })}
            {!studio.runs.length ? <div className="compact-empty"><Icon name="run" size={20} /><span><strong>No Runs yet</strong><small>Start any published Flow from the Studio.</small></span></div> : null}
          </div>
        </section>
        <section className="overview-card trigger-card">
          <header><div><p className="panel-kicker">Activation</p><h2>Triggers</h2></div><Button tone="quiet" icon="plus" onClick={() => setShowTrigger(true)}>Add trigger</Button></header>
          <p>Manual commands, signed webhooks, and bounded schedules all enter the same Run creation seam.</p>
          <div className="trigger-list">
            {studio.triggers.slice(0, 5).map((trigger) => { const flow = studio.flows.find((item) => item.id === trigger.flow_id); return <article key={trigger.id}><span className="resource-avatar"><Icon name={trigger.trigger_type === "webhook" ? "external" : "timeline"} size={17} /></span><span><strong>{trigger.name}</strong><small>{flow?.name} · pinned v{trigger.flow_version}</small></span><Badge tone={trigger.enabled ? "success" : "neutral"}>{trigger.enabled ? "enabled" : "disabled"}</Badge><button type="button" onClick={() => toggleTrigger(trigger)}>{trigger.enabled ? "Disable" : "Enable"}</button></article>; })}
            {!studio.triggers.length ? <div className="compact-empty"><Icon name="timeline" size={20} /><span><strong>No trigger bindings</strong><small>Create one without changing the Flow definition.</small></span></div> : null}
          </div>
          {revealedWebhook ? <div className="webhook-reveal"><p className="panel-kicker">Copy now · shown once</p><code>{revealedWebhook}</code></div> : null}
        </section>
      </div>
      {showTrigger ? <TriggerModal flows={studio.flows} mutate={mutate} onClose={() => setShowTrigger(false)} onCreated={(result) => { setShowTrigger(false); if (result?.secret) setRevealedWebhook(`/api/v1/hooks/${result.secret}`); }} /> : null}
    </section>
  );
}

/** Workspace-scoped distilled rules.
 *
 * This lives on Overview and not in Documentation because a principle is a
 * property of *this* workspace's history, not of the contract: it is derived by
 * query from the dead-end evidence of Flows that already ran, it changes as
 * Runs accumulate, and it belongs to no single Flow or Run — so no workbench
 * owns it. Overview is the only surface already scoped to the workspace as a
 * whole. Documentation states what the system guarantees for everyone and holds
 * no live workspace data; a derived, empty-by-default panel would make it read
 * as a claim rather than an observation.
 */
function Principles({ principles, markers, focusRun }) {
  // The ceiling is counted from the vocabulary the server actually ships, not
  // asserted in prose. A claim about how narrow a table is must not be able to
  // go stale when the table grows.
  const vocabulary = markers ?? [];
  return (
    <section className="overview-section principles-section">
      <header>
        <div><p className="panel-kicker">Derived knowledge · never stored, never model-written</p><h2>What this workspace has learned</h2></div>
        <p>A dead end refuses one exact pinned path. A principle generalizes it across independent Flows — and only advises.</p>
      </header>
      <p className="principle-ceiling">
        <Icon name="lock" size={15} />
        <span>
          <strong>The honest ceiling.</strong> The mechanism is general: it groups any declared predicate over any
          executor kind. The vocabulary is not. The runtime currently recognises{" "}
          <strong>{vocabulary.length === 1 ? "exactly one predicate" : `${vocabulary.length} predicates`}</strong>
          {vocabulary.length ? <> — {vocabulary.map((marker, index) => <span key={marker.name}>{index ? ", " : ""}<code>{marker.executor_kind}.{marker.config_key}</code></span>)}</> : null}
          , so that is the whole of what this system can state. A failure carrying no recognised predicate produces no
          signature and never distils, by construction. Treat any rule here as one entry of vocabulary, not as broad
          judgement.
        </span>
      </p>
      {principles.length ? (
        <ol className="principle-list">
          {principles.map((principle) => (
            <li key={principle.signature}>
              <header>
                <Badge tone="blue" dot>Advisory</Badge>
                <code>{principle.error_code}</code>
                <span className="principle-count"><b>{principle.distinct_flows}</b> distinct Flows · <b>{principle.distinct_dead_ends}</b> dead ends</span>
              </header>
              <p>{principle.statement}</p>
              <dl className="principle-facts">
                <div><dt>Executor kind</dt><dd>{principle.executor_kind}</dd></div>
                <div><dt>Declared predicate</dt><dd>{principle.policy_marker}</dd></div>
                <div><dt>Signature</dt><dd><code>{principle.signature.slice(0, 20)}…</code></dd></div>
                <div><dt>Cited</dt><dd>{formatTime(principle.first_cited_at)} → {formatTime(principle.last_cited_at)}</dd></div>
              </dl>
              <CitedRuns label={`Citing Runs · ${principle.citing_run_ids.length}`} ids={principle.citing_run_ids} onSelectRun={focusRun} />
            </li>
          ))}
        </ol>
      ) : (
        <div className="compact-empty">
          <Icon name="skill" size={20} />
          <span>
            <strong>No principle distilled yet</strong>
            <small>Three <em>different</em> Flows must each fail the same declared way. Repeating one Flow ratifies a dead end instead, which is the brake&rsquo;s job.</small>
          </span>
        </div>
      )}
    </section>
  );
}

function Metric({ icon, value, label, detail }) {
  return <article className="metric-card"><span><Icon name={icon} size={19} /></span><div><strong>{value}</strong><p>{label}</p><small>{detail}</small></div></article>;
}

function UseCase({ mark, title, description, onClick }) {
  return <button type="button" className="use-case" onClick={onClick}><Badge tone="ai">{mark}</Badge><strong>{title}</strong><p>{description}</p><span>Open workbench <Icon name="chevron" size={14} /></span></button>;
}

function TriggerModal({ flows, mutate, onClose, onCreated }) {
  const [form, setForm] = useState({ flowId: flows[0]?.id ?? "", name: "Inbound automation", type: "webhook", interval: 60 });
  const selectedFlow = flows.find((flow) => flow.id === form.flowId) ?? flows[0];
  const [scheduleInput, setScheduleInput] = useState(JSON.stringify(exampleForSchema(selectedFlow?.version.input_schema), null, 2));
  const submit = async (event) => {
    event.preventDefault();
    let input = {};
    if (form.type === "schedule") {
      try { input = parseJson(scheduleInput, "Schedule input"); }
      catch (error) { await mutate(() => Promise.reject(error), { refreshAfter: false, success: "" }); return; }
    }
    const result = await mutate(() => api(`/api/v1/studio/flows/${form.flowId}/triggers`, { method: "POST", body: { name: form.name, trigger_type: form.type, config: form.type === "schedule" ? { interval_minutes: Number(form.interval), input } : {} } }), { success: `${titleCase(form.type)} trigger created` });
    if (result) onCreated(result);
  };
  return <Modal title="Add a trigger" description="The binding pins the Flow version active at creation. Rotating the Flow never silently changes existing activation." onClose={onClose}><form className="modal-form" onSubmit={submit}><Field label="Flow"><select value={form.flowId} onChange={(event) => { const next = flows.find((flow) => flow.id === event.target.value); setForm({ ...form, flowId: event.target.value }); setScheduleInput(JSON.stringify(exampleForSchema(next?.version.input_schema), null, 2)); }}>{flows.map((flow) => <option key={flow.id} value={flow.id}>{flow.name} · v{flow.current_version}</option>)}</select></Field><Field label="Trigger name"><input required value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></Field><Field label="Type"><select value={form.type} onChange={(event) => setForm({ ...form, type: event.target.value })}><option value="webhook">Signed webhook</option><option value="schedule">Bounded schedule</option></select></Field>{form.type === "schedule" ? <><Field label="Interval in minutes" hint="Between 5 and 10,080 minutes"><input type="number" min="5" max="10080" value={form.interval} onChange={(event) => setForm({ ...form, interval: event.target.value })} /></Field><JsonField label="Scheduled Run input" value={scheduleInput} onChange={setScheduleInput} rows={8} hint="Validated against the pinned Flow input schema." /></> : <div className="decision-summary"><Icon name="key" size={20} /><div><strong>One-time webhook secret</strong><p>The URL is shown once after creation. Only the hash is retained.</p></div></div>}<div className="modal-actions"><Button tone="quiet" type="button" onClick={onClose}>Cancel</Button><Button tone="primary" type="submit">Create binding</Button></div></form></Modal>;
}
