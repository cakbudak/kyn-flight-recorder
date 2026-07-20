import React, { useEffect, useMemo, useRef, useState } from "react";
import { api, commandId } from "../api.js";
import { Icon } from "../icons.jsx";
import { exampleForSchema, formatTime, parseJson, shortId, titleCase } from "../lib.js";
import { Badge, Button, EmptyState, Field, JsonField, Modal, PageHeader, StatusBadge } from "./ui.jsx";

/** The three invariance claims, in the order the scoreboard states them.
 *
 * `sibling` names the matching key in a sibling's `stable_across_repetitions`,
 * because a cross-model claim is only reportable once each model agreed with
 * *itself*, and naming the unstable model is the whole point of the refusal.
 */
const INVARIANCE_CLAIMS = [
  {
    key: "routed_outcome",
    sibling: "outcome",
    label: "Routed outcome",
    detail: "The named outcome the deterministic gate selected."
  },
  {
    key: "terminal_status",
    sibling: "status",
    label: "Terminal status",
    detail: "Where each sibling Run came to rest."
  },
  {
    key: "guard_behaviour",
    sibling: "guard_trace",
    label: "Guard behaviour",
    detail: "Every gate, assert, router, template and approval, excluding the model steps themselves."
  }
];

const CLASSIFICATION = {
  within_noise: {
    tone: "neutral",
    label: "Within noise",
    detail: "Smaller than the harness's own spread on identical input, so it is a non-result."
  },
  signal: {
    tone: "blue",
    label: "Signal",
    detail: "Larger than the harness's measured spread on identical input."
  },
  unmeasured: {
    tone: "warning",
    label: "Unmeasured",
    detail: "The harness has not measured itself, so no difference here may be reported as a finding."
  }
};

function formatNumber(value) {
  if (value === null || value === undefined) return "—";
  return typeof value === "number" ? value.toLocaleString("en-US") : String(value);
}

function comparisonModels(comparison) {
  // Request order, never sorted by any measurement. Sorting a table by tokens
  // or latency is how an invariance report quietly becomes a leaderboard.
  const byModel = new Map(comparison.siblings.map((sibling) => [sibling.model, sibling]));
  return comparison.models.map((model) => byModel.get(model)).filter(Boolean);
}

