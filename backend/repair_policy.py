"""Declarative repair policy for the Agent Studio maintenance loop.

This module is deliberately pure. It performs no I/O, opens no database
connection, and imports nothing from the store or the runtime. It answers one
question — *given a diagnosed fault, what is the single bounded patch this
system is allowed to propose?* — and answers it the same way every time.

The value here is not the patch. It is the **edge**: every fault class is either
admitted with an explicit allow-list of JSON-Pointer paths and operations, or
refused with a named reason. There is no third category, and there is no path
by which a failure can earn authority it was not granted by a human.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, Mapping

from .contracts import (
    MAX_NODE_ATTEMPTS,
    RETRYABLE_ERROR_CODES,
    ContractViolation,
    default_node_settings,
)


RepairTarget = Literal["action_config", "flow_node_settings"]

# The publish-time contract bounds a node at three attempts. A repair may never
# propose more than the definition would accept.
MAX_ATTEMPTS_CEILING = MAX_NODE_ATTEMPTS


@dataclass(frozen=True)
class RepairPolicy:
    """One admitted fault class and the exact space it may touch."""

    fault_class: str
    executor_kinds: tuple[str, ...]
    target: RepairTarget
    allowed_paths: tuple[str, ...]
    allowed_operations: tuple[str, ...]
    rationale: str

    def admits_kind(self, executor_kind: str) -> bool:
        """An empty `executor_kinds` means the fault is not kind-specific."""

        return not self.executor_kinds or executor_kind in self.executor_kinds


@dataclass(frozen=True)
class ResolvedRepair:
    """A policy, the surface it targets, and the patch computed against it."""

    policy: RepairPolicy
    target: RepairTarget
    patch: list[dict[str, Any]]


AUTHORITY_POLICY = RepairPolicy(
    fault_class="authority_policy",
    executor_kinds=("data_store",),
    target="action_config",
    allowed_paths=("/config/write_enabled",),
    allowed_operations=("replace",),
    rationale=(
        "A Data Store Action that denies its own declared bounded write is a "
        "configuration mismatch, not a capability request. The repair widens one "
        "boolean inside the Action's already-granted sandbox collection and "
        "cannot reach any other effect surface."
    ),
)

PROVIDER_FAILURE = RepairPolicy(
    fault_class="provider_failure",
    executor_kinds=(),
    target="flow_node_settings",
    allowed_paths=("/settings/max_attempts", "/settings/retry_on"),
    allowed_operations=("replace",),
    rationale=(
        "A transient provider fault on an under-provisioned node is a retry "
        "policy gap, not a defect in the Action contract. The repair raises the "
        "node's attempt budget within the publish-time ceiling of three and "
        "admits the observed retryable code. It changes no schema, no authority, "
        "and no Action definition."
    ),
)

REPAIR_POLICIES: tuple[RepairPolicy, ...] = (AUTHORITY_POLICY, PROVIDER_FAILURE)

REPAIR_REFUSALS: Mapping[str, str] = MappingProxyType(
    {
        "data_contract": (
            "A data contract fault means the pinned input or output schema "
            "caught a value the system could not honour. Loosening a strict "
            "schema converts a caught failure into an uncaught one and pushes "
            "the corruption downstream, so a human must decide whether the "
            "contract or the producer is wrong."
        ),
        "runtime_failure": (
            "A runtime failure has no bounded diagnosis: the observed symptom "
            "does not identify which declaration is wrong, so any automatic "
            "patch would be a guess applied with production authority. A human "
            "must read the evidence and author the successor version."
        ),
        "authority_escalation": (
            "Skill grants, agent authority, and allowed Action version ids are "
            "granted by a human and are never earned by failing. A repair that "
            "could widen them would make every fault a privilege escalation "
            "path, so this class is permanently outside the repair space."
        ),
    }
)

_POLICY_BY_FAULT_CLASS = {policy.fault_class: policy for policy in REPAIR_POLICIES}


def _path_is_allowed(path: str, allowed_paths: tuple[str, ...]) -> bool:
    return any(
        path == allowed or path.startswith(f"{allowed}/") for allowed in allowed_paths
    )


def _validate_against_policy(
    policy: RepairPolicy, patch: list[dict[str, Any]]
) -> None:
    """Hold the resolver to its own declaration.

    The resolver computes the patch, so nothing outside this module could catch
    a policy bug that silently widened the space. This check is the resolver
    proving to itself that it stayed inside the fence it published.
    """

    if not patch:
        raise ContractViolation(
            f"repair policy {policy.fault_class} computed an empty patch"
        )
    for operation in patch:
        if operation["op"] not in policy.allowed_operations:
            raise ContractViolation(
                f"repair policy {policy.fault_class} does not permit "
                f"operation {operation['op']!r}"
            )
        if not _path_is_allowed(operation["path"], policy.allowed_paths):
            raise ContractViolation(
                f"repair policy {policy.fault_class} does not permit "
                f"path {operation['path']!r}"
            )


def _resolve_authority_policy(
    policy: RepairPolicy, action_config: Mapping[str, Any]
) -> list[dict[str, Any]]:
    if action_config.get("write_enabled") is not False:
        raise ContractViolation(
            "the diagnosed Action already grants its bounded write, so there is "
            "nothing for an authority policy repair to widen"
        )
    return [{"op": "replace", "path": "/config/write_enabled", "value": True}]


def _resolve_provider_failure(
    policy: RepairPolicy,
    node_settings: Mapping[str, Any],
    error_code: str,
) -> list[dict[str, Any]]:
    if error_code not in RETRYABLE_ERROR_CODES:
        raise ContractViolation(
            f"error code {error_code!r} is not retryable, so raising the retry "
            "budget would not change the outcome"
        )
    # A node that declares no settings still has an effective retry policy: the
    # publish-time default. The repair must reason about what actually ran.
    effective = default_node_settings()
    effective.update(node_settings)

    raw_attempts = effective.get("max_attempts", 1)
    if not isinstance(raw_attempts, int) or isinstance(raw_attempts, bool):
        raise ContractViolation("the diagnosed Flow node has no usable attempt budget")
    current_attempts = max(1, min(MAX_ATTEMPTS_CEILING, raw_attempts))

    raw_retry_on = effective.get("retry_on", [])
    if not isinstance(raw_retry_on, (list, tuple)):
        raise ContractViolation("the diagnosed Flow node has no usable retry policy")
    current_retry_on = [item for item in raw_retry_on if item in RETRYABLE_ERROR_CODES]

    under_provisioned = current_attempts < MAX_ATTEMPTS_CEILING
    code_not_admitted = error_code not in current_retry_on
    if not under_provisioned and not code_not_admitted:
        raise ContractViolation(
            "the diagnosed Flow node already retries this code at the maximum "
            "attempt budget the publish-time contract allows, so the fault is "
            "not an under-provisioned retry policy"
        )

    next_attempts = min(MAX_ATTEMPTS_CEILING, max(2, current_attempts + 1))
    next_retry_on = list(current_retry_on)
    if error_code not in next_retry_on:
        next_retry_on.append(error_code)
    # The publish-time contract caps `retry_on` at three distinct codes; the
    # source set has exactly three members, so this cannot overflow.
    return [
        {"op": "replace", "path": "/settings/max_attempts", "value": next_attempts},
        {"op": "replace", "path": "/settings/retry_on", "value": next_retry_on},
    ]


def resolve_repair(
    *,
    fault_class: str,
    executor_kind: str,
    action_config: Mapping[str, Any],
    node_settings: Mapping[str, Any],
    error_code: str,
) -> ResolvedRepair:
    """Resolve one diagnosed fault to one bounded, policy-validated patch.

    Raises `ContractViolation` when the class is deliberately refused, when the
    class is unknown, or when the repair's precondition is already satisfied —
    a system that proposes a patch changing nothing is not a maintenance loop.
    """

    refusal = REPAIR_REFUSALS.get(fault_class)
    if refusal is not None:
        raise ContractViolation(
            f"fault class {fault_class!r} is never automatically repaired: {refusal}"
        )
    policy = _POLICY_BY_FAULT_CLASS.get(fault_class)
    if policy is None:
        raise ContractViolation(
            f"fault class {fault_class!r} has no admitted repair policy"
        )
    if not policy.admits_kind(executor_kind):
        raise ContractViolation(
            f"repair policy {policy.fault_class} does not admit executor kind "
            f"{executor_kind!r}"
        )

    if policy is AUTHORITY_POLICY:
        patch = _resolve_authority_policy(policy, action_config)
    elif policy is PROVIDER_FAILURE:
        patch = _resolve_provider_failure(policy, node_settings, error_code)
    else:  # pragma: no cover - defended against an unwired policy
        raise ContractViolation(
            f"repair policy {policy.fault_class} has no resolver"
        )

    _validate_against_policy(policy, patch)
    return ResolvedRepair(policy=policy, target=policy.target, patch=patch)
