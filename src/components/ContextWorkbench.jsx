import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { Icon } from "../icons.jsx";
import { formatTime, shortId, slugDraft, slugify, titleCase } from "../lib.js";
import { Badge, Button, EmptyState, Field, Modal, PageHeader, Segmented } from "./ui.jsx";

const READ_MODES = [
  { id: "glance", label: "Glance", detail: "Opening window + headings" },
  { id: "outline", label: "Outline", detail: "Structure before detail" },
  { id: "focus", label: "Focus", detail: "Exact bounded line range" },
  { id: "grep", label: "Grep", detail: "Literal matches with context" },
  { id: "full", label: "Full", detail: "Whole source up to 96 KiB" }
];

export default function ContextWorkbench({ snapshot, mutate, busy, onUseInBoardRoom, focusRun }) {
  const studio = snapshot.studio;
  const sources = studio.knowledge_sources ?? [];
  const candidates = studio.memory_candidates ?? [];
  const memories = studio.memories ?? [];
  const [tab, setTab] = useState("read");
  const [selectedSourceId, setSelectedSourceId] = useState(sources[0]?.id ?? null);
  const [sourceDialog, setSourceDialog] = useState(null);

  useEffect(() => {
    if (sources.some((source) => source.id === selectedSourceId)) return;
    setSelectedSourceId(sources[0]?.id ?? null);
  }, [selectedSourceId, sources]);

  const selectedSource = sources.find((source) => source.id === selectedSourceId) ?? null;
  return (
    <section className="context-page">
      <PageHeader
        eyebrow="Cited context · governed learning"
        title="Context & Memory"
        description="Import immutable knowledge, read only what the job needs, carry exact citations into agent work, and promote Run evidence to recallable Memory only through a human-owned gate."
        actions={<Button tone="primary" icon="plus" onClick={() => setSourceDialog({ mode: "create", source: null })}>Import source</Button>}
      />
      <div className="context-loop" aria-label="Context lifecycle">
        {[
          ["01", "Import", "immutable source", "context"],
          ["02", "SmartRead", "bounded + cited", "read"],
          ["03", "Use", "Agent or Flow input", "agent"],
          ["04", "Observe", "verified Run events", "run"],
          ["05", "Promote", "human-governed Memory", "memory"]
        ].map(([number, name, detail, icon], index) => <React.Fragment key={number}><article><span>{number}</span><Icon name={icon} size={18} /><div><strong>{name}</strong><small>{detail}</small></div></article>{index < 4 ? <i aria-hidden="true">→</i> : null}</React.Fragment>)}
      </div>
      <Segmented
        label="Context workspace"
        value={tab}
        onChange={setTab}
        items={[
          { value: "read", label: "SmartRead", count: sources.length },
          { value: "search", label: "Knowledge search" },
          { value: "memory", label: "Governed Memory", count: memories.filter((item) => item.state === "active").length }
        ]}
      />

      {tab === "read" ? <SmartReadWorkspace
        sources={sources}
        selectedSource={selectedSource}
        setSelectedSourceId={setSelectedSourceId}
        setSourceDialog={setSourceDialog}
        mutate={mutate}
        busy={busy}
        onUseInBoardRoom={onUseInBoardRoom}
      /> : null}
      {tab === "search" ? <KnowledgeSearch mutate={mutate} onUseInBoardRoom={onUseInBoardRoom} /> : null}
      {tab === "memory" ? <MemoryWorkspace snapshot={snapshot} candidates={candidates} memories={memories} mutate={mutate} busy={busy} focusRun={focusRun} /> : null}

      {sourceDialog ? <SourceModal
        source={sourceDialog.source}
        mutate={mutate}
        busy={busy}
        onClose={() => setSourceDialog(null)}
        onSaved={(source) => { if (source) setSelectedSourceId(source.id); setSourceDialog(null); }}
      /> : null}
    </section>
  );
}

