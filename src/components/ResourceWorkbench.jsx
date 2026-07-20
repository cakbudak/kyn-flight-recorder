import React, { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import { Icon } from "../icons.jsx";
import {
  ACTION_PRESETS,
  SUCCESS_ERROR,
  clone,
  parseJson,
  shortId,
  slugDraft,
  slugify,
  titleCase
} from "../lib.js";
import {
  Badge,
  Button,
  EmptyState,
  Field,
  JsonField,
  PageHeader
} from "./ui.jsx";

const META = {
  actions: {
    singular: "Action",
    icon: "action",
    eyebrow: "Typed capability registry",
    description: "One versioned invocation contract for graph nodes and Agent-granted Actions. Select any seeded or custom Action and publish an immutable successor."
  },
  agents: {
    singular: "Agent",
    icon: "agent",
    eyebrow: "Model behavior and authority",
    description: "Pin a model to exact Prompt and Skill versions. Nothing is hidden in a workflow node or mutable global string."
  },
  prompts: {
    singular: "Prompt",
    icon: "prompt",
    eyebrow: "Versioned instruction templates",
    description: "Declare every runtime variable and publish forward-only Prompt successors without changing Agents already in flight."
  },
  skills: {
    singular: "Skill",
    icon: "skill",
    eyebrow: "Explicit capability grants",
    description: "Bundle instructions with a bounded allowlist of static tools and exact Action versions. Model prose never grants authority."
  }
};

export default function ResourceWorkbench({ snapshot, mutate, busy, kind }) {
  const meta = META[kind];
  const inventory = kind === "actions" ? snapshot.studio.actions : snapshot[kind];
  const [selectedId, setSelectedId] = useState(inventory[0]?.id ?? null);
  const [creating, setCreating] = useState(false);
  const [query, setQuery] = useState("");

  useEffect(() => {
    if (creating || inventory.some((item) => item.id === selectedId)) return;
    setSelectedId(inventory[0]?.id ?? null);
  }, [creating, inventory, selectedId]);

  const selected = inventory.find((item) => item.id === selectedId) ?? null;
  const filtered = useMemo(() => inventory.filter((item) => `${item.name} ${item.slug} ${item.description ?? item.version.instructions ?? ""}`.toLowerCase().includes(query.toLowerCase())), [inventory, query]);
  const select = (id) => { setCreating(false); setSelectedId(id); };
  const saved = (result) => { if (result) { setCreating(false); setSelectedId(result.id); } };

  return (
    <section className="registry-page">
      <PageHeader
        eyebrow={meta.eyebrow}
        title={kind === "actions" ? "Actions" : titleCase(kind)}
        description={meta.description}
        actions={<Button tone="primary" icon="plus" onClick={() => { setCreating(true); setSelectedId(null); }}>New {meta.singular}</Button>}
      />
      <div className="registry-workbench">
        <aside className="registry-list" aria-label={`${meta.singular} definitions`}>
          <label className="search-box"><Icon name="search" size={16} /><input type="search" placeholder={`Find ${kind}…`} value={query} onChange={(event) => setQuery(event.target.value)} /></label>
          <div className="registry-list-meta"><span>{filtered.length} of {inventory.length}</span><Badge tone="neutral">immutable versions</Badge></div>
          <div className="registry-scroll">
            {creating ? <button type="button" className="registry-item is-active"><span className="resource-avatar"><Icon name={meta.icon} size={18} /></span><span><strong>Untitled {meta.singular}</strong><small>Unpublished draft</small></span><Badge tone="warning">new</Badge></button> : null}
            {filtered.map((item) => (
              <button key={item.id} type="button" className={`registry-item ${selected?.id === item.id && !creating ? "is-active" : ""}`} onClick={() => select(item.id)}>
                <span className={`resource-avatar avatar-${item.version.kind ?? kind}`}><Icon name={meta.icon} size={18} /></span>
                <span><strong>{item.name}</strong><small>{item.slug}</small></span>
                <Badge tone="neutral">v{item.current_version}</Badge>
              </button>
            ))}
            {!filtered.length && !creating ? <EmptyState icon={meta.icon} title={`No ${kind} found`} description="Change the search or create a new definition." /> : null}
          </div>
        </aside>
        <main className="registry-editor">
          {kind === "actions" ? <ActionEditor key={`action-${creating ? "new" : `${selected?.id}-${selected?.current_version}`}`} snapshot={snapshot} resource={creating ? null : selected} mutate={mutate} busy={busy} onSaved={saved} /> : null}
          {kind === "prompts" ? <PromptEditor key={`prompt-${creating ? "new" : `${selected?.id}-${selected?.current_version}`}`} resource={creating ? null : selected} mutate={mutate} busy={busy} onSaved={saved} /> : null}
          {kind === "skills" ? <SkillEditor key={`skill-${creating ? "new" : `${selected?.id}-${selected?.current_version}`}`} snapshot={snapshot} resource={creating ? null : selected} mutate={mutate} busy={busy} onSaved={saved} /> : null}
          {kind === "agents" ? <AgentEditor key={`agent-${creating ? "new" : `${selected?.id}-${selected?.current_version}`}`} snapshot={snapshot} resource={creating ? null : selected} mutate={mutate} busy={busy} onSaved={saved} /> : null}
        </main>
      </div>
    </section>
  );
}

function EditorHeader({ icon, title, subtitle, version, fingerprint, children }) {
  return (
    <header className="editor-header">
      <div className="editor-title"><span className="resource-avatar"><Icon name={icon} size={20} /></span><div><p className="panel-kicker">{subtitle}</p><h2>{title}</h2><small>{fingerprint ? `${shortId(fingerprint, 16)} · append-only` : "Not published yet"}</small></div></div>
      <div className="editor-actions">{version ? <Badge tone="success">current v{version}</Badge> : <Badge tone="warning">draft</Badge>}{children}</div>
    </header>
  );
}

function ActionEditor({ snapshot, resource, mutate, busy, onSaved }) {
  const current = resource?.version;
  const initialKind = current?.kind ?? "template";
  const preset = ACTION_PRESETS[initialKind] ?? ACTION_PRESETS.template;
  const [form, setForm] = useState({
    name: resource?.name ?? "Untitled Action",
    slug: resource?.slug ?? "untitled-action",
    description: resource?.description ?? preset.description,
    kind: initialKind,
    input: JSON.stringify(current?.input_schema ?? preset.input_schema, null, 2),
    output: JSON.stringify(current?.output_schema ?? preset.output_schema, null, 2),
    config: JSON.stringify(current?.config ?? preset.config, null, 2),
    outcomes: clone(current?.outcomes ?? preset.outcomes),
    agentVersionId: current?.agent_version_id ?? snapshot.agents[0]?.version.id ?? ""
  });
  const [tab, setTab] = useState("contract");

  const patch = (field, value) => setForm((state) => ({ ...state, [field]: value }));
  const applyKind = (kind) => {
    const next = ACTION_PRESETS[kind] ?? ACTION_PRESETS.template;
    setForm((state) => ({ ...state, kind, description: next.description, input: JSON.stringify(next.input_schema, null, 2), output: JSON.stringify(next.output_schema, null, 2), config: JSON.stringify(next.config, null, 2), outcomes: clone(next.outcomes), agentVersionId: kind === "ai" ? (state.agentVersionId || snapshot.agents[0]?.version.id || "") : "" }));
  };

  const submit = async (event) => {
    event.preventDefault();
    let body;
    try {
      body = {
        name: form.name,
        description: form.description,
        kind: form.kind,
        input_schema: parseJson(form.input, "Action input schema"),
        output_schema: parseJson(form.output, "Action output schema"),
        outcomes: form.outcomes,
        config: parseJson(form.config, "Action config"),
        agent_version_id: form.kind === "ai" ? form.agentVersionId : null
      };
    } catch (error) {
      await mutate(() => Promise.reject(error), { refreshAfter: false, success: "" });
      return;
    }
    const result = await mutate(
      () => resource
        ? api(`/api/v1/studio/actions/${resource.id}/versions`, { method: "POST", body: { ...body, expected_version: resource.current_version } })
        : api("/api/v1/studio/actions", { method: "POST", body: { ...body, slug: form.slug } }),
      { success: resource ? `Action v${resource.current_version + 1} published` : "Action v1 published" }
    );
    onSaved(result);
  };

  const selectedAgent = snapshot.agents.find((agent) => agent.version.id === form.agentVersionId);
  return (
    <form className="definition-editor" onSubmit={submit}>
      <EditorHeader icon="action" title={form.name} subtitle={`${titleCase(form.kind)} Action`} version={resource?.current_version} fingerprint={current?.fingerprint}><Button tone="primary" icon="save" type="submit" disabled={busy}>Publish {resource ? "successor" : "v1"}</Button></EditorHeader>
      <div className="editor-tabs" role="tablist" aria-label="Action editor sections">
        {["contract", "execution", "outputs", "versions"].map((item) => <button key={item} type="button" role="tab" aria-selected={tab === item} className={tab === item ? "is-active" : ""} onClick={() => setTab(item)}>{titleCase(item)}</button>)}
      </div>
      <div className="editor-scroll">
        {tab === "contract" ? <>
          <section className="form-section"><div className="form-section-title"><span>01</span><div><h3>Identity and purpose</h3><p>Stable resource identity; revisions append immutable execution versions.</p></div></div><div className="field-grid two"><Field label="Name" required><input required value={form.name} onChange={(event) => { patch("name", event.target.value); if (!resource) patch("slug", slugify(event.target.value)); }} /></Field><Field label="Slug" hint={resource ? "Immutable after v1" : "Stable lowercase identifier"}><input required disabled={Boolean(resource)} value={form.slug} onChange={(event) => patch("slug", slugDraft(event.target.value))} onBlur={(event) => patch("slug", slugify(event.target.value))} /></Field></div><Field label="Description" required><textarea required rows="3" value={form.description} onChange={(event) => patch("description", event.target.value)} /></Field><Field label="Executor kind"><select value={form.kind} onChange={(event) => applyKind(event.target.value)}>{Object.entries(ACTION_PRESETS).map(([value, item]) => <option key={value} value={value}>{item.label}</option>)}{form.kind === "sandbox" ? <option value="sandbox">Sandbox (legacy seed)</option> : null}</select></Field></section>
          <section className="form-section"><div className="form-section-title"><span>02</span><div><h3>Typed boundary</h3><p>Both sides are validated on every invocation, including Agent-requested calls.</p></div></div><div className="json-grid"><JsonField label="Input schema" value={form.input} onChange={(value) => patch("input", value)} rows={15} hint="Strict JSON Schema object" /><JsonField label="Output schema" value={form.output} onChange={(value) => patch("output", value)} rows={15} hint="Strict JSON Schema object" /></div></section>
        </> : null}
        {tab === "execution" ? <section className="form-section"><div className="form-section-title"><span>03</span><div><h3>{form.kind === "ai" ? "AI stack and model policy" : "Bounded executor configuration"}</h3><p>Database config selects one code-owned executor. It can never register server code.</p></div></div>{form.kind === "ai" ? <><Field label="Pinned Agent version" hint="The Agent itself pins its Prompt and Skills."><select value={form.agentVersionId} onChange={(event) => patch("agentVersionId", event.target.value)}>{snapshot.agents.map((agent) => <option key={agent.version.id} value={agent.version.id}>{agent.name} · {agent.version.model} · v{agent.version.version}</option>)}</select></Field>{selectedAgent ? <AgentStackCard snapshot={snapshot} agent={selectedAgent} /> : <EmptyState icon="agent" title="Create an Agent first" description="An AI Action cannot run without an immutable Agent pin." />}</> : null}<JsonField label="Executor config" value={form.config} onChange={(value) => patch("config", value)} rows={16} hint="Allowlisted shape, validated by the control plane" /></section> : null}
        {tab === "outputs" ? <section className="form-section"><div className="form-section-title"><span>04</span><div><h3>Named outcomes</h3><p>Each outcome becomes an independent source port on the Flow canvas. Error is mandatory.</p></div></div><OutcomeForm outcomes={form.outcomes} onChange={(value) => patch("outcomes", value)} /><div className="port-preview">{form.outcomes.map((outcome) => <span key={outcome.id} className={`tone-${outcome.tone}`}><i />{outcome.label}<small>{outcome.id}</small></span>)}</div></section> : null}
        {tab === "versions" ? <VersionHistory resource={resource} kind="Action" render={(version) => `${titleCase(version.kind)} · ${version.outcomes.length} outputs · ${version.effect_level}`} /> : null}
      </div>
    </form>
  );
}

function PromptEditor({ resource, mutate, busy, onSaved }) {
  const current = resource?.version;
  const [form, setForm] = useState({ name: resource?.name ?? "Untitled Prompt", slug: resource?.slug ?? "untitled-prompt", template: current?.template ?? "Analyze {{brief}} and return only contract-bound evidence.", variables: (current?.variables ?? ["brief"]).join(", ") });
  const submit = async (event) => {
    event.preventDefault();
    const body = { name: form.name, template: form.template, variables: form.variables.split(",").map((item) => item.trim()).filter(Boolean) };
    const result = await mutate(() => resource ? api(`/api/v1/prompts/${resource.id}/versions`, { method: "POST", body: { ...body, expected_version: resource.current_version } }) : api("/api/v1/prompts", { method: "POST", body: { ...body, slug: form.slug } }), { success: resource ? `Prompt v${resource.current_version + 1} published` : "Prompt v1 published" });
    onSaved(result);
  };
  return <form className="definition-editor" onSubmit={submit}><EditorHeader icon="prompt" title={form.name} subtitle="Prompt template" version={resource?.current_version} fingerprint={current?.fingerprint}><Button tone="primary" icon="save" type="submit" disabled={busy}>Publish {resource ? "successor" : "v1"}</Button></EditorHeader><div className="editor-scroll single-column"><section className="form-section"><div className="form-section-title"><span>01</span><div><h3>Identity</h3><p>The slug stays stable while the template advances.</p></div></div><div className="field-grid two"><Field label="Name"><input required value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value, slug: resource ? form.slug : slugify(event.target.value) })} /></Field><Field label="Slug" hint={resource ? "Immutable after v1" : "Stable identifier"}><input required disabled={Boolean(resource)} value={form.slug} onChange={(event) => setForm({ ...form, slug: slugDraft(event.target.value) })} onBlur={(event) => setForm((currentForm) => ({ ...currentForm, slug: slugify(event.target.value) }))} /></Field></div></section><section className="form-section"><div className="form-section-title"><span>02</span><div><h3>Declared variables</h3><p>Template placeholders and this list must match exactly.</p></div></div><Field label="Variables" hint="Comma separated, for example: brief, audience"><input value={form.variables} onChange={(event) => setForm({ ...form, variables: event.target.value })} /></Field><Field label="Prompt template"><textarea className="prompt-textarea" required rows="15" value={form.template} onChange={(event) => setForm({ ...form, template: event.target.value })} /></Field><div className="template-preview"><p className="panel-kicker">Template preview</p><pre>{form.template}</pre></div></section><VersionHistory resource={resource} kind="Prompt" render={(version) => `${version.variables.length} variables · ${version.template.length} characters`} /></div></form>;
}

