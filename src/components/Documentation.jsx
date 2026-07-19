import React from "react";
import { Icon } from "../icons.jsx";
import { Badge, Button, PageHeader } from "./ui.jsx";

const SECTIONS = [
  ["mental-model", "Mental model"],
  ["build-flow", "Build a Flow"],
  ["named-outputs", "Named outputs"],
  ["agent-stack", "Agents and AI"],
  ["subflows", "Reusable Flows"],
  ["runs", "Run operations"],
  ["maintenance", "Maintenance loop"],
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
          <DocSection id="mental-model" number="01" kicker="Mental model" title="Five definitions. One execution truth.">
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

          <DocSection id="build-flow" number="02" kicker="Visual workbench" title="Build a Flow on the full canvas">
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

          <DocSection id="named-outputs" number="03" kicker="Routing" title="A node can own up to twelve outputs">
            <p>Success/failure is only the default. Router and AI Actions can declare domain outputs such as <code>enterprise</code>, <code>needs-review</code>, <code>duplicate</code>, and <code>fallback</code>. Every output has an ID, human label, description, and visual tone. The canvas distributes ports vertically so wires do not collapse into one source point.</p>
            <div className="port-doc"><span className="tone-ai"><i />Enterprise<small>enterprise</small></span><span className="tone-success"><i />Self serve<small>self-serve</small></span><span className="tone-warning"><i />Needs review<small>needs-review</small></span><span className="tone-danger"><i />Error<small>error</small></span></div>
            <Callout tone="warning" title="No hidden fallback route">A wire is valid only if the source version declares that exact outcome. The runtime never silently substitutes a generic success path. Error continuation is explicit per node and requires an error route.</Callout>
          </DocSection>

          <DocSection id="agent-stack" number="04" kicker="OpenAI integration" title="AI is visible, pinned, and authority-bounded">
            <p>An AI Action chooses an immutable Agent version. The Agent chooses an OpenAI model and pins one Prompt plus zero or more Skills. Skills grant a bounded union of static tools and exact callable Action versions. At invocation, the runtime intersects a model request with that union, validates every argument, and records a model-call summary plus Action receipts.</p>
            <div className="doc-sequence"><span>AI Action</span><i>→</i><span>Agent v4</span><i>→</i><span>Prompt v2</span><i>+</i><span>Skills v3/v7</span><i>→</i><span>Responses API</span></div>
            <div className="doc-two-column"><div><h3>OpenAI owns</h3><ul><li>Model inference</li><li>Responses transport</li><li>Reasoning and function-call proposals</li><li>Strict structured output generation</li></ul></div><div><h3>Kyn owns</h3><ul><li>Graph orchestration and durable state</li><li>Version and authority pins</li><li>Action dispatch and validation</li><li>Approval, receipts, evidence, repair and replay truth</li></ul></div></div>
            <Code title="AI executor policy">{`{
  "max_tool_calls": 2,
  "reasoning_effort": "medium",
  "outcome_path": "decision"
}`}</Code>
            <p>If <code>outcome_path</code> is configured, the corresponding strict output field must be a string enum that exactly matches every non-error Action outcome. That is how AI decisions become typed graph ports—not parsed prose.</p>
          </DocSection>

          <DocSection id="subflows" number="05" kicker="Composition" title="A published Flow is a first-class node">
            <p>After publication, a Flow version appears in the node library. Its input, output, and outcome contracts become the node boundary. Execution creates a linked child Run rather than flattening evidence into the parent. If the child pauses for approval, the parent pauses on its Flow Step; when the child becomes terminal, the parent resumes or fails from that exact result.</p>
            <div className="doc-contract-row"><article><strong>Pin</strong><p>The parent stores the child Flow version ID and fingerprint.</p></article><article><strong>Execute</strong><p>Child gets the same correlation ID plus explicit parent Run and Step IDs.</p></article><article><strong>Observe</strong><p>Parent and child retain separate Steps, events, calls, receipts, effects, and outcomes.</p></article><article><strong>Bound</strong><p>Cycles are rejected; depth is four; expanded work is capped at 200 nodes.</p></article></div>
          </DocSection>

          <DocSection id="runs" number="06" kicker="Operations" title="Observe and control work as Runs">
            <p>A Run is persisted and fully pinned before a worker or provider call begins. The operations console overlays current state on the exact pinned graph and exposes separate views for Steps, the hash-linked event timeline, OpenAI call summaries, Action receipts, bounded effects, approvals, and lineage.</p>
            <table className="doc-table"><thead><tr><th>Evidence</th><th>What it proves</th><th>What it deliberately omits</th></tr></thead><tbody><tr><td>Step</td><td>Node, version, attempt, validated input/output, outcome and error</td><td>No secret credentials</td></tr><tr><td>Event</td><td>Ordered state change with actor, payload fingerprint and hash link</td><td>No hidden chain-of-thought</td></tr><tr><td>Model call</td><td>Provider response ID, model, request ID, status, token usage and safe hashes</td><td>No API key or raw reasoning</td></tr><tr><td>Action receipt</td><td>Exact version, attempt, idempotency key, result and failure code</td><td>No unvalidated executor output</td></tr><tr><td>Effect</td><td>Committed collection and payload in the isolated workspace store</td><td>No production integration claim</td></tr></tbody></table>
            <Callout tone="ai" title="Human gates are state, not UI">An approval Action moves the Step and Run into <code>waiting_approval</code>. The decision stores actor and reason, then either resumes the same pinned graph or terminates it as rejected. Refreshing or closing the page does not bypass the gate.</Callout>
          </DocSection>

          <DocSection id="maintenance" number="07" kicker="Forward recovery" title="Diagnose, approve a successor, then prove it">
            <p>The maintenance loop is an included platform capability, not the only demo. A blocked or failed Run remains terminal. Recovery proceeds through explicit new artifacts and linked work:</p>
            <div className="maintenance-doc-flow"><article><span>01</span><strong>Diagnose</strong><p>Code owns the causal candidate. A diagnostician may explain it only from cited event IDs belonging to the Run.</p></article><i>→</i><article><span>02</span><strong>Propose</strong><p>A repair policy constructs an allowlisted patch, expected revisions, and a tamper-evident proposal hash.</p></article><i>→</i><article><span>03</span><strong>Approve</strong><p>A human confirms the exact hash, actor, reason, acknowledgement, Action version, and Flow revision fences.</p></article><i>→</i><article><span>04</span><strong>Prove</strong><p>The runtime publishes successors and executes a linked proof Run. Parent history and effects remain unchanged.</p></article></div>
          </DocSection>

          <DocSection id="credentials" number="08" kicker="BYOK security" title="Your OpenAI key lives only in this browser tab">
            <p>Settings stores the key in <code>sessionStorage</code>. It is sent only in the <code>X-OpenAI-API-Key</code> header of same-origin operations that may need a model. The server constructs an official SDK client for that bounded operation. The key is never written to SQLite, an event, a receipt, a log, a response, or the repository, and disappears when the tab session ends or you clear it.</p>
            <Callout tone="warning" title="Use a restricted, temporary key—not a production credential">OpenAI recommends keeping standard API keys out of browser code. This anonymous Build Week lab uses visitor-requested session BYOK, so anyone with access to the tab can invoke its bounded model surface. Clear the key before sharing or leaving the tab. <a href="https://developers.openai.com/api/reference/overview#authentication" target="_blank" rel="noreferrer">OpenAI authentication guidance</a>.</Callout>
          </DocSection>

          <DocSection id="contracts" number="09" kicker="Bounded by design" title="Runtime and graph limits">
            <div className="limit-grid"><Limit value="64" label="nodes per Flow" /><Limit value="192" label="routes per Flow" /><Limit value="12" label="outcomes per node" /><Limit value="4" label="nested Flow levels" /><Limit value="200" label="expanded nodes" /><Limit value="3" label="attempts per node" /><Limit value="4" label="AI tool calls" /><Limit value="256 KiB" label="API body" /></div>
            <p>Flows are directed acyclic graphs. Every node must be reachable from the start. Step mappings may read only reachable predecessors. Coordinates are bounded. Provider I/O happens outside SQLite write transactions. Terminal Run states are absorbing. Mutations use optimistic revision or version fences.</p>
          </DocSection>

          <DocSection id="boundary" number="10" kicker="What this release is" title="A standalone projection—not Kyn’s private architecture">
            <p>This repository deliberately excludes Ainou, CE, Appiyon’s Parts/Entities, Bricks/Packs/Frames, internal graph storage, private Agents, and Mekyn. Its SQLite schema is a simple product-facing set of tables for definitions, immutable versions, Runs, evidence, approvals, and effects. It does not reproduce the ontology or implementation behind the larger Kyn system.</p>
            <div className="boundary-compare"><div><Badge tone="success">Included and real</Badge><ul><li>Versioned Actions, Prompts, Skills, Agents, Flows</li><li>Official OpenAI SDK transport and browser BYOK</li><li>Full visual graph editor and reusable Flow nodes</li><li>Durable execution, evidence, approval, repair and proof</li><li>Bounded webhook and schedule activation</li></ul></div><div><Badge tone="neutral">Deliberately excluded</Badge><ul><li>Ainou and private multi-layer orchestration</li><li>Parts/Entities and Bricks/Packs/Frames</li><li>CE training and token-model internals</li><li>Production connectors or arbitrary code/network authority</li><li>Claims that the sandbox is a production integration</li></ul></div></div>
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
