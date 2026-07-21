"""Small deterministic contracts shared by the runtime and HTTP boundary."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Collection, Iterable, Mapping, NamedTuple, Sequence


GENESIS_HASH = "0" * 64
IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
PLACEHOLDER_RE = re.compile(r"{{([a-z][a-z0-9_]*)}}")
SECRET_KEY_RE = re.compile(
    r"(?:authorization|api[_-]?key|password|secret|token|cookie|credential)",
    re.IGNORECASE,
)


class RuntimeErrorBase(Exception):
    """Base error carrying a stable public code and status."""

    code = "runtime_error"
    http_status = 400

    def __init__(self, message: str, *, detail: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.detail = dict(detail or {})


class ContractViolation(RuntimeErrorBase, ValueError):
    code = "contract_violation"
    http_status = 422


class Conflict(RuntimeErrorBase):
    code = "conflict"
    http_status = 409


class NotFound(RuntimeErrorBase):
    code = "not_found"
    http_status = 404


class Unauthorized(RuntimeErrorBase):
    code = "unauthorized"
    http_status = 401


class OpenAIKeyRequired(Unauthorized):
    code = "openai_key_required"


class Forbidden(RuntimeErrorBase):
    code = "forbidden"
    http_status = 403


class PayloadTooLarge(RuntimeErrorBase):
    code = "body_too_large"
    http_status = 413


class RateLimited(RuntimeErrorBase):
    code = "rate_limited"
    http_status = 429


class ProviderFailure(RuntimeErrorBase):
    code = "provider_failure"
    http_status = 502


class ActionBlocked(RuntimeErrorBase):
    """A bounded executor deliberately refused an effect or violated its guard."""

    code = "action_blocked"
    http_status = 409


class BrakeEngaged(RuntimeErrorBase):
    """A canonical dead end VETOES the pinned Flow version a Run would execute.

    Scope is the Flow version, not the traversed path: which nodes a Run visits
    is decided by data that does not exist until it runs, so the path cannot be
    known before the Run is created — and refusing before creation is what makes
    "no Run row, no Step, no effect" true.
    """

    code = "brake_engaged"
    http_status = 409


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# -- Dead end ratification ------------------------------------------------
#
# A `dead_end` is durable evidence that one exact approach failed. Its
# `ratification_state` is never stored: it is derived from a count of distinct
# citing Runs, so repetition — not a model's opinion — is what promotes it.

RATIFICATION_STATES = ("proposed", "confirmed", "canonical")
CONFIRMED_DISTINCT_RUNS = 2
CANONICAL_DISTINCT_RUNS = 3

# The per-node retry contract. `RETRYABLE_ERROR_CODES` bounds which failures a
# node may re-attempt at all, and `DEFAULT_NODE_SETTINGS` is the effective policy
# of a node that declares none. Both live here because the runtime enforces them
# at publish time, the repair policy reasons about them, and the store applies a
# repair to them — one definition, so the three cannot drift.

RETRYABLE_ERROR_CODES = frozenset(
    {"provider_failure", "action_blocked", "contract_violation"}
)
MAX_NODE_ATTEMPTS = 3
DEFAULT_NODE_SETTINGS: Mapping[str, Any] = MappingProxyType(
    {
        "max_attempts": 1,
        "backoff_seconds": 0,
        "retry_on": ("provider_failure",),
        "on_error": "fail",
    }
)


def default_node_settings() -> dict[str, Any]:
    """Return a mutable copy of the default node retry policy."""

    return {
        "max_attempts": DEFAULT_NODE_SETTINGS["max_attempts"],
        "backoff_seconds": DEFAULT_NODE_SETTINGS["backoff_seconds"],
        "retry_on": list(DEFAULT_NODE_SETTINGS["retry_on"]),
        "on_error": DEFAULT_NODE_SETTINGS["on_error"],
    }

_ISO_TIMESTAMP_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:?\d{2})?"
)
_UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
_PREFIXED_ID_RE = re.compile(r"\b[a-z][a-z0-9]*_[0-9a-f]{8,}\b", re.IGNORECASE)
_HEX_BLOB_RE = re.compile(r"\b[0-9a-f]{16,}\b", re.IGNORECASE)
_DIGIT_RUN_RE = re.compile(r"\d+")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_failure_detail(value: Any, *, maximum: int = 500) -> str:
    """Strip volatile substrings so one recurring fault keeps one fingerprint.

    Run ids, step ids, UUIDs, hashes, ISO timestamps, and bare digit runs differ
    on every execution of the very same dead end. Replacing them with stable
    placeholders is what lets a second and third independent Run ratify the
    first instead of minting fresh, uncountable evidence.
    """

    text = _WHITESPACE_RE.sub(" ", str(value or "")).strip()
    text = _ISO_TIMESTAMP_RE.sub("<time>", text)
    text = _UUID_RE.sub("<id>", text)
    text = _PREFIXED_ID_RE.sub("<id>", text)
    text = _HEX_BLOB_RE.sub("<id>", text)
    text = _DIGIT_RUN_RE.sub("<n>", text)
    return text[:maximum].strip()


def dead_end_fingerprint(
    *,
    flow_version_id: str,
    node_id: str,
    error_code: str,
    normalized_detail: str,
) -> str:
    """Identify the exact failed approach a `dead_end` VETOES."""

    return fingerprint(
        {
            "flow_version_id": str(flow_version_id),
            "node_id": str(node_id),
            "error_code": str(error_code),
            "normalized_detail": str(normalized_detail),
        }
    )


def ratification_state(distinct_runs: int) -> str:
    """Derive ratification from repeated independent execution, never from state."""

    if distinct_runs >= CANONICAL_DISTINCT_RUNS:
        return "canonical"
    if distinct_runs >= CONFIRMED_DISTINCT_RUNS:
        return "confirmed"
    return "proposed"


# Counting is only half the rule. *Which* failures are allowed to be counted at
# all is the other half, and it is declared in `RATIFIABLE_FAULTS` below — it
# lives after the policy-marker vocabulary it depends on.


# -- Principle distillation -----------------------------------------------
#
# A `dead_end` pins one exact `(flow_version, node)` site. A `principle` is the
# generalization: the same *structural* failure observed across independent
# Flows. It is advisory only — it surfaces while authoring, where being wrong
# costs a reader two seconds. The brake stays the only thing that refuses, and
# only on the exact path three independent Runs proved. Warn early, refuse late.

PRINCIPLE_MIN_DEAD_ENDS = 3
PRINCIPLE_MIN_DISTINCT_FLOWS = 3


class PolicyMarker(NamedTuple):
    """One recognised configuration predicate a family of failures shares.

    The vocabulary is a small explicit table rather than ad-hoc string building
    so a principle can only ever speak about a predicate somebody declared here.
    A failure with no recognised marker produces no signature and can never
    distil into a principle — fail-closed by construction.
    """

    name: str
    executor_kind: str
    config_key: str
    denied_value: Any
    default_value: Any
    clause: str


POLICY_MARKERS: tuple[PolicyMarker, ...] = (
    PolicyMarker(
        name="write_enabled_denied",
        executor_kind="data_store",
        config_key="write_enabled",
        denied_value=False,
        default_value=True,
        clause="its pinned `write_enabled` policy is false",
    ),
)

_MARKERS_BY_NAME = {marker.name: marker for marker in POLICY_MARKERS}


def policy_marker(executor_kind: Any, config: Any) -> str | None:
    """Name the declared configuration predicate a failed Action carried."""

    if not isinstance(config, Mapping):
        return None
    kind = str(executor_kind or "")
    for marker in POLICY_MARKERS:
        if marker.executor_kind != kind:
            continue
        observed = config.get(marker.config_key, marker.default_value)
        if type(observed) is type(marker.denied_value) and observed == marker.denied_value:
            return marker.name
    return None


def principle_signature(
    *, executor_kind: Any, error_code: Any, policy_marker: Any
) -> str | None:
    """Identify a failure *structure*, deliberately coarser than a fingerprint.

    A dead end pins the flow version and node; a principle generalizes, so the
    signature drops both and keeps only what a reader could act on elsewhere.
    """

    if not executor_kind or not error_code or not policy_marker:
        return None
    if str(policy_marker) not in _MARKERS_BY_NAME:
        return None
    return fingerprint(
        {
            "executor_kind": str(executor_kind),
            "error_code": str(error_code),
            "policy_marker": str(policy_marker),
        }
    )


def principle_statement(
    *, executor_kind: Any, error_code: Any, policy_marker: Any
) -> str:
    """Render the rule from a fixed template. No model participates, ever."""

    marker = _MARKERS_BY_NAME.get(str(policy_marker))
    clause = marker.clause if marker is not None else "an unrecognised policy holds"
    return (
        f"Across at least {PRINCIPLE_MIN_DISTINCT_FLOWS} distinct Flows, a "
        f"`{executor_kind}` Action failed with `{error_code}` because {clause}. "
        "This is advisory: publishing and running stay allowed. Grant the "
        "policy on a successor Action version, or route the Flow around it."
    )


# -- Ratifiable fault classes ---------------------------------------------
#
# Minting a `dead_end` is not free. Three citations make a pinned path
# `canonical`, and the brake then refuses that Flow version for every future
# input. That is only defensible for a **structural** defect: one where the
# reason for the failure is a property of the *pinned definition*, so repeating
# the same pinned path cannot succeed no matter what data arrives. For such a
# defect the escape hatch is real — repairing it publishes a successor version
# with a new `flow_version_id`, a new fingerprint, and therefore no brake.
#
# Two failure kinds look identical to a naive count and are not structural at
# all. A validation gate rejecting bad input is the gate *working*; its message
# is author-configured and static, so three rejections of three different bad
# inputs collapse to one fingerprint, ratify, and refuse the Flow forever —
# including for valid input, and no successor version can clear it because the
# assertion is unchanged. A transient provider fault is a property of the
# moment, not of the path; there is no defect to repair, so the escape hatch is
# meaningless. Both would turn the brake from a memory into a trap.
#
# The two tables below are the entire membership rule and they are exposed
# through `ratification_policy()` so a reader can audit them. Anything not named
# in `RATIFIABLE_FAULTS` does not ratify: a brake that fires wrongly is worse
# than one that fires rarely, so the default is closed.


class RatifiableFault(NamedTuple):
    """One admitted fault class and the exact structure it must present.

    An empty `executor_kind` matches any kind. A `policy_marker` of `None` means
    the class does not require one; naming a marker requires that exact declared
    predicate from `POLICY_MARKERS`.
    """

    name: str
    error_code: str
    executor_kind: str
    policy_marker: str | None
    reason: str


class NonRatifiableFault(NamedTuple):
    """One deliberately refused fault class and why it is refused.

    These entries enforce nothing — the allow-list already refuses everything it
    does not name. They exist so the exclusions are auditable rather than
    implied by absence, exactly as `REPAIR_REFUSALS` states the classes the
    repair space will never touch.
    """

    name: str
    error_code: str
    executor_kind: str
    reason: str


RATIFIABLE_FAULTS: tuple[RatifiableFault, ...] = (
    RatifiableFault(
        name="data_store_write_denied",
        error_code="action_blocked",
        executor_kind="data_store",
        policy_marker="write_enabled_denied",
        reason=(
            "The pinned Data Store Action version declares `write_enabled` "
            "false, so the denial is a property of the definition and not of "
            "the Run input. Every future Run down this exact path is denied "
            "identically, which is precisely the claim a canonical dead end "
            "makes. Granting the policy publishes a successor Action and Flow "
            "version, so the escape hatch clears the brake by construction."
        ),
    ),
)


NON_RATIFIABLE_FAULTS: tuple[NonRatifiableFault, ...] = (
    NonRatifiableFault(
        name="assertion_rejected",
        error_code="action_blocked",
        executor_kind="assert",
        reason=(
            "An assertion is a validation gate and failing is its job. Its "
            "message is author-configured and static, so three rejections of "
            "three different bad inputs share one fingerprint and would ratify "
            "a Flow-wide refusal that valid input can never pass. Publishing a "
            "successor does not change the assertion, so it would simply "
            "re-brake: a permanent trap with no defect to repair."
        ),
    ),
    NonRatifiableFault(
        name="undeclared_policy_denial",
        error_code="action_blocked",
        executor_kind="",
        reason=(
            "A bounded executor refused an effect without carrying any declared "
            "predicate from `POLICY_MARKERS`, so nothing identifies the refusal "
            "as a property of the pinned definition rather than of this Run's "
            "data. Fail closed: an unrecognised denial is never ratified."
        ),
    ),
    NonRatifiableFault(
        name="transient_provider_fault",
        error_code="provider_failure",
        executor_kind="",
        reason=(
            "Transient by construction: `provider_failure` is a member of "
            "`RETRYABLE_ERROR_CODES`, and `repair_policy.PROVIDER_FAILURE` "
            "treats it as an under-provisioned retry budget, not a defect in "
            "any contract. Detail normalization strips digit runs, so three "
            "unrelated rate limits from one organisation collapse to one "
            "fingerprint and would ratify a permanent refusal of a path that "
            "never had anything wrong with it."
        ),
    ),
    NonRatifiableFault(
        name="run_data_contract_violation",
        error_code="contract_violation",
        executor_kind="",
        reason=(
            "A pinned input or output schema caught a value in *this* Run's "
            "data. A different input may satisfy the same schema, so repetition "
            "proves nothing about the path. Some contract violations are "
            "structural wiring faults, but the terminal code cannot distinguish "
            "them from data rejections, so the whole class fails closed."
        ),
    ),
    NonRatifiableFault(
        name="subflow_brake_refusal",
        error_code="brake_engaged",
        executor_kind="",
        reason=(
            "The parent of a braked subflow inherits a refusal, not a discovery. "
            "The underlying dead end is already canonical at the site three Runs "
            "actually proved it; letting one memory mint another would cascade "
            "the brake up the call graph on evidence nobody independently "
            "re-executed."
        ),
    ),
    NonRatifiableFault(
        name="subflow_terminal_summary",
        error_code="subflow_failure",
        executor_kind="",
        reason=(
            "The parent sees only the child's terminal summary. If the child's "
            "fault is structural it ratifies at the child's own node, where the "
            "evidence and the repair both live."
        ),
    ),
    NonRatifiableFault(
        name="graph_integrity_fault",
        error_code="missing_node",
        executor_kind="",
        reason=(
            "Publish-time validation already proves the graph is acyclic, that "
            "every route target exists, and that every node is reachable from "
            "the start node, so this and `flow_traversal_exhausted` are "
            "defensive branches with no reachable instance. A fault class that "
            "can never be observed is dead policy and the brake carries none."
        ),
    ),
    NonRatifiableFault(
        name="completion_unevidenced",
        error_code="completion_unevidenced",
        executor_kind="",
        reason=(
            "A declared acceptance criterion can go unevidenced because this "
            "Run's input never reached the work, which is a property of the "
            "data and not of the pinned definition. Ratifying it would brake a "
            "Flow that valid input still satisfies — the same trap as "
            "`assertion_rejected`, where three refusals of three different bad "
            "inputs share one fingerprint. The structural case is refused far "
            "earlier and without spending a Run: publication rejects a "
            "criterion whose evidence kind no pinned site can ever mint, "
            "because a Flow may not declare a contract its own graph cannot "
            "satisfy."
        ),
    ),
)


_RATIFIABLE_BY_CODE: Mapping[str, tuple[RatifiableFault, ...]] = MappingProxyType(
    {
        code: tuple(
            entry for entry in RATIFIABLE_FAULTS if entry.error_code == code
        )
        for code in {entry.error_code for entry in RATIFIABLE_FAULTS}
    }
)

# A ratifiable class may only name a predicate the policy vocabulary declares,
# so the two tables cannot drift into an entry that can never match.
for _entry in RATIFIABLE_FAULTS:
    if _entry.policy_marker is not None and _entry.policy_marker not in _MARKERS_BY_NAME:
        raise RuntimeError(
            f"ratifiable fault {_entry.name!r} names an undeclared policy marker"
        )


def is_ratifiable_fault(
    *,
    error_code: Any,
    executor_kind: Any,
    policy_marker: Any,
) -> bool:
    """Answer whether one terminal failure may mint dead-end evidence.

    Fail closed: a failure matches only when a declared class names its exact
    error code, admits its executor kind, and — where the class requires one —
    finds the declared policy predicate it demands.
    """

    code = str(error_code or "")
    kind = str(executor_kind or "")
    marker = None if policy_marker is None else str(policy_marker)
    for entry in _RATIFIABLE_BY_CODE.get(code, ()):
        if entry.executor_kind and entry.executor_kind != kind:
            continue
        if entry.policy_marker is not None and entry.policy_marker != marker:
            continue
        return True
    return False


def ratification_policy() -> list[dict[str, Any]]:
    """Render both tables as one auditable list, admissions first."""

    return [
        {
            "name": entry.name,
            "ratifiable": True,
            "error_code": entry.error_code,
            "executor_kind": entry.executor_kind or None,
            "policy_marker": entry.policy_marker,
            "reason": entry.reason,
        }
        for entry in RATIFIABLE_FAULTS
    ] + [
        {
            "name": entry.name,
            "ratifiable": False,
            "error_code": entry.error_code,
            "executor_kind": entry.executor_kind or None,
            "policy_marker": None,
            "reason": entry.reason,
        }
        for entry in NON_RATIFIABLE_FAULTS
    ]


JSON_SCHEMA_TYPES = frozenset(
    {"object", "array", "string", "number", "integer", "boolean", "null"}
)


def normalize_json_schema(value: Any, field: str = "JSON Schema") -> dict[str, Any]:
    """Validate and copy the bounded JSON Schema subset exposed by the Studio."""

    if not isinstance(value, dict):
        raise ContractViolation(f"{field} must be an object")
    if len(canonical_json(value).encode("utf-8")) > 16 * 1024:
        raise ContractViolation(f"{field} exceeds 16 KiB")

    def walk(schema: Any, path: str, depth: int) -> dict[str, Any]:
        if depth > 6 or not isinstance(schema, dict):
            raise ContractViolation(f"{field} has an invalid schema at {path}")
        allowed_keys = {
            "type",
            "properties",
            "required",
            "additionalProperties",
            "items",
            "enum",
            "description",
            "minLength",
            "maxLength",
            "minimum",
            "maximum",
            "minItems",
            "maxItems",
        }
        if set(schema) - allowed_keys:
            raise ContractViolation(
                f"{field} uses unsupported keywords at {path}: "
                f"{', '.join(sorted(set(schema) - allowed_keys))}"
            )
        schema_type = schema.get("type")
        if schema_type not in JSON_SCHEMA_TYPES:
            raise ContractViolation(f"{field} has an unsupported type at {path}")
        normalized: dict[str, Any] = {"type": schema_type}
        description = schema.get("description")
        if description is not None:
            normalized["description"] = require_string(
                description, f"{field} description", maximum=500
            )
        if "enum" in schema:
            enum = schema["enum"]
            if not isinstance(enum, list) or not 1 <= len(enum) <= 32:
                raise ContractViolation(f"{field} enum is invalid at {path}")
            normalized["enum"] = json.loads(canonical_json(enum))

        if schema_type == "object":
            properties = schema.get("properties", {})
            required = schema.get("required", [])
            if not isinstance(properties, dict) or len(properties) > 32:
                raise ContractViolation(f"{field} properties are invalid at {path}")
            if not isinstance(required, list) or len(set(required)) != len(required):
                raise ContractViolation(f"{field} required fields are invalid at {path}")
            if not all(isinstance(name, str) and name in properties for name in required):
                raise ContractViolation(f"{field} required fields are unknown at {path}")
            if schema.get("additionalProperties") is not False:
                raise ContractViolation(
                    f"{field} must set additionalProperties=false at {path}"
                )
            normalized_properties: dict[str, Any] = {}
            for name, child in properties.items():
                require_slug(name.replace("_", "-"), f"{field} property")
                if SECRET_KEY_RE.search(name):
                    raise ContractViolation(
                        f"{field} may not declare secret-like property {name}"
                    )
                normalized_properties[name] = walk(child, f"{path}.{name}", depth + 1)
            normalized.update(
                {
                    "properties": normalized_properties,
                    "required": list(required),
                    "additionalProperties": False,
                }
            )
        elif schema_type == "array":
            normalized["items"] = walk(schema.get("items"), f"{path}[]", depth + 1)
            for keyword in ("minItems", "maxItems"):
                if keyword in schema:
                    amount = schema[keyword]
                    if not isinstance(amount, int) or isinstance(amount, bool) or not 0 <= amount <= 100:
                        raise ContractViolation(f"{field} {keyword} is invalid at {path}")
                    normalized[keyword] = amount
            if normalized.get("minItems", 0) > normalized.get("maxItems", 100):
                raise ContractViolation(f"{field} array bounds conflict at {path}")
        elif schema_type == "string":
            for keyword, default_max in (("minLength", 0), ("maxLength", 20_000)):
                if keyword in schema:
                    amount = schema[keyword]
                    if not isinstance(amount, int) or isinstance(amount, bool) or not 0 <= amount <= 20_000:
                        raise ContractViolation(f"{field} {keyword} is invalid at {path}")
                    normalized[keyword] = amount
            if normalized.get("minLength", 0) > normalized.get("maxLength", 20_000):
                raise ContractViolation(f"{field} string bounds conflict at {path}")
        elif schema_type in {"number", "integer"}:
            for keyword in ("minimum", "maximum"):
                if keyword in schema:
                    amount = schema[keyword]
                    if not isinstance(amount, (int, float)) or isinstance(amount, bool):
                        raise ContractViolation(f"{field} {keyword} is invalid at {path}")
                    normalized[keyword] = amount
            if normalized.get("minimum", float("-inf")) > normalized.get("maximum", float("inf")):
                raise ContractViolation(f"{field} numeric bounds conflict at {path}")
        return normalized

    return walk(value, "$", 0)


def validate_json_schema(value: Any, schema: Mapping[str, Any], field: str = "value") -> Any:
    """Validate a value against the Studio's normalized JSON Schema subset."""

    def fail(path: str, message: str) -> None:
        raise ContractViolation(f"{field} {path} {message}")

    def walk(candidate: Any, current: Mapping[str, Any], path: str) -> None:
        kind = current["type"]
        valid = {
            "object": isinstance(candidate, dict),
            "array": isinstance(candidate, list),
            "string": isinstance(candidate, str),
            "number": isinstance(candidate, (int, float)) and not isinstance(candidate, bool),
            "integer": isinstance(candidate, int) and not isinstance(candidate, bool),
            "boolean": isinstance(candidate, bool),
            "null": candidate is None,
        }[kind]
        if not valid:
            fail(path, f"must be {kind}")
        if "enum" in current and candidate not in current["enum"]:
            fail(path, "is not an allowed enum value")
        if kind == "object":
            properties = current["properties"]
            missing = [name for name in current["required"] if name not in candidate]
            unexpected = sorted(set(candidate) - set(properties))
            if missing:
                fail(path, f"is missing {', '.join(missing)}")
            if unexpected:
                fail(path, f"contains unexpected fields {', '.join(unexpected)}")
            for name, child in properties.items():
                if name in candidate:
                    walk(candidate[name], child, f"{path}.{name}")
        elif kind == "array":
            if len(candidate) < current.get("minItems", 0):
                fail(path, "contains too few items")
            if len(candidate) > current.get("maxItems", 100):
                fail(path, "contains too many items")
            for index, item in enumerate(candidate):
                walk(item, current["items"], f"{path}[{index}]")
        elif kind == "string":
            if len(candidate) < current.get("minLength", 0):
                fail(path, "is too short")
            if len(candidate) > current.get("maxLength", 20_000):
                fail(path, "is too long")
        elif kind in {"number", "integer"}:
            if candidate < current.get("minimum", float("-inf")):
                fail(path, "is below the minimum")
            if candidate > current.get("maximum", float("inf")):
                fail(path, "is above the maximum")

    safe_value = json.loads(canonical_json(value))
    walk(safe_value, schema, "$")
    return safe_value


