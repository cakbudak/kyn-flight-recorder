from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from backend.contracts import ContractViolation, verify_event_chain
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
