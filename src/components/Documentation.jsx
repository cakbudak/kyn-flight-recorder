import React from "react";
import { Icon } from "../icons.jsx";
import { Badge, Button, PageHeader } from "./ui.jsx";

const SECTIONS = [
  ["mental-model", "Mental model"],
  ["context-layer", "Context & Memory"],
  ["build-flow", "Build a Flow"],
  ["named-outputs", "Named outputs"],
  ["agent-stack", "Agents and AI"],
  ["boardrooms", "Parallel BoardRooms"],
  ["subflows", "Reusable Flows"],
  ["runs", "Run operations"],
  ["completion", "Completion truth"],
  ["ratification", "Learning from failure"],
  ["comparisons", "Switch the brain"],
  ["maintenance", "Maintenance loop"],
  ["forge", "Capability Forge"],
  ["credentials", "Credentials"],
  ["contracts", "Runtime limits"],
  ["boundary", "Public boundary"]
];

export default function Documentation({ setView }) {
  return (
    <section className="docs-page">
      <PageHeader eyebrow="Product documentation · live with the runtime" title="Build and operate Kyn Agent Studio" description="This page explains the executable contract behind the interface. Nothing below depends on private Kyn layers." actions={<Button tone="primary" icon="flow" onClick={() => setView("studio")}>Open Flow Studio</Button>} />
      <div className="docs-layout">
        <nav className="docs-nav" aria-label="Documentation sections">
          <p className="nav-section">On this page</p>
          {SECTIONS.map(([id, label], index) => <a key={id} href={`#${id}`}><span>{String(index + 1).padStart(2, "0")}</span>{label}</a>)}
          <div className="docs-nav-note"><Icon name="lock" size={17} /><p><strong>Runtime truth</strong>Source is public. Contracts are enforced by Python and SQLite, not implied by the UI.</p></div>
        </nav>
        <main className="docs-content">
          <DocSection id="mental-model" number="01" kicker="Mental model" title="Definitions describe capability. Runs own execution truth.">
            <p>An <strong>Action</strong> is a typed contract over one bounded, code-owned executor. A <strong>Prompt</strong> declares a template and its variables. A <strong>Skill</strong> combines instructions with exact tool and Action grants. An <strong>Agent</strong> pins a model, Prompt version, and Skill versions. A <strong>Flow</strong> connects Action, Agent, and published Flow versions into an acyclic graph.</p>
            <div className="doc-definition-grid">
              <Definition icon="action" title="Action" text="Input schema · output schema · named outcomes · executor config · effect level" />
              <Definition icon="prompt" title="Prompt" text="Template · exact variables · immutable fingerprint" />
              <Definition icon="skill" title="Skill" text="Instructions · static tools · exact callable Action versions" />
              <Definition icon="agent" title="Agent" text="Role · model · instructions · Prompt pin · Skill pins" />
              <Definition icon="flow" title="Flow" text="Typed boundary · nodes · mappings · routes · public outcomes" />
              <Definition icon="run" title="Run" text="Pinned graph · Steps · events · calls · receipts · effects · lineage" />
            </div>
            <Callout tone="success" title="The key distinction">Definitions describe what may happen. The Run records what did happen. A trace can help debugging; it cannot replace authoritative state, authority checks, approvals, or receipts.</Callout>
          </DocSection>

          <DocSection id="context-layer" number="02" kicker="Context before inference" title="Read less, cite exactly, and govern what becomes Memory">
            <p>A Knowledge Source copies bounded UTF-8 content into an immutable SQLite version; it never grants the server a filesystem path or a remote fetch URL. SmartRead then selects the smallest useful view of that exact version. Every passage includes the source/version IDs, filename, content fingerprint, and line range, so an Agent receives evidence it can cite rather than an anonymous blob.</p>
            <div className="doc-contract-row"><article><strong>Glance</strong><p>Opening window plus headings for fast orientation.</p></article><article><strong>Outline</strong><p>Structural headings and declarations before detail.</p></article><article><strong>Focus / Grep</strong><p>An exact line window or literal matches with local context.</p></article><article><strong>Full</strong><p>Whole content only when the immutable source is below the explicit 96 KiB bound.</p></article></div>
            <div className="doc-sequence"><span>Source vN</span><i>→</i><span>SmartRead</span><i>→</i><span>line citations</span><i>→</i><span>Agent / Flow input</span><i>→</i><span>Run evidence</span></div>
            <p>Knowledge search is deterministic term retrieval across only current source versions. SmartRead, Knowledge search, and Memory recall are also normal Action kinds: a Skill may grant their exact versions to an Agent, and every invocation then travels through the same validation, receipt, and evidence path as other Actions.</p>
            <div className="doc-sequence"><span>SmartRead Action</span><i>→</i><span>active Memory recall</span><i>→</i><span>cited handoff</span><i>→</i><span>BoardRoom subflow</span></div>
            <Callout tone="success" title="Compose the knowledge path as a Flow">From any cited SmartRead result, Compose cited Flow creates an unsaved, fully editable graph. It pins the exact source version and read window, recalls only active Human-promoted Memory, combines both bounded citation envelopes through deterministic code, and maps that context into a published BoardRoom. The first Run may return no Memory; after promotion, the same pinned Flow automatically supplies matching Memory to the next Run.</Callout>
            <Callout tone="ai" title="Memory is not an unreviewed transcript">Only a completed Run with a verified event ledger may source a candidate. Human and model-authored candidates enter quarantine, cite owned event IDs, and remain absent from recall. Code rechecks completion, ledger validity, citation ownership, source snapshot, fingerprint, and zero authority. A human must acknowledge the exact fingerprint to promote it; retirement is a later append-only state event.</Callout>
            <Code title="Citation carried into work">{`{
  "source_version_id": "ksv_…",
  "fingerprint": "sha256…",
  "line_start": 41,
  "line_end": 58,
  "label": "launch-brief.md:L41-L58"
}`}</Code>
          </DocSection>

          <DocSection id="build-flow" number="03" kicker="Visual workbench" title="Build a Flow on the full canvas">
            <p>Select a published Flow from the toolbar or create a blank draft. Add nodes from the left library by click or drag. The center canvas supports pan, zoom, minimap, multi-selection, deletion, snap-to-grid, undo/redo, and deterministic auto-layout. Selecting a node opens its complete contract and routing controls on the right.</p>
            <ol className="doc-steps">
              <li><span>1</span><div><strong>Declare the Flow boundary</strong><p>Set its input and output JSON Schemas and public outcomes. Those become the contract when another Flow reuses it.</p></div></li>
              <li><span>2</span><div><strong>Add immutable capability versions</strong><p>Actions, Agents, and Subflows are pinned at publication—not resolved dynamically when the Run starts.</p></div></li>
              <li><span>3</span><div><strong>Map each input explicitly</strong><p>Read from Flow input, a reachable predecessor Step output, or a literal. There is no ambient mutable context.</p></div></li>
              <li><span>4</span><div><strong>Wire named outcomes</strong><p>Drag from the visible port or choose a destination in the Outcome routes inspector. “End Flow” makes it terminal.</p></div></li>
              <li><span>5</span><div><strong>Publish a successor</strong><p>Saving an existing Flow appends vN+1. Active and historical Runs keep the exact earlier graph they pinned.</p></div></li>
            </ol>
            <Code title="Node contract">{`{
  "id": "quality-gate",
  "type": "action",
  "version_id": "actv_…",
  "input_mapping": {
    "score": { "source": "step", "node_id": "analyze", "path": "score" }
  },
  "settings": {
    "max_attempts": 2,
    "backoff_seconds": 0.5,
    "retry_on": ["provider_failure"],
    "on_error": "fail"
  }
}`}</Code>
          </DocSection>

          <DocSection id="named-outputs" number="04" kicker="Routing" title="A node can own up to twelve outputs">
            <p>Success/failure is only the default. Router and AI Actions can declare domain outputs such as <code>enterprise</code>, <code>needs-review</code>, <code>duplicate</code>, and <code>fallback</code>. Every output has an ID, human label, description, and visual tone. The canvas distributes ports vertically so wires do not collapse into one source point.</p>
            <div className="port-doc"><span className="tone-ai"><i />Enterprise<small>enterprise</small></span><span className="tone-success"><i />Self serve<small>self-serve</small></span><span className="tone-warning"><i />Needs review<small>needs-review</small></span><span className="tone-danger"><i />Error<small>error</small></span></div>
            <Callout tone="warning" title="No hidden fallback route">A wire is valid only if the source version declares that exact outcome. The runtime never silently substitutes a generic success path. Error continuation is explicit per node and requires an error route.</Callout>
          </DocSection>

          <DocSection id="agent-stack" number="05" kicker="OpenAI integration" title="AI is visible, pinned, and authority-bounded">
            <p>An AI Action chooses an immutable Agent version. The Agent chooses an OpenAI model and pins one Prompt plus zero or more Skills. Skills grant a bounded union of static tools and exact callable Action versions. At invocation, the runtime intersects a model request with that union, validates every argument, and records a model-call summary plus Action receipts.</p>
            <div className="doc-sequence"><span>AI Action</span><i>→</i><span>Agent v4</span><i>→</i><span>Prompt v2</span><i>+</i><span>Skills v3/v7</span><i>→</i><span>Responses API</span></div>
            <div className="doc-two-column"><div><h3>OpenAI owns</h3><ul><li>Model inference</li><li>Responses transport</li><li>Reasoning and function-call proposals</li><li>Strict structured output generation</li></ul></div><div><h3>Kyn owns</h3><ul><li>Graph orchestration and durable state</li><li>Version and authority pins</li><li>Action dispatch and validation</li><li>Approval, receipts, evidence, repair and replay truth</li></ul></div></div>
            <Code title="AI executor policy">{`{
  "max_tool_calls": 2,
  "max_output_tokens": 4000,
  "reasoning_effort": "medium",
  "outcome_path": "decision"
}`}</Code>
            <p><code>max_output_tokens</code> is the Action’s explicit response ceiling (256–8,000; 4,000 by default). Larger synthesis nodes can budget more than narrow worker nodes without removing the hard cost boundary. If OpenAI reaches that ceiling, the Run records an actionable incomplete-response failure instead of a generic transport error.</p>
            <p>If <code>outcome_path</code> is configured, the corresponding strict output field must be a string enum that exactly matches every non-error Action outcome. That is how AI decisions become typed graph ports—not parsed prose.</p>
          </DocSection>

          <DocSection id="boardrooms" number="06" kicker="Independent multi-agent work" title="Fan out in parallel. Join in code. Preserve dissent.">
            <p>A fan-out is a generic Flow node, not a special chat widget. It pins two to eight distinct Action, Agent, or reusable Flow versions that accept one identical mapped input contract. The runtime dispatches them concurrently through separate operation sessions, records one parent Step plus one child Step per member, then computes the barrier result without asking a model.</p>
            <div className="doc-two-column"><div><h3>Safe inside the fan-out</h3><ul><li>Read-only and model work</li><li>Exact immutable target pins</li><li>Independent provider calls</li><li>Isolated or fail-fast member errors</li><li>All-members or numeric quorum joins</li></ul></div><div><h3>Placed after the barrier</h3><ul><li>Human approval and rejection</li><li>Any Action capable of minting an effect</li><li>Dissent-preserving synthesis</li><li>Bounded store writes</li><li>Public terminal-result normalization</li></ul></div></div>
            <p>The barrier reads a declared verdict path from every schema-valid member output, matches exact affirmative values, and exposes completed, failed, affirmative, converged, and dissenting-member records. It emits <code>converged</code>, <code>review</code>, or <code>error</code> as separately wireable ports. No participant can see a peer before joining or convert quorum into unanimity.</p>
            <Code title="Generic fan-out node">{`{
  "id": "council",
  "type": "fan_out",
  "version_id": "fanout-v1",
  "members": [
    { "id": "product", "type": "action", "version_id": "actv_…" },
    { "id": "risk", "type": "action", "version_id": "actv_…" },
    { "id": "operations", "type": "action", "version_id": "actv_…" }
  ],
  "barrier": {
    "mode": "quorum",
    "quorum": 2,
    "verdict_path": "verdict",
    "affirmative_values": ["commit"],
    "on_member_error": "isolate"
  }
}`}</Code>
            <Callout tone="success" title="BoardRoom is a factory over the generic motor">The guided surface publishes each participant’s Prompt, Skill, Agent and strict AI Action; one editor stack; optional approval and bounded write Actions; and one ordinary Flow. The room then appears in Flow Studio, where every node, mapping, route, model, Prompt, Skill, and successor remains editable.</Callout>
          </DocSection>

          <DocSection id="subflows" number="07" kicker="Composition" title="A published Flow is a first-class node">
            <p>After publication, a Flow version appears in the node library. Its input, output, and outcome contracts become the node boundary. Execution creates a linked child Run rather than flattening evidence into the parent. If the child pauses for approval, the parent pauses on its Flow Step; when the child becomes terminal, the parent resumes or fails from that exact result.</p>
            <p>The operations console groups those records as one top-level orchestration with nested Subflow executions. One click therefore remains one operator-visible job, while each reusable Flow keeps its independently replayable version, Steps, events, model calls, and approval evidence.</p>
            <div className="doc-contract-row"><article><strong>Pin</strong><p>The parent stores the child Flow version ID and fingerprint.</p></article><article><strong>Execute</strong><p>Child gets the same correlation ID plus explicit parent Run and Step IDs.</p></article><article><strong>Observe</strong><p>Parent and child retain separate Steps, events, calls, receipts, effects, and outcomes.</p></article><article><strong>Bound</strong><p>Cycles are rejected; depth is four; expanded work is capped at 200 nodes.</p></article></div>
          </DocSection>

          <DocSection id="runs" number="08" kicker="Operations" title="Observe and control work as Runs">
            <p>A Run is persisted and fully pinned before a worker or provider call begins. The operations console overlays current state on the exact pinned graph and exposes separate views for Steps, the hash-linked event timeline, OpenAI call summaries, Action receipts, bounded effects, approvals, and lineage.</p>
            <table className="doc-table"><thead><tr><th>Evidence</th><th>What it proves</th><th>What it deliberately omits</th></tr></thead><tbody><tr><td>Step</td><td>Node, version, attempt, validated input/output, outcome and error</td><td>No secret credentials</td></tr><tr><td>Event</td><td>Ordered state change with actor, payload fingerprint and hash link</td><td>No hidden chain-of-thought</td></tr><tr><td>Model call</td><td>Provider response ID, model, request ID, status, token usage and safe hashes</td><td>No API key or raw reasoning</td></tr><tr><td>Action receipt</td><td>Exact version, attempt, idempotency key, result and failure code</td><td>No unvalidated executor output</td></tr><tr><td>Effect</td><td>Committed collection and payload in the isolated workspace store</td><td>No production integration claim</td></tr></tbody></table>
            <Callout tone="ai" title="Human gates are state, not UI">An approval Action moves the Step and Run into <code>waiting_approval</code>. Its rendered decision material has a visible <code>max_message_chars</code> policy (64,000 by default; configurable down to 512), independent of OpenAI output-token budgets. The decision stores actor and reason. Approval resumes through the explicit approved route; rejection resumes through an explicit rejected route when one exists, otherwise it fails closed. Refreshing or closing the page does not bypass the gate.</Callout>
          </DocSection>

          <DocSection id="completion" number="09" kicker="Goal / stop seam" title="“Finished” is a claim. Evidence decides whether it becomes true.">
            <p>In the Flow inspector, define up to eight observable acceptance promises. Each promise selects one admitted evidence kind—completed Step, successful Action receipt, Human approval, or committed effect—and one or more graph sites capable of minting it. The Flow version also pins an independent Goal-Judge Agent, including its exact model, Prompt, and Skills.</p>
            <div className="doc-contract-row"><article><strong>Declare</strong><p>Name the work that must actually have happened, not a sentiment or score.</p></article><article><strong>Nominate</strong><p>The Judge reads bounded redacted Run material and nominates evidence IDs per promise.</p></article><article><strong>Resolve</strong><p>Code checks same Run, declared kind, declared site, and admitted state for every ID.</p></article><article><strong>Stop</strong><p>One promise without a surviving anchor records <code>completion_unevidenced</code>; the Run never becomes completed.</p></article></div>
            <Callout tone="ai" title="The Judge is visible and non-authoritative">Its assessment and per-promise reasons remain in the hash-linked ledger as a model claim. They explain the verdict; they cannot mint evidence, borrow another Run's record, turn a failed receipt into success, or cast the Agent that performed the judged work.</Callout>
            <Code title="Pinned acceptance promise">{`{
  "id": "record-in-ledger",
  "statement": "The submitted record was written.",
  "evidence_kind": "effect",
  "node_ids": ["publish-to-ledger"]
}`}</Code>
          </DocSection>

          <DocSection id="ratification" number="10" kicker="Evidence flywheel" title="The runtime remembers what did not work">
            <p>A structural failure mints append-only dead-end evidence for the exact pinned Flow version, node, fault class, and normalized detail. State is derived by counting distinct citing Runs—never by incrementing a mutable counter and never by asking a model.</p>
            <div className="doc-sequence"><span>1 Run · proposed</span><i>→</i><span>2 Runs · confirmed</span><i>→</i><span>3 Runs · canonical</span><i>→</i><span>next Run refused pre-creation</span></div>
            <p>The refusal cites the Runs that proved the dead end. It creates no Run row, Step, event, or effect. Publishing a repaired successor changes the immutable Flow fingerprint and clears the brake: only the unchanged path is refused.</p>
            <Callout tone="warning" title="Warn early, refuse late">Three different Flows failing the same declared predicate distil a workspace principle. A principle advises during authoring but never blocks publication or execution. Only the exact canonical dead end has veto power.</Callout>
          </DocSection>

          <DocSection id="comparisons" number="11" kicker="Model-agnostic runtime" title="Switch the brain without moving the scaffold">
            <p>A controlled sweep prepares every sibling Run and hash-ledgers the complete expected model × repetition × Run-ID manifest <strong>before provider I/O</strong>. Every sibling receives the same input fingerprint and pins one byte-identical Flow version, including every transitive Action, Agent, Prompt, Skill, route, schema, and Goal-Judge.</p>
            <div className="doc-two-column"><div><h3>Verified controls</h3><ul><li>Pre-I/O sibling manifest and Run IDs</li><li>Flow version and fingerprint</li><li>Recomputed input fingerprint</li><li>Requested model equals provider-returned model</li><li>Every sibling event ledger verifies</li></ul></div><div><h3>Never overclaimed</h3><ul><li>Sampling controls this surface cannot set</li><li>A ranking or universal model-quality claim</li><li>A baseline from a deliberately model-varying run</li><li>Token or latency differences below measured noise</li><li>Invariance when a model disagrees with itself</li></ul></div></div>
            <Callout tone="success" title="A refusal is a result">If the provider silently aliases a requested model, a sibling is missing, or any manifest/ledger check fails, the comparison is marked unusable above every number. A smaller experiment can never masquerade as the complete one requested.</Callout>
          </DocSection>

          <DocSection id="maintenance" number="12" kicker="Forward recovery" title="Diagnose, approve a successor, then prove it">
            <p>The maintenance loop is an included platform capability, not the only demo. A blocked or failed Run remains terminal. Recovery proceeds through explicit new artifacts and linked work:</p>
            <div className="maintenance-doc-flow"><article><span>01</span><strong>Diagnose</strong><p>Code owns the causal candidate. A diagnostician may explain it only from cited event IDs belonging to the Run.</p></article><i>→</i><article><span>02</span><strong>Propose</strong><p>A repair policy constructs an allowlisted patch, expected revisions, and a tamper-evident proposal hash.</p></article><i>→</i><article><span>03</span><strong>Approve</strong><p>A human confirms the exact hash, actor, reason, acknowledgement, Action version, and Flow revision fences.</p></article><i>→</i><article><span>04</span><strong>Prove</strong><p>The runtime publishes successors and executes a linked proof Run. Parent history and effects remain unchanged.</p></article></div>
          </DocSection>

          <DocSection id="forge" number="13" kicker="Evidence-bound self-improvement" title="Distil, qualify, then human-promote a reusable Skill">
            <p>The Capability Forge starts only from a <strong>completed</strong> model-backed Step whose Run event ledger verifies. Code freezes a bounded source envelope: the Flow and Step pins, source Agent fingerprint, model-call hashes, validated input/output, terminal state, and relevant ledger events. A second, independent Agent receives that envelope through one strict, tool-free OpenAI Responses call.</p>
            <div className="maintenance-doc-flow"><article><span>01</span><strong>Observe</strong><p>Select one completed source model Step. Failed or unverifiable Runs are ineligible.</p></article><i>→</i><article><span>02</span><strong>Distil</strong><p>An independent pinned Agent proposes behavioral instructions and must cite supplied event IDs.</p></article><i>→</i><article><span>03</span><strong>Qualify</strong><p>Code replays source hashes, the ledger chain, citations, fingerprints, Agent independence, and zero authority delta.</p></article><i>→</i><article><span>04</span><strong>Promote</strong><p>A human acknowledges the exact candidate fingerprint and publishes one authority-free immutable Skill v1.</p></article></div>
            <Callout tone="warning" title="Provenance is not performance">Qualification proves where the candidate came from and what it cannot do. It does not prove a universal improvement. Promotion changes no Agent or Flow; the operator must attach the Skill through a successor Agent version and prove the changed outcome in a new Run.</Callout>
            <p>Every distillation receipt, candidate, qualification, and decision is append-only. Rejection preserves the candidate and its evidence. Promotion grants zero static tools and zero callable Actions by construction, so model-written text can never widen its own authority.</p>
          </DocSection>

          <DocSection id="credentials" number="14" kicker="BYOK security" title="Your OpenAI key lives only in this browser tab">
            <p>Settings stores the key in <code>sessionStorage</code>. It is sent only in the <code>X-OpenAI-API-Key</code> header of same-origin operations that may need a model. The server constructs an official SDK client for that bounded operation. The key is never written to SQLite, an event, a receipt, a log, a response, or the repository, and disappears when the tab session ends or you clear it.</p>
            <Callout tone="warning" title="Use a restricted, temporary key—not a production credential">OpenAI recommends keeping standard API keys out of browser code. This anonymous Build Week lab uses visitor-requested session BYOK, so anyone with access to the tab can invoke its bounded model surface. Clear the key before sharing or leaving the tab. <a href="https://developers.openai.com/api/reference/overview#authentication" target="_blank" rel="noreferrer">OpenAI authentication guidance</a>.</Callout>
          </DocSection>

          <DocSection id="contracts" number="15" kicker="Bounded by design" title="Runtime and graph limits">
            <div className="limit-grid"><Limit value="64" label="nodes per Flow" /><Limit value="192" label="routes per Flow" /><Limit value="12" label="outcomes per node" /><Limit value="8" label="fan-out members" /><Limit value="8" label="acceptance promises" /><Limit value="96 KiB" label="Judge / full-read bound" /><Limit value="4" label="nested Flow levels" /><Limit value="200" label="expanded nodes / sources" /><Limit value="256 KiB" label="source / API body" /><Limit value="3" label="attempts per node" /><Limit value="1" label="fan-out parent attempt" /><Limit value="4" label="AI tool calls" /><Limit value="6" label="models per sweep" /><Limit value="5" label="repetitions per model" /></div>
            <p>Flows are directed acyclic graphs. Every node must be reachable from the start. Step mappings may read only reachable predecessors. Coordinates are bounded. Provider I/O happens outside SQLite write transactions. Terminal Run states are absorbing. Mutations use optimistic revision or version fences.</p>
          </DocSection>

          <DocSection id="boundary" number="16" kicker="What this release is" title="A standalone projection—not Kyn’s private architecture">
            <p>This repository deliberately excludes Ainou, CE, Appiyon’s Parts/Entities, Bricks/Packs/Frames, internal graph storage, private Agents, and Mekyn. Its SQLite schema is a simple product-facing set of tables for definitions, immutable versions, Runs, evidence, approvals, and effects. It does not reproduce the ontology or implementation behind the larger Kyn system.</p>
            <div className="boundary-compare"><div><Badge tone="success">Included and real</Badge><ul><li>Versioned Actions, Prompts, Skills, Agents, Flows</li><li>Knowledge, SmartRead, cited retrieval and governed Memory</li><li>Generic concurrent fan-out/barrier and guided BoardRooms</li><li>Official OpenAI SDK transport and browser BYOK</li><li>Full visual graph editor and reusable Flow nodes</li><li>Evidence-bound Goal-Judge completion contracts</li><li>Dead-end ratification, principles, and controlled model sweeps</li><li>Durable execution, approval, repair and linked proof</li><li>Quarantined, provenance-qualified Capability Forge</li><li>Bounded webhook and schedule activation</li></ul></div><div><Badge tone="neutral">Deliberately excluded</Badge><ul><li>Ainou and private multi-layer orchestration</li><li>Parts/Entities and Bricks/Packs/Frames</li><li>CE training and token-model internals</li><li>Private Boardroom organizational-dynamics machinery</li><li>Production connectors or arbitrary code/network authority</li><li>Claims of autonomous or universally improving Skills</li><li>Claims that the sandbox is a production integration</li></ul></div></div>
          </DocSection>
        </main>
      </div>
    </section>
  );
}

function DocSection({ id, number, kicker, title, children }) {
  return <section className="doc-section" id={id}><header><span>{number}</span><div><p className="panel-kicker">{kicker}</p><h2>{title}</h2></div></header>{children}</section>;
}

function Definition({ icon, title, text }) { return <article><span><Icon name={icon} size={20} /></span><div><strong>{title}</strong><p>{text}</p></div></article>; }
function Callout({ tone, title, children }) { return <aside className={`doc-callout callout-${tone}`}><Icon name={tone === "warning" ? "warning" : tone === "ai" ? "agent" : "check"} size={20} /><div><strong>{title}</strong><p>{children}</p></div></aside>; }
function Code({ title, children }) { return <div className="doc-code"><header><span>{title}</span><Badge tone="neutral">JSON</Badge></header><pre>{children}</pre></div>; }
function Limit({ value, label }) { return <article><strong>{value}</strong><span>{label}</span></article>; }
