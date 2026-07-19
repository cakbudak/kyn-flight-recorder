"""SQLite schema for the flat standalone product projection."""

from __future__ import annotations


SCHEMA_SQL = r"""
CREATE TABLE IF NOT EXISTS workspaces (
    id TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    model_calls_used INTEGER NOT NULL DEFAULT 0 CHECK (model_calls_used >= 0)
);

CREATE TABLE IF NOT EXISTS prompts (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    slug TEXT NOT NULL,
    name TEXT NOT NULL,
    current_version INTEGER NOT NULL DEFAULT 1 CHECK (current_version >= 1),
    created_at TEXT NOT NULL,
    UNIQUE (workspace_id, slug)
);

CREATE TABLE IF NOT EXISTS prompt_versions (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    prompt_id TEXT NOT NULL REFERENCES prompts(id) ON DELETE RESTRICT,
    version INTEGER NOT NULL CHECK (version >= 1),
    template TEXT NOT NULL,
    variables_json TEXT NOT NULL,
    fingerprint TEXT NOT NULL CHECK (length(fingerprint) = 64),
    created_at TEXT NOT NULL,
    UNIQUE (prompt_id, version)
);

CREATE TABLE IF NOT EXISTS skills (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    slug TEXT NOT NULL,
    name TEXT NOT NULL,
    current_version INTEGER NOT NULL DEFAULT 1 CHECK (current_version >= 1),
    created_at TEXT NOT NULL,
    UNIQUE (workspace_id, slug)
);

CREATE TABLE IF NOT EXISTS skill_versions (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    skill_id TEXT NOT NULL REFERENCES skills(id) ON DELETE RESTRICT,
    version INTEGER NOT NULL CHECK (version >= 1),
    instructions TEXT NOT NULL,
    allowed_tools_json TEXT NOT NULL,
    fingerprint TEXT NOT NULL CHECK (length(fingerprint) = 64),
    created_at TEXT NOT NULL,
    UNIQUE (skill_id, version)
);

CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    slug TEXT NOT NULL,
    name TEXT NOT NULL,
    current_version INTEGER NOT NULL DEFAULT 1 CHECK (current_version >= 1),
    created_at TEXT NOT NULL,
    UNIQUE (workspace_id, slug)
);

CREATE TABLE IF NOT EXISTS agent_versions (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
    version INTEGER NOT NULL CHECK (version >= 1),
    role TEXT NOT NULL CHECK (role IN ('executor', 'diagnostician', 'repairer')),
    model TEXT NOT NULL,
    instructions TEXT NOT NULL,
    prompt_version_id TEXT NOT NULL REFERENCES prompt_versions(id) ON DELETE RESTRICT,
    skill_version_ids_json TEXT NOT NULL,
    fingerprint TEXT NOT NULL CHECK (length(fingerprint) = 64),
    created_at TEXT NOT NULL,
    UNIQUE (agent_id, version)
);

CREATE TABLE IF NOT EXISTS flows (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    slug TEXT NOT NULL,
    name TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    current_version INTEGER NOT NULL DEFAULT 1 CHECK (current_version >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (workspace_id, slug)
);

CREATE TABLE IF NOT EXISTS flow_versions (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    flow_id TEXT NOT NULL REFERENCES flows(id) ON DELETE RESTRICT,
    version INTEGER NOT NULL CHECK (version >= 1),
    executor_agent_version_id TEXT NOT NULL REFERENCES agent_versions(id) ON DELETE RESTRICT,
    diagnostician_agent_version_id TEXT NOT NULL REFERENCES agent_versions(id) ON DELETE RESTRICT,
    repairer_agent_version_id TEXT NOT NULL REFERENCES agent_versions(id) ON DELETE RESTRICT,
    request_json TEXT NOT NULL,
    policy_json TEXT NOT NULL,
    repair_policy_json TEXT NOT NULL,
    fingerprint TEXT NOT NULL CHECK (length(fingerprint) = 64),
    parent_version_id TEXT REFERENCES flow_versions(id) ON DELETE RESTRICT,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (flow_id, version)
);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    flow_id TEXT NOT NULL REFERENCES flows(id) ON DELETE RESTRICT,
    flow_version_id TEXT NOT NULL REFERENCES flow_versions(id) ON DELETE RESTRICT,
    parent_run_id TEXT REFERENCES runs(id) ON DELETE RESTRICT,
    correlation_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running', 'blocked', 'completed', 'failed')),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    goal TEXT NOT NULL,
    requested_environment TEXT NOT NULL,
    error_code TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    occurred_at TEXT NOT NULL,
    type TEXT NOT NULL,
    actor_type TEXT NOT NULL CHECK (actor_type IN ('runtime', 'agent', 'tool', 'human')),
    actor_id TEXT,
    payload_json TEXT NOT NULL,
    prev_hash TEXT NOT NULL CHECK (length(prev_hash) = 64),
    event_hash TEXT NOT NULL CHECK (length(event_hash) = 64),
    UNIQUE (run_id, sequence)
);

CREATE TABLE IF NOT EXISTS model_calls (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
    agent_version_id TEXT NOT NULL REFERENCES agent_versions(id) ON DELETE RESTRICT,
    role TEXT NOT NULL CHECK (role IN ('executor', 'diagnostician', 'repairer')),
    provider_response_id TEXT NOT NULL,
    status TEXT NOT NULL,
    model TEXT NOT NULL,
    input_hash TEXT NOT NULL CHECK (length(input_hash) = 64),
    output_hash TEXT NOT NULL CHECK (length(output_hash) = 64),
    usage_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tool_receipts (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
    agent_version_id TEXT NOT NULL REFERENCES agent_versions(id) ON DELETE RESTRICT,
    event_id TEXT NOT NULL REFERENCES events(id) ON DELETE RESTRICT,
    call_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('succeeded', 'denied', 'failed')),
    error_code TEXT,
    result_json TEXT NOT NULL,
    effect_kind TEXT NOT NULL CHECK (effect_kind IN ('none', 'sandbox_release')),
    idempotency_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (run_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS diagnoses (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    run_id TEXT NOT NULL UNIQUE REFERENCES runs(id) ON DELETE RESTRICT,
    agent_version_id TEXT NOT NULL REFERENCES agent_versions(id) ON DELETE RESTRICT,
    model_call_id TEXT NOT NULL REFERENCES model_calls(id) ON DELETE RESTRICT,
    fault_class TEXT NOT NULL,
    summary TEXT NOT NULL,
    evidence_event_ids_json TEXT NOT NULL,
    confidence TEXT NOT NULL CHECK (confidence IN ('low', 'medium', 'high')),
    why_not_retry TEXT NOT NULL,
    repair_path TEXT NOT NULL,
    fingerprint TEXT NOT NULL CHECK (length(fingerprint) = 64),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS repairs (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    diagnosis_id TEXT NOT NULL UNIQUE REFERENCES diagnoses(id) ON DELETE RESTRICT,
    flow_id TEXT NOT NULL REFERENCES flows(id) ON DELETE RESTRICT,
    agent_version_id TEXT NOT NULL REFERENCES agent_versions(id) ON DELETE RESTRICT,
    model_call_id TEXT NOT NULL REFERENCES model_calls(id) ON DELETE RESTRICT,
    expected_flow_revision INTEGER NOT NULL CHECK (expected_flow_revision >= 1),
    patch_json TEXT NOT NULL,
    summary TEXT NOT NULL,
    risk TEXT NOT NULL CHECK (risk IN ('low', 'medium', 'high')),
    proposal_hash TEXT NOT NULL CHECK (length(proposal_hash) = 64),
    status TEXT NOT NULL CHECK (status IN ('proposed', 'applied')),
    applied_flow_version_id TEXT REFERENCES flow_versions(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    applied_at TEXT
);

CREATE TABLE IF NOT EXISTS repair_approvals (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    repair_id TEXT NOT NULL UNIQUE REFERENCES repairs(id) ON DELETE RESTRICT,
    proposal_hash TEXT NOT NULL CHECK (length(proposal_hash) = 64),
    expected_flow_revision INTEGER NOT NULL,
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    acknowledged INTEGER NOT NULL CHECK (acknowledged = 1),
    applied_flow_version_id TEXT NOT NULL REFERENCES flow_versions(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sandbox_releases (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
    flow_version_id TEXT NOT NULL REFERENCES flow_versions(id) ON DELETE RESTRICT,
    environment TEXT NOT NULL,
    artifact TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_events_run_sequence ON events(run_id, sequence);
CREATE INDEX IF NOT EXISTS ix_runs_workspace_created ON runs(workspace_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS ux_runs_one_child
ON runs(parent_run_id) WHERE parent_run_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_receipts_run ON tool_receipts(run_id, created_at);
CREATE INDEX IF NOT EXISTS ix_model_calls_run ON model_calls(run_id, created_at);

CREATE TRIGGER IF NOT EXISTS trg_prompt_versions_no_update
BEFORE UPDATE ON prompt_versions BEGIN SELECT RAISE(ABORT, 'prompt version is immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_prompt_versions_no_delete
BEFORE DELETE ON prompt_versions BEGIN SELECT RAISE(ABORT, 'prompt version is immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_skill_versions_no_update
BEFORE UPDATE ON skill_versions BEGIN SELECT RAISE(ABORT, 'skill version is immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_skill_versions_no_delete
BEFORE DELETE ON skill_versions BEGIN SELECT RAISE(ABORT, 'skill version is immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_agent_versions_no_update
BEFORE UPDATE ON agent_versions BEGIN SELECT RAISE(ABORT, 'agent version is immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_agent_versions_no_delete
BEFORE DELETE ON agent_versions BEGIN SELECT RAISE(ABORT, 'agent version is immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_flow_versions_no_update
BEFORE UPDATE ON flow_versions BEGIN SELECT RAISE(ABORT, 'flow version is immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_flow_versions_no_delete
BEFORE DELETE ON flow_versions BEGIN SELECT RAISE(ABORT, 'flow version is immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_events_no_update
BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_events_no_delete
BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_model_calls_no_update
BEFORE UPDATE ON model_calls BEGIN SELECT RAISE(ABORT, 'model calls are append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_model_calls_no_delete
BEFORE DELETE ON model_calls BEGIN SELECT RAISE(ABORT, 'model calls are append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_tool_receipts_no_update
BEFORE UPDATE ON tool_receipts BEGIN SELECT RAISE(ABORT, 'tool receipts are append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_tool_receipts_no_delete
BEFORE DELETE ON tool_receipts BEGIN SELECT RAISE(ABORT, 'tool receipts are append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_diagnoses_no_update
BEFORE UPDATE ON diagnoses BEGIN SELECT RAISE(ABORT, 'diagnoses are immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_diagnoses_no_delete
BEFORE DELETE ON diagnoses BEGIN SELECT RAISE(ABORT, 'diagnoses are immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_approvals_no_update
BEFORE UPDATE ON repair_approvals BEGIN SELECT RAISE(ABORT, 'repair approvals are append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_approvals_no_delete
BEFORE DELETE ON repair_approvals BEGIN SELECT RAISE(ABORT, 'repair approvals are append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_sandbox_releases_no_update
BEFORE UPDATE ON sandbox_releases BEGIN SELECT RAISE(ABORT, 'sandbox releases are append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_sandbox_releases_no_delete
BEFORE DELETE ON sandbox_releases BEGIN SELECT RAISE(ABORT, 'sandbox releases are append-only'); END;

CREATE TRIGGER IF NOT EXISTS trg_runs_terminal_absorbing
BEFORE UPDATE OF status ON runs
WHEN NEW.status <> OLD.status AND OLD.status IN ('blocked', 'completed', 'failed')
BEGIN SELECT RAISE(ABORT, 'terminal run status is absorbing'); END;

CREATE TRIGGER IF NOT EXISTS trg_runs_transition_shape
BEFORE UPDATE OF status ON runs
WHEN NEW.status <> OLD.status
AND OLD.status NOT IN ('blocked', 'completed', 'failed')
AND NOT (
    OLD.status = 'running' AND NEW.status IN ('blocked', 'completed', 'failed')
)
BEGIN SELECT RAISE(ABORT, 'illegal run status transition'); END;

CREATE TRIGGER IF NOT EXISTS trg_runs_revision_fence
BEFORE UPDATE OF status ON runs
WHEN NEW.status <> OLD.status AND NEW.revision <> OLD.revision + 1
BEGIN SELECT RAISE(ABORT, 'run transition must advance one revision'); END;

CREATE TRIGGER IF NOT EXISTS trg_flows_revision_fence
BEFORE UPDATE OF revision, current_version ON flows
WHEN NEW.revision <> OLD.revision + 1 OR NEW.current_version <> OLD.current_version + 1
BEGIN SELECT RAISE(ABORT, 'flow update must advance one revision and version'); END;

CREATE TRIGGER IF NOT EXISTS trg_repairs_transition
BEFORE UPDATE OF status ON repairs
WHEN NEW.status <> OLD.status AND NOT (OLD.status = 'proposed' AND NEW.status = 'applied')
BEGIN SELECT RAISE(ABORT, 'illegal repair status transition'); END;
"""


IMMUTABLE_TABLES = frozenset(
    {
        "prompt_versions",
        "skill_versions",
        "agent_versions",
        "flow_versions",
        "events",
        "model_calls",
        "tool_receipts",
        "diagnoses",
        "repair_approvals",
        "sandbox_releases",
    }
)
