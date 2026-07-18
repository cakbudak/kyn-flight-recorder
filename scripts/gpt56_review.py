#!/usr/bin/env python3
"""Ask GPT-5.6 to adversarially review the synthetic trace diagnosis.

This is submission evidence, not a runtime dependency. The demo remains fully
deterministic and offline. Only an allow-listed, synthetic evidence packet is
sent; the API key and raw API response are never written to disk.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "app" / "data" / "demo-run.json"
DEFAULT_OUTPUT = ROOT / "evidence" / "gpt-5.6-review.json"
API_URL = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = "gpt-5.6"
MAX_OUTPUT_TOKENS = 1800

SYSTEM_PROMPT = """You are an adversarial trace-consistency reviewer.
Review only the supplied synthetic evidence. Determine whether the stated causal
diagnosis follows from the graph, events, state, and intervention contract. Do
not infer live execution, external effects, or facts not present in the packet.
You cannot authorize an intervention or mutate run state. Be concise and name
unsupported claims explicitly. Return only the requested structured result."""

REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["supported", "partially_supported", "unsupported"],
        },
        "confidence_percent": {"type": "integer"},
        "supported_claims": {"type": "array", "items": {"type": "string"}},
        "unsupported_claims": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "suggested_copy": {"type": "string"},
    },
    "required": [
        "verdict",
        "confidence_percent",
        "supported_claims",
        "unsupported_claims",
        "risks",
        "suggested_copy",
    ],
    "additionalProperties": False,
}

SAFE_NODE_FIELD_KEYS = {
    "allowed_command",
    "approval_id",
    "artifact",
    "current",
    "decision",
    "definition",
    "demo_only",
    "effect_boundary",
    "executed",
    "expected",
    "lease_remaining_seconds",
    "owner",
    "partition",
    "permission",
    "policy",
    "queue",
    "reads",
    "reasoning_visibility",
    "revision",
    "run_id",
    "step",
    "target",
    "tool",
}


class ReviewError(RuntimeError):
    """A bounded, user-safe GPT review failure."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def load_fixture(path: Path = FIXTURE_PATH) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ReviewError("Fixture root must be an object.")
    if value.get("fixture", {}).get("classification") != "synthetic_demo":
        raise ReviewError("GPT review accepts only a synthetic_demo fixture.")
    if value.get("run", {}).get("impact", {}).get("external_effect") is not False:
        raise ReviewError("GPT review requires external_effect=false.")
    return value


def build_review_packet(fixture: dict[str, Any]) -> dict[str, Any]:
    """Create an explicit allow-listed packet; credential-like fields never enter it."""

    run = fixture["run"]
    intervention = fixture["intervention"]
    resolution = intervention["resolution"]
    return {
        "packet_version": "1.0",
        "fixture": {
            "id": fixture["fixture"]["id"],
            "classification": fixture["fixture"]["classification"],
            "description": fixture["fixture"]["description"],
        },
        "run": {
            "id": run["id"],
            "correlation_id": run["correlation_id"],
            "status": run["status"],
            "revision": run["revision"],
            "goal": run["goal"],
            "diagnosis": run["diagnosis"],
            "impact": run["impact"],
        },
        "nodes": [
            {
                "id": node["id"],
                "lane": node["lane"],
                "kind": node["kind"],
                "title": node["title"],
                "source": node["source"],
                "status": node["status"],
                "evidence": node["evidence"],
                "fields": {
                    key: value
                    for key, value in node.get("fields", {}).items()
                    if key in SAFE_NODE_FIELD_KEYS
                },
            }
            for node in fixture["nodes"]
        ],
        "edges": fixture["edges"],
        "events": [
            {
                "sequence": event["sequence"],
                "source": event["source"],
                "type": event["type"],
                "status": event["status"],
                "summary": event["summary"],
                "correlation_id": event["correlation_id"],
            }
            for event in fixture["events"]
        ],
        "intervention_contract": {
            "type": intervention["type"],
            "expected_revision": intervention["expected_revision"],
            "allowed_from": intervention["allowed_from"],
            "scope": intervention["scope"],
            "preview": intervention["preview"],
            "resolution": {
                "new_revision": resolution["new_revision"],
                "diagnosis": resolution["diagnosis"],
                "node_updates": resolution["node_updates"],
                "edge_updates": resolution["edge_updates"],
                "events": [
                    {
                        "sequence": event["sequence"],
                        "source": event["source"],
                        "type": event["type"],
                        "status": event["status"],
                        "summary": event["summary"],
                    }
                    for event in resolution["events"]
                ],
            },
        },
    }


def build_request(packet: dict[str, Any], model: str = DEFAULT_MODEL) -> dict[str, Any]:
    if model != "gpt-5.6" and not model.startswith("gpt-5.6-"):
        raise ReviewError("The Build Week evidence runner only accepts a GPT-5.6 model.")
    user_prompt = (
        "Audit the diagnosis in this synthetic flight-recorder packet. Distinguish "
        "what the evidence proves from what the UI must label as simulation.\n\n"
        + canonical_json(packet)
    )
    return {
        "model": model,
        "reasoning": {"effort": "low"},
        "input": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "kyn_trace_review",
                "schema": REVIEW_SCHEMA,
                "strict": True,
            }
        },
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "store": False,
    }