function SmartReadWorkspace({ sources, selectedSource, setSelectedSourceId, setSourceDialog, mutate, busy, onUseInBoardRoom }) {
  const [versionId, setVersionId] = useState(selectedSource?.version.id ?? "");
  const [mode, setMode] = useState("glance");
  const [query, setQuery] = useState("");
  const [lineStart, setLineStart] = useState(1);
  const [lineEnd, setLineEnd] = useState(40);
  const [result, setResult] = useState(null);
  const selectedVersion = selectedSource?.versions.find((version) => version.id === versionId) ?? selectedSource?.version ?? null;

  useEffect(() => {
    setVersionId(selectedSource?.version.id ?? "");
    setResult(null);
  }, [selectedSource?.id, selectedSource?.version.id]);
  useEffect(() => {
    const lineCount = Math.max(1, selectedVersion?.line_count ?? 1);
    setLineStart(1);
    setLineEnd(Math.min(40, lineCount));
  }, [selectedVersion?.id, selectedVersion?.line_count]);

  const read = async (event) => {
    event.preventDefault();
    const body = { source_version_id: versionId, mode };
    if (mode === "grep") Object.assign(body, { query, max_results: 12 });
    if (mode === "focus") Object.assign(body, { line_start: Number(lineStart), line_end: Number(lineEnd) });
    const next = await mutate(() => api("/api/v1/studio/knowledge/smart-read", { method: "POST", body }), { success: "Cited read complete", refreshAfter: false });
    if (next) setResult(next);
  };

  return <div className="context-workbench">
    <aside className="source-rail" aria-label="Knowledge sources">
      <header><div><p className="panel-kicker">Immutable library</p><h2>Sources</h2></div><Badge tone="neutral">{sources.length}/200</Badge></header>
      <div className="source-list">
        {sources.map((source) => <button key={source.id} type="button" className={selectedSource?.id === source.id ? "is-active" : ""} onClick={() => setSelectedSourceId(source.id)}><span className="resource-avatar avatar-context"><Icon name="context" size={17} /></span><span><strong>{source.name}</strong><small>{source.version.filename} · {source.version.line_count} lines</small></span><Badge tone="neutral">v{source.current_version}</Badge></button>)}
        {!sources.length ? <EmptyState icon="context" title="No sources yet" description="Import Markdown, text, JSON, YAML, or source code. Server filesystem paths are never accepted." /> : null}
      </div>
      {selectedSource ? <div className="source-provenance"><p><strong>{selectedSource.description || "No description"}</strong></p><dl><div><dt>Version</dt><dd>v{selectedSource.current_version}</dd></div><div><dt>Bytes</dt><dd>{selectedSource.version.byte_count.toLocaleString()}</dd></div><div><dt>Fingerprint</dt><dd><code>{shortId(selectedSource.version.fingerprint, 15)}</code></dd></div><div><dt>Imported</dt><dd>{formatTime(selectedSource.version.created_at)}</dd></div></dl><Button tone="quiet" icon="plus" onClick={() => setSourceDialog({ mode: "revise", source: selectedSource })}>Publish new version</Button></div> : null}
    </aside>

    <main className="smart-read-panel">
      <header className="context-panel-header"><div><p className="panel-kicker">Token-aware inspection primitive</p><h2>SmartRead</h2><p>Choose intent before volume. Every returned passage carries the exact immutable source version, fingerprint, and line range.</p></div><Badge tone="success"><Icon name="lock" size={12} /> citation-first</Badge></header>
      {!selectedSource ? <EmptyState icon="read" title="Import a source to read" description="SmartRead never reaches arbitrary paths. It reads only text already admitted to this isolated workspace." /> : <>
        <form className="smart-read-controls" onSubmit={read}>
          <Field label="Source version"><select value={versionId} onChange={(event) => { setVersionId(event.target.value); setResult(null); }}>{selectedSource.versions.map((version) => <option key={version.id} value={version.id}>v{version.version} · {version.filename} · {version.line_count} lines</option>)}</select></Field>
          <div className="read-mode-grid" role="radiogroup" aria-label="SmartRead mode">
            {READ_MODES.map((item) => <label key={item.id} className={mode === item.id ? "is-checked" : ""}><input type="radio" name="read-mode" value={item.id} checked={mode === item.id} onChange={() => { setMode(item.id); setResult(null); }} /><Icon name={item.id === "grep" ? "search" : "read"} size={16} /><span><strong>{item.label}</strong><small>{item.detail}</small></span></label>)}
          </div>
          {mode === "grep" ? <Field label="Literal query" required hint="Case-insensitive match with a two-line context window."><input required value={query} onChange={(event) => setQuery(event.target.value)} placeholder="human approval" /></Field> : null}
          {mode === "focus" ? <div className="field-grid two"><Field label="First line"><input type="number" min="1" max={selectedVersion?.line_count ?? 1} value={lineStart} onChange={(event) => { const next = Math.max(1, Math.min(selectedVersion?.line_count ?? 1, Number(event.target.value) || 1)); setLineStart(next); setLineEnd((current) => Math.max(next, Math.min(Number(current) || next, next + 159, selectedVersion?.line_count ?? next))); }} /></Field><Field label="Last line" hint="Maximum 160 lines"><input type="number" min={lineStart} max={Math.min(selectedVersion?.line_count ?? 1, Number(lineStart) + 159)} value={lineEnd} onChange={(event) => setLineEnd(event.target.value)} /></Field></div> : null}
          <div className="context-form-actions"><span><Icon name="lock" size={14} />No model call. No summarization. Exact source text.</span><Button tone="primary" icon="read" type="submit" disabled={busy}>Read cited context</Button></div>
        </form>
        {result ? <ReadResult result={result} onUseInBoardRoom={onUseInBoardRoom} /> : <div className="smart-read-empty"><Icon name="read" size={28} /><div><strong>Read with a declared purpose</strong><p>Glance for orientation, outline for structure, grep for literal evidence, focus for an exact window, full only for bounded small sources.</p></div></div>}
      </>}
    </main>
  </div>;
}

