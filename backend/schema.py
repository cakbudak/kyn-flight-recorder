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
    allowed_action_version_ids_json TEXT NOT NULL DEFAULT '[]',
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

CREATE TABLE IF NOT EXISTS actions (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    slug TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    current_version INTEGER NOT NULL DEFAULT 1 CHECK (current_version >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (workspace_id, slug)
);

CREATE TABLE IF NOT EXISTS action_versions (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    action_id TEXT NOT NULL REFERENCES actions(id) ON DELETE RESTRICT,
    version INTEGER NOT NULL CHECK (version >= 1),
    kind TEXT NOT NULL CHECK (kind IN ('ai', 'template', 'condition', 'approval', 'sandbox')),
    executor_kind TEXT,
    input_schema_json TEXT NOT NULL,
    output_schema_json TEXT NOT NULL,
    outcomes_json TEXT,
    config_json TEXT NOT NULL,
    agent_version_id TEXT REFERENCES agent_versions(id) ON DELETE RESTRICT,
    effect_level TEXT NOT NULL CHECK (effect_level IN ('none', 'model', 'approval', 'sandbox_write')),
    fingerprint TEXT NOT NULL CHECK (length(fingerprint) = 64),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (action_id, version)
);

CREATE TABLE IF NOT EXISTS automation_flows (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    slug TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    current_version INTEGER NOT NULL DEFAULT 1 CHECK (current_version >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (workspace_id, slug)
);

CREATE TABLE IF NOT EXISTS automation_flow_versions (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    flow_id TEXT NOT NULL REFERENCES automation_flows(id) ON DELETE RESTRICT,
    version INTEGER NOT NULL CHECK (version >= 1),
    input_schema_json TEXT NOT NULL,
    output_schema_json TEXT,
    outcomes_json TEXT,
    acceptance_criteria_json TEXT,
    judge_agent_version_id TEXT REFERENCES agent_versions(id) ON DELETE RESTRICT,
    start_node_id TEXT NOT NULL,
    nodes_json TEXT NOT NULL,
    routes_json TEXT NOT NULL,
    pinned_resources_json TEXT NOT NULL,
    requires_model INTEGER NOT NULL CHECK (requires_model IN (0, 1)),
    fingerprint TEXT NOT NULL CHECK (length(fingerprint) = 64),
    parent_version_id TEXT REFERENCES automation_flow_versions(id) ON DELETE RESTRICT,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (flow_id, version)
);

CREATE TABLE IF NOT EXISTS automation_runs (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    flow_id TEXT NOT NULL REFERENCES automation_flows(id) ON DELETE RESTRICT,
    flow_version_id TEXT NOT NULL REFERENCES automation_flow_versions(id) ON DELETE RESTRICT,
    parent_run_id TEXT REFERENCES automation_runs(id) ON DELETE RESTRICT,
    parent_step_id TEXT REFERENCES automation_run_steps(id) ON DELETE RESTRICT,
    relation_kind TEXT NOT NULL DEFAULT 'root' CHECK (
        relation_kind IN ('root', 'rerun', 'proof', 'subflow', 'comparison')
    ),
    correlation_id TEXT NOT NULL,
    idempotency_key TEXT,
    status TEXT NOT NULL CHECK (status IN (
        'created', 'running', 'waiting_approval', 'completed', 'blocked', 'failed', 'cancelled'
    )),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    input_json TEXT NOT NULL,
    output_json TEXT,
    outcome TEXT,
    current_node_id TEXT,
    error_code TEXT,
    error_message TEXT,
    model_override TEXT,
    comparison_id TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    UNIQUE (workspace_id, idempotency_key),
    -- The override is the one deliberate hole in "everything is pinned", so the
    -- storage layer itself refuses to let it appear anywhere but a comparison
    -- sibling, and refuses a sibling that is not attached to a comparison.
    CHECK (model_override IS NULL OR relation_kind = 'comparison'),
    CHECK ((model_override IS NULL) = (comparison_id IS NULL))
);

CREATE TABLE IF NOT EXISTS automation_run_steps (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    run_id TEXT NOT NULL REFERENCES automation_runs(id) ON DELETE RESTRICT,
    parent_step_id TEXT REFERENCES automation_run_steps(id) ON DELETE RESTRICT,
    member_id TEXT,
    node_id TEXT NOT NULL,
    node_type TEXT NOT NULL CHECK (node_type IN ('action', 'agent', 'flow', 'fan_out')),
    target_version_id TEXT NOT NULL,
    attempt INTEGER NOT NULL CHECK (attempt >= 1),
    status TEXT NOT NULL CHECK (status IN (
        'running', 'waiting_approval', 'completed', 'blocked', 'failed', 'skipped'
    )),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    input_json TEXT NOT NULL,
    output_json TEXT,
    route_outcome TEXT,
    error_code TEXT,
    error_message TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    CHECK ((parent_step_id IS NULL) = (member_id IS NULL)),
    CHECK (member_id IS NULL OR node_type <> 'fan_out')
);

CREATE TABLE IF NOT EXISTS automation_events (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    run_id TEXT NOT NULL REFERENCES automation_runs(id) ON DELETE RESTRICT,
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    occurred_at TEXT NOT NULL,
    type TEXT NOT NULL,
    actor_type TEXT NOT NULL CHECK (actor_type IN ('runtime', 'action', 'agent', 'human')),
    actor_id TEXT,
    payload_json TEXT NOT NULL,
    prev_hash TEXT NOT NULL CHECK (length(prev_hash) = 64),
    event_hash TEXT NOT NULL CHECK (length(event_hash) = 64),
    UNIQUE (run_id, sequence)
);

CREATE TABLE IF NOT EXISTS automation_model_calls (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    run_id TEXT NOT NULL REFERENCES automation_runs(id) ON DELETE RESTRICT,
    step_id TEXT NOT NULL REFERENCES automation_run_steps(id) ON DELETE RESTRICT,
    agent_version_id TEXT NOT NULL REFERENCES agent_versions(id) ON DELETE RESTRICT,
    provider_response_id TEXT NOT NULL,
    status TEXT NOT NULL,
    model TEXT NOT NULL,
    input_hash TEXT NOT NULL CHECK (length(input_hash) = 64),
    output_hash TEXT NOT NULL CHECK (length(output_hash) = 64),
    usage_json TEXT NOT NULL,
    request_id TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS automation_action_receipts (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    run_id TEXT NOT NULL REFERENCES automation_runs(id) ON DELETE RESTRICT,
    step_id TEXT NOT NULL REFERENCES automation_run_steps(id) ON DELETE RESTRICT,
    node_id TEXT NOT NULL,
    action_version_id TEXT NOT NULL REFERENCES action_versions(id) ON DELETE RESTRICT,
    attempt INTEGER NOT NULL CHECK (attempt >= 1),
    outcome TEXT NOT NULL CHECK (outcome IN ('succeeded', 'denied', 'failed', 'waiting_approval')),
    input_json TEXT NOT NULL,
    output_json TEXT NOT NULL,
    error_code TEXT,
    idempotency_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (run_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS automation_approval_requests (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    run_id TEXT NOT NULL REFERENCES automation_runs(id) ON DELETE RESTRICT,
    step_id TEXT NOT NULL UNIQUE REFERENCES automation_run_steps(id) ON DELETE RESTRICT,
    node_id TEXT NOT NULL,
    message TEXT NOT NULL,
    context_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS automation_approval_decisions (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    request_id TEXT NOT NULL UNIQUE REFERENCES automation_approval_requests(id) ON DELETE RESTRICT,
    approved INTEGER NOT NULL CHECK (approved IN (0, 1)),
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS automation_effects (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    run_id TEXT NOT NULL REFERENCES automation_runs(id) ON DELETE RESTRICT,
    step_id TEXT NOT NULL REFERENCES automation_run_steps(id) ON DELETE RESTRICT,
    action_version_id TEXT NOT NULL REFERENCES action_versions(id) ON DELETE RESTRICT,
    collection TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS automation_trigger_bindings (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    flow_id TEXT NOT NULL REFERENCES automation_flows(id) ON DELETE RESTRICT,
    flow_version_id TEXT NOT NULL REFERENCES automation_flow_versions(id) ON DELETE RESTRICT,
    name TEXT NOT NULL,
    trigger_type TEXT NOT NULL CHECK (trigger_type IN ('webhook', 'schedule')),
    config_json TEXT NOT NULL,
    token_hash TEXT UNIQUE,
    token_hint TEXT,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    next_fire_at TEXT,
    last_fired_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS automation_diagnoses (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    run_id TEXT NOT NULL UNIQUE REFERENCES automation_runs(id) ON DELETE RESTRICT,
    failed_step_id TEXT REFERENCES automation_run_steps(id) ON DELETE RESTRICT,
    action_version_id TEXT REFERENCES action_versions(id) ON DELETE RESTRICT,
    fault_class TEXT NOT NULL,
    root_cause TEXT NOT NULL,
    explanation TEXT NOT NULL,
    confidence_milli INTEGER NOT NULL CHECK (confidence_milli BETWEEN 0 AND 1000),
    evidence_event_ids_json TEXT NOT NULL,
    created_by_agent_version_id TEXT REFERENCES agent_versions(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS automation_repair_proposals (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    diagnosis_id TEXT NOT NULL UNIQUE REFERENCES automation_diagnoses(id) ON DELETE RESTRICT,
    flow_id TEXT NOT NULL REFERENCES automation_flows(id) ON DELETE RESTRICT,
    action_id TEXT NOT NULL REFERENCES actions(id) ON DELETE RESTRICT,
    expected_flow_revision INTEGER NOT NULL CHECK (expected_flow_revision >= 1),
    expected_action_version INTEGER NOT NULL CHECK (expected_action_version >= 1),
    patch_json TEXT NOT NULL,
    proposal_hash TEXT NOT NULL UNIQUE CHECK (length(proposal_hash) = 64),
    status TEXT NOT NULL CHECK (status IN ('proposed', 'applied')),
    applied_action_version_id TEXT REFERENCES action_versions(id) ON DELETE RESTRICT,
    applied_flow_version_id TEXT REFERENCES automation_flow_versions(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    applied_at TEXT
);

CREATE TABLE IF NOT EXISTS automation_repair_decisions (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    proposal_id TEXT NOT NULL UNIQUE REFERENCES automation_repair_proposals(id) ON DELETE RESTRICT,
    proposal_hash TEXT NOT NULL CHECK (length(proposal_hash) = 64),
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    acknowledged INTEGER NOT NULL CHECK (acknowledged = 1),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS automation_dead_end_evidence (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    fingerprint TEXT NOT NULL CHECK (length(fingerprint) = 64),
    run_id TEXT NOT NULL REFERENCES automation_runs(id) ON DELETE RESTRICT,
    flow_version_id TEXT NOT NULL REFERENCES automation_flow_versions(id) ON DELETE RESTRICT,
    node_id TEXT NOT NULL,
    error_code TEXT NOT NULL,
    normalized_detail TEXT NOT NULL,
    created_at TEXT NOT NULL,
    flow_id TEXT REFERENCES automation_flows(id) ON DELETE RESTRICT,
    executor_kind TEXT,
    policy_marker TEXT,
    UNIQUE (fingerprint, run_id)
);

-- Capability Forge. All four tables are append-only. A model call may produce
-- a quarantined candidate, code may qualify its provenance once, and a human
-- may decide it once. Only a promoted decision points at a normal immutable
-- Skill version; candidates themselves carry no authority columns at all.
CREATE TABLE IF NOT EXISTS skill_distillation_model_calls (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    source_run_id TEXT NOT NULL REFERENCES automation_runs(id) ON DELETE RESTRICT,
    source_step_id TEXT NOT NULL REFERENCES automation_run_steps(id) ON DELETE RESTRICT,
    source_model_call_id TEXT NOT NULL REFERENCES automation_model_calls(id) ON DELETE RESTRICT,
    distiller_agent_version_id TEXT NOT NULL REFERENCES agent_versions(id) ON DELETE RESTRICT,
    provider_response_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('completed', 'failed')),
    model TEXT NOT NULL,
    input_hash TEXT NOT NULL CHECK (length(input_hash) = 64),
    output_hash TEXT NOT NULL CHECK (length(output_hash) = 64),
    usage_json TEXT NOT NULL,
    request_id TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS skill_candidates (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    source_run_id TEXT NOT NULL REFERENCES automation_runs(id) ON DELETE RESTRICT,
    source_step_id TEXT NOT NULL REFERENCES automation_run_steps(id) ON DELETE RESTRICT,
    source_model_call_id TEXT NOT NULL REFERENCES automation_model_calls(id) ON DELETE RESTRICT,
    source_agent_version_id TEXT NOT NULL REFERENCES agent_versions(id) ON DELETE RESTRICT,
    distiller_agent_version_id TEXT NOT NULL REFERENCES agent_versions(id) ON DELETE RESTRICT,
    distillation_model_call_id TEXT NOT NULL UNIQUE
        REFERENCES skill_distillation_model_calls(id) ON DELETE RESTRICT,
    name TEXT NOT NULL,
    instructions TEXT NOT NULL,
    rationale TEXT NOT NULL,
    evidence_event_ids_json TEXT NOT NULL,
    source_snapshot_hash TEXT NOT NULL CHECK (length(source_snapshot_hash) = 64),
    fingerprint TEXT NOT NULL UNIQUE CHECK (length(fingerprint) = 64),
    created_at TEXT NOT NULL,
    CHECK (source_agent_version_id <> distiller_agent_version_id)
);

CREATE TABLE IF NOT EXISTS skill_candidate_qualifications (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    candidate_id TEXT NOT NULL UNIQUE REFERENCES skill_candidates(id) ON DELETE RESTRICT,
    passed INTEGER NOT NULL CHECK (passed IN (0, 1)),
    checks_json TEXT NOT NULL,
    observed_source_snapshot_hash TEXT NOT NULL
        CHECK (length(observed_source_snapshot_hash) = 64),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS skill_candidate_decisions (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    candidate_id TEXT NOT NULL UNIQUE REFERENCES skill_candidates(id) ON DELETE RESTRICT,
    qualification_id TEXT REFERENCES skill_candidate_qualifications(id) ON DELETE RESTRICT,
    decision TEXT NOT NULL CHECK (decision IN ('promoted', 'rejected')),
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    acknowledged INTEGER NOT NULL CHECK (acknowledged = 1),
    skill_version_id TEXT REFERENCES skill_versions(id) ON DELETE RESTRICT,
    candidate_fingerprint TEXT NOT NULL CHECK (length(candidate_fingerprint) = 64),
    created_at TEXT NOT NULL,
    CHECK (
        (decision = 'promoted' AND qualification_id IS NOT NULL AND skill_version_id IS NOT NULL)
        OR (decision = 'rejected' AND skill_version_id IS NULL)
    )
);

-- Bounded public context projection. Knowledge is user-imported text with
-- immutable versions and line-addressable passages; it is not the private
-- Kyn.ist graph. Memory is separately governed so model-written candidates can
-- never enter recall until code qualifies them and a human promotes the exact
-- fingerprint.
CREATE TABLE IF NOT EXISTS knowledge_sources (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    slug TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    current_version INTEGER NOT NULL DEFAULT 1 CHECK (current_version >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (workspace_id, slug)
);

CREATE TABLE IF NOT EXISTS knowledge_source_versions (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    source_id TEXT NOT NULL REFERENCES knowledge_sources(id) ON DELETE RESTRICT,
    version INTEGER NOT NULL CHECK (version >= 1),
    filename TEXT NOT NULL,
    media_type TEXT NOT NULL,
    content TEXT NOT NULL,
    byte_count INTEGER NOT NULL CHECK (byte_count >= 0 AND byte_count <= 262144),
    line_count INTEGER NOT NULL CHECK (line_count >= 1 AND line_count <= 10000),
    fingerprint TEXT NOT NULL CHECK (length(fingerprint) = 64),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (source_id, version)
);

CREATE TABLE IF NOT EXISTS knowledge_passages (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    source_version_id TEXT NOT NULL
        REFERENCES knowledge_source_versions(id) ON DELETE RESTRICT,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 1),
    line_start INTEGER NOT NULL CHECK (line_start >= 1),
    line_end INTEGER NOT NULL CHECK (line_end >= line_start),
    text TEXT NOT NULL,
    fingerprint TEXT NOT NULL CHECK (length(fingerprint) = 64),
    UNIQUE (source_version_id, ordinal)
);

CREATE TABLE IF NOT EXISTS memory_distillation_model_calls (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    source_run_id TEXT NOT NULL REFERENCES automation_runs(id) ON DELETE RESTRICT,
    distiller_agent_version_id TEXT NOT NULL
        REFERENCES agent_versions(id) ON DELETE RESTRICT,
    provider_response_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('completed', 'failed')),
    model TEXT NOT NULL,
    input_hash TEXT NOT NULL CHECK (length(input_hash) = 64),
    output_hash TEXT NOT NULL CHECK (length(output_hash) = 64),
    usage_json TEXT NOT NULL,
    request_id TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_candidates (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    source_run_id TEXT NOT NULL REFERENCES automation_runs(id) ON DELETE RESTRICT,
    distillation_model_call_id TEXT UNIQUE
        REFERENCES memory_distillation_model_calls(id) ON DELETE RESTRICT,
    author_kind TEXT NOT NULL CHECK (author_kind IN ('human', 'model')),
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    rationale TEXT NOT NULL,
    tags_json TEXT NOT NULL,
    evidence_event_ids_json TEXT NOT NULL,
    source_snapshot_hash TEXT NOT NULL CHECK (length(source_snapshot_hash) = 64),
    fingerprint TEXT NOT NULL UNIQUE CHECK (length(fingerprint) = 64),
    created_at TEXT NOT NULL,
    CHECK (
        (author_kind = 'human' AND distillation_model_call_id IS NULL) OR
        (author_kind = 'model' AND distillation_model_call_id IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS memory_candidate_qualifications (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    candidate_id TEXT NOT NULL UNIQUE REFERENCES memory_candidates(id) ON DELETE RESTRICT,
    passed INTEGER NOT NULL CHECK (passed IN (0, 1)),
    checks_json TEXT NOT NULL,
    observed_source_snapshot_hash TEXT NOT NULL CHECK (length(observed_source_snapshot_hash) = 64),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    slug TEXT NOT NULL,
    name TEXT NOT NULL,
    current_version INTEGER NOT NULL DEFAULT 1 CHECK (current_version >= 1),
    created_at TEXT NOT NULL,
    UNIQUE (workspace_id, slug)
);

CREATE TABLE IF NOT EXISTS memory_versions (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE RESTRICT,
    version INTEGER NOT NULL CHECK (version >= 1),
    source_candidate_id TEXT NOT NULL UNIQUE
        REFERENCES memory_candidates(id) ON DELETE RESTRICT,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    tags_json TEXT NOT NULL,
    source_run_id TEXT NOT NULL REFERENCES automation_runs(id) ON DELETE RESTRICT,
    evidence_event_ids_json TEXT NOT NULL,
    fingerprint TEXT NOT NULL CHECK (length(fingerprint) = 64),
    created_at TEXT NOT NULL,
    UNIQUE (memory_id, version)
);

CREATE TABLE IF NOT EXISTS memory_candidate_decisions (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    candidate_id TEXT NOT NULL UNIQUE REFERENCES memory_candidates(id) ON DELETE RESTRICT,
    qualification_id TEXT REFERENCES memory_candidate_qualifications(id) ON DELETE RESTRICT,
    decision TEXT NOT NULL CHECK (decision IN ('promoted', 'rejected')),
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    acknowledged INTEGER NOT NULL CHECK (acknowledged = 1),
    candidate_fingerprint TEXT NOT NULL CHECK (length(candidate_fingerprint) = 64),
    memory_version_id TEXT REFERENCES memory_versions(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    CHECK (
        (decision = 'promoted' AND qualification_id IS NOT NULL AND memory_version_id IS NOT NULL) OR
        (decision = 'rejected' AND memory_version_id IS NULL)
    )
);

CREATE TABLE IF NOT EXISTS memory_state_events (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE RESTRICT,
    state TEXT NOT NULL CHECK (state IN ('active', 'retired')),
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_events_run_sequence ON events(run_id, sequence);
CREATE INDEX IF NOT EXISTS ix_runs_workspace_created ON runs(workspace_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS ux_runs_one_child
ON runs(parent_run_id) WHERE parent_run_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_receipts_run ON tool_receipts(run_id, created_at);
CREATE INDEX IF NOT EXISTS ix_model_calls_run ON model_calls(run_id, created_at);
CREATE INDEX IF NOT EXISTS ix_actions_workspace_created ON actions(workspace_id, created_at);
CREATE INDEX IF NOT EXISTS ix_automation_flows_workspace_created
ON automation_flows(workspace_id, created_at);
CREATE INDEX IF NOT EXISTS ix_automation_runs_workspace_created
ON automation_runs(workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_automation_steps_run
ON automation_run_steps(run_id, started_at, id);
CREATE INDEX IF NOT EXISTS ix_automation_events_run_sequence
ON automation_events(run_id, sequence);
CREATE INDEX IF NOT EXISTS ix_automation_triggers_due
ON automation_trigger_bindings(enabled, trigger_type, next_fire_at);
CREATE INDEX IF NOT EXISTS ix_automation_dead_ends_path
ON automation_dead_end_evidence(workspace_id, flow_version_id, node_id, fingerprint);
CREATE INDEX IF NOT EXISTS ix_skill_candidates_workspace_created
ON skill_candidates(workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_knowledge_sources_workspace_created
ON knowledge_sources(workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_knowledge_passages_version_lines
ON knowledge_passages(source_version_id, line_start, line_end);
CREATE INDEX IF NOT EXISTS ix_memory_candidates_workspace_created
ON memory_candidates(workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_memory_versions_workspace_created
ON memory_versions(workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_memory_state_events_memory_created
ON memory_state_events(memory_id, created_at DESC);

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
CREATE TRIGGER IF NOT EXISTS trg_action_versions_no_update
BEFORE UPDATE ON action_versions BEGIN SELECT RAISE(ABORT, 'action version is immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_action_versions_no_delete
BEFORE DELETE ON action_versions BEGIN SELECT RAISE(ABORT, 'action version is immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_automation_flow_versions_no_update
BEFORE UPDATE ON automation_flow_versions BEGIN SELECT RAISE(ABORT, 'automation flow version is immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_automation_flow_versions_no_delete
BEFORE DELETE ON automation_flow_versions BEGIN SELECT RAISE(ABORT, 'automation flow version is immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_automation_events_no_update
BEFORE UPDATE ON automation_events BEGIN SELECT RAISE(ABORT, 'automation events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_automation_events_no_delete
BEFORE DELETE ON automation_events BEGIN SELECT RAISE(ABORT, 'automation events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_automation_model_calls_no_update
BEFORE UPDATE ON automation_model_calls BEGIN SELECT RAISE(ABORT, 'automation model calls are append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_automation_model_calls_no_delete
BEFORE DELETE ON automation_model_calls BEGIN SELECT RAISE(ABORT, 'automation model calls are append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_automation_receipts_no_update
BEFORE UPDATE ON automation_action_receipts BEGIN SELECT RAISE(ABORT, 'automation receipts are append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_automation_receipts_no_delete
BEFORE DELETE ON automation_action_receipts BEGIN SELECT RAISE(ABORT, 'automation receipts are append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_automation_approval_requests_no_update
BEFORE UPDATE ON automation_approval_requests BEGIN SELECT RAISE(ABORT, 'approval requests are append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_automation_approval_requests_no_delete
BEFORE DELETE ON automation_approval_requests BEGIN SELECT RAISE(ABORT, 'approval requests are append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_automation_approval_decisions_no_update
BEFORE UPDATE ON automation_approval_decisions BEGIN SELECT RAISE(ABORT, 'approval decisions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_automation_approval_decisions_no_delete
BEFORE DELETE ON automation_approval_decisions BEGIN SELECT RAISE(ABORT, 'approval decisions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_automation_effects_no_update
BEFORE UPDATE ON automation_effects BEGIN SELECT RAISE(ABORT, 'automation effects are append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_automation_effects_no_delete
BEFORE DELETE ON automation_effects BEGIN SELECT RAISE(ABORT, 'automation effects are append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_automation_diagnoses_no_update
BEFORE UPDATE ON automation_diagnoses BEGIN SELECT RAISE(ABORT, 'automation diagnoses are immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_automation_diagnoses_no_delete
BEFORE DELETE ON automation_diagnoses BEGIN SELECT RAISE(ABORT, 'automation diagnoses are immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_automation_repair_decisions_no_update
BEFORE UPDATE ON automation_repair_decisions BEGIN SELECT RAISE(ABORT, 'automation repair decisions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_automation_repair_decisions_no_delete
BEFORE DELETE ON automation_repair_decisions BEGIN SELECT RAISE(ABORT, 'automation repair decisions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_automation_dead_end_evidence_no_update
BEFORE UPDATE ON automation_dead_end_evidence BEGIN SELECT RAISE(ABORT, 'dead end evidence is append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_automation_dead_end_evidence_no_delete
BEFORE DELETE ON automation_dead_end_evidence BEGIN SELECT RAISE(ABORT, 'dead end evidence is append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_skill_distillation_calls_no_update
BEFORE UPDATE ON skill_distillation_model_calls BEGIN SELECT RAISE(ABORT, 'skill distillation calls are append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_skill_distillation_calls_no_delete
BEFORE DELETE ON skill_distillation_model_calls BEGIN SELECT RAISE(ABORT, 'skill distillation calls are append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_skill_candidates_no_update
BEFORE UPDATE ON skill_candidates BEGIN SELECT RAISE(ABORT, 'skill candidates are immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_skill_candidates_no_delete
BEFORE DELETE ON skill_candidates BEGIN SELECT RAISE(ABORT, 'skill candidates are immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_skill_candidates_independent_agent
BEFORE INSERT ON skill_candidates
WHEN (
    SELECT agent_id FROM agent_versions WHERE id = NEW.source_agent_version_id
) = (
    SELECT agent_id FROM agent_versions WHERE id = NEW.distiller_agent_version_id
)
BEGIN SELECT RAISE(ABORT, 'skill candidate distiller must be an independent agent'); END;
CREATE TRIGGER IF NOT EXISTS trg_skill_candidate_qualifications_no_update
BEFORE UPDATE ON skill_candidate_qualifications BEGIN SELECT RAISE(ABORT, 'skill candidate qualifications are append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_skill_candidate_qualifications_no_delete
BEFORE DELETE ON skill_candidate_qualifications BEGIN SELECT RAISE(ABORT, 'skill candidate qualifications are append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_skill_candidate_decisions_no_update
BEFORE UPDATE ON skill_candidate_decisions BEGIN SELECT RAISE(ABORT, 'skill candidate decisions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_skill_candidate_decisions_no_delete
BEFORE DELETE ON skill_candidate_decisions BEGIN SELECT RAISE(ABORT, 'skill candidate decisions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_knowledge_source_versions_no_update
BEFORE UPDATE ON knowledge_source_versions BEGIN SELECT RAISE(ABORT, 'knowledge source versions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_knowledge_source_versions_no_delete
BEFORE DELETE ON knowledge_source_versions BEGIN SELECT RAISE(ABORT, 'knowledge source versions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_knowledge_passages_no_update
BEFORE UPDATE ON knowledge_passages BEGIN SELECT RAISE(ABORT, 'knowledge passages are immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_knowledge_passages_no_delete
BEFORE DELETE ON knowledge_passages BEGIN SELECT RAISE(ABORT, 'knowledge passages are immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_knowledge_sources_version_fence
BEFORE UPDATE OF current_version ON knowledge_sources
WHEN NEW.current_version <> OLD.current_version + 1
BEGIN SELECT RAISE(ABORT, 'knowledge source update must advance one version'); END;
CREATE TRIGGER IF NOT EXISTS trg_memory_distillation_calls_no_update
BEFORE UPDATE ON memory_distillation_model_calls BEGIN SELECT RAISE(ABORT, 'memory distillation calls are append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_memory_distillation_calls_no_delete
BEFORE DELETE ON memory_distillation_model_calls BEGIN SELECT RAISE(ABORT, 'memory distillation calls are append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_memory_candidates_no_update
BEFORE UPDATE ON memory_candidates BEGIN SELECT RAISE(ABORT, 'memory candidates are immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_memory_candidates_no_delete
BEFORE DELETE ON memory_candidates BEGIN SELECT RAISE(ABORT, 'memory candidates are immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_memory_qualifications_no_update
BEFORE UPDATE ON memory_candidate_qualifications BEGIN SELECT RAISE(ABORT, 'memory qualifications are append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_memory_qualifications_no_delete
BEFORE DELETE ON memory_candidate_qualifications BEGIN SELECT RAISE(ABORT, 'memory qualifications are append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_memory_decisions_no_update
BEFORE UPDATE ON memory_candidate_decisions BEGIN SELECT RAISE(ABORT, 'memory decisions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_memory_decisions_no_delete
BEFORE DELETE ON memory_candidate_decisions BEGIN SELECT RAISE(ABORT, 'memory decisions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_memory_versions_no_update
BEFORE UPDATE ON memory_versions BEGIN SELECT RAISE(ABORT, 'memory versions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_memory_versions_no_delete
BEFORE DELETE ON memory_versions BEGIN SELECT RAISE(ABORT, 'memory versions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_memory_state_events_no_update
BEFORE UPDATE ON memory_state_events BEGIN SELECT RAISE(ABORT, 'memory state events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_memory_state_events_no_delete
BEFORE DELETE ON memory_state_events BEGIN SELECT RAISE(ABORT, 'memory state events are append-only'); END;

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

CREATE TRIGGER IF NOT EXISTS trg_automation_runs_terminal_absorbing
BEFORE UPDATE OF status ON automation_runs
WHEN NEW.status <> OLD.status AND OLD.status IN ('completed', 'blocked', 'failed', 'cancelled')
BEGIN SELECT RAISE(ABORT, 'terminal automation run status is absorbing'); END;

CREATE TRIGGER IF NOT EXISTS trg_automation_runs_transition_shape
BEFORE UPDATE OF status ON automation_runs
WHEN NEW.status <> OLD.status
AND NOT (
    (OLD.status = 'created' AND NEW.status IN ('running', 'cancelled')) OR
    (OLD.status = 'running' AND NEW.status IN ('waiting_approval', 'completed', 'blocked', 'failed', 'cancelled')) OR
    (OLD.status = 'waiting_approval' AND NEW.status IN ('running', 'blocked', 'cancelled'))
)
BEGIN SELECT RAISE(ABORT, 'illegal automation run status transition'); END;

CREATE TRIGGER IF NOT EXISTS trg_automation_runs_revision_fence
BEFORE UPDATE OF status ON automation_runs
WHEN NEW.status <> OLD.status AND NEW.revision <> OLD.revision + 1
BEGIN SELECT RAISE(ABORT, 'automation run transition must advance one revision'); END;

CREATE TRIGGER IF NOT EXISTS trg_automation_steps_transition_shape
BEFORE UPDATE OF status ON automation_run_steps
WHEN NEW.status <> OLD.status
AND NOT (
    (OLD.status = 'running' AND NEW.status IN ('waiting_approval', 'completed', 'blocked', 'failed', 'skipped')) OR
    (OLD.status = 'waiting_approval' AND NEW.status IN ('completed', 'blocked'))
)
BEGIN SELECT RAISE(ABORT, 'illegal automation step status transition'); END;

CREATE TRIGGER IF NOT EXISTS trg_automation_steps_revision_fence
BEFORE UPDATE OF status ON automation_run_steps
WHEN NEW.status <> OLD.status AND NEW.revision <> OLD.revision + 1
BEGIN SELECT RAISE(ABORT, 'automation step transition must advance one revision'); END;

CREATE TRIGGER IF NOT EXISTS trg_automation_flows_revision_fence
BEFORE UPDATE OF revision, current_version ON automation_flows
WHEN NEW.revision <> OLD.revision + 1 OR NEW.current_version <> OLD.current_version + 1
BEGIN SELECT RAISE(ABORT, 'automation flow update must advance one revision and version'); END;

CREATE TRIGGER IF NOT EXISTS trg_actions_version_fence
BEFORE UPDATE OF current_version ON actions
WHEN NEW.current_version <> OLD.current_version + 1
BEGIN SELECT RAISE(ABORT, 'action update must advance one version'); END;

CREATE TRIGGER IF NOT EXISTS trg_prompts_version_fence
BEFORE UPDATE OF current_version ON prompts
WHEN NEW.current_version <> OLD.current_version + 1
BEGIN SELECT RAISE(ABORT, 'prompt update must advance one version'); END;

CREATE TRIGGER IF NOT EXISTS trg_skills_version_fence
BEFORE UPDATE OF current_version ON skills
WHEN NEW.current_version <> OLD.current_version + 1
BEGIN SELECT RAISE(ABORT, 'skill update must advance one version'); END;

CREATE TRIGGER IF NOT EXISTS trg_agents_version_fence
BEFORE UPDATE OF current_version ON agents
WHEN NEW.current_version <> OLD.current_version + 1
BEGIN SELECT RAISE(ABORT, 'agent update must advance one version'); END;

CREATE TRIGGER IF NOT EXISTS trg_automation_repairs_transition
BEFORE UPDATE OF status ON automation_repair_proposals
WHEN NEW.status <> OLD.status AND NOT (OLD.status = 'proposed' AND NEW.status = 'applied')
BEGIN SELECT RAISE(ABORT, 'illegal automation repair transition'); END;
"""


# The structural-signature index is applied *after* the dead-end migration adds
# its columns, so it cannot live in SCHEMA_SQL: on a pre-existing database the
# columns do not exist yet when SCHEMA_SQL runs.
DEAD_END_STRUCTURE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS ix_automation_dead_ends_structure
ON automation_dead_end_evidence(workspace_id, executor_kind, error_code, policy_marker);
"""


# Comparison siblings are read back by grouping on `comparison_id`, which is a
# migrated column for the same reason: it does not exist when SCHEMA_SQL runs
# against an already deployed database.
RUN_COMPARISON_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS ix_automation_runs_comparison
ON automation_runs(workspace_id, comparison_id, created_at)
WHERE comparison_id IS NOT NULL;
"""


# The rebuilt `automation_runs` must carry byte-identical guards to the ones
# SCHEMA_SQL installs, because `DROP TABLE` takes a table's triggers with it and
# SCHEMA_SQL has already run by the time the migration executes. Sharing one
# constant is what keeps the migrated table and a fresh table the same table.
AUTOMATION_RUN_GUARDS_SQL = """
CREATE INDEX IF NOT EXISTS ix_automation_runs_workspace_created
ON automation_runs(workspace_id, created_at DESC);

CREATE TRIGGER IF NOT EXISTS trg_automation_runs_terminal_absorbing
BEFORE UPDATE OF status ON automation_runs
WHEN NEW.status <> OLD.status AND OLD.status IN ('completed', 'blocked', 'failed', 'cancelled')
BEGIN SELECT RAISE(ABORT, 'terminal automation run status is absorbing'); END;

CREATE TRIGGER IF NOT EXISTS trg_automation_runs_transition_shape
BEFORE UPDATE OF status ON automation_runs
WHEN NEW.status <> OLD.status
AND NOT (
    (OLD.status = 'created' AND NEW.status IN ('running', 'cancelled')) OR
    (OLD.status = 'running' AND NEW.status IN ('waiting_approval', 'completed', 'blocked', 'failed', 'cancelled')) OR
    (OLD.status = 'waiting_approval' AND NEW.status IN ('running', 'blocked', 'cancelled'))
)
BEGIN SELECT RAISE(ABORT, 'illegal automation run status transition'); END;

CREATE TRIGGER IF NOT EXISTS trg_automation_runs_revision_fence
BEFORE UPDATE OF status ON automation_runs
WHEN NEW.status <> OLD.status AND NEW.revision <> OLD.revision + 1
BEGIN SELECT RAISE(ABORT, 'automation run transition must advance one revision'); END;
"""


# Fan-out member Steps share their parent's graph node id and are distinguished
# by a pinned member id. Two partial indexes preserve the old uniqueness
# guarantee for ordinary Steps while making each member attempt independently
# fenced. The migration rebuild drops the old table's triggers, so one shared
# constant reinstalls the exact same guards for fresh and upgraded databases.
AUTOMATION_STEP_GUARDS_SQL = """
CREATE INDEX IF NOT EXISTS ix_automation_steps_run
ON automation_run_steps(run_id, started_at, id);

CREATE UNIQUE INDEX IF NOT EXISTS ux_automation_steps_graph_attempt
ON automation_run_steps(run_id, node_id, attempt)
WHERE member_id IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS ux_automation_steps_member_attempt
ON automation_run_steps(parent_step_id, member_id, attempt)
WHERE member_id IS NOT NULL;

CREATE TRIGGER IF NOT EXISTS trg_automation_steps_transition_shape
BEFORE UPDATE OF status ON automation_run_steps
WHEN NEW.status <> OLD.status
AND NOT (
    (OLD.status = 'running' AND NEW.status IN (
        'waiting_approval', 'completed', 'blocked', 'failed', 'skipped'
    )) OR
    (OLD.status = 'waiting_approval' AND NEW.status IN ('completed', 'blocked'))
)
BEGIN SELECT RAISE(ABORT, 'illegal automation step status transition'); END;

CREATE TRIGGER IF NOT EXISTS trg_automation_steps_revision_fence
BEFORE UPDATE OF status ON automation_run_steps
WHEN NEW.status <> OLD.status AND NEW.revision <> OLD.revision + 1
BEGIN SELECT RAISE(ABORT, 'automation step transition must advance one revision'); END;
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
        "action_versions",
        "automation_flow_versions",
        "automation_events",
        "automation_model_calls",
        "automation_action_receipts",
        "automation_approval_requests",
        "automation_approval_decisions",
        "automation_effects",
        "automation_diagnoses",
        "automation_repair_decisions",
        "automation_dead_end_evidence",
        "skill_distillation_model_calls",
        "skill_candidates",
        "skill_candidate_qualifications",
        "skill_candidate_decisions",
        "knowledge_source_versions",
        "knowledge_passages",
        "memory_distillation_model_calls",
        "memory_candidates",
        "memory_candidate_qualifications",
        "memory_candidate_decisions",
        "memory_versions",
        "memory_state_events",
    }
)
