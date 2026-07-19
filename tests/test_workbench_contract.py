from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.contracts import Conflict, ContractViolation, verify_event_chain
from backend.service import ControlPlane
from backend.store import Store


VALUE_SCHEMA = {
    "type": "object",
    "properties": {"value": {"type": "string"}},
    "required": ["value"],
    "additionalProperties": False,
}

TEXT_SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
    "additionalProperties": False,
}


def outcomes(*items: tuple[str, str, str]) -> list[dict[str, str]]:
    return [
        {"id": item_id, "label": label, "description": "", "tone": tone}
        for item_id, label, tone in items
    ]


SUCCESS_ERROR = outcomes(
    ("success", "Success", "success"),
    ("error", "Error", "danger"),
)

APPROVAL_OUTCOMES = outcomes(
    ("approved", "Approved", "success"),
    ("rejected", "Rejected", "warning"),
    ("error", "Error", "danger"),
)

APPROVAL_SCHEMA = {
    "type": "object",
    "properties": {
        "approved": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["approved", "reason"],
    "additionalProperties": False,
}


class NoModelClient:
    def create(self, payload: dict[str, object]) -> dict[str, object]:
        del payload
        raise AssertionError("deterministic workbench tests must not call a model")


class ProfessionalWorkbenchContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = Store(Path(self.temporary.name) / "workbench.sqlite3")
        self.store.initialize()
        self.plane = ControlPlane(self.store, NoModelClient())
        bootstrap = self.plane.create_workspace(seed=False)
        self.workspace_id = bootstrap["workspace_id"]

    def _template_action(self, *, slug: str, template: str) -> dict[str, object]:
        return self.plane.create_action(
            self.workspace_id,
            name=slug.replace("-", " ").title(),
            slug=slug,
            description="A deterministic version-pinned formatter.",
            kind="template",
            input_schema=VALUE_SCHEMA,
            output_schema=TEXT_SCHEMA,
            outcomes=SUCCESS_ERROR,
            config={"template": template},
            agent_version_id=None,
        )

    def test_router_owns_arbitrary_named_outputs_and_run_exposes_selected_outcome(self) -> None:
        declared = outcomes(
            ("enterprise", "Enterprise", "ai"),
            ("startup", "Startup", "success"),
            ("unclassified", "Unclassified", "warning"),
            ("error", "Error", "danger"),
        )
        router = self.plane.create_action(
            self.workspace_id,
            name="Account segment router",
            slug="account-segment-router",
            description="Route one typed account segment through named outputs.",
            kind="router",
            input_schema=VALUE_SCHEMA,
            output_schema={
                "type": "object",
                "properties": {
                    "outcome": {
                        "type": "string",
                        "enum": ["enterprise", "startup", "unclassified"],
                    },
                    "actual": {"type": "string"},
                },
                "required": ["outcome", "actual"],
                "additionalProperties": False,
            },
            outcomes=declared,
            config={
                "branches": [
                    {
                        "outcome": "enterprise",
                        "path": "value",
                        "operator": "equals",
                        "value": "enterprise",
                    },
                    {
                        "outcome": "startup",
                        "path": "value",
                        "operator": "equals",
                        "value": "startup",
                    },
                ],
                "fallback_outcome": "unclassified",
            },
            agent_version_id=None,
        )
        flow = self.plane.create_studio_flow(
            self.workspace_id,
            name="Segment decision",
            slug="segment-decision",
            description="A Flow whose public outcomes mirror its Router ports.",
            input_schema=VALUE_SCHEMA,
            output_schema=router["version"]["output_schema"],
            outcomes=declared,
            start_node_id="route-account",
            nodes=[
                {
                    "id": "route-account",
                    "type": "action",
                    "version_id": router["version"]["id"],
                    "input_mapping": {
                        "value": {"source": "input", "path": "value"}
                    },
                }
            ],
            routes=[],
        )

        run = self.plane.start_studio_run(
            self.workspace_id, flow["id"], input_data={"value": "enterprise"}
        )

        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["outcome"], "enterprise")
        self.assertEqual(
            run["output"], {"outcome": "enterprise", "actual": "enterprise"}
        )
        self.assertEqual(run["steps"][0]["route_outcome"], "enterprise")
        self.assertEqual(
            [item["id"] for item in router["version"]["outcomes"]],
            ["enterprise", "startup", "unclassified", "error"],
        )
        self.assertTrue(verify_event_chain(run["events"]))

        terminal = self._template_action(slug="terminal-message", template="{{value}}")
        with self.assertRaisesRegex(ContractViolation, "is not declared"):
            self.plane.create_studio_flow(
                self.workspace_id,
                name="Invalid owned route",
                slug="invalid-owned-route",
                description="Must reject a wire from a port the source does not own.",
                input_schema=VALUE_SCHEMA,
                start_node_id="router",
                nodes=[
                    {
                        "id": "router",
                        "type": "action",
                        "version_id": router["version"]["id"],
                        "input_mapping": {
                            "value": {"source": "input", "path": "value"}
                        },
                    },
                    {
                        "id": "terminal",
                        "type": "action",
                        "version_id": terminal["version"]["id"],
                        "input_mapping": {
                            "value": {"source": "input", "path": "value"}
                        },
                    },
                ],
                routes=[{"from": "router", "to": "terminal", "outcome": "success"}],
            )

    def test_action_successor_is_editable_without_mutating_a_pinned_flow(self) -> None:
        action = self._template_action(slug="stable-greeting", template="Hello {{value}}")
        first_version = action["version"]
        flow = self.plane.create_studio_flow(
            self.workspace_id,
            name="Pinned greeting",
            slug="pinned-greeting",
            description="The Flow keeps the Action version selected at save time.",
            input_schema=VALUE_SCHEMA,
            start_node_id="greet",
            nodes=[
                {
                    "id": "greet",
                    "type": "action",
                    "version_id": first_version["id"],
                    "input_mapping": {
                        "value": {"source": "input", "path": "value"}
                    },
                }
            ],
            routes=[],
        )
        successor = self.plane.revise_action(
            self.workspace_id,
            action["id"],
            expected_version=1,
            name="Stable greeting",
            description="A successor with revised copy.",
            kind="template",
            input_schema=VALUE_SCHEMA,
            output_schema=TEXT_SCHEMA,
            outcomes=SUCCESS_ERROR,
            config={"template": "Welcome {{value}}"},
            agent_version_id=None,
        )

        self.assertEqual(successor["current_version"], 2)
        self.assertEqual([item["version"] for item in successor["versions"]], [2, 1])
        self.assertEqual(successor["versions"][1]["id"], first_version["id"])
        self.assertEqual(successor["versions"][1]["config"]["template"], "Hello {{value}}")
        with self.assertRaises(Conflict):
            self.plane.revise_action(
                self.workspace_id,
                action["id"],
                expected_version=1,
                name="Stale write",
                description="This stale editor must lose the compare-and-swap.",
                kind="template",
                input_schema=VALUE_SCHEMA,
                output_schema=TEXT_SCHEMA,
                outcomes=SUCCESS_ERROR,
                config={"template": "Stale {{value}}"},
                agent_version_id=None,
            )

        run = self.plane.start_studio_run(
            self.workspace_id, flow["id"], input_data={"value": "Ada"}
        )
        self.assertEqual(run["output"], {"text": "Hello Ada"})
        self.assertEqual(run["steps"][0]["target_version_id"], first_version["id"])

    def test_prompt_skill_and_agent_edits_append_versions_and_preserve_old_pins(self) -> None:
        prompt = self.plane.create_prompt(
            self.workspace_id,
            name="Triage prompt",
            slug="triage-prompt",
            template="Triage {{value}}.",
            variables=["value"],
        )
        skill = self.plane.create_skill(
            self.workspace_id,
            name="Bounded triage",
            slug="bounded-triage",
            instructions="Reason only over the supplied payload.",
            allowed_tools=[],
            allowed_action_version_ids=[],
        )
        agent = self.plane.create_agent(
            self.workspace_id,
            name="Triage agent",
            slug="triage-agent",
            role="executor",
            model="gpt-5.6",
            instructions="Return contract-bound output.",
            prompt_version_id=prompt["version"]["id"],
            skill_version_ids=[skill["version"]["id"]],
        )
        old_agent_pin = agent["version"]

        prompt_v2 = self.plane.revise_prompt(
            self.workspace_id,
            prompt["id"],
            expected_version=1,
            name="Triage prompt",
            template="Classify {{value}} and cite the decisive field.",
            variables=["value"],
        )
        skill_v2 = self.plane.revise_skill(
            self.workspace_id,
            skill["id"],
            expected_version=1,
            name="Bounded triage",
            instructions="Classify without network or write authority.",
            allowed_tools=[],
            allowed_action_version_ids=[],
        )
        agent_v2 = self.plane.revise_agent(
            self.workspace_id,
            agent["id"],
            expected_version=1,
            name="Triage agent",
            role="executor",
            model="gpt-5.6-sol",
            instructions="Use the successor Prompt and Skill pins.",
            prompt_version_id=prompt_v2["version"]["id"],
            skill_version_ids=[skill_v2["version"]["id"]],
        )

        self.assertEqual(agent_v2["current_version"], 2)
        self.assertEqual(agent_v2["versions"][1]["id"], old_agent_pin["id"])
        self.assertEqual(
            agent_v2["versions"][1]["prompt_version_id"],
            prompt["version"]["id"],
        )
        self.assertEqual(
            agent_v2["versions"][1]["skill_version_ids"],
            [skill["version"]["id"]],
        )
        self.assertEqual(agent_v2["version"]["model"], "gpt-5.6-sol")
        with self.assertRaises(Conflict):
            self.plane.revise_prompt(
                self.workspace_id,
                prompt["id"],
                expected_version=1,
                name="Stale prompt",
                template="Stale {{value}}.",
                variables=["value"],
            )

    def test_completed_flow_is_a_typed_node_with_linked_child_run_and_cycle_fence(self) -> None:
        formatter = self._template_action(
            slug="child-formatter", template="Child handled {{value}}"
        )
        child = self.plane.create_studio_flow(
            self.workspace_id,
            name="Reusable child",
            slug="reusable-child",
            description="A completed Flow version exposed as a typed node.",
            input_schema=VALUE_SCHEMA,
            output_schema=TEXT_SCHEMA,
            outcomes=SUCCESS_ERROR,
            start_node_id="format",
            nodes=[
                {
                    "id": "format",
                    "type": "action",
                    "version_id": formatter["version"]["id"],
                    "input_mapping": {
                        "value": {"source": "input", "path": "value"}
                    },
                }
            ],
            routes=[],
        )
        parent = self.plane.create_studio_flow(
            self.workspace_id,
            name="Parent orchestration",
            slug="parent-orchestration",
            description="Reuses the pinned child Flow without flattening its evidence.",
            input_schema=VALUE_SCHEMA,
            output_schema=TEXT_SCHEMA,
            outcomes=SUCCESS_ERROR,
            start_node_id="child-flow",
            nodes=[
                {
                    "id": "child-flow",
                    "type": "flow",
                    "version_id": child["version"]["id"],
                    "input_mapping": {
                        "value": {"source": "input", "path": "value"}
                    },
                }
            ],
            routes=[],
        )

        run = self.plane.start_studio_run(
            self.workspace_id, parent["id"], input_data={"value": "case-42"}
        )

        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["output"], {"text": "Child handled case-42"})
        self.assertEqual(run["steps"][0]["node_type"], "flow")
        self.assertEqual(len(run["children"]), 1)
        child_run = self.plane.get_studio_run(
            self.workspace_id, run["children"][0]["id"]
        )
        self.assertEqual(child_run["relation_kind"], "subflow")
        self.assertEqual(child_run["parent_run_id"], run["id"])
        self.assertEqual(child_run["parent_step_id"], run["steps"][0]["id"])
        self.assertEqual(child_run["correlation_id"], run["correlation_id"])
        self.assertEqual(child_run["output"], run["output"])
        self.assertTrue(verify_event_chain(run["events"]))
        self.assertTrue(verify_event_chain(child_run["events"]))

        with self.assertRaisesRegex(ContractViolation, "cycle"):
            self.plane.revise_studio_flow(
                self.workspace_id,
                child["id"],
                expected_revision=1,
                input_schema=VALUE_SCHEMA,
                output_schema=TEXT_SCHEMA,
                outcomes=SUCCESS_ERROR,
                start_node_id="parent-flow",
                nodes=[
                    {
                        "id": "parent-flow",
                        "type": "flow",
                        "version_id": parent["version"]["id"],
                        "input_mapping": {
                            "value": {"source": "input", "path": "value"}
                        },
                    }
                ],
                routes=[],
            )

    def test_subflow_human_gate_pauses_and_resumes_parent_with_separate_evidence(self) -> None:
        approval = self.plane.create_action(
            self.workspace_id,
            name="Reusable human gate",
            slug="reusable-human-gate",
            description="A durable decision inside a reusable Flow.",
            kind="approval",
            input_schema=VALUE_SCHEMA,
            output_schema=APPROVAL_SCHEMA,
            outcomes=APPROVAL_OUTCOMES,
            config={"message_template": "Approve {{value}}?"},
            agent_version_id=None,
        )
        child = self.plane.create_studio_flow(
            self.workspace_id,
            name="Approval child",
            slug="approval-child",
            description="A reusable Flow that owns its human decision.",
            input_schema=VALUE_SCHEMA,
            output_schema=APPROVAL_SCHEMA,
            outcomes=APPROVAL_OUTCOMES,
            start_node_id="approve",
            nodes=[
                {
                    "id": "approve",
                    "type": "action",
                    "version_id": approval["version"]["id"],
                    "input_mapping": {
                        "value": {"source": "input", "path": "value"}
                    },
                }
            ],
            routes=[],
        )
        parent = self.plane.create_studio_flow(
            self.workspace_id,
            name="Approval parent",
            slug="approval-parent",
            description="The parent must wait for and resume from its child Run.",
            input_schema=VALUE_SCHEMA,
            output_schema=APPROVAL_SCHEMA,
            outcomes=APPROVAL_OUTCOMES,
            start_node_id="approval-flow",
            nodes=[
                {
                    "id": "approval-flow",
                    "type": "flow",
                    "version_id": child["version"]["id"],
                    "input_mapping": {
                        "value": {"source": "input", "path": "value"}
                    },
                }
            ],
            routes=[],
        )

        waiting_parent = self.plane.start_studio_run(
            self.workspace_id, parent["id"], input_data={"value": "release-42"}
        )
        self.assertEqual(waiting_parent["status"], "waiting_approval")
        self.assertIsNone(waiting_parent["pending_approval"])
        self.assertEqual(waiting_parent["steps"][0]["status"], "waiting_approval")
        self.assertEqual(len(waiting_parent["children"]), 1)

        waiting_child = self.plane.get_studio_run(
            self.workspace_id, waiting_parent["children"][0]["id"]
        )
        self.assertEqual(waiting_child["status"], "waiting_approval")
        self.assertIsNotNone(waiting_child["pending_approval"])
        self.assertEqual(waiting_child["relation_kind"], "subflow")

        completed_child = self.plane.decide_studio_approval(
            self.workspace_id,
            waiting_child["pending_approval"]["id"],
            approved=True,
            actor="browser-operator",
            reason="The exact pinned child context authorizes this continuation.",
        )
        completed_parent = self.plane.get_studio_run(
            self.workspace_id, waiting_parent["id"]
        )

        self.assertEqual(completed_child["status"], "completed")
        self.assertEqual(completed_child["outcome"], "approved")
        self.assertEqual(completed_parent["status"], "completed")
        self.assertEqual(completed_parent["outcome"], "approved")
        self.assertEqual(completed_parent["output"], completed_child["output"])
        self.assertEqual(completed_parent["steps"][0]["status"], "completed")
        self.assertTrue(verify_event_chain(completed_child["events"]))
        self.assertTrue(verify_event_chain(completed_parent["events"]))

    def test_terminal_approval_output_contract_failure_does_not_leave_a_live_run(self) -> None:
        approval = self.plane.create_action(
            self.workspace_id,
            name="Mismatched terminal gate",
            slug="mismatched-terminal-gate",
            description="Exercise terminal output validation after a durable pause.",
            kind="approval",
            input_schema=VALUE_SCHEMA,
            output_schema=APPROVAL_SCHEMA,
            outcomes=APPROVAL_OUTCOMES,
            config={"message_template": "Approve {{value}}?"},
            agent_version_id=None,
        )
        flow = self.plane.create_studio_flow(
            self.workspace_id,
            name="Mismatched approval output",
            slug="mismatched-approval-output",
            description="A terminal mismatch must fail closed after approval.",
            input_schema=VALUE_SCHEMA,
            output_schema=VALUE_SCHEMA,
            outcomes=APPROVAL_OUTCOMES,
            start_node_id="approve",
            nodes=[
                {
                    "id": "approve",
                    "type": "action",
                    "version_id": approval["version"]["id"],
                    "input_mapping": {
                        "value": {"source": "input", "path": "value"}
                    },
                }
            ],
            routes=[],
        )

        waiting = self.plane.start_studio_run(
            self.workspace_id, flow["id"], input_data={"value": "release-43"}
        )
        failed = self.plane.decide_studio_approval(
            self.workspace_id,
            waiting["pending_approval"]["id"],
            approved=True,
            actor="browser-operator",
            reason="The decision is valid even though the Flow contract is deliberately wrong.",
        )

        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["error_code"], "contract_violation")
        self.assertIsNone(failed["current_node_id"])
        self.assertEqual(failed["steps"][0]["status"], "completed")
        self.assertTrue(verify_event_chain(failed["events"]))


if __name__ == "__main__":
    unittest.main()