function ReadResult({ result, onUseInBoardRoom }) {
  const records = [...(result.headings ?? []), ...(result.passages ?? [])];
  const envelope = records.map((item) => `[${item.citation.label} · ${item.citation.fingerprint.slice(0, 12)}]\n${item.text}`).join("\n\n");
  return <section className="read-result" aria-live="polite">
    <header><div><p className="panel-kicker">Verified result · {result.mode}</p><h3>{result.source.name}</h3><p>{records.length} cited record{records.length === 1 ? "" : "s"} · result <code>{shortId(result.result_fingerprint, 16)}</code></p></div>{records.length ? <Button tone="default" icon="boardroom" onClick={() => onUseInBoardRoom(envelope)}>Take context to BoardRoom</Button> : null}</header>
    <div className="citation-list">{records.map((item, index) => <CitationCard key={`${item.citation.label}-${index}`} item={item} />)}{!records.length ? <EmptyState icon="search" title="No matching passages" description="The result is still fingerprinted. Change the literal query or inspect the outline." /> : null}</div>
  </section>;
}

function CitationCard({ item }) {
  return <article className="citation-card"><header><Badge tone="blue">{item.citation.label}</Badge>{item.match_line ? <span>match L{item.match_line}</span> : null}<code>{shortId(item.citation.fingerprint, 14)}</code></header><pre>{item.text}</pre><footer><span>source v{item.citation.source_version}</span><span>lines {item.citation.line_start}–{item.citation.line_end}</span></footer></article>;
}

function KnowledgeSearch({ mutate, onUseInBoardRoom }) {
  const [query, setQuery] = useState("");
  const [result, setResult] = useState(null);
  const search = async (event) => {
    event.preventDefault();
    const next = await mutate(() => api("/api/v1/studio/knowledge/search", { method: "POST", body: { query, max_results: 20 } }), { success: "Knowledge search complete", refreshAfter: false });
    if (next) setResult(next);
  };
  const envelope = result?.results.map((item) => `[${item.citation.label} · ${item.citation.fingerprint.slice(0, 12)}]\n${item.text}`).join("\n\n") ?? "";
  return <section className="knowledge-search-panel">
    <header className="context-panel-header"><div><p className="panel-kicker">Deterministic workspace retrieval</p><h2>Search current knowledge</h2><p>Rank literal terms across only the current immutable version of every admitted source. No embeddings, hidden index, or model inference.</p></div><Badge tone="neutral">code-ranked</Badge></header>
    <form className="knowledge-search-form" onSubmit={search}><Field label="Search terms" required><input required value={query} onChange={(event) => setQuery(event.target.value)} placeholder="evidence approval runtime" /></Field><Button tone="primary" icon="search" type="submit">Search sources</Button></form>
    {result ? <><div className="search-result-summary"><span><strong>{result.results.length}</strong> passages</span><span>terms: {result.terms.join(", ")}</span><code>{shortId(result.result_fingerprint, 16)}</code>{result.results.length ? <Button tone="default" icon="boardroom" onClick={() => onUseInBoardRoom(envelope)}>Use in BoardRoom</Button> : null}</div><div className="citation-list search-citations">{result.results.map((item) => <CitationCard key={item.passage_id} item={item} />)}</div></> : <div className="smart-read-empty"><Icon name="search" size={28} /><div><strong>Retrieval remains inspectable</strong><p>Each score exposes its matched terms and every result resolves to an exact source fingerprint and line window.</p></div></div>}
  </section>;
}