export default function Comparisons({
  snapshot,
  mutate,
  busy,
  focusRun,
  comparisonFlowId = null,
  onComparisonRequestHandled
}) {
  const comparisons = snapshot.studio.comparisons ?? [];
  const [selectedId, setSelectedId] = useState(comparisons[0]?.id ?? null);
  const [showStart, setShowStart] = useState(false);
  const [presetFlowId, setPresetFlowId] = useState(null);
  const honoured = useRef(null);
  const selected = comparisons.find((item) => item.id === selectedId) ?? comparisons[0] ?? null;

  useEffect(() => {
    if (comparisons.some((item) => item.id === selectedId)) return;
    setSelectedId(comparisons[0]?.id ?? null);
  }, [comparisons, selectedId]);

  // Flow Studio can ask for a comparison of the Flow on the canvas. Honour that
  // request exactly once so a later refresh cannot reopen a dismissed dialog.
  useEffect(() => {
    if (!comparisonFlowId || honoured.current === comparisonFlowId) return;
    honoured.current = comparisonFlowId;
    setPresetFlowId(comparisonFlowId);
    setShowStart(true);
    onComparisonRequestHandled?.();
  }, [comparisonFlowId, onComparisonRequestHandled]);

  const modelBackedFlows = useMemo(
    () => snapshot.studio.flows.filter((flow) => flow.version.requires_model),
    [snapshot.studio.flows]
  );

  const openStart = (flowId = null) => {
    setPresetFlowId(flowId);
    setShowStart(true);
  };

  return (
    <section className="comparisons-page">
      <PageHeader
        eyebrow="Controlled cross-model sweep"
        title="Model comparison"
        description="One immutable pinned Flow version, one input, N models. Before provider I/O, the complete expected model × repetition × Run set is hash-ledgered as a manifest. Every sibling then pins a byte-identical flow_version_id, so the only recorded delta is the model. This asks whether the scaffolding behaves the same on every brain — not which brain is best."
        actions={
          <Button
            tone="primary"
            icon="compare"
            onClick={() => openStart(null)}
            disabled={!modelBackedFlows.length}
          >
            New comparison
          </Button>
        }
      />
      <div className="comparisons-workbench">
        <aside className="comparison-list" aria-label="Recorded comparisons">
          <header>
            <span>{comparisons.length} comparison{comparisons.length === 1 ? "" : "s"}</span>
            <Badge tone="neutral">Derived from Runs</Badge>
          </header>
          <div className="comparison-list-scroll">
            {comparisons.map((item) => {
              const flow = snapshot.studio.flows.find((entry) => entry.id === item.flow_id);
              return (
                <button
                  key={item.id}
                  type="button"
                  className={`comparison-list-item ${selected?.id === item.id ? "is-active" : ""}`}
                  onClick={() => setSelectedId(item.id)}
                  aria-current={selected?.id === item.id ? "true" : undefined}
                >
                  <span className={`comparison-state-dot ${item.usable ? "is-usable" : "is-unusable"}`} aria-hidden="true" />
                  <span>
                    <strong>{flow?.name ?? item.flow_id}</strong>
                    <small>{shortId(item.id)} · {formatTime(item.created_at)}</small>
                    <em>{item.models.length} models × {item.repetitions} rep{item.repetitions === 1 ? "" : "s"}</em>
                  </span>
                  <Badge tone={item.usable ? "success" : "danger"} dot>
                    {item.usable ? "Usable" : "Unusable"}
                  </Badge>
                </button>
              );
            })}
            {!comparisons.length ? (
              <EmptyState
                icon="compare"
                title="No comparison recorded yet"
                description={
                  modelBackedFlows.length
                    ? "Pin one published, model-backed Flow version and run it against two or more brains to find out whether the scaffolding, not the model, decides the outcome."
                    : "Publish a Flow that calls a model first. A deterministic Flow has no brain to vary, so the runtime refuses to compare it."
                }
                action={
                  modelBackedFlows.length ? (
                    <Button tone="primary" icon="compare" onClick={() => openStart(null)}>Start the first comparison</Button>
                  ) : null
                }
              />
            ) : null}
          </div>
        </aside>
        <main className="comparison-detail">
          {selected ? (
            <Scoreboard comparison={selected} snapshot={snapshot} onSelectRun={focusRun} />
          ) : (
            <EmptyState
              icon="compare"
              title="Select a comparison"
              description="The scoreboard states its proof of control before it states any measurement."
            />
          )}
        </main>
      </div>
      {showStart ? (
        <StartComparisonModal
          snapshot={snapshot}
          flows={modelBackedFlows}
          initialFlowId={presetFlowId}
          mutate={mutate}
          busy={busy}
          onClose={() => setShowStart(false)}
          onCreated={(created) => {
            setShowStart(false);
            if (created?.id) setSelectedId(created.id);
          }}
        />
      ) : null}
    </section>
  );
}

/** The scoreboard, in the one order that keeps it honest.
 *
 * Verdict, then proof of control, then invariance, then integrity problems,
 * then the noise band, then the per-model rows. The measurements come last on
 * purpose: a reader must have to pass the evidence that the comparison was
 * controlled before they can reach the numbers it produced.
 */
function Scoreboard({ comparison, snapshot, onSelectRun }) {
  const flow = snapshot.studio.flows.find((entry) => entry.id === comparison.flow_id);
  return (
    <article className="scoreboard" aria-labelledby="scoreboard-title">
      <header className="scoreboard-header">
        <div>
          <p className="panel-kicker">{flow?.name ?? comparison.flow_id} · pinned v{comparison.flow_version}</p>
          <h2 id="scoreboard-title">Invariance across {comparison.models.length} brains</h2>
          <p>
            Same pinned graph, same input, {comparison.repetitions} repetition{comparison.repetitions === 1 ? "" : "s"} per model.
            This record reports whether the scaffolding behaved identically on every brain.{" "}
            <strong>It is not a ranking and contains no verdict about model quality.</strong>
          </p>
        </div>
        <div className="scoreboard-marks">
          <Badge tone="ai">evidence_class · {comparison.evidence_class}</Badge>
          <Badge tone="warning" dot>usable_as_baseline · false</Badge>
        </div>
      </header>

      <Verdict comparison={comparison} />

      <p className="scoreboard-baseline-note">{comparison.baseline_note}</p>

      <ControlProof comparison={comparison} />
      <Invariance comparison={comparison} />
      {comparison.integrity_problems.length ? <IntegrityProblems comparison={comparison} /> : null}
      <NoiseBand comparison={comparison} />
      <SiblingRows comparison={comparison} onSelectRun={onSelectRun} />
    </article>
  );
}