def require_string(
    value: Any,
    field: str,
    *,
    minimum: int = 1,
    maximum: int = 8_000,
) -> str:
    if not isinstance(value, str):
        raise ContractViolation(f"{field} must be a string")
    normalized = value.strip()
    if len(normalized) < minimum:
        raise ContractViolation(f"{field} is too short")
    if len(normalized) > maximum:
        raise ContractViolation(f"{field} is too long")
    return normalized


def require_slug(value: Any, field: str = "slug") -> str:
    slug = require_string(value, field, maximum=64)
    if not IDENTIFIER_RE.fullmatch(slug):
        raise ContractViolation(
            f"{field} must use lowercase letters, digits, and single hyphens"
        )
    return slug


def require_string_list(
    value: Any,
    field: str,
    *,
    maximum_items: int = 16,
    maximum_item_length: int = 128,
    allow_empty: bool = True,
) -> list[str]:
    if not isinstance(value, list):
        raise ContractViolation(f"{field} must be an array")
    if not allow_empty and not value:
        raise ContractViolation(f"{field} must not be empty")
    if len(value) > maximum_items:
        raise ContractViolation(f"{field} has too many items")
    normalized = [
        require_string(item, f"{field}[{index}]", maximum=maximum_item_length)
        for index, item in enumerate(value)
    ]
    if len(set(normalized)) != len(normalized):
        raise ContractViolation(f"{field} must not contain duplicates")
    return normalized


