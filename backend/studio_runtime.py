"""Version-pinned Action/Agent graph runtime for Kyn.ist Agent Studio."""

from __future__ import annotations

import json
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from .contracts import (
    ActionBlocked,
    DEFAULT_APPROVAL_MESSAGE_CHARS,
    DEFAULT_STUDIO_OUTPUT_TOKENS,
    MAX_ACCEPTANCE_CRITERIA,
    MAX_FLOW_NODES,
    PLACEHOLDER_RE,
    RETRYABLE_ERROR_CODES,
    BrakeEngaged,
    Conflict,
    ContractViolation,
    ProviderFailure,
    canonical_json,
    default_node_settings,
    extract_output_text,
    fingerprint,
    function_calls,
    normalize_json_schema,
    redact,
    render_prompt,
    require_completed_response,
    require_string,
    safe_response_summary,
    stateless_replay_items,
    validate_json_schema,
)
from .runtime import ResponseTransport
from .context_store import ContextStore
from .stop_seam import (
    AcceptanceCriterion,
    EvidenceBundle,
    EvidenceRecord,
    adjudicate,
)
from .studio_store import StudioStore


NODE_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
FAN_OUT_VERSION_ID = "fanout-v1"
FAN_OUT_MEMBER_TYPES = frozenset({"action", "agent", "flow"})
CALLABLE_ACTION_KINDS = frozenset(
    {
        "template",
        "condition",
        "router",
        "sandbox",
        "transform",
        "assert",
        "smart_read",
        "knowledge_search",
        "memory_recall",
    }
)
# The Action kinds whose executor mints a run effect, and the kind that mints a
# human approval decision. Named once, beside the executor that mints them, so
# the publication guard and the runtime cannot drift on what a node can produce.
EFFECT_MINTING_ACTION_KINDS = frozenset({"sandbox", "data_store"})
APPROVAL_ACTION_KIND = "approval"
PROVIDER_DETAIL_FIELDS = frozenset(
    {"provider_code", "provider_type", "provider_param", "status", "request_id"}
)
# A parent inheriting a subflow refusal cites the dead ends behind it. The brake
# refuses on the first canonical match, so this bound keeps one event small
# without ever hiding the match that actually caused the refusal.
MAX_CITED_DEAD_ENDS = 3
# The provider boundary accepts 256 KiB for the complete request. Keep the
# evidence question and the fully rendered request below that boundary here,
# before a completed graph reaches external I/O at the stop seam. A large Run
# therefore fails closed with a contract error rather than depending on a
# provider-specific rejection.
MAX_ADJUDICATION_QUESTION_BYTES = 96 * 1024
MAX_ADJUDICATION_REQUEST_BYTES = 240 * 1024
# Context read Actions expose their structured result and, when their immutable
# output contract asks for it, one directly mappable cited string. Twelve
# thousand characters matches the BoardRoom input boundary: a Flow may pass the
# envelope straight into a council without a hidden truncation step. Wider reads
# fail closed and tell the author to choose a narrower SmartRead policy.
MAX_TRANSFER_CONTEXT_CHARS = 12_000
# `candidate_json` / `evidence_json` keep existing forensic Agents usable as
# independent judges; the two explicit names are preferred for new judge Prompts.
JUDGE_PROMPT_VARIABLES = frozenset(
    {"acceptance_criteria", "run_evidence", "candidate_json", "evidence_json"}
)


@dataclass(frozen=True)
class ActionResult:
    output: Any
    route_outcome: str
    paused: bool = False
    approval_message: str | None = None
    child_run_id: str | None = None


@dataclass(frozen=True)
class GoalJudgement:
    """The model's bounded claim, kept distinct from the runtime's decision.

    A judgement can nominate anchors and explain why. It cannot admit a Run:
    `adjudicate` independently narrows those nominations against code-owned
    records, and only that deterministic result reaches the terminal seam.
    """

    agent_version_id: str
    assessment: str
    claimed: Mapping[str, tuple[str, ...]]
    reasons: Mapping[str, str]
    marked_unevidenced: Mapping[str, bool]


def _bounded_context_envelope(parts: Sequence[str], *, operation: str) -> str:
    envelope = "\n\n".join(part for part in parts if part)
    if len(envelope) > MAX_TRANSFER_CONTEXT_CHARS:
        raise ContractViolation(
            f"{operation} transferable context exceeds {MAX_TRANSFER_CONTEXT_CHARS:,} "
            "characters; choose a narrower read or fewer results"
        )
    return envelope


def _smart_read_envelope(result: Mapping[str, Any]) -> str:
    records = [*(result.get("headings") or []), *(result.get("passages") or [])]
    return _bounded_context_envelope(
        [
            f"[{record['citation']['label']} · "
            f"{str(record['citation']['fingerprint'])[:12]}]\n{record['text']}"
            for record in records
        ],
        operation="SmartRead",
    )


def _knowledge_search_envelope(result: Mapping[str, Any]) -> str:
    return _bounded_context_envelope(
        [
            f"[{record['citation']['label']} · "
            f"{str(record['citation']['fingerprint'])[:12]}]\n{record['text']}"
            for record in result.get("results", [])
        ],
        operation="Knowledge search",
    )


def _memory_recall_envelope(result: Mapping[str, Any]) -> str:
    return _bounded_context_envelope(
        [
            f"[Memory · {record['title']} · {str(record['fingerprint'])[:12]} · "
            f"source Run {record['provenance']['source_run_id']}]\n{record['content']}"
            for record in result.get("results", [])
        ],
        operation="Memory recall",
    )


def _safe_provider_detail(error: ProviderFailure) -> dict[str, Any]:
    detail: dict[str, Any] = {}
    for field in PROVIDER_DETAIL_FIELDS:
        value = error.detail.get(field)
        if field == "status" and isinstance(value, int) and not isinstance(value, bool):
            detail[field] = value
        elif isinstance(value, str) and 0 < len(value) <= 128:
            detail[field] = value
    return detail


def _public_error_message(
    error: ContractViolation | ProviderFailure | ActionBlocked | BrakeEngaged,
) -> str:
    if not isinstance(error, ProviderFailure):
        return str(error)
    detail = _safe_provider_detail(error)
    diagnostics = [
        str(detail[field])
        for field in ("provider_code", "provider_type", "provider_param")
        if field in detail
    ]
    return f"{error} ({', '.join(diagnostics)})" if diagnostics else str(error)


