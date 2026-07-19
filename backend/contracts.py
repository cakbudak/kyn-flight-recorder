"""Small deterministic contracts shared by the runtime and HTTP boundary."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any, Iterable, Mapping, Sequence


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

    rendered = PLACEHOLDER_RE.sub(lambda match: str(values[match.group(1)]), template)
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
