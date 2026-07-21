"""Pure contracts for evidence-bound Skill distillation.

The Forge is deliberately narrower than a self-modifying runtime. A model may
propose instructions from one completed model-backed Step. Code owns the source
envelope, the cited-event boundary, the candidate fingerprint, and the later
qualification. No tool or Action authority exists in this contract.
"""

from __future__ import annotations

from typing import Any, Mapping

from .contracts import (
    ContractViolation,
    canonical_json,
    extract_output_text,
    fingerprint,
    validate_json_schema,
)


SKILL_CANDIDATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "minLength": 3, "maxLength": 80},
        "instructions": {"type": "string", "minLength": 40, "maxLength": 3_000},
        "rationale": {"type": "string", "minLength": 20, "maxLength": 1_200},
        "evidence_event_ids": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": 12,
        },
    },
    "required": ["name", "instructions", "rationale", "evidence_event_ids"],
    "additionalProperties": False,
}


def source_material(
    run: Mapping[str, Any],
    model_call: Mapping[str, Any],
    source_agent: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the bounded, code-owned source envelope shown to the distiller.

    The selected Step carries the actual input and output. The ledger excerpt
    includes that Step's events plus terminal/authority evidence, capped at 24
    records. This is enough to ground a behavioral instruction without copying
    an arbitrarily large Run into a second model request.
    """

    step = next(
        (item for item in run.get("steps", []) if item.get("id") == model_call.get("step_id")),
        None,
    )
    if step is None:
        raise ContractViolation("Skill candidate source Step was not found")

    terminal_types = {
        "completion.admitted",
        "approval.decided",
        "effect.committed",
        "run.status_changed",
    }
    relevant: list[dict[str, Any]] = []
    for event in run.get("events", []):
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if (
            payload.get("step_id") == step["id"]
            or payload.get("node_id") == step["node_id"]
            or event.get("type") in terminal_types
        ):
            relevant.append(
                {
                    "id": event["id"],
                    "sequence": event["sequence"],
                    "type": event["type"],
                    "actor_type": event["actor_type"],
                    "payload": payload,
                    "event_hash": event["event_hash"],
                }
            )
    if not relevant:
        relevant = [
            {
                "id": event["id"],
                "sequence": event["sequence"],
                "type": event["type"],
                "actor_type": event["actor_type"],
                "payload": event.get("payload", {}),
                "event_hash": event["event_hash"],
            }
            for event in run.get("events", [])[-8:]
        ]
    relevant = relevant[-24:]

    return {
        "run": {
            "id": run["id"],
            "flow_id": run["flow_id"],
            "flow_version_id": run["flow_version_id"],
            "flow_version": run["flow_version"],
            "flow_fingerprint": run["flow_fingerprint"],
            "status": run["status"],
            "relation_kind": run["relation_kind"],
            "input": run["input"],
            "output": run["output"],
            "outcome": run["outcome"],
            "ledger_verified": run["ledger_verified"],
            "finished_at": run["finished_at"],
        },
        "step": dict(step),
        "source_model_call": {
            key: model_call.get(key)
            for key in (
                "id",
                "step_id",
                "agent_version_id",
                "status",
                "model",
                "input_hash",
                "output_hash",
                "usage",
                "created_at",
            )
        },
        "source_agent": {
            "id": source_agent["id"],
            "fingerprint": source_agent["fingerprint"],
            "role": source_agent["role"],
            "model": source_agent["model"],
            "instructions": source_agent["instructions"],
            "prompt": {
                "id": source_agent["prompt"]["id"],
                "fingerprint": source_agent["prompt"]["fingerprint"],
            },
            "skills": [
                {
                    "id": skill["id"],
                    "fingerprint": skill["fingerprint"],
                    "instructions": skill["instructions"],
                }
                for skill in source_agent["skills"]
            ],
        },
        "evidence_ledger": relevant,
    }


def build_distillation_payload(
    *,
    distiller_agent: Mapping[str, Any],
    source: Mapping[str, Any],
) -> dict[str, Any]:
    """Create one strict, tool-free Responses request for a Skill candidate."""

    existing_skills = "\n\n".join(
        f"Pinned Skill {skill['id']} ({skill['fingerprint']}):\n{skill['instructions']}"
        for skill in distiller_agent["skills"]
    )
    instructions = (
        "You are a pinned Kyn.ist capability distiller. Propose one reusable, "
        "behavioral Skill instruction from the supplied completed model Step and "
        "its code-owned evidence. Cite only supplied event IDs. Preserve uncertainty "
        "from a single observation. Do not claim general performance, do not invent "
        "facts, tools, Actions, connectors, effects, permissions, or private Kyn "
        "layers. The candidate will remain quarantined until code qualifies its "
        "provenance and a human promotes it.\n\n"
        f"Pinned distiller Agent {distiller_agent['id']} "
        f"({distiller_agent['fingerprint']}):\n{distiller_agent['instructions']}"
    )
    if existing_skills:
        instructions = f"{instructions}\n\n{existing_skills}"
    return {
        "model": distiller_agent["model"],
        "instructions": instructions,
        "input": [
            {
                "role": "user",
                "content": canonical_json(
                    {
                        "task": (
                            "Distil one narrowly reusable behavioral capability. "
                            "Explain why the cited events support the proposal."
                        ),
                        "source": dict(source),
                    }
                ),
            }
        ],
        "tool_choice": "none",
        "parallel_tool_calls": False,
        "max_output_tokens": 1_200,
        "store": False,
        "reasoning": {"effort": "high"},
        "text": {
            "format": {
                "type": "json_schema",
                "name": "kyn_skill_candidate",
                "schema": SKILL_CANDIDATE_SCHEMA,
                "strict": True,
            }
        },
        "metadata": {
            "kyn_surface": "agent-studio",
            "operation": "skill_distillation",
            "source_run_id": source["run"]["id"],
            "source_step_id": source["step"]["id"],
            "distiller_agent_version_id": distiller_agent["id"],
        },
    }


def parse_candidate(response: Mapping[str, Any], source: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the strict result and narrow citations to the supplied ledger."""

    import json

    try:
        parsed = json.loads(extract_output_text(response))
    except json.JSONDecodeError:
        raise ContractViolation("Skill distiller output is not valid JSON") from None
    candidate = validate_json_schema(parsed, SKILL_CANDIDATE_SCHEMA, "Skill candidate")
    citations = list(candidate["evidence_event_ids"])
    if len(set(citations)) != len(citations):
        raise ContractViolation("Skill candidate repeats an evidence event id")
    supplied = {event["id"] for event in source["evidence_ledger"]}
    outside = sorted(set(citations) - supplied)
    if outside:
        raise ContractViolation("Skill candidate cited evidence outside its source envelope")
    return candidate


def candidate_fingerprint(material: Mapping[str, Any]) -> str:
    """Hash the complete immutable candidate material."""

    return fingerprint(
        {
            key: material[key]
            for key in (
                "source_run_id",
                "source_step_id",
                "source_model_call_id",
                "distiller_agent_version_id",
                "distillation_model_call_id",
                "name",
                "instructions",
                "rationale",
                "evidence_event_ids",
                "source_snapshot_hash",
            )
        }
    )
