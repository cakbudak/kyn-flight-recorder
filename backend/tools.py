"""Static safe tool registry. Database data can grant tools, never define code."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .contracts import ContractViolation, canonical_json, fingerprint, require_string
from .store import Store


TOOL_DEFINITIONS: dict[str, dict[str, Any]] = {
    "inspect_release_policy": {
        "type": "function",
        "name": "inspect_release_policy",
        "description": (
            "Read the immutable release policy pinned to this run. Call this before staging."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        "strict": True,
    },
    "stage_release": {
        "type": "function",
        "name": "stage_release",
        "description": (
            "Stage the pinned artifact in the requested local sandbox environment. "
            "The runtime policy may deny the effect."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "environment": {
                    "type": "string",
                    "enum": ["staging", "production"],
                    "description": "The exact environment from the pinned flow request.",
                },
                "artifact": {
                    "type": "string",
                    "description": "The exact artifact from the pinned flow request.",
                },
            },
            "required": ["environment", "artifact"],
            "additionalProperties": False,
        },
        "strict": True,
    },
}


class ToolRegistry:
    def __init__(self, store: Store) -> None:
        self.store = store

    @property
    def known_names(self) -> frozenset[str]:
        return frozenset(TOOL_DEFINITIONS)

    def definitions(self, names: Sequence[str]) -> list[dict[str, Any]]:
        unknown = sorted(set(names) - self.known_names)
        if unknown:
            raise ContractViolation(f"unknown tool: {', '.join(unknown)}")
        return [TOOL_DEFINITIONS[name] for name in names]

    def execute(
        self,
        *,
        workspace_id: str,
        run_id: str,
        flow_version_id: str,
        agent_version_id: str,
        allowed_tools: Sequence[str],
        request: Mapping[str, Any],
        policy: Mapping[str, Any],
        call_id: str,
        name: str,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        if name not in set(allowed_tools) or name not in self.known_names:
            raise ContractViolation("tool is not authorized by the pinned skills")
        if name == "inspect_release_policy":
            return self._inspect(
                workspace_id=workspace_id,
                run_id=run_id,
                flow_version_id=flow_version_id,
                agent_version_id=agent_version_id,
                request=request,
                policy=policy,
                call_id=call_id,
                arguments=arguments,
            )
        if name == "stage_release":
            return self._stage(
                workspace_id=workspace_id,
                run_id=run_id,
                flow_version_id=flow_version_id,
                agent_version_id=agent_version_id,
                request=request,
                policy=policy,
                call_id=call_id,
                arguments=arguments,
            )
        raise ContractViolation("tool is not implemented by the static registry")

    def _inspect(
        self,
        *,
        workspace_id: str,
        run_id: str,
        flow_version_id: str,
        agent_version_id: str,
        request: Mapping[str, Any],
        policy: Mapping[str, Any],
        call_id: str,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        if dict(arguments):
            raise ContractViolation("inspect_release_policy accepts no arguments")
        allowed = policy.get("allowed_environments")
        if not isinstance(allowed, list) or not all(isinstance(item, str) for item in allowed):
            raise ContractViolation("pinned release policy is malformed")
        requested = require_string(request.get("environment"), "requested environment", maximum=32)
        result = {
            "allowed_environments": list(allowed),
            "requested_environment": requested,
            "policy_fingerprint": fingerprint(policy),
        }
        return self.store.record_tool_receipt(
            workspace_id,
            run_id,
            agent_version_id=agent_version_id,
            flow_version_id=flow_version_id,
            call_id=call_id,
            tool_name="inspect_release_policy",
            arguments={},
            outcome="succeeded",
            error_code=None,
            result=result,
            effect=None,
            idempotency_key=fingerprint(
                {"run_id": run_id, "call_id": call_id, "tool": "inspect_release_policy"}
            ),
        )

    def _stage(
        self,
        *,
        workspace_id: str,
        run_id: str,
        flow_version_id: str,
        agent_version_id: str,
        request: Mapping[str, Any],
        policy: Mapping[str, Any],
        call_id: str,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        if set(arguments) != {"environment", "artifact"}:
            raise ContractViolation("stage_release arguments must be exactly environment and artifact")
        environment = require_string(arguments.get("environment"), "environment", maximum=32)
        artifact = require_string(arguments.get("artifact"), "artifact", maximum=160)
        requested_environment = require_string(
            request.get("environment"), "requested environment", maximum=32
        )
        requested_artifact = require_string(request.get("artifact"), "requested artifact", maximum=160)
        if environment != requested_environment or artifact != requested_artifact:
            raise ContractViolation("tool arguments do not match the pinned flow request")
        allowed = policy.get("allowed_environments")
        if not isinstance(allowed, list) or not all(isinstance(item, str) for item in allowed):
            raise ContractViolation("pinned release policy is malformed")

        idempotency_key = fingerprint(
            {
                "run_id": run_id,
                "call_id": call_id,
                "tool": "stage_release",
                "arguments": {"environment": environment, "artifact": artifact},
            }
        )
        if environment not in allowed:
            return self.store.record_tool_receipt(
                workspace_id,
                run_id,
                agent_version_id=agent_version_id,
                flow_version_id=flow_version_id,
                call_id=call_id,
                tool_name="stage_release",
                arguments={"environment": environment, "artifact": artifact},
                outcome="denied",
                error_code="policy_mismatch",
                result={
                    "requested_environment": environment,
                    "allowed_environments": list(allowed),
                    "artifact": artifact,
                    "message": "Pinned policy denied the sandbox release.",
                },
                effect=None,
                idempotency_key=idempotency_key,
            )

        return self.store.record_tool_receipt(
            workspace_id,
            run_id,
            agent_version_id=agent_version_id,
            flow_version_id=flow_version_id,
            call_id=call_id,
            tool_name="stage_release",
            arguments={"environment": environment, "artifact": artifact},
            outcome="succeeded",
            error_code=None,
            result={
                "requested_environment": environment,
                "allowed_environments": list(allowed),
                "artifact": artifact,
                "message": "Sandbox release was durably staged.",
            },
            effect={"environment": environment, "artifact": artifact},
            idempotency_key=idempotency_key,
        )

    @staticmethod
    def function_output(receipt: Mapping[str, Any]) -> str:
        return canonical_json(
            {
                "ok": receipt["outcome"] == "succeeded",
                "receipt_id": receipt["id"],
                "outcome": receipt["outcome"],
                "error_code": receipt.get("error_code"),
                "result": receipt["result"],
            }
        )
