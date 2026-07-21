"""Product control plane: the sole mutation API used by HTTP and tests."""

from __future__ import annotations

import json
import re
import threading
from typing import Any, Callable, Mapping, Sequence

from .contracts import (
    DEFAULT_APPROVAL_MESSAGE_CHARS,
    DEFAULT_STUDIO_OUTPUT_TOKENS,
    MAX_APPROVAL_MESSAGE_CHARS,
    MAX_STUDIO_OUTPUT_TOKENS,
    MIN_APPROVAL_MESSAGE_CHARS,
    MIN_STUDIO_OUTPUT_TOKENS,
    canonical_json,
    Conflict,
    ContractViolation,
    extract_output_text,
    NotFound,
    PLACEHOLDER_RE,
    ProviderFailure,
    fingerprint,
    new_id,
    normalize_acceptance_criteria,
    normalize_json_schema,
    normalize_judge_agent_version_id,
    normalize_outcomes,
    policy_marker,
    require_completed_response,
    require_slug,
    require_string,
    require_string_list,
    render_prompt,
    safe_response_summary,
    validate_json_schema,
)
from .context_store import ContextStore
from .model_comparison import build_comparison
from .runtime import AgentRuntime, ResponseTransport
from .skill_forge import build_distillation_payload, parse_candidate
from .store import Store
from .studio_runtime import (
    CALLABLE_ACTION_KINDS,
    JUDGE_PROMPT_VARIABLES,
    StudioRuntime,
    action_mints_effect,
    fan_out_outcomes,
    fan_out_output_schema,
    validate_acceptance_contract,
    validate_flow_definition,
)
from .studio_store import StudioStore
from .tools import ToolRegistry


SUPPORTED_MODELS = frozenset({"gpt-5.6", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"})
ROLE_NAMES = frozenset({"executor", "diagnostician", "repairer"})
HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
BOARDROOM_PARTICIPANT_OUTPUT_TOKENS = 2_000
BOARDROOM_EDITOR_OUTPUT_TOKENS = DEFAULT_STUDIO_OUTPUT_TOKENS


def _normalize_studio_output_tokens(
    value: Any,
    field: str,
    *,
    default: int,
) -> int:
    if value is None:
        return default
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not MIN_STUDIO_OUTPUT_TOKENS <= value <= MAX_STUDIO_OUTPUT_TOKENS
    ):
        raise ContractViolation(
            f"{field} must be between {MIN_STUDIO_OUTPUT_TOKENS} and "
            f"{MAX_STUDIO_OUTPUT_TOKENS}"
        )
    return value


def _normalize_approval_message_chars(value: Any) -> int:
    if value is None:
        return DEFAULT_APPROVAL_MESSAGE_CHARS
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not MIN_APPROVAL_MESSAGE_CHARS <= value <= MAX_APPROVAL_MESSAGE_CHARS
    ):
        raise ContractViolation(
            "approval max_message_chars must be between "
            f"{MIN_APPROVAL_MESSAGE_CHARS} and {MAX_APPROVAL_MESSAGE_CHARS}"
        )
    return value


BOARDROOM_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "brief": {"type": "string", "minLength": 1, "maxLength": 8_000},
        "context": {"type": "string", "maxLength": 12_000},
    },
    "required": ["brief", "context"],
    "additionalProperties": False,
}
BOARDROOM_PARTICIPANT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["commit", "challenge", "abstain"],
        },
        "analysis": {"type": "string", "minLength": 1, "maxLength": 3_000},
        "recommendations": {
            "type": "array",
            "items": {"type": "string", "maxLength": 500},
            "maxItems": 8,
        },
        "risks": {
            "type": "array",
            "items": {"type": "string", "maxLength": 500},
            "maxItems": 8,
        },
        "citations": {
            "type": "array",
            "items": {"type": "string", "maxLength": 200},
            "maxItems": 16,
        },
    },
    "required": ["verdict", "analysis", "recommendations", "risks", "citations"],
    "additionalProperties": False,
}
BOARDROOM_EDITOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "minLength": 1, "maxLength": 3_000},
        "consensus": {
            "type": "array",
            "items": {"type": "string", "maxLength": 500},
            "maxItems": 12,
        },
        "dissent": {
            "type": "array",
            "items": {"type": "string", "maxLength": 500},
            "maxItems": 12,
        },
        "open_questions": {
            "type": "array",
            "items": {"type": "string", "maxLength": 500},
            "maxItems": 12,
        },
        "citations": {
            "type": "array",
            "items": {"type": "string", "maxLength": 200},
            "maxItems": 20,
        },
    },
    "required": ["decision", "consensus", "dissent", "open_questions", "citations"],
    "additionalProperties": False,
}
BOARDROOM_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "enum": ["synthesized", "approved", "rejected", "written"],
        },
        **json.loads(canonical_json(BOARDROOM_EDITOR_SCHEMA["properties"])),
        "approval_reason": {"type": "string", "maxLength": 2_000},
        "effect_id": {"type": "string", "maxLength": 100},
        "collection": {"type": "string", "maxLength": 64},
    },
    "required": [
        "status",
        "decision",
        "consensus",
        "dissent",
        "open_questions",
        "citations",
        "approval_reason",
        "effect_id",
        "collection",
    ],
    "additionalProperties": False,
}

CONTEXT_TRANSFER_SCHEMA: dict[str, Any] = {
    "type": "string",
    "maxLength": 12_000,
}
CONTEXT_CITATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "source_id": {"type": "string"},
        "source_version_id": {"type": "string"},
        "source_version": {"type": "integer"},
        "source_name": {"type": "string"},
        "filename": {"type": "string"},
        "fingerprint": {"type": "string"},
        "line_start": {"type": "integer"},
        "line_end": {"type": "integer"},
        "label": {"type": "string"},
    },
    "required": [
        "source_id",
        "source_version_id",
        "source_version",
        "source_name",
        "filename",
        "fingerprint",
        "line_start",
        "line_end",
        "label",
    ],
    "additionalProperties": False,
}
CONTEXT_PASSAGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "text": {"type": "string", "maxLength": 20_000},
        "citation": CONTEXT_CITATION_SCHEMA,
    },
    "required": ["text", "citation"],
    "additionalProperties": False,
}
SMART_READ_SOURCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "version_id": {"type": "string"},
        "version": {"type": "integer"},
        "name": {"type": "string"},
        "filename": {"type": "string"},
        "media_type": {"type": "string"},
        "fingerprint": {"type": "string"},
        "line_count": {"type": "integer"},
        "byte_count": {"type": "integer"},
    },
    "required": [
        "id",
        "version_id",
        "version",
        "name",
        "filename",
        "media_type",
        "fingerprint",
        "line_count",
        "byte_count",
    ],
    "additionalProperties": False,
}


