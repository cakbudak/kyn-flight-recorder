from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from backend.contracts import ContractViolation, ProviderFailure, verify_event_chain
from backend.service import ControlPlane
from backend.store import Store


OBJECT_SCHEMA = {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}


class StudioResponsesClient:
    """Provider-shaped deterministic seam for the configurable Studio runtime."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self.requests: list[dict[str, object]] = []

    def create(self, payload: dict[str, object]) -> dict[str, object]:
        if self.store.in_write_transaction():
            raise AssertionError("provider I/O happened inside a SQLite write transaction")
        self.requests.append(json.loads(json.dumps(payload)))
        metadata = payload.get("metadata")
        if not isinstance(metadata, dict) or metadata.get("kyn_surface") != "agent-studio":
            raise AssertionError("Studio model calls must identify their runtime surface")
        result = {
            "summary": "The launch brief is concrete, bounded, and ready for a human decision.",
            "score": 0.91,
            "risks": ["A human must still authorize the public sandbox record."],
        }
        return {
            "id": f"resp_studio_{len(self.requests)}",
            "status": "completed",
            "model": "gpt-5.6",
            "usage": {"input_tokens": 44, "output_tokens": 27, "total_tokens": 71},
            "output": [
                {
                    "id": f"msg_studio_{len(self.requests)}",
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(result, separators=(",", ":")),
                            "annotations": [],
                        }
                    ],
                }
            ],
        }


class ToolCallingStudioResponsesClient:
    """Two-turn provider seam proving stateless reasoning + strict final output."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self.requests: list[dict[str, object]] = []

    def create(self, payload: dict[str, object]) -> dict[str, object]:
        if self.store.in_write_transaction():
            raise AssertionError("provider I/O happened inside a SQLite write transaction")
        self.requests.append(json.loads(json.dumps(payload)))
        if len(self.requests) == 1:
            return {
                "id": "resp_tool_turn",
                "status": "completed",
                "model": "gpt-5.6-sol",
                "usage": {"input_tokens": 40, "output_tokens": 20, "total_tokens": 60},
                "output": [
                    {
                        "id": "rs_tool_turn",
                        "type": "reasoning",
                        "encrypted_content": "opaque-provider-reasoning",
                        "summary": [],
                        "status": "completed",
                    },
                    {
                        "id": "fc_tool_turn",
                        "type": "function_call",
                        "call_id": "call_needs_work",
                        "name": "needs-work-response",
                        "arguments": json.dumps(
                            {"summary": "The launch brief needs one bounded clarification."}
                        ),
                        "status": "completed",
                    },
                ],
            }
        return {
            "id": "resp_final_turn",
            "status": "completed",
            "model": "gpt-5.6-sol",
            "usage": {"input_tokens": 80, "output_tokens": 30, "total_tokens": 110},
            "output": [
                {
                    "id": "msg_final_turn",
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(
                                {
                                    "summary": "The bounded launch is ready for human review.",
                                    "score": 0.91,
                                    "risks": ["The sandbox effect still requires approval."],
                                }
                            ),
                            "annotations": [],
                        }
                    ],
                }
            ],
        }


class FailingStudioResponsesClient:
    def create(self, payload: dict[str, object]) -> dict[str, object]:
        del payload
        raise ProviderFailure(
            "OpenAI request failed with status 400",
            detail={
                "provider_code": "invalid_value",
                "provider_type": "invalid_request_error",
                "provider_param": "input[1].encrypted_content",
                "status": 400,
                "request_id": "req_runtime_failure",
            },
        )


class AgentStudioRuntimeContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.database_path = Path(self.temporary.name) / "agent-studio.sqlite3"
        self.store = Store(self.database_path)
        self.store.initialize()
        self.client = StudioResponsesClient(self.store)
        self.plane = ControlPlane(self.store, self.client)
        self.bootstrap = self.plane.create_workspace(seed=True)
        self.workspace_id = self.bootstrap["workspace_id"]

    def test_seed_is_a_configurable_studio_not_one_prescribed_demo(self) -> None:
        studio = self.bootstrap["snapshot"]["studio"]
        self.assertGreaterEqual(len(studio["actions"]), 5)
        self.assertGreaterEqual(len(studio["flows"]), 1)
        kinds = {action["version"]["kind"] for action in studio["actions"]}
        self.assertTrue({"ai", "template", "condition", "approval", "sandbox"}.issubset(kinds))
        flow = studio["flows"][0]
        self.assertEqual(flow["revision"], 1)
        self.assertGreaterEqual(len(flow["version"]["nodes"]), 4)
        self.assertTrue(flow["version"]["requires_model"])

    def test_seeded_flow_runs_pauses_for_human_and_resumes_with_real_effect(self) -> None:
        flow_id = self.bootstrap["snapshot"]["studio"]["flows"][0]["id"]
        run = self.plane.start_studio_run(
            self.workspace_id,
            flow_id,
            input_data={
                "brief": (
                    "Launch a public Build Week preview that demonstrates configurable "
                    "Actions, Agents, Flows, approvals, and authoritative Runs."
                )
            },
        )

        self.assertEqual(run["status"], "waiting_approval")
        self.assertIsNotNone(run["pending_approval"])
        self.assertEqual([step["status"] for step in run["steps"]], [
            "completed",
            "completed",
            "waiting_approval",
        ])
        self.assertTrue(verify_event_chain(run["events"]))
        self.assertEqual(run["effects"], [])

        completed = self.plane.decide_studio_approval(
            self.workspace_id,
            run["pending_approval"]["id"],
            approved=True,
            actor="build-week-judge",
            reason="The bounded sandbox effect and pinned run evidence are acceptable.",
        )
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(len(completed["effects"]), 1)
        self.assertEqual(completed["parent_run_id"], None)
        self.assertTrue(verify_event_chain(completed["events"]))
        self.assertGreaterEqual(len(self.client.requests), 1)

        rerun = self.plane.rerun_studio_run(
            self.workspace_id,
            completed["id"],
            input_data=completed["input"],
            idempotency_key="judge-rerun-1",
        )
        repeated = self.plane.rerun_studio_run(
            self.workspace_id,
            completed["id"],
            input_data=completed["input"],
            idempotency_key="judge-rerun-1",
        )
        self.assertEqual(rerun["id"], repeated["id"])
        self.assertEqual(rerun["parent_run_id"], completed["id"])
        self.assertEqual(rerun["flow_version_id"], completed["flow_version_id"])

    def test_ai_action_keeps_tools_reasoning_and_strict_output_in_one_contract(self) -> None:
        client = ToolCallingStudioResponsesClient(self.store)
        flow_id = self.bootstrap["snapshot"]["studio"]["flows"][0]["id"]
        run = self.plane.start_studio_run(
            self.workspace_id,
            flow_id,
            input_data={
                "brief": (
                    "Prove a stateless model tool turn and a schema-bound final response "
                    "before a human authorizes the sandbox effect."
                )
            },
            client=client,
        )

        self.assertEqual(run["status"], "waiting_approval")
        self.assertEqual(len(client.requests), 2)
        for request in client.requests:
            self.assertEqual(request["include"], ["reasoning.encrypted_content"])
            self.assertEqual(request["reasoning"], {"effort": "medium"})
            self.assertEqual(
                request["text"],
                {
                    "format": {
                        "type": "json_schema",
                        "name": "kyn_action_output",
                        "schema": self.plane.get_studio_action(
                            self.workspace_id,
                            next(
                                action["id"]
                                for action in self.bootstrap["snapshot"]["studio"]["actions"]
                                if action["slug"] == "ai-launch-analysis"
                            ),
                        )["version"]["output_schema"],
                        "strict": True,
                    }
                },
            )
            self.assertEqual(len(request["tools"]), 1)
        self.assertEqual(client.requests[0]["tool_choice"], "auto")
        second_input = client.requests[1]["input"]
        reasoning_item = next(
            item for item in second_input if item.get("type") == "reasoning"
        )
        self.assertEqual(
            reasoning_item["encrypted_content"], "opaque-provider-reasoning"
        )
        replayed_provider_items = [
            item
            for item in second_input
            if item.get("type") in {"reasoning", "function_call"}
        ]
        self.assertTrue(replayed_provider_items)
        self.assertTrue(all("status" not in item for item in replayed_provider_items))
        self.assertTrue(
            any(item.get("type") == "function_call_output" for item in second_input)
        )

    def test_failed_provider_attempt_is_visible_without_provider_message_leakage(self) -> None:
        flow_id = self.bootstrap["snapshot"]["studio"]["flows"][0]["id"]
        run = self.plane.start_studio_run(
            self.workspace_id,
            flow_id,
            input_data={"brief": "Expose a safe failed model-attempt receipt in the Run evidence."},
            client=FailingStudioResponsesClient(),
        )

        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["error_code"], "provider_failure")
        self.assertIn("invalid_value", run["error_message"])
        self.assertIn("input[1].encrypted_content", run["error_message"])
        self.assertEqual(len(run["model_calls"]), 1)
        self.assertEqual(run["model_calls"][0]["status"], "failed")
        self.assertEqual(run["model_calls"][0]["request_id"], "req_runtime_failure")
        self.assertEqual(run["model_calls"][0]["usage"], {})

    def test_user_can_define_and_execute_a_deterministic_action_flow(self) -> None:
        action = self.plane.create_action(
            self.workspace_id,
            name="Greeting formatter",
            slug="greeting-formatter",
            description="Render a deterministic greeting from validated input.",
            kind="template",
            input_schema={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
                "additionalProperties": False,
            },
            config={"template": "Hello {{name}}"},
            agent_version_id=None,
        )
        flow = self.plane.create_studio_flow(
            self.workspace_id,
            name="Greeting flow",
            slug="greeting-flow",
            description="One real user-defined Action wired into a Flow.",
            input_schema={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
                "additionalProperties": False,
            },
            start_node_id="greet",
            nodes=[
                {
                    "id": "greet",
                    "type": "action",
                    "version_id": action["version"]["id"],
                    "input_mapping": {
                        "name": {"source": "input", "path": "name"},
                    },
                }
            ],
            routes=[],
        )
        run = self.plane.start_studio_run(
            self.workspace_id,
            flow["id"],
            input_data={"name": "Ada"},
        )
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["output"], {"text": "Hello Ada"})
        self.assertEqual(run["model_calls"], [])
        self.assertTrue(verify_event_chain(run["events"]))

    def test_skill_action_authority_is_workspace_scoped_and_version_pinned(self) -> None:
        studio_action = self.bootstrap["snapshot"]["studio"]["actions"][0]
        skill = self.plane.create_skill(
            self.workspace_id,
            name="Studio action authority",
            slug="studio-action-authority",
            instructions="The agent may invoke only the explicitly pinned Action version.",
            allowed_tools=[],
            allowed_action_version_ids=[studio_action["version"]["id"]],
        )
        self.assertEqual(
            skill["version"]["allowed_action_version_ids"],
            [studio_action["version"]["id"]],
        )
        with self.assertRaisesRegex(ContractViolation, "Action version"):
            self.plane.create_skill(
                self.workspace_id,
                name="Foreign authority",
                slug="foreign-authority",
                instructions="This must not accept a fabricated Action id.",
                allowed_tools=[],
                allowed_action_version_ids=["actv_00000000000000000000000000000000"],
            )

    def test_studio_definitions_and_events_are_database_immutable(self) -> None:
        flow_id = self.bootstrap["snapshot"]["studio"]["flows"][0]["id"]
        run = self.plane.start_studio_run(
            self.workspace_id,
            flow_id,
            input_data={"brief": "Verify immutable public runtime evidence before approval."},
        )
        with closing(sqlite3.connect(self.database_path)) as connection:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                connection.execute("UPDATE action_versions SET version = 9")
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                connection.execute(
                    "DELETE FROM automation_events WHERE run_id = ?", (run["id"],)
                )


if __name__ == "__main__":
    unittest.main()