/** Usable or not, stated before anything a reader could quote out of context. */
function Verdict({ comparison }) {
  if (!comparison.usable) {
    const codes = [...new Set(comparison.integrity_problems.map((problem) => problem.code))];
    return (
      <section className="comparison-verdict is-unusable" role="alert" aria-labelledby="comparison-verdict-title">
        <span className="comparison-verdict-icon"><Icon name="warning" size={22} /></span>
        <div>
          <p className="panel-kicker">usable · false</p>
          <h3 id="comparison-verdict-title">This comparison is not a result and must not be presented as one.</h3>
          <p>
            An integrity gate fired, so at least one sibling did not test the model it claims. Every number below is
            still shown because hiding it would hide the evidence, but <strong>nothing here supports a conclusion
            about any model</strong>. This is not an error to dismiss: it is the reason the comparisons that pass can
            be trusted at all.
          </p>
          <ul className="comparison-verdict-codes">
            {codes.map((code) => <li key={code}><code>{code}</code></li>)}
          </ul>
        </div>
      </section>
    );
  }
  return (
    <section className="comparison-verdict is-usable" aria-labelledby="comparison-verdict-title">
      <span className="comparison-verdict-icon"><Icon name="check" size={22} /></span>
      <div>
        <p className="panel-kicker">usable · true</p>
        <h3 id="comparison-verdict-title">Every integrity gate held. The recorded delta is the model and nothing else.</h3>
        <p>
          The expected sibling set was ledger-pinned before provider I/O. Each sibling then pinned the same immutable
          Flow version, received the same input by recomputed fingerprint, and was answered by the model it actually
          requested. What that licenses is a statement about{" "}
          <strong>invariance</strong> — never a ranking.
        </p>
      </div>
    </section>
  );
}