OUTCOME_TONES = frozenset({"neutral", "success", "warning", "danger", "ai"})


def default_outcomes_for_kind(kind: str) -> list[dict[str, str]]:
    """Compatibility contract for versions created before declared ports existed."""

    ids = {
        "condition": ("true", "false", "error"),
        "router": ("matched", "fallback", "error"),
        "approval": ("approved", "rejected", "error"),
    }.get(kind, ("success", "error"))
    tone_by_id = {
        "success": "success",
        "true": "success",
        "approved": "success",
        "false": "warning",
        "rejected": "warning",
        "fallback": "warning",
        "error": "danger",
    }
    return [
        {
            "id": outcome_id,
            "label": outcome_id.replace("-", " ").title(),
            "description": "",
            "tone": tone_by_id.get(outcome_id, "neutral"),
        }
        for outcome_id in ids
    ]


def normalize_outcomes(
    value: Any,
    field: str,
    *,
    default_kind: str | None = None,
    require_error: bool = True,
) -> list[dict[str, str]]:
    """Validate the immutable named source-port contract for an Action or Flow."""

    if value is None and default_kind is not None:
        value = default_outcomes_for_kind(default_kind)
    if not isinstance(value, list) or not 1 <= len(value) <= 12:
        raise ContractViolation(f"{field} must contain between one and twelve outcomes")
    normalized: list[dict[str, str]] = []
    ids: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict) or set(item) - {
            "id",
            "label",
            "description",
            "tone",
        } or not {"id", "label"}.issubset(item):
            raise ContractViolation(f"{field}[{index}] has an invalid shape")
        outcome_id = require_slug(item["id"], f"{field}[{index}] id")
        if outcome_id in ids:
            raise ContractViolation(f"{field} outcome ids must be unique")
        ids.add(outcome_id)
        tone = item.get("tone", "neutral")
        if tone not in OUTCOME_TONES:
            raise ContractViolation(f"{field}[{index}] tone is invalid")
        normalized.append(
            {
                "id": outcome_id,
                "label": require_string(
                    item["label"], f"{field}[{index}] label", maximum=48
                ),
                "description": (
                    require_string(
                        item["description"],
                        f"{field}[{index}] description",
                        maximum=240,
                    )
                    if item.get("description")
                    else ""
                ),
                "tone": str(tone),
            }
        )
    if require_error and "error" not in ids:
        raise ContractViolation(f"{field} must declare an error outcome")
    return normalized


