from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from backend.contracts import Conflict, ContractViolation, NotFound, verify_event_chain
from backend.service import ControlPlane
from backend.store import Store


OBJECT = {
    "type": "object",
    "properties": {"value": {"type": "string"}},
    "required": ["value"],
    "additionalProperties": False,
}


class NoModelClient:
    def create(self, payload: dict[str, object]) -> dict[str, object]:
        del payload
        raise AssertionError("deterministic product tests must not call a model")


class ProductWorkflowContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = Store(Path(self.temporary.name) / "studio.sqlite3")
        self.store.initialize()
        self.plane = ControlPlane(self.store, NoModelClient())
        bootstrap = self.plane.create_workspace(seed=True)
        self.workspace_id = bootstrap["workspace_id"]

    def test_new_workspace_exposes_the_complete_bounded_action_palette(self) -> None:
        snapshot = self.plane.snapshot(self.workspace_id)["studio"]
        kinds = {action["version"]["kind"] for action in snapshot["actions"]}
        self.assertTrue(
            {
                "ai",
                "template",
                "transform",
                "delay",
                "condition",
                "assert",
                "approval",
                "data_store",
            }.issubset(kinds)
        )

    def test_canvas_layout_retry_policy_and_successor_version_are_pinned(self) -> None:
        action = self.plane.create_action(
            self.workspace_id,
            name="Normalize payload",
            slug="normalize-payload",
            description="Project an incoming value into a stable payload.",
            kind="transform",
            input_schema=OBJECT,
            output_schema={
                "type": "object",
                "properties": {"normalized": {"type": "string"}},
                "required": ["normalized"],
                "additionalProperties": False,
            },
            config={
                "operation": "map",
                "mappings": {
                    "normalized": {"source": "input", "path": "value"},
                },
            },
            agent_version_id=None,
        )
        flow = self.plane.create_studio_flow(
            self.workspace_id,
            name="Canvas flow",
            slug="canvas-flow",
            description="A visual workflow with operational node policy.",
            input_schema=OBJECT,
            start_node_id="normalize",
            nodes=[
                {
                    "id": "normalize",
                    "type": "action",
                    "version_id": action["version"]["id"],
                    "input_mapping": {
                        "value": {"source": "input", "path": "value"},
                    },
                    "position": {"x": 240, "y": 180},
                    "settings": {
                        "max_attempts": 2,
                        "backoff_seconds": 0,
                        "retry_on": ["provider_failure"],
                        "on_error": "fail",
                    },
                }
            ],
            routes=[],
        )
        node = flow["version"]["nodes"][0]
        self.assertEqual(node["position"], {"x": 240, "y": 180})
        self.assertEqual(node["settings"]["max_attempts"], 2)

        successor = self.plane.revise_studio_flow(
            self.workspace_id,
            flow["id"],
            expected_revision=1,
            name="Canvas flow successor",
            description="The same stable Flow identity with revised metadata and layout.",
            input_schema=OBJECT,
            start_node_id="normalize",
            nodes=[{**node, "position": {"x": 520, "y": 220}}],
            routes=[],
        )
        self.assertEqual(successor["revision"], 2)
        self.assertEqual(successor["name"], "Canvas flow successor")
        self.assertEqual(
            successor["description"],
            "The same stable Flow identity with revised metadata and layout.",
        )
        self.assertEqual(successor["current_version"], 2)
        self.assertEqual(successor["version"]["parent_version_id"], flow["version"]["id"])
        self.assertEqual(successor["version"]["nodes"][0]["position"]["x"], 520)
        with self.assertRaises(Conflict):
            self.plane.revise_studio_flow(
                self.workspace_id,
                flow["id"],
                expected_revision=1,
                input_schema=OBJECT,
                start_node_id="normalize",
                nodes=[node],
                routes=[],
            )

    def test_transform_delay_and_assert_are_real_bounded_executors(self) -> None:
        transform = self.plane.create_action(
            self.workspace_id,
            name="Map payload",
            slug="map-payload",
            description="Map validated input without arbitrary code.",
            kind="transform",
            input_schema=OBJECT,
            output_schema={
                "type": "object",
                "properties": {"mapped": {"type": "string"}},
                "required": ["mapped"],
                "additionalProperties": False,
            },
            config={
                "operation": "map",
                "mappings": {"mapped": {"source": "input", "path": "value"}},
            },
            agent_version_id=None,
        )
        delay = self.plane.create_action(
            self.workspace_id,
            name="Bounded delay",
            slug="bounded-delay",
            description="Delay a run without shell or network authority.",
            kind="delay",
            input_schema={
                "type": "object",
                "properties": {"mapped": {"type": "string"}},
                "required": ["mapped"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {"mapped": {"type": "string"}},
                "required": ["mapped"],
                "additionalProperties": False,
            },
            config={"milliseconds": 1},
            agent_version_id=None,
        )
        assertion = self.plane.create_action(
            self.workspace_id,
            name="Contract gate",
            slug="contract-gate",
            description="Fail closed when a mapped value is not ready.",
            kind="assert",
            input_schema={
                "type": "object",
                "properties": {"mapped": {"type": "string"}},
                "required": ["mapped"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "passed": {"type": "boolean"},
                    "actual": {"type": "string"},
                },
                "required": ["passed", "actual"],
                "additionalProperties": False,
            },
            config={
                "path": "mapped",
                "operator": "equals",
                "value": "ready",
                "message": "The payload is not ready for delivery.",
            },
            agent_version_id=None,
        )
        flow = self.plane.create_studio_flow(
            self.workspace_id,
            name="Deterministic operations",
            slug="deterministic-operations",
            description="Three real bounded executors in one workflow.",
            input_schema=OBJECT,
            start_node_id="map",
            nodes=[
                {
                    "id": "map",
                    "type": "action",
                    "version_id": transform["version"]["id"],
                    "input_mapping": {"value": {"source": "input", "path": "value"}},
                },
                {
                    "id": "wait",
                    "type": "action",
                    "version_id": delay["version"]["id"],
                    "input_mapping": {
                        "mapped": {"source": "step", "node_id": "map", "path": "mapped"}
                    },
                },
                {
                    "id": "gate",
                    "type": "action",
                    "version_id": assertion["version"]["id"],
                    "input_mapping": {
                        "mapped": {"source": "step", "node_id": "wait", "path": "mapped"}
                    },
                },
            ],
            routes=[
                {"from": "map", "to": "wait", "outcome": "success"},
                {"from": "wait", "to": "gate", "outcome": "success"},
            ],
        )
        started = time.monotonic()
        run = self.plane.start_studio_run(
            self.workspace_id, flow["id"], input_data={"value": "ready"}
        )
        self.assertGreaterEqual(time.monotonic() - started, 0.001)
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["output"], {"passed": True, "actual": "ready"})
        self.assertEqual(len(run["action_receipts"]), 3)
        self.assertTrue(verify_event_chain(run["events"]))

    def test_webhook_trigger_executes_a_real_deterministic_flow(self) -> None:
        action = self.plane.create_action(
            self.workspace_id,
            name="Webhook acknowledgement",
            slug="webhook-acknowledgement",
            description="Acknowledge one validated webhook payload.",
            kind="template",
            input_schema=OBJECT,
            output_schema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
                "additionalProperties": False,
            },
            config={"template": "Accepted {{value}}"},
            agent_version_id=None,
        )
        flow = self.plane.create_studio_flow(
            self.workspace_id,
            name="Webhook flow",
            slug="webhook-flow",
            description="Starts from a signed public webhook token.",
            input_schema=OBJECT,
            start_node_id="ack",
            nodes=[
                {
                    "id": "ack",
                    "type": "action",
                    "version_id": action["version"]["id"],
                    "input_mapping": {"value": {"source": "input", "path": "value"}},
                }
            ],
            routes=[],
        )
        trigger = self.plane.create_studio_trigger(
            self.workspace_id,
            flow["id"],
            name="Inbound lead",
            trigger_type="webhook",
            config={},
        )
        self.assertIn("secret", trigger)
        fired = self.plane.fire_studio_webhook(
            trigger["secret"], {"value": "lead-42"}
        )
        self.assertEqual(fired["run"]["status"], "completed")
        self.assertEqual(fired["run"]["output"], {"text": "Accepted lead-42"})
        self.assertNotIn("secret", self.plane.studio_snapshot(self.workspace_id)["triggers"][0])
        disabled = self.plane.set_studio_trigger_enabled(
            self.workspace_id,
            trigger["id"],
            enabled=False,
            expected_revision=1,
        )
        self.assertFalse(disabled["enabled"])
        self.assertEqual(disabled["revision"], 2)
        with self.assertRaises(NotFound):
            self.plane.fire_studio_webhook(trigger["secret"], {"value": "blocked"})
        with self.assertRaises(Conflict):
            self.plane.set_studio_trigger_enabled(
                self.workspace_id,
                trigger["id"],
                enabled=True,
                expected_revision=1,
            )

    def test_model_schedule_prepares_a_pinned_run_without_server_credentials(self) -> None:
        flow = self.plane.snapshot(self.workspace_id)["studio"]["flows"][0]
        with self.assertRaisesRegex(ContractViolation, "between five minutes"):
            self.plane.create_studio_trigger(
                self.workspace_id,
                flow["id"],
                name="Overactive schedule",
                trigger_type="schedule",
                config={"interval_minutes": 4, "input": {"brief": "too frequent"}},
            )
        trigger = self.plane.create_studio_trigger(
            self.workspace_id,
            flow["id"],
            name="Scheduled launch review",
            trigger_type="schedule",
            config={
                "interval_minutes": 60,
                "input": {
                    "brief": (
                        "Review a typed automation with explicit authority, Human approval, "
                        "bounded effects, evidence, and a measurable success condition."
                    )
                },
            },
        )
        with self.store.write() as connection:
            connection.execute(
                "UPDATE automation_trigger_bindings SET next_fire_at = ? WHERE id = ?",
                ("2000-01-01T00:00:00.000Z", trigger["id"]),
            )

        fired = self.plane.fire_due_studio_schedules()

        self.assertEqual(len(fired), 1)
        run = fired[0]["run"]
        self.assertEqual(run["status"], "created")
        self.assertEqual(run["flow_version"], flow["version"]["version"])
        self.assertEqual(run["model_calls"], [])
        self.assertIn(
            "run.credential_required",
            [event["type"] for event in run["events"]],
        )

    def test_run_is_observable_after_pinning_and_before_execution(self) -> None:
        flow_id = next(
            flow["id"]
            for flow in self.plane.studio_snapshot(self.workspace_id)["flows"]
            if flow["slug"] == "agent-reviewed-launch"
        )
        prepared = self.plane.prepare_studio_run(
            self.workspace_id,
            flow_id,
            input_data={
                "brief": (
                    "Observe this Run after immutable resource pinning and before any "
                    "OpenAI request is allowed to start."
                )
            },
            idempotency_key="observable-before-execution",
        )
        self.assertEqual(prepared["status"], "created")
        self.assertEqual(prepared["steps"], [])
        self.assertIn("run.queued", [event["type"] for event in prepared["events"]])
        self.assertTrue(verify_event_chain(prepared["events"]))

    def test_failed_action_is_diagnosed_repaired_and_proven_on_a_linked_child(self) -> None:
        action = self.plane.create_action(
            self.workspace_id,
            name="Customer delivery store",
            slug="customer-delivery-store",
            description="Write an approved delivery into the isolated workspace store.",
            kind="data_store",
            input_schema=OBJECT,
            output_schema={
                "type": "object",
                "properties": {
                    "effect_id": {"type": "string"},
                    "collection": {"type": "string"},
                },
                "required": ["effect_id", "collection"],
                "additionalProperties": False,
            },
            config={
                "operation": "append_record",
                "collection": "customer-deliveries",
                "write_enabled": False,
            },
            agent_version_id=None,
        )
        flow = self.plane.create_studio_flow(
            self.workspace_id,
            name="Repairable delivery",
            slug="repairable-delivery",
            description="A policy-blocked delivery used to prove bounded maintenance.",
            input_schema=OBJECT,
            start_node_id="deliver",
            nodes=[
                {
                    "id": "deliver",
                    "type": "action",
                    "version_id": action["version"]["id"],
                    "input_mapping": {"value": {"source": "input", "path": "value"}},
                    "position": {"x": 280, "y": 220},
                    "settings": {
                        "max_attempts": 1,
                        "backoff_seconds": 0,
                        "retry_on": [],
                        "on_error": "fail",
                    },
                }
            ],
            routes=[],
        )
        blocked = self.plane.start_studio_run(
            self.workspace_id, flow["id"], input_data={"value": "release-42"}
        )
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(blocked["steps"][-1]["status"], "blocked")
        self.assertEqual(blocked["action_receipts"][0]["outcome"], "denied")
        self.assertEqual(blocked["effects"], [])

        diagnosis = self.plane.diagnose_studio_run(self.workspace_id, blocked["id"])
        self.assertEqual(diagnosis["fault_class"], "authority_policy")
        self.assertEqual(diagnosis["failed_node_id"], "deliver")
        self.assertTrue(set(diagnosis["evidence_event_ids"]).issubset(
            {event["id"] for event in blocked["events"]}
        ))
        proposal = self.plane.propose_studio_repair(
            self.workspace_id, diagnosis["id"]
        )
        self.assertEqual(
            proposal["patch"],
            [{"op": "replace", "path": "/config/write_enabled", "value": True}],
        )
        applied = self.plane.apply_studio_repair(
            self.workspace_id,
            proposal["id"],
            proposal_hash=proposal["proposal_hash"],
            expected_flow_revision=proposal["expected_flow_revision"],
            expected_action_version=proposal["expected_action_version"],
            actor="workflow-operator",
            reason="The cited denial proves the missing bounded write authority.",
            acknowledged=True,
        )
        self.assertEqual(applied["status"], "applied")
        self.assertEqual(applied["applied_flow_version"], 2)
        self.assertEqual(applied["applied_action_version"], 2)

        proof = self.plane.prove_studio_repair(
            self.workspace_id,
            proposal["id"],
            input_data=blocked["input"],
            idempotency_key="repair-proof-1",
        )
        self.assertEqual(proof["status"], "completed")
        self.assertEqual(proof["parent_run_id"], blocked["id"])
        self.assertEqual(proof["flow_version"], 2)
        self.assertEqual(len(proof["effects"]), 1)
        repeated_proof = self.plane.prove_studio_repair(
            self.workspace_id,
            proposal["id"],
            input_data=blocked["input"],
            idempotency_key="a-different-browser-command",
        )
        self.assertEqual(repeated_proof["id"], proof["id"])
        self.assertEqual(len(repeated_proof["effects"]), 1)
        unchanged = self.plane.get_studio_run(self.workspace_id, blocked["id"])
        self.assertEqual(unchanged["status"], "blocked")
        self.assertEqual(unchanged["effects"], [])
        self.assertEqual(unchanged["diagnosis"]["id"], diagnosis["id"])
        self.assertEqual(unchanged["repair"]["status"], "applied")
        self.assertEqual(unchanged["flow_graph"]["start_node_id"], "deliver")


if __name__ == "__main__":
    unittest.main()
