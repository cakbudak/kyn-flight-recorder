import React, { forwardRef, useEffect, useId, useRef } from "react";
import { Icon } from "../icons.jsx";
import { STATUS_TONE, shortId, titleCase } from "../lib.js";
import { setTheme, useTheme } from "../theme.js";

export function ThemeToggle({ className = "" }) {
  const theme = useTheme();
  const next = theme === "light" ? "dark" : "light";
  return (
    <button
      type="button"
      className={`icon-button theme-toggle ${className}`.trim()}
      onClick={() => setTheme(next)}
      aria-label={`Switch to ${next} theme`}
      title={`Switch to ${next} theme`}
    >
      <Icon name={theme === "light" ? "moon" : "sun"} size={17} />
    </button>
  );
}

const THEME_CHOICES = [
  { value: "light", icon: "sun", label: "Light", hint: "Off-white surfaces, darkened semantic ink" },
  { value: "dark", icon: "moon", label: "Dark", hint: "The default console palette" }
];

/** Explicit, labelled theme choice for Settings. */
export function ThemeChoice() {
  const theme = useTheme();
  return (
    <div className="choice-grid" role="radiogroup" aria-label="Interface theme">
      {THEME_CHOICES.map((choice) => (
        <label key={choice.value} className={`choice-card ${theme === choice.value ? "is-checked" : ""}`}>
          <input
            type="radio"
            name="theme"
            value={choice.value}
            checked={theme === choice.value}
            onChange={() => setTheme(choice.value)}
          />
          <span className="node-symbol"><Icon name={choice.icon} size={16} /></span>
          <span><strong>{choice.label}</strong><small>{choice.hint}</small></span>
        </label>
      ))}
    </div>
  );
}

export function Button({ children, icon, tone = "default", className = "", type = "button", ...props }) {
  const accessibleLabel = typeof children === "string" ? children : undefined;
  return (
    <button type={type} className={`button button-${tone} ${className}`.trim()} aria-label={accessibleLabel} {...props}>
      {icon ? <Icon name={icon} size={16} /> : null}
      <span>{children}</span>
    </button>
  );
}

export const IconButton = forwardRef(function IconButton({ icon, label, className = "", type = "button", ...props }, ref) {
  return (
    <button ref={ref} type={type} className={`icon-button ${className}`.trim()} aria-label={label} title={label} {...props}>
      <Icon name={icon} size={17} />
    </button>
  );
});

export function Badge({ children, tone = "neutral", dot = false }) {
  return (
    <span className={`badge badge-${tone}`}>
      {dot ? <i aria-hidden="true" /> : null}
      {children}
    </span>
  );
}

export function StatusBadge({ status }) {
  return <Badge tone={STATUS_TONE[status] ?? "neutral"} dot>{titleCase(status)}</Badge>;
}

export function Field({ label, hint, error, required, children, className = "" }) {
  return (
    <label className={`field ${className}`.trim()}>
      <span className="field-label">
        {label}{required ? <b aria-hidden="true">*</b> : null}
      </span>
      {children}
      {hint ? <small className="field-hint">{hint}</small> : null}
      {error ? <small className="field-error">{error}</small> : null}
    </label>
  );
}

export function JsonField({ label, value, onChange, rows = 8, hint, readOnly = false }) {
  const id = useId();
  let invalid = false;
  try { JSON.parse(value); } catch { invalid = true; }
  return (
    <label className={`field json-field ${invalid ? "is-invalid" : ""}`} htmlFor={id}>
      <span className="field-label"><Icon name="code" size={14} />{label}</span>
      <textarea
        id={id}
        rows={rows}
        value={value}
        onChange={(event) => onChange?.(event.target.value)}
        spellCheck="false"
        readOnly={readOnly}
      />
      <small className={invalid ? "field-error" : "field-hint"}>
        {invalid ? "Invalid JSON — this cannot be published." : hint}
      </small>
    </label>
  );
}