# The acceptance contract a Flow version may declare. Bounded like `outcomes`,
# but lower, because the two bounds measure different things: outcomes bound how
# far a graph may fan out, while every acceptance criterion must hold at least
# one resolved evidence anchor before a Run may complete. So the ceiling here is
# what a single Run can plausibly evidence and a reader can hold in their head
# while reading a refusal — eight, deliberately under the twelve of `outcomes`.
MAX_ACCEPTANCE_CRITERIA = 8

# The Flow node ceiling, named here rather than left as a literal in the runtime
# because the acceptance contract bounds against it too: a criterion may name at
# most every node in its Flow once, so anything beyond this could only be a
# duplicate or a typo. One definition, so the two bounds cannot drift apart.
MAX_FLOW_NODES = 64


def _evidence_kind_names() -> frozenset[str]:
    """Read the evidence vocabulary from `stop_seam`, which owns it.

    Imported inside the call rather than at module scope: `stop_seam` depends on
    this module for its refusal type, so a top-level import here would close a
    cycle. The table stays owned there because that is where it is enforced
    against run-owned truth, and a second copy here is exactly the drift the
    note on `DEFAULT_NODE_SETTINGS` exists to refuse.
    """

    from .stop_seam import EVIDENCE_KINDS

    return frozenset(kind.name for kind in EVIDENCE_KINDS)


