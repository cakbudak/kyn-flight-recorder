import React, { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import { Icon } from "../icons.jsx";
import { formatTime, shortId, slugify, titleCase } from "../lib.js";
import {
  Badge,
  Button,
  CitedRuns,
  DefinitionList,
  EmptyState,
  Field,
  Modal,
  PageHeader
} from "./ui.jsx";

const STATUS_TONE = {
  quarantined: "warning",
  qualified: "success",
  promoted: "ai",
  rejected: "neutral",
  blocked: "danger"
};

function agentVersions(snapshot) {
  return snapshot.agents.flatMap((agent) =>
    agent.versions.map((version) => ({ resource: agent, version }))
  );
}

function sourceOptions(snapshot) {
  const flows = new Map(snapshot.studio.flows.map((flow) => [flow.id, flow]));
  const agentResources = new Map(snapshot.agents.flatMap((agent) =>
    agent.versions.map((version) => [version.id, agent.id])
  ));
  return snapshot.studio.runs
    .filter((run) => run.status === "completed" && run.ledger_verified)
    .flatMap((run) => run.model_calls
      .filter((call) => call.status === "completed")
      .map((call) => ({
        id: `${run.id}:${call.id}`,
        run,
        call,
        step: run.steps.find((step) => step.id === call.step_id),
        flow: flows.get(run.flow_id),
        agentId: agentResources.get(call.agent_version_id)
      })))
    .filter((source) => source.step?.status === "completed");
}

export default function CapabilityForge({ snapshot, mutate, busy, setView, focusRun }) {
  const candidates = snapshot.studio.skill_candidates ?? [];
  const sources = useMemo(() => sourceOptions(snapshot), [snapshot]);
  const agents = useMemo(() => agentVersions(snapshot), [snapshot]);
  const [selectedId, setSelectedId] = useState(candidates[0]?.id ?? null);
  const [showDistill, setShowDistill] = useState(false);
  const [decision, setDecision] = useState(null);
  const selected = candidates.find((candidate) => candidate.id === selectedId) ?? candidates[0] ?? null;
  const canDistill = sources.some((source) =>
    agents.some((agent) => agent.resource.id !== source.agentId)
  );

  useEffect(() => {
    if (candidates.some((candidate) => candidate.id === selectedId)) return;
    setSelectedId(candidates[0]?.id ?? null);
  }, [candidates, selectedId]);

  const qualify = (candidate) => mutate(
    () => api(`/api/v1/studio/skill-candidates/${candidate.id}/qualifications`, {
      method: "POST",
      body: {}
    }),
    { success: "Candidate provenance qualified" }
  );

  return (
    <section className="forge-page">
      <PageHeader
        eyebrow="Evidence-bound capability learning"
        title="Capability Forge"
        description="Turn one successful model Step into a quarantined, traceable Skill candidate. Code verifies provenance and zero authority gain; a human alone can publish the immutable Skill."
        actions={<>
          <Button tone="quiet" icon="docs" onClick={() => setView("docs")}>Read the contract</Button>
          <Button tone="primary" icon="plus" onClick={() => setShowDistill(true)} disabled={!canDistill}>Distil candidate</Button>
        </>}
      />

      <section className="forge-loop" aria-label="Capability Forge lifecycle">
        {[
          ["01", "Observe", "Completed Run · verified ledger", "run"],
          ["02", "Distil", "Independent Agent · strict output", "agent"],
          ["03", "Qualify", "Citations · hashes · zero authority", "lock"],
          ["04", "Promote", "Human decision · immutable Skill v1", "skill"]
        ].map(([number, title, detail, icon], index) => <React.Fragment key={number}>
          <article><span>{number}</span><i><Icon name={icon} size={18} /></i><strong>{title}</strong><small>{detail}</small></article>
          {index < 3 ? <em aria-hidden="true">→</em> : null}
        </React.Fragment>)}
      </section>

      <div className="forge-ceiling">
        <Icon name="warning" size={18} />
        <div>
          <strong>Qualification is not a performance claim.</strong>
          <p>It proves the candidate came from the cited completed Run, survived an independent distiller, and grants no authority. After promotion, attach the Skill to a successor Agent and prove the changed outcome in a new Run.</p>
        </div>
      </div>

      <div className="forge-workbench">
        <aside className="forge-list" aria-label="Skill candidates">
          <header><span>{candidates.length} candidate{candidates.length === 1 ? "" : "s"}</span><Badge tone="neutral">append-only</Badge></header>
          <div className="forge-list-scroll">
            {candidates.map((candidate) => (
              <button
                key={candidate.id}
                type="button"
                className={selected?.id === candidate.id ? "is-active" : ""}
                onClick={() => setSelectedId(candidate.id)}
                aria-current={selected?.id === candidate.id ? "true" : undefined}
              >
                <span className="forge-list-icon"><Icon name="skill" size={17} /></span>
                <span><strong>{candidate.name}</strong><small>{candidate.source.flow_name} · {shortId(candidate.source.run_id, 11)}</small></span>
                <Badge tone={STATUS_TONE[candidate.status] ?? "neutral"}>{titleCase(candidate.status)}</Badge>
              </button>
            ))}
            {!candidates.length ? <EmptyState
              icon="skill"
              title="No candidate in quarantine"
              description={sources.length
                ? "Choose a completed model-backed Run and let an independent Agent distil one narrow capability."
                : "Complete a model-backed Run first. Failed, running, or unverifiable Runs cannot teach the Forge."}
              action={canDistill
                ? <Button tone="primary" icon="plus" onClick={() => setShowDistill(true)}>Distil the first candidate</Button>
                : <Button tone="quiet" icon={sources.length ? "agent" : "run"} onClick={() => setView(sources.length ? "agents" : "runs")}>{sources.length ? "Create an independent Agent" : "Open Runs"}</Button>}
            /> : null}
          </div>
        </aside>

        <main className="forge-detail">
          {selected ? <CandidateDetail
            candidate={selected}
            busy={busy}
            qualify={qualify}
            onDecision={setDecision}
            focusRun={focusRun}
            setView={setView}
          /> : <EmptyState
            icon="skill"
            title="Select a candidate"
            description="Its source evidence, model receipt, qualification gates, and human decision will appear here."
          />}
        </main>
      </div>

      {showDistill ? <DistillModal
        sources={sources}
        agents={agents}
        busy={busy}
        mutate={mutate}
        onClose={() => setShowDistill(false)}
        onCreated={(candidate) => {
          setShowDistill(false);
          if (candidate) setSelectedId(candidate.id);
        }}
      /> : null}
      {decision ? <DecisionModal
        candidate={decision.candidate}
        kind={decision.kind}
        busy={busy}
        mutate={mutate}
        onClose={() => setDecision(null)}
        onDecided={() => setDecision(null)}
      /> : null}
    </section>
  );
}

function CandidateDetail({ candidate, busy, qualify, onDecision, focusRun, setView }) {
  const checks = candidate.qualification?.checks ?? [];
  return (
    <article className="candidate-detail">
      <header className="candidate-hero">
        <div>
          <p className="panel-kicker">Candidate {shortId(candidate.id, 14)} · {formatTime(candidate.created_at)}</p>
          <h2>{candidate.name}</h2>
          <p>{candidate.rationale}</p>
        </div>
        <Badge tone={STATUS_TONE[candidate.status] ?? "neutral"} dot>{titleCase(candidate.status)}</Badge>
      </header>

      <section className="candidate-stage-grid">
        <article className="is-complete"><span>01</span><Icon name="run" size={17} /><strong>Observed</strong><small>completed source</small></article>
        <article className="is-complete"><span>02</span><Icon name="agent" size={17} /><strong>Distilled</strong><small>strict model output</small></article>
        <article className={candidate.qualification?.passed ? "is-complete" : candidate.qualification ? "is-blocked" : "is-current"}><span>03</span><Icon name="lock" size={17} /><strong>Qualified</strong><small>{candidate.qualification ? (candidate.qualification.passed ? "all gates passed" : "gate blocked") : "awaiting code gate"}</small></article>
        <article className={candidate.decision ? (candidate.status === "promoted" ? "is-complete" : "is-blocked") : candidate.qualification?.passed ? "is-current" : ""}><span>04</span><Icon name="skill" size={17} /><strong>Decided</strong><small>{candidate.decision ? titleCase(candidate.decision.decision) : "awaiting human"}</small></article>
      </section>

      <section className="candidate-content-card">
        <header><div><p className="panel-kicker">Proposed behavioral capability</p><h3>Instructions</h3></div><Badge tone="warning">quarantined text</Badge></header>
        <blockquote>{candidate.instructions}</blockquote>
        <p className="candidate-hash"><Icon name="lock" size={14} />Candidate fingerprint <code>{candidate.fingerprint}</code></p>
      </section>

      <div className="candidate-proof-grid">
        <section className="candidate-content-card">
          <header><div><p className="panel-kicker">Source lineage</p><h3>One immutable observation</h3></div><Button tone="quiet" icon="run" onClick={() => focusRun(candidate.source.run_id)}>Open Run</Button></header>
          <DefinitionList items={[
            ["Flow", `${candidate.source.flow_name} · ${shortId(candidate.source.flow_version_id, 14)}`],
            ["Step", `${candidate.source.node_id} · ${shortId(candidate.source.step_id, 14)}`],
            ["Source Agent", <code key="source-agent">{shortId(candidate.source.agent_version_id, 18)}</code>],
            ["Source model call", <code key="source-call">{shortId(candidate.source.model_call_id, 18)}</code>],
            ["Finished", formatTime(candidate.source.finished_at)]
          ]} />
          <CitedRuns label="Source Run" ids={[candidate.source.run_id]} onSelectRun={focusRun} />
          <div className="candidate-event-citations">
            <p className="panel-kicker">Model-cited ledger events · {candidate.evidence_event_ids.length}</p>
            <div>{candidate.evidence_event_ids.map((id) => <code key={id}>{shortId(id, 16)}</code>)}</div>
          </div>
        </section>

        <section className="candidate-content-card">
          <header><div><p className="panel-kicker">Distillation receipt</p><h3>Independent model work</h3></div><Badge tone="ai">{candidate.distillation.model}</Badge></header>
          <DefinitionList items={[
            ["Distiller Agent", <code key="distiller">{shortId(candidate.distillation.agent_version_id, 18)}</code>],
            ["Provider response", <code key="provider">{shortId(candidate.distillation.provider_response_id, 18)}</code>],
            ["Input hash", <code key="input">{candidate.distillation.input_hash.slice(0, 18)}…</code>],
            ["Output hash", <code key="output">{candidate.distillation.output_hash.slice(0, 18)}…</code>],
            ["Tokens", candidate.distillation.usage?.total_tokens ?? "reported by provider"]
          ]} />
          <div className="authority-zero">
            <Icon name="lock" size={20} />
            <div><strong>Authority delta = 0</strong><p>0 static tools · 0 callable Actions · 0 Agents changed</p></div>
          </div>
        </section>
      </div>

      {candidate.qualification ? <section className={`qualification-panel ${candidate.qualification.passed ? "is-passed" : "is-blocked"}`}>
        <header><div><p className="panel-kicker">Deterministic provenance qualification</p><h3>{candidate.qualification.passed ? "Every code-owned gate passed" : "Candidate remains blocked"}</h3></div><Badge tone={candidate.qualification.passed ? "success" : "danger"}>{checks.filter((check) => check.passed).length}/{checks.length} gates</Badge></header>
        <div className="qualification-checks">{checks.map((check) => <article key={check.id}><span><Icon name={check.passed ? "check" : "warning"} size={15} /></span><div><strong>{titleCase(check.id)}</strong><p>{check.detail}</p></div></article>)}</div>
        <p className="candidate-hash"><Icon name="lock" size={14} />Observed source snapshot <code>{candidate.qualification.observed_source_snapshot_hash}</code></p>
      </section> : null}

      {candidate.decision ? <section className={`candidate-decision is-${candidate.decision.decision}`}>
        <Icon name={candidate.status === "promoted" ? "check" : "close"} size={22} />
        <div>
          <p className="panel-kicker">Human decision · {formatTime(candidate.decision.created_at)}</p>
          <h3>{candidate.status === "promoted" ? "Published as immutable Skill v1" : "Candidate rejected without deletion"}</h3>
          <p>{candidate.decision.reason}</p>
          <small>{candidate.decision.actor} acknowledged fingerprint <code>{candidate.decision.candidate_fingerprint.slice(0, 18)}…</code></small>
        </div>
        {candidate.promoted_skill ? <Button tone="primary" icon="skill" onClick={() => setView("skills")}>Open Skills</Button> : null}
      </section> : null}

      {!candidate.qualification ? <div className="candidate-actions">
        <div><strong>Run the provenance gate</strong><p>Code replays the source snapshot, event chain, citations, fingerprints, Agent independence, and authority delta. No model call is made.</p></div>
        <Button tone="primary" icon="lock" onClick={() => qualify(candidate)} disabled={busy}>Qualify candidate</Button>
        <Button tone="quiet" onClick={() => onDecision({ kind: "reject", candidate })} disabled={busy}>Reject</Button>
      </div> : candidate.qualification.passed && !candidate.decision ? <div className="candidate-actions is-ready">
        <div><strong>Human promotion fence</strong><p>Publishing creates one normal Skill v1 with these exact instructions and no authority. No Agent or Flow is silently changed.</p></div>
        <Button tone="primary" icon="skill" onClick={() => onDecision({ kind: "promote", candidate })} disabled={busy}>Review promotion</Button>
        <Button tone="quiet" onClick={() => onDecision({ kind: "reject", candidate })} disabled={busy}>Reject</Button>
      </div> : null}
    </article>
  );
}

function DistillModal({ sources, agents, busy, mutate, onClose, onCreated }) {
  const [sourceId, setSourceId] = useState(sources[0]?.id ?? "");
  const source = sources.find((item) => item.id === sourceId) ?? sources[0];
  const independent = agents.filter((agent) => agent.resource.id !== source?.agentId);
  const [distillerId, setDistillerId] = useState(independent[0]?.version.id ?? "");

  useEffect(() => {
    const eligible = agents.filter((agent) => agent.resource.id !== source?.agentId);
    if (eligible.some((agent) => agent.version.id === distillerId)) return;
    setDistillerId(eligible[0]?.version.id ?? "");
  }, [agents, source, distillerId]);

  const submit = async (event) => {
    event.preventDefault();
    const candidate = await mutate(
      () => api("/api/v1/studio/skill-candidates", {
        method: "POST",
        keyMode: "required",
        body: {
          source_run_id: source.run.id,
          source_model_call_id: source.call.id,
          distiller_agent_version_id: distillerId
        }
      }),
      { success: "Skill candidate distilled into quarantine" }
    );
    if (candidate) onCreated(candidate);
  };

  return <Modal
    title="Distil a Skill candidate"
    description="The model sees one bounded, code-owned source envelope. The resulting instructions gain no tools, Actions, or runtime authority."
    onClose={onClose}
    width="760px"
  >
    <form className="modal-form" onSubmit={submit}>
      <Field label="Completed source model Step" hint="Only completed Runs with a verified event ledger are eligible.">
        <select value={sourceId} onChange={(event) => setSourceId(event.target.value)}>
          {sources.map((item) => <option key={item.id} value={item.id}>{item.flow?.name ?? item.run.flow_id} · {item.step.node_id} · {item.call.model} · {shortId(item.run.id, 10)}</option>)}
        </select>
      </Field>
      {source ? <div className="forge-source-preview">
        <span><Icon name="run" size={17} /></span>
        <div><strong>{source.flow?.name ?? "Completed Flow"}</strong><p>{source.step.node_id} produced a completed model call with {source.call.usage?.total_tokens ?? "reported"} tokens.</p></div>
        <Badge tone="success">ledger verified</Badge>
      </div> : null}
      <Field label="Independent distiller Agent" hint="The Agent that produced the source cannot distil its own output.">
        <select required value={distillerId} onChange={(event) => setDistillerId(event.target.value)}>
          {independent.map((agent) => <option key={agent.version.id} value={agent.version.id}>{agent.resource.name} · v{agent.version.version} · {agent.version.model}</option>)}
        </select>
      </Field>
      <div className="forge-cost-note">
        <Icon name="key" size={18} />
        <div><strong>One browser-owned OpenAI call</strong><p>Strict JSON Schema · high reasoning effort · no tools · no provider storage · one immutable receipt.</p></div>
      </div>
      <div className="modal-actions">
        <Button tone="quiet" type="button" onClick={onClose}>Cancel</Button>
        <Button tone="primary" icon="agent" type="submit" disabled={busy || !source || !distillerId}>Distil into quarantine</Button>
      </div>
    </form>
  </Modal>;
}

function DecisionModal({ candidate, kind, busy, mutate, onClose, onDecided }) {
  const promote = kind === "promote";
  const [form, setForm] = useState({
    name: candidate.name,
    slug: slugify(candidate.name),
    actor: "capability-owner",
    reason: promote
      ? "The provenance gates passed and this bounded instruction is reusable."
      : "This candidate should not become a reusable workspace capability.",
    acknowledged: false
  });
  const submit = async (event) => {
    event.preventDefault();
    const path = `/api/v1/studio/skill-candidates/${candidate.id}/${promote ? "promotion" : "rejection"}`;
    const body = promote
      ? form
      : { actor: form.actor, reason: form.reason, acknowledged: form.acknowledged };
    const result = await mutate(
      () => api(path, { method: "POST", body }),
      { success: promote ? "Candidate promoted to Skill v1" : "Candidate rejected and retained" }
    );
    if (result) onDecided(result);
  };
  return <Modal
    title={promote ? "Promote immutable Skill v1" : "Reject candidate"}
    description={promote
      ? "This publishes the candidate instructions exactly as shown, with zero tool or Action authority. Assign it to an Agent only through a later successor version."
      : "Rejection is append-only. The candidate, model receipt, and cited evidence remain visible."}
    onClose={onClose}
  >
    <form className="modal-form" onSubmit={submit}>
      {promote ? <div className="field-grid two">
        <Field label="Skill name"><input required value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value, slug: slugify(event.target.value) })} /></Field>
        <Field label="Skill slug"><input required value={form.slug} onChange={(event) => setForm({ ...form, slug: event.target.value })} /></Field>
      </div> : null}
      <Field label="Decision actor"><input required value={form.actor} onChange={(event) => setForm({ ...form, actor: event.target.value })} /></Field>
      <Field label="Reason" hint="Recorded beside the exact candidate fingerprint."><textarea required minLength="12" rows="4" value={form.reason} onChange={(event) => setForm({ ...form, reason: event.target.value })} /></Field>
      <label className="acknowledgement"><input type="checkbox" checked={form.acknowledged} onChange={(event) => setForm({ ...form, acknowledged: event.target.checked })} /><span><strong>I acknowledge the exact candidate and its boundary.</strong><small>{promote ? "This proves provenance, not improved performance, and changes no Agent or Flow." : "The rejected candidate will remain in the audit history."}</small></span></label>
      <div className="modal-actions">
        <Button tone="quiet" type="button" onClick={onClose}>Cancel</Button>
        <Button tone={promote ? "primary" : "danger"} icon={promote ? "skill" : "close"} type="submit" disabled={busy || !form.acknowledged}>{promote ? "Publish Skill v1" : "Reject candidate"}</Button>
      </div>
    </form>
  </Modal>;
}
