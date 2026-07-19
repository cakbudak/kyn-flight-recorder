from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.contracts import ContractViolation, ProviderFailure
from backend.repair_policy import REPAIR_POLICIES, REPAIR_REFUSALS, resolve_repair
from backend.service import ControlPlane
from backend.store import Store


OBJECT = {
    "type": "object",
    "properties": {"value": {"type": "string"}},
    "required": ["value"],
    "additionalProperties": False,
}

# Paths that would let a failure escalate what the model is allowed to do.
AUTHORITY_PATHS = (
    "allowed_action_version_ids",
    "skill_version_ids",
    "agent_version_id",
    "effective_action_version_ids",
)


class FailingProviderClient:
    def create(self, payload: dict[str, object]) -> dict[str, object]:
        del payload
        raise ProviderFailure(
            "OpenAI request failed with status 503",
            detail={
                "provider_code": "server_error",
                "provider_type": "api_error",
                "status": 503,
                "request_id": "req_transient",
            },
        )


class NoModelClient:
    def create(self, payload: dict[str, object]) -> dict[str, object]:
        del payload
        raise AssertionError("deterministic repair tests must not call a model")


class RepairPolicyContractTest(unittest.TestCase):
    """A repair space is only credible if it has a documented edge.

    The fence was always production grade; what it fenced was a single point.
    These contracts hold the widened space to the same fence, and pin the
    classes the system is deliberately never allowed to repair.
    """

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = Store(Path(self.temporary.name) / "repair.sqlite3")
        self.store.initialize()
        self.plane = ControlPlane(self.store, NoModelClient())
        self.bootstrap = self.plane.create_workspace(seed=True)
        self.workspace_id = self.bootstrap["workspace_id"]

    # -- the space is real ------------------------------------------------

    def test_more_than_one_repair_class_is_admitted(self) -> None:
        fault_classes = {policy.fault_class for policy in REPAIR_POLICIES}
        self.assertGreaterEqual(len(fault_classes), 2)
        self.assertIn("authority_policy", fault_classes)
        self.assertIn("provider_failure", fault_classes)

    def test_every_policy_declares_paths_operations_and_a_rationale(self) -> None:
        for policy in REPAIR_POLICIES:
            self.assertTrue(policy.allowed_paths, policy.fault_class)
            self.assertTrue(policy.allowed_operations, policy.fault_class)
            self.assertTrue(policy.rationale.strip(), policy.fault_class)
            self.assertIn(policy.target, {"action_config", "flow_node_settings"})

    # -- the edge is documented -------------------------------------------

    def test_refused_classes_each_carry_a_named_reason(self) -> None:
        self.assertIn("data_contract", REPAIR_REFUSALS)
        self.assertIn("runtime_failure", REPAIR_REFUSALS)
        for fault_class, reason in REPAIR_REFUSALS.items():
            self.assertTrue(reason.strip(), fault_class)
            self.assertGreater(len(reason), 30, fault_class)

    def test_no_admitted_policy_can_widen_authority(self) -> None:
        """Authority is granted by a human. It is never earned by failing."""

        for policy in REPAIR_POLICIES:
            for path in policy.allowed_paths:
                for forbidden in AUTHORITY_PATHS:
                    self.assertNotIn(
                        forbidden,
                        path,
                        f"policy {policy.fault_class} may reach authority via {path}",
                    )

    def test_a_refused_class_names_why_rather_than_failing_generically(self) -> None:
        with self.assertRaises(ContractViolation) as caught:
            resolve_repair(
                fault_class="data_contract",
                executor_kind="template",
                action_config={},
                node_settings={"max_attempts": 1, "retry_on": []},
                error_code="contract_violation",
            )
        self.assertIn("schema", str(caught.exception).lower())

    def test_an_unknown_fault_class_is_refused(self) -> None:
        with self.assertRaises(ContractViolation):
            resolve_repair(
                fault_class="invented_fault",
                executor_kind="template",
                action_config={},
                node_settings={"max_attempts": 1, "retry_on": []},
                error_code="whatever",
            )

    # -- the fence still holds --------------------------------------------

    def test_resolver_emits_only_paths_its_policy_allows(self) -> None:
        resolved = resolve_repair(
            fault_class="authority_policy",
            executor_kind="data_store",
            action_config={"write_enabled": False},
            node_settings={"max_attempts": 1, "retry_on": []},
            error_code="action_blocked",
        )
        policy = resolved.policy
        for operation in resolved.patch:
            self.assertIn(operation["op"], policy.allowed_operations)
            self.assertTrue(
                any(operation["path"].startswith(allowed) for allowed in policy.allowed_paths),
                operation["path"],
            )

    def test_authority_policy_repair_is_unchanged(self) -> None:
        """The pre-existing bounded repair must survive generalization."""

        resolved = resolve_repair(
            fault_class="authority_policy",
            executor_kind="data_store",
            action_config={"write_enabled": False},
            node_settings={"max_attempts": 1, "retry_on": []},
            error_code="action_blocked",
        )
        self.assertEqual(resolved.target, "action_config")
        self.assertEqual(
            resolved.patch,
            [{"op": "replace", "path": "/config/write_enabled", "value": True}],
        )

    def test_authority_policy_repair_is_refused_when_write_is_already_enabled(self) -> None:
        with self.assertRaises(ContractViolation):
            resolve_repair(
                fault_class="authority_policy",
                executor_kind="data_store",
                action_config={"write_enabled": True},
                node_settings={"max_attempts": 1, "retry_on": []},
                error_code="action_blocked",
            )

    # -- the second admitted class ----------------------------------------

    def test_under_provisioned_retry_is_a_bounded_flow_repair(self) -> None:
        resolved = resolve_repair(
            fault_class="provider_failure",
            executor_kind="ai",
            action_config={},
            node_settings={"max_attempts": 1, "retry_on": [], "backoff_seconds": 0},
            error_code="provider_failure",
        )
        self.assertEqual(resolved.target, "flow_node_settings")
        paths = {operation["path"] for operation in resolved.patch}
        self.assertIn("/settings/max_attempts", paths)
        self.assertIn("/settings/retry_on", paths)
        attempts = next(
            operation for operation in resolved.patch
            if operation["path"] == "/settings/max_attempts"
        )
        # The publish-time contract already bounds this at three.
        self.assertLessEqual(attempts["value"], 3)
        self.assertGreater(attempts["value"], 1)

    def test_retry_repair_is_refused_once_the_policy_is_already_provisioned(self) -> None:
        with self.assertRaises(ContractViolation):
            resolve_repair(
                fault_class="provider_failure",
                executor_kind="ai",
                action_config={},
                node_settings={
                    "max_attempts": 3,
                    "retry_on": ["provider_failure"],
                    "backoff_seconds": 1,
                },
                error_code="provider_failure",
            )

    # -- end to end through the real control plane -------------------------

    def test_a_transient_provider_failure_proposes_and_applies_a_flow_successor(self) -> None:
        flow_id = self.bootstrap["snapshot"]["studio"]["flows"][0]["id"]
        failed = self.plane.start_studio_run(
            self.workspace_id,
            flow_id,
            input_data={"brief": "Prove that a transient provider fault is repairable."},
            client=FailingProviderClient(),
        )
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["error_code"], "provider_failure")

        diagnosis = self.plane.diagnose_studio_run(self.workspace_id, failed["id"])
        self.assertEqual(diagnosis["fault_class"], "provider_failure")

        proposal = self.plane.propose_studio_repair(self.workspace_id, diagnosis["id"])
        paths = {operation["path"] for operation in proposal["patch"]}
        self.assertIn("/settings/max_attempts", paths)

        applied = self.plane.apply_studio_repair(
            self.workspace_id,
            proposal["id"],
            proposal_hash=proposal["proposal_hash"],
            expected_flow_revision=proposal["expected_flow_revision"],
            expected_action_version=proposal["expected_action_version"],
            actor="workflow-operator",
            reason="The cited provider fault is transient and the node retried once.",
            acknowledged=True,
        )
        self.assertEqual(applied["status"], "applied")
        # A retry repair changes the Flow, never the Action definition.
        self.assertEqual(applied["applied_flow_version"], 2)
        self.assertIsNone(applied["applied_action_version_id"])

        # The failed parent stays failed, with no effects, forever.
        parent = self.plane.get_studio_run(self.workspace_id, failed["id"])
        self.assertEqual(parent["status"], "failed")
        self.assertEqual(parent["effects"], [])


if __name__ == "__main__":
    unittest.main()