def normalize_acceptance_criteria(
    value: Any,
    field: str,
    *,
    node_ids: Collection[str] | None = None,
) -> list[dict[str, str]]:
    """Validate the immutable acceptance contract pinned to a Flow version.

    Zero criteria is the default and means the feature is inert: no judge call,
    no model spend, and the same completion path every Flow takes today.

    Each criterion names the `node_ids` whose work may evidence it, and is
    satisfied by an anchor attributable to any one of them. Without that pin an
    `effect` criterion would be satisfied by *any* effect the Run wrote, so "the
    report was published" could be carried by an unrelated store write — the
    resolver can filter fabricated and foreign-Run anchors, but it cannot filter
    irrelevance. Several sites are admitted because a Flow may branch to two
    nodes either of which legitimately does the work; a single site would fail
    such a Run structurally, while the work was genuinely done.

    The list is order-normalized because it is semantically a set: two authors
    declaring the same sites in different order must reach the same pinned
    version rather than two versions that differ only in spelling.

    `node_ids` (the argument) is the pinned node set, supplied at publication;
    it is optional so the normalizer stays usable on its own.
    """

    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_ACCEPTANCE_CRITERIA:
        raise ContractViolation(
            f"{field} must contain at most {MAX_ACCEPTANCE_CRITERIA} criteria"
        )
    kinds = _evidence_kind_names()
    normalized: list[dict[str, str]] = []
    ids: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict) or set(item) != {
            "id",
            "statement",
            "evidence_kind",
            "node_ids",
        }:
            raise ContractViolation(f"{field}[{index}] has an invalid shape")
        criterion_id = require_slug(item["id"], f"{field}[{index}] id")
        if criterion_id in ids:
            raise ContractViolation(f"{field} criterion ids must be unique")
        ids.add(criterion_id)
        if item["evidence_kind"] not in kinds:
            raise ContractViolation(
                f"{field}[{index}] evidence_kind is not an admitted evidence kind"
            )
        declared_sites = item["node_ids"]
        if (
            not isinstance(declared_sites, list)
            or not 1 <= len(declared_sites) <= MAX_FLOW_NODES
        ):
            raise ContractViolation(
                f"{field}[{index}] must name between one and "
                f"{MAX_FLOW_NODES} node ids"
            )
        sites = [
            require_slug(site, f"{field}[{index}] node id") for site in declared_sites
        ]
        if len(set(sites)) != len(sites):
            raise ContractViolation(f"{field}[{index}] must not name a node twice")
        if node_ids is not None:
            unknown = sorted(set(sites) - set(node_ids))
            if unknown:
                raise ContractViolation(
                    f"{field} criterion {criterion_id} pins "
                    f"{', '.join(unknown)}, which this Flow does not declare"
                )
        normalized.append(
            {
                "id": criterion_id,
                "statement": require_string(
                    item["statement"], f"{field}[{index}] statement", maximum=240
                ),
                "evidence_kind": str(item["evidence_kind"]),
                "node_ids": sorted(sites),
            }
        )
    return normalized


