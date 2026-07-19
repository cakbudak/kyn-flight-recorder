"""Product control plane: the sole mutation API used by HTTP and tests."""

from __future__ import annotations

import re
from typing import Any

from .contracts import (
    ContractViolation,
    require_slug,
    require_string,
    require_string_list,
    render_prompt,
)
from .runtime import AgentRuntime, ResponseTransport
from .store import Store
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
    ) -> None:
        if default_model not in SUPPORTED_MODELS:
            raise ContractViolation("default model is not supported")
        self.store = store
        self.client = client
        self.default_model = default_model
        self.tools = ToolRegistry(store)
        self.runtime = AgentRuntime(store, client, self.tools)

    def create_workspace(self, *, seed: bool = True) -> dict[str, Any]:
        workspace = self.store.create_workspace()
        if seed:
            self.store.seed_default_lab(workspace["id"], model=self.default_model)
        return {
            "workspace_id": workspace["id"],
            "workspace_token": workspace["token"],
            "snapshot": self.store.workspace_snapshot(workspace["id"]),
        }

    def resolve_workspace(self, token: str) -> str:
        return self.store.resolve_workspace(token)

    def snapshot(self, workspace_id: str) -> dict[str, Any]:
        return self.store.workspace_snapshot(workspace_id)

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
        return self.store.create_skill(
            workspace_id,
            name=normalized_name,
            slug=normalized_slug,
            instructions=normalized_instructions,
            allowed_tools=normalized_tools,
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

    def run_flow(self, workspace_id: str, flow_id: str) -> dict[str, Any]:
        return self.runtime.execute(workspace_id, flow_id)

    def diagnose_run(self, workspace_id: str, run_id: str) -> dict[str, Any]:
        return self.runtime.diagnose(workspace_id, run_id)

    def propose_repair(self, workspace_id: str, diagnosis_id: str) -> dict[str, Any]:
        return self.runtime.propose_repair(workspace_id, diagnosis_id)

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

    def rerun(self, workspace_id: str, run_id: str) -> dict[str, Any]:
        target = self.store.rerun_target(workspace_id, run_id)
        return self.runtime.execute(workspace_id, **target)

    def get_run(self, workspace_id: str, run_id: str) -> dict[str, Any]:
        return self.store.get_run(workspace_id, run_id)

    def get_flow(self, workspace_id: str, flow_id: str) -> dict[str, Any]:
        return self.store.get_flow(workspace_id, flow_id)

    def get_flow_version(
        self, workspace_id: str, flow_id: str, version: int
    ) -> dict[str, Any]:
        return self.store.get_flow_version(workspace_id, flow_id, version)