function SourceModal({ source, mutate, busy, onClose, onSaved }) {
  const revising = Boolean(source);
  const [form, setForm] = useState({
    name: source?.name ?? "Launch evidence",
    slug: source?.slug ?? "launch-evidence",
    description: source?.description ?? "Bounded evidence for an agent workflow.",
    filename: revising ? "successor.md" : "launch-evidence.md",
    media_type: "text/markdown",
    content: "# Evidence brief\n\n## Goal\nDescribe the decision and the proof required.\n\n## Constraints\nEvery material claim must retain an exact citation.",
    created_by: "workspace-operator"
  });
  const patch = (key, value) => setForm((current) => ({ ...current, [key]: value }));
  const loadFile = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    const content = await file.text();
    const media = file.type === "application/json" ? "application/json" : /\.ya?ml$/i.test(file.name) ? "application/yaml" : /\.(js|jsx|ts|tsx|py|go|rs|java|css|html)$/i.test(file.name) ? "text/x-source-code" : /\.md$/i.test(file.name) ? "text/markdown" : "text/plain";
    setForm((current) => ({ ...current, name: revising ? current.name : file.name.replace(/\.[^.]+$/, ""), slug: revising ? current.slug : slugify(file.name.replace(/\.[^.]+$/, "")), filename: file.name, media_type: media, content }));
  };
  const submit = async (event) => {
    event.preventDefault();
    const body = revising
      ? { expected_version: source.current_version, name: form.name, description: form.description, filename: form.filename, media_type: form.media_type, content: form.content, created_by: form.created_by }
      : form;
    const result = await mutate(() => api(revising ? `/api/v1/studio/knowledge-sources/${source.id}/versions` : "/api/v1/studio/knowledge-sources", { method: "POST", body }), { success: revising ? `Knowledge v${source.current_version + 1} published` : "Knowledge source imported" });
    if (result) onSaved(result);
  };
  return <Modal title={revising ? `Publish ${source.name} v${source.current_version + 1}` : "Import knowledge source"} description="Content is copied into an immutable workspace version. Paths and remote fetches are deliberately unsupported." onClose={onClose} width="780px"><form className="modal-form source-form" onSubmit={submit}><label className="file-drop"><Icon name="context" size={22} /><span><strong>Choose a UTF-8 text file</strong><small>Markdown · text · JSON · YAML · source code · max 256 KiB</small></span><input type="file" accept=".md,.txt,.json,.yaml,.yml,.js,.jsx,.ts,.tsx,.py,.go,.rs,.java,.css,.html,text/*,application/json,application/yaml" onChange={loadFile} /></label><div className="field-grid two"><Field label="Name" required><input required value={form.name} onChange={(event) => { patch("name", event.target.value); if (!revising) patch("slug", slugify(event.target.value)); }} /></Field><Field label="Slug" hint={revising ? "Immutable source identity" : "Stable lowercase identifier"}><input required disabled={revising} value={form.slug} onChange={(event) => patch("slug", slugDraft(event.target.value))} onBlur={(event) => patch("slug", slugify(event.target.value))} /></Field></div><Field label="Purpose"><textarea rows="2" value={form.description} onChange={(event) => patch("description", event.target.value)} /></Field><div className="field-grid two"><Field label="Display filename" required><input required value={form.filename} onChange={(event) => patch("filename", event.target.value)} /></Field><Field label="Media type"><select value={form.media_type} onChange={(event) => patch("media_type", event.target.value)}><option value="text/markdown">Markdown</option><option value="text/plain">Plain text</option><option value="application/json">JSON</option><option value="application/yaml">YAML</option><option value="text/x-source-code">Source code</option></select></Field></div><Field label="Imported by"><input value={form.created_by} onChange={(event) => patch("created_by", event.target.value)} /></Field><Field label="Source content" required hint={`${new Blob([form.content]).size.toLocaleString()} bytes · immutable after publication`}><textarea className="source-content-input" required rows="15" value={form.content} onChange={(event) => patch("content", event.target.value)} spellCheck="false" /></Field><div className="modal-actions"><Button tone="quiet" onClick={onClose}>Cancel</Button><Button tone="primary" icon="save" type="submit" disabled={busy}>{revising ? "Publish successor" : "Import immutable source"}</Button></div></form></Modal>;
}

