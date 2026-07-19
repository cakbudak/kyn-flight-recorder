"""Agent execution, evidence-grounded diagnosis, and bounded repair proposal."""

from __future__ import annotations

import json
from typing import Any, Mapping, Protocol, Sequence

from .contracts import (
    ContractViolation,
    ProviderFailure,
    canonical_json,
    exact_set,
    extract_output_text,
    fingerprint,
    function_calls,
    require_string,
    require_string_list,
    render_prompt,
    safe_response_summary,
)
from .store import Store
from .tools import ToolRegistry


class ResponseTransport(Protocol):
    def create(self, payload: Mapping[str, Any]) -> dict[str, Any]: ...


class ExecutionFailure(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


DIAGNOSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "fault_class": {"type": "string", "enum": ["policy_mismatch"]},
        "summary": {"type": "string"},
        "evidence_event_ids": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "why_not_retry": {"type": "string"},
        "repair_path": {
            "type": "string",
            "enum": ["/policy/allowed_environments"],
        },
    },
    "required": [
        "fault_class",
        "summary",
        "evidence_event_ids",
        "confidence",
        "why_not_retry",
        "repair_path",
    ],
    "additionalProperties": False,
}


REPAIR_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "risk": {"type": "string", "enum": ["low", "medium", "high"]},
        "patch": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "op": {"type": "string", "enum": ["replace"]},
                    "path": {
                        "type": "string",
                        "enum": ["/policy/allowed_environments"],
                    },
                    "value": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["op", "path", "value"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "risk", "patch"],
    "additionalProperties": False,
}


