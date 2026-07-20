"""The brake must be a memory, not a trap.

Minting `dead_end` evidence is not free: three citations make a pinned path
`canonical` and the brake then refuses it for every future input. That is only
defensible for a *structural* defect — one where repeating the same pinned path
genuinely cannot succeed. These contracts pin the membership rule, the scope the
brake actually enforces, and what a braked subflow does to its parent.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.contracts import (
    NON_RATIFIABLE_FAULTS,
    RATIFIABLE_FAULTS,
    BrakeEngaged,
    ProviderFailure,
    is_ratifiable_fault,
    ratification_policy,
)
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

STORE_OUTPUT = {
    "type": "object",
    "properties": {"effect_id": {"type": "string"}, "collection": {"type": "string"}},
    "required": ["effect_id", "collection"],
    "additionalProperties": False,
}

SCORE_SCHEMA = {
    "type": "object",
    "properties": {"score": {"type": "number", "minimum": 0, "maximum": 1}},
    "required": ["score"],
    "additionalProperties": False,
}

ASSERT_OUTPUT = {
    "type": "object",
    "properties": {"passed": {"type": "boolean"}, "actual": {"type": "number"}},
    "required": ["passed", "actual"],
    "additionalProperties": False,
}

CONDITION_OUTPUT = {
    "type": "object",
    "properties": {"matched": {"type": "boolean"}, "actual": {"type": "string"}},
    "required": ["matched", "actual"],
    "additionalProperties": False,
}

NO_RETRY = {
    "max_attempts": 1,
    "backoff_seconds": 0,
    "retry_on": [],
    "on_error": "fail",
}


class NoModelClient:
    def create(self, payload: dict[str, object]) -> dict[str, object]:
        del payload
        raise AssertionError("deterministic brake tests must not call a model")


class FailingProviderClient:
    """One organisation's transient rate limit, with a volatile detail."""

    def __init__(self) -> None:
        self.calls = 0

    def create(self, payload: dict[str, object]) -> dict[str, object]:
        del payload
        self.calls += 1
        raise ProviderFailure(
            f"OpenAI request failed with status 429 after {self.calls} attempts",
            detail={
                "provider_code": "rate_limit_exceeded",
                "provider_type": "rate_limit_error",
                "status": 429,
                "request_id": f"req_transient_{self.calls}",
            },
        )