def normalize_judge_agent_version_id(
    value: Any, field: str, *, criteria: Sequence[Mapping[str, Any]]
) -> str | None:
    """Pair the judge casting with the contract it adjudicates.

    Required if and only if at least one criterion is declared: a contract with
    no judge could never be adjudicated, and a judge with no contract would be a
    model call with nothing to decide.
    """

    if not criteria:
        if value is not None:
            raise ContractViolation(
                f"{field} may only be declared alongside acceptance criteria"
            )
        return None
    if value is None:
        raise ContractViolation(
            f"{field} is required when acceptance criteria are declared"
        )
    return require_string(value, field, maximum=80)


def render_prompt(
    template: str,
    *,
    declared_variables: Sequence[str],
    values: Mapping[str, Any],
    maximum_output: int = 24_000,
) -> str:
    template = require_string(template, "prompt template", maximum=12_000)
    declared = list(declared_variables)
    if len(set(declared)) != len(declared):
        raise ContractViolation("declared prompt variables must be unique")
    for variable in declared:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", variable):
            raise ContractViolation("declared prompt variable has an invalid name")

    placeholders = set(PLACEHOLDER_RE.findall(template))
    template_without_placeholders = PLACEHOLDER_RE.sub("", template)
    if "{{" in template_without_placeholders or "}}" in template_without_placeholders:
        raise ContractViolation("prompt contains an unsupported placeholder shape")
    declared_set = set(declared)
    if placeholders != declared_set:
        missing_declarations = sorted(placeholders - declared_set)
        unused_declarations = sorted(declared_set - placeholders)
        detail = []
        if missing_declarations:
            detail.append(f"undeclared placeholders: {', '.join(missing_declarations)}")
        if unused_declarations:
            detail.append(f"unused declarations: {', '.join(unused_declarations)}")
        raise ContractViolation("prompt variable declaration mismatch: " + "; ".join(detail))

    supplied = set(values)
    missing = sorted(declared_set - supplied)
    unexpected = sorted(supplied - declared_set)
    if missing:
        raise ContractViolation(f"missing prompt values: {', '.join(missing)}")
    if unexpected:
        raise ContractViolation(f"unexpected prompt values: {', '.join(unexpected)}")

    def render_value(variable: str) -> str:
        value = values[variable]
        if isinstance(value, str):
            return value
        try:
            return canonical_json(value)
        except (TypeError, ValueError):
            raise ContractViolation(
                f"prompt value {variable} is not JSON-serializable"
            ) from None

    # Structured Flow outputs enter later prompts as canonical JSON, not as a
    # Python repr. Besides being stable across processes this preserves the
    # actual type boundary the model is being asked to inspect.
    rendered = PLACEHOLDER_RE.sub(
        lambda match: render_value(match.group(1)), template
    )
    if len(rendered) > maximum_output:
        raise ContractViolation("rendered prompt is too long")
    return rendered


