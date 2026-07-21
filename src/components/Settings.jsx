import React, { useState } from "react";
import { browserKey, saveBrowserKey } from "../api.js";
import { Icon } from "../icons.jsx";
import { shortId, topLevelRuns } from "../lib.js";
import { Badge, Button, Field, PageHeader, ThemeChoice } from "./ui.jsx";

export default function Settings({ snapshot, keyConfigured, onKeyChanged, onNewWorkspace, busy }) {
  const [key, setKey] = useState(browserKey());
  const [show, setShow] = useState(false);
  const [message, setMessage] = useState("");
  const save = (event) => {
    event.preventDefault();
    const normalized = key.trim();
    if (normalized && (normalized.length < 20 || normalized.length > 512 || /\s/.test(normalized))) {
      setMessage("Enter a valid API key between 20 and 512 characters without whitespace.");
      return;
    }
    saveBrowserKey(normalized);
    onKeyChanged();
    setMessage(normalized ? "Key stored for this browser tab." : "Key cleared from this browser tab.");
  };
  const clear = () => { setKey(""); saveBrowserKey(""); onKeyChanged(); setMessage("Key cleared from this browser tab."); };
  const workspaceId = snapshot.workspace?.id ?? snapshot.workspace_id;
  return (
    <section className="settings-page">
      <PageHeader eyebrow="Browser and workspace configuration" title="Settings" description="Credentials remain outside the product database. Workspace state is isolated, expiring, and deliberately disposable." />
      <div className="settings-grid">
        <section className="settings-card key-card">
          <header><span className="settings-icon"><Icon name="key" size={22} /></span><div><p className="panel-kicker">Bring your own key</p><h2>OpenAI API key</h2></div><Badge tone={keyConfigured ? "success" : "warning"} dot>{keyConfigured ? "Configured" : "Not configured"}</Badge></header>
          <p>Required only for AI Actions, Agent nodes, and model-backed diagnosis. Deterministic Actions and Flows run without a key.</p>
          <div className="credential-caution"><Icon name="warning" size={17} /><p><strong>Use a restricted, temporary project key.</strong>OpenAI recommends keeping standard API keys out of browser code. This public BYOK lab stores the key only for your tab, but browser access remains a deliberate visitor-owned risk.</p></div>
          <form onSubmit={save}>
            <Field label="API key" hint="Stored in sessionStorage; removed when this tab session ends.">
              <div className="secret-input"><input type={show ? "text" : "password"} autoComplete="off" spellCheck="false" placeholder="sk-…" value={key} onChange={(event) => setKey(event.target.value)} /><Button type="button" tone="quiet" onClick={() => setShow((value) => !value)}>{show ? "Hide" : "Show"}</Button></div>
            </Field>
            <div className="settings-actions"><Button tone="quiet" type="button" onClick={clear} disabled={!keyConfigured && !key}>Clear</Button><Button tone="primary" icon="save" type="submit">Save in this tab</Button></div>
          </form>
          {message ? <p className="settings-message" role="status">{message}</p> : null}
          <div className="credential-path">
            <span>Browser tab</span><i>same-origin header</i><span>Bounded API operation</span><i>per-call client</i><span>OpenAI Responses</span>
          </div>
        </section>
        <section className="settings-card appearance-card">
          <header><span className="settings-icon"><Icon name="sun" size={22} /></span><div><p className="panel-kicker">Interface</p><h2>Theme</h2></div></header>
          <p>Follows your system preference until you choose here. The choice is stored in this browser.</p>
          <div className="appearance-choice"><ThemeChoice /></div>
        </section>
        <section className="settings-card security-card">
          <header><span className="settings-icon"><Icon name="lock" size={22} /></span><div><p className="panel-kicker">Credential contract</p><h2>What the runtime guarantees</h2></div></header>
          <ul>
            <li><Icon name="check" size={16} /><span><strong>Never persisted</strong><small>No key enters SQLite, a Run, Step, event, model summary, receipt, effect, or repair.</small></span></li>
            <li><Icon name="check" size={16} /><span><strong>Never returned</strong><small>The server does not echo the key or expose a credential inspection endpoint.</small></span></li>
            <li><Icon name="check" size={16} /><span><strong>Same origin only</strong><small>The browser client refuses non-API paths and sends no cross-origin runtime request.</small></span></li>
            <li><Icon name="check" size={16} /><span><strong>Bounded authority</strong><small>A valid key does not unlock shell, filesystem, arbitrary HTTP, or production writes.</small></span></li>
          </ul>
          <a className="security-guidance-link" href="https://developers.openai.com/api/reference/overview#authentication" target="_blank" rel="noreferrer">Read OpenAI API key guidance <Icon name="external" size={13} /></a>
        </section>
        <section className="settings-card workspace-card">
          <header><span className="settings-icon"><Icon name="flow" size={22} /></span><div><p className="panel-kicker">Isolated SQLite projection</p><h2>Workspace</h2></div><Badge tone="success">active</Badge></header>
          <dl><div><dt>Workspace</dt><dd><code>{shortId(workspaceId, 16)}</code></dd></div><div><dt>Actions</dt><dd>{snapshot.studio.actions.length}</dd></div><div><dt>Flows</dt><dd>{snapshot.studio.flows.length}</dd></div><div><dt>Orchestrations</dt><dd>{topLevelRuns(snapshot.studio.runs).length}</dd></div><div><dt>Execution records</dt><dd>{snapshot.studio.runs.length}</dd></div><div><dt>Agents</dt><dd>{snapshot.agents.length}</dd></div></dl>
          <div className="workspace-warning"><Icon name="warning" size={18} /><p><strong>Starting fresh is forward-only.</strong>Your current workspace is not deleted; this browser receives a new isolated cookie and can no longer address the old workspace.</p></div>
          <Button tone="danger" onClick={onNewWorkspace} disabled={busy}>Create a new workspace</Button>
        </section>
        <section className="settings-card boundary-settings-card">
          <header><span className="settings-icon"><strong>K</strong></span><div><p className="panel-kicker">Release boundary</p><h2>Standalone by construction</h2></div></header>
          <p>This Build Week repository contains a flat product projection and no imports or copied schemas from the private Kyn system.</p>
          <div className="boundary-tags"><Badge tone="success">Python stdlib server</Badge><Badge tone="success">SQLite WAL</Badge><Badge tone="success">Official OpenAI SDK</Badge><Badge tone="success">Self-hosted UI assets</Badge><Badge tone="neutral">No Ainou</Badge><Badge tone="neutral">No CE</Badge><Badge tone="neutral">No ontology copy</Badge></div>
          <a className="button button-default" href="https://github.com/cakbudak/kyn-agent-studio" target="_blank" rel="noreferrer"><Icon name="external" size={16} /><span>Audit the public source</span></a>
        </section>
      </div>
    </section>
  );
}