export function Modal({ title, description, onClose, children, width = "680px" }) {
  const closeRef = useRef(null);
  const dialogRef = useRef(null);
  const onCloseRef = useRef(onClose);
  const titleId = useId();
  const descriptionId = useId();
  useEffect(() => { onCloseRef.current = onClose; }, [onClose]);
  useEffect(() => {
    const previous = document.activeElement;
    closeRef.current?.focus();
    const onKey = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab" || !dialogRef.current) return;
      const focusable = [...dialogRef.current.querySelectorAll(
        'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
      )].filter((element) => !element.hidden && element.getClientRects().length);
      if (!focusable.length) {
        event.preventDefault();
        dialogRef.current.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable.at(-1);
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      if (previous instanceof HTMLElement && previous.isConnected) previous.focus();
    };
  }, []);
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose();
    }}>
      <section ref={dialogRef} tabIndex="-1" className="modal" role="dialog" aria-modal="true" aria-labelledby={titleId} aria-describedby={description ? descriptionId : undefined} style={{ "--modal-width": width }}>
        <header className="modal-header">
          <div>
            <h2 id={titleId}>{title}</h2>
            {description ? <p id={descriptionId}>{description}</p> : null}
          </div>
          <IconButton ref={closeRef} icon="close" label="Close dialog" onClick={onClose} />
        </header>
        <div className="modal-body">{children}</div>
      </section>
    </div>
  );
}

export function EmptyState({ icon = "flow", title, description, action }) {
  return (
    <div className="empty-state">
      <span className="empty-icon"><Icon name={icon} size={24} /></span>
      <h3>{title}</h3>
      <p>{description}</p>
      {action}
    </div>
  );
}

export function Segmented({ value, onChange, items, label }) {
  return (
    <div className="segmented" role="tablist" aria-label={label}>
      {items.map((item) => (
        <button key={item.value} type="button" role="tab" aria-selected={value === item.value} className={value === item.value ? "is-active" : ""} onClick={() => onChange(item.value)}>
          {item.label}
          {item.count !== undefined ? <span>{item.count}</span> : null}
        </button>
      ))}
    </div>
  );
}

export function DefinitionList({ items }) {
  return (
    <dl className="definition-list">
      {items.map(([term, value]) => <div key={term}><dt>{term}</dt><dd>{value ?? "—"}</dd></div>)}
    </dl>
  );
}

export function KeyValue({ data }) {
  return <pre className="code-block">{JSON.stringify(data, null, 2)}</pre>;
}

/** Run citations rendered as links that select the cited Run.
 *
 * Shared because a citation means the same thing wherever it appears: the
 * evidence is a real Run you can open, not a quoted identifier. The brake
 * refusal, the dead-end panel, the publish advisory and the principles panel
 * all cite Runs, so they cite them identically.
 */
export function CitedRuns({ label, ids, currentRunId, onSelectRun }) {
  const headingId = useId();
  return (
    <div className="dead-end-citations">
      <p className="panel-kicker" id={headingId}>{label}</p>
      <ul aria-labelledby={headingId}>
        {ids.map((id) => (
          <li key={id}>
            {onSelectRun ? (
              <button type="button" onClick={() => onSelectRun(id)} aria-label={`Open citing Run ${shortId(id, 14)}`} aria-current={id === currentRunId ? "true" : undefined}>
                <Icon name="run" size={12} /><code>{shortId(id, 14)}</code>
              </button>
            ) : <code>{shortId(id, 14)}</code>}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function Spinner({ label = "Working" }) {
  return <span className="spinner" role="status"><i aria-hidden="true" /><span>{label}</span></span>;
}

export function PageHeader({ eyebrow, title, description, actions }) {
  return (
    <header className="page-header">
      <div>
        {eyebrow ? <p className="eyebrow">{eyebrow}</p> : null}
        <h1>{title}</h1>
        {description ? <p>{description}</p> : null}
      </div>
      {actions ? <div className="page-actions">{actions}</div> : null}
    </header>
  );
}