def validate_flow_definition(
    *,
    start_node_id: Any,
    nodes: Any,
    routes: Any,
) -> tuple[str, list[dict[str, Any]], list[dict[str, str]]]:
    start = require_string(start_node_id, "Flow start node", maximum=64)
    if not NODE_ID_RE.fullmatch(start):
        raise ContractViolation("Flow start node has an invalid id")
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= MAX_FLOW_NODES:
        raise ContractViolation("Flow must contain between one and sixty-four nodes")
    normalized_nodes: list[dict[str, Any]] = []
    ids: set[str] = set()
    for index, node in enumerate(nodes):
        required_node_keys = {"id", "type", "version_id", "input_mapping"}
        optional_node_keys = {"position", "settings", "members", "barrier"}
        if (
            not isinstance(node, dict)
            or not required_node_keys.issubset(node)
            or not set(node).issubset(required_node_keys | optional_node_keys)
        ):
            raise ContractViolation(f"Flow node {index} has an invalid shape")
        node_id = require_string(node["id"], f"Flow node {index} id", maximum=64)
        if not NODE_ID_RE.fullmatch(node_id) or node_id in ids:
            raise ContractViolation("Flow node ids must be unique lowercase slugs")
        ids.add(node_id)
        node_type = node["type"]
        if node_type not in {"action", "agent", "flow", "fan_out"}:
            raise ContractViolation(
                "Flow node type must be action, agent, flow, or fan_out"
            )
        version_id = require_string(
            node["version_id"], f"Flow node {node_id} version", maximum=80
        )
        normalized_members: list[dict[str, str]] | None = None
        normalized_barrier: dict[str, Any] | None = None
        if node_type == "fan_out":
            if version_id != FAN_OUT_VERSION_ID:
                raise ContractViolation("fan-out node must pin fanout-v1")
            members = node.get("members")
            if not isinstance(members, list) or not 2 <= len(members) <= 8:
                raise ContractViolation("fan-out node must contain two to eight members")
            member_ids: set[str] = set()
            member_targets: set[tuple[str, str]] = set()
            normalized_members = []
            for member_index, member in enumerate(members):
                if not isinstance(member, dict) or set(member) != {
                    "id",
                    "type",
                    "version_id",
                }:
                    raise ContractViolation(
                        f"fan-out member {member_index} has an invalid shape"
                    )
                member_id = require_string(
                    member["id"], f"fan-out member {member_index} id", maximum=64
                )
                member_type = member["type"]
                member_version = require_string(
                    member["version_id"],
                    f"fan-out member {member_id} version",
                    maximum=80,
                )
                if not NODE_ID_RE.fullmatch(member_id) or member_id in member_ids:
                    raise ContractViolation("fan-out member ids must be unique lowercase slugs")
                if member_type not in FAN_OUT_MEMBER_TYPES:
                    raise ContractViolation("fan-out member type is not supported")
                target = (str(member_type), member_version)
                if target in member_targets:
                    raise ContractViolation(
                        "fan-out members must pin distinct target versions"
                    )
                member_ids.add(member_id)
                member_targets.add(target)
                normalized_members.append(
                    {
                        "id": member_id,
                        "type": str(member_type),
                        "version_id": member_version,
                    }
                )
            barrier = node.get("barrier")
            if not isinstance(barrier, dict) or set(barrier) != {
                "mode",
                "quorum",
                "verdict_path",
                "affirmative_values",
                "on_member_error",
            }:
                raise ContractViolation("fan-out barrier has an invalid shape")
            mode = barrier["mode"]
            quorum = barrier["quorum"]
            if mode not in {"all", "quorum"}:
                raise ContractViolation("fan-out barrier mode is invalid")
            if (
                not isinstance(quorum, int)
                or isinstance(quorum, bool)
                or not 1 <= quorum <= len(normalized_members)
            ):
                raise ContractViolation("fan-out quorum is impossible")
            if mode == "all" and quorum != len(normalized_members):
                raise ContractViolation("an all barrier quorum must equal its member count")
            verdict_path = _normalize_path(
                barrier["verdict_path"], "fan-out verdict path"
            )
            affirmative_values = barrier["affirmative_values"]
            if (
                not isinstance(affirmative_values, list)
                or not 1 <= len(affirmative_values) <= 8
                or len({canonical_json(item) for item in affirmative_values})
                != len(affirmative_values)
            ):
                raise ContractViolation("fan-out affirmative values are invalid")
            if barrier["on_member_error"] not in {"isolate", "fail_fast"}:
                raise ContractViolation("fan-out member error policy is invalid")
            normalized_barrier = {
                "mode": str(mode),
                "quorum": quorum,
                "verdict_path": verdict_path,
                "affirmative_values": json.loads(canonical_json(affirmative_values)),
                "on_member_error": str(barrier["on_member_error"]),
            }
        elif "members" in node or "barrier" in node:
            raise ContractViolation("only fan-out nodes may declare members or a barrier")
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
        position = node.get(
            "position",
            {"x": 160 + (index % 4) * 260, "y": 140 + (index // 4) * 180},
        )
        if not isinstance(position, dict) or set(position) != {"x", "y"}:
            raise ContractViolation(f"Flow node {node_id} position is invalid")
        normalized_position: dict[str, int] = {}
        for axis in ("x", "y"):
            coordinate = position[axis]
            if (
                not isinstance(coordinate, (int, float))
                or isinstance(coordinate, bool)
                or not math.isfinite(coordinate)
                or not -20_000 <= coordinate <= 20_000
            ):
                raise ContractViolation(f"Flow node {node_id} position is out of bounds")
            normalized_position[axis] = int(round(coordinate))
        settings = node.get("settings", default_node_settings())
        if not isinstance(settings, dict) or set(settings) != {
            "max_attempts",
            "backoff_seconds",
            "retry_on",
            "on_error",
        }:
            raise ContractViolation(f"Flow node {node_id} settings are invalid")
        max_attempts = settings["max_attempts"]
        backoff_seconds = settings["backoff_seconds"]
        retry_on = settings["retry_on"]
        on_error = settings["on_error"]
        if not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or not 1 <= max_attempts <= 3:
            raise ContractViolation(f"Flow node {node_id} max_attempts must be between one and three")
        if (
            not isinstance(backoff_seconds, (int, float))
            or isinstance(backoff_seconds, bool)
            or not 0 <= backoff_seconds <= 5
        ):
            raise ContractViolation(f"Flow node {node_id} backoff_seconds is invalid")
        if (
            not isinstance(retry_on, list)
            or len(retry_on) > 3
            or any(item not in RETRYABLE_ERROR_CODES for item in retry_on)
            or len(set(retry_on)) != len(retry_on)
        ):
            raise ContractViolation(f"Flow node {node_id} retry_on is invalid")
        if on_error not in {"fail", "continue"}:
            raise ContractViolation(f"Flow node {node_id} on_error is invalid")
        if node_type == "fan_out" and max_attempts != 1:
            raise ContractViolation("fan-out nodes execute exactly once per Run")
        normalized_node: dict[str, Any] = {
                "id": node_id,
                "type": node_type,
                "version_id": version_id,
                "input_mapping": normalized_mapping,
                "position": normalized_position,
                "settings": {
                    "max_attempts": max_attempts,
                    "backoff_seconds": float(backoff_seconds),
                    "retry_on": list(retry_on),
                    "on_error": on_error,
                },
            }
        if normalized_members is not None and normalized_barrier is not None:
            normalized_node["members"] = normalized_members
            normalized_node["barrier"] = normalized_barrier
        normalized_nodes.append(normalized_node)
    if start not in ids:
        raise ContractViolation("Flow start node does not exist")
    if not isinstance(routes, list) or len(routes) > 192:
        raise ContractViolation("Flow routes are invalid or exceed the limit")
    normalized_routes: list[dict[str, str]] = []
    unique_routes: set[tuple[str, str]] = set()
    adjacency: dict[str, list[str]] = {node_id: [] for node_id in ids}
    for index, route in enumerate(routes):
        if not isinstance(route, dict) or set(route) != {"from", "to", "outcome"}:
            raise ContractViolation(f"Flow route {index} has an invalid shape")
        source = require_string(route["from"], "Flow route source", maximum=64)
        target = require_string(route["to"], "Flow route target", maximum=64)
        outcome = require_string(route["outcome"], "Flow route outcome", maximum=64)
        if source not in ids or target not in ids or source == target:
            raise ContractViolation("Flow route references invalid nodes")
        if not NODE_ID_RE.fullmatch(outcome):
            raise ContractViolation("Flow route outcome has an invalid id")
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


def action_mints_effect(executor_kind: Any, config: Any) -> bool:
    """Whether a pinned Action can ever mint a run effect.

    Kind alone is not the predicate. A Store Action pinned with
    `write_enabled: false` is refused by the very check its executor applies on
    every attempt, so it can never mint an effect however the Run goes.
    """

    if executor_kind not in EFFECT_MINTING_ACTION_KINDS:
        return False
    if not isinstance(config, Mapping):
        return False
    return config.get("write_enabled", True) is True


def validate_acceptance_contract(
    *,
    acceptance_criteria: Sequence[Mapping[str, Any]],
    judge_agent_version_id: str | None,
    nodes: Sequence[Mapping[str, Any]],
    node_contracts: Mapping[str, Mapping[str, Any]],
    subflow_cast: Mapping[str, frozenset[str]] = MappingProxyType({}),
) -> None:
    """Refuse, at publication, two declarations no Run could ever redeem.

    This runs beside `validate_flow_definition` rather than inside it because
    both refusals read the *resolved* graph — what each node's pinned target can
    actually mint, and which Agent versions the graph already casts — while that
    function is deliberately pure over the node shapes alone. Both refusals are
    decidable without executing anything, so they cost no Runs.

    A Flow that declares no criteria reaches neither loop, which is the whole
    inertness guarantee.
    """

    for criterion in acceptance_criteria:
        evidence_kind = str(criterion["evidence_kind"])
        # Checked for *every* named site, not merely one. A criterion naming two
        # sites promises a reader that either could carry the claim, so
        # admitting an incapable site beside a capable one would let the
        # contract read as stronger than the graph can honour.
        for site in criterion["node_ids"]:
            node_id = str(site)
            # Membership was already proved when the criteria were normalized
            # against the pinned node set, so an absent contract here would be a
            # caller defect; treating it as an empty contract fails closed.
            contract = node_contracts.get(node_id) or {}
            # A Flow may not declare a contract its own pinned graph cannot
            # possibly satisfy. A `step` is minted for every node by the runtime
            # itself. Receipts belong only to Action nodes; the other two name
            # still narrower capabilities: only a writing Action mints an effect,
            # and only a human-approval Action mints an approval decision. A
            # subflow node satisfies none of those three — its work is
            # minted against the child Run, so it can never be anchored to this
            # one.
            satisfiable = {
                "effect": bool(contract.get("mints_effect")),
                "receipt": bool(contract.get("mints_receipt")),
                "approval": contract.get("executor_kind") == APPROVAL_ACTION_KIND,
            }.get(evidence_kind, True)
            if not satisfiable:
                raise ContractViolation(
                    f"Flow acceptance criterion {criterion['id']} demands "
                    f"{evidence_kind} evidence from node {node_id}, whose "
                    "pinned target can never mint it"
                )

    if judge_agent_version_id is None:
        return
    # Independence is a property of the casting, not of the prompt. Nobody
    # grades their own homework, and an Agent pinned one indirection deep behind
    # an AI Action — or down inside a pinned subflow — is still cast in the work
    # it would be judging. `subflow_cast` carries the transitive casting the
    # store read off each pinned subflow version, so the whole pinned set is
    # checked rather than only the nodes this Flow declares itself.
    cast_by: dict[str, list[str]] = {}
    for node in nodes:
        node_id = str(node["id"])
        if node["type"] == "agent":
            cast_by.setdefault(str(node["version_id"]), []).append(node_id)
        pinned_agent = (node_contracts.get(node_id) or {}).get("agent_version_id")
        if pinned_agent:
            cast_by.setdefault(str(pinned_agent), []).append(node_id)
        for pinned_agent in (node_contracts.get(node_id) or {}).get(
            "agent_version_ids", ()
        ):
            cast_by.setdefault(str(pinned_agent), []).append(node_id)
        for inherited in subflow_cast.get(node_id, ()):
            cast_by.setdefault(str(inherited), []).append(node_id)
    casting = cast_by.get(judge_agent_version_id)
    if casting:
        raise ContractViolation(
            "Flow judge Agent version is already cast by "
            f"node {', '.join(sorted(set(casting)))}, so it would adjudicate "
            "its own work"
        )


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


def fan_out_outcomes(mode: str) -> list[dict[str, str]]:
    ids = (
        ("success", "partial", "error")
        if mode == "all"
        else ("converged", "review", "error")
    )
    tones = {
        "success": "success",
        "converged": "success",
        "partial": "warning",
        "review": "warning",
        "error": "danger",
    }
    return [
        {
            "id": outcome,
            "label": outcome.replace("-", " ").title(),
            "description": "",
            "tone": tones[outcome],
        }
        for outcome in ids
    ]


def fan_out_output_schema(
    member_contracts: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    member_properties: dict[str, Any] = {}
    for member_id, contract in member_contracts.items():
        fields = {
            "status": {
                "type": "string",
                "enum": ["completed", "blocked", "failed"],
            },
            "outcome": {"type": "string", "maxLength": 64},
            "output": contract["output_schema"],
            "error_code": {"type": "string", "maxLength": 80},
            "error": {"type": "string", "maxLength": 500},
            "step_id": {"type": "string", "maxLength": 80},
            "duration_ms": {"type": "integer", "minimum": 0},
        }
        member_properties[member_id] = {
            "type": "object",
            "properties": fields,
            "required": ["status", "outcome", "step_id", "duration_ms"],
            "additionalProperties": False,
        }
    barrier_fields = {
        "mode": {"type": "string", "enum": ["all", "quorum"]},
        "expected": {"type": "integer", "minimum": 2, "maximum": 8},
        "completed": {"type": "integer", "minimum": 0, "maximum": 8},
        "failed": {"type": "integer", "minimum": 0, "maximum": 8},
        "affirmative": {"type": "integer", "minimum": 0, "maximum": 8},
        "quorum": {"type": "integer", "minimum": 1, "maximum": 8},
        "converged": {"type": "boolean"},
        "dissenting_members": {
            "type": "array",
            "items": {"type": "string", "maxLength": 64},
            "maxItems": 8,
        },
    }
    return normalize_json_schema(
        {
            "type": "object",
            "properties": {
                "members": {
                    "type": "object",
                    "properties": member_properties,
                    "required": list(member_properties),
                    "additionalProperties": False,
                },
                "barrier": {
                    "type": "object",
                    "properties": barrier_fields,
                    "required": list(barrier_fields),
                    "additionalProperties": False,
                },
            },
            "required": ["members", "barrier"],
            "additionalProperties": False,
        },
        "fan-out output schema",
    )


class StudioRuntime:
    def __init__(
        self,
        repository: StudioStore,
        client: ResponseTransport,
        *,
        max_output_tokens: int = DEFAULT_STUDIO_OUTPUT_TOKENS,
        context: ContextStore | None = None,
    ) -> None:
        self.repository = repository
        self.client = client
        self.max_output_tokens = max_output_tokens
        self.context = context

    def execute(
        self,
        workspace_id: str,
        flow_id: str,
        *,
        input_data: Mapping[str, Any],
        flow_version: int | None = None,
        parent_run_id: str | None = None,
        parent_step_id: str | None = None,
        relation_kind: str | None = None,
        correlation_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        run = self.prepare(
            workspace_id,
            flow_id,
            input_data=input_data,
            flow_version=flow_version,
            parent_run_id=parent_run_id,
            parent_step_id=parent_step_id,
            relation_kind=relation_kind,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
        )
        if run["status"] != "created":
            return run
        return self.continue_run(workspace_id, run["id"])

    def prepare(
        self,
        workspace_id: str,
        flow_id: str,
        *,
        input_data: Mapping[str, Any],
        flow_version: int | None = None,
        parent_run_id: str | None = None,
        parent_step_id: str | None = None,
        relation_kind: str | None = None,
        correlation_id: str | None = None,
        idempotency_key: str | None = None,
        model_override: str | None = None,
        comparison_id: str | None = None,
        pinned_model: str | None = None,
    ) -> dict[str, Any]:
        """Persist a fully pinned Run before any worker or provider call starts.

        `model_override` is the one field that is not read off the pinned graph.
        It is deliberately absent from `execute`, so the only way a Run acquires
        one is a caller that reached this method directly and supplied the
        comparison it belongs to.
        """

        context = self.repository.flow_context(workspace_id, flow_id, flow_version)
        validated_input = validate_json_schema(
            dict(input_data), context["version"]["input_schema"], "Run input"
        )
        self._enforce_brake(workspace_id, context["version"])
        run_id, created = self.repository.create_run(
            workspace_id,
            flow_id,
            input_data=validated_input,
            flow_version=int(context["version"]["version"]),
            parent_run_id=parent_run_id,
            parent_step_id=parent_step_id,
            relation_kind=relation_kind,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            model_override=model_override,
            comparison_id=comparison_id,
            pinned_model=pinned_model,
        )
        if not created:
            return self.repository.get_run(workspace_id, run_id)
        self.repository.append_event(
            workspace_id,
            run_id,
            event_type="run.queued",
            actor_type="runtime",
            actor_id=None,
            payload={"current_node_id": context["version"]["start_node_id"]},
        )
        return self.repository.get_run(workspace_id, run_id)

    def _enforce_brake(
        self, workspace_id: str, flow_version: Mapping[str, Any]
    ) -> None:
        """Refuse a candidate Run of a Flow version a canonical dead end VETOES.

        The check runs after the Flow context resolves and before `create_run`,
        so a refused Run leaves no Run row, no Step, no event, and no effect.
        That guarantee is exactly why the scope is the pinned Flow *version* and
        not the traversed path: the path is chosen by data that does not exist
        until the Run runs, so it cannot be known here.
        """

        verdict = self.repository.check_brake(
            workspace_id, flow_version_id=flow_version["id"]
        )
        if not verdict["refused"]:
            return
        match = verdict["matches"][0]
        raise BrakeEngaged(
            "A canonical dead end already proves this pinned Flow version fails. "
            "Repair the Flow to publish a successor version.",
            detail={**match, "matches": verdict["matches"]},
        )

    def continue_run(self, workspace_id: str, run_id: str) -> dict[str, Any]:
        run = self.repository.get_run(workspace_id, run_id)
        if run["status"] == "created":
            context = self.repository.flow_context(
                workspace_id, run["flow_id"], int(run["flow_version"])
            )
            self.repository.transition_run(
                workspace_id,
                run_id,
                status="running",
                current_node_id=context["version"]["start_node_id"],
            )
        elif run["status"] != "running":
            return run
        return self._drive(workspace_id, run_id)

    def resume_after_approval(self, workspace_id: str, run_id: str) -> dict[str, Any]:
        run = self.repository.get_run(workspace_id, run_id)
        if run["status"] != "running":
            return run
        return self._drive(workspace_id, run_id)

    def explain_diagnosis(
        self,
        workspace_id: str,
        run_id: str,
        step_id: str,
        *,
        agent_version_id: str,
        candidate: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Let a pinned diagnostician explain code-owned evidence without widening it."""

        agent = self.repository.get_agent_runtime(workspace_id, agent_version_id)
        schema = {
            "type": "object",
            "properties": {
                "root_cause": {"type": "string", "minLength": 12, "maxLength": 500},
                "explanation": {"type": "string", "minLength": 20, "maxLength": 1500},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "evidence_event_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 12,
                },
            },
            "required": [
                "root_cause",
                "explanation",
                "confidence",
                "evidence_event_ids",
            ],
            "additionalProperties": False,
        }
        payload = {
            "model": self._effective_model(workspace_id, run_id, agent),
            "instructions": (
                "You are the pinned Kyn.ist diagnostician. Explain only the supplied "
                "code-owned causal candidate. Every claim must cite supplied event IDs. "
                "Do not invent a different fault class, authority, effect, or repair path."
            ),
            "input": [
                {
                    "role": "user",
                    "content": canonical_json(dict(candidate)),
                }
            ],
            "tool_choice": "none",
            "parallel_tool_calls": False,
            "max_output_tokens": min(self.max_output_tokens, 1_200),
            "store": False,
            "reasoning": {"effort": "medium"},
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "kyn_grounded_diagnosis",
                    "schema": schema,
                    "strict": True,
                }
            },
            "metadata": {
                "kyn_surface": "agent-studio",
                "run_id": run_id,
                "step_id": step_id,
                "agent_version_id": agent["id"],
                "operation": "diagnosis",
            },
        }
        response = self._call_and_record(
            workspace_id, run_id, step_id, agent, payload
        )
        try:
            parsed = json.loads(extract_output_text(response))
        except json.JSONDecodeError:
            raise ContractViolation("diagnostician output is not valid JSON") from None
        return validate_json_schema(parsed, schema, "diagnostician output")

    def _drive(self, workspace_id: str, run_id: str) -> dict[str, Any]:
        with self.repository.store.operation_session():
            return self._drive_in_session(workspace_id, run_id)

    def _drive_in_session(self, workspace_id: str, run_id: str) -> dict[str, Any]:
        run = self.repository.get_run(workspace_id, run_id)
        context = self.repository.flow_context(
            workspace_id, run["flow_id"], int(run["flow_version"])
        )
        nodes = {node["id"]: node for node in context["version"]["nodes"]}
        routes = context["version"]["routes"]
        completed_outputs = {
            step["node_id"]: step["output"]
            for step in run["steps"]
            if step["status"] == "completed" and step.get("member_id") is None
        }
        node_id = run["current_node_id"]
        last_output = run["output"]
        terminal_resume_outcome: str | None = None
        if node_id is None:
            completed_steps = [
                step
                for step in run["steps"]
                if step["status"] == "completed" and step.get("member_id") is None
            ]
            if completed_steps:
                last_step = completed_steps[-1]
                last_output = last_step["output"]
                if last_step["route_outcome"] is not None:
                    terminal_resume_outcome = self._flow_terminal_outcome(
                        context["version"], last_step["route_outcome"]
                    )
        traversed = 0
        while node_id is not None:
            traversed += 1
            if traversed > len(nodes) + 1:
                return self._fail_run(
                    workspace_id,
                    run_id,
                    "flow_traversal_exhausted",
                    "Flow traversal exceeded its pinned node count",
                    node_id=node_id,
                )
            node = nodes.get(node_id)
            if node is None:
                return self._fail_run(
                    workspace_id,
                    run_id,
                    "missing_node",
                    "Pinned Flow node is missing",
                    node_id=node_id,
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
            except (ContractViolation, ProviderFailure, ActionBlocked) as error:
                return self._fail_run(
                    workspace_id,
                    run_id,
                    error.code,
                    _public_error_message(error),
                    status="blocked" if isinstance(error, ActionBlocked) else "failed",
                    node_id=node_id,
                )

            settings = node.get("settings", default_node_settings())
            continued_after_error = False
            for attempt in range(1, int(settings["max_attempts"]) + 1):
                live_run = self.repository.get_run(workspace_id, run_id)
                if live_run["status"] != "running":
                    return live_run
                step_id = self.repository.start_step(
                    workspace_id,
                    run_id,
                    node_id=node_id,
                    node_type=node["type"],
                    target_version_id=node["version_id"],
                    input_data=mapped_input,
                )
                try:
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
                            attempt=attempt,
                            invocation_key=f"node:{node_id}:attempt:{attempt}",
                        )
                    elif node["type"] == "agent":
                        result = self._invoke_agent_node(
                            workspace_id,
                            run_id,
                            step_id,
                            node_id=node_id,
                            agent_version_id=node["version_id"],
                            input_data=mapped_input,
                        )
                    elif node["type"] == "flow":
                        result = self._invoke_subflow(
                            workspace_id,
                            run_id,
                            step_id,
                            node_id=node_id,
                            flow_version_id=node["version_id"],
                            input_data=mapped_input,
                        )
                    else:
                        result = self._invoke_fan_out(
                            workspace_id,
                            run_id,
                            step_id,
                            node=node,
                            input_data=mapped_input,
                        )
                except (
                    ContractViolation,
                    ProviderFailure,
                    ActionBlocked,
                    BrakeEngaged,
                ) as error:
                    public_message = _public_error_message(error)
                    # A refusal is a deliberate stop, not a fault: both
                    # `ActionBlocked` and `BrakeEngaged` land the Run in
                    # `blocked` so the surface reads the same either way.
                    terminal_status = (
                        "blocked"
                        if isinstance(error, (ActionBlocked, BrakeEngaged))
                        else "failed"
                    )
                    if isinstance(error, BrakeEngaged):
                        # A braked subflow must terminate its parent legibly. Left
                        # uncaught it escapes the drive loop entirely: the parent
                        # Run and this Step strand in `running` on the synchronous
                        # path, and the async worker reports it as an unexplained
                        # `worker_failure`. Carry the refusal's citations into the
                        # parent's own evidence before the Step closes, so the
                        # parent cites the Runs that actually proved the dead end.
                        self._record_subflow_refusal(
                            workspace_id,
                            run_id,
                            node_id=node_id,
                            step_id=step_id,
                            error=error,
                        )
                    try:
                        self.repository.finish_step(
                            workspace_id,
                            run_id,
                            step_id,
                            status=terminal_status,
                            output=None,
                            route_outcome="error",
                            error_code=error.code,
                            error_message=public_message,
                        )
                    except (Conflict, ContractViolation):
                        return self.repository.get_run(workspace_id, run_id)
                    if (
                        attempt < int(settings["max_attempts"])
                        and error.code in settings["retry_on"]
                    ):
                        self.repository.append_event(
                            workspace_id,
                            run_id,
                            event_type="step.retry_scheduled",
                            actor_type="runtime",
                            actor_id=None,
                            payload={
                                "node_id": node_id,
                                "failed_attempt": attempt,
                                "next_attempt": attempt + 1,
                                "error_code": error.code,
                                "backoff_seconds": settings["backoff_seconds"],
                            },
                        )
                        if float(settings["backoff_seconds"]) > 0:
                            time.sleep(float(settings["backoff_seconds"]))
                        continue
                    error_target = self._next_node(routes, node_id, "error")
                    if settings["on_error"] == "continue" and error_target is not None:
                        last_output = {
                            "error": {"code": error.code, "message": public_message}
                        }
                        completed_outputs[node_id] = last_output
                        node_id = error_target
                        continued_after_error = True
                        break
                    return self._fail_run(
                        workspace_id,
                        run_id,
                        error.code,
                        public_message,
                        status=terminal_status,
                        node_id=node_id,
                    )
                if result.paused:
                    self.repository.finish_step(
                        workspace_id,
                        run_id,
                        step_id,
                        status="waiting_approval",
                        output=result.output,
                        route_outcome=(
                            "approved" if result.child_run_id is None else "waiting"
                        ),
                    )
                    if result.child_run_id is None:
                        next_node = self._next_node(routes, node_id, "approved")
                        self.repository.create_approval_request(
                            workspace_id,
                            run_id,
                            step_id,
                            node_id=node_id,
                            message=result.approval_message or "Human approval required",
                            context=mapped_input,
                        )
                    else:
                        next_node = node_id
                        self.repository.append_event(
                            workspace_id,
                            run_id,
                            event_type="subflow.waiting",
                            actor_type="runtime",
                            actor_id=None,
                            payload={
                                "node_id": node_id,
                                "step_id": step_id,
                                "child_run_id": result.child_run_id,
                            },
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
                    flow_outcome = self._flow_terminal_outcome(
                        context["version"], result.route_outcome
                    )
                    return self._complete_run(
                        workspace_id,
                        run_id,
                        output=last_output,
                        outcome=flow_outcome,
                        flow_version=context["version"],
                    )
                node_id = next_node
                break
            if continued_after_error:
                continue
        return self._complete_run(
            workspace_id,
            run_id,
            output=last_output,
            outcome=terminal_resume_outcome or "success",
            flow_version=context["version"],
        )

    def _record_subflow_refusal(
        self,
        workspace_id: str,
        run_id: str,
        *,
        node_id: str,
        step_id: str | None,
        error: BrakeEngaged,
    ) -> None:
        """Carry a braked subflow's citations into the parent's own evidence.

        The parent inherits a legible refusal rather than an opaque failure: the
        event names the Runs that proved the dead end, so the parent's ledger
        can be audited without reading the child's. Only fields the refusal
        already published are copied, and nothing here mints new evidence — the
        parent re-proved nothing, so `brake_engaged` is deliberately outside
        `RATIFIABLE_FAULTS`.
        """

        matches = error.detail.get("matches") or []
        if not isinstance(matches, list):
            matches = []
        self.repository.append_event(
            workspace_id,
            run_id,
            event_type="subflow.brake_engaged",
            actor_type="runtime",
            actor_id=None,
            payload={
                "node_id": node_id,
                "step_id": step_id,
                "matches": [
                    {
                        "fingerprint": match.get("fingerprint"),
                        "flow_version_id": match.get("flow_version_id"),
                        "node_id": match.get("node_id"),
                        "error_code": match.get("error_code"),
                        "ratification_state": match.get("ratification_state"),
                        "distinct_runs": match.get("distinct_runs"),
                        "citing_run_ids": list(match.get("citing_run_ids") or []),
                    }
                    for match in matches[:MAX_CITED_DEAD_ENDS]
                    if isinstance(match, Mapping)
                ],
            },
        )

    ADJUDICATION_SCHEMA: Mapping[str, Any] = MappingProxyType(
        {
            "type": "object",
            "properties": {
                "assessment": {"type": "string", "minLength": 20, "maxLength": 1200},
                "criteria": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": MAX_ACCEPTANCE_CRITERIA,
                    "items": {
                        "type": "object",
                        "properties": {
                            "criterion_id": {"type": "string"},
                            "unevidenced": {"type": "boolean"},
                            "anchors": {
                                "type": "array",
                                "items": {"type": "string"},
                                "maxItems": 8,
                            },
                            "reason": {
                                "type": "string",
                                "minLength": 8,
                                "maxLength": 400,
                            },
                        },
                        "required": [
                            "criterion_id",
                            "unevidenced",
                            "anchors",
                            "reason",
                        ],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["assessment", "criteria"],
            "additionalProperties": False,
        }
    )

    def _record_adjudication(
        self,
        workspace_id: str,
        run_id: str,
        adjudication: Any,
        judgement: GoalJudgement,
    ) -> None:
        """Put the adjudication in the ledger whichever way it went.

        Recording only refusals would make the happy path unauditable and would
        quietly imply that an unrecorded completion was never questioned.
        """

        self.repository.append_event(
            workspace_id,
            run_id,
            event_type=(
                "completion.admitted"
                if adjudication.admitted
                else "completion.refused"
            ),
            actor_type="runtime",
            actor_id=None,
            payload={
                "admitted": adjudication.admitted,
                "unevidenced": list(adjudication.unevidenced),
                # Explicitly a claim, never the decision. Keeping the model's
                # narrative is what makes a refusal explainable without letting
                # prose become authority over the terminal transition.
                "judge_claim": {
                    "agent_version_id": judgement.agent_version_id,
                    "assessment": judgement.assessment,
                    "criteria": [
                        {
                            "criterion_id": criterion_id,
                            "marked_unevidenced": judgement.marked_unevidenced[
                                criterion_id
                            ],
                            "claimed_anchors": list(judgement.claimed[criterion_id]),
                            "reason": judgement.reasons[criterion_id],
                        }
                        for criterion_id in judgement.claimed
                    ],
                },
                "criteria": [
                    {
                        "criterion_id": resolution.criterion_id,
                        "statement": resolution.statement,
                        "evidence_kind": resolution.evidence_kind,
                        "declared_sites": list(resolution.node_ids),
                        "holds": resolution.holds,
                        "surviving": list(resolution.surviving),
                        "discarded": [
                            {
                                "anchor_id": item.anchor_id,
                                "refusal": item.refusal,
                                "reason": item.reason,
                            }
                            for item in resolution.discarded
                        ],
                    }
                    for resolution in adjudication.resolutions
                ],
            },
        )

    def _adjudication_step_id(self, run: Mapping[str, Any]) -> str | None:
        """Attach the judge's model call to the last Step that finished work.

        A model call needs a Step to hang from, and no Step may be created once
        a Run is terminal, so the judge borrows the Run's own last Step exactly
        as diagnosis borrows the failed one.
        """

        for step in reversed(run["steps"]):
            if step["status"] == "completed" and step.get("member_id") is None:
                return step["id"]
        graph_steps = [step for step in run["steps"] if step.get("member_id") is None]
        return graph_steps[-1]["id"] if graph_steps else None

    def _adjudicate_completion(
        self,
        workspace_id: str,
        run_id: str,
        flow_version: Mapping[str, Any],
    ) -> Any:
        """Bind a completion claim to evidence before the claim becomes true.

        Returns `None` when the pinned Flow version declares no contract, which
        is the default and costs a model call nothing. Otherwise the Goal-Judge
        is shown this Run's evidence as it actually is — not pre-filtered to
        records that already qualify, which would pre-decide the question it
        exists to answer — and must anchor every criterion it holds satisfied.
        """

        declared = list(flow_version.get("acceptance_criteria") or ())
        if not declared:
            return None
        run = self.repository.get_run(workspace_id, run_id)
        evidence = self.repository.adjudication_evidence(workspace_id, run_id)
        candidates = evidence["candidates"]
        criteria = tuple(
            AcceptanceCriterion(
                id=str(item["id"]),
                statement=str(item["statement"]),
                evidence_kind=str(item["evidence_kind"]),
                node_ids=tuple(item.get("node_ids") or ()),
            )
            for item in declared
        )
        judgement = self._call_goal_judge(
            workspace_id,
            run_id,
            run,
            flow_version,
            criteria=criteria,
            candidates=candidates,
        )
        claimed = judgement.claimed
        offered = {
            record["id"]
            for records in candidates.values()
            for record in records
        }
        # Gate one: anti-fabrication. Code decided what this Run contains, and
        # the judge may only speak about that. An anchor outside it is not a
        # weak claim to be filtered later — it is a broken contract, because
        # the judge cited something code never offered.
        for anchors in claimed.values():
            outside = [anchor for anchor in anchors if anchor not in offered]
            if outside:
                raise ContractViolation(
                    "Goal-Judge cited evidence outside the code-owned candidate"
                )
        # Gate two: anti-irrelevance, independently and over a bundle that can
        # actually see what was claimed.
        #
        # The evidence is fetched a second time, now with the claimed ids, and
        # this is not redundant I/O. The first fetch is scoped to this Run
        # because that is what the judge may speak about; resolving against it
        # would make ownership true by construction, so `anchor_foreign_run`
        # could never fire, deleting the ownership check would break no test,
        # and a borrowed anchor would be reported as merely unresolvable. That
        # collapse is the one this design named as forbidden, and it arrived
        # anyway — through a parameter that existed and simply was not passed.
        #
        # Gate one still refuses a borrowed anchor first, so this gate's
        # ownership check is defence in depth by construction rather than by
        # accident: it is what remains correct, and correctly *diagnosed*, if
        # gate one is ever removed.
        resolved = self.repository.adjudication_evidence(
            workspace_id,
            run_id,
            anchor_ids=[anchor for anchors in claimed.values() for anchor in anchors],
        )
        bundle = self._evidence_bundle(run_id, resolved["records"])
        return adjudicate(criteria, claimed, bundle), judgement

    @staticmethod
    def _evidence_bundle(
        run_id: str, records: Mapping[str, Sequence[Mapping[str, Any]]]
    ) -> EvidenceBundle:
        def collection(name: str) -> tuple[EvidenceRecord, ...]:
            return tuple(
                EvidenceRecord(
                    id=str(item["id"]),
                    run_id=str(item["run_id"]),
                    state=item["state"],
                    node_id=item["node_id"],
                )
                for item in records.get(name, ())
            )

        return EvidenceBundle(
            run_id=run_id,
            effects=collection("effects"),
            receipts=collection("receipts"),
            approvals=collection("approvals"),
            steps=collection("steps"),
        )

    def _call_goal_judge(
        self,
        workspace_id: str,
        run_id: str,
        run: Mapping[str, Any],
        flow_version: Mapping[str, Any],
        *,
        criteria: Sequence[AcceptanceCriterion],
        candidates: Mapping[str, Sequence[Mapping[str, Any]]],
    ) -> GoalJudgement:
        """Ask the pinned judge which criteria are *unevidenced*, adversarially."""

        agent = self.repository.get_agent_runtime(
            workspace_id, flow_version["judge_agent_version_id"]
        )
        step_id = self._adjudication_step_id(run)
        if step_id is None:
            raise ContractViolation("a Run with no Step cannot be adjudicated")
        question = {
            "acceptance_criteria": [
                {
                    "criterion_id": item.id,
                    "statement": item.statement,
                    "evidence_kind": item.evidence_kind,
                    "declared_sites": list(item.node_ids),
                }
                for item in criteria
            ],
            "run_evidence": {
                name: [
                    {
                        "id": record["id"],
                        "kind": record["kind"],
                        "site": record["node_id"],
                        "state": record["state"],
                        "content": redact(record.get("content")),
                    }
                    for record in records
                ]
                for name, records in candidates.items()
            },
        }
        serialized_question = canonical_json(question)
        if len(serialized_question.encode("utf-8")) > MAX_ADJUDICATION_QUESTION_BYTES:
            raise ContractViolation(
                "Run evidence exceeds the bounded Goal-Judge context"
            )
        prompt_variables = tuple(agent["prompt"]["variables"])
        unsupported_variables = sorted(set(prompt_variables) - JUDGE_PROMPT_VARIABLES)
        if unsupported_variables:
            raise ContractViolation(
                "Goal-Judge Prompt declares unsupported variables: "
                + ", ".join(unsupported_variables)
            )
        prompt_sources = {
            "acceptance_criteria": question["acceptance_criteria"],
            "run_evidence": question["run_evidence"],
            "candidate_json": question["acceptance_criteria"],
            "evidence_json": question["run_evidence"],
        }
        prompt_values = {
            variable: canonical_json(prompt_sources[variable])
            for variable in prompt_variables
        }
        pinned_prompt = render_prompt(
            agent["prompt"]["template"],
            declared_variables=prompt_variables,
            values=prompt_values,
            maximum_output=MAX_ADJUDICATION_QUESTION_BYTES,
        )
        seam_instructions = (
            "You are operating at the Kyn.ist stop seam. The Run claims it is "
            "finished; that claim is evidence, not proof. For each acceptance "
            "criterion decide whether the supplied Run evidence actually shows "
            "the declared work was performed at a declared site. Ask which "
            "criteria are UNEVIDENCED and what was claimed but not performed. "
            "Anchor every criterion you consider satisfied to supplied evidence "
            "IDs; never invent an ID, never cite evidence from another Run, and "
            "prefer marking a criterion unevidenced over anchoring it to evidence "
            "that does not show the declared work. Refusing costs a rerun; wrongly "
            "admitting lets unfinished work be recorded as finished. Your text and "
            "anchor nominations are claims only; deterministic runtime resolution "
            "makes the admission decision."
        )
        payload = {
            "model": self._effective_model(workspace_id, run_id, agent),
            "instructions": (
                f"{self._agent_instructions(agent)}\n\n"
                f"Pinned Prompt {agent['prompt']['id']} "
                f"({agent['prompt']['fingerprint']}):\n{pinned_prompt}\n\n"
                f"Runtime-owned stop contract:\n{seam_instructions}"
            ),
            "input": [{"role": "user", "content": serialized_question}],
            "tool_choice": "none",
            "parallel_tool_calls": False,
            "max_output_tokens": min(self.max_output_tokens, 1_400),
            "store": False,
            "reasoning": {"effort": "medium"},
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "kyn_completion_adjudication",
                    "schema": dict(self.ADJUDICATION_SCHEMA),
                    "strict": True,
                }
            },
            "metadata": {
                "kyn_surface": "agent-studio",
                "run_id": run_id,
                "step_id": step_id,
                "agent_version_id": agent["id"],
                "operation": "adjudication",
            },
        }
        if len(canonical_json(payload).encode("utf-8")) > MAX_ADJUDICATION_REQUEST_BYTES:
            raise ContractViolation("Goal-Judge request exceeds its bounded context")
        response = self._call_and_record(
            workspace_id, run_id, step_id, agent, payload
        )
        try:
            parsed = json.loads(extract_output_text(response))
        except json.JSONDecodeError:
            raise ContractViolation("Goal-Judge output is not valid JSON") from None
        validated = validate_json_schema(
            parsed, dict(self.ADJUDICATION_SCHEMA), "Goal-Judge output"
        )
        claimed: dict[str, tuple[str, ...]] = {}
        reasons: dict[str, str] = {}
        marked_unevidenced: dict[str, bool] = {}
        for item in validated["criteria"]:
            # A criterion the judge marked unevidenced contributes no anchors
            # even if it supplied some, so a judge cannot hedge its way to an
            # admission by refusing in prose while anchoring in data.
            anchors = () if item["unevidenced"] else tuple(item["anchors"])
            criterion_id = str(item["criterion_id"])
            if criterion_id in claimed:
                raise ContractViolation(
                    "Goal-Judge output must address each criterion exactly once"
                )
            claimed[criterion_id] = anchors
            reasons[criterion_id] = str(item["reason"])
            marked_unevidenced[criterion_id] = bool(item["unevidenced"])
        declared_ids = {criterion.id for criterion in criteria}
        if set(claimed) != declared_ids:
            raise ContractViolation(
                "Goal-Judge output must address every declared criterion exactly once"
            )
        return GoalJudgement(
            agent_version_id=str(agent["id"]),
            assessment=str(validated["assessment"]),
            claimed=MappingProxyType(claimed),
            reasons=MappingProxyType(reasons),
            marked_unevidenced=MappingProxyType(marked_unevidenced),
        )

    def _complete_run(
        self,
        workspace_id: str,
        run_id: str,
        *,
        output: Any,
        outcome: str,
        flow_version: Mapping[str, Any],
    ) -> dict[str, Any]:
        try:
            if flow_version["output_schema"] is not None:
                validate_json_schema(output, flow_version["output_schema"], "Flow output")
            completion = self._adjudicate_completion(
                workspace_id, run_id, flow_version
            )
        except (ContractViolation, ProviderFailure) as error:
            return self._fail_run(
                workspace_id,
                run_id,
                error.code,
                _public_error_message(error),
            )
        if completion is not None:
            adjudication, judgement = completion
            self._record_adjudication(
                workspace_id, run_id, adjudication, judgement
            )
            if not adjudication.admitted:
                return self._fail_run(
                    workspace_id,
                    run_id,
                    "completion_unevidenced",
                    "The completion claim is not covered by resolved evidence: "
                    + ", ".join(adjudication.unevidenced)
                    + " went unevidenced.",
                    node_id=self.repository.get_run(workspace_id, run_id)[
                        "current_node_id"
                    ],
                )
        self.repository.transition_run(
            workspace_id,
            run_id,
            status="completed",
            current_node_id=None,
            output=output,
            outcome=outcome,
        )
        return self.repository.get_run(workspace_id, run_id)

    def _fail_run(
        self,
        workspace_id: str,
        run_id: str,
        code: str,
        message: str,
        *,
        status: str = "failed",
        node_id: str | None = None,
    ) -> dict[str, Any]:
        run = self.repository.get_run(workspace_id, run_id)
        if run["status"] not in {"running", "created"}:
            return run
        self.repository.transition_run(
            workspace_id,
            run_id,
            status=status,
            current_node_id=None,
            error_code=code,
            error_message=message[:500],
            outcome="error",
        )
        if node_id is not None and status in {"failed", "blocked"}:
            # The Run is terminal and its write transaction is closed. Mint the
            # append-only evidence of the exact approach that just failed.
            self.repository.record_dead_end(
                workspace_id,
                run_id,
                flow_version_id=str(run["flow_version_id"]),
                node_id=node_id,
                error_code=code,
                detail=message,
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
        if node["type"] == "fan_out":
            members = node.get("members")
            if not isinstance(members, list) or not members:
                raise ContractViolation("Pinned fan-out has no members")
            return self._member_contract(workspace_id, members[0])["input_schema"]
        if node["type"] == "action":
            return self.repository.get_action_version(
                workspace_id, node["version_id"]
            )["input_schema"]
        if node["type"] == "flow":
            flow = self.repository.get_flow_version_by_id(
                workspace_id, node["version_id"]
            )
            return flow["input_schema"]
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
        if node["type"] == "fan_out":
            members = node.get("members")
            if not isinstance(members, list) or not members:
                raise ContractViolation("Pinned fan-out has no members")
            return fan_out_output_schema(
                {
                    str(member["id"]): self._member_contract(workspace_id, member)
                    for member in members
                }
            )
        if node["type"] == "action":
            return self.repository.get_action_version(
                workspace_id, node["version_id"]
            )["output_schema"]
        if node["type"] == "flow":
            flow = self.repository.get_flow_version_by_id(
                workspace_id, node["version_id"]
            )
            if flow["output_schema"] is None:
                raise ContractViolation("Pinned subflow has no output contract")
            return flow["output_schema"]
        return {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        }

    def _member_contract(
        self, workspace_id: str, member: Mapping[str, Any]
    ) -> dict[str, Any]:
        if member["type"] == "action":
            action = self.repository.get_action_version(
                workspace_id, member["version_id"]
            )
            return {
                "input_schema": action["input_schema"],
                "output_schema": action["output_schema"],
            }
        if member["type"] == "flow":
            flow = self.repository.get_flow_version_by_id(
                workspace_id, member["version_id"]
            )
            if flow["output_schema"] is None:
                raise ContractViolation("Pinned fan-out member Flow has no output contract")
            return {
                "input_schema": flow["input_schema"],
                "output_schema": flow["output_schema"],
            }
        agent = self.repository.get_agent_runtime(
            workspace_id, member["version_id"]
        )
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
        return exact[0] if exact else None

    @staticmethod
    def _flow_terminal_outcome(
        flow_version: Mapping[str, Any], node_outcome: str
    ) -> str:
        declared = {item["id"] for item in flow_version["outcomes"]}
        if node_outcome in declared:
            return node_outcome
        if "success" in declared:
            return "success"
        raise ContractViolation("terminal node outcome is not declared by the Flow")

    def _invoke_fan_out(
        self,
        workspace_id: str,
        run_id: str,
        step_id: str,
        *,
        node: Mapping[str, Any],
        input_data: Mapping[str, Any],
    ) -> ActionResult:
        members = node.get("members")
        barrier = node.get("barrier")
        if not isinstance(members, list) or not isinstance(barrier, dict):
            raise ContractViolation("Pinned fan-out composition is missing")
        self.repository.append_event(
            workspace_id,
            run_id,
            event_type="fan_out.dispatched",
            actor_type="runtime",
            actor_id=None,
            payload={
                "step_id": step_id,
                "node_id": node["id"],
                "member_ids": [member["id"] for member in members],
                "barrier": barrier,
                "input_fingerprint": fingerprint(dict(input_data)),
            },
        )
        completed: dict[str, dict[str, Any]] = {}
        # Two to eight is publication-validated. One worker per pinned member is
        # therefore a hard concurrency bound rather than an elastic pool.
        with ThreadPoolExecutor(
            max_workers=len(members), thread_name_prefix="kyn-fanout"
        ) as pool:
            futures = {
                pool.submit(
                    self._invoke_fan_out_member,
                    workspace_id,
                    run_id,
                    step_id,
                    node_id=str(node["id"]),
                    member=member,
                    input_data=json.loads(canonical_json(dict(input_data))),
                ): str(member["id"])
                for member in members
            }
            for future in as_completed(futures):
                member_id = futures[future]
                try:
                    completed[member_id] = future.result()
                except BaseException as error:
                    # A worker's own executor closes every Step for all expected
                    # runtime failures. Reaching this branch means a coding or
                    # infrastructure failure escaped before it could construct a
                    # truthful member record, so the parent must fail rather than
                    # manufacture a result or a step id.
                    raise ContractViolation(
                        f"fan-out member {member_id} escaped its execution boundary"
                    ) from error
        ordered = {str(member["id"]): completed[str(member["id"])] for member in members}
        completed_count = sum(
            item["status"] == "completed" for item in ordered.values()
        )
        failed_count = len(ordered) - completed_count
        affirmative_values = {
            canonical_json(item) for item in barrier["affirmative_values"]
        }
        affirmative = 0
        dissenting: list[str] = []
        for member_id, item in ordered.items():
            if item["status"] != "completed":
                continue
            try:
                verdict = _value_at(
                    item["output"],
                    barrier["verdict_path"],
                    field=f"fan-out member {member_id} verdict",
                )
            except ContractViolation:
                dissenting.append(member_id)
                continue
            if canonical_json(verdict) in affirmative_values:
                affirmative += 1
            else:
                dissenting.append(member_id)
        mode = str(barrier["mode"])
        quorum = int(barrier["quorum"])
        converged = (
            completed_count == len(members)
            if mode == "all"
            else affirmative >= quorum
        )
        barrier_output = {
            "mode": mode,
            "expected": len(members),
            "completed": completed_count,
            "failed": failed_count,
            "affirmative": affirmative,
            "quorum": quorum,
            "converged": converged,
            "dissenting_members": dissenting,
        }
        self.repository.append_event(
            workspace_id,
            run_id,
            event_type="fan_out.barrier_reached",
            actor_type="runtime",
            actor_id=None,
            payload={
                "step_id": step_id,
                "node_id": node["id"],
                "member_step_ids": {
                    member_id: item["step_id"] for member_id, item in ordered.items()
                },
                "barrier": barrier_output,
            },
        )
        if failed_count and barrier["on_member_error"] == "fail_fast":
            failed_members = [
                member_id
                for member_id, item in ordered.items()
                if item["status"] != "completed"
            ]
            raise ContractViolation(
                "fan-out member failure closed the node: " + ", ".join(failed_members)
            )
        if mode == "all":
            outcome = (
                "success"
                if completed_count == len(members)
                else ("partial" if completed_count else "error")
            )
        else:
            outcome = (
                "converged"
                if affirmative >= quorum
                else ("review" if completed_count else "error")
            )
        return ActionResult(
            output={"members": ordered, "barrier": barrier_output},
            route_outcome=outcome,
        )

    def _invoke_fan_out_member(
        self,
        workspace_id: str,
        run_id: str,
        parent_step_id: str,
        *,
        node_id: str,
        member: Mapping[str, Any],
        input_data: Mapping[str, Any],
    ) -> dict[str, Any]:
        started = time.monotonic()
        member_id = str(member["id"])
        step_id: str | None = None
        with self.repository.store.operation_session():
            try:
                contract = self._member_contract(workspace_id, member)
                validated_input = validate_json_schema(
                    dict(input_data),
                    contract["input_schema"],
                    f"fan-out member {member_id} input",
                )
                step_id = self.repository.start_step(
                    workspace_id,
                    run_id,
                    node_id=node_id,
                    node_type=str(member["type"]),
                    target_version_id=str(member["version_id"]),
                    input_data=validated_input,
                    parent_step_id=parent_step_id,
                    member_id=member_id,
                )
                if member["type"] == "action":
                    action = self.repository.get_action_version(
                        workspace_id, member["version_id"]
                    )
                    result = self._invoke_action(
                        workspace_id,
                        run_id,
                        step_id,
                        node_id=node_id,
                        action=action,
                        input_data=validated_input,
                        attempt=1,
                        invocation_key=f"fanout:{parent_step_id}:{member_id}",
                    )
                elif member["type"] == "agent":
                    result = self._invoke_agent_node(
                        workspace_id,
                        run_id,
                        step_id,
                        node_id=node_id,
                        agent_version_id=str(member["version_id"]),
                        input_data=validated_input,
                    )
                else:
                    result = self._invoke_subflow(
                        workspace_id,
                        run_id,
                        step_id,
                        node_id=node_id,
                        flow_version_id=str(member["version_id"]),
                        input_data=validated_input,
                        invocation_key=f"fanout:{parent_step_id}:{member_id}",
                    )
                if result.paused:
                    raise ContractViolation("fan-out member attempted to pause at its barrier")
                validated_output = validate_json_schema(
                    result.output,
                    contract["output_schema"],
                    f"fan-out member {member_id} output",
                )
                self.repository.finish_step(
                    workspace_id,
                    run_id,
                    step_id,
                    status="completed",
                    output=validated_output,
                    route_outcome=result.route_outcome,
                )
                return {
                    "status": "completed",
                    "outcome": result.route_outcome,
                    "output": validated_output,
                    "step_id": step_id,
                    "duration_ms": max(0, int((time.monotonic() - started) * 1_000)),
                }
            except (
                ContractViolation,
                ProviderFailure,
                ActionBlocked,
                BrakeEngaged,
                Conflict,
            ) as error:
                status = (
                    "blocked"
                    if isinstance(error, (ActionBlocked, BrakeEngaged))
                    else "failed"
                )
                message = _public_error_message(error)
                if step_id is not None:
                    try:
                        self.repository.finish_step(
                            workspace_id,
                            run_id,
                            step_id,
                            status=status,
                            output=None,
                            route_outcome="error",
                            error_code=error.code,
                            error_message=message,
                        )
                    except (Conflict, ContractViolation):
                        pass
                if step_id is None:
                    raise
                return {
                    "status": status,
                    "outcome": "error",
                    "error_code": error.code,
                    "error": message[:500],
                    "step_id": step_id,
                    "duration_ms": max(0, int((time.monotonic() - started) * 1_000)),
                }

    def _invoke_subflow(
        self,
        workspace_id: str,
        run_id: str,
        step_id: str,
        *,
        node_id: str,
        flow_version_id: str,
        input_data: Mapping[str, Any],
        invocation_key: str | None = None,
    ) -> ActionResult:
        target = self.repository.get_flow_version_by_id(
            workspace_id, flow_version_id
        )
        child = self.execute(
            workspace_id,
            target["flow_id"],
            input_data=input_data,
            flow_version=int(target["version"]),
            parent_run_id=run_id,
            parent_step_id=step_id,
            relation_kind="subflow",
            correlation_id=self.repository.get_run(workspace_id, run_id)[
                "correlation_id"
            ],
            idempotency_key=(
                f"subflow:{run_id}:{invocation_key}"
                if invocation_key is not None
                else f"subflow:{run_id}:{node_id}"
            ),
        )
        if child["status"] == "waiting_approval":
            return ActionResult(
                output={"child_run_id": child["id"], "status": child["status"]},
                route_outcome="waiting",
                paused=True,
                child_run_id=child["id"],
            )
        if child["status"] == "completed":
            return ActionResult(
                output=child["output"],
                route_outcome=child["outcome"] or "success",
            )
        if child["status"] == "blocked":
            raise ActionBlocked(
                f"Subflow {target['name']} blocked: {child['error_message'] or 'authority denied'}"
            )
        raise ContractViolation(
            f"Subflow {target['name']} ended as {child['status']}: "
            f"{child['error_message'] or child['error_code'] or 'unknown failure'}"
        )

    def resume_parent_from_subflow(
        self, workspace_id: str, child_run_id: str
    ) -> dict[str, Any] | None:
        child = self.repository.get_run(workspace_id, child_run_id)
        if (
            child["relation_kind"] != "subflow"
            or child["parent_run_id"] is None
            or child["parent_step_id"] is None
            or child["status"] not in {"completed", "blocked", "failed", "cancelled"}
        ):
            return None
        parent = self.repository.get_run(workspace_id, child["parent_run_id"])
        if parent["status"] != "waiting_approval":
            return parent
        step = next(
            (
                item
                for item in parent["steps"]
                if item["id"] == child["parent_step_id"]
            ),
            None,
        )
        if step is None or step["status"] != "waiting_approval":
            raise Conflict("parent subflow Step is no longer waiting")
        context = self.repository.flow_context(
            workspace_id, parent["flow_id"], int(parent["flow_version"])
        )
        routes = context["version"]["routes"]
        if child["status"] == "completed":
            output = validate_json_schema(
                child["output"],
                self._node_output_schema(
                    workspace_id,
                    next(
                        node
                        for node in context["version"]["nodes"]
                        if node["id"] == step["node_id"]
                    ),
                ),
                f"subflow node {step['node_id']} output",
            )
            route_outcome = child["outcome"] or "success"
            self.repository.finish_step(
                workspace_id,
                parent["id"],
                step["id"],
                status="completed",
                output=output,
                route_outcome=route_outcome,
            )
            next_node = self._next_node(routes, step["node_id"], route_outcome)
            self.repository.transition_run(
                workspace_id,
                parent["id"],
                status="running",
                current_node_id=next_node,
                output=output,
            )
            return self._drive(workspace_id, parent["id"])
        message = child["error_message"] or f"Subflow ended as {child['status']}"
        self.repository.finish_step(
            workspace_id,
            parent["id"],
            step["id"],
            status="blocked" if child["status"] == "blocked" else "failed",
            output=None,
            route_outcome="error",
            error_code=child["error_code"] or "subflow_failure",
            error_message=message,
        )
        return self._fail_run(
            workspace_id,
            parent["id"],
            child["error_code"] or "subflow_failure",
            message,
            status="blocked" if child["status"] == "blocked" else "failed",
            node_id=str(step["node_id"]),
        )

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
        try:
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
            elif kind == "transform":
                transformed: dict[str, Any] = {}
                for target, source in action["config"]["mappings"].items():
                    if source["source"] == "literal":
                        transformed[target] = json.loads(canonical_json(source["value"]))
                    else:
                        transformed[target] = _value_at(
                            validated_input,
                            source["path"],
                            field=f"Action {action['slug']} mapping for {target}",
                        )
                result = ActionResult(output=transformed, route_outcome="success")
            elif kind == "delay":
                milliseconds = int(action["config"]["milliseconds"])
                if milliseconds:
                    time.sleep(milliseconds / 1_000)
                result = ActionResult(
                    output=json.loads(canonical_json(validated_input)),
                    route_outcome="success",
                )
            elif kind in {"condition", "assert"}:
                actual = _value_at(
                    validated_input,
                    action["config"]["path"],
                    field=f"Action {action['slug']} condition",
                )
                matched = self._compare(
                    actual, action["config"]["operator"], action["config"]["value"]
                )
                if kind == "assert" and not matched:
                    raise ActionBlocked(action["config"]["message"])
                result = ActionResult(
                    output=(
                        {"passed": True, "actual": actual}
                        if kind == "assert"
                        else {"matched": matched, "actual": actual}
                    ),
                    route_outcome=("success" if kind == "assert" else ("true" if matched else "false")),
                )
            elif kind == "router":
                selected = action["config"]["fallback_outcome"]
                actual: Any = None
                for branch in action["config"]["branches"]:
                    actual = _value_at(
                        validated_input,
                        branch["path"],
                        field=f"Action {action['slug']} router",
                    )
                    if self._compare(actual, branch["operator"], branch["value"]):
                        selected = branch["outcome"]
                        break
                result = ActionResult(
                    output={"outcome": selected, "actual": actual},
                    route_outcome=selected,
                )
            elif kind == "smart_read":
                if self.context is None:
                    raise ContractViolation("SmartRead context repository is unavailable")
                mode = action["config"]["mode"]
                options: dict[str, Any] = {"mode": mode}
                if mode == "focus":
                    options.update(
                        line_start=validated_input["line_start"],
                        line_end=validated_input["line_end"],
                    )
                elif mode == "grep":
                    options.update(
                        query=validated_input["query"],
                        max_results=validated_input["max_results"],
                    )
                output = self.context.smart_read(
                    workspace_id,
                    validated_input["source_version_id"],
                    **options,
                )
                if "context" in action["output_schema"]["properties"]:
                    output = {**output, "context": _smart_read_envelope(output)}
                result = ActionResult(output=output, route_outcome="success")
            elif kind == "knowledge_search":
                if self.context is None:
                    raise ContractViolation("Knowledge context repository is unavailable")
                output = self.context.search_knowledge(
                    workspace_id,
                    validated_input["query"],
                    max_results=validated_input["max_results"],
                )
                if "context" in action["output_schema"]["properties"]:
                    output = {**output, "context": _knowledge_search_envelope(output)}
                result = ActionResult(output=output, route_outcome="success")
            elif kind == "memory_recall":
                if self.context is None:
                    raise ContractViolation("Memory context repository is unavailable")
                output = self.context.search_memories(
                    workspace_id,
                    validated_input["query"],
                    max_results=validated_input["max_results"],
                )
                if "context" in action["output_schema"]["properties"]:
                    output = {**output, "context": _memory_recall_envelope(output)}
                result = ActionResult(output=output, route_outcome="success")
            elif kind == APPROVAL_ACTION_KIND:
                template = action["config"]["message_template"]
                variables = sorted(set(PLACEHOLDER_RE.findall(template)))
                message = render_prompt(
                    template,
                    declared_variables=variables,
                    values={variable: validated_input[variable] for variable in variables},
                    maximum_output=action["config"].get(
                        "max_message_chars", DEFAULT_APPROVAL_MESSAGE_CHARS
                    ),
                    output_label="approval message",
                )
                result = ActionResult(
                    output={"pending": True},
                    route_outcome="approved",
                    paused=True,
                    approval_message=message,
                )
            elif kind in EFFECT_MINTING_ACTION_KINDS:
                if action["config"].get("write_enabled", True) is not True:
                    raise ActionBlocked(
                        "The pinned Data Store Action policy does not authorize this write."
                    )
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
        except (ContractViolation, ProviderFailure, ActionBlocked) as error:
            self.repository.record_receipt(
                workspace_id,
                run_id,
                step_id,
                node_id=node_id,
                action_version_id=action["id"],
                attempt=attempt,
                outcome="denied" if isinstance(error, ActionBlocked) else "failed",
                input_data=validated_input,
                output={"error": {"code": error.code, "message": _public_error_message(error)}},
                error_code=error.code,
                idempotency_key=receipt_key,
            )
            raise
        try:
            if not result.paused:
                validate_json_schema(
                    result.output,
                    action["output_schema"],
                    f"Action {action['slug']} output",
                )
            declared_outcomes = {item["id"] for item in action["outcomes"]}
            if result.route_outcome not in declared_outcomes:
                raise ContractViolation(
                    f"Action {action['slug']} emitted undeclared outcome "
                    f"{result.route_outcome}"
                )
        except ContractViolation as error:
            self.repository.record_receipt(
                workspace_id,
                run_id,
                step_id,
                node_id=node_id,
                action_version_id=action["id"],
                attempt=attempt,
                outcome="failed",
                input_data=validated_input,
                output={
                    "error": {
                        "code": error.code,
                        "message": _public_error_message(error),
                    }
                },
                error_code=error.code,
                idempotency_key=receipt_key,
            )
            raise
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
        max_output_tokens = int(
            action["config"].get("max_output_tokens", self.max_output_tokens)
        )
        used_tool_calls = 0
        effective_model = self._effective_model(workspace_id, run_id, agent)
        while True:
            payload: dict[str, Any] = {
                "model": effective_model,
                "instructions": instructions,
                "input": input_items,
                "parallel_tool_calls": False,
                "max_output_tokens": max_output_tokens,
                "store": False,
                "reasoning": {"effort": action["config"]["reasoning_effort"]},
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "kyn_action_output",
                        "schema": action["output_schema"],
                        "strict": True,
                    }
                },
                "metadata": {
                    "kyn_surface": "agent-studio",
                    "run_id": run_id,
                    "step_id": step_id,
                    "node_id": node_id,
                    "agent_version_id": agent["id"],
                },
            }
            if tool_definitions:
                payload["tools"] = tool_definitions
                payload["include"] = ["reasoning.encrypted_content"]
                payload["tool_choice"] = (
                    "auto" if used_tool_calls < max_tool_calls else "none"
                )
            else:
                payload["tool_choice"] = "none"
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
                outcome_path = action["config"].get("outcome_path")
                route_outcome = (
                    _value_at(
                        output,
                        outcome_path,
                        field=f"AI Action {action['slug']} outcome",
                    )
                    if outcome_path
                    else "success"
                )
                if not isinstance(route_outcome, str):
                    raise ContractViolation("AI Action outcome must be a string")
                return ActionResult(output=output, route_outcome=route_outcome)
            if used_tool_calls + len(calls) > max_tool_calls:
                raise ContractViolation("Agent exceeded the pinned Action-call budget")
            input_items.extend(stateless_replay_items(response))
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
            "model": self._effective_model(workspace_id, run_id, agent),
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

    def _effective_model(
        self, workspace_id: str, run_id: str, agent: Mapping[str, Any]
    ) -> str:
        """Resolve the model for one call: the Run's override, else the pinned one.

        This is the *only* place the pinned Agent is not taken literally, and it
        substitutes exactly one field. Instructions, Prompt, Skills, granted
        Actions, schemas and reasoning effort are read straight off the pinned
        Agent at every call site, so a comparison sibling differs from a normal
        Run in the model string and in nothing else.
        """

        override = self.repository.run_model_override(workspace_id, run_id)
        return str(override) if override else str(agent["model"])

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
        input_hash = fingerprint(payload)
        try:
            response = self.client.create(payload)
        except ProviderFailure as error:
            detail = _safe_provider_detail(error)
            request_id = detail.get("request_id")
            self.repository.record_model_call(
                workspace_id,
                run_id,
                step_id,
                agent_version_id=agent["id"],
                provider_response_id=(
                    request_id if isinstance(request_id, str) else "unavailable"
                ),
                status="failed",
                model=str(payload.get("model", "unknown"))[:100],
                input_hash=input_hash,
                output_hash=fingerprint(
                    {"error_code": error.code, "provider_detail": detail}
                ),
                usage={},
                request_id=request_id if isinstance(request_id, str) else None,
            )
            raise
        summary = safe_response_summary(response)
        self.repository.record_model_call(
            workspace_id,
            run_id,
            step_id,
            agent_version_id=agent["id"],
            provider_response_id=summary["provider_response_id"],
            status=summary["status"],
            model=summary["model"],
            input_hash=input_hash,
            output_hash=fingerprint(response),
            usage=summary["usage"],
            request_id=(
                str(response.get("_request_id"))[:128]
                if response.get("_request_id")
                else None
            ),
        )
        require_completed_response(
            response,
            max_output_tokens=payload.get("max_output_tokens"),
        )
        return response