function SkillEditor({ snapshot, resource, mutate, busy, onSaved }) {
  const current = resource?.version;
  const [form, setForm] = useState({ name: resource?.name ?? "Untitled Skill", slug: resource?.slug ?? "untitled-skill", instructions: current?.instructions ?? "Use only the explicit capabilities granted below.", tools: current?.allowed_tools ?? [], actions: current?.allowed_action_version_ids ?? [] });
  const toggle = (field, id) => setForm((state) => ({ ...state, [field]: state[field].includes(id) ? state[field].filter((item) => item !== id) : [...state[field], id] }));
  const submit = async (event) => {
    event.preventDefault();
    const body = { name: form.name, instructions: form.instructions, allowed_tools: form.tools, allowed_action_version_ids: form.actions };
    const result = await mutate(() => resource ? api(`/api/v1/skills/${resource.id}/versions`, { method: "POST", body: { ...body, expected_version: resource.current_version } }) : api("/api/v1/skills", { method: "POST", body: { ...body, slug: form.slug } }), { success: resource ? `Skill v${resource.current_version + 1} published` : "Skill v1 published" });
    onSaved(result);
  };
  return <form className="definition-editor" onSubmit={submit}><EditorHeader icon="skill" title={form.name} subtitle="Authority bundle" version={resource?.current_version} fingerprint={current?.fingerprint}><Button tone="primary" icon="save" type="submit" disabled={busy}>Publish {resource ? "successor" : "v1"}</Button></EditorHeader><div className="editor-scroll single-column"><section className="form-section"><div className="form-section-title"><span>01</span><div><h3>Identity and instructions</h3><p>A Skill combines guidance with executable authority. They are inspected separately at runtime.</p></div></div><div className="field-grid two"><Field label="Name"><input required value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value, slug: resource ? form.slug : slugify(event.target.value) })} /></Field><Field label="Slug"><input required disabled={Boolean(resource)} value={form.slug} onChange={(event) => setForm({ ...form, slug: slugDraft(event.target.value) })} onBlur={(event) => setForm((currentForm) => ({ ...currentForm, slug: slugify(event.target.value) }))} /></Field></div><Field label="Instructions"><textarea required rows="7" value={form.instructions} onChange={(event) => setForm({ ...form, instructions: event.target.value })} /></Field></section><section className="form-section"><div className="form-section-title"><span>02</span><div><h3>Static tool authority</h3><p>These are code-owned sandbox tools. A Skill can grant them, never create new implementation.</p></div></div><div className="choice-grid">{[{ id: "inspect_release_policy", name: "Inspect release policy", detail: "Read one pinned sandbox policy." }, { id: "stage_release", name: "Stage release", detail: "Create one bounded sandbox receipt/effect." }].map((tool) => <Choice key={tool.id} checked={form.tools.includes(tool.id)} onChange={() => toggle("tools", tool.id)} icon="code" title={tool.name} detail={tool.detail} meta={tool.id} />)}</div></section><section className="form-section"><div className="form-section-title"><span>03</span><div><h3>Callable Action authority</h3><p>The Agent may request only these exact immutable Action versions.</p></div></div><div className="choice-grid dense">{snapshot.studio.actions.map((action) => <Choice key={action.version.id} checked={form.actions.includes(action.version.id)} onChange={() => toggle("actions", action.version.id)} icon="action" title={action.name} detail={`${titleCase(action.version.kind)} · ${action.version.outcomes.length} outcomes`} meta={`v${action.version.version}`} />)}</div></section><VersionHistory resource={resource} kind="Skill" render={(version) => `${version.allowed_tools.length} tools · ${version.allowed_action_version_ids.length} Actions`} /></div></form>;
}