def redact(value: Any) -> Any:
    if isinstance(value, list):
        return [redact(item) for item in value]
    if not isinstance(value, dict):
        return value
    cleaned: dict[str, Any] = {}
    for key, entry in value.items():
        cleaned[str(key)] = "[REDACTED]" if SECRET_KEY_RE.search(str(key)) else redact(entry)
    return cleaned


def event_hash_material(event: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": event["id"],
        "run_id": event["run_id"],
        "sequence": event["sequence"],
        "occurred_at": event["occurred_at"],
        "type": event["type"],
        "actor_type": event["actor_type"],
        "actor_id": event.get("actor_id"),
        "payload": event["payload"],
        "prev_hash": event["prev_hash"],
    }


def compute_event_hash(event: Mapping[str, Any]) -> str:
    return fingerprint(event_hash_material(event))


def verify_event_chain(events: Sequence[Mapping[str, Any]]) -> bool:
    previous = GENESIS_HASH
    for expected_sequence, event in enumerate(events, start=1):
        if event.get("sequence") != expected_sequence:
            return False
        if event.get("prev_hash") != previous:
            return False
        if event.get("event_hash") != compute_event_hash(event):
            return False
        previous = str(event["event_hash"])
    return True


def extract_output_text(response: Mapping[str, Any]) -> str:
    chunks: list[str] = []
    output = response.get("output")
    if not isinstance(output, list):
        raise ProviderFailure("OpenAI response output is missing")
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and part.get("type") == "output_text":
                text = part.get("text")
                if isinstance(text, str):
                    chunks.append(text)
    result = "".join(chunks).strip()
    if not result:
        raise ProviderFailure("OpenAI response did not contain output text")
    return result


