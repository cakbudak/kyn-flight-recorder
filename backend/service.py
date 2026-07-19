"""Product control plane: the sole mutation API used by HTTP and tests."""

from __future__ import annotations

import re
import json
from typing import Any, Callable

from .contracts import (
    ContractViolation,
    PLACEHOLDER_RE,
    normalize_json_schema,
    require_slug,
    require_string,
    require_string_list,
    render_prompt,
)
from .runtime import AgentRuntime, ResponseTransport
from .store import Store
from .studio_runtime import StudioRuntime, validate_flow_definition
from .studio_store import StudioStore
from .tools import ToolRegistry


SUPPORTED_MODELS = frozenset({"gpt-5.6", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"})
ROLE_NAMES = frozenset({"executor", "diagnostician", "repairer"})
HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")


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
        self.studio_runtime = StudioRuntime(self.studio, client)

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
        return StudioRuntime(self.studio, client)

    def create_workspace(self, *, seed: bool = True) -> dict[str, Any]:
        workspace = self.store.create_workspace()
        if seed:
            self.store.seed_default_lab(workspace["id"], model=self.default_model)
            self.studio.seed_default(workspace["id"], model=self.default_model)
        return {
            "workspace_id": workspace["id"],
            "workspace_token": workspace["token"],
            "snapshot": self.snapshot(workspace["id"]),
        }

    def resolve_workspace(self, token: str) -> str:
        return self.store.resolve_workspace(token)

    def snapshot(self, workspace_id: str) -> dict[str, Any]:
        snapshot = self.store.workspace_snapshot(workspace_id)
        snapshot["studio"] = self.studio.snapshot(workspace_id)
        return snapshot

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
            "condition": "none",
            "approval": "approval",
            "sandbox": "sandbox_write",
        }
        if normalized_kind not in effect_levels:
            raise ContractViolation("Action kind is not supported")
        properties = set(normalized_input["properties"])
        if normalized_kind == "ai":
            if normalized_agent is None or set(normalized_config) != {
                "max_tool_calls",
                "reasoning_effort",
            }:
                raise ContractViolation("AI Action config or Agent pin is invalid")
            max_calls = normalized_config["max_tool_calls"]
            if not isinstance(max_calls, int) or isinstance(max_calls, bool) or not 0 <= max_calls <= 4:
                raise ContractViolation("AI Action max_tool_calls must be between zero and four")
            if normalized_config["reasoning_effort"] not in {"low", "medium", "high"}:
                raise ContractViolation("AI Action reasoning_effort is invalid")
            agent = self.studio.get_agent_runtime(workspace_id, normalized_agent)
            if set(agent["prompt"]["variables"]) != properties:
                raise ContractViolation(
                    "AI Action input properties must exactly match its pinned Prompt variables"
                )
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
        elif normalized_kind == "approval":
            if normalized_agent is not None or set(normalized_config) != {"message_template"}:
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
        else:
            if normalized_agent is not None or set(normalized_config) != {
                "operation",
                "collection",
            }:
                raise ContractViolation("sandbox Action config is invalid")
            if normalized_config["operation"] != "append_record":
                raise ContractViolation("sandbox Action operation is not supported")
            normalized_config["collection"] = require_slug(
                normalized_config["collection"], "sandbox collection"
            )
        return self.studio.create_action(
            workspace_id,
            name=require_string(name, "Action name", maximum=100),
            slug=require_slug(slug),
            description=require_string(description, "Action description", maximum=500),
            kind=normalized_kind,
            input_schema=normalized_input,
            output_schema=normalized_output,
            config=normalized_config,
            agent_version_id=normalized_agent,
            effect_level=effect_levels[normalized_kind],
        )

    def create_studio_flow(
        self,
        workspace_id: str,
        *,
        name: Any,
        slug: Any,
        description: Any,
        input_schema: Any,
        start_node_id: Any,
        nodes: Any,
        routes: Any,
    ) -> dict[str, Any]:
        normalized_schema = normalize_json_schema(input_schema, "Flow input schema")
        if normalized_schema["type"] != "object":
            raise ContractViolation("Flow input schema must be an object")
        start, normalized_nodes, normalized_routes = validate_flow_definition(
            start_node_id=start_node_id,
            nodes=nodes,
            routes=routes,
        )
        for node in normalized_nodes:
            if node["type"] == "action":
                target = self.studio.get_action_version(workspace_id, node["version_id"])
                expected = target["input_schema"]
            else:
                agent = self.studio.get_agent_runtime(workspace_id, node["version_id"])
                expected = {
                    "properties": {
                        variable: {} for variable in agent["prompt"]["variables"]
                    },
                    "required": list(agent["prompt"]["variables"]),
                }
            mapped = set(node["input_mapping"])
            properties = set(expected["properties"])
            required = set(expected["required"])
            if not required.issubset(mapped) or not mapped.issubset(properties):
                raise ContractViolation(
                    f"Flow node {node['id']} mapping does not satisfy its pinned input contract"
                )
        return self.studio.create_flow(
            workspace_id,
            name=require_string(name, "Flow name", maximum=100),
            slug=require_slug(slug),
            description=require_string(description, "Flow description", maximum=500),
            input_schema=normalized_schema,
            start_node_id=start,
            nodes=normalized_nodes,
            routes=normalized_routes,
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
        return self._studio_runtime(client).resume_after_approval(workspace_id, run_id)

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
            correlation_id=parent["correlation_id"],
            idempotency_key=f"rerun:{parent['id']}:{key}",
        )

    def get_studio_run(self, workspace_id: str, run_id: str) -> dict[str, Any]:
        return self.studio.get_run(workspace_id, run_id)

    def get_studio_action(self, workspace_id: str, action_id: str) -> dict[str, Any]:
        return self.studio.get_action(workspace_id, action_id)

    def get_studio_flow(self, workspace_id: str, flow_id: str) -> dict[str, Any]:
        return self.studio.get_flow(workspace_id, flow_id)

    def studio_snapshot(self, workspace_id: str) -> dict[str, Any]:
        return self.studio.snapshot(workspace_id)

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
        adjacency: dict[str, list[str]] = {node_id: [] for node_id in nodes}
        for route in flow_version["routes"]:
            adjacency[route["from"]].append(route["to"])
        weights: dict[str, int] = {}
        for node_id, node in nodes.items():
            if node["type"] == "agent":
                weights[node_id] = 1
                continue
            action = self.studio.get_action_version(workspace_id, node["version_id"])
            weights[node_id] = (
                int(action["config"].get("max_tool_calls", 0)) + 1
                if action["kind"] == "ai"
                else 0
            )
        memo: dict[str, int] = {}

        def maximum_path(node_id: str) -> int:
            if node_id in memo:
                return memo[node_id]
            following = adjacency[node_id]
            downstream = max((maximum_path(item) for item in following), default=0)
            memo[node_id] = weights[node_id] + downstream
            return memo[node_id]

        return maximum_path(start)

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