function AgentEditor({ snapshot, resource, mutate, busy, onSaved }) {
  const current = resource?.version;
  const [form, setForm] = useState({ name: resource?.name ?? "Untitled Agent", slug: resource?.slug ?? "untitled-agent", role: current?.role ?? "executor", model: current?.model ?? "gpt-5.6", instructions: current?.instructions ?? "Use pinned evidence and return only the requested contract.", prompt: current?.prompt_version_id ?? snapshot.prompts[0]?.version.id ?? "", skills: current?.skill_version_ids ?? [] });
  const toggleSkill = (id) => setForm((state) => ({ ...state, skills: state.skills.includes(id) ? state.skills.filter((item) => item !== id) : [...state.skills, id] }));
  const submit = async (event) => {
    event.preventDefault();
    const body = { name: form.name, role: form.role, model: form.model, instructions: form.instructions, prompt_version_id: form.prompt, skill_version_ids: form.skills };
    const result = await mutate(() => resource ? api(`/api/v1/agents/${resource.id}/versions`, { method: "POST", body: { ...body, expected_version: resource.current_version } }) : api("/api/v1/agents", { method: "POST", body: { ...body, slug: form.slug } }), { success: resource ? `Agent v${resource.current_version + 1} published` : "Agent v1 published" });
    onSaved(result);
  };
  const prompt = snapshot.prompts.find((item) => item.version.id === form.prompt);
  return <form className="definition-editor" onSubmit={submit}><EditorHeader icon="agent" title={form.name} subtitle="Agent definition" version={resource?.current_version} fingerprint={current?.fingerprint}><Button tone="primary" icon="save" type="submit" disabled={busy || !form.prompt}>Publish {resource ? "successor" : "v1"}</Button></EditorHeader><div className="editor-scroll single-column"><section className="form-section"><div className="form-section-title"><span>01</span><div><h3>Identity and role</h3><p>The Agent is a versioned runtime input, not a name attached to arbitrary behavior.</p></div></div><div className="field-grid two"><Field label="Name"><input required value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value, slug: resource ? form.slug : slugify(event.target.value) })} /></Field><Field label="Slug"><input required disabled={Boolean(resource)} value={form.slug} onChange={(event) => setForm({ ...form, slug: slugDraft(event.target.value) })} onBlur={(event) => setForm((currentForm) => ({ ...currentForm, slug: slugify(event.target.value) }))} /></Field><Field label="Role"><select value={form.role} onChange={(event) => setForm({ ...form, role: event.target.value })}><option value="executor">Executor</option><option value="diagnostician">Diagnostician</option><option value="repairer">Repairer</option></select></Field><Field label="OpenAI model"><select value={form.model} onChange={(event) => setForm({ ...form, model: event.target.value })}><option value="gpt-5.6">gpt-5.6</option><option value="gpt-5.6-sol">gpt-5.6-sol</option><option value="gpt-5.6-terra">gpt-5.6-terra</option><option value="gpt-5.6-luna">gpt-5.6-luna</option></select></Field></div><Field label="Agent instructions"><textarea required rows="7" value={form.instructions} onChange={(event) => setForm({ ...form, instructions: event.target.value })} /></Field></section><section className="form-section"><div className="form-section-title"><span>02</span><div><h3>Prompt pin</h3><p>The Prompt supplies the exact variables an AI Action must satisfy.</p></div></div><Field label="Prompt version"><select value={form.prompt} onChange={(event) => setForm({ ...form, prompt: event.target.value })}>{snapshot.prompts.map((item) => <option key={item.version.id} value={item.version.id}>{item.name} · v{item.version.version} · {item.version.variables.join(", ")}</option>)}</select></Field>{prompt ? <div className="template-preview"><p className="panel-kicker">Pinned template</p><pre>{prompt.version.template}</pre></div> : null}</section><section className="form-section"><div className="form-section-title"><span>03</span><div><h3>Skill pins</h3><p>Effective tools and callable Actions are the union of these exact versions.</p></div></div><div className="choice-grid">{snapshot.skills.map((skill) => <Choice key={skill.version.id} checked={form.skills.includes(skill.version.id)} onChange={() => toggleSkill(skill.version.id)} icon="skill" title={skill.name} detail={`${skill.version.allowed_tools.length} tools · ${skill.version.allowed_action_version_ids.length} Actions`} meta={`v${skill.version.version}`} />)}</div></section><EffectiveAuthority snapshot={snapshot} skillIds={form.skills} /><VersionHistory resource={resource} kind="Agent" render={(version) => `${version.model} · ${version.skill_version_ids.length} Skills · ${version.effective_tools.length} tools`} /></div></form>;
}