def _smart_read_action_output_schema(*, mode: str) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "mode": {"type": "string"},
        "source": SMART_READ_SOURCE_SCHEMA,
        "passages": {
            "type": "array",
            "items": CONTEXT_PASSAGE_SCHEMA,
            "maxItems": 100,
        },
        "result_fingerprint": {"type": "string"},
        "context": CONTEXT_TRANSFER_SCHEMA,
    }
    if mode == "glance":
        properties["headings"] = {
            "type": "array",
            "items": CONTEXT_PASSAGE_SCHEMA,
            "maxItems": 100,
        }
    if mode == "grep":
        properties["query"] = {"type": "string"}
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _search_action_output_schema(*, memory: bool) -> dict[str, Any]:
    if memory:
        item = {
            "type": "object",
            "properties": {
                "memory_id": {"type": "string"},
                "memory_version_id": {"type": "string"},
                "title": {"type": "string"},
                "content": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "score": {"type": "integer"},
                "matched_terms": {"type": "array", "items": {"type": "string"}},
                "fingerprint": {"type": "string"},
                "provenance": {
                    "type": "object",
                    "properties": {
                        "source_candidate_id": {"type": "string"},
                        "source_run_id": {"type": "string"},
                        "evidence_event_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": [
                        "source_candidate_id",
                        "source_run_id",
                        "evidence_event_ids",
                    ],
                    "additionalProperties": False,
                },
            },
            "required": [
                "memory_id",
                "memory_version_id",
                "title",
                "content",
                "tags",
                "score",
                "matched_terms",
                "fingerprint",
                "provenance",
            ],
            "additionalProperties": False,
        }
    else:
        item = {
            "type": "object",
            "properties": {
                "passage_id": {"type": "string"},
                "text": {"type": "string"},
                "score": {"type": "integer"},
                "matched_terms": {"type": "array", "items": {"type": "string"}},
                "citation": CONTEXT_CITATION_SCHEMA,
                "passage_fingerprint": {"type": "string"},
            },
            "required": [
                "passage_id",
                "text",
                "score",
                "matched_terms",
                "citation",
                "passage_fingerprint",
            ],
            "additionalProperties": False,
        }
    properties = {
        "query": {"type": "string"},
        "terms": {"type": "array", "items": {"type": "string"}},
        "results": {"type": "array", "items": item},
        "result_fingerprint": {"type": "string"},
        "context": CONTEXT_TRANSFER_SCHEMA,
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


class ControlPlane:
    def __init__(
        self,
        store: Store,
        client: ResponseTransport,
        *,
        default_model: str = "gpt-5.6",
        client_factory: Callable[[str], ResponseTransport] | None = None,
    ) -> None:
        if default_model not in SUPPORTED_MODELS:
            raise ContractViolation("default model is not supported")
        self.store = store
        self.client = client
        self.client_factory = client_factory
        self.default_model = default_model
        self.tools = ToolRegistry(store)
        self.runtime = AgentRuntime(store, client, self.tools)
        self.studio = StudioStore(store)
        self.context = ContextStore(store, self.studio)
        self.studio_runtime = StudioRuntime(self.studio, client, context=self.context)
        self._active_studio_runs: set[str] = set()
        self._active_studio_runs_lock = threading.Lock()
        self._studio_worker_slots = threading.BoundedSemaphore(2)

    def client_for_browser_key(self, api_key: str) -> ResponseTransport:
        if self.client_factory is None:
            # Deterministic unit/browser seams deliberately inject one shared client.
            return self.client
        return self.client_factory(api_key)

    def _runtime(self, client: ResponseTransport | None) -> AgentRuntime:
        if client is None or client is self.client:
            return self.runtime
        return AgentRuntime(self.store, client, self.tools)

    def _studio_runtime(self, client: ResponseTransport | None) -> StudioRuntime:
        if client is None or client is self.client:
            return self.studio_runtime
        return StudioRuntime(self.studio, client, context=self.context)

    def create_workspace(self, *, seed: bool = True) -> dict[str, Any]:
        workspace = self.store.create_workspace()
        if seed:
            self.store.seed_default_lab(workspace["id"], model=self.default_model)
            self.studio.seed_default(workspace["id"], model=self.default_model)
            self._seed_context_actions(workspace["id"])
            self._seed_contracted_flow(workspace["id"])
        return {
            "workspace_id": workspace["id"],
            "workspace_token": workspace["token"],
            "snapshot": self.snapshot(workspace["id"]),
        }

    def _seed_context_actions(self, workspace_id: str) -> None:
        """Publish the context primitives needed for a visible closed-loop Flow.

        The source version, line window, and recall query stay node mappings, not
        ambient config. The Flow canvas can therefore show exactly where context
        came from and how it was passed to the next capability.
        """

        focus_input = {
            "type": "object",
            "properties": {
                "source_version_id": {"type": "string"},
                "line_start": {"type": "integer", "minimum": 1},
                "line_end": {"type": "integer", "minimum": 1},
            },
            "required": ["source_version_id", "line_start", "line_end"],
            "additionalProperties": False,
        }
        search_input = {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 200},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 30},
            },
            "required": ["query", "max_results"],
            "additionalProperties": False,
        }
        source_input = {
            "type": "object",
            "properties": {"source_version_id": {"type": "string"}},
            "required": ["source_version_id"],
            "additionalProperties": False,
        }
        grep_input = {
            "type": "object",
            "properties": {
                "source_version_id": {"type": "string"},
                "query": {"type": "string", "minLength": 1, "maxLength": 200},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 30},
            },
            "required": ["source_version_id", "query", "max_results"],
            "additionalProperties": False,
        }
        read_descriptions = {
            "glance": "Reads the bounded opening and headings",
            "outline": "Reads only the source structure",
            "focus": "Reads one exact bounded line window",
            "grep": "Reads bounded literal matches with context",
            "full": "Reads the complete source inside the runtime size limit",
        }
        for mode in ("glance", "outline", "focus", "grep", "full"):
            self.create_action(
                workspace_id,
                name=f"SmartRead {mode}",
                slug=f"smartread-{mode}",
                description=(
                    f"{read_descriptions[mode]} from one immutable Knowledge version "
                    "and emits structured citations plus a directly mappable context envelope."
                ),
                kind="smart_read",
                input_schema=(
                    focus_input
                    if mode == "focus"
                    else grep_input
                    if mode == "grep"
                    else source_input
                ),
                output_schema=_smart_read_action_output_schema(mode=mode),
                config={"mode": mode},
                agent_version_id=None,
            )
        self.create_action(
            workspace_id,
            name="Current knowledge search",
            slug="current-knowledge-search",
            description=(
                "Deterministically ranks literal matches across current immutable "
                "Knowledge versions and emits cited transferable context."
            ),
            kind="knowledge_search",
            input_schema=search_input,
            output_schema=_search_action_output_schema(memory=False),
            config={},
            agent_version_id=None,
        )
        self.create_action(
            workspace_id,
            name="Governed Memory recall",
            slug="governed-memory-recall",
            description=(
                "Recalls only active Human-promoted Memory and carries exact candidate "
                "and source-Run provenance into the Flow."
            ),
            kind="memory_recall",
            input_schema=search_input,
            output_schema=_search_action_output_schema(memory=True),
            config={},
            agent_version_id=None,
        )
        self.create_action(
            workspace_id,
            name="Cited context handoff",
            slug="cited-context-handoff",
            description=(
                "Combines current source citations and governed Memory into one explicit "
                "downstream context field without model inference."
            ),
            kind="template",
            input_schema={
                "type": "object",
                "properties": {
                    "knowledge_context": CONTEXT_TRANSFER_SCHEMA,
                    "memory_context": CONTEXT_TRANSFER_SCHEMA,
                },
                "required": ["knowledge_context", "memory_context"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {"text": CONTEXT_TRANSFER_SCHEMA},
                "required": ["text"],
                "additionalProperties": False,
            },
            config={
                "template": (
                    "CURRENT IMMUTABLE SOURCE EVIDENCE\n{{knowledge_context}}\n\n"
                    "ACTIVE HUMAN-PROMOTED MEMORY\n{{memory_context}}"
                )
            },
            agent_version_id=None,
        )

    # The one seeded Flow that declares what finishing means, so the stop seam is
    # reachable in the shipped demo rather than only in the test suite.
    #
    # Deliberately branch-shaped, and the branch is the whole point. One pinned
    # version both refuses and admits, decided by nothing but the Run input: an
    # input below the readiness threshold routes away from the ledger, so the
    # declared evidence is never minted and the Run fails `completion_unevidenced`;
    # an input above it writes the record and the same version completes. The
    # refusal is therefore honest — the evidence genuinely does not exist — which
    # is also why a real Goal-Judge reaches it without being told to, and why the
    # fault is not ratifiable: it is a property of this Run's data, not of the
    # definition.
    #
    # Seeded through this class's own authoring API rather than by direct insert,
    # so the acceptance contract passes exactly the two publication guards a
    # user-authored Flow passes — including self-adjudication, which is why the
    # judge Agent below is cast by no node of this graph. Workspace creation
    # fails loudly if either guard ever refuses it.
    def _seed_contracted_flow(self, workspace_id: str) -> dict[str, Any]:
        record_property = {"type": "string", "minLength": 1, "maxLength": 2_000}
        readiness_property = {"type": "number", "minimum": 0, "maximum": 1}
        gate = self.create_action(
            workspace_id,
            name="Publication readiness gate",
            slug="publication-readiness-gate",
            description=(
                "Routes a submitted record by its declared readiness score, with no "
                "model in the decision."
            ),
            kind="condition",
            input_schema={
                "type": "object",
                "properties": {"readiness": dict(readiness_property)},
                "required": ["readiness"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "matched": {"type": "boolean"},
                    "actual": {"type": "number"},
                },
                "required": ["matched", "actual"],
                "additionalProperties": False,
            },
            config={"path": "readiness", "operator": "gte", "value": 0.75},
            agent_version_id=None,
        )
        ledger = self.create_action(
            workspace_id,
            name="Evidence ledger write",
            slug="evidence-ledger-write",
            description=(
                "Appends the submitted record to this workspace's published evidence "
                "ledger as one idempotent effect."
            ),
            kind="data_store",
            input_schema={
                "type": "object",
                "properties": {"record": dict(record_property)},
                "required": ["record"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "effect_id": {"type": "string"},
                    "collection": {"type": "string"},
                },
                "required": ["effect_id", "collection"],
                "additionalProperties": False,
            },
            config={
                "operation": "append_record",
                "collection": "published-evidence",
                "write_enabled": True,
            },
            agent_version_id=None,
        )
        hold = self.create_action(
            workspace_id,
            name="Hold for revision",
            slug="hold-for-revision",
            description=(
                "Returns the record unpublished when readiness is short, writing "
                "nothing anywhere."
            ),
            kind="template",
            input_schema={
                "type": "object",
                "properties": {"record": dict(record_property)},
                "required": ["record"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
                "additionalProperties": False,
            },
            config={
                "template": "Held for revision and published nowhere: {{record}}"
            },
            agent_version_id=None,
        )
        prompt = self.create_prompt(
            workspace_id,
            name="Goal-Judge adjudication prompt",
            slug="goal-judge-adjudication",
            template=(
                "Decide, for each declared acceptance criterion, whether this Run's "
                "own evidence shows the declared work was performed at a declared "
                "site. Anchor only supplied evidence ids. Prefer marking a criterion "
                "unevidenced over anchoring it to evidence that does not show the "
                "declared work."
            ),
            variables=[],
        )
        judge = self.create_agent(
            workspace_id,
            name="Completion Goal-Judge",
            slug="completion-goal-judge",
            role="executor",
            model=self.default_model,
            instructions=(
                "You adjudicate completion claims at the stop seam. A claim of being "
                "finished is evidence, never proof. Judge only against the Run "
                "evidence supplied to you."
            ),
            prompt_version_id=prompt["version"]["id"],
            skill_version_ids=[],
        )
        return self.create_studio_flow(
            workspace_id,
            name="Contracted evidence publication",
            slug="contracted-evidence-publication",
            description=(
                "Declares what finishing means: this Run may only report completed if "
                "the submitted record actually reached the evidence ledger."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "record": dict(record_property),
                    "readiness": dict(readiness_property),
                },
                "required": ["record", "readiness"],
                "additionalProperties": False,
            },
            acceptance_criteria=[
                {
                    "id": "record-in-ledger",
                    "statement": (
                        "The submitted record was written to the workspace evidence "
                        "ledger."
                    ),
                    "evidence_kind": "effect",
                    "node_ids": ["publish-to-ledger"],
                },
                {
                    "id": "ledger-write-succeeded",
                    "statement": (
                        "The ledger write reported success rather than a mere "
                        "attempt."
                    ),
                    "evidence_kind": "receipt",
                    "node_ids": ["publish-to-ledger"],
                },
            ],
            judge_agent_version_id=judge["version"]["id"],
            start_node_id="readiness-gate",
            nodes=[
                {
                    "id": "readiness-gate",
                    "type": "action",
                    "version_id": gate["version"]["id"],
                    "input_mapping": {
                        "readiness": {"source": "input", "path": "readiness"}
                    },
                },
                {
                    "id": "publish-to-ledger",
                    "type": "action",
                    "version_id": ledger["version"]["id"],
                    "input_mapping": {
                        "record": {"source": "input", "path": "record"}
                    },
                },
                {
                    "id": "hold-for-revision",
                    "type": "action",
                    "version_id": hold["version"]["id"],
                    "input_mapping": {
                        "record": {"source": "input", "path": "record"}
                    },
                },
            ],
            routes=[
                {"from": "readiness-gate", "to": "publish-to-ledger", "outcome": "true"},
                {
                    "from": "readiness-gate",
                    "to": "hold-for-revision",
                    "outcome": "false",
                },
            ],
        )

    def resolve_workspace(self, token: str) -> str:
        return self.store.resolve_workspace(token)

    def snapshot(self, workspace_id: str) -> dict[str, Any]:
        snapshot = self.store.workspace_snapshot(workspace_id)
        snapshot["studio"] = self.studio_snapshot(workspace_id)
        return snapshot

    @staticmethod
    def _expected_resource_version(value: Any, resource: str) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ContractViolation(f"expected {resource} version is invalid")
        return value

    def create_prompt(
        self,
        workspace_id: str,
        *,
        name: Any,
        slug: Any,
        template: Any,
        variables: Any,
    ) -> dict[str, Any]:
        normalized_name = require_string(name, "prompt name", maximum=100)
        normalized_slug = require_slug(slug)
        normalized_template = require_string(template, "prompt template", maximum=12_000)
        normalized_variables = require_string_list(
            variables,
            "prompt variables",
            maximum_items=12,
            maximum_item_length=48,
        )
        render_prompt(
            normalized_template,
            declared_variables=normalized_variables,
            values={variable: f"<{variable}>" for variable in normalized_variables},
        )
        return self.store.create_prompt(
            workspace_id,
            name=normalized_name,
            slug=normalized_slug,
            template=normalized_template,
            variables=normalized_variables,
        )

    def revise_prompt(
        self,
        workspace_id: str,
        prompt_id: str,
        *,
        expected_version: Any,
        name: Any,
        template: Any,
        variables: Any,
    ) -> dict[str, Any]:
        expected = self._expected_resource_version(expected_version, "Prompt")
        normalized_name = require_string(name, "prompt name", maximum=100)
        normalized_template = require_string(
            template, "prompt template", maximum=12_000
        )
        normalized_variables = require_string_list(
            variables,
            "prompt variables",
            maximum_items=12,
            maximum_item_length=48,
        )
        render_prompt(
            normalized_template,
            declared_variables=normalized_variables,
            values={variable: f"<{variable}>" for variable in normalized_variables},
        )
        return self.store.revise_prompt(
            workspace_id,
            prompt_id,
            expected_version=expected,
            name=normalized_name,
            template=normalized_template,
            variables=normalized_variables,
        )

    def create_skill(
        self,
        workspace_id: str,
        *,
        name: Any,
        slug: Any,
        instructions: Any,
        allowed_tools: Any,
        allowed_action_version_ids: Any = None,
    ) -> dict[str, Any]:
        normalized_name = require_string(name, "skill name", maximum=100)
        normalized_slug = require_slug(slug)
        normalized_instructions = require_string(
            instructions, "skill instructions", maximum=8_000
        )
        normalized_tools = require_string_list(
            allowed_tools,
            "allowed tools",
            maximum_items=8,
            maximum_item_length=64,
        )
        unknown = sorted(set(normalized_tools) - self.tools.known_names)
        if unknown:
            raise ContractViolation(f"unknown tool: {', '.join(unknown)}")
        normalized_actions = require_string_list(
            [] if allowed_action_version_ids is None else allowed_action_version_ids,
            "allowed Action version ids",
            maximum_items=12,
            maximum_item_length=80,
        )
        return self.store.create_skill(
            workspace_id,
            name=normalized_name,
            slug=normalized_slug,
            instructions=normalized_instructions,
            allowed_tools=normalized_tools,
            allowed_action_version_ids=normalized_actions,
        )

    def revise_skill(
        self,
        workspace_id: str,
        skill_id: str,
        *,
        expected_version: Any,
        name: Any,
        instructions: Any,
        allowed_tools: Any,
        allowed_action_version_ids: Any,
    ) -> dict[str, Any]:
        expected = self._expected_resource_version(expected_version, "Skill")
        normalized_tools = require_string_list(
            allowed_tools,
            "allowed tools",
            maximum_items=8,
            maximum_item_length=64,
        )
        unknown = sorted(set(normalized_tools) - self.tools.known_names)
        if unknown:
            raise ContractViolation(f"unknown tool: {', '.join(unknown)}")
        return self.store.revise_skill(
            workspace_id,
            skill_id,
            expected_version=expected,
            name=require_string(name, "skill name", maximum=100),
            instructions=require_string(
                instructions, "skill instructions", maximum=8_000
            ),
            allowed_tools=normalized_tools,
            allowed_action_version_ids=require_string_list(
                allowed_action_version_ids,
                "allowed Action version ids",
                maximum_items=12,
                maximum_item_length=80,
            ),
        )

    def create_action(
        self,
        workspace_id: str,
        *,
        name: Any,
        slug: Any,
        description: Any,
        kind: Any,
        input_schema: Any,
        output_schema: Any,
        config: Any,
        agent_version_id: Any,
        outcomes: Any = None,
        _action_id: str | None = None,
        _expected_version: int | None = None,
    ) -> dict[str, Any]:
        normalized_kind = require_string(kind, "Action kind", maximum=32)
        normalized_input = normalize_json_schema(input_schema, "Action input schema")
        normalized_output = normalize_json_schema(output_schema, "Action output schema")
        if normalized_input["type"] != "object" or normalized_output["type"] != "object":
            raise ContractViolation("Action input and output schemas must be objects")
        if not isinstance(config, dict) or len(json.dumps(config, default=str)) > 12_000:
            raise ContractViolation("Action config must be a bounded object")
        normalized_config = json.loads(json.dumps(config))
        normalized_agent = (
            None
            if agent_version_id is None
            else require_string(agent_version_id, "Action Agent version id", maximum=80)
        )
        effect_levels = {
            "ai": "model",
            "template": "none",
            "transform": "none",
            "delay": "none",
            "condition": "none",
            "router": "none",
            "assert": "none",
            "approval": "approval",
            "sandbox": "sandbox_write",
            "data_store": "sandbox_write",
            "smart_read": "none",
            "knowledge_search": "none",
            "memory_recall": "none",
        }
        storage_kinds = {
            "ai": "ai",
            "template": "template",
            "transform": "template",
            "delay": "template",
            "condition": "condition",
            "router": "condition",
            "assert": "condition",
            "approval": "approval",
            "sandbox": "sandbox",
            "data_store": "sandbox",
            "smart_read": "template",
            "knowledge_search": "template",
            "memory_recall": "template",
        }
        if normalized_kind not in effect_levels:
            raise ContractViolation("Action kind is not supported")
        normalized_outcomes = normalize_outcomes(
            outcomes,
            "Action outcomes",
            default_kind=normalized_kind,
        )
        outcome_ids = {item["id"] for item in normalized_outcomes}
        properties = set(normalized_input["properties"])
        if normalized_kind == "ai":
            required_config = {"max_tool_calls", "reasoning_effort"}
            optional_config = {"outcome_path", "max_output_tokens"}
            if (
                normalized_agent is None
                or not required_config.issubset(normalized_config)
                or not set(normalized_config).issubset(required_config | optional_config)
            ):
                raise ContractViolation("AI Action config or Agent pin is invalid")
            max_calls = normalized_config["max_tool_calls"]
            if not isinstance(max_calls, int) or isinstance(max_calls, bool) or not 0 <= max_calls <= 4:
                raise ContractViolation("AI Action max_tool_calls must be between zero and four")
            if normalized_config["reasoning_effort"] not in {"low", "medium", "high"}:
                raise ContractViolation("AI Action reasoning_effort is invalid")
            if "max_output_tokens" in normalized_config:
                normalized_config["max_output_tokens"] = _normalize_studio_output_tokens(
                    normalized_config["max_output_tokens"],
                    "AI Action max_output_tokens",
                    default=DEFAULT_STUDIO_OUTPUT_TOKENS,
                )
            agent = self.studio.get_agent_runtime(workspace_id, normalized_agent)
            if set(agent["prompt"]["variables"]) != properties:
                raise ContractViolation(
                    "AI Action input properties must exactly match its pinned Prompt variables"
                )
            if "outcome_path" in normalized_config:
                outcome_path = require_string(
                    normalized_config["outcome_path"],
                    "AI Action outcome path",
                    maximum=64,
                )
                if not re.fullmatch(r"[a-z][a-z0-9_]*", outcome_path):
                    raise ContractViolation("AI Action outcome path must be a top-level field")
                outcome_property = normalized_output["properties"].get(outcome_path)
                if (
                    not isinstance(outcome_property, dict)
                    or outcome_property.get("type") != "string"
                    or set(outcome_property.get("enum", [])) != (outcome_ids - {"error"})
                ):
                    raise ContractViolation(
                        "AI Action outcome field enum must exactly match declared non-error outcomes"
                    )
                normalized_config["outcome_path"] = outcome_path
            elif outcome_ids != {"success", "error"}:
                raise ContractViolation(
                    "AI Action custom outcomes require an outcome_path"
                )
        elif normalized_kind in {"smart_read", "knowledge_search", "memory_recall"}:
            if normalized_agent is not None:
                raise ContractViolation("context read Actions may not pin an Agent")
            input_properties = set(normalized_input["properties"])
            input_required = set(normalized_input["required"])
            output_properties = set(normalized_output["properties"])
            if normalized_kind == "smart_read":
                if set(normalized_config) != {"mode"}:
                    raise ContractViolation("SmartRead Action config is invalid")
                mode = normalized_config["mode"]
                if mode not in {"glance", "outline", "focus", "grep", "full"}:
                    raise ContractViolation("SmartRead Action mode is invalid")
                expected_input = {
                    "focus": {"source_version_id", "line_start", "line_end"},
                    "grep": {"source_version_id", "query", "max_results"},
                }.get(mode, {"source_version_id"})
                expected_output = {
                    "mode",
                    "source",
                    "passages",
                    "result_fingerprint",
                }
                if mode == "glance":
                    expected_output.add("headings")
                elif mode == "grep":
                    expected_output.add("query")
                if input_properties != expected_input or input_required != expected_input:
                    raise ContractViolation(
                        f"SmartRead {mode} input schema must require exactly "
                        + ", ".join(sorted(expected_input))
                    )
                if frozenset(output_properties) not in {
                    frozenset(expected_output),
                    frozenset({*expected_output, "context"}),
                }:
                    raise ContractViolation(
                        f"SmartRead {mode} output schema has the wrong top-level fields"
                    )
                if normalized_input["properties"]["source_version_id"]["type"] != "string":
                    raise ContractViolation("SmartRead source_version_id must be a string")
                if mode == "focus" and any(
                    normalized_input["properties"][name]["type"] != "integer"
                    for name in ("line_start", "line_end")
                ):
                    raise ContractViolation("SmartRead focus line fields must be integers")
                if mode == "grep" and (
                    normalized_input["properties"]["query"]["type"] != "string"
                    or normalized_input["properties"]["max_results"]["type"] != "integer"
                ):
                    raise ContractViolation("SmartRead grep query or max_results type is invalid")
            else:
                if normalized_config:
                    raise ContractViolation("context search Action config must be empty")
                expected_input = {"query", "max_results"}
                expected_output = {"query", "terms", "results", "result_fingerprint"}
                if input_properties != expected_input or input_required != expected_input:
                    raise ContractViolation(
                        "context search input schema must require query and max_results"
                    )
                if frozenset(output_properties) not in {
                    frozenset(expected_output),
                    frozenset({*expected_output, "context"}),
                }:
                    raise ContractViolation("context search output schema has the wrong top-level fields")
                if (
                    normalized_input["properties"]["query"]["type"] != "string"
                    or normalized_input["properties"]["max_results"]["type"] != "integer"
                ):
                    raise ContractViolation("context search input types are invalid")
            if "context" in output_properties and normalized_output["properties"][
                "context"
            ].get("type") != "string":
                raise ContractViolation("transferable context output must be a string")
        elif normalized_kind == "template":
            if normalized_agent is not None or set(normalized_config) != {"template"}:
                raise ContractViolation("template Action config is invalid")
            template = require_string(
                normalized_config["template"], "Action template", maximum=8_000
            )
            variables = set(PLACEHOLDER_RE.findall(template))
            if not variables or not variables.issubset(properties):
                raise ContractViolation("Action template variables must exist in its input schema")
            normalized_config["template"] = template
            if set(normalized_output["properties"]) != {"text"}:
                raise ContractViolation("template Action output must contain only text")
        elif normalized_kind == "transform":
            if normalized_agent is not None or set(normalized_config) != {
                "operation",
                "mappings",
            }:
                raise ContractViolation("transform Action config is invalid")
            if normalized_config["operation"] != "map":
                raise ContractViolation("transform Action operation is not supported")
            mappings = normalized_config["mappings"]
            if not isinstance(mappings, dict) or set(mappings) != set(normalized_output["properties"]):
                raise ContractViolation("transform mappings must define every output property")
            normalized_mappings: dict[str, dict[str, Any]] = {}
            for target, source in mappings.items():
                if not isinstance(source, dict) or source.get("source") not in {"input", "literal"}:
                    raise ContractViolation(f"transform mapping {target} is invalid")
                if source["source"] == "literal":
                    if set(source) != {"source", "value"}:
                        raise ContractViolation(f"transform literal mapping {target} is invalid")
                    normalized_mappings[target] = json.loads(json.dumps(source))
                else:
                    if set(source) != {"source", "path"}:
                        raise ContractViolation(f"transform input mapping {target} is invalid")
                    path = require_string(source["path"], f"transform path {target}", maximum=160)
                    if not re.fullmatch(r"[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)*", path):
                        raise ContractViolation(f"transform path {target} is invalid")
                    normalized_mappings[target] = {"source": "input", "path": path}
            normalized_config["mappings"] = normalized_mappings
        elif normalized_kind == "delay":
            if normalized_agent is not None or set(normalized_config) != {"milliseconds"}:
                raise ContractViolation("delay Action config is invalid")
            milliseconds = normalized_config["milliseconds"]
            if not isinstance(milliseconds, int) or isinstance(milliseconds, bool) or not 0 <= milliseconds <= 5_000:
                raise ContractViolation("delay Action milliseconds must be between zero and 5000")
            if normalized_input != normalized_output:
                raise ContractViolation("delay Action output schema must equal its input schema")
        elif normalized_kind == "condition":
            if normalized_agent is not None or set(normalized_config) != {
                "path",
                "operator",
                "value",
            }:
                raise ContractViolation("condition Action config is invalid")
            path = require_string(normalized_config["path"], "condition path", maximum=160)
            if not re.fullmatch(r"[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)*", path):
                raise ContractViolation("condition path is invalid")
            if normalized_config["operator"] not in {
                "equals",
                "not_equals",
                "contains",
                "gt",
                "gte",
                "lt",
                "lte",
            }:
                raise ContractViolation("condition operator is invalid")
            if outcome_ids != {"true", "false", "error"}:
                raise ContractViolation(
                    "condition Action outcomes must be true, false, and error"
                )
        elif normalized_kind == "router":
            if normalized_agent is not None or set(normalized_config) != {
                "branches",
                "fallback_outcome",
            }:
                raise ContractViolation("router Action config is invalid")
            branches = normalized_config["branches"]
            if not isinstance(branches, list) or not 1 <= len(branches) <= 10:
                raise ContractViolation("router Action must declare one to ten branches")
            normalized_branches: list[dict[str, Any]] = []
            branch_outcomes: set[str] = set()
            for index, branch in enumerate(branches):
                if not isinstance(branch, dict) or set(branch) != {
                    "outcome",
                    "path",
                    "operator",
                    "value",
                }:
                    raise ContractViolation(f"router branch {index} is invalid")
                branch_outcome = require_slug(
                    branch["outcome"], f"router branch {index} outcome"
                )
                if branch_outcome in branch_outcomes or branch_outcome not in outcome_ids:
                    raise ContractViolation("router branch outcomes must be unique and declared")
                branch_outcomes.add(branch_outcome)
                branch_path = require_string(
                    branch["path"], f"router branch {index} path", maximum=160
                )
                if not re.fullmatch(r"[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)*", branch_path):
                    raise ContractViolation(f"router branch {index} path is invalid")
                if branch["operator"] not in {
                    "equals",
                    "not_equals",
                    "contains",
                    "gt",
                    "gte",
                    "lt",
                    "lte",
                }:
                    raise ContractViolation(f"router branch {index} operator is invalid")
                normalized_branches.append(
                    {
                        "outcome": branch_outcome,
                        "path": branch_path,
                        "operator": branch["operator"],
                        "value": json.loads(json.dumps(branch["value"])),
                    }
                )
            fallback = require_slug(
                normalized_config["fallback_outcome"], "router fallback outcome"
            )
            if fallback not in outcome_ids or fallback in branch_outcomes or fallback == "error":
                raise ContractViolation("router fallback outcome must be a distinct declared outcome")
            normalized_config = {
                "branches": normalized_branches,
                "fallback_outcome": fallback,
            }
            if outcome_ids != branch_outcomes | {fallback, "error"}:
                raise ContractViolation(
                    "router outcomes must exactly match branches, fallback, and error"
                )
            if set(normalized_output["properties"]) != {"outcome", "actual"}:
                raise ContractViolation("router Action output must contain outcome and actual")
        elif normalized_kind == "assert":
            if normalized_agent is not None or set(normalized_config) != {
                "path",
                "operator",
                "value",
                "message",
            }:
                raise ContractViolation("assert Action config is invalid")
            path = require_string(normalized_config["path"], "assert path", maximum=160)
            if not re.fullmatch(r"[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)*", path):
                raise ContractViolation("assert path is invalid")
            if normalized_config["operator"] not in {
                "equals",
                "not_equals",
                "contains",
                "gt",
                "gte",
                "lt",
                "lte",
            }:
                raise ContractViolation("assert operator is invalid")
            normalized_config["message"] = require_string(
                normalized_config["message"], "assert message", maximum=500
            )
            if set(normalized_output["properties"]) != {"passed", "actual"}:
                raise ContractViolation("assert Action output must contain passed and actual")
        elif normalized_kind == "approval":
            if normalized_agent is not None or frozenset(normalized_config) not in {
                frozenset({"message_template"}),
                frozenset({"message_template", "max_message_chars"}),
            }:
                raise ContractViolation("approval Action config is invalid")
            template = require_string(
                normalized_config["message_template"],
                "approval message template",
                maximum=2_000,
            )
            variables = set(PLACEHOLDER_RE.findall(template))
            if not variables or not variables.issubset(properties):
                raise ContractViolation("approval message variables must exist in its input schema")
            normalized_config["message_template"] = template
            normalized_config["max_message_chars"] = _normalize_approval_message_chars(
                normalized_config.get("max_message_chars")
            )
            if outcome_ids != {"approved", "rejected", "error"}:
                raise ContractViolation(
                    "approval Action outcomes must be approved, rejected, and error"
                )
        else:
            allowed_keys = {"operation", "collection"}
            if normalized_kind == "data_store":
                allowed_keys.add("write_enabled")
            if normalized_agent is not None or frozenset(normalized_config) not in {
                frozenset({"operation", "collection"}),
                frozenset(allowed_keys),
            }:
                raise ContractViolation("Data Store Action config is invalid")
            if normalized_config["operation"] != "append_record":
                raise ContractViolation("Data Store Action operation is not supported")
            normalized_config["collection"] = require_slug(
                normalized_config["collection"], "sandbox collection"
            )
            if normalized_kind == "data_store":
                write_enabled = normalized_config.get("write_enabled", True)
                if not isinstance(write_enabled, bool):
                    raise ContractViolation("Data Store write_enabled must be a boolean")
                normalized_config["write_enabled"] = write_enabled
        if normalized_kind not in {"condition", "router", "approval", "ai"} and not {
            "success",
            "error",
        }.issubset(outcome_ids):
            raise ContractViolation("Action outcomes must include success and error")
        common = {
            "name": require_string(name, "Action name", maximum=100),
            "description": require_string(
                description, "Action description", maximum=500
            ),
            "kind": storage_kinds[normalized_kind],
            "input_schema": normalized_input,
            "output_schema": normalized_output,
            "outcomes": normalized_outcomes,
            "config": normalized_config,
            "agent_version_id": normalized_agent,
            "effect_level": effect_levels[normalized_kind],
            "executor_kind": (
                normalized_kind
                if storage_kinds[normalized_kind] != normalized_kind
                else None
            ),
        }
        if _action_id is not None:
            if _expected_version is None:
                raise ContractViolation("expected Action version is required")
            return self.studio.revise_action(
                workspace_id,
                _action_id,
                expected_version=_expected_version,
                **common,
            )
        return self.studio.create_action(
            workspace_id,
            slug=require_slug(slug),
            **common,
        )

    def revise_action(
        self,
        workspace_id: str,
        action_id: str,
        *,
        expected_version: Any,
        name: Any,
        description: Any,
        kind: Any,
        input_schema: Any,
        output_schema: Any,
        outcomes: Any,
        config: Any,
        agent_version_id: Any,
    ) -> dict[str, Any]:
        if (
            not isinstance(expected_version, int)
            or isinstance(expected_version, bool)
            or expected_version < 1
        ):
            raise ContractViolation("expected Action version is invalid")
        current = self.studio.get_action(workspace_id, action_id)
        return self.create_action(
            workspace_id,
            name=name,
            slug=current["slug"],
            description=description,
            kind=kind,
            input_schema=input_schema,
            output_schema=output_schema,
            outcomes=outcomes,
            config=config,
            agent_version_id=agent_version_id,
            _action_id=action_id,
            _expected_version=expected_version,
        )

    def create_studio_flow(
        self,
        workspace_id: str,
        *,
        name: Any,
        slug: Any,
        description: Any,
        input_schema: Any,
        output_schema: Any = None,
        outcomes: Any = None,
        acceptance_criteria: Any = None,
        judge_agent_version_id: Any = None,
        start_node_id: Any,
        nodes: Any,
        routes: Any,
    ) -> dict[str, Any]:
        normalized_schema = normalize_json_schema(input_schema, "Flow input schema")
        if normalized_schema["type"] != "object":
            raise ContractViolation("Flow input schema must be an object")
        normalized_outcomes = normalize_outcomes(
            outcomes, "Flow outcomes", default_kind="flow"
        )
        start, normalized_nodes, normalized_routes = validate_flow_definition(
            start_node_id=start_node_id,
            nodes=nodes,
            routes=routes,
        )
        contracts: dict[str, dict[str, Any]] = {}
        for node in normalized_nodes:
            contract = self._studio_node_contract(workspace_id, node)
            contracts[node["id"]] = contract
            expected = contract["input_schema"]
            mapped = set(node["input_mapping"])
            properties = set(expected["properties"])
            required = set(expected["required"])
            if not required.issubset(mapped) or not mapped.issubset(properties):
                raise ContractViolation(
                    f"Flow node {node['id']} mapping does not satisfy its pinned input contract"
                )
        self._validate_route_outcome_ownership(normalized_routes, contracts)
        normalized_criteria, normalized_judge = self._normalize_acceptance_contract(
            workspace_id,
            acceptance_criteria,
            judge_agent_version_id,
            nodes=normalized_nodes,
            contracts=contracts,
        )
        normalized_output = (
            normalize_json_schema(output_schema, "Flow output schema")
            if output_schema is not None
            else self._derive_flow_output_schema(
                normalized_nodes, normalized_routes, contracts
            )
        )
        if normalized_output["type"] != "object":
            raise ContractViolation("Flow output schema must be an object")
        published = self.studio.create_flow(
            workspace_id,
            name=require_string(name, "Flow name", maximum=100),
            slug=require_slug(slug),
            description=require_string(description, "Flow description", maximum=500),
            input_schema=normalized_schema,
            output_schema=normalized_output,
            outcomes=normalized_outcomes,
            start_node_id=start,
            nodes=normalized_nodes,
            routes=normalized_routes,
            acceptance_criteria=normalized_criteria,
            judge_agent_version_id=normalized_judge,
        )
        return {
            **published,
            "advisories": self._flow_advisories(workspace_id, contracts),
        }

    def revise_studio_flow(
        self,
        workspace_id: str,
        flow_id: str,
        *,
        expected_revision: Any,
        name: Any = None,
        description: Any = None,
        input_schema: Any,
        output_schema: Any = None,
        outcomes: Any = None,
        acceptance_criteria: Any = None,
        judge_agent_version_id: Any = None,
        start_node_id: Any,
        nodes: Any,
        routes: Any,
    ) -> dict[str, Any]:
        if (
            not isinstance(expected_revision, int)
            or isinstance(expected_revision, bool)
            or expected_revision < 1
        ):
            raise ContractViolation("expected Flow revision is invalid")
        normalized_schema = normalize_json_schema(input_schema, "Flow input schema")
        if normalized_schema["type"] != "object":
            raise ContractViolation("Flow input schema must be an object")
        current = self.studio.get_flow(workspace_id, flow_id)
        normalized_name = require_string(
            current["name"] if name is None else name,
            "Flow name",
            maximum=100,
        )
        normalized_description = require_string(
            current["description"] if description is None else description,
            "Flow description",
            maximum=500,
        )
        normalized_outcomes = normalize_outcomes(
            outcomes if outcomes is not None else current["version"]["outcomes"],
            "Flow outcomes",
            default_kind="flow",
        )
        start, normalized_nodes, normalized_routes = validate_flow_definition(
            start_node_id=start_node_id,
            nodes=nodes,
            routes=routes,
        )
        prior_fan_outs = {
            node["id"]: {member["id"] for member in node["members"]}
            for node in current["version"]["nodes"]
            if node["type"] == "fan_out"
        }
        for node in normalized_nodes:
            if node["type"] != "fan_out" or node["id"] not in prior_fan_outs:
                continue
            member_ids = {member["id"] for member in node["members"]}
            if member_ids != prior_fan_outs[node["id"]]:
                raise ContractViolation(
                    f"Flow fan-out {node['id']} member IDs are immutable after publication; "
                    "they are keys in downstream schemas. Replace pinned targets or publish "
                    "a new fan-out and compatible downstream Action contracts."
                )
        contracts: dict[str, dict[str, Any]] = {}
        for node in normalized_nodes:
            contract = self._studio_node_contract(workspace_id, node)
            contracts[node["id"]] = contract
            expected = contract["input_schema"]
            mapped = set(node["input_mapping"])
            properties = set(expected["properties"])
            required = set(expected["required"])
            if not required.issubset(mapped) or not mapped.issubset(properties):
                raise ContractViolation(
                    f"Flow node {node['id']} mapping does not satisfy its pinned input contract"
                )
        self._validate_route_outcome_ownership(normalized_routes, contracts)
        # An omitted acceptance contract carries forward with its judge, exactly
        # as `outcomes` does. Dropping it on silence would let a revision that
        # simply forgot the field quietly retire a safety contract; clearing it
        # stays expressible by declaring `[]`. If a carried-forward criterion
        # pins a node this revision removed, publication refuses — loudly, which
        # is the right way for a stale contract to surface.
        carried_forward = acceptance_criteria is None and judge_agent_version_id is None
        normalized_criteria, normalized_judge = self._normalize_acceptance_contract(
            workspace_id,
            current["version"]["acceptance_criteria"] if carried_forward else acceptance_criteria,
            (
                current["version"]["judge_agent_version_id"]
                if carried_forward
                else judge_agent_version_id
            ),
            nodes=normalized_nodes,
            contracts=contracts,
        )
        normalized_output = (
            normalize_json_schema(output_schema, "Flow output schema")
            if output_schema is not None
            else (
                current["version"]["output_schema"]
                or self._derive_flow_output_schema(
                    normalized_nodes, normalized_routes, contracts
                )
            )
        )
        if normalized_output["type"] != "object":
            raise ContractViolation("Flow output schema must be an object")
        revised = self.studio.revise_flow(
            workspace_id,
            flow_id,
            expected_revision=expected_revision,
            name=normalized_name,
            description=normalized_description,
            input_schema=normalized_schema,
            output_schema=normalized_output,
            outcomes=normalized_outcomes,
            start_node_id=start,
            nodes=normalized_nodes,
            routes=normalized_routes,
            acceptance_criteria=normalized_criteria,
            judge_agent_version_id=normalized_judge,
        )
        return {
            **revised,
            "advisories": self._flow_advisories(workspace_id, contracts),
        }

    def _studio_node_contract(
        self, workspace_id: str, node: dict[str, Any]
    ) -> dict[str, Any]:
        if node["type"] == "action":
            action = self.studio.get_action_version(workspace_id, node["version_id"])
            return {
                "input_schema": action["input_schema"],
                "output_schema": action["output_schema"],
                "outcomes": action["outcomes"],
                # The declared policy predicate a principle would recognise.
                "executor_kind": action["kind"],
                "policy_marker": policy_marker(action["kind"], action["config"]),
                # What this pinned Action can mint, for the acceptance guard:
                # whether it can ever write, and which Agent version it casts.
                "mints_effect": action_mints_effect(action["kind"], action["config"]),
                "mints_receipt": True,
                "agent_version_id": action["agent_version_id"],
            }
        if node["type"] == "agent":
            agent = self.studio.get_agent_runtime(workspace_id, node["version_id"])
            return {
                "input_schema": {
                    "type": "object",
                    "properties": {
                        variable: {"type": "string"}
                        for variable in agent["prompt"]["variables"]
                    },
                    "required": list(agent["prompt"]["variables"]),
                    "additionalProperties": False,
                },
                "output_schema": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                    "additionalProperties": False,
                },
                "outcomes": normalize_outcomes(
                    None, "Agent outcomes", default_kind="agent"
                ),
            }
        if node["type"] == "fan_out":
            members = node.get("members")
            barrier = node.get("barrier")
            if not isinstance(members, list) or not isinstance(barrier, dict):
                raise ContractViolation("fan-out node is missing its pinned composition")
            member_contracts: dict[str, dict[str, Any]] = {}
            cast_agents: set[str] = set()
            for member in members:
                member_contract = self._studio_node_contract(workspace_id, member)
                self._assert_fan_out_target_safe(
                    workspace_id, member, seen_flow_versions=set()
                )
                member_contracts[member["id"]] = member_contract
                if member["type"] == "agent":
                    cast_agents.add(str(member["version_id"]))
                pinned = member_contract.get("agent_version_id")
                if pinned:
                    cast_agents.add(str(pinned))
                cast_agents.update(
                    str(item) for item in member_contract.get("agent_version_ids", [])
                )
            input_schemas = [
                canonical_json(contract["input_schema"])
                for contract in member_contracts.values()
            ]
            if len(set(input_schemas)) != 1:
                raise ContractViolation(
                    "fan-out members must accept the identical mapped input contract"
                )
            for member_id, contract in member_contracts.items():
                verdict_schema = self._schema_at_path(
                    contract["output_schema"],
                    str(barrier["verdict_path"]),
                    field=f"fan-out member {member_id} verdict",
                )
                for affirmative in barrier["affirmative_values"]:
                    try:
                        validate_json_schema(
                            affirmative,
                            verdict_schema,
                            f"fan-out member {member_id} affirmative value",
                        )
                    except ContractViolation as error:
                        raise ContractViolation(
                            f"fan-out affirmative value is incompatible with member {member_id} verdict"
                        ) from error
            return {
                "input_schema": next(iter(member_contracts.values()))["input_schema"],
                "output_schema": fan_out_output_schema(member_contracts),
                "outcomes": fan_out_outcomes(str(barrier["mode"])),
                "mints_effect": False,
                "mints_receipt": any(
                    member["type"] == "action" for member in members
                ),
                "agent_version_ids": sorted(cast_agents),
            }
        flow = self.studio.get_flow_version_by_id(
            workspace_id, node["version_id"]
        )
        if flow["output_schema"] is None:
            raise ContractViolation(
                "Flow reuse requires a child version with an explicit output schema"
            )
        return {
            "input_schema": flow["input_schema"],
            "output_schema": flow["output_schema"],
            "outcomes": flow["outcomes"],
            "agent_version_ids": sorted(
                self.studio.flow_version_cast_agents(workspace_id, node["version_id"])
            ),
        }

    @staticmethod
    def _schema_at_path(
        schema: Mapping[str, Any], path: str, *, field: str
    ) -> Mapping[str, Any]:
        current: Mapping[str, Any] = schema
        for part in path.split("."):
            if current.get("type") != "object":
                raise ContractViolation(f"{field} path leaves an object contract")
            properties = current.get("properties")
            if not isinstance(properties, dict) or part not in properties:
                raise ContractViolation(f"{field} path is not declared by its output contract")
            current = properties[part]
        return current

    def _assert_fan_out_target_safe(
        self,
        workspace_id: str,
        target: Mapping[str, Any],
        *,
        seen_flow_versions: set[str],
    ) -> None:
        """Keep concurrent members read/model-only and bounded.

        Approval and effect Actions remain valid immediately downstream of the
        barrier. They are refused *inside* a fan-out because one paused member
        cannot safely suspend a shared barrier and concurrent writes would turn
        participant multiplicity into authority multiplicity.
        """

        target_type = target["type"]
        version_id = str(target["version_id"])
        if target_type == "agent":
            self.studio.get_agent_runtime(workspace_id, version_id)
            return
        if target_type == "action":
            action = self.studio.get_action_version(workspace_id, version_id)
            if action["kind"] == "approval" or action_mints_effect(
                action["kind"], action["config"]
            ):
                raise ContractViolation(
                    "fan-out members may not pause or mint effects; route approval and writes after the barrier"
                )
            if action["kind"] == "ai" and action["agent_version_id"]:
                agent = self.studio.get_agent_runtime(
                    workspace_id, action["agent_version_id"]
                )
                for granted_version_id in agent["effective_action_version_ids"]:
                    granted = self.studio.get_action_version(
                        workspace_id, granted_version_id
                    )
                    if granted["kind"] == "approval" or action_mints_effect(
                        granted["kind"], granted["config"]
                    ):
                        raise ContractViolation(
                            "fan-out AI member grants an Action that may pause or mint an effect"
                        )
            return
        if target_type != "flow":
            raise ContractViolation("fan-out member target type is invalid")
        if version_id in seen_flow_versions:
            raise ContractViolation("fan-out member Flow recursion is not supported")
        seen_flow_versions.add(version_id)
        flow = self.studio.get_flow_version_by_id(workspace_id, version_id)
        for node in flow["nodes"]:
            if node["type"] == "fan_out":
                raise ContractViolation(
                    "a fan-out member Flow may not contain another fan-out"
                )
            self._assert_fan_out_target_safe(
                workspace_id, node, seen_flow_versions=seen_flow_versions
            )
        seen_flow_versions.remove(version_id)


    def _normalize_acceptance_contract(
        self,
        workspace_id: str,
        acceptance_criteria: Any,
        judge_agent_version_id: Any,
        *,
        nodes: Sequence[Mapping[str, Any]],
        contracts: Mapping[str, Mapping[str, Any]],
    ) -> tuple[list[dict[str, str]], str | None]:
        """Normalize the declared acceptance contract and run its two guards.

        Called after the node contracts are resolved because both guards read
        the *pinned* graph: what a node's Action can actually mint, and which
        Agent versions the graph already casts.
        """

        criteria = normalize_acceptance_criteria(
            acceptance_criteria,
            "Flow acceptance criteria",
            node_ids={str(node["id"]) for node in nodes},
        )
        judge = normalize_judge_agent_version_id(
            judge_agent_version_id, "Flow judge Agent version", criteria=criteria
        )
        if judge is not None:
            try:
                judge_agent = self.studio.get_agent_runtime(workspace_id, judge)
            except NotFound as error:
                raise ContractViolation(
                    "Flow judge Agent version does not belong to the workspace"
                ) from error
            unsupported_variables = sorted(
                set(judge_agent["prompt"]["variables"]) - JUDGE_PROMPT_VARIABLES
            )
            if unsupported_variables:
                raise ContractViolation(
                    "Flow judge Agent Prompt declares unsupported variables: "
                    + ", ".join(unsupported_variables)
                )
        # Walked only when a judge is declared, so a Flow that declares no
        # contract pays nothing for a guarantee it never asked for.
        subflow_cast = (
            {
                str(node["id"]): self.studio.flow_version_cast_agents(
                    workspace_id, str(node["version_id"])
                )
                for node in nodes
                if node["type"] == "flow"
            }
            if judge is not None
            else {}
        )
        validate_acceptance_contract(
            acceptance_criteria=criteria,
            judge_agent_version_id=judge,
            nodes=nodes,
            node_contracts=contracts,
            subflow_cast=subflow_cast,
        )
        return criteria, judge

    def _flow_advisories(
        self, workspace_id: str, contracts: Mapping[str, Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        """Surface, at authoring time, every rule this draft's structure matches.

        This is the whole safety argument for principles: they appear here, where
        a wrong one costs a reader two seconds, and nowhere near the decision to
        admit a Run. Publishing always succeeds; only the brake ever refuses.
        """

        matched_nodes: dict[tuple[str, str], list[str]] = {}
        for node_id, contract in contracts.items():
            kind = contract.get("executor_kind")
            marker = contract.get("policy_marker")
            if not kind or not marker:
                continue
            matched_nodes.setdefault((str(kind), str(marker)), []).append(str(node_id))
        if not matched_nodes:
            return []
        advisories: list[dict[str, Any]] = []
        for principle in self.studio.list_principles(workspace_id):
            node_ids = matched_nodes.get(
                (str(principle["executor_kind"]), str(principle["policy_marker"]))
            )
            if not node_ids:
                continue
            advisories.append({**principle, "node_ids": sorted(node_ids)})
        return advisories

    @staticmethod
    def _validate_route_outcome_ownership(
        routes: list[dict[str, str]], contracts: dict[str, dict[str, Any]]
    ) -> None:
        for route in routes:
            owned = {item["id"] for item in contracts[route["from"]]["outcomes"]}
            if route["outcome"] not in owned:
                raise ContractViolation(
                    f"Flow route outcome {route['outcome']} is not declared by node {route['from']}"
                )

    @staticmethod
    def _derive_flow_output_schema(
        nodes: list[dict[str, Any]],
        routes: list[dict[str, str]],
        contracts: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        non_terminal = {route["from"] for route in routes}
        terminal_ids = [node["id"] for node in nodes if node["id"] not in non_terminal]
        terminal_schemas = [contracts[node_id]["output_schema"] for node_id in terminal_ids]
        if not terminal_schemas:
            raise ContractViolation("Flow must expose at least one terminal output")
        properties: dict[str, Any] = {}
        required = set(terminal_schemas[0].get("required", []))
        for schema in terminal_schemas:
            required &= set(schema.get("required", []))
            for name, definition in schema.get("properties", {}).items():
                current = properties.get(name)
                if current is not None and current != definition:
                    raise ContractViolation(
                        "Flow terminal output schemas conflict; declare a Flow output schema"
                    )
                properties[name] = definition
        return normalize_json_schema(
            {
                "type": "object",
                "properties": properties,
                "required": sorted(required),
                "additionalProperties": False,
            },
            "derived Flow output schema",
        )

    def start_studio_run(
        self,
        workspace_id: str,
        flow_id: str,
        *,
        input_data: Any,
        client: ResponseTransport | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(input_data, dict):
            raise ContractViolation("Run input must be an object")
        normalized_key = (
            require_string(idempotency_key, "Run idempotency key", maximum=100)
            if idempotency_key is not None
            else None
        )
        return self._studio_runtime(client).execute(
            workspace_id,
            flow_id,
            input_data=input_data,
            idempotency_key=normalized_key,
        )

    def prepare_studio_run(
        self,
        workspace_id: str,
        flow_id: str,
        *,
        input_data: Any,
        client: ResponseTransport | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(input_data, dict):
            raise ContractViolation("Run input must be an object")
        normalized_key = (
            require_string(idempotency_key, "Run idempotency key", maximum=100)
            if idempotency_key is not None
            else None
        )
        return self._studio_runtime(client).prepare(
            workspace_id,
            flow_id,
            input_data=input_data,
            idempotency_key=normalized_key,
        )

    def continue_studio_run(
        self,
        workspace_id: str,
        run_id: str,
        *,
        client: ResponseTransport | None = None,
    ) -> dict[str, Any]:
        return self._studio_runtime(client).continue_run(workspace_id, run_id)

    def enqueue_studio_run(
        self,
        workspace_id: str,
        flow_id: str,
        *,
        input_data: Any,
        client: ResponseTransport | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        runtime = self._studio_runtime(client)
        run = self.prepare_studio_run(
            workspace_id,
            flow_id,
            input_data=input_data,
            client=client,
            idempotency_key=idempotency_key,
        )
        return self.enqueue_existing_studio_run(
            workspace_id, run["id"], client=client, runtime=runtime
        )

    def enqueue_existing_studio_run(
        self,
        workspace_id: str,
        run_id: str,
        *,
        client: ResponseTransport | None = None,
        runtime: StudioRuntime | None = None,
    ) -> dict[str, Any]:
        runtime = runtime or self._studio_runtime(client)
        run = self.studio.get_run(workspace_id, run_id)
        if run["status"] not in {"created", "running"}:
            return run
        with self._active_studio_runs_lock:
            if run["id"] in self._active_studio_runs:
                return run
            self._active_studio_runs.add(run["id"])
        worker = threading.Thread(
            target=self._drive_studio_run_background,
            args=(runtime, workspace_id, run["id"]),
            name=f"kyn-run-{run['id'][-8:]}",
            daemon=True,
        )
        worker.start()
        return self.studio.get_run(workspace_id, run["id"])

    def _drive_studio_run_background(
        self, runtime: StudioRuntime, workspace_id: str, run_id: str
    ) -> None:
        try:
            with self._studio_worker_slots:
                runtime.continue_run(workspace_id, run_id)
        except Exception:
            # Unknown worker failures become bounded evidence, never a silent dead Run.
            try:
                run = self.studio.get_run(workspace_id, run_id)
                if run["status"] in {"created", "running"}:
                    self.studio.append_event(
                        workspace_id,
                        run_id,
                        event_type="run.worker_failed",
                        actor_type="runtime",
                        actor_id=None,
                        payload={"error_code": "worker_failure"},
                    )
                    self.studio.transition_run(
                        workspace_id,
                        run_id,
                        status="failed",
                        current_node_id=None,
                        error_code="worker_failure",
                        error_message="The bounded Run worker failed unexpectedly.",
                    )
            except Exception:
                pass
        finally:
            with self._active_studio_runs_lock:
                self._active_studio_runs.discard(run_id)

    def decide_studio_approval(
        self,
        workspace_id: str,
        request_id: str,
        *,
        approved: Any,
        actor: Any,
        reason: Any,
        client: ResponseTransport | None = None,
    ) -> dict[str, Any]:
        if not isinstance(approved, bool):
            raise ContractViolation("approval decision must be a boolean")
        run_id = self.studio.decide_approval(
            workspace_id,
            request_id,
            approved=approved,
            actor=require_string(actor, "approval actor", maximum=100),
            reason=require_string(reason, "approval reason", minimum=12, maximum=500),
        )
        runtime = self._studio_runtime(client)
        result = runtime.resume_after_approval(workspace_id, run_id)
        cursor = result
        while cursor["relation_kind"] == "subflow" and cursor["status"] in {
            "completed",
            "blocked",
            "failed",
            "cancelled",
        }:
            parent = runtime.resume_parent_from_subflow(workspace_id, cursor["id"])
            if parent is None:
                break
            cursor = parent
        return result

    def rerun_studio_run(
        self,
        workspace_id: str,
        run_id: str,
        *,
        input_data: Any,
        idempotency_key: Any,
        client: ResponseTransport | None = None,
    ) -> dict[str, Any]:
        parent = self.studio.get_run(workspace_id, run_id)
        if parent["status"] not in {"completed", "blocked", "failed", "cancelled"}:
            raise ContractViolation("only a terminal Run can be rerun")
        if not isinstance(input_data, dict):
            raise ContractViolation("rerun input must be an object")
        key = require_string(idempotency_key, "rerun idempotency key", maximum=100)
        return self._studio_runtime(client).execute(
            workspace_id,
            parent["flow_id"],
            input_data=input_data,
            flow_version=int(parent["flow_version"]),
            parent_run_id=parent["id"],
            relation_kind="rerun",
            correlation_id=parent["correlation_id"],
            idempotency_key=f"rerun:{parent['id']}:{key}",
        )

    def get_studio_run(self, workspace_id: str, run_id: str) -> dict[str, Any]:
        return self.studio.get_run(workspace_id, run_id)

    # -- controlled model comparison ---------------------------------------

    def compare_studio_models(
        self,
        workspace_id: str,
        flow_id: str,
        *,
        input_data: Any,
        models: Any,
        repetitions: Any = 1,
        client: ResponseTransport | None = None,
    ) -> dict[str, Any]:
        """Run one pinned Flow version against N models and derive the sweep.

        This is the only path that may set a model override, which is the single
        deliberate hole in "everything about a Run is pinned". Every sibling pins
        the *same* immutable Flow version, so every Action, Agent, Prompt, Skill
        and schema in the graph is byte-identical and the only recorded delta is
        the model. That pinning is what makes this a controlled experiment rather
        than a table of numbers.

        Refusal is all-or-nothing on purpose. Every precondition — supported
        models, at least two distinct ones, a Flow that actually calls a model,
        and the whole forecast against the workspace budget — is checked before
        the first Run row exists, so a refused comparison leaves no partial
        evidence for someone to read as a result.
        """

        if not isinstance(input_data, dict):
            raise ContractViolation("comparison input must be an object")
        requested = require_string_list(
            models, "comparison models", maximum_items=6, maximum_item_length=64
        )
        distinct = list(dict.fromkeys(requested))
        if len(distinct) != len(requested):
            raise ContractViolation("a comparison must not repeat a model")
        if len(distinct) < 2:
            raise ContractViolation("a comparison needs at least two distinct models")
        unsupported = [model for model in distinct if model not in SUPPORTED_MODELS]
        if unsupported:
            # The override is a hole in the pinning, so its membership check is
            # the fence around that hole. Refusing before anything is created is
            # what keeps the hole from widening into "whatever string was sent".
            raise ContractViolation(
                f"model override is not in the supported set: {unsupported[0]}"
            )
        rounds = self._comparison_repetitions(repetitions)

        context = self.studio.flow_context(workspace_id, flow_id)
        flow_version = context["version"]
        if not flow_version["requires_model"]:
            raise ContractViolation(
                "this pinned Flow version calls no model, so there is no brain to vary"
            )
        pinned_models = self.studio.flow_version_pinned_models(
            workspace_id, flow_version["id"]
        )
        if len(pinned_models) != 1:
            raise ContractViolation(
                "a comparison needs exactly one pinned model to replace; this Flow "
                "version pins " + str(len(pinned_models))
            )
        pinned_model = pinned_models[0]

        comparison_id = new_id("cmp")
        correlation_id = new_id("corr")
        runtime = self._studio_runtime(client)
        # Prepare the complete sibling set before the first provider call. A
        # process death while rows are being prepared leaves no manifest and is
        # therefore derivably unusable; a death after the manifest leaves the
        # exact missing Run(s) visible instead of shrinking the experiment.
        prepared: list[tuple[str, int, dict[str, Any]]] = []
        for model in distinct:
            for round_index in range(1, rounds + 1):
                prepared.append(
                    (
                        model,
                        round_index,
                        self._prepare_comparison_sibling(
                            runtime,
                            workspace_id,
                            flow_id,
                            input_data=input_data,
                            flow_version=int(flow_version["version"]),
                            model=model,
                            pinned_model=pinned_model,
                            comparison_id=comparison_id,
                            correlation_id=correlation_id,
                            round_index=round_index,
                        ),
                    )
                )
        first_run = prepared[0][2]
        self.studio.append_event(
            workspace_id,
            first_run["id"],
            event_type="comparison.manifest_pinned",
            actor_type="runtime",
            actor_id=None,
            payload={
                "comparison_id": comparison_id,
                "flow_id": flow_id,
                "flow_version_id": flow_version["id"],
                "flow_fingerprint": flow_version["fingerprint"],
                "input_fingerprint": fingerprint(first_run["input"]),
                "pinned_model": pinned_model,
                "models": distinct,
                "repetitions": rounds,
                "siblings": [
                    {
                        "run_id": run["id"],
                        "model": model,
                        "repetition": round_index,
                    }
                    for model, round_index, run in prepared
                ],
            },
        )
        for _model, _round_index, run in prepared:
            self._continue_comparison_sibling(runtime, workspace_id, run)
        return self._derive_comparison(workspace_id, comparison_id)

    @staticmethod
    def _comparison_repetitions(value: Any) -> int:
        """Repetitions are how the harness measures its own noise before judging.

        One run per model is noise rendered as a finding, so more than one is the
        honest default wherever a caller can afford it. When only one is run the
        derived record says so and refuses to classify any numeric difference as
        a result rather than pretending the single sample was stable.
        """

        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ContractViolation("comparison repetitions must be a positive integer")
        if value > 5:
            raise ContractViolation("comparison repetitions are bounded to five")
        return value

    def studio_comparison_model_call_forecast(
        self,
        workspace_id: str,
        flow_id: str,
        *,
        models: Sequence[str],
        repetitions: int,
    ) -> int:
        """Declare the whole sweep's cost before any of it is spent.

        N models times R repetitions is N*R times a single Run's forecast. The
        HTTP layer charges that total against the per-workspace, per-address and
        global model budgets *before* the command is invoked, which is what makes
        an over-budget comparison a refusal rather than a half-finished sweep
        whose remaining siblings are silently missing.
        """

        per_run = self.studio_flow_model_call_forecast(workspace_id, flow_id)
        return per_run * len(models) * repetitions

    def _prepare_comparison_sibling(
        self,
        runtime: StudioRuntime,
        workspace_id: str,
        flow_id: str,
        *,
        input_data: Mapping[str, Any],
        flow_version: int,
        model: str,
        pinned_model: str,
        comparison_id: str,
        correlation_id: str,
        round_index: int,
    ) -> dict[str, Any]:
        """Pin one sibling through the same fences a normal Run passes through.

        Preparation performs no external I/O. The worker path is entered only
        after every sibling exists and their expected set is ledger-pinned.
        """

        return runtime.prepare(
            workspace_id,
            flow_id,
            input_data=input_data,
            flow_version=flow_version,
            relation_kind="comparison",
            correlation_id=correlation_id,
            idempotency_key=f"comparison:{comparison_id}:{model}:{round_index}",
            model_override=model,
            comparison_id=comparison_id,
            pinned_model=pinned_model,
        )

    def _continue_comparison_sibling(
        self,
        runtime: StudioRuntime,
        workspace_id: str,
        run: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Execute one already manifested sibling in the ordinary worker fence."""

        with self._active_studio_runs_lock:
            if run["id"] in self._active_studio_runs:
                return self.studio.get_run(workspace_id, run["id"])
            self._active_studio_runs.add(run["id"])
        try:
            with self._studio_worker_slots:
                return runtime.continue_run(workspace_id, run["id"])
        finally:
            with self._active_studio_runs_lock:
                self._active_studio_runs.discard(run["id"])

    def list_comparisons(
        self, workspace_id: str, *, limit: int | None = None
    ) -> list[dict[str, Any]]:
        """Read every comparison back out of its siblings, newest first."""

        groups = self.studio.comparison_groups(workspace_id)
        if limit is not None:
            groups = groups[:limit]
        return [
            self._derive_comparison(workspace_id, group["id"], group=group)
            for group in groups
        ]

    def get_comparison(self, workspace_id: str, comparison_id: str) -> dict[str, Any]:
        return self._derive_comparison(workspace_id, comparison_id)

    def _derive_comparison(
        self,
        workspace_id: str,
        comparison_id: str,
        *,
        group: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if group is None:
            groups = self.studio.comparison_groups(workspace_id, comparison_id)
            if not groups:
                raise NotFound("model comparison was not found")
            group = groups[0]
        runs = [
            self.studio.get_run(workspace_id, run_id) for run_id in group["run_ids"]
        ]
        manifest_events = [
            event["payload"]
            for run in runs
            for event in run["events"]
            if event["type"] == "comparison.manifest_pinned"
        ]
        ordered: dict[str, list[dict[str, Any]]] = {}
        for run in runs:
            ordered.setdefault(str(run["model_override"]), []).append(run)
        first = runs[0]
        return build_comparison(
            comparison_id=comparison_id,
            created_at=group["created_at"],
            flow_id=first["flow_id"],
            flow_version_id=first["flow_version_id"],
            flow_version=first["flow_version"],
            flow_fingerprint=first["flow_fingerprint"],
            pinned_model=self._comparison_pinned_model(workspace_id, first),
            input_fingerprint=fingerprint(first["input"]),
            repetitions=max(len(items) for items in ordered.values()),
            runs_by_model=list(ordered.items()),
            manifests=manifest_events,
        )

    def _comparison_pinned_model(
        self, workspace_id: str, run: Mapping[str, Any]
    ) -> str:
        """The model the sweep displaced, read back off the pinned version.

        Derived rather than copied onto the comparison: the pinned Flow version
        is immutable, so reading the model out of it can never disagree with what
        the siblings actually replaced.
        """

        for event in run["events"]:
            if event["type"] == "run.model_overridden":
                recorded = event["payload"].get("pinned_model")
                if isinstance(recorded, str):
                    return recorded
        pinned = self.studio.flow_version_pinned_models(
            workspace_id, run["flow_version_id"]
        )
        return pinned[0] if pinned else ""

    def cancel_studio_run(
        self,
        workspace_id: str,
        run_id: str,
        *,
        actor: Any,
        reason: Any,
    ) -> dict[str, Any]:
        return self.studio.cancel_run(
            workspace_id,
            run_id,
            actor=require_string(actor, "cancel actor", maximum=100),
            reason=require_string(reason, "cancel reason", minimum=8, maximum=500),
        )

    def diagnose_studio_run(
        self,
        workspace_id: str,
        run_id: str,
        *,
        client: ResponseTransport | None = None,
    ) -> dict[str, Any]:
        existing = self.studio.find_run_diagnosis(workspace_id, run_id)
        if existing is not None:
            return existing
        run = self.studio.get_run(workspace_id, run_id)
        if run["status"] not in {"blocked", "failed"}:
            raise ContractViolation("only a blocked or failed Run can be diagnosed")
        failed = next(
            (
                step
                for step in reversed(run["steps"])
                if step["status"] in {"failed", "blocked"}
            ),
            None,
        )
        if failed is None or failed["node_type"] != "action":
            raise ContractViolation("Run has no diagnosable failed Action Step")
        action = self.studio.get_action_version(
            workspace_id, failed["target_version_id"]
        )
        if (
            action["kind"] == "data_store"
            and action["config"].get("write_enabled") is False
            and failed["error_code"] == "action_blocked"
        ):
            fault_class = "authority_policy"
            root_cause = "The pinned Data Store Action denies its own bounded write."
            explanation = (
                "The Action receipt records a denied invocation and no effect row exists. "
                "The only repairable mismatch is the immutable write_enabled policy on "
                f"Action {action['slug']} v{action['version']}."
            )
            confidence_milli = 990
        else:
            fault_class = (
                "provider_failure"
                if failed["error_code"] == "provider_failure"
                else "data_contract"
                if failed["error_code"] == "contract_violation"
                else "runtime_failure"
            )
            root_cause = failed["error_message"] or "The failed Step recorded no message."
            explanation = (
                "The diagnosis is bounded to the failed Step and its authoritative Run events."
            )
            confidence_milli = 850
        evidence = [
            event["id"]
            for event in run["events"]
            if event["type"] in {
                "action.receipted",
                "step.failed",
                "step.blocked",
                "run.status_changed",
            }
            and (
                event["payload"].get("step_id") in {None, failed["id"]}
                or event["type"] == "run.status_changed"
            )
        ]
        if not evidence:
            evidence = [run["events"][-1]["id"]]

        created_by_agent_version_id = None
        if client is not None:
            diagnostician = self.studio.find_agent_runtime_by_role(
                workspace_id, "diagnostician"
            )
            grounded = self._studio_runtime(client).explain_diagnosis(
                workspace_id,
                run_id,
                failed["id"],
                agent_version_id=diagnostician["id"],
                candidate={
                    "fault_class": fault_class,
                    "root_cause": root_cause,
                    "explanation": explanation,
                    "failed_node_id": failed["node_id"],
                    "action": {
                        "slug": action["slug"],
                        "kind": action["kind"],
                        "version": action["version"],
                    },
                    "evidence_event_ids": evidence,
                },
            )
            cited = grounded["evidence_event_ids"]
            if not set(cited).issubset(set(evidence)):
                raise ContractViolation(
                    "diagnostician cited evidence outside the code-owned candidate"
                )
            root_cause = grounded["root_cause"]
            explanation = grounded["explanation"]
            confidence_milli = int(round(float(grounded["confidence"]) * 1_000))
            evidence = cited
            created_by_agent_version_id = diagnostician["id"]
        return self.studio.record_diagnosis(
            workspace_id,
            run_id,
            failed_step_id=failed["id"],
            failed_node_id=failed["node_id"],
            action_version_id=action["id"],
            fault_class=fault_class,
            root_cause=root_cause,
            explanation=explanation,
            confidence_milli=confidence_milli,
            evidence_event_ids=evidence,
            created_by_agent_version_id=created_by_agent_version_id,
        )

    def propose_studio_repair(
        self, workspace_id: str, diagnosis_id: str
    ) -> dict[str, Any]:
        return self.studio.propose_repair(workspace_id, diagnosis_id)

    def apply_studio_repair(
        self,
        workspace_id: str,
        proposal_id: str,
        *,
        proposal_hash: Any,
        expected_flow_revision: Any,
        expected_action_version: Any,
        actor: Any,
        reason: Any,
        acknowledged: Any,
    ) -> dict[str, Any]:
        normalized_hash = require_string(
            proposal_hash, "repair proposal hash", maximum=64
        )
        if not HEX_64_RE.fullmatch(normalized_hash):
            raise ContractViolation("repair proposal hash is invalid")
        for value, field in (
            (expected_flow_revision, "expected Flow revision"),
            (expected_action_version, "expected Action version"),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ContractViolation(f"{field} is invalid")
        if not isinstance(acknowledged, bool):
            raise ContractViolation("repair acknowledgement must be a boolean")
        return self.studio.apply_repair(
            workspace_id,
            proposal_id,
            proposal_hash=normalized_hash,
            expected_flow_revision=expected_flow_revision,
            expected_action_version=expected_action_version,
            actor=require_string(actor, "repair actor", maximum=100),
            reason=require_string(reason, "repair reason", minimum=12, maximum=500),
            acknowledged=acknowledged,
        )

    def prove_studio_repair(
        self,
        workspace_id: str,
        proposal_id: str,
        *,
        input_data: Any,
        idempotency_key: Any,
        client: ResponseTransport | None = None,
    ) -> dict[str, Any]:
        if not isinstance(input_data, dict):
            raise ContractViolation("repair proof input must be an object")
        proposal = self.studio.get_repair(workspace_id, proposal_id)
        if proposal["status"] != "applied" or not proposal.get("applied_flow_version"):
            raise ContractViolation("repair must be applied before its proof Run")
        diagnosis = self.studio.get_diagnosis(
            workspace_id, proposal["diagnosis_id"]
        )
        parent = self.studio.get_run(workspace_id, diagnosis["run_id"])
        require_string(
            idempotency_key, "repair proof idempotency key", maximum=100
        )
        return self._studio_runtime(client).execute(
            workspace_id,
            proposal["flow_id"],
            input_data=input_data,
            flow_version=int(proposal["applied_flow_version"]),
            parent_run_id=parent["id"],
            relation_kind="proof",
            correlation_id=parent["correlation_id"],
            idempotency_key=f"repair-proof:{proposal_id}",
        )

    def get_studio_action(self, workspace_id: str, action_id: str) -> dict[str, Any]:
        return self.studio.get_action(workspace_id, action_id)

    def get_studio_flow(self, workspace_id: str, flow_id: str) -> dict[str, Any]:
        return self.studio.get_flow(workspace_id, flow_id)

    def studio_snapshot(self, workspace_id: str) -> dict[str, Any]:
        snapshot = self.studio.snapshot(workspace_id)
        snapshot.update(self.context.snapshot(workspace_id))
        snapshot["boardrooms"] = self._boardroom_projections(
            workspace_id, snapshot["flows"]
        )
        snapshot["comparisons"] = self.list_comparisons(workspace_id, limit=10)
        # The comparison surface has to offer exactly the models the override
        # membership check will accept. Projecting the set the server enforces
        # keeps the two from drifting: a hardcoded browser list would silently
        # start offering a model the runtime refuses, or hiding one it allows.
        snapshot["supported_models"] = sorted(SUPPORTED_MODELS)
        return snapshot

    def _boardroom_projections(
        self, workspace_id: str, flows: Sequence[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        """Derive BoardRooms from ordinary editable fan-out Flows.

        BoardRoom deliberately owns no second persistence or execution model.
        This projection is a lens over the generic Flow contract, so opening a
        room in Flow Studio edits the exact resource the runtime executes.
        """

        projected: list[dict[str, Any]] = []
        for flow in flows:
            version = flow["version"]
            fan_nodes = [
                node for node in version["nodes"] if node["type"] == "fan_out"
            ]
            if not fan_nodes:
                continue
            fan_node = fan_nodes[0]
            members: list[dict[str, Any]] = []
            for member in fan_node["members"]:
                label = member["id"]
                model = None
                if member["type"] == "action":
                    target = self.studio.get_action_version(
                        workspace_id, member["version_id"]
                    )
                    label = target["name"]
                    if target["agent_version_id"] is not None:
                        agent = self.studio.get_agent_runtime(
                            workspace_id, target["agent_version_id"]
                        )
                        model = agent["model"]
                elif member["type"] == "agent":
                    agent = self.studio.get_agent_runtime(
                        workspace_id, member["version_id"]
                    )
                    label = agent["name"]
                    model = agent["model"]
                else:
                    subflow = self.studio.get_flow_version_by_id(
                        workspace_id, member["version_id"]
                    )
                    label = subflow["name"]
                members.append(
                    {
                        "id": member["id"],
                        "type": member["type"],
                        "version_id": member["version_id"],
                        "label": label,
                        "model": model,
                    }
                )

            approval_mode = "none"
            write_collection = None
            for node in version["nodes"]:
                if node["type"] != "action":
                    continue
                action = self.studio.get_action_version(
                    workspace_id, node["version_id"]
                )
                if action["kind"] == "approval":
                    approval_mode = "human"
                if action["kind"] == "data_store" and action_mints_effect(
                    action["kind"], action["config"]
                ):
                    write_collection = action["config"]["collection"]
            editor_node_id = next(
                (
                    route["to"]
                    for route in version["routes"]
                    if route["from"] == fan_node["id"]
                    and route["outcome"] in {"converged", "review"}
                ),
                None,
            )
            projected.append(
                {
                    "id": flow["id"],
                    "flow_id": flow["id"],
                    "flow_version_id": version["id"],
                    "name": flow["name"],
                    "slug": flow["slug"],
                    "purpose": flow["description"],
                    "revision": flow["revision"],
                    "composition_node_id": fan_node["id"],
                    "editor_node_id": editor_node_id,
                    "members": members,
                    "barrier": fan_node["barrier"],
                    "approval_mode": approval_mode,
                    "write_collection": write_collection,
                    "model_call_forecast": self.studio_flow_model_call_forecast(
                        workspace_id, flow["id"]
                    ),
                    "editable_in_flow_studio": True,
                }
            )
        return projected

    # -- Context: Knowledge, SmartRead, and governed Memory ------------

    @staticmethod
    def _knowledge_filename(value: Any) -> str:
        filename = require_string(value, "Knowledge filename", maximum=180)
        if any(marker in filename for marker in ("/", "\\", "..")) or any(
            ord(character) < 32 for character in filename
        ):
            raise ContractViolation("Knowledge filename must be a display name, not a path")
        return filename

    @staticmethod
    def _knowledge_media_type(value: Any) -> str:
        media_type = require_string(value, "Knowledge media type", maximum=80)
        if media_type not in {
            "text/plain",
            "text/markdown",
            "application/json",
            "application/yaml",
            "text/x-source-code",
        }:
            raise ContractViolation("Knowledge media type is not supported")
        return media_type

    def create_knowledge_source(
        self,
        workspace_id: str,
        *,
        name: Any,
        slug: Any,
        description: Any,
        filename: Any,
        media_type: Any,
        content: Any,
        created_by: Any = "user",
    ) -> dict[str, Any]:
        return self.context.create_source(
            workspace_id,
            name=require_string(name, "Knowledge Source name", maximum=100),
            slug=require_slug(slug),
            description=require_string(
                description, "Knowledge Source description", minimum=0, maximum=500
            ),
            filename=self._knowledge_filename(filename),
            media_type=self._knowledge_media_type(media_type),
            content=content,
            created_by=require_string(created_by, "Knowledge author", maximum=100),
        )

    def revise_knowledge_source(
        self,
        workspace_id: str,
        source_id: str,
        *,
        expected_version: Any,
        name: Any,
        description: Any,
        filename: Any,
        media_type: Any,
        content: Any,
        created_by: Any = "user",
    ) -> dict[str, Any]:
        expected = self._expected_resource_version(expected_version, "Knowledge Source")
        return self.context.revise_source(
            workspace_id,
            source_id,
            expected_version=expected,
            name=require_string(name, "Knowledge Source name", maximum=100),
            description=require_string(
                description, "Knowledge Source description", minimum=0, maximum=500
            ),
            filename=self._knowledge_filename(filename),
            media_type=self._knowledge_media_type(media_type),
            content=content,
            created_by=require_string(created_by, "Knowledge author", maximum=100),
        )

    def get_knowledge_source(self, workspace_id: str, source_id: str) -> dict[str, Any]:
        return self.context.get_source(workspace_id, source_id)

    def smart_read(
        self,
        workspace_id: str,
        *,
        source_version_id: Any,
        mode: Any,
        query: Any = None,
        line_start: Any = None,
        line_end: Any = None,
        max_results: Any = 12,
    ) -> dict[str, Any]:
        return self.context.smart_read(
            workspace_id,
            require_string(source_version_id, "Knowledge Source version id", maximum=80),
            mode=require_string(mode, "SmartRead mode", maximum=20),
            query=query,
            line_start=line_start,
            line_end=line_end,
            max_results=max_results,
        )

    def search_knowledge(
        self, workspace_id: str, *, query: Any, max_results: Any = 12
    ) -> dict[str, Any]:
        return self.context.search_knowledge(workspace_id, query, max_results=max_results)

    @staticmethod
    def _memory_tags(value: Any) -> list[str]:
        tags = require_string_list(
            value, "Memory tags", maximum_items=12, maximum_item_length=40
        )
        normalized: list[str] = []
        for tag in tags:
            folded = tag.casefold()
            if folded not in normalized:
                normalized.append(folded)
        return normalized

    @staticmethod
    def _memory_event_ids(value: Any) -> list[str]:
        return require_string_list(
            value, "Memory evidence event ids", maximum_items=20, maximum_item_length=80
        )

    def create_human_memory_candidate(
        self,
        workspace_id: str,
        *,
        source_run_id: Any,
        title: Any,
        content: Any,
        rationale: Any,
        tags: Any,
        evidence_event_ids: Any,
    ) -> dict[str, Any]:
        return self.context.create_human_candidate(
            workspace_id,
            source_run_id=require_string(source_run_id, "Memory source Run id", maximum=80),
            title=require_string(title, "Memory title", maximum=140),
            content=require_string(content, "Memory content", maximum=6_000),
            rationale=require_string(rationale, "Memory rationale", maximum=1_500),
            tags=self._memory_tags(tags),
            evidence_event_ids=self._memory_event_ids(evidence_event_ids),
        )

    def draft_model_memory_candidate(
        self,
        workspace_id: str,
        *,
        source_run_id: Any,
        distiller_agent_version_id: Any,
        evidence_event_ids: Any,
        client: ResponseTransport,
    ) -> dict[str, Any]:
        run_id = require_string(source_run_id, "Memory source Run id", maximum=80)
        agent_id = require_string(
            distiller_agent_version_id, "Memory distiller Agent version id", maximum=80
        )
        cited_ids = self._memory_event_ids(evidence_event_ids)
        run = self.studio.get_run(workspace_id, run_id)
        if run["status"] != "completed" or run.get("ledger_verified") is not True:
            raise ContractViolation("Memory source Run must be completed with a verified ledger")
        owned_events = {event["id"]: event for event in run["events"]}
        if not cited_ids or not set(cited_ids).issubset(owned_events):
            raise ContractViolation("Memory distillation evidence must belong to the source Run")
        agent = self.studio.get_agent_runtime(workspace_id, agent_id)
        schema = {
            "type": "object",
            "properties": {
                "title": {"type": "string", "minLength": 3, "maxLength": 140},
                "content": {"type": "string", "minLength": 12, "maxLength": 6000},
                "rationale": {"type": "string", "minLength": 12, "maxLength": 1500},
                "tags": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1, "maxLength": 40},
                    "minItems": 1,
                    "maxItems": 12,
                },
                "evidence_event_ids": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1, "maxLength": 80},
                    "minItems": 1,
                    "maxItems": 20,
                },
            },
            "required": ["title", "content", "rationale", "tags", "evidence_event_ids"],
            "additionalProperties": False,
        }
        evidence = {
            "run_id": run_id,
            "flow_version_id": run["flow_version_id"],
            "outcome": run["outcome"],
            "output": run["output"],
            "events": [owned_events[event_id] for event_id in cited_ids],
        }
        evidence_json = canonical_json(evidence)
        if len(evidence_json.encode("utf-8")) > 96 * 1024:
            raise ContractViolation("Memory distillation evidence exceeds 96 KiB")
        payload = {
            "model": agent["model"],
            "instructions": (
                "You are a pinned Memory distiller. Propose one reusable observation "
                "using only the supplied verified Run evidence. Cite only supplied "
                "event IDs. Do not grant authority, invent outcomes, or write policy.\n\n"
                + StudioRuntime._agent_instructions(agent)
            ),
            "input": [{"role": "user", "content": evidence_json}],
            "tool_choice": "none",
            "parallel_tool_calls": False,
            "max_output_tokens": 1200,
            "store": False,
            "reasoning": {"effort": "medium"},
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "kyn_memory_candidate",
                    "schema": schema,
                    "strict": True,
                }
            },
            "metadata": {
                "kyn_surface": "agent-studio",
                "operation": "memory-distillation",
                "source_run_id": run_id,
                "agent_version_id": agent_id,
            },
        }
        if self.store.in_write_transaction():
            raise RuntimeError("external model I/O under a SQLite write transaction")
        response = client.create(payload)
        require_completed_response(
            response,
            max_output_tokens=payload["max_output_tokens"],
            operation="OpenAI Memory distillation response",
        )
        try:
            parsed = json.loads(extract_output_text(response))
        except json.JSONDecodeError:
            raise ContractViolation("Memory distiller output is not valid JSON") from None
        candidate = validate_json_schema(parsed, schema, "Memory distiller output")
        if not set(candidate["evidence_event_ids"]).issubset(cited_ids):
            raise ContractViolation("Memory distiller cited evidence outside its supplied window")
        summary = safe_response_summary(response)
        return self.context.create_model_candidate(
            workspace_id,
            source_run_id=run_id,
            distiller_agent_version_id=agent_id,
            title=require_string(candidate["title"], "Memory title", maximum=140),
            content=require_string(candidate["content"], "Memory content", maximum=6_000),
            rationale=require_string(candidate["rationale"], "Memory rationale", maximum=1_500),
            tags=self._memory_tags(candidate["tags"]),
            evidence_event_ids=self._memory_event_ids(candidate["evidence_event_ids"]),
            provider_response_id=summary["provider_response_id"],
            status="completed",
            model=summary["model"],
            input_hash=fingerprint(payload),
            output_hash=fingerprint(candidate),
            usage=summary["usage"],
            request_id=(
                str(response.get("_request_id"))[:128]
                if response.get("_request_id") is not None
                else None
            ),
        )

    def qualify_memory_candidate(
        self, workspace_id: str, candidate_id: str
    ) -> dict[str, Any]:
        return self.context.qualify_candidate(workspace_id, candidate_id)

    def promote_memory_candidate(
        self,
        workspace_id: str,
        candidate_id: str,
        *,
        slug: Any,
        actor: Any,
        reason: Any,
        acknowledged: Any,
        candidate_fingerprint: Any,
    ) -> dict[str, Any]:
        if acknowledged is not True:
            raise ContractViolation("Memory promotion requires explicit acknowledgement")
        fingerprint_value = require_string(
            candidate_fingerprint, "Memory candidate fingerprint", maximum=64
        )
        if not HEX_64_RE.fullmatch(fingerprint_value):
            raise ContractViolation("Memory candidate fingerprint is invalid")
        return self.context.promote_candidate(
            workspace_id,
            candidate_id,
            slug=require_slug(slug),
            actor=require_string(actor, "Memory decision actor", maximum=100),
            reason=require_string(
                reason, "Memory promotion reason", minimum=20, maximum=1_500
            ),
            candidate_fingerprint_value=fingerprint_value,
        )

    def reject_memory_candidate(
        self,
        workspace_id: str,
        candidate_id: str,
        *,
        actor: Any,
        reason: Any,
        acknowledged: Any,
        candidate_fingerprint: Any,
    ) -> dict[str, Any]:
        if acknowledged is not True:
            raise ContractViolation("Memory rejection requires explicit acknowledgement")
        fingerprint_value = require_string(
            candidate_fingerprint, "Memory candidate fingerprint", maximum=64
        )
        if not HEX_64_RE.fullmatch(fingerprint_value):
            raise ContractViolation("Memory candidate fingerprint is invalid")
        return self.context.reject_candidate(
            workspace_id,
            candidate_id,
            actor=require_string(actor, "Memory decision actor", maximum=100),
            reason=require_string(
                reason, "Memory rejection reason", minimum=20, maximum=1_500
            ),
            candidate_fingerprint_value=fingerprint_value,
        )

    def retire_memory(
        self,
        workspace_id: str,
        memory_id: str,
        *,
        actor: Any,
        reason: Any,
    ) -> dict[str, Any]:
        return self.context.retire_memory(
            workspace_id,
            memory_id,
            actor=require_string(actor, "Memory retirement actor", maximum=100),
            reason=require_string(
                reason, "Memory retirement reason", minimum=20, maximum=1_500
            ),
        )

    def search_memories(
        self, workspace_id: str, *, query: Any, max_results: Any = 12
    ) -> dict[str, Any]:
        return self.context.search_memories(workspace_id, query, max_results=max_results)

    # -- BoardRoom: editable template over the generic fan-out motor -----

    def create_boardroom(
        self,
        workspace_id: str,
        *,
        name: Any,
        slug: Any,
        purpose: Any,
        participants: Any,
        editor: Any,
        quorum: Any,
        error_policy: Any,
        approval_mode: Any,
        write_collection: Any = None,
    ) -> dict[str, Any]:
        room_name = require_string(name, "BoardRoom name", maximum=100)
        room_slug = require_slug(slug, "BoardRoom slug")
        room_purpose = require_string(purpose, "BoardRoom purpose", maximum=1_500)
        if not isinstance(participants, list) or not 2 <= len(participants) <= 8:
            raise ContractViolation("BoardRoom must contain two to eight participants")
        if (
            not isinstance(quorum, int)
            or isinstance(quorum, bool)
            or not 1 <= quorum <= len(participants)
        ):
            raise ContractViolation("BoardRoom quorum is impossible")
        if error_policy not in {"isolate", "fail_fast"}:
            raise ContractViolation("BoardRoom member error policy is invalid")
        if approval_mode not in {"none", "human"}:
            raise ContractViolation("BoardRoom approval mode is invalid")
        normalized_collection = (
            require_slug(write_collection, "BoardRoom write collection")
            if write_collection is not None
            else None
        )
        if normalized_collection is not None and approval_mode != "human":
            raise ContractViolation("BoardRoom writes require a human approval gate")
        editor_required = {
            "name",
            "model",
            "instructions",
            "reasoning_effort",
        }
        if (
            not isinstance(editor, dict)
            or not editor_required.issubset(editor)
            or not set(editor).issubset(editor_required | {"max_output_tokens"})
        ):
            raise ContractViolation("BoardRoom editor config is invalid")
        editor_model = require_string(editor["model"], "BoardRoom editor model", maximum=80)
        if editor_model not in SUPPORTED_MODELS:
            raise ContractViolation("BoardRoom editor model is not supported")
        if editor["reasoning_effort"] not in {"low", "medium", "high"}:
            raise ContractViolation("BoardRoom editor reasoning effort is invalid")
        normalized_editor = {
            "name": require_string(editor["name"], "BoardRoom editor name", maximum=100),
            "model": editor_model,
            "instructions": require_string(
                editor["instructions"], "BoardRoom editor instructions", maximum=4_000
            ),
            "reasoning_effort": editor["reasoning_effort"],
            "max_output_tokens": _normalize_studio_output_tokens(
                editor.get("max_output_tokens"),
                "BoardRoom editor max_output_tokens",
                default=BOARDROOM_EDITOR_OUTPUT_TOKENS,
            ),
        }

        normalized_participants: list[dict[str, Any]] = []
        participant_ids: set[str] = set()
        for index, participant in enumerate(participants):
            required = {
                "id",
                "name",
                "perspective",
                "model",
                "instructions",
                "allowed_action_version_ids",
                "max_tool_calls",
                "reasoning_effort",
            }
            if (
                not isinstance(participant, dict)
                or not required.issubset(participant)
                or not set(participant).issubset(required | {"max_output_tokens"})
            ):
                raise ContractViolation(f"BoardRoom participant {index} config is invalid")
            participant_id = require_slug(
                participant["id"], f"BoardRoom participant {index} id"
            )
            if participant_id in participant_ids:
                raise ContractViolation("BoardRoom participant ids must be unique")
            participant_ids.add(participant_id)
            model = require_string(
                participant["model"],
                f"BoardRoom participant {participant_id} model",
                maximum=80,
            )
            if model not in SUPPORTED_MODELS:
                raise ContractViolation(
                    f"BoardRoom participant {participant_id} model is not supported"
                )
            grants = require_string_list(
                participant["allowed_action_version_ids"],
                f"BoardRoom participant {participant_id} Action grants",
                maximum_items=12,
                maximum_item_length=80,
            )
            for version_id in grants:
                granted = self.studio.get_action_version(workspace_id, version_id)
                if granted["kind"] not in CALLABLE_ACTION_KINDS:
                    raise ContractViolation(
                        f"BoardRoom participant {participant_id} grants a non-callable Action"
                    )
                if granted["kind"] == "approval" or action_mints_effect(
                    granted["kind"], granted["config"]
                ):
                    raise ContractViolation(
                        "BoardRoom participant grants may not pause or mint effects"
                    )
            max_calls = participant["max_tool_calls"]
            if (
                not isinstance(max_calls, int)
                or isinstance(max_calls, bool)
                or not 0 <= max_calls <= 4
            ):
                raise ContractViolation(
                    f"BoardRoom participant {participant_id} tool budget is invalid"
                )
            if max_calls and not grants:
                raise ContractViolation(
                    f"BoardRoom participant {participant_id} has a tool budget but no Action grant"
                )
            reasoning = participant["reasoning_effort"]
            if reasoning not in {"low", "medium", "high"}:
                raise ContractViolation(
                    f"BoardRoom participant {participant_id} reasoning effort is invalid"
                )
            normalized_participants.append(
                {
                    "id": participant_id,
                    "name": require_string(
                        participant["name"],
                        f"BoardRoom participant {participant_id} name",
                        maximum=100,
                    ),
                    "perspective": require_string(
                        participant["perspective"],
                        f"BoardRoom participant {participant_id} perspective",
                        maximum=1_500,
                    ),
                    "model": model,
                    "instructions": require_string(
                        participant["instructions"],
                        f"BoardRoom participant {participant_id} instructions",
                        maximum=4_000,
                    ),
                    "allowed_action_version_ids": grants,
                    "max_tool_calls": max_calls,
                    "reasoning_effort": reasoning,
                    "max_output_tokens": _normalize_studio_output_tokens(
                        participant.get("max_output_tokens"),
                        f"BoardRoom participant {participant_id} max_output_tokens",
                        default=BOARDROOM_PARTICIPANT_OUTPUT_TOKENS,
                    ),
                }
            )

        final_statuses = (
            ["synthesized"]
            if approval_mode == "none"
            else ["rejected", "approved" if normalized_collection is None else "written"]
        )
        generated_slugs = {
            "prompts": [
                *(f"{room_slug}-{item['id']}-prompt" for item in normalized_participants),
                f"{room_slug}-editor-prompt",
            ],
            "skills": [
                *(f"{room_slug}-{item['id']}-skill" for item in normalized_participants),
                f"{room_slug}-editor-skill",
            ],
            "agents": [
                *(f"{room_slug}-{item['id']}-agent" for item in normalized_participants),
                f"{room_slug}-editor-agent",
            ],
            "actions": [
                *(f"{room_slug}-{item['id']}-vote" for item in normalized_participants),
                f"{room_slug}-synthesis",
                *(f"{room_slug}-result-{status}" for status in final_statuses),
                *([f"{room_slug}-approval"] if approval_mode == "human" else []),
                *([f"{room_slug}-write"] if normalized_collection is not None else []),
            ],
            "automation_flows": [room_slug],
        }
        for resource_slugs in generated_slugs.values():
            for generated_slug in resource_slugs:
                require_slug(generated_slug, "generated BoardRoom resource slug")
            if len(set(resource_slugs)) != len(resource_slugs):
                raise ContractViolation("BoardRoom generated resource slugs collide")
        with self.store.read() as connection:
            for table, resource_slugs in generated_slugs.items():
                placeholders = ",".join("?" for _ in resource_slugs)
                conflict = connection.execute(
                    f'SELECT slug FROM "{table}" '
                    f"WHERE workspace_id = ? AND slug IN ({placeholders}) LIMIT 1",
                    (workspace_id, *resource_slugs),
                ).fetchone()
                if conflict is not None:
                    raise Conflict(
                        f"BoardRoom resource slug already exists: {conflict['slug']}"
                    )

        # Every field above is normalized, every grant resolved, and every
        # authority boundary checked before the first resource is published.
        participant_resources: list[dict[str, Any]] = []
        for participant in normalized_participants:
            resource_slug = f"{room_slug}-{participant['id']}"
            prompt = self.create_prompt(
                workspace_id,
                name=f"{participant['name']} · {room_name}",
                slug=f"{resource_slug}-prompt",
                template=(
                    "Review the brief independently. Do not infer another participant's "
                    "position. Cite the supplied context when it supports a claim.\n\n"
                    "Brief:\n{{brief}}\n\nEvidence context:\n{{context}}"
                ),
                variables=["brief", "context"],
            )
            skill = self.create_skill(
                workspace_id,
                name=f"{participant['name']} perspective",
                slug=f"{resource_slug}-skill",
                instructions=(
                    f"Perspective: {participant['perspective']}\n\n"
                    "Return an independent verdict. Preserve uncertainty and cite only "
                    "context actually supplied to this run."
                ),
                allowed_tools=[],
                allowed_action_version_ids=participant["allowed_action_version_ids"],
            )
            agent = self.create_agent(
                workspace_id,
                name=participant["name"],
                slug=f"{resource_slug}-agent",
                role="executor",
                model=participant["model"],
                instructions=(
                    f"BoardRoom purpose: {room_purpose}\n\n"
                    f"{participant['instructions']}\n\n"
                    "You cannot see other participant outputs. Verdict must be commit, "
                    "challenge, or abstain."
                ),
                prompt_version_id=prompt["version"]["id"],
                skill_version_ids=[skill["version"]["id"]],
            )
            action = self.create_action(
                workspace_id,
                name=f"{participant['name']} independent vote",
                slug=f"{resource_slug}-vote",
                description=(
                    f"Runs the {participant['name']} perspective independently and "
                    "returns a strict verdict with analysis, risk, and citations."
                ),
                kind="ai",
                input_schema=BOARDROOM_INPUT_SCHEMA,
                output_schema=BOARDROOM_PARTICIPANT_SCHEMA,
                outcomes=None,
                config={
                    "max_tool_calls": participant["max_tool_calls"],
                    "reasoning_effort": participant["reasoning_effort"],
                    "max_output_tokens": participant["max_output_tokens"],
                },
                agent_version_id=agent["version"]["id"],
            )
            participant_resources.append(
                {
                    "id": participant["id"],
                    "prompt": prompt,
                    "skill": skill,
                    "agent": agent,
                    "action": action,
                }
            )

        editor_prompt = self.create_prompt(
            workspace_id,
            name=f"{normalized_editor['name']} · {room_name}",
            slug=f"{room_slug}-editor-prompt",
            template=(
                "Synthesize the completed participant records. Preserve every material "
                "dissent and failed member; do not turn quorum into unanimity.\n\n"
                "Participants:\n{{members}}\n\nCode-owned barrier:\n{{barrier}}"
            ),
            variables=["members", "barrier"],
        )
        editor_skill = self.create_skill(
            workspace_id,
            name=f"{room_name} dissent-preserving synthesis",
            slug=f"{room_slug}-editor-skill",
            instructions=(
                "The deterministic barrier owns quorum. The editor explains its result, "
                "retains dissent verbatim in substance, and grants no authority."
            ),
            allowed_tools=[],
            allowed_action_version_ids=[],
        )
        editor_agent = self.create_agent(
            workspace_id,
            name=normalized_editor["name"],
            slug=f"{room_slug}-editor-agent",
            role="executor",
            model=normalized_editor["model"],
            instructions=(
                f"BoardRoom purpose: {room_purpose}\n\n"
                f"{normalized_editor['instructions']}"
            ),
            prompt_version_id=editor_prompt["version"]["id"],
            skill_version_ids=[editor_skill["version"]["id"]],
        )
        member_contracts = {
            item["id"]: {"output_schema": BOARDROOM_PARTICIPANT_SCHEMA}
            for item in participant_resources
        }
        fan_schema = fan_out_output_schema(member_contracts)
        editor_input_schema = {
            "type": "object",
            "properties": {
                "members": fan_schema["properties"]["members"],
                "barrier": fan_schema["properties"]["barrier"],
            },
            "required": ["members", "barrier"],
            "additionalProperties": False,
        }
        editor_action = self.create_action(
            workspace_id,
            name=f"{room_name} synthesis",
            slug=f"{room_slug}-synthesis",
            description=(
                "Synthesizes only completed participant records after code has computed "
                "quorum, retaining dissent and open questions as strict data."
            ),
            kind="ai",
            input_schema=editor_input_schema,
            output_schema=BOARDROOM_EDITOR_SCHEMA,
            outcomes=None,
            config={
                "max_tool_calls": 0,
                "reasoning_effort": normalized_editor["reasoning_effort"],
                "max_output_tokens": normalized_editor["max_output_tokens"],
            },
            agent_version_id=editor_agent["version"]["id"],
        )

        nodes: list[dict[str, Any]] = [
            {
                "id": "council",
                "type": "fan_out",
                "version_id": "fanout-v1",
                "input_mapping": {
                    "brief": {"source": "input", "path": "brief"},
                    "context": {"source": "input", "path": "context"},
                },
                "members": [
                    {
                        "id": item["id"],
                        "type": "action",
                        "version_id": item["action"]["version"]["id"],
                    }
                    for item in participant_resources
                ],
                "barrier": {
                    "mode": "quorum",
                    "quorum": quorum,
                    "verdict_path": "verdict",
                    "affirmative_values": ["commit"],
                    "on_member_error": error_policy,
                },
                "position": {"x": 160, "y": 220},
            },
            {
                "id": "editor",
                "type": "action",
                "version_id": editor_action["version"]["id"],
                "input_mapping": {
                    "members": {"source": "step", "node_id": "council", "path": "members"},
                    "barrier": {"source": "step", "node_id": "council", "path": "barrier"},
                },
                "position": {"x": 540, "y": 220},
            },
        ]
        routes: list[dict[str, str]] = [
            {"from": "council", "to": "editor", "outcome": outcome}
            for outcome in ("converged", "review", "error")
        ]

        approval_action: dict[str, Any] | None = None
        write_action: dict[str, Any] | None = None
        final_actions: list[dict[str, Any]] = []

        def publish_final_action(status: str) -> dict[str, Any]:
            action = self.create_action(
                workspace_id,
                name=f"{room_name} {status} result",
                slug=f"{room_slug}-result-{status}",
                description=f"Normalizes the BoardRoom {status} terminal result.",
                kind="transform",
                input_schema=BOARDROOM_RESULT_SCHEMA,
                output_schema=BOARDROOM_RESULT_SCHEMA,
                outcomes=None,
                config={
                    "operation": "map",
                    "mappings": {
                        key: {"source": "input", "path": key}
                        for key in BOARDROOM_RESULT_SCHEMA["properties"]
                    },
                },
                agent_version_id=None,
            )
            final_actions.append(action)
            return action

        def result_mapping(
            *,
            status: str,
            approval_node: str | None = None,
            effect_node: str | None = None,
        ) -> dict[str, Any]:
            mapping: dict[str, Any] = {
                key: {"source": "step", "node_id": "editor", "path": key}
                for key in BOARDROOM_EDITOR_SCHEMA["properties"]
            }
            mapping["status"] = {"source": "literal", "value": status}
            mapping["approval_reason"] = (
                {"source": "step", "node_id": approval_node, "path": "reason"}
                if approval_node
                else {"source": "literal", "value": ""}
            )
            mapping["effect_id"] = (
                {"source": "step", "node_id": effect_node, "path": "effect_id"}
                if effect_node
                else {"source": "literal", "value": ""}
            )
            mapping["collection"] = (
                {"source": "step", "node_id": effect_node, "path": "collection"}
                if effect_node
                else {"source": "literal", "value": ""}
            )
            return mapping

        if approval_mode == "none":
            final = publish_final_action("synthesized")
            nodes.append(
                {
                    "id": "finalize",
                    "type": "action",
                    "version_id": final["version"]["id"],
                    "input_mapping": result_mapping(status="synthesized"),
                    "position": {"x": 900, "y": 220},
                }
            )
            routes.append({"from": "editor", "to": "finalize", "outcome": "success"})
        else:
            approval_action = self.create_action(
                workspace_id,
                name=f"Approve {room_name} decision",
                slug=f"{room_slug}-approval",
                description="Requires a human decision before any BoardRoom write.",
                kind="approval",
                input_schema=BOARDROOM_EDITOR_SCHEMA,
                output_schema={
                    "type": "object",
                    "properties": {
                        "approved": {"type": "boolean"},
                        "reason": {"type": "string", "maxLength": 2_000},
                    },
                    "required": ["approved", "reason"],
                    "additionalProperties": False,
                },
                outcomes=None,
                config={
                    "message_template": (
                        "Approve this BoardRoom decision: {{decision}}\n\n"
                        "Dissent retained: {{dissent}}"
                    ),
                    "max_message_chars": DEFAULT_APPROVAL_MESSAGE_CHARS,
                },
                agent_version_id=None,
            )
            nodes.append(
                {
                    "id": "approval",
                    "type": "action",
                    "version_id": approval_action["version"]["id"],
                    "input_mapping": {
                        key: {"source": "step", "node_id": "editor", "path": key}
                        for key in BOARDROOM_EDITOR_SCHEMA["properties"]
                    },
                    "position": {"x": 860, "y": 220},
                }
            )
            routes.append({"from": "editor", "to": "approval", "outcome": "success"})
            rejected = publish_final_action("rejected")
            nodes.append(
                {
                    "id": "rejected-result",
                    "type": "action",
                    "version_id": rejected["version"]["id"],
                    "input_mapping": result_mapping(
                        status="rejected", approval_node="approval"
                    ),
                    "position": {"x": 1_180, "y": 390},
                }
            )
            routes.append(
                {"from": "approval", "to": "rejected-result", "outcome": "rejected"}
            )
            if normalized_collection is None:
                approved = publish_final_action("approved")
                nodes.append(
                    {
                        "id": "approved-result",
                        "type": "action",
                        "version_id": approved["version"]["id"],
                        "input_mapping": result_mapping(
                            status="approved", approval_node="approval"
                        ),
                        "position": {"x": 1_180, "y": 120},
                    }
                )
                routes.append(
                    {"from": "approval", "to": "approved-result", "outcome": "approved"}
                )
            else:
                write_action = self.create_action(
                    workspace_id,
                    name=f"Write {room_name} decision",
                    slug=f"{room_slug}-write",
                    description="Writes the approved synthesis to the bounded workspace collection.",
                    kind="data_store",
                    input_schema=BOARDROOM_EDITOR_SCHEMA,
                    output_schema={
                        "type": "object",
                        "properties": {
                            "effect_id": {"type": "string"},
                            "collection": {"type": "string"},
                        },
                        "required": ["effect_id", "collection"],
                        "additionalProperties": False,
                    },
                    outcomes=None,
                    config={
                        "operation": "append_record",
                        "collection": normalized_collection,
                        "write_enabled": True,
                    },
                    agent_version_id=None,
                )
                nodes.append(
                    {
                        "id": "write",
                        "type": "action",
                        "version_id": write_action["version"]["id"],
                        "input_mapping": {
                            key: {"source": "step", "node_id": "editor", "path": key}
                            for key in BOARDROOM_EDITOR_SCHEMA["properties"]
                        },
                        "position": {"x": 1_160, "y": 120},
                    }
                )
                routes.append({"from": "approval", "to": "write", "outcome": "approved"})
                written = publish_final_action("written")
                nodes.append(
                    {
                        "id": "written-result",
                        "type": "action",
                        "version_id": written["version"]["id"],
                        "input_mapping": result_mapping(
                            status="written",
                            approval_node="approval",
                            effect_node="write",
                        ),
                        "position": {"x": 1_480, "y": 120},
                    }
                )
                routes.append({"from": "write", "to": "written-result", "outcome": "success"})

        flow = self.create_studio_flow(
            workspace_id,
            name=room_name,
            slug=room_slug,
            description=room_purpose,
            input_schema=BOARDROOM_INPUT_SCHEMA,
            output_schema=BOARDROOM_RESULT_SCHEMA,
            outcomes=None,
            start_node_id="council",
            nodes=nodes,
            routes=routes,
        )
        return {
            "name": room_name,
            "slug": room_slug,
            "purpose": room_purpose,
            "participant_resources": participant_resources,
            "editor": {
                "prompt": editor_prompt,
                "skill": editor_skill,
                "agent": editor_agent,
                "action": editor_action,
            },
            "approval_action": approval_action,
            "write_action": write_action,
            "final_actions": final_actions,
            "flow": flow,
            "runtime": {
                "composition": "fan_out",
                "parallel_members": len(participant_resources),
                "barrier_mode": "quorum",
                "quorum": quorum,
                "error_policy": error_policy,
                "approval_mode": approval_mode,
                "write_collection": normalized_collection,
            },
        }

    def check_brake(self, workspace_id: str, flow_id: str) -> dict[str, Any]:
        """Report, without writing, whether a canonical dead end VETOES this Flow.

        The verdict is scoped to the Flow's current pinned version, never to a
        traversal path: the path a Run takes is decided by data that does not
        exist yet. The verdict carries `fault_classes` so a reader can audit
        which failures are allowed to ratify in the first place.
        """

        context = self.studio.flow_context(workspace_id, flow_id)
        return self.studio.check_brake(
            workspace_id, flow_version_id=context["version"]["id"]
        )

    def list_dead_ends(self, workspace_id: str) -> list[dict[str, Any]]:
        return self.studio.list_dead_ends(workspace_id)

    def list_principles(self, workspace_id: str) -> list[dict[str, Any]]:
        """Return every distilled rule. A principle advises; it never refuses."""

        return self.studio.list_principles(workspace_id)

    # -- Capability Forge -------------------------------------------------

    def draft_skill_candidate(
        self,
        workspace_id: str,
        *,
        source_run_id: Any,
        source_model_call_id: Any,
        distiller_agent_version_id: Any,
        client: ResponseTransport,
    ) -> dict[str, Any]:
        """Distil one completed model Step into a quarantined candidate."""

        run_id = require_string(source_run_id, "source Run id", maximum=80)
        model_call_id = require_string(
            source_model_call_id, "source model call id", maximum=80
        )
        distiller_id = require_string(
            distiller_agent_version_id,
            "distiller Agent version id",
            maximum=80,
        )
        source = self.studio.skill_candidate_source(
            workspace_id, run_id, model_call_id
        )
        distiller = self.studio.get_agent_runtime(workspace_id, distiller_id)
        if distiller["agent_id"] == source["source_agent"]["agent_id"]:
            raise ContractViolation(
                "Skill candidate distiller must be independent from the source Agent"
            )
        payload = build_distillation_payload(
            distiller_agent=distiller, source=source["material"]
        )
        input_hash = fingerprint(payload)
        try:
            response = client.create(payload)
        except ProviderFailure as error:
            request_id = error.detail.get("request_id")
            safe_request_id = (
                request_id
                if isinstance(request_id, str) and 0 < len(request_id) <= 128
                else None
            )
            self.studio.record_skill_distillation_call(
                workspace_id,
                source_run_id=run_id,
                source_step_id=source["model_call"]["step_id"],
                source_model_call_id=model_call_id,
                distiller_agent_version_id=distiller_id,
                provider_response_id=safe_request_id or "unavailable",
                status="failed",
                model=distiller["model"],
                input_hash=input_hash,
                output_hash=fingerprint(
                    {
                        "error_code": error.code,
                        "provider_code": error.detail.get("provider_code"),
                    }
                ),
                usage={},
                request_id=safe_request_id,
            )
            raise
        summary = safe_response_summary(response)
        request_id = response.get("_request_id")
        safe_request_id = (
            str(request_id)[:128] if isinstance(request_id, str) and request_id else None
        )
        distillation_call = self.studio.record_skill_distillation_call(
            workspace_id,
            source_run_id=run_id,
            source_step_id=source["model_call"]["step_id"],
            source_model_call_id=model_call_id,
            distiller_agent_version_id=distiller_id,
            provider_response_id=summary["provider_response_id"],
            status=("completed" if summary["status"] == "completed" else "failed"),
            model=summary["model"],
            input_hash=input_hash,
            output_hash=fingerprint(response),
            usage=summary["usage"],
            request_id=safe_request_id,
        )
        require_completed_response(
            response,
            max_output_tokens=payload.get("max_output_tokens"),
            operation="OpenAI Skill distillation response",
        )
        candidate = parse_candidate(response, source["material"])
        return self.studio.create_skill_candidate(
            workspace_id,
            source_run_id=run_id,
            source_step_id=source["model_call"]["step_id"],
            source_model_call_id=model_call_id,
            source_agent_version_id=source["source_agent"]["id"],
            distiller_agent_version_id=distiller_id,
            distillation_model_call_id=distillation_call["id"],
            name=candidate["name"],
            instructions=candidate["instructions"],
            rationale=candidate["rationale"],
            evidence_event_ids=candidate["evidence_event_ids"],
            source_snapshot_hash=source["snapshot_hash"],
        )

    def qualify_skill_candidate(
        self, workspace_id: str, candidate_id: Any
    ) -> dict[str, Any]:
        return self.studio.qualify_skill_candidate(
            workspace_id,
            require_string(candidate_id, "Skill candidate id", maximum=80),
        )

    def promote_skill_candidate(
        self,
        workspace_id: str,
        candidate_id: Any,
        *,
        name: Any,
        slug: Any,
        actor: Any,
        reason: Any,
        acknowledged: Any,
    ) -> dict[str, Any]:
        if acknowledged is not True:
            raise ContractViolation("Skill promotion requires explicit acknowledgement")
        return self.studio.promote_skill_candidate(
            workspace_id,
            require_string(candidate_id, "Skill candidate id", maximum=80),
            name=require_string(name, "Skill name", maximum=100),
            slug=require_slug(slug, "Skill slug"),
            actor=require_string(actor, "promotion actor", maximum=100),
            reason=require_string(
                reason, "promotion reason", minimum=12, maximum=600
            ),
        )

    def reject_skill_candidate(
        self,
        workspace_id: str,
        candidate_id: Any,
        *,
        actor: Any,
        reason: Any,
        acknowledged: Any,
    ) -> dict[str, Any]:
        if acknowledged is not True:
            raise ContractViolation("Skill rejection requires explicit acknowledgement")
        return self.studio.reject_skill_candidate(
            workspace_id,
            require_string(candidate_id, "Skill candidate id", maximum=80),
            actor=require_string(actor, "rejection actor", maximum=100),
            reason=require_string(
                reason, "rejection reason", minimum=12, maximum=600
            ),
        )

    def create_studio_trigger(
        self,
        workspace_id: str,
        flow_id: str,
        *,
        name: Any,
        trigger_type: Any,
        config: Any,
    ) -> dict[str, Any]:
        normalized_type = require_string(
            trigger_type, "trigger type", maximum=24
        )
        if normalized_type not in {"webhook", "schedule"}:
            raise ContractViolation("trigger type must be webhook or schedule")
        if not isinstance(config, dict):
            raise ContractViolation("trigger config must be an object")
        normalized_config = json.loads(json.dumps(config))
        if normalized_type == "webhook":
            if normalized_config:
                raise ContractViolation("webhook trigger config must be empty")
        else:
            if set(normalized_config) != {"interval_minutes", "input"}:
                raise ContractViolation("schedule trigger config is invalid")
            interval = normalized_config["interval_minutes"]
            if (
                not isinstance(interval, int)
                or isinstance(interval, bool)
                or not 5 <= interval <= 10_080
            ):
                raise ContractViolation(
                    "schedule interval must be between five minutes and seven days"
                )
            if not isinstance(normalized_config["input"], dict):
                raise ContractViolation("schedule input must be an object")
        return self.studio.create_trigger(
            workspace_id,
            flow_id,
            name=require_string(name, "trigger name", maximum=100),
            trigger_type=normalized_type,
            config=normalized_config,
        )

    def set_studio_trigger_enabled(
        self,
        workspace_id: str,
        trigger_id: str,
        *,
        enabled: Any,
        expected_revision: Any,
    ) -> dict[str, Any]:
        if not isinstance(enabled, bool):
            raise ContractViolation("trigger enabled state must be a boolean")
        if (
            not isinstance(expected_revision, int)
            or isinstance(expected_revision, bool)
            or expected_revision < 1
        ):
            raise ContractViolation("trigger expected revision is invalid")
        return self.studio.set_trigger_enabled(
            workspace_id,
            require_string(trigger_id, "trigger id", maximum=80),
            enabled=enabled,
            expected_revision=expected_revision,
        )

    def fire_studio_webhook(
        self, secret: Any, input_data: Any
    ) -> dict[str, Any]:
        normalized_secret = require_string(secret, "webhook token", maximum=128)
        if not isinstance(input_data, dict):
            raise ContractViolation("webhook payload must be an object")
        trigger = self.studio.resolve_webhook(normalized_secret)
        if trigger["requires_model"]:
            run = self.studio_runtime.prepare(
                trigger["workspace_id"],
                trigger["flow_id"],
                input_data=input_data,
                flow_version=trigger["flow_version"],
                idempotency_key=f"webhook:{trigger['id']}:{new_id('delivery')}",
            )
            self.studio.append_event(
                trigger["workspace_id"],
                run["id"],
                event_type="run.credential_required",
                actor_type="runtime",
                actor_id=None,
                payload={"trigger_id": trigger["id"], "reason": "browser_byok"},
            )
            run = self.studio.get_run(trigger["workspace_id"], run["id"])
        else:
            run = self.studio_runtime.execute(
                trigger["workspace_id"],
                trigger["flow_id"],
                input_data=input_data,
                flow_version=trigger["flow_version"],
                idempotency_key=f"webhook:{trigger['id']}:{new_id('delivery')}",
            )
        self.studio.mark_trigger_fired(trigger["id"])
        return {"trigger_id": trigger["id"], "run": run}

    def fire_due_studio_schedules(self) -> list[dict[str, Any]]:
        """Claim due schedules once; model-backed Runs wait for a visitor credential."""

        results: list[dict[str, Any]] = []
        for trigger in self.studio.claim_due_schedules():
            idempotency_key = (
                f"schedule:{trigger['id']}:{trigger['next_fire_at']}"
            )
            if trigger["requires_model"]:
                run = self.studio_runtime.prepare(
                    trigger["workspace_id"],
                    trigger["flow_id"],
                    input_data=trigger["config"]["input"],
                    flow_version=trigger["flow_version"],
                    idempotency_key=idempotency_key,
                )
                if run["status"] == "created":
                    self.studio.append_event(
                        trigger["workspace_id"],
                        run["id"],
                        event_type="run.credential_required",
                        actor_type="runtime",
                        actor_id=None,
                        payload={"trigger_id": trigger["id"], "reason": "browser_byok"},
                    )
                    run = self.studio.get_run(trigger["workspace_id"], run["id"])
            else:
                run = self.studio_runtime.execute(
                    trigger["workspace_id"],
                    trigger["flow_id"],
                    input_data=trigger["config"]["input"],
                    flow_version=trigger["flow_version"],
                    idempotency_key=idempotency_key,
                )
            results.append({"trigger_id": trigger["id"], "run": run})
        return results

    def studio_flow_model_call_forecast(
        self,
        workspace_id: str,
        flow_id: str,
        *,
        version: int | None = None,
        start_node_id: str | None = None,
    ) -> int:
        context = self.studio.flow_context(workspace_id, flow_id, version)
        flow_version = context["version"]
        start = start_node_id or flow_version["start_node_id"]
        nodes = {node["id"]: node for node in flow_version["nodes"]}
        if start is None or start not in nodes:
            return 0
        completion_calls = 1 if flow_version["judge_agent_version_id"] else 0
        adjacency: dict[str, list[str]] = {node_id: [] for node_id in nodes}
        for route in flow_version["routes"]:
            adjacency[route["from"]].append(route["to"])
        weights: dict[str, int] = {}

        def target_weight(target: Mapping[str, Any]) -> int:
            if target["type"] == "agent":
                return 1
            if target["type"] == "flow":
                child = self.studio.get_flow_version_by_id(
                    workspace_id, target["version_id"]
                )
                return self.studio_flow_model_call_forecast(
                    workspace_id,
                    child["flow_id"],
                    version=int(child["version"]),
                )
            action = self.studio.get_action_version(
                workspace_id, target["version_id"]
            )
            return (
                int(action["config"].get("max_tool_calls", 0)) + 1
                if action["kind"] == "ai"
                else 0
            )

        for node_id, node in nodes.items():
            if node["type"] == "fan_out":
                weights[node_id] = sum(
                    target_weight(member) for member in node["members"]
                )
                continue
            weights[node_id] = target_weight(node)
        memo: dict[str, int] = {}

        def maximum_path(node_id: str) -> int:
            if node_id in memo:
                return memo[node_id]
            following = adjacency[node_id]
            downstream = max((maximum_path(item) for item in following), default=0)
            memo[node_id] = weights[node_id] + downstream
            return memo[node_id]

        # A Goal-Judge is cast by the Flow at its terminal stop seam, not by a
        # graph node. Charge it in addition to the maximum executable node path
        # so the HTTP boundary keeps the browser-owned client and reserves the
        # call before execution. Recursive subflow forecasts carry their own
        # terminal judge into the parent node's weight by the same rule.
        return maximum_path(start) + completion_calls

    def studio_approval_model_call_forecast(
        self, workspace_id: str, request_id: str
    ) -> int:
        approval = self.studio.get_approval_request(workspace_id, request_id)
        run = self.studio.get_run(workspace_id, approval["run_id"])
        return self.studio_flow_model_call_forecast(
            workspace_id,
            run["flow_id"],
            version=int(run["flow_version"]),
            start_node_id=run["current_node_id"],
        )

    def studio_rerun_model_call_forecast(
        self, workspace_id: str, run_id: str
    ) -> int:
        run = self.studio.get_run(workspace_id, run_id)
        return self.studio_flow_model_call_forecast(
            workspace_id, run["flow_id"], version=int(run["flow_version"])
        )

    def studio_continue_model_call_forecast(
        self, workspace_id: str, run_id: str
    ) -> int:
        run = self.studio.get_run(workspace_id, run_id)
        return self.studio_flow_model_call_forecast(
            workspace_id,
            run["flow_id"],
            version=int(run["flow_version"]),
            start_node_id=run["current_node_id"],
        )

    def create_agent(
        self,
        workspace_id: str,
        *,
        name: Any,
        slug: Any,
        role: Any,
        model: Any,
        instructions: Any,
        prompt_version_id: Any,
        skill_version_ids: Any,
    ) -> dict[str, Any]:
        normalized_role = require_string(role, "agent role", maximum=32)
        if normalized_role not in ROLE_NAMES:
            raise ContractViolation("agent role is not supported")
        normalized_model = require_string(model, "agent model", maximum=64)
        if normalized_model not in SUPPORTED_MODELS:
            raise ContractViolation("agent model is not supported")
        skills = require_string_list(
            skill_version_ids,
            "agent skill version ids",
            maximum_items=8,
            maximum_item_length=80,
        )
        return self.store.create_agent(
            workspace_id,
            name=require_string(name, "agent name", maximum=100),
            slug=require_slug(slug),
            role=normalized_role,
            model=normalized_model,
            instructions=require_string(instructions, "agent instructions", maximum=8_000),
            prompt_version_id=require_string(
                prompt_version_id, "prompt version id", maximum=80
            ),
            skill_version_ids=skills,
        )

    def revise_agent(
        self,
        workspace_id: str,
        agent_id: str,
        *,
        expected_version: Any,
        name: Any,
        role: Any,
        model: Any,
        instructions: Any,
        prompt_version_id: Any,
        skill_version_ids: Any,
    ) -> dict[str, Any]:
        expected = self._expected_resource_version(expected_version, "Agent")
        normalized_role = require_string(role, "agent role", maximum=32)
        if normalized_role not in ROLE_NAMES:
            raise ContractViolation("agent role is not supported")
        normalized_model = require_string(model, "agent model", maximum=64)
        if normalized_model not in SUPPORTED_MODELS:
            raise ContractViolation("agent model is not supported")
        return self.store.revise_agent(
            workspace_id,
            agent_id,
            expected_version=expected,
            name=require_string(name, "agent name", maximum=100),
            role=normalized_role,
            model=normalized_model,
            instructions=require_string(
                instructions, "agent instructions", maximum=8_000
            ),
            prompt_version_id=require_string(
                prompt_version_id, "prompt version id", maximum=80
            ),
            skill_version_ids=require_string_list(
                skill_version_ids,
                "agent skill version ids",
                maximum_items=8,
                maximum_item_length=80,
            ),
        )

    def create_flow(
        self,
        workspace_id: str,
        *,
        name: Any,
        slug: Any,
        executor_agent_version_id: Any,
        diagnostician_agent_version_id: Any,
        repairer_agent_version_id: Any,
        request: Any,
        policy: Any,
        repair_policy: Any,
    ) -> dict[str, Any]:
        normalized_request, normalized_policy, normalized_repair_policy = self._flow_contract(
            request, policy, repair_policy
        )
        return self.store.create_flow(
            workspace_id,
            name=require_string(name, "flow name", maximum=100),
            slug=require_slug(slug),
            executor_agent_version_id=require_string(
                executor_agent_version_id, "executor agent version id", maximum=80
            ),
            diagnostician_agent_version_id=require_string(
                diagnostician_agent_version_id,
                "diagnostician agent version id",
                maximum=80,
            ),
            repairer_agent_version_id=require_string(
                repairer_agent_version_id, "repairer agent version id", maximum=80
            ),
            request=normalized_request,
            policy=normalized_policy,
            repair_policy=normalized_repair_policy,
        )

    def _flow_contract(
        self, request: Any, policy: Any, repair_policy: Any
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        if not isinstance(request, dict) or set(request) != {"goal", "artifact", "environment"}:
            raise ContractViolation("flow request must contain exactly goal, artifact, and environment")
        normalized_request = {
            "goal": require_string(request["goal"], "flow goal", maximum=500),
            "artifact": require_string(request["artifact"], "flow artifact", maximum=160),
            "environment": require_string(
                request["environment"], "flow environment", maximum=32
            ),
        }
        if normalized_request["environment"] not in {"staging", "production"}:
            raise ContractViolation("flow environment is not supported")
        if not isinstance(policy, dict) or set(policy) != {"allowed_environments"}:
            raise ContractViolation("flow policy must contain only allowed_environments")
        allowed = require_string_list(
            policy["allowed_environments"],
            "allowed environments",
            maximum_items=2,
            maximum_item_length=32,
            allow_empty=False,
        )
        if not set(allowed).issubset({"staging", "production"}):
            raise ContractViolation("flow policy contains an unsupported environment")
        if not isinstance(repair_policy, dict) or set(repair_policy) != {
            "allowed_paths",
            "allowed_operations",
            "max_operations",
        }:
            raise ContractViolation("repair policy has an invalid shape")
        if repair_policy.get("allowed_paths") != ["/policy/allowed_environments"]:
            raise ContractViolation("repair policy path is not supported")
        if repair_policy.get("allowed_operations") != ["replace"]:
            raise ContractViolation("repair policy operation is not supported")
        if repair_policy.get("max_operations") != 1:
            raise ContractViolation("repair policy must allow exactly one operation")
        return (
            normalized_request,
            {"allowed_environments": allowed},
            {
                "allowed_paths": ["/policy/allowed_environments"],
                "allowed_operations": ["replace"],
                "max_operations": 1,
            },
        )

    def run_flow(
        self,
        workspace_id: str,
        flow_id: str,
        *,
        client: ResponseTransport | None = None,
    ) -> dict[str, Any]:
        return self._runtime(client).execute(workspace_id, flow_id)

    def diagnose_run(
        self,
        workspace_id: str,
        run_id: str,
        *,
        client: ResponseTransport | None = None,
    ) -> dict[str, Any]:
        return self._runtime(client).diagnose(workspace_id, run_id)

    def propose_repair(
        self,
        workspace_id: str,
        diagnosis_id: str,
        *,
        client: ResponseTransport | None = None,
    ) -> dict[str, Any]:
        return self._runtime(client).propose_repair(workspace_id, diagnosis_id)

    def apply_repair(
        self,
        workspace_id: str,
        repair_id: str,
        *,
        proposal_hash: Any,
        expected_flow_revision: Any,
        actor: Any,
        reason: Any,
        acknowledged: Any,
    ) -> dict[str, Any]:
        normalized_hash = require_string(proposal_hash, "proposal hash", maximum=64)
        if not HEX_64_RE.fullmatch(normalized_hash):
            raise ContractViolation("proposal hash must be 64 lowercase hexadecimal characters")
        if not isinstance(expected_flow_revision, int) or isinstance(expected_flow_revision, bool):
            raise ContractViolation("expected flow revision must be an integer")
        if acknowledged is not True:
            raise ContractViolation("repair acknowledgement is required")
        return self.store.apply_repair(
            workspace_id,
            repair_id,
            proposal_hash=normalized_hash,
            expected_flow_revision=expected_flow_revision,
            actor=require_string(actor, "approval actor", maximum=100),
            reason=require_string(reason, "approval reason", minimum=12, maximum=500),
            acknowledged=True,
        )

    def rerun(
        self,
        workspace_id: str,
        run_id: str,
        *,
        client: ResponseTransport | None = None,
    ) -> dict[str, Any]:
        target = self.store.rerun_target(workspace_id, run_id)
        return self._runtime(client).execute(workspace_id, **target)

    def existing_rerun(self, workspace_id: str, run_id: str) -> dict[str, Any] | None:
        return self.store.existing_child_run(workspace_id, run_id)

    def get_run(self, workspace_id: str, run_id: str) -> dict[str, Any]:
        return self.store.get_run(workspace_id, run_id)

    def get_flow(self, workspace_id: str, flow_id: str) -> dict[str, Any]:
        return self.store.get_flow(workspace_id, flow_id)

    def get_flow_version(
        self, workspace_id: str, flow_id: str, version: int
    ) -> dict[str, Any]:
        return self.store.get_flow_version(workspace_id, flow_id, version)