class BrakeHarness(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = Store(Path(self.temporary.name) / "brake.sqlite3")
        self.store.initialize()
        self.plane = ControlPlane(self.store, NoModelClient())
        self.workspace_id = self.plane.create_workspace(seed=False)["workspace_id"]

    def dead_ends(self) -> list[dict[str, object]]:
        return self.plane.list_dead_ends(self.workspace_id)

    def denied_store_action(self, slug: str = "denied-delivery-store") -> dict[str, object]:
        return self.plane.create_action(
            self.workspace_id,
            name=f"Denied store {slug}",
            slug=slug,
            description="A data store Action whose bounded write policy is disabled.",
            kind="data_store",
            input_schema=VALUE_SCHEMA,
            output_schema=STORE_OUTPUT,
            config={
                "operation": "append_record",
                "collection": "denied-deliveries",
                "write_enabled": False,
            },
            agent_version_id=None,
        )


class AssertionRejectionDoesNotRatifyTest(BrakeHarness):
    """An assertion gate rejecting bad input is the gate working, not a defect.

    The rejection message is author-configured and static, so three rejections
    of three *different* bad inputs collapse to one fingerprint. Ratifying that
    would refuse the Flow for every future input, including valid ones, and no
    successor version could clear it because the assertion is unchanged.
    """

    def setUp(self) -> None:
        super().setUp()
        gate = self.plane.create_action(
            self.workspace_id,
            name="Readiness assertion",
            slug="readiness-assertion",
            description="Blocks execution when an explicit readiness threshold is not met.",
            kind="assert",
            input_schema=SCORE_SCHEMA,
            output_schema=ASSERT_OUTPUT,
            config={
                "path": "score",
                "operator": "gte",
                "value": 0.75,
                "message": "The readiness score is below the approved threshold.",
            },
            agent_version_id=None,
        )
        self.flow = self.plane.create_studio_flow(
            self.workspace_id,
            name="Readiness gate",
            slug="readiness-gate",
            description="A validation gate whose whole job is to reject unready input.",
            input_schema=SCORE_SCHEMA,
            start_node_id="gate",
            nodes=[
                {
                    "id": "gate",
                    "type": "action",
                    "version_id": gate["version"]["id"],
                    "input_mapping": {"score": {"source": "input", "path": "score"}},
                    "settings": dict(NO_RETRY),
                }
            ],
            routes=[],
        )

    def test_three_rejected_inputs_do_not_brake_and_the_flow_still_runs(self) -> None:
        for score in (0.10, 0.25, 0.40):
            run = self.plane.start_studio_run(
                self.workspace_id, self.flow["id"], input_data={"score": score}
            )
            self.assertEqual(run["status"], "blocked")
            self.assertEqual(run["error_code"], "action_blocked")

        # The gate is a validation contract, not a failed approach. It mints no
        # dead end at all, so nothing can ratify.
        self.assertEqual(self.dead_ends(), [])
        self.assertFalse(
            self.plane.check_brake(self.workspace_id, self.flow["id"])["refused"]
        )

        # The decisive proof: a valid input must still run afterwards.
        passed = self.plane.start_studio_run(
            self.workspace_id, self.flow["id"], input_data={"score": 0.99}
        )
        self.assertEqual(passed["status"], "completed")
        self.assertEqual(passed["output"], {"passed": True, "actual": 0.99})

        # And a fourth bad input is still rejected on its merits, not braked.
        fourth = self.plane.start_studio_run(
            self.workspace_id, self.flow["id"], input_data={"score": 0.05}
        )
        self.assertEqual(fourth["status"], "blocked")
        self.assertEqual(fourth["error_code"], "action_blocked")


class TransientProviderFaultDoesNotRatifyTest(BrakeHarness):
    """A rate limit is a fault of the moment, never of the pinned path."""

    def setUp(self) -> None:
        super().setUp()
        self.client = FailingProviderClient()
        self.plane = ControlPlane(self.store, self.client)
        bootstrap = self.plane.create_workspace(seed=True)
        self.workspace_id = bootstrap["workspace_id"]
        self.flow = bootstrap["snapshot"]["studio"]["flows"][0]

    def test_three_rate_limits_do_not_ratify_into_a_permanent_refusal(self) -> None:
        for index in range(3):
            run = self.plane.start_studio_run(
                self.workspace_id,
                self.flow["id"],
                input_data={
                    "brief": (
                        "Demonstrate a bounded transient provider fault during "
                        f"public verification attempt number {index}."
                    )
                },
            )
            self.assertEqual(run["status"], "failed")
            self.assertEqual(run["error_code"], "provider_failure")

        self.assertEqual(
            [record["error_code"] for record in self.dead_ends()],
            [],
            "a transient provider fault must never mint dead-end evidence",
        )
        self.assertFalse(
            self.plane.check_brake(self.workspace_id, self.flow["id"])["refused"]
        )


class RatifiableFaultTableTest(BrakeHarness):
    """The membership rule is a declared table a reader can audit."""

    def test_the_table_is_explicit_and_every_entry_states_a_reason(self) -> None:
        self.assertTrue(RATIFIABLE_FAULTS)
        self.assertTrue(NON_RATIFIABLE_FAULTS)
        for entry in RATIFIABLE_FAULTS + NON_RATIFIABLE_FAULTS:
            self.assertGreater(len(entry.reason), 40, entry.name)
        names = [entry.name for entry in RATIFIABLE_FAULTS + NON_RATIFIABLE_FAULTS]
        self.assertEqual(len(names), len(set(names)))

    def test_the_admitted_class_is_a_policy_denial_on_a_data_store(self) -> None:
        self.assertTrue(
            is_ratifiable_fault(
                error_code="action_blocked",
                executor_kind="data_store",
                policy_marker="write_enabled_denied",
            )
        )

    def test_assertions_transient_faults_and_the_unknown_never_ratify(self) -> None:
        refused = (
            # An assertion doing its declared job.
            ("action_blocked", "assert", None),
            # A denial with no declared configuration predicate behind it.
            ("action_blocked", "sandbox", None),
            # Transient by construction.
            ("provider_failure", "ai", None),
            ("provider_failure", "data_store", "write_enabled_denied"),
            # This Run's data failed this Run's schema.
            ("contract_violation", "template", None),
            # A refusal is already a memory; it must not mint another.
            ("brake_engaged", "", None),
            # Anything nobody declared.
            ("subflow_failure", "", None),
            ("worker_failure", "", None),
            ("missing_node", "", None),
            ("flow_traversal_exhausted", "", None),
        )
        for error_code, executor_kind, marker in refused:
            with self.subTest(error_code=error_code, executor_kind=executor_kind):
                self.assertFalse(
                    is_ratifiable_fault(
                        error_code=error_code,
                        executor_kind=executor_kind,
                        policy_marker=marker,
                    )
                )

    def test_the_table_is_exposed_on_the_read_only_brake_verdict(self) -> None:
        action = self.denied_store_action()
        flow = self.plane.create_studio_flow(
            self.workspace_id,
            name="Auditable denial",
            slug="auditable-denial",
            description="A policy-blocked Flow used to read the brake's own policy.",
            input_schema=VALUE_SCHEMA,
            start_node_id="deliver",
            nodes=[
                {
                    "id": "deliver",
                    "type": "action",
                    "version_id": action["version"]["id"],
                    "input_mapping": {"value": {"source": "input", "path": "value"}},
                    "settings": dict(NO_RETRY),
                }
            ],
            routes=[],
        )
        verdict = self.plane.check_brake(self.workspace_id, flow["id"])
        self.assertEqual(verdict["fault_classes"], ratification_policy())
        ratifiable = [
            entry for entry in verdict["fault_classes"] if entry["ratifiable"]
        ]
        self.assertTrue(ratifiable)
        for entry in verdict["fault_classes"]:
            self.assertIn("reason", entry)
            self.assertIn("error_code", entry)


class BrakeScopeIsTheFlowVersionTest(BrakeHarness):
    """The brake refuses at Flow-version scope, and the docs must say so.

    Which nodes a Run traverses depends on data that does not exist until the
    Run executes, so a pre-execution check cannot know the path. Refusing at
    version scope is the only semantic compatible with the stronger property:
    a refused Run leaves no Run row, no Step, and no effect.
    """

    def setUp(self) -> None:
        super().setUp()
        condition = self.plane.create_action(
            self.workspace_id,
            name="Delivery branch",
            slug="delivery-branch",
            description="Routes deliveries down the bounded store branch or past it.",
            kind="condition",
            input_schema=VALUE_SCHEMA,
            output_schema=CONDITION_OUTPUT,
            config={"path": "value", "operator": "equals", "value": "deliver"},
            agent_version_id=None,
        )
        skip = self.plane.create_action(
            self.workspace_id,
            name="Skip notice",
            slug="skip-notice",
            description="Renders the notice used when no delivery is attempted.",
            kind="template",
            input_schema=VALUE_SCHEMA,
            output_schema=TEXT_SCHEMA,
            config={"template": "Skipped {{value}}"},
            agent_version_id=None,
        )
        store = self.denied_store_action()
        self.flow = self.plane.create_studio_flow(
            self.workspace_id,
            name="Branching delivery",
            slug="branching-delivery",
            description="One branch reaches the denied store; the other never does.",
            input_schema=VALUE_SCHEMA,
            start_node_id="branch",
            nodes=[
                {
                    "id": "branch",
                    "type": "action",
                    "version_id": condition["version"]["id"],
                    "input_mapping": {"value": {"source": "input", "path": "value"}},
                    "settings": dict(NO_RETRY),
                },
                {
                    "id": "deliver",
                    "type": "action",
                    "version_id": store["version"]["id"],
                    "input_mapping": {"value": {"source": "input", "path": "value"}},
                    "settings": dict(NO_RETRY),
                },
                {
                    "id": "skip",
                    "type": "action",
                    "version_id": skip["version"]["id"],
                    "input_mapping": {"value": {"source": "input", "path": "value"}},
                    "settings": dict(NO_RETRY),
                },
            ],
            routes=[
                {"from": "branch", "to": "deliver", "outcome": "true"},
                {"from": "branch", "to": "skip", "outcome": "false"},
            ],
        )

    def test_a_canonical_dead_end_refuses_every_candidate_of_that_flow_version(
        self,
    ) -> None:
        for _ in range(3):
            run = self.plane.start_studio_run(
                self.workspace_id, self.flow["id"], input_data={"value": "deliver"}
            )
            self.assertEqual(run["status"], "blocked")
        record = self.dead_ends()[0]
        self.assertEqual(record["node_id"], "deliver")
        self.assertEqual(record["ratification_state"], "canonical")

        # This input routes `branch → skip` and never reaches `deliver`. The
        # brake still refuses, because the traversed path is not knowable before
        # the Run exists and refusing before creation is the stronger guarantee.
        with self.assertRaises(BrakeEngaged) as caught:
            self.plane.start_studio_run(
                self.workspace_id, self.flow["id"], input_data={"value": "skip"}
            )
        self.assertEqual(caught.exception.detail["node_id"], "deliver")

    def test_check_brake_takes_no_traversal_scope_it_cannot_honour(self) -> None:
        import inspect

        signature = inspect.signature(self.plane.studio.check_brake)
        self.assertNotIn(
            "node_ids",
            signature.parameters,
            "a parameter the implementation ignores misrepresents the scope",
        )
        verdict = self.plane.check_brake(self.workspace_id, self.flow["id"])
        self.assertEqual(verdict["scope"], "flow_version")


class BrakedSubflowTerminatesItsParentTest(BrakeHarness):
    """A braked subflow must end its parent legibly, not strand or mangle it."""

    def setUp(self) -> None:
        super().setUp()
        action = self.denied_store_action()
        self.child = self.plane.create_studio_flow(
            self.workspace_id,
            name="Denied child",
            slug="denied-child",
            description="A reusable Flow whose only node is a denied bounded write.",
            input_schema=VALUE_SCHEMA,
            output_schema=STORE_OUTPUT,
            start_node_id="deliver",
            nodes=[
                {
                    "id": "deliver",
                    "type": "action",
                    "version_id": action["version"]["id"],
                    "input_mapping": {"value": {"source": "input", "path": "value"}},
                    "settings": dict(NO_RETRY),
                }
            ],
            routes=[],
        )
        self.parent = self.plane.create_studio_flow(
            self.workspace_id,
            name="Parent orchestration",
            slug="parent-orchestration",
            description="Reuses the pinned child Flow as a typed node.",
            input_schema=VALUE_SCHEMA,
            output_schema=STORE_OUTPUT,
            start_node_id="child",
            nodes=[
                {
                    "id": "child",
                    "type": "flow",
                    "version_id": self.child["version"]["id"],
                    "input_mapping": {"value": {"source": "input", "path": "value"}},
                    "settings": dict(NO_RETRY),
                }
            ],
            routes=[],
        )
        for index in range(3):
            run = self.plane.start_studio_run(
                self.workspace_id, self.child["id"], input_data={"value": f"child-{index}"}
            )
            self.assertEqual(run["status"], "blocked")
        self.assertEqual(self.dead_ends()[0]["ratification_state"], "canonical")

    def test_the_parent_ends_blocked_and_cites_the_refusal(self) -> None:
        parent_run = self.plane.start_studio_run(
            self.workspace_id, self.parent["id"], input_data={"value": "parent-1"}
        )

        # No stranded `running` Run and no unexplained `worker_failure`.
        self.assertEqual(parent_run["status"], "blocked")
        self.assertEqual(parent_run["error_code"], "brake_engaged")
        self.assertEqual(parent_run["current_node_id"], None)
        self.assertEqual(parent_run["outcome"], "error")
        self.assertEqual(parent_run["effects"], [])

        # The Step the parent already created is closed, not abandoned.
        self.assertEqual([step["status"] for step in parent_run["steps"]], ["blocked"])
        self.assertEqual(parent_run["steps"][0]["error_code"], "brake_engaged")

        # The refusal's citations travel into the parent's own evidence.
        refusals = [
            event
            for event in parent_run["events"]
            if event["type"] == "subflow.brake_engaged"
        ]
        self.assertEqual(len(refusals), 1)
        citations = refusals[0]["payload"]["matches"][0]["citing_run_ids"]
        self.assertEqual(len(citations), 3)
        for run_id in citations:
            cited = self.plane.get_studio_run(self.workspace_id, run_id)
            self.assertEqual(cited["status"], "blocked")

    def test_the_parents_refusal_does_not_ratify_a_second_dead_end(self) -> None:
        before = {record["fingerprint"] for record in self.dead_ends()}
        for index in range(3):
            run = self.plane.start_studio_run(
                self.workspace_id, self.parent["id"], input_data={"value": f"parent-{index}"}
            )
            self.assertEqual(run["status"], "blocked")

        after = {record["fingerprint"] for record in self.dead_ends()}
        self.assertEqual(
            after,
            before,
            "a brake refusal must not cascade a second canonical dead end upward",
        )
        # The parent Flow version is therefore still runnable on its own terms.
        self.assertFalse(
            self.plane.check_brake(self.workspace_id, self.parent["id"])["refused"]
        )


if __name__ == "__main__":
    unittest.main()