def prompt_digest(request_body: dict[str, Any]) -> str:
    return sha256_text(canonical_json(request_body["input"]))


def call_responses_api(request_body: dict[str, Any], api_key: str, timeout: float = 60) -> dict[str, Any]:
    if not api_key:
        raise ReviewError("OPENAI_API_KEY is not set; no external request was made.")
    request = Request(
        API_URL,
        data=canonical_json(request_body).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "kyn-flight-recorder-build-week/1.0",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed OpenAI URL
            return json.loads(response.read())
    except HTTPError as error:
        message = f"OpenAI API returned HTTP {error.code}"
        try:
            payload = json.loads(error.read(4096))
            api_message = payload.get("error", {}).get("message")
            if isinstance(api_message, str) and api_message:
                message = f"{message}: {api_message[:500]}"
        except (json.JSONDecodeError, AttributeError):
            pass
        raise ReviewError(message) from error
    except URLError as error:
        raise ReviewError(f"OpenAI API connection failed: {error.reason}") from error


def extract_output_text(response: dict[str, Any]) -> str:
    status = response.get("status")
    if status not in (None, "completed"):
        reason = response.get("incomplete_details", {}).get("reason", "unknown")
        raise ReviewError(f"GPT-5.6 response was not complete: {reason}")
    for item in response.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "refusal":
                raise ReviewError(f"GPT-5.6 refused the review: {content.get('refusal', 'unspecified')}")
            if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                return content["text"]
    raise ReviewError("GPT-5.6 response contained no output_text item.")


def validate_review(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReviewError("Structured review must be an object.")
    required = set(REVIEW_SCHEMA["required"])
    if set(value) != required:
        raise ReviewError("Structured review fields do not match the evidence contract.")
    if value["verdict"] not in {"supported", "partially_supported", "unsupported"}:
        raise ReviewError("Structured review has an unknown verdict.")
    confidence = value["confidence_percent"]
    if isinstance(confidence, bool) or not isinstance(confidence, int) or not 0 <= confidence <= 100:
        raise ReviewError("Structured review confidence_percent must be an integer from 0 to 100.")
    for key in ("supported_claims", "unsupported_claims", "risks"):
        entries = value[key]
        if not isinstance(entries, list) or not all(isinstance(entry, str) for entry in entries):
            raise ReviewError(f"Structured review {key} must be an array of strings.")
    if not isinstance(value["suggested_copy"], str):
        raise ReviewError("Structured review suggested_copy must be a string.")
    return value


def build_evidence(
    response: dict[str, Any],
    review: dict[str, Any],
    fixture: dict[str, Any],
    request_body: dict[str, Any],
) -> dict[str, Any]:
    returned_model = response.get("model")
    if not isinstance(returned_model, str) or not returned_model.startswith("gpt-5.6"):
        raise ReviewError("Response model is not identifiable as GPT-5.6.")
    usage = response.get("usage", {})
    return {
        "evidence_version": "1.0",
        "status": "completed",
        "purpose": "Adversarial consistency review; never used to mutate or authorize demo state.",
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "fixture_id": fixture["fixture"]["id"],
        "fixture_sha256": sha256_text(canonical_json(fixture)),
        "prompt_sha256": prompt_digest(request_body),
        "model_requested": request_body["model"],
        "model_returned": returned_model,
        "response_id": response.get("id"),
        "review": review,
        "usage": {
            key: usage[key]
            for key in ("input_tokens", "output_tokens", "total_tokens")
            if isinstance(usage.get(key), int)
        },
        "privacy": {
            "source_classification": "synthetic_demo",
            "allow_listed_packet": True,
            "raw_request_persisted": False,
            "raw_response_persisted": False,
            "api_key_persisted": False,
        },
    }


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="validate and hash the request without network access")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="GPT-5.6 model id (default: gpt-5.6)")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="sanitized evidence output path")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        fixture = load_fixture()
        packet = build_review_packet(fixture)
        request_body = build_request(packet, args.model)
        if args.dry_run:
            print(
                json.dumps(
                    {
                        "status": "dry_run",
                        "external_request": False,
                        "model": request_body["model"],
                        "fixture_id": fixture["fixture"]["id"],
                        "fixture_sha256": sha256_text(canonical_json(fixture)),
                        "prompt_sha256": prompt_digest(request_body),
                        "packet_bytes": len(canonical_json(packet).encode("utf-8")),
                        "output": str(args.output),
                    },
                    indent=2,
                )
            )
            return 0

        response = call_responses_api(request_body, os.environ.get("OPENAI_API_KEY", ""))
        review = validate_review(json.loads(extract_output_text(response)))
        evidence = build_evidence(response, review, fixture, request_body)
        write_json_atomic(args.output, evidence)
        print(f"PASS: sanitized GPT-5.6 evidence written to {args.output}")
        return 0
    except (ReviewError, json.JSONDecodeError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