function MemoryWorkspace({ snapshot, candidates, memories, mutate, busy, focusRun }) {
  const eligibleRuns = snapshot.studio.runs.filter((run) => run.status === "completed" && run.ledger_verified === true);
  const [runId, setRunId] = useState(eligibleRuns[0]?.id ?? "");
  const run = eligibleRuns.find((item) => item.id === runId) ?? eligibleRuns[0] ?? null;
  const [eventIds, setEventIds] = useState([]);
  const [authorKind, setAuthorKind] = useState("human");
  const [human, setHuman] = useState({ title: "", content: "", rationale: "", tags: "evidence, governance" });
  const [distillerId, setDistillerId] = useState(snapshot.agents[0]?.version.id ?? "");
  const [selectedCandidateId, setSelectedCandidateId] = useState(candidates.find((item) => !item.decision)?.id ?? candidates[0]?.id ?? null);
  const [decision, setDecision] = useState({ slug: "", actor: "workspace-operator", reason: "I reviewed the exact cited Run evidence and accept this bounded reusable memory.", acknowledged: false });
  const [recallQuery, setRecallQuery] = useState("");
  const [recallResult, setRecallResult] = useState(null);
  const [retireTarget, setRetireTarget] = useState(null);
  const [retireReason, setRetireReason] = useState("A newer evidence-backed memory supersedes this version for future recall.");

  useEffect(() => {
    if (!run) { setEventIds([]); return; }
    const preferred = run.events.filter((event) => ["step.completed", "run.completed", "effect.committed"].includes(event.type)).map((event) => event.id);
    setEventIds((preferred.length ? preferred : run.events.slice(-1).map((event) => event.id)).slice(0, 20));
  }, [run?.id]);
  useEffect(() => {
    if (candidates.some((candidate) => candidate.id === selectedCandidateId)) return;
    setSelectedCandidateId(candidates.find((item) => !item.decision)?.id ?? candidates[0]?.id ?? null);
  }, [candidates, selectedCandidateId]);
  const selectedCandidate = candidates.find((candidate) => candidate.id === selectedCandidateId) ?? null;

  const createCandidate = async (event) => {
    event.preventDefault();
    if (!run) return;
    const body = authorKind === "human"
      ? { author_kind: "human", source_run_id: run.id, title: human.title, content: human.content, rationale: human.rationale, tags: human.tags.split(",").map((tag) => tag.trim()).filter(Boolean), evidence_event_ids: eventIds }
      : { author_kind: "model", source_run_id: run.id, distiller_agent_version_id: distillerId, evidence_event_ids: eventIds };
    const result = await mutate(() => api("/api/v1/studio/memory-candidates", { method: "POST", keyMode: authorKind === "model" ? "required" : "none", body }), { success: authorKind === "model" ? "Model proposal quarantined" : "Human proposal quarantined" });
    if (result) {
      setSelectedCandidateId(result.id);
      setDecision((current) => ({ ...current, slug: slugify(result.title), acknowledged: false }));
    }
  };
  const qualify = () => mutate(() => api(`/api/v1/studio/memory-candidates/${selectedCandidate.id}/qualifications`, { method: "POST", body: {} }), { success: "Candidate provenance qualified" });
  const decideCandidate = async (kind) => {
    if (!selectedCandidate) return;
    const path = kind === "promote" ? "promotion" : "rejection";
    const body = kind === "promote"
      ? { slug: decision.slug || slugify(selectedCandidate.title), actor: decision.actor, reason: decision.reason, acknowledged: decision.acknowledged, candidate_fingerprint: selectedCandidate.fingerprint }
      : { actor: decision.actor, reason: decision.reason, acknowledged: decision.acknowledged, candidate_fingerprint: selectedCandidate.fingerprint };
    const result = await mutate(() => api(`/api/v1/studio/memory-candidates/${selectedCandidate.id}/${path}`, { method: "POST", body }), { success: kind === "promote" ? "Memory promoted for recall" : "Candidate rejected and preserved" });
    if (result) setRecallResult(null);
  };
  const recall = async (event) => {
    event.preventDefault();
    const result = await mutate(() => api("/api/v1/studio/memories/search", { method: "POST", body: { query: recallQuery, max_results: 12 } }), { success: "Memory recall complete", refreshAfter: false });
    if (result) setRecallResult(result);
  };
  const retire = async () => {
    if (!retireTarget) return;
    const result = await mutate(() => api(`/api/v1/studio/memories/${retireTarget}/retirement`, { method: "POST", body: { actor: decision.actor, reason: retireReason } }), { success: "Memory retired from future recall" });
    if (result) {
      setRecallResult(null);
      setRetireTarget(null);
    }
  };

  return <div className="memory-workspace">
    <section className="memory-proposal-panel">
      <header className="context-panel-header"><div><p className="panel-kicker">Quarantine first</p><h2>Propose from verified Run evidence</h2><p>Neither a human nor a model can write directly into recall. A candidate is append-only, authority-free, and invisible to retrieval until qualification and explicit promotion.</p></div><Badge tone="warning">candidate ≠ memory</Badge></header>
      {!eligibleRuns.length ? <EmptyState icon="run" title="No eligible source Run" description="Complete a Flow with a verified event ledger, then return here to cite its exact events." action={snapshot.studio.runs.length ? <Button tone="default" icon="run" onClick={() => focusRun(snapshot.studio.runs[0].id)}>Open Runs</Button> : null} /> : <form className="memory-candidate-form" onSubmit={createCandidate}>
        <div className="field-grid two"><Field label="Completed source Run"><select value={run?.id ?? ""} onChange={(event) => setRunId(event.target.value)}>{eligibleRuns.map((item) => <option key={item.id} value={item.id}>{shortId(item.id, 16)} · {titleCase(item.outcome ?? item.status)}</option>)}</select></Field><Field label="Candidate author"><select value={authorKind} onChange={(event) => setAuthorKind(event.target.value)}><option value="human">Human proposal</option><option value="model">Pinned Agent distillation</option></select></Field></div>
        <fieldset className="memory-event-picker"><legend>Cited Run events <b>*</b></legend><p>Select the exact ledger records that support this candidate.</p><div>{run.events.map((event) => <label key={event.id}><input type="checkbox" checked={eventIds.includes(event.id)} onChange={(change) => setEventIds((current) => change.target.checked ? [...current, event.id].slice(0, 20) : current.filter((id) => id !== event.id))} /><span><strong>{event.type}</strong><small>#{event.sequence} · {shortId(event.id, 14)}</small></span></label>)}</div></fieldset>
        {authorKind === "human" ? <><Field label="Memory title" required><input required maxLength="140" value={human.title} onChange={(event) => setHuman((current) => ({ ...current, title: event.target.value }))} placeholder="A reusable, falsifiable observation" /></Field><Field label="Content" required><textarea required rows="4" value={human.content} onChange={(event) => setHuman((current) => ({ ...current, content: event.target.value }))} placeholder="What should future work recall?" /></Field><Field label="Why these events prove it" required><textarea required rows="3" value={human.rationale} onChange={(event) => setHuman((current) => ({ ...current, rationale: event.target.value }))} /></Field><Field label="Tags"><input value={human.tags} onChange={(event) => setHuman((current) => ({ ...current, tags: event.target.value }))} /></Field></> : <Field label="Pinned distiller Agent" required hint="One tool-free strict Responses call. The output still enters quarantine."><select value={distillerId} onChange={(event) => setDistillerId(event.target.value)}>{snapshot.agents.flatMap((agent) => agent.versions.map((version) => <option key={version.id} value={version.id}>{agent.name} · v{version.version} · {version.model}</option>))}</select></Field>}
        <div className="context-form-actions"><span><Icon name="lock" size={14} />Source Run completed · ledger verified · max 20 citations</span><Button tone="primary" icon="memory" type="submit" disabled={busy || !eventIds.length || (authorKind === "model" && !distillerId)}>Create quarantined candidate</Button></div>
      </form>}
    </section>

    <div className="memory-governance-grid">
      <aside className="candidate-list"><header><div><p className="panel-kicker">Append-only review queue</p><h2>Candidates</h2></div><Badge tone="neutral">{candidates.length}</Badge></header>{candidates.map((candidate) => <button type="button" key={candidate.id} className={selectedCandidate?.id === candidate.id ? "is-active" : ""} onClick={() => { setSelectedCandidateId(candidate.id); setDecision((current) => ({ ...current, slug: slugify(candidate.title), acknowledged: false })); }}><span><strong>{candidate.title}</strong><small>{candidate.author_kind} · {shortId(candidate.source_run_id, 13)}</small></span><Badge tone={candidate.decision ? candidate.decision.decision === "promoted" ? "success" : "danger" : candidate.qualification?.passed ? "blue" : "warning"}>{candidate.decision?.decision ?? (candidate.qualification ? candidate.qualification.passed ? "qualified" : "failed" : "quarantined")}</Badge></button>)}{!candidates.length ? <EmptyState icon="memory" title="No candidates" description="Proposals appear here before they can influence recall." /> : null}</aside>
      <main className="candidate-review">
        {!selectedCandidate ? <EmptyState icon="memory" title="Select a candidate" description="Inspect provenance, run deterministic qualification, then explicitly promote or reject the exact fingerprint." /> : <>
          <header><div><p className="panel-kicker">Candidate review</p><h2>{selectedCandidate.title}</h2><p>{selectedCandidate.rationale}</p></div><Badge tone={selectedCandidate.decision ? "neutral" : "warning"}>{selectedCandidate.decision?.decision ?? "no decision"}</Badge></header>
          <div className="candidate-content"><p>{selectedCandidate.content}</p><div>{selectedCandidate.tags.map((tag) => <Badge key={tag} tone="neutral">{tag}</Badge>)}</div></div>
          <dl className="candidate-provenance"><div><dt>Author</dt><dd>{selectedCandidate.author_kind}</dd></div><div><dt>Source Run</dt><dd><button type="button" onClick={() => focusRun(selectedCandidate.source_run_id)}><code>{shortId(selectedCandidate.source_run_id, 16)}</code></button></dd></div><div><dt>Evidence</dt><dd>{selectedCandidate.evidence_event_ids.length} events</dd></div><div><dt>Fingerprint</dt><dd><code>{shortId(selectedCandidate.fingerprint, 18)}</code></dd></div></dl>
          {selectedCandidate.qualification ? <div className={`qualification-report ${selectedCandidate.qualification.passed ? "is-passed" : "is-failed"}`}><header><Icon name={selectedCandidate.qualification.passed ? "check" : "warning"} size={18} /><strong>{selectedCandidate.qualification.passed ? "Qualification passed" : "Qualification failed"}</strong></header>{Object.entries(selectedCandidate.qualification.checks).map(([check, passed]) => <span key={check}><Icon name={passed ? "check" : "close"} size={13} />{check.replaceAll("_", " ")}</span>)}</div> : <Button tone="default" icon="check" onClick={qualify} disabled={busy || Boolean(selectedCandidate.decision)}>Run deterministic qualification</Button>}
          {selectedCandidate.qualification?.passed && !selectedCandidate.decision ? <section className="memory-decision"><h3>Human decision over exact candidate</h3><div className="field-grid two"><Field label="Memory slug"><input value={decision.slug} onChange={(event) => setDecision((current) => ({ ...current, slug: slugDraft(event.target.value) }))} onBlur={(event) => setDecision((current) => ({ ...current, slug: slugify(event.target.value) }))} /></Field><Field label="Actor"><input value={decision.actor} onChange={(event) => setDecision((current) => ({ ...current, actor: event.target.value }))} /></Field></div><Field label="Reason" hint="At least 20 characters; stored append-only."><textarea rows="3" value={decision.reason} onChange={(event) => setDecision((current) => ({ ...current, reason: event.target.value }))} /></Field><label className="check-row"><input type="checkbox" checked={decision.acknowledged} onChange={(event) => setDecision((current) => ({ ...current, acknowledged: event.target.checked }))} /><span><strong>I reviewed this exact fingerprint</strong><small>Promotion changes recall state, not Agent authority or existing Flows.</small></span></label><div className="memory-decision-actions"><Button tone="danger" icon="close" disabled={!decision.acknowledged || decision.reason.length < 20} onClick={() => decideCandidate("reject")}>Reject and preserve</Button><Button tone="primary" icon="memory" disabled={!decision.acknowledged || decision.reason.length < 20 || !decision.slug} onClick={() => decideCandidate("promote")}>Promote to Memory</Button></div></section> : null}
          {selectedCandidate.decision ? <div className="decision-record"><Icon name={selectedCandidate.decision.decision === "promoted" ? "check" : "close"} size={18} /><div><strong>{titleCase(selectedCandidate.decision.decision)} by {selectedCandidate.decision.actor}</strong><p>{selectedCandidate.decision.reason}</p><small>{formatTime(selectedCandidate.decision.created_at)}</small></div></div> : null}
        </>}
      </main>
    </div>

    <section className="memory-recall-panel">
      <header className="context-panel-header"><div><p className="panel-kicker">Active memory only</p><h2>Recall with provenance</h2><p>Search excludes quarantined, rejected, and retired records. Every hit returns its source candidate, source Run, evidence event IDs, and immutable fingerprint.</p></div><Badge tone="success">{memories.filter((item) => item.state === "active").length} active</Badge></header>
      <form className="knowledge-search-form" onSubmit={recall}><Field label="Recall terms"><input required value={recallQuery} onChange={(event) => setRecallQuery(event.target.value)} placeholder="approval evidence boundary" /></Field><Button tone="primary" icon="search" type="submit">Recall Memory</Button></form>
      {recallResult ? <div className="memory-result-list">{recallResult.results.map((item) => <article key={item.memory_version_id}><header><div><Badge tone="success">active memory</Badge><strong>{item.title}</strong></div><span>score {item.score}</span></header><p>{item.content}</p><footer><span>{item.tags.join(" · ")}</span><button type="button" onClick={() => focusRun(item.provenance.source_run_id)}><Icon name="run" size={13} />{shortId(item.provenance.source_run_id, 14)}</button><code>{shortId(item.fingerprint, 14)}</code></footer></article>)}{!recallResult.results.length ? <EmptyState icon="search" title="Nothing recalled" description="No active memory matched these terms." /> : null}</div> : null}
      <div className="memory-inventory"><h3>Memory inventory</h3>{memories.map((memory) => <article key={memory.id} className={memory.state === "retired" ? "is-retired" : ""}><span className="resource-avatar avatar-memory"><Icon name="memory" size={17} /></span><div><strong>{memory.name}</strong><small>{memory.version.tags.join(" · ")} · v{memory.current_version}</small></div><Badge tone={memory.state === "active" ? "success" : "neutral"}>{memory.state}</Badge>{memory.state === "active" ? <Button tone="quiet" onClick={() => setRetireTarget(memory.id)}>Retire</Button> : null}</article>)}</div>
    </section>
    {retireTarget ? <Modal title="Retire Memory from recall" description="The immutable Memory and state history remain inspectable; future recall excludes it." onClose={() => setRetireTarget(null)}><div className="modal-form"><Field label="Reason" hint="At least 20 characters"><textarea rows="4" value={retireReason} onChange={(event) => setRetireReason(event.target.value)} /></Field><div className="modal-actions"><Button tone="quiet" onClick={() => setRetireTarget(null)}>Cancel</Button><Button tone="danger" icon="memory" disabled={retireReason.length < 20} onClick={retire}>Retire Memory</Button></div></div></Modal> : null}
  </div>;
}