function AgentStackCard({ snapshot, agent }) {
  const prompt = snapshot.prompts.find((item) => item.version.id === agent.version.prompt_version_id);
  const skills = snapshot.skills.filter((item) => agent.version.skill_version_ids.includes(item.version.id));
  return <div className="stack-card"><header><span className="resource-avatar avatar-agent"><Icon name="agent" size={18} /></span><div><strong>{agent.name}</strong><small>{agent.version.role} · {agent.version.model}</small></div><Badge tone="ai">v{agent.version.version}</Badge></header><div><p><Icon name="prompt" size={15} /><span><strong>{prompt?.name ?? "Missing Prompt"}</strong><small>{prompt ? `v${prompt.version.version} · ${prompt.version.variables.join(", ")}` : "Broken pin"}</small></span></p>{skills.map((skill) => <p key={skill.id}><Icon name="skill" size={15} /><span><strong>{skill.name}</strong><small>v{skill.version.version} · {skill.version.allowed_tools.length} tools · {skill.version.allowed_action_version_ids.length} Actions</small></span></p>)}</div></div>;
}

function OutcomeForm({ outcomes, onChange }) {
  const update = (index, field, value) => onChange(outcomes.map((item, itemIndex) => itemIndex === index ? { ...item, [field]: field === "id" ? slugify(value) : value } : item));
  return <div className="outcome-form"><div className="outcome-head"><span>Label</span><span>Port ID</span><span>Tone</span><span /></div>{outcomes.map((outcome, index) => <div className="outcome-row" key={`${outcome.id}-${index}`}><input value={outcome.label} aria-label={`Outcome ${index + 1} label`} onChange={(event) => update(index, "label", event.target.value)} /><input value={outcome.id} aria-label={`Outcome ${index + 1} ID`} disabled={outcome.id === "error"} onChange={(event) => update(index, "id", event.target.value)} /><select value={outcome.tone} aria-label={`Outcome ${index + 1} tone`} onChange={(event) => update(index, "tone", event.target.value)}><option value="neutral">Neutral</option><option value="success">Success</option><option value="warning">Warning</option><option value="danger">Danger</option><option value="ai">AI</option></select><button type="button" className="icon-button" aria-label={`Remove ${outcome.label}`} disabled={outcome.id === "error" || outcomes.length <= 2} onClick={() => onChange(outcomes.filter((_, itemIndex) => itemIndex !== index))}><Icon name="trash" size={16} /></button></div>)}<Button tone="quiet" icon="plus" type="button" disabled={outcomes.length >= 12} onClick={() => onChange([...outcomes.slice(0, -1), { id: `outcome-${outcomes.length}`, label: `Outcome ${outcomes.length}`, description: "", tone: "neutral" }, outcomes.at(-1) ?? SUCCESS_ERROR[1]])}>Add output</Button></div>;
}