class AgentRuntime:
    def __init__(
        self,
        store: Store,
        client: ResponseTransport,
        tools: ToolRegistry,
        *,
        max_output_tokens: int = 1_200,
    ) -> None:
        self.store = store
        self.client = client
        self.tools = tools
        self.max_output_tokens = max_output_tokens

    def execute(
        self,
        workspace_id: str,
        flow_id: str,
        *,
        flow_version: int | None = None,
        parent_run_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        initial_context = self.store.flow_runtime_context(workspace_id, flow_id, flow_version)
        selected_version = int(initial_context["version"]["version"])
        run_id, created = self.store.create_run(
            workspace_id,
            flow_id,
            flow_version=selected_version,
            parent_run_id=parent_run_id,
            correlation_id=correlation_id,
        )
        if not created:
            return self.store.get_run(workspace_id, run_id)
        context = self.store.flow_runtime_context(workspace_id, flow_id, selected_version)
        executor = context["agents"]["executor"]
        self.store.append_event(
            workspace_id,
            run_id,
            event_type="agent.started",
            actor_type="agent",
            actor_id=executor["id"],
            payload={
                "role": "executor",
                "agent_version_id": executor["id"],
                "agent_fingerprint": executor["fingerprint"],
                "prompt_version_id": executor["prompt"]["id"],
                "skill_version_ids": [skill["id"] for skill in executor["skills"]],
                "effective_tools": executor["effective_tools"],
            },
        )

        request = context["version"]["request"]
        policy = context["version"]["policy"]
        prompt = render_prompt(
            executor["prompt"]["template"],
            declared_variables=executor["prompt"]["variables"],
            values={
                "goal": request["goal"],
                "artifact": request["artifact"],
                "requested_environment": request["environment"],
                "policy_json": canonical_json(policy),
            },
        )
        instructions = self._instructions(executor)
        input_items: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        required_tools = ["inspect_release_policy", "stage_release"]
        stage_receipt: dict[str, Any] | None = None

        try:
            for expected_tool in required_tools:
                payload = self._executor_payload(
                    executor,
                    run_id,
                    instructions=instructions,
                    input_items=input_items,
                    forced_tool=expected_tool,
                )
                response, _model_call_id = self._call_and_record(
                    workspace_id,
                    run_id,
                    executor,
                    payload,
                )
                calls = function_calls(response)
                if len(calls) != 1:
                    raise ExecutionFailure(
                        "required_tool_not_called",
                        f"executor did not call required tool {expected_tool}",
                    )
                call = calls[0]
                name = call.get("name")
                if not isinstance(name, str) or name not in executor["effective_tools"]:
                    raise ExecutionFailure(
                        "tool_not_authorized",
                        "executor requested a tool not granted by its pinned skills",
                    )
                if name != expected_tool:
                    raise ExecutionFailure(
                        "required_tool_not_called",
                        f"executor called {name} when {expected_tool} was required",
                    )
                call_id = call.get("call_id")
                arguments_text = call.get("arguments")
                if not isinstance(call_id, str) or not call_id or not isinstance(arguments_text, str):
                    raise ExecutionFailure("invalid_tool_call", "executor returned a malformed tool call")
                try:
                    arguments = json.loads(arguments_text)
                except json.JSONDecodeError:
                    raise ExecutionFailure("invalid_tool_call", "tool arguments were not valid JSON") from None
                if not isinstance(arguments, dict):
                    raise ExecutionFailure("invalid_tool_call", "tool arguments must be an object")

                self.store.append_event(
                    workspace_id,
                    run_id,
                    event_type="tool.requested",
                    actor_type="agent",
                    actor_id=executor["id"],
                    payload={
                        "call_id": call_id,
                        "tool_name": name,
                        "arguments": arguments,
                        "authority": {
                            "agent_version_id": executor["id"],
                            "skill_version_ids": [skill["id"] for skill in executor["skills"]],
                        },
                    },
                )
                receipt = self.tools.execute(
                    workspace_id=workspace_id,
                    run_id=run_id,
                    flow_version_id=context["version"]["id"],
                    agent_version_id=executor["id"],
                    allowed_tools=executor["effective_tools"],
                    request=request,
                    policy=policy,
                    call_id=call_id,
                    name=name,
                    arguments=arguments,
                )
                if name == "stage_release":
                    stage_receipt = receipt
                response_output = response.get("output")
                if not isinstance(response_output, list):
                    raise ExecutionFailure("invalid_model_response", "response output is missing")
                input_items.extend(response_output)
                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": self.tools.function_output(receipt),
                    }
                )

            summary_hash: str | None = None
            try:
                summary_payload = self._executor_payload(
                    executor,
                    run_id,
                    instructions=instructions,
                    input_items=input_items,
                    forced_tool=None,
                )
                summary_response, _model_call_id = self._call_and_record(
                    workspace_id,
                    run_id,
                    executor,
                    summary_payload,
                )
                summary_hash = fingerprint({"text": extract_output_text(summary_response)})
            except ProviderFailure as error:
                self.store.append_event(
                    workspace_id,
                    run_id,
                    event_type="agent.summary_failed",
                    actor_type="runtime",
                    actor_id=None,
                    payload={"code": error.code},
                )

            if stage_receipt is None:
                raise ExecutionFailure("required_tool_not_called", "stage_release produced no receipt")
            if stage_receipt["outcome"] == "denied":
                terminal_status = "blocked"
                error_code = stage_receipt["error_code"]
            elif stage_receipt["outcome"] == "succeeded":
                terminal_status = "completed"
                error_code = None
            else:
                terminal_status = "failed"
                error_code = stage_receipt["error_code"] or "tool_failed"
            self.store.transition_run(
                workspace_id,
                run_id,
                status=terminal_status,
                error_code=error_code,
                agent_version_id=executor["id"],
                summary_hash=summary_hash,
            )
        except ExecutionFailure as error:
            self.store.append_event(
                workspace_id,
                run_id,
                event_type="agent.contract_rejected",
                actor_type="runtime",
                actor_id=None,
                payload={"code": error.code, "message": str(error)},
            )
            self.store.transition_run(
                workspace_id,
                run_id,
                status="failed",
                error_code=error.code,
                agent_version_id=executor["id"],
            )
        except (ContractViolation, ProviderFailure) as error:
            code = "provider_failure" if isinstance(error, ProviderFailure) else "runtime_contract_violation"
            self.store.append_event(
                workspace_id,
                run_id,
                event_type="agent.failed",
                actor_type="runtime",
                actor_id=None,
                payload={"code": code},
            )
            self.store.transition_run(
                workspace_id,
                run_id,
                status="failed",
                error_code=code,
                agent_version_id=executor["id"],
            )
        return self.store.get_run(workspace_id, run_id)

    def diagnose(self, workspace_id: str, run_id: str) -> dict[str, Any]:
        run = self.store.get_run(workspace_id, run_id)
        if run["diagnosis"] is not None:
            return run["diagnosis"]
        candidate = self.store.diagnosis_candidate(workspace_id, run_id)
        context = self.store.flow_runtime_context(
            workspace_id, run["flow_id"], int(run["flow_version"])
        )
        agent = context["agents"]["diagnostician"]
        self._start_handoff(
            workspace_id,
            run_id,
            from_role="executor",
            to_agent=agent,
            purpose="diagnose recorded tool failure",
        )
        prompt = render_prompt(
            agent["prompt"]["template"],
            declared_variables=agent["prompt"]["variables"],
            values={
                "candidate_json": canonical_json(
                    {key: value for key, value in candidate.items() if key != "events"}
                ),
                "evidence_json": canonical_json(candidate["events"]),
            },
        )
        payload = self._structured_payload(
            agent,
            run_id,
            prompt=prompt,
            schema_name="kyn_diagnosis",
            schema=DIAGNOSIS_SCHEMA,
            metadata={"run_id": run_id},
        )
        try:
            response, model_call_id = self._call_and_record(
                workspace_id, run_id, agent, payload
            )
            result = self._parse_object(extract_output_text(response), "diagnosis")
            validated = self._validate_diagnosis(result, candidate)
            return self.store.create_diagnosis(
                workspace_id,
                run_id,
                agent_version_id=agent["id"],
                model_call_id=model_call_id,
                **validated,
            )
        except (ContractViolation, ProviderFailure) as error:
            self.store.append_event(
                workspace_id,
                run_id,
                event_type="diagnosis.rejected",
                actor_type="runtime",
                actor_id=None,
                payload={"code": error.code},
            )
            raise

    def propose_repair(self, workspace_id: str, diagnosis_id: str) -> dict[str, Any]:
        diagnosis = self.store.get_diagnosis(workspace_id, diagnosis_id)
        run = self.store.get_run(workspace_id, diagnosis["run_id"])
        if run["repair"] is not None:
            return run["repair"]
        context = self.store.flow_runtime_context(
            workspace_id, run["flow_id"], int(run["flow_version"])
        )
        agent = context["agents"]["repairer"]
        self._start_handoff(
            workspace_id,
            run["id"],
            from_role="diagnostician",
            to_agent=agent,
            purpose="propose bounded manifest repair",
        )
        manifest = {
            "request": context["version"]["request"],
            "policy": context["version"]["policy"],
        }
        repair_policy = context["version"]["repair_policy"]
        prompt = render_prompt(
            agent["prompt"]["template"],
            declared_variables=agent["prompt"]["variables"],
            values={
                "diagnosis_json": canonical_json(diagnosis),
                "manifest_json": canonical_json(manifest),
                "repair_policy_json": canonical_json(repair_policy),
            },
        )
        payload = self._structured_payload(
            agent,
            run["id"],
            prompt=prompt,
            schema_name="kyn_repair",
            schema=REPAIR_SCHEMA,
            metadata={"run_id": run["id"], "diagnosis_id": diagnosis_id},
        )
        try:
            response, model_call_id = self._call_and_record(
                workspace_id, run["id"], agent, payload
            )
            result = self._parse_object(extract_output_text(response), "repair")
            validated = self._validate_repair(
                result,
                diagnosis=diagnosis,
                request=context["version"]["request"],
                policy=context["version"]["policy"],
                repair_policy=repair_policy,
            )
            return self.store.create_repair(
                workspace_id,
                diagnosis_id,
                agent_version_id=agent["id"],
                model_call_id=model_call_id,
                **validated,
            )
        except (ContractViolation, ProviderFailure) as error:
            self.store.append_event(
                workspace_id,
                run["id"],
                event_type="repair.rejected",
                actor_type="runtime",
                actor_id=None,
                payload={"code": error.code},
            )
            raise

    def _call_and_record(
        self,
        workspace_id: str,
        run_id: str,
        agent: Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> tuple[dict[str, Any], str]:
        if self.store.in_write_transaction():
            raise RuntimeError("external model I/O under a SQLite write transaction")
        response = self.client.create(payload)
        summary = safe_response_summary(response)
        model_call_id = self.store.record_model_call(
            workspace_id,
            run_id,
            agent_version_id=agent["id"],
            role=agent["role"],
            provider_response_id=summary["provider_response_id"],
            status=summary["status"],
            model=summary["model"],
            input_hash=fingerprint(payload),
            output_hash=fingerprint(response),
            usage=summary["usage"],
        )
        if summary["status"] != "completed":
            raise ProviderFailure("OpenAI response did not complete")
        return response, model_call_id

    def _executor_payload(
        self,
        agent: Mapping[str, Any],
        run_id: str,
        *,
        instructions: str,
        input_items: Sequence[Mapping[str, Any]],
        forced_tool: str | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": agent["model"],
            "instructions": instructions,
            "input": [dict(item) for item in input_items],
            "tools": self.tools.definitions(agent["effective_tools"]),
            "parallel_tool_calls": False,
            "max_output_tokens": self.max_output_tokens,
            "store": False,
            "metadata": {"kyn_role": "executor", "run_id": run_id},
        }
        payload["tool_choice"] = (
            {"type": "function", "name": forced_tool} if forced_tool else "none"
        )
        return payload

    def _structured_payload(
        self,
        agent: Mapping[str, Any],
        run_id: str,
        *,
        prompt: str,
        schema_name: str,
        schema: Mapping[str, Any],
        metadata: Mapping[str, str],
    ) -> dict[str, Any]:
        return {
            "model": agent["model"],
            "instructions": self._instructions(agent),
            "input": [{"role": "user", "content": prompt}],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "schema": dict(schema),
                    "strict": True,
                }
            },
            "max_output_tokens": self.max_output_tokens,
            "store": False,
            "metadata": {"kyn_role": agent["role"], **dict(metadata)},
        }

    @staticmethod
    def _instructions(agent: Mapping[str, Any]) -> str:
        skill_instructions = "\n\n".join(
            f"Pinned skill {skill['id']} ({skill['fingerprint']}):\n{skill['instructions']}"
            for skill in agent["skills"]
        )
        return (
            f"Agent role: {agent['role']}\nAgent version: {agent['id']}\n"
            f"{agent['instructions']}\n\n{skill_instructions}"
        ).strip()

    def _start_handoff(
        self,
        workspace_id: str,
        run_id: str,
        *,
        from_role: str,
        to_agent: Mapping[str, Any],
        purpose: str,
    ) -> None:
        self.store.append_event(
            workspace_id,
            run_id,
            event_type="agent.handoff",
            actor_type="runtime",
            actor_id=None,
            payload={
                "from_role": from_role,
                "to_role": to_agent["role"],
                "to_agent_version_id": to_agent["id"],
                "purpose": purpose,
            },
        )
        self.store.append_event(
            workspace_id,
            run_id,
            event_type="agent.started",
            actor_type="agent",
            actor_id=to_agent["id"],
            payload={
                "role": to_agent["role"],
                "agent_version_id": to_agent["id"],
                "agent_fingerprint": to_agent["fingerprint"],
                "prompt_version_id": to_agent["prompt"]["id"],
                "skill_version_ids": [skill["id"] for skill in to_agent["skills"]],
                "effective_tools": to_agent["effective_tools"],
            },
        )

    @staticmethod
    def _parse_object(text: str, label: str) -> dict[str, Any]:
        try:
            result = json.loads(text)
        except json.JSONDecodeError:
            raise ContractViolation(f"structured {label} is not valid JSON") from None
        if not isinstance(result, dict):
            raise ContractViolation(f"structured {label} must be an object")
        return result

    @staticmethod
    def _validate_diagnosis(
        result: Mapping[str, Any], candidate: Mapping[str, Any]
    ) -> dict[str, Any]:
        expected_keys = {
            "fault_class",
            "summary",
            "evidence_event_ids",
            "confidence",
            "why_not_retry",
            "repair_path",
        }
        if set(result) != expected_keys:
            raise ContractViolation("diagnosis has unexpected or missing fields")
        if result["fault_class"] != candidate["fault_class"]:
            raise ContractViolation("diagnosis fault class contradicts deterministic evidence")
        evidence = require_string_list(
            result["evidence_event_ids"], "diagnosis evidence", allow_empty=False
        )
        evidence = exact_set(
            evidence, candidate["evidence_event_ids"], "diagnosis evidence"
        )
        confidence = result["confidence"]
        if confidence not in {"low", "medium", "high"}:
            raise ContractViolation("diagnosis confidence is invalid")
        if result["repair_path"] != candidate["repair_path"]:
            raise ContractViolation("diagnosis repair path is not supported by evidence")
        return {
            "fault_class": str(result["fault_class"]),
            "summary": require_string(result["summary"], "diagnosis summary", minimum=20, maximum=800),
            "evidence_event_ids": evidence,
            "confidence": str(confidence),
            "why_not_retry": require_string(
                result["why_not_retry"], "diagnosis retry explanation", minimum=20, maximum=800
            ),
            "repair_path": str(result["repair_path"]),
        }

    @staticmethod
    def _validate_repair(
        result: Mapping[str, Any],
        *,
        diagnosis: Mapping[str, Any],
        request: Mapping[str, Any],
        policy: Mapping[str, Any],
        repair_policy: Mapping[str, Any],
    ) -> dict[str, Any]:
        if set(result) != {"summary", "risk", "patch"}:
            raise ContractViolation("repair has unexpected or missing fields")
        if diagnosis["fault_class"] != "policy_mismatch":
            raise ContractViolation("repair has no supported diagnosis")
        patch = result["patch"]
        if not isinstance(patch, list) or len(patch) != 1:
            raise ContractViolation("repair must contain exactly one operation")
        operation = patch[0]
        if not isinstance(operation, dict) or set(operation) != {"op", "path", "value"}:
            raise ContractViolation("repair operation has an invalid shape")
        allowed_paths = repair_policy.get("allowed_paths")
        allowed_operations = repair_policy.get("allowed_operations")
        if operation["path"] not in allowed_paths:
            raise ContractViolation("repair path is outside the pinned repair policy")
        if operation["op"] not in allowed_operations:
            raise ContractViolation("repair operation is outside the pinned repair policy")
        if operation["path"] != diagnosis["repair_path"]:
            raise ContractViolation("repair path does not match the diagnosis")
        current = require_string_list(
            policy.get("allowed_environments"), "allowed environments", allow_empty=False
        )
        requested = require_string(request.get("environment"), "requested environment", maximum=32)
        value = require_string_list(operation["value"], "repair value", allow_empty=False)
        expected_value = list(current)
        if requested not in expected_value:
            expected_value.append(requested)
        if value != expected_value:
            raise ContractViolation(
                "repair value must preserve existing environments and add only the requested environment"
            )
        risk = result["risk"]
        if risk not in {"low", "medium", "high"}:
            raise ContractViolation("repair risk is invalid")
        return {
            "patch": [
                {
                    "op": str(operation["op"]),
                    "path": str(operation["path"]),
                    "value": value,
                }
            ],
            "summary": require_string(result["summary"], "repair summary", minimum=20, maximum=800),
            "risk": str(risk),
        }