def function_calls(response: Mapping[str, Any]) -> list[dict[str, Any]]:
    output = response.get("output")
    if not isinstance(output, list):
        raise ProviderFailure("OpenAI response output is missing")
    return [dict(item) for item in output if isinstance(item, dict) and item.get("type") == "function_call"]


def stateless_replay_items(response: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Copy Responses output items across the stateless input boundary.

    The API's output envelope includes a top-level item ``status`` field. GPT-5.6
    currently rejects that response-only field when the same JSON object is replayed
    as an input item, even though the SDK response model exposes it. Preserve every
    output item required for reasoning/tool continuity while dropping only that
    provider-owned response annotation.
    """

    output = response.get("output")
    if not isinstance(output, list):
        raise ProviderFailure("OpenAI response output is missing")
    replay: list[dict[str, Any]] = []
    for item in output:
        if not isinstance(item, dict):
            raise ProviderFailure("OpenAI response output item is invalid")
        normalized = dict(item)
        normalized.pop("status", None)
        replay.append(normalized)
    return replay


def safe_response_summary(response: Mapping[str, Any]) -> dict[str, Any]:
    output = response.get("output")
    output_types = [item.get("type") for item in output if isinstance(item, dict)] if isinstance(output, list) else []
    usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
    return {
        "provider_response_id": str(response.get("id", ""))[:128],
        "status": str(response.get("status", "unknown"))[:32],
        "model": str(response.get("model", "unknown"))[:128],
        "output_types": output_types[:16],
        "usage": {
            key: value
            for key, value in usage.items()
            if key in {"input_tokens", "output_tokens", "total_tokens"}
            and isinstance(value, int)
            and value >= 0
        },
    }


def exact_set(actual: Iterable[str], expected: Iterable[str], field: str) -> list[str]:
    actual_list = list(actual)
    expected_list = list(expected)
    if set(actual_list) != set(expected_list) or len(actual_list) != len(set(actual_list)):
        raise ContractViolation(f"{field} does not match the authoritative evidence set")
    return actual_list
