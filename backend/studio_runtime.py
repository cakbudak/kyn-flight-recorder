"""Version-pinned Action/Agent graph runtime for Kyn.ist Agent Studio."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .contracts import (
    PLACEHOLDER_RE,
    Conflict,
    ContractViolation,
    ProviderFailure,
    canonical_json,
    extract_output_text,
    fingerprint,
    function_calls,
    render_prompt,
    require_string,
    safe_response_summary,
    validate_json_schema,
)
from .runtime import ResponseTransport
from .studio_store import StudioStore


NODE_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
ROUTE_OUTCOMES = frozenset({"success", "true", "false", "approved", "rejected"})
CALLABLE_ACTION_KINDS = frozenset({"template", "condition", "sandbox"})


@dataclass(frozen=True)
class ActionResult:
    output: Any
    route_outcome: str
    paused: bool = False
    approval_message: str | None = None


def validate_flow_definition(
    *,
    start_node_id: Any,
    nodes: Any,
    routes: Any,
) -> tuple[str, list[dict[str, Any]], list[dict[str, str]]]:
    start = require_string(start_node_id, "Flow start node", maximum=64)
    if not NODE_ID_RE.fullmatch(start):
        raise ContractViolation("Flow start node has an invalid id")
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 12:
        raise ContractViolation("Flow must contain between one and twelve nodes")
    normalized_nodes: list[dict[str, Any]] = []
    ids: set[str] = set()
    for index, node in enumerate(nodes):
        if not isinstance(node, dict) or set(node) != {
            "id",
            "type",
            "version_id",
            "input_mapping",
        }:
            raise ContractViolation(f"Flow node {index} has an invalid shape")
        node_id = require_string(node["id"], f"Flow node {index} id", maximum=64)
        if not NODE_ID_RE.fullmatch(node_id) or node_id in ids:
            raise ContractViolation("Flow node ids must be unique lowercase slugs")
        ids.add(node_id)
        node_type = node["type"]
        if node_type not in {"action", "agent"}:
            raise ContractViolation("Flow node type must be action or agent")
        version_id = require_string(
            node["version_id"], f"Flow node {node_id} version", maximum=80
        )
        mapping = node["input_mapping"]
        if not isinstance(mapping, dict) or len(mapping) > 32:
            raise ContractViolation(f"Flow node {node_id} input mapping is invalid")
        normalized_mapping: dict[str, dict[str, Any]] = {}
        for target, source in mapping.items():
            if not isinstance(target, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", target):
                raise ContractViolation(f"Flow node {node_id} mapping target is invalid")
            if not isinstance(source, dict) or source.get("source") not in {
                "input",
                "step",
                "literal",
            }:
                raise ContractViolation(f"Flow node {node_id} mapping source is invalid")
            source_kind = source["source"]
            if source_kind == "literal":
                if set(source) != {"source", "value"}:
                    raise ContractViolation(f"Flow node {node_id} literal mapping is invalid")
                normalized_mapping[target] = {
                    "source": "literal",
                    "value": json.loads(canonical_json(source["value"])),
                }
            elif source_kind == "input":
                if set(source) != {"source", "path"}:
                    raise ContractViolation(f"Flow node {node_id} input mapping is invalid")
                normalized_mapping[target] = {
                    "source": "input",
                    "path": _normalize_path(source["path"], f"Flow node {node_id} input path"),
                }
            else:
                if set(source) != {"source", "node_id", "path"}:
                    raise ContractViolation(f"Flow node {node_id} Step mapping is invalid")
                source_node = require_string(
                    source["node_id"], f"Flow node {node_id} source node", maximum=64
                )
                normalized_mapping[target] = {
                    "source": "step",
                    "node_id": source_node,
                    "path": _normalize_path(source["path"], f"Flow node {node_id} Step path"),
                }
        normalized_nodes.append(
            {
                "id": node_id,
                "type": node_type,
                "version_id": version_id,
                "input_mapping": normalized_mapping,
            }
        )
    if start not in ids:
        raise ContractViolation("Flow start node does not exist")
    if not isinstance(routes, list) or len(routes) > 24:
        raise ContractViolation("Flow routes are invalid or exceed the limit")
    normalized_routes: list[dict[str, str]] = []
    unique_routes: set[tuple[str, str]] = set()
    adjacency: dict[str, list[str]] = {node_id: [] for node_id in ids}
    for index, route in enumerate(routes):
        if not isinstance(route, dict) or set(route) != {"from", "to", "outcome"}:
            raise ContractViolation(f"Flow route {index} has an invalid shape")
        source = require_string(route["from"], "Flow route source", maximum=64)
        target = require_string(route["to"], "Flow route target", maximum=64)
        outcome = require_string(route["outcome"], "Flow route outcome", maximum=16)
        if source not in ids or target not in ids or source == target:
            raise ContractViolation("Flow route references invalid nodes")
        if outcome not in ROUTE_OUTCOMES:
            raise ContractViolation("Flow route outcome is unsupported")
        if (source, outcome) in unique_routes:
            raise ContractViolation("Flow has an ambiguous route for one outcome")
        unique_routes.add((source, outcome))
        adjacency[source].append(target)
        normalized_routes.append({"from": source, "to": target, "outcome": outcome})

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visiting:
            raise ContractViolation("Flow graph must be acyclic")
        if node_id in visited:
            return
        visiting.add(node_id)
        for target in adjacency[node_id]:
            visit(target)
        visiting.remove(node_id)
        visited.add(node_id)

    visit(start)
    if visited != ids:
        raise ContractViolation("Every Flow node must be reachable from the start node")

    def reaches(source: str, target: str) -> bool:
        pending = [source]
        seen: set[str] = set()
        while pending:
            current = pending.pop()
            if current == target:
                return True
            if current in seen:
                continue
            seen.add(current)
            pending.extend(adjacency[current])
        return False

    for node in normalized_nodes:
        for source in node["input_mapping"].values():
            if source["source"] != "step":
                continue
            source_node = source["node_id"]
            if source_node not in ids or not reaches(source_node, node["id"]):
                raise ContractViolation(
                    f"Flow node {node['id']} may read only a reachable predecessor Step"
                )
    return start, normalized_nodes, normalized_routes


def _normalize_path(value: Any, field: str) -> str:
    path = require_string(value, field, maximum=160)
    if not re.fullmatch(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*|\.[0-9]+)*", path):
        raise ContractViolation(f"{field} is invalid")
    return path


def _value_at(root: Any, path: str, *, field: str) -> Any:
    current = root
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            raise ContractViolation(f"{field} path {path} was not produced")
    return json.loads(canonical_json(current))


class StudioRuntime:
    def __init__(
        self,
        repository: StudioStore,
        client: ResponseTransport,
        *,
        max_output_tokens: int = 1_500,
    ) -> None:
        self.repository = repository
        self.client = client
        self.max_output_tokens = max_output_tokens

    def execute(
        self,
        workspace_id: str,
        flow_id: str,
        *,
        input_data: Mapping[str, Any],
        flow_version: int | None = None,
        parent_run_id: str | None = None,
        correlation_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        context = self.repository.flow_context(workspace_id, flow_id, flow_version)
        validated_input = validate_json_schema(
            dict(input_data), context["version"]["input_schema"], "Run input"
        )
        run_id, created = self.repository.create_run(
            workspace_id,
            flow_id,
            input_data=validated_input,
            flow_version=int(context["version"]["version"]),
            parent_run_id=parent_run_id,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
        )
        if not created:
            return self.repository.get_run(workspace_id, run_id)
        self.repository.transition_run(
            workspace_id,
            run_id,
            status="running",
            current_node_id=context["version"]["start_node_id"],
        )
        return self._drive(workspace_id, run_id)

    def resume_after_approval(self, workspace_id: str, run_id: str) -> dict[str, Any]:
        run = self.repository.get_run(workspace_id, run_id)
        if run["status"] != "running":
            return run
        return self._drive(workspace_id, run_id)

    def _drive(self, workspace_id: str, run_id: str) -> dict[str, Any]:
        run = self.repository.get_run(workspace_id, run_id)
        context = self.repository.flow_context(
            workspace_id, run["flow_id"], int(run["flow_version"])
        )
        nodes = {node["id"]: node for node in context["version"]["nodes"]}
        routes = context["version"]["routes"]
        completed_outputs = {
            step["node_id"]: step["output"]
            for step in run["steps"]
            if step["status"] == "completed"
        }
        node_id = run["current_node_id"]
        last_output = run["output"]
        traversed = 0
        while node_id is not None:
            traversed += 1
            if traversed > len(nodes) + 1:
                return self._fail_run(
                    workspace_id,
                    run_id,
                    "flow_traversal_exhausted",
                    "Flow traversal exceeded its pinned node count",
                )
            node = nodes.get(node_id)
            if node is None:
                return self._fail_run(
                    workspace_id, run_id, "missing_node", "Pinned Flow node is missing"
                )
            step_id: str | None = None
            try:
                mapped_input = self._resolve_mapping(
                    node["input_mapping"],
                    run_input=run["input"],
                    step_outputs=completed_outputs,
                )
                input_schema = self._node_input_schema(workspace_id, node)
                mapped_input = validate_json_schema(
                    mapped_input, input_schema, f"node {node_id} input"
                )
                step_id = self.repository.start_step(
                    workspace_id,
                    run_id,
                    node_id=node_id,
                    node_type=node["type"],
                    target_version_id=node["version_id"],
                    input_data=mapped_input,
                )
                if node["type"] == "action":
                    action = self.repository.get_action_version(
                        workspace_id, node["version_id"]
                    )
                    result = self._invoke_action(
                        workspace_id,
                        run_id,
                        step_id,
                        node_id=node_id,
                        action=action,
                        input_data=mapped_input,
                        attempt=1,
                        invocation_key=f"node:{node_id}:attempt:1",
                    )
                else:
                    result = self._invoke_agent_node(
                        workspace_id,
                        run_id,
                        step_id,
                        node_id=node_id,
                        agent_version_id=node["version_id"],
                        input_data=mapped_input,
                    )
                if result.paused:
                    next_node = self._next_node(routes, node_id, "approved")
                    self.repository.finish_step(
                        workspace_id,
                        run_id,
                        step_id,
                        status="waiting_approval",
                        output=result.output,
                        route_outcome="approved",
                    )
                    self.repository.create_approval_request(
                        workspace_id,
                        run_id,
                        step_id,
                        node_id=node_id,
                        message=result.approval_message or "Human approval required",
                        context=mapped_input,
                    )
                    self.repository.transition_run(
                        workspace_id,
                        run_id,
                        status="waiting_approval",
                        current_node_id=next_node,
                    )
                    return self.repository.get_run(workspace_id, run_id)
                output_schema = self._node_output_schema(workspace_id, node)
                output = validate_json_schema(
                    result.output, output_schema, f"node {node_id} output"
                )
                self.repository.finish_step(
                    workspace_id,
                    run_id,
                    step_id,
                    status="completed",
                    output=output,
                    route_outcome=result.route_outcome,
                )
                completed_outputs[node_id] = output
                last_output = output
                next_node = self._next_node(
                    routes, node_id, result.route_outcome
                )
                if next_node is None:
                    self.repository.transition_run(
                        workspace_id,
                        run_id,
                        status="completed",
                        current_node_id=None,
                        output=last_output,
                    )
                    return self.repository.get_run(workspace_id, run_id)
                node_id = next_node
            except (ContractViolation, ProviderFailure) as error:
                if step_id is not None:
                    try:
                        self.repository.finish_step(
                            workspace_id,
                            run_id,
                            step_id,
                            status="failed",
                            output=None,
                            route_outcome=None,
                            error_code=error.code,
                            error_message=str(error),
                        )
                    except (Conflict, ContractViolation):
                        pass
                return self._fail_run(
                    workspace_id, run_id, error.code, str(error)
                )
        self.repository.transition_run(
            workspace_id,
            run_id,
            status="completed",
            current_node_id=None,
            output=last_output,
        )
        return self.repository.get_run(workspace_id, run_id)

    def _fail_run(
        self, workspace_id: str, run_id: str, code: str, message: str
    ) -> dict[str, Any]:
        run = self.repository.get_run(workspace_id, run_id)
        if run["status"] not in {"running", "created"}:
            return run
        self.repository.transition_run(
            workspace_id,
            run_id,
            status="failed",
            current_node_id=None,
            error_code=code,
            error_message=message[:500],
        )
        return self.repository.get_run(workspace_id, run_id)

    @staticmethod
    def _resolve_mapping(
        mapping: Mapping[str, Mapping[str, Any]],
        *,
        run_input: Mapping[str, Any],
        step_outputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for target, source in mapping.items():
            if source["source"] == "literal":
                result[target] = json.loads(canonical_json(source["value"]))
            elif source["source"] == "input":
                result[target] = _value_at(
                    run_input, source["path"], field=f"mapping for {target}"
                )
            else:
                source_node = source["node_id"]
                if source_node not in step_outputs:
                    raise ContractViolation(
                        f"mapping for {target} references unfinished Step {source_node}"
                    )
                result[target] = _value_at(
                    step_outputs[source_node],
                    source["path"],
                    field=f"mapping for {target}",
                )
        return result

    def _node_input_schema(
        self, workspace_id: str, node: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        if node["type"] == "action":
            return self.repository.get_action_version(
                workspace_id, node["version_id"]
            )["input_schema"]
        agent = self.repository.get_agent_runtime(workspace_id, node["version_id"])
        return {
            "type": "object",
            "properties": {
                variable: {"type": "string"}
                for variable in agent["prompt"]["variables"]
            },
            "required": list(agent["prompt"]["variables"]),
            "additionalProperties": False,
        }

    def _node_output_schema(
        self, workspace_id: str, node: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        if node["type"] == "action":
            return self.repository.get_action_version(
                workspace_id, node["version_id"]
            )["output_schema"]
        return {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        }

    @staticmethod
    def _next_node(
        routes: Sequence[Mapping[str, str]], node_id: str, outcome: str
    ) -> str | None:
        exact = [
            route["to"]
            for route in routes
            if route["from"] == node_id and route["outcome"] == outcome
        ]
        if exact:
            return exact[0]
        if outcome != "success":
            fallback = [
                route["to"]
                for route in routes
                if route["from"] == node_id and route["outcome"] == "success"
            ]
            if fallback:
                return fallback[0]
        return None

    def _invoke_action(
        self,
        workspace_id: str,
        run_id: str,
        step_id: str,
        *,
        node_id: str,
        action: Mapping[str, Any],
        input_data: Mapping[str, Any],
        attempt: int,
        invocation_key: str,
    ) -> ActionResult:
        validated_input = validate_json_schema(
            dict(input_data), action["input_schema"], f"Action {action['slug']} input"
        )
        receipt_key = fingerprint(
            {
                "run_id": run_id,
                "step_id": step_id,
                "node_id": node_id,
                "action_version_id": action["id"],
                "attempt": attempt,
                "invocation_key": invocation_key,
                "input": validated_input,
            }
        )
        kind = action["kind"]
        if kind == "template":
            template = action["config"]["template"]
            variables = sorted(set(PLACEHOLDER_RE.findall(template)))
            output = {
                "text": render_prompt(
                    template,
                    declared_variables=variables,
                    values={variable: validated_input[variable] for variable in variables},
                )
            }
            result = ActionResult(output=output, route_outcome="success")
        elif kind == "condition":
            actual = _value_at(
                validated_input,
                action["config"]["path"],
                field=f"Action {action['slug']} condition",
            )
            matched = self._compare(
                actual, action["config"]["operator"], action["config"]["value"]
            )
            result = ActionResult(
                output={"matched": matched, "actual": actual},
                route_outcome="true" if matched else "false",
            )
        elif kind == "approval":
            template = action["config"]["message_template"]
            variables = sorted(set(PLACEHOLDER_RE.findall(template)))
            message = render_prompt(
                template,
                declared_variables=variables,
                values={variable: validated_input[variable] for variable in variables},
                maximum_output=2_000,
            )
            result = ActionResult(
                output={"pending": True},
                route_outcome="approved",
                paused=True,
                approval_message=message,
            )
        elif kind == "sandbox":
            effect = self.repository.create_effect(
                workspace_id,
                run_id,
                step_id,
                action_version_id=action["id"],
                collection=action["config"]["collection"],
                payload=validated_input,
                idempotency_key=receipt_key,
            )
            result = ActionResult(
                output={"effect_id": effect["id"], "collection": effect["collection"]},
                route_outcome="success",
            )
        elif kind == "ai":
            result = self._invoke_ai_action(
                workspace_id,
                run_id,
                step_id,
                node_id=node_id,
                action=action,
                input_data=validated_input,
            )
        else:
            raise ContractViolation("Action kind is not implemented")
        if not result.paused:
            validate_json_schema(
                result.output,
                action["output_schema"],
                f"Action {action['slug']} output",
            )
        outcome = "waiting_approval" if result.paused else "succeeded"
        self.repository.record_receipt(
            workspace_id,
            run_id,
            step_id,
            node_id=node_id,
            action_version_id=action["id"],
            attempt=attempt,
            outcome=outcome,
            input_data=validated_input,
            output=result.output,
            error_code=None,
            idempotency_key=receipt_key,
        )
        return result

    @staticmethod
    def _compare(actual: Any, operator: str, expected: Any) -> bool:
        try:
            if operator == "equals":
                return actual == expected
            if operator == "not_equals":
                return actual != expected
            if operator == "contains":
                return expected in actual
            if operator == "gt":
                return actual > expected
            if operator == "gte":
                return actual >= expected
            if operator == "lt":
                return actual < expected
            if operator == "lte":
                return actual <= expected
        except (TypeError, ValueError):
            raise ContractViolation("Condition operands are incompatible") from None
        raise ContractViolation("Condition operator is not supported")

    def _invoke_ai_action(
        self,
        workspace_id: str,
        run_id: str,
        step_id: str,
        *,
        node_id: str,
        action: Mapping[str, Any],
        input_data: Mapping[str, Any],
    ) -> ActionResult:
        agent_version_id = action["agent_version_id"]
        if not isinstance(agent_version_id, str):
            raise ContractViolation("AI Action has no pinned Agent")
        agent = self.repository.get_agent_runtime(workspace_id, agent_version_id)
        prompt = render_prompt(
            agent["prompt"]["template"],
            declared_variables=agent["prompt"]["variables"],
            values=input_data,
        )
        instructions = self._agent_instructions(agent)
        granted_actions = self._granted_action_tools(workspace_id, agent)
        tool_by_name = {item["action"]["slug"]: item["action"] for item in granted_actions}
        tool_definitions = [item["definition"] for item in granted_actions]
        input_items: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        max_tool_calls = int(action["config"].get("max_tool_calls", 0))
        used_tool_calls = 0
        while True:
            payload: dict[str, Any] = {
                "model": agent["model"],
                "instructions": instructions,
                "input": input_items,
                "parallel_tool_calls": False,
                "max_output_tokens": self.max_output_tokens,
                "store": False,
                "metadata": {
                    "kyn_surface": "agent-studio",
                    "run_id": run_id,
                    "step_id": step_id,
                    "node_id": node_id,
                    "agent_version_id": agent["id"],
                },
            }
            if tool_definitions and used_tool_calls < max_tool_calls:
                payload["tools"] = tool_definitions
                payload["tool_choice"] = "auto"
            else:
                payload["tool_choice"] = "none"
                payload["text"] = {
                    "format": {
                        "type": "json_schema",
                        "name": "kyn_action_output",
                        "schema": action["output_schema"],
                        "strict": True,
                    }
                }
            response = self._call_and_record(
                workspace_id, run_id, step_id, agent, payload
            )
            calls = function_calls(response)
            if not calls:
                try:
                    output = json.loads(extract_output_text(response))
                except json.JSONDecodeError:
                    raise ContractViolation("AI Action output is not valid JSON") from None
                if not isinstance(output, dict):
                    raise ContractViolation("AI Action output must be an object")
                return ActionResult(output=output, route_outcome="success")
            if used_tool_calls + len(calls) > max_tool_calls:
                raise ContractViolation("Agent exceeded the pinned Action-call budget")
            response_output = response.get("output")
            if not isinstance(response_output, list):
                raise ProviderFailure("OpenAI response output is missing")
            input_items.extend(response_output)
            for call in calls:
                name = call.get("name")
                call_id = call.get("call_id")
                arguments_text = call.get("arguments")
                if (
                    not isinstance(name, str)
                    or name not in tool_by_name
                    or not isinstance(call_id, str)
                    or not isinstance(arguments_text, str)
                ):
                    raise ContractViolation("Agent requested an unauthorized or malformed Action")
                try:
                    arguments = json.loads(arguments_text)
                except json.JSONDecodeError:
                    raise ContractViolation("Agent Action arguments are not valid JSON") from None
                if not isinstance(arguments, dict):
                    raise ContractViolation("Agent Action arguments must be an object")
                nested = self._invoke_action(
                    workspace_id,
                    run_id,
                    step_id,
                    node_id=node_id,
                    action=tool_by_name[name],
                    input_data=arguments,
                    attempt=1,
                    invocation_key=f"model-call:{call_id}",
                )
                if nested.paused:
                    raise ContractViolation("Agent-callable Actions cannot pause a Run")
                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": canonical_json(nested.output),
                    }
                )
                used_tool_calls += 1

    def _invoke_agent_node(
        self,
        workspace_id: str,
        run_id: str,
        step_id: str,
        *,
        node_id: str,
        agent_version_id: str,
        input_data: Mapping[str, Any],
    ) -> ActionResult:
        agent = self.repository.get_agent_runtime(workspace_id, agent_version_id)
        prompt = render_prompt(
            agent["prompt"]["template"],
            declared_variables=agent["prompt"]["variables"],
            values=input_data,
        )
        payload = {
            "model": agent["model"],
            "instructions": self._agent_instructions(agent),
            "input": [{"role": "user", "content": prompt}],
            "tool_choice": "none",
            "max_output_tokens": self.max_output_tokens,
            "store": False,
            "metadata": {
                "kyn_surface": "agent-studio",
                "run_id": run_id,
                "step_id": step_id,
                "node_id": node_id,
                "agent_version_id": agent["id"],
            },
        }
        response = self._call_and_record(
            workspace_id, run_id, step_id, agent, payload
        )
        return ActionResult(
            output={"text": extract_output_text(response)}, route_outcome="success"
        )

    def _granted_action_tools(
        self, workspace_id: str, agent: Mapping[str, Any]
    ) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        seen_names: set[str] = set()
        for version_id in agent["effective_action_version_ids"]:
            action = self.repository.get_action_version(workspace_id, version_id)
            if action["kind"] not in CALLABLE_ACTION_KINDS:
                raise ContractViolation(
                    f"Skill grants Action {action['slug']} which cannot be model-called"
                )
            if action["slug"] in seen_names:
                raise ContractViolation("Agent has ambiguous granted Action names")
            seen_names.add(action["slug"])
            tools.append(
                {
                    "action": action,
                    "definition": {
                        "type": "function",
                        "name": action["slug"],
                        "description": action["description"],
                        "parameters": action["input_schema"],
                        "strict": True,
                    },
                }
            )
        return tools

    @staticmethod
    def _agent_instructions(agent: Mapping[str, Any]) -> str:
        skill_text = "\n\n".join(
            f"Pinned Skill {skill['id']} ({skill['fingerprint']}):\n{skill['instructions']}"
            for skill in agent["skills"]
        )
        return (
            f"Pinned Agent {agent['id']} ({agent['fingerprint']}).\n"
            f"{agent['instructions']}\n\n{skill_text}"
        ).strip()

    def _call_and_record(
        self,
        workspace_id: str,
        run_id: str,
        step_id: str,
        agent: Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        if self.repository.store.in_write_transaction():
            raise RuntimeError("external model I/O under a SQLite write transaction")
        response = self.client.create(payload)
        summary = safe_response_summary(response)
        self.repository.record_model_call(
            workspace_id,
            run_id,
            step_id,
            agent_version_id=agent["id"],
            provider_response_id=summary["provider_response_id"],
            status=summary["status"],
            model=summary["model"],
            input_hash=fingerprint(payload),
            output_hash=fingerprint(response),
            usage=summary["usage"],
            request_id=(
                str(response.get("_request_id"))[:128]
                if response.get("_request_id")
                else None
            ),
        )
        if summary["status"] != "completed":
            raise ProviderFailure("OpenAI response did not complete")
        return response