function Choice({ checked, onChange, icon, title, detail, meta }) {
  return <label className={`choice-card ${checked ? "is-checked" : ""}`}><input type="checkbox" checked={checked} onChange={onChange} /><span className="resource-avatar"><Icon name={icon} size={17} /></span><span><strong>{title}</strong><small>{detail}</small></span><Badge tone={checked ? "success" : "neutral"}>{meta}</Badge></label>;
}

function EffectiveAuthority({ snapshot, skillIds }) {
  const versions = snapshot.skills.flatMap((resource) => resource.versions).filter((version) => skillIds.includes(version.id));
  const tools = [...new Set(versions.flatMap((version) => version.allowed_tools))];
  const actions = [...new Set(versions.flatMap((version) => version.allowed_action_version_ids))];
  return <section className="form-section authority-summary"><div className="form-section-title"><span>04</span><div><h3>Effective authority</h3><p>This is what the runtime will actually intersect with model requests.</p></div></div><div className="authority-metrics"><article><strong>{tools.length}</strong><span>static tools</span><small>{tools.join(", ") || "None"}</small></article><article><strong>{actions.length}</strong><span>Action versions</span><small>{actions.length ? "Exact immutable pins" : "None"}</small></article><article><strong>{skillIds.length}</strong><span>Skill versions</span><small>No ambient grants</small></article></div></section>;
}

function VersionHistory({ resource, kind, render }) {
  if (!resource) return <section className="form-section"><div className="form-section-title"><span>→</span><div><h3>First publication</h3><p>Publishing creates immutable v1. Later edits append successors and preserve every prior pin.</p></div></div></section>;
  return <section className="form-section"><div className="form-section-title"><span>↗</span><div><h3>Version history</h3><p>{kind} versions are immutable and ordered newest first.</p></div></div><div className="large-version-list">{resource.versions.map((version) => <article key={version.id}><span>v{version.version}</span><div><strong>{render(version)}</strong><small>{version.id} · {version.fingerprint.slice(0, 20)}…</small></div>{version.version === resource.current_version ? <Badge tone="success">current</Badge> : <Badge tone="neutral">pinned history</Badge>}</article>)}</div></section>;
}