/** What was actually controlled, and — just as loudly — what was not. */
function ControlProof({ comparison }) {
  return (
    <section className="comparison-section" aria-labelledby="control-proof-title">
      <header>
        <h3 id="control-proof-title"><Icon name="lock" size={14} />Proof of control</h3>
        <span>Stated before any measurement</span>
      </header>
      <dl className="control-pins">
        <div>
          <dt>Shared flow_version_id</dt>
          <dd><code>{comparison.flow_version_id}</code></dd>
        </div>
        <div>
          <dt>Input fingerprint</dt>
          <dd><code>{comparison.input_fingerprint}</code></dd>
        </div>
        <div>
          <dt>Flow fingerprint</dt>
          <dd><code>{comparison.flow_fingerprint}</code></dd>
        </div>
        <div>
          <dt>Pinned model replaced by override</dt>
          <dd><code>{comparison.pinned_model}</code></dd>
        </div>
        <div>
          <dt>Pre-I/O sweep manifest</dt>
          <dd><code>{comparison.manifest?.fingerprint ?? "missing"}</code></dd>
        </div>
      </dl>
      <div className="control-columns">
        <section className="control-column is-enforced" aria-labelledby="control-enforced-title">
          <h4 id="control-enforced-title">
            <Icon name="check" size={13} />Enforced and verified
            <b>{comparison.control.enforced_and_verified.length}</b>
          </h4>
          <ul>
            {comparison.control.enforced_and_verified.map((entry) => (
              <li key={entry.control}>
                <p className="control-name">
                  <code>{entry.control}</code>
                  <Badge tone={entry.verified ? "success" : "danger"} dot>
                    {entry.verified ? "Verified" : "Not verified"}
                  </Badge>
                </p>
                <p className="control-method">{entry.method}</p>
                {entry.value ? <p className="control-value"><code>{entry.value}</code></p> : null}
              </li>
            ))}
          </ul>
        </section>
        <section className="control-column is-uncontrolled" aria-labelledby="control-uncontrolled-title">
          <h4 id="control-uncontrolled-title">
            <Icon name="warning" size={13} />Not controllable here
            <b>{comparison.control.not_controllable_here.length}</b>
          </h4>
          <p className="control-column-note">
            Claiming a control that was not enforced is the fastest way to make an honest experiment dishonest, so each
            one is named with its reason rather than summarised as a single nondeterminism bucket.
          </p>
          <ul>
            {comparison.control.not_controllable_here.map((entry) => (
              <li key={entry.variable}>
                <p className="control-name"><code>{entry.variable}</code></p>
                <p className="control-method">{entry.reason}</p>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </section>
  );
}

/** The headline claim, and the refusal to state it when a model moved on itself. */
function Invariance({ comparison }) {
  const unstableModels = (siblingKey) =>
    comparison.siblings
      .filter((sibling) => !sibling.stable_across_repetitions[siblingKey])
      .map((sibling) => sibling.model);

  return (
    <section className="comparison-section" aria-labelledby="invariance-title">
      <header>
        <h3 id="invariance-title"><Icon name="skill" size={14} />Invariance — the headline</h3>
        <span>Cost spread is a footnote to this, never a ranking</span>
      </header>
      <ul className="invariance-list">
        {INVARIANCE_CLAIMS.map((claim) => {
          const record = comparison.invariance[claim.key];
          const unstable = unstableModels(claim.sibling);
          const refused = !record.stable_within_each_model;
          const state = refused ? "refused" : record.invariant ? "invariant" : "differs";
          return (
            <li key={claim.key} className={`invariance-item is-${state}`}>
              <header>
                <strong>{claim.label}</strong>
                <Badge tone={state === "invariant" ? "success" : state === "differs" ? "warning" : "danger"} dot>
                  {state === "invariant" ? "Invariant" : state === "differs" ? "Differs across brains" : "Not stated"}
                </Badge>
              </header>
              <p className="invariance-detail">{claim.detail}</p>
              {refused ? (
                <p className="invariance-refusal">
                  <strong>{unstable.join(", ")}</strong> did not agree with{" "}
                  {unstable.length === 1 ? "itself" : "themselves"} across{" "}
                  {unstable.length === 1 ? "its" : "their"} own repetitions, so cross-model invariance is not claimed
                  here. We will not manufacture agreement by picking the run that agreed. This is a finding, not
                  missing data.
                </p>
              ) : null}
              <dl className="invariance-by-model">
                {comparison.models.map((model, index) => {
                  const value = record.by_model[model];
                  const text = value === null || value === undefined ? "—" : String(value);
                  // A guard signature is a whole serialised trace, far too long
                  // to read side by side. It is truncated for display with the
                  // full value on the element, and the question a reader
                  // actually has -- does it match the first model? -- is
                  // answered directly rather than left to eyeballing a blob.
                  const reference = record.by_model[comparison.models[0]];
                  const matches = String(reference ?? "—") === text;
                  return (
                    <div key={model}>
                      <dt>{model}</dt>
                      <dd>
                        <code title={text}>{text.length > 28 ? `${text.slice(0, 27)}…` : text}</code>
                        {index > 0 ? (
                          <b className={matches ? "is-match" : "is-differ"}>
                            {matches ? "matches first" : "differs from first"}
                          </b>
                        ) : null}
                      </dd>
                    </div>
                  );
                })}
              </dl>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/** Every recorded reason the comparison cannot be read as a result. */
function IntegrityProblems({ comparison }) {
  return (
    <section className="comparison-section is-integrity" aria-labelledby="integrity-title">
      <header>
        <h3 id="integrity-title"><Icon name="warning" size={14} />Integrity problems</h3>
        <span>{comparison.integrity_problems.length} recorded</span>
      </header>
      <ul className="integrity-list">
        {comparison.integrity_problems.map((problem, index) => (
          <li key={`${problem.code}-${problem.call_id ?? index}`}>
            <header>
              <code>{problem.code}</code>
              {problem.model ? <strong>{problem.model}</strong> : null}
              {problem.call_id ? <span className="integrity-call">call {shortId(problem.call_id, 14)}</span> : null}
            </header>
            {problem.requested || problem.answered ? (
              <p className="integrity-swap">
                <span>requested <code>{problem.requested ?? "—"}</code></span>
                <Icon name="chevron" size={13} />
                <span>answered <code>{problem.answered ?? "—"}</code></span>
              </p>
            ) : null}
            <p>{problem.detail}</p>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** The instrument measuring itself, before it weighs anything. */
function NoiseBand({ comparison }) {
  const band = comparison.noise_band;
  return (
    <section className="comparison-section" aria-labelledby="noise-title">
      <header>
        <h3 id="noise-title"><Icon name="timeline" size={14} />Noise band</h3>
        <Badge tone={band.measured ? "cyan" : "warning"} dot>
          {band.measured ? "Measured" : "Not measured"}
        </Badge>
      </header>
      <p className="comparison-note">{band.note}</p>
      <dl className="noise-grid">
        <div>
          <dt>Basis</dt>
          <dd><code>{band.basis}</code></dd>
        </div>
        <div>
          <dt>Repetitions per model</dt>
          <dd>{band.repetitions}</dd>
        </div>
        <div>
          <dt>Token band</dt>
          <dd>{band.total_tokens === null ? "—" : `±${formatNumber(band.total_tokens)} tokens`}</dd>
        </div>
        <div>
          <dt>Latency band</dt>
          <dd>{band.duration_ms === null ? "—" : `±${formatNumber(band.duration_ms)} ms`}</dd>
        </div>
      </dl>
      <ul className="spread-list">
        {[
          ["total_tokens", "Token spread across brains", "tokens"],
          ["duration_ms", "Latency spread across brains", "ms"]
        ].map(([key, label, unit]) => {
          const spread = comparison.spread[key];
          const classification = CLASSIFICATION[spread.classification] ?? CLASSIFICATION.unmeasured;
          return (
            <li key={key} className={`spread-item is-${spread.classification}`}>
              <header>
                <strong>{label}</strong>
                <Badge tone={classification.tone} dot>{classification.label}</Badge>
              </header>
              <p className="spread-range">
                {formatNumber(spread.min)} – {formatNumber(spread.max)} {unit}
                <span>difference {formatNumber(spread.difference)} {unit}</span>
                <span>band {spread.noise_band === null ? "unmeasured" : `±${formatNumber(spread.noise_band)} ${unit}`}</span>
              </p>
              <p className="spread-detail">{classification.detail}</p>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/** The measurements, last, unranked, with every raw repetition kept. */
function SiblingRows({ comparison, onSelectRun }) {
  const siblings = comparisonModels(comparison);
  return (
    <section className="comparison-section" aria-labelledby="siblings-title">
      <header>
        <h3 id="siblings-title"><Icon name="run" size={14} />Per-model measurements</h3>
        <span>Request order · not ranked</span>
      </header>
      {/* Nine columns will not fit every viewport, so the table scrolls inside
          its own container rather than widening the panel. A scroll container
          that only a mouse can move is a keyboard trap for the columns past the
          edge, so it is focusable and named. */}
      <div
        className="sibling-table-scroll"
        tabIndex={0}
        role="region"
        aria-label="Per-model measurements, scrollable horizontally"
      >
        <table className="sibling-table">
          <caption>
            Listed in the order the models were requested. No column is sorted by any measurement and no row is marked
            as better: a cross-model sweep measures whether the scaffolding moved, not which brain won.
          </caption>
          <thead>
            <tr>
              <th scope="col">Model</th>
              <th scope="col">Status</th>
              <th scope="col">Outcome</th>
              <th scope="col">Total tokens</th>
              <th scope="col">Latency</th>
              <th scope="col">Effects</th>
              <th scope="col">Model calls</th>
              <th scope="col">Answered by</th>
              <th scope="col">Integrity</th>
            </tr>
          </thead>
          <tbody>
            {siblings.map((sibling) => (
              <tr key={sibling.model} className={sibling.integrity.verified ? "" : "is-compromised"}>
                <th scope="row">
                  <code>{sibling.model}</code>
                  <small>{sibling.repetitions} rep{sibling.repetitions === 1 ? "" : "s"}</small>
                </th>
                <td><StatusBadge status={sibling.status ?? "unknown"} /></td>
                <td>{sibling.outcome ?? "—"}</td>
                <td>
                  {formatNumber(sibling.total_tokens)}
                  {sibling.repetitions > 1 ? (
                    <small>{formatNumber(sibling.tokens.min)} – {formatNumber(sibling.tokens.max)}</small>
                  ) : null}
                </td>
                <td>
                  {sibling.duration_ms === null ? "—" : `${formatNumber(sibling.duration_ms)} ms`}
                  {sibling.repetitions > 1 ? (
                    <small>{formatNumber(sibling.duration.min)} – {formatNumber(sibling.duration.max)} ms</small>
                  ) : null}
                </td>
                <td>{formatNumber(sibling.effect_count)}</td>
                <td>{formatNumber(sibling.runs.reduce((total, run) => total + run.model_call_count, 0))}</td>
                <td>
                  <span className="answered-models">
                    {[...new Set(sibling.runs.flatMap((run) => run.response_models))].map((model) => (
                      <code key={String(model)} className={model === sibling.model ? "" : "is-mismatch"}>
                        {model ?? "missing"}
                      </code>
                    ))}
                  </span>
                </td>
                <td>
                  <Badge tone={sibling.integrity.verified ? "success" : "danger"} dot>
                    {sibling.integrity.verified ? "Verified" : "Compromised"}
                  </Badge>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ul className="repetition-list">
        {siblings.map((sibling) => (
          <li key={sibling.model}>
            <details>
              <summary>
                <code>{sibling.model}</code>
                <span>{sibling.repetitions} raw repetition{sibling.repetitions === 1 ? "" : "s"} retained</span>
              </summary>
              <ul className="repetition-runs">
                {sibling.runs.map((run, index) => (
                  <li key={run.run_id}>
                    <span className="repetition-index">#{index + 1}</span>
                    <button
                      type="button"
                      onClick={() => onSelectRun?.(run.run_id)}
                      aria-label={`Open sibling Run ${shortId(run.run_id, 14)} for ${sibling.model} repetition ${index + 1}`}
                    >
                      <Icon name="run" size={12} /><code>{shortId(run.run_id, 14)}</code>
                    </button>
                    <span>{titleCase(run.status ?? "unknown")}</span>
                    <span>{run.outcome ?? "—"}</span>
                    <span>{formatNumber(run.total_tokens)} tokens</span>
                    <span>{run.duration_ms === null ? "—" : `${formatNumber(run.duration_ms)} ms`}</span>
                    <span className={run.integrity.verified ? "" : "is-mismatch"}>
                      answered {run.response_models.map((model) => model ?? "missing").join(", ") || "—"}
                    </span>
                  </li>
                ))}
              </ul>
            </details>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** Declare the whole forecast before a single credit is spent. */
function StartComparisonModal({ snapshot, flows, initialFlowId, mutate, busy, onClose, onCreated }) {
  const supported = snapshot.studio.supported_models ?? [];
  const initial = flows.find((flow) => flow.id === initialFlowId) ?? flows[0] ?? null;
  const [flowId, setFlowId] = useState(initial?.id ?? "");
  const flow = flows.find((entry) => entry.id === flowId) ?? null;
  const [input, setInput] = useState(() =>
    JSON.stringify(exampleForSchema(initial?.version.input_schema ?? { type: "object", properties: {} }), null, 2)
  );
  const [models, setModels] = useState(() => supported.slice(0, 2));
  const [repetitions, setRepetitions] = useState(1);

  const changeFlow = (nextId) => {
    setFlowId(nextId);
    const next = flows.find((entry) => entry.id === nextId);
    if (next) setInput(JSON.stringify(exampleForSchema(next.version.input_schema), null, 2));
  };

  const toggleModel = (model) => {
    setModels((current) =>
      current.includes(model) ? current.filter((entry) => entry !== model) : [...current, model]
    );
  };

  const siblingRuns = models.length * repetitions;
  const ready = Boolean(flow) && models.length >= 2 && repetitions >= 1 && repetitions <= 5;

  const submit = async (event) => {
    event.preventDefault();
    try {
      const created = await mutate(
        () => api(`/api/v1/studio/flows/${flow.id}/comparisons`, {
          method: "POST",
          keyMode: "required",
          body: {
            input: parseJson(input, "Comparison input"),
            models,
            repetitions
          }
        }),
        { success: `Comparison recorded across ${models.length} models` }
      );
      if (created) onCreated(created);
    } catch { /* mutate renders the bounded error */ }
  };

  if (!flows.length) {
    return (
      <Modal
        title="No model-backed Flow to compare"
        description="A comparison varies the brain, so it needs a Flow that has one."
        onClose={onClose}
      >
        <p className="comparison-note">
          Every published Flow in this workspace is deterministic. A deterministic Flow calls no model, so there is no
          brain to vary and the runtime refuses the comparison rather than producing an empty table. Publish a Flow
          containing an AI node first.
        </p>
        <div className="modal-actions"><Button tone="quiet" onClick={onClose}>Close</Button></div>
      </Modal>
    );
  }

  return (
    <Modal
      title="Compare models on one pinned Flow version"
      description="Every sibling pins the identical Flow version and receives the identical input. Only the model changes, and that override is recorded on the Run and in its hash-linked chain."
      onClose={onClose}
      width="720px"
    >
      <form className="modal-form" onSubmit={submit}>
        <Field label="Model-backed Flow" required hint="Deterministic Flows are excluded: they have no brain to vary.">
          <select value={flowId} onChange={(event) => changeFlow(event.target.value)}>
            {flows.map((entry) => (
              <option key={entry.id} value={entry.id}>{entry.name} · v{entry.current_version}</option>
            ))}
          </select>
        </Field>
        <JsonField
          label="Comparison input"
          value={input}
          onChange={setInput}
          rows={8}
          hint="One validated input object, hashed and reused verbatim by every sibling."
        />
        <fieldset className="model-choice">
          <legend>Models — pick two or more</legend>
          <div className="model-choice-grid">
            {supported.map((model) => (
              <label key={model} className={`choice-card ${models.includes(model) ? "is-checked" : ""}`}>
                {/* The visible label carries a shared caption, so the model
                    name alone is the accessible name a screen reader needs to
                    tell four otherwise identical checkboxes apart. */}
                <input
                  type="checkbox"
                  aria-label={model}
                  checked={models.includes(model)}
                  onChange={() => toggleModel(model)}
                />
                <span className="node-symbol"><Icon name="agent" size={16} /></span>
                <span><strong>{model}</strong><small>Recorded per-Run override</small></span>
              </label>
            ))}
          </div>
          {models.length < 2 ? (
            <p className="field-error">A comparison needs at least two distinct models.</p>
          ) : null}
        </fieldset>
        <Field
          label="Repetitions per model"
          required
          hint="At one repetition the harness has not measured itself, so no cost difference may be reported as a finding. Bounded to five."
        >
          <input
            type="number"
            min="1"
            max="5"
            value={repetitions}
            onChange={(event) => setRepetitions(Math.max(1, Math.min(5, Number(event.target.value) || 1)))}
          />
        </Field>
        <section className="comparison-forecast" aria-labelledby="comparison-forecast-title">
          <h4 id="comparison-forecast-title"><Icon name="key" size={14} />This spends your credit before it reports anything</h4>
          <p>
            <strong>{models.length} model{models.length === 1 ? "" : "s"} × {repetitions} repetition{repetitions === 1 ? "" : "s"} = {siblingRuns} sibling Run{siblingRuns === 1 ? "" : "s"}</strong>,
            each charged to the OpenAI key held in this browser tab, and each making at least one model call. The whole
            sweep is charged against the workspace budget <em>before</em> the first sibling runs, so a comparison you
            cannot afford is refused outright rather than left half-finished.
          </p>
          {repetitions === 1 ? (
            <p className="comparison-forecast-caveat">
              At one repetition this sweep can report invariance but never a cost difference. Raise repetitions if you
              want the token and latency spread to mean anything.
            </p>
          ) : null}
        </section>
        <div className="modal-actions">
          <Button tone="quiet" type="button" onClick={onClose}>Cancel</Button>
          <Button tone="primary" icon="compare" type="submit" disabled={!ready || busy}>
            Spend {siblingRuns} Run{siblingRuns === 1 ? "" : "s"} and compare
          </Button>
        </div>
      </form>
    </Modal>
  );
}
