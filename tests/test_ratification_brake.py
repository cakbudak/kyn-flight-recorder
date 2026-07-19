from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.contracts import BrakeEngaged, verify_event_chain
from backend.service import ControlPlane
from backend.store import Store


OBJECT = {
    "type": "object",
    "properties": {"value": {"type": "string"}},
    "required": ["value"],
    "additionalProperties": False,
}

STORE_OUTPUT = {
    "type": "object",
    "properties": {
        "effect_id": {"type": "string"},
        "collection": {"type": "string"},
    },
    "required": ["effect_id", "collection"],
    "additionalProperties": False,
}


class NoModelClient:
    def create(self, payload: dict[str, object]) -> dict[str, object]:
        del payload
        raise AssertionError("deterministic brake tests must not call a model")


class RatificationBrakeContractTest(unittest.TestCase):
    """The system remembers what did not work, and that memory has veto power.

    Ratification is derived from repeated *independent* execution. No model
    participates: every state below is a count over append-only evidence.
    """

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = Store(Path(self.temporary.name) / "brake.sqlite3")
        self.store.initialize()
        self.plane = ControlPlane(self.store, NoModelClient())
        bootstrap = self.plane.create_workspace(seed=False)
        self.workspace_id = bootstrap["workspace_id"]
        self.flow = self._blocked_delivery_flow()

    def _blocked_delivery_flow(self) -> dict[str, object]:
        action = self.plane.create_action(
            self.workspace_id,
            name="Denied delivery store",
            slug="denied-delivery-store",
            description="A data store Action whose bounded write policy is disabled.",
            kind="data_store",
            input_schema=OBJECT,
            output_schema=STORE_OUTPUT,
            config={
                "operation": "append_record",
                "collection": "denied-deliveries",
                "write_enabled": False,
            },
            agent_version_id=None,
        )
        return self.plane.create_studio_flow(
            self.workspace_id,
            name="Repeatable denial",
            slug="repeatable-denial",
            description="A policy-blocked Flow used to prove ratification.",
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

    def _fail_once(self, value: str) -> dict[str, object]:
        run = self.plane.start_studio_run(
            self.workspace_id, self.flow["id"], input_data={"value": value}
        )
        self.assertEqual(run["status"], "blocked")
        self.assertEqual(run["effects"], [])
        return run

    def _dead_ends(self) -> list[dict[str, object]]:
        return self.plane.list_dead_ends(self.workspace_id)

    def test_one_blocked_run_mints_a_proposed_dead_end_that_does_not_brake(self) -> None:
        run = self._fail_once("release-1")

        records = self._dead_ends()
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["ratification_state"], "proposed")
        self.assertEqual(record["distinct_runs"], 1)
        self.assertEqual(record["node_id"], "deliver")
        self.assertEqual(record["citing_run_ids"], [run["id"]])
        self.assertEqual(len(record["fingerprint"]), 64)

        # A single failure must never block a second honest attempt.
        second = self._fail_once("release-2")
        self.assertEqual(second["status"], "blocked")

    def test_second_independent_run_confirms_and_third_makes_it_canonical(self) -> None:
        first = self._fail_once("release-1")
        second = self._fail_once("release-2")

        record = self._dead_ends()[0]
        self.assertEqual(record["ratification_state"], "confirmed")
        self.assertEqual(record["distinct_runs"], 2)

        third = self._fail_once("release-3")
        record = self._dead_ends()[0]
        self.assertEqual(record["ratification_state"], "canonical")
        self.assertEqual(record["distinct_runs"], 3)
        self.assertEqual(
            record["citing_run_ids"], [first["id"], second["id"], third["id"]]
        )

    def test_canonical_dead_end_refuses_the_fourth_run_before_it_is_created(self) -> None:
        for index in range(3):
            self._fail_once(f"release-{index}")

        before = len(self.plane.snapshot(self.workspace_id)["studio"]["runs"])

        with self.assertRaises(BrakeEngaged) as caught:
            self.plane.start_studio_run(
                self.workspace_id, self.flow["id"], input_data={"value": "release-4"}
            )

        error = caught.exception
        self.assertEqual(error.code, "brake_engaged")
        self.assertEqual(error.http_status, 409)
        self.assertEqual(error.detail["node_id"], "deliver")
        self.assertEqual(error.detail["ratification_state"], "canonical")
        self.assertEqual(len(error.detail["citing_run_ids"]), 3)
        self.assertEqual(len(error.detail["fingerprint"]), 64)

        after = self.plane.snapshot(self.workspace_id)["studio"]["runs"]
        self.assertEqual(len(after), before)
        for run in after:
            self.assertNotEqual(run["input"], {"value": "release-4"})

    def test_refused_run_writes_no_step_effect_or_event(self) -> None:
        for index in range(3):
            self._fail_once(f"release-{index}")

        with self.store.read() as connection:
            events_before = connection.execute(
                "SELECT COUNT(*) AS total FROM automation_events"
            ).fetchone()["total"]
            effects_before = connection.execute(
                "SELECT COUNT(*) AS total FROM automation_effects"
            ).fetchone()["total"]

        with self.assertRaises(BrakeEngaged):
            self.plane.start_studio_run(
                self.workspace_id, self.flow["id"], input_data={"value": "release-4"}
            )

        with self.store.read() as connection:
            events_after = connection.execute(
                "SELECT COUNT(*) AS total FROM automation_events"
            ).fetchone()["total"]
            effects_after = connection.execute(
                "SELECT COUNT(*) AS total FROM automation_effects"
            ).fetchone()["total"]

        self.assertEqual(events_after, events_before)
        self.assertEqual(effects_after, effects_before)

    def test_check_brake_is_read_only_and_returns_a_verdict(self) -> None:
        verdict = self.plane.check_brake(self.workspace_id, self.flow["id"])
        self.assertFalse(verdict["refused"])
        self.assertEqual(verdict["matches"], [])

        for index in range(3):
            self._fail_once(f"release-{index}")

        with self.store.read() as connection:
            before = connection.execute(
                "SELECT COUNT(*) AS total FROM automation_dead_end_evidence"
            ).fetchone()["total"]

        verdict = self.plane.check_brake(self.workspace_id, self.flow["id"])
        self.assertTrue(verdict["refused"])
        self.assertEqual(verdict["matches"][0]["node_id"], "deliver")
        self.assertEqual(verdict["matches"][0]["ratification_state"], "canonical")

        with self.store.read() as connection:
            after = connection.execute(
                "SELECT COUNT(*) AS total FROM automation_dead_end_evidence"
            ).fetchone()["total"]
        self.assertEqual(after, before)

    def test_repairing_the_flow_clears_the_brake(self) -> None:
        for index in range(3):
            self._fail_once(f"release-{index}")
        self.assertTrue(self.plane.check_brake(self.workspace_id, self.flow["id"])["refused"])

        blocked = self.plane.snapshot(self.workspace_id)["studio"]["runs"][0]
        diagnosis = self.plane.diagnose_studio_run(self.workspace_id, blocked["id"])
        proposal = self.plane.propose_studio_repair(self.workspace_id, diagnosis["id"])
        self.plane.apply_studio_repair(
            self.workspace_id,
            proposal["id"],
            proposal_hash=proposal["proposal_hash"],
            expected_flow_revision=proposal["expected_flow_revision"],
            expected_action_version=proposal["expected_action_version"],
            actor="workflow-operator",
            reason="The cited denial proves the missing bounded write authority.",
            acknowledged=True,
        )

        # The successor Flow version is a different pinned path, so the brake
        # cannot trap a genuinely repaired system.
        verdict = self.plane.check_brake(self.workspace_id, self.flow["id"])
        self.assertFalse(verdict["refused"])

        proved = self.plane.start_studio_run(
            self.workspace_id, self.flow["id"], input_data={"value": "release-5"}
        )
        self.assertEqual(proved["status"], "completed")
        self.assertEqual(len(proved["effects"]), 1)
        self.assertTrue(verify_event_chain(proved["events"]))

    def test_one_run_cannot_ratify_its_own_dead_end_twice(self) -> None:
        run = self._fail_once("release-1")
        record = self._dead_ends()[0]

        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.write() as connection:
                connection.execute(
                    "INSERT INTO automation_dead_end_evidence "
                    "(id, workspace_id, fingerprint, run_id, flow_version_id, node_id, "
                    "error_code, normalized_detail, created_at) "
                    "SELECT 'ade_forged', workspace_id, fingerprint, run_id, "
                    "flow_version_id, node_id, error_code, normalized_detail, created_at "
                    "FROM automation_dead_end_evidence WHERE fingerprint = ?",
                    (record["fingerprint"],),
                )

        self.assertEqual(self._dead_ends()[0]["distinct_runs"], 1)
        self.assertEqual(self._dead_ends()[0]["citing_run_ids"], [run["id"]])

    def test_dead_end_evidence_is_append_only(self) -> None:
        self._fail_once("release-1")

        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.write() as connection:
                connection.execute(
                    "UPDATE automation_dead_end_evidence SET node_id = 'rewritten'"
                )

        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.write() as connection:
                connection.execute("DELETE FROM automation_dead_end_evidence")

        self.assertEqual(self._dead_ends()[0]["node_id"], "deliver")

    def test_a_different_fault_is_a_different_dead_end(self) -> None:
        for index in range(3):
            self._fail_once(f"release-{index}")

        other = self.plane.create_action(
            self.workspace_id,
            name="Second denied store",
            slug="second-denied-store",
            description="A second bounded store whose write policy is disabled.",
            kind="data_store",
            input_schema=OBJECT,
            output_schema=STORE_OUTPUT,
            config={
                "operation": "append_record",
                "collection": "other-deliveries",
                "write_enabled": False,
            },
            agent_version_id=None,
        )
        other_flow = self.plane.create_studio_flow(
            self.workspace_id,
            name="Other denial",
            slug="other-denial",
            description="A separate policy-blocked Flow.",
            input_schema=OBJECT,
            start_node_id="deliver",
            nodes=[
                {
                    "id": "deliver",
                    "type": "action",
                    "version_id": other["version"]["id"],
                    "input_mapping": {"value": {"source": "input", "path": "value"}},
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

        # A canonical dead end on one Flow must never brake an unrelated Flow.
        run = self.plane.start_studio_run(
            self.workspace_id, other_flow["id"], input_data={"value": "release-9"}
        )
        self.assertEqual(run["status"], "blocked")
        self.assertEqual(len(self._dead_ends()), 2)

    def test_volatile_detail_does_not_fragment_the_fingerprint(self) -> None:
        first = self._fail_once("release-1")
        second = self._fail_once("release-2")

        # Two Runs carry different ids and timestamps in their failure detail;
        # normalization must still recognise one recurring fault.
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(len(self._dead_ends()), 1)
        self.assertEqual(self._dead_ends()[0]["distinct_runs"], 2)


if __name__ == "__main__":
    unittest.main()
