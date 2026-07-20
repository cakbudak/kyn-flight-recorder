from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

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
        raise AssertionError("deterministic distillation tests must not call a model")


class PrincipleDistillationContractTest(unittest.TestCase):
    """The brake refuses one exact path. A principle states what was learned.

    A principle is advisory and fires while authoring, where being wrong costs a
    reader two seconds. The brake stays the only thing that refuses, and it
    refuses only the exact pinned path it has three independent Runs for. Warn
    early, refuse late.
    """

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = Store(Path(self.temporary.name) / "principles.sqlite3")
        self.store.initialize()
        self.plane = ControlPlane(self.store, NoModelClient())
        bootstrap = self.plane.create_workspace(seed=False)
        self.workspace_id = bootstrap["workspace_id"]

    def _denied_store_flow(self, marker: str) -> dict[str, object]:
        action = self.plane.create_action(
            self.workspace_id,
            name=f"Denied store {marker}",
            slug=f"denied-store-{marker}",
            description="A bounded store whose declared write policy is disabled.",
            kind="data_store",
            input_schema=OBJECT,
            output_schema=STORE_OUTPUT,
            config={
                "operation": "append_record",
                "collection": f"denied-{marker}",
                "write_enabled": False,
            },
            agent_version_id=None,
        )
        return self.plane.create_studio_flow(
            self.workspace_id,
            name=f"Denied delivery {marker}",
            slug=f"denied-delivery-{marker}",
            description="A policy-blocked Flow used to prove distillation.",
            input_schema=OBJECT,
            start_node_id="deliver",
            nodes=[
                {
                    "id": "deliver",
                    "type": "action",
                    "version_id": action["version"]["id"],
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

    def _fail(self, flow: dict[str, object], value: str) -> dict[str, object]:
        run = self.plane.start_studio_run(
            self.workspace_id, flow["id"], input_data={"value": value}
        )
        self.assertEqual(run["status"], "blocked")
        return run

    # -- quorum ------------------------------------------------------------

    def test_one_flow_failing_repeatedly_never_distils_a_principle(self) -> None:
        """Repetition inside one Flow is the brake's job, not a principle's.

        Letting one Flow mint a rule would let a single loud failure speak for
        the whole workspace.
        """

        flow = self._denied_store_flow("a")
        # Three is the most one Flow can repeat: the third citation makes the
        # dead end canonical, and from the fourth attempt on the brake refuses
        # before a Run row exists. That is precisely the point — repetition
        # inside one Flow is already handled, and handled by refusing.
        for index in range(3):
            self._fail(flow, f"release-{index}")

        self.assertEqual(self.plane.list_principles(self.workspace_id), [])

    def test_two_distinct_flows_are_not_yet_a_principle(self) -> None:
        for marker in ("a", "b"):
            flow = self._denied_store_flow(marker)
            self._fail(flow, "release-1")

        self.assertEqual(self.plane.list_principles(self.workspace_id), [])

    def test_three_distinct_flows_sharing_a_structure_distil_one_principle(self) -> None:
        flows = []
        for marker in ("a", "b", "c"):
            flow = self._denied_store_flow(marker)
            self._fail(flow, "release-1")
            flows.append(flow)

        principles = self.plane.list_principles(self.workspace_id)
        self.assertEqual(len(principles), 1)
        principle = principles[0]
        self.assertEqual(principle["distinct_flows"], 3)
        self.assertEqual(len(principle["signature"]), 64)
        self.assertEqual(
            sorted(principle["citing_flow_ids"]), sorted(flow["id"] for flow in flows)
        )
        self.assertEqual(len(principle["citing_run_ids"]), 3)
        # The statement is derived from the signature, never model prose.
        self.assertIn("data_store", principle["statement"])
        self.assertIn("write_enabled", principle["statement"])

    def test_the_statement_is_deterministic(self) -> None:
        for marker in ("a", "b", "c"):
            self._fail(self._denied_store_flow(marker), "release-1")

        first = self.plane.list_principles(self.workspace_id)[0]
        second = self.plane.list_principles(self.workspace_id)[0]
        self.assertEqual(first["statement"], second["statement"])
        self.assertEqual(first["signature"], second["signature"])

    # -- it advises, it never refuses --------------------------------------

    def test_publishing_a_matching_flow_succeeds_and_returns_an_advisory(self) -> None:
        for marker in ("a", "b", "c"):
            self._fail(self._denied_store_flow(marker), "release-1")

        published = self._denied_store_flow("d")

        # Publishing always succeeds. The principle informs; the brake decides.
        self.assertEqual(published["current_version"], 1)
        advisories = published.get("advisories") or []
        self.assertEqual(len(advisories), 1)
        self.assertEqual(advisories[0]["signature"], self.plane.list_principles(self.workspace_id)[0]["signature"])
        self.assertEqual(advisories[0]["node_ids"], ["deliver"])
        self.assertEqual(len(advisories[0]["citing_run_ids"]), 3)

    def test_a_matching_flow_still_runs_because_a_principle_never_brakes(self) -> None:
        for marker in ("a", "b", "c"):
            self._fail(self._denied_store_flow(marker), "release-1")

        fresh = self._denied_store_flow("d")
        run = self.plane.start_studio_run(
            self.workspace_id, fresh["id"], input_data={"value": "release-1"}
        )
        # Blocked by its own policy, not refused by the principle: a Run row
        # exists, with Steps and evidence.
        self.assertEqual(run["status"], "blocked")
        self.assertTrue(run["steps"])
        self.assertTrue(run["events"])

    def test_an_unrelated_flow_publishes_without_an_advisory(self) -> None:
        for marker in ("a", "b", "c"):
            self._fail(self._denied_store_flow(marker), "release-1")

        action = self.plane.create_action(
            self.workspace_id,
            name="Plain formatter",
            slug="plain-formatter",
            description="A deterministic formatter with no store authority.",
            kind="template",
            input_schema=OBJECT,
            output_schema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
                "additionalProperties": False,
            },
            config={"template": "{{value}}"},
            agent_version_id=None,
        )
        published = self.plane.create_studio_flow(
            self.workspace_id,
            name="Unrelated formatting",
            slug="unrelated-formatting",
            description="A Flow that shares no structure with the distilled failures.",
            input_schema=OBJECT,
            start_node_id="format",
            nodes=[
                {
                    "id": "format",
                    "type": "action",
                    "version_id": action["version"]["id"],
                    "input_mapping": {"value": {"source": "input", "path": "value"}},
                }
            ],
            routes=[],
        )
        self.assertEqual(published.get("advisories") or [], [])

    def test_a_repaired_structure_stops_matching(self) -> None:
        for marker in ("a", "b", "c"):
            self._fail(self._denied_store_flow(marker), "release-1")
        self.assertEqual(len(self.plane.list_principles(self.workspace_id)), 1)

        # A store Action that grants its declared write shares no structural
        # signature with the distilled failures.
        action = self.plane.create_action(
            self.workspace_id,
            name="Granted store",
            slug="granted-store",
            description="A bounded store whose declared write is enabled.",
            kind="data_store",
            input_schema=OBJECT,
            output_schema=STORE_OUTPUT,
            config={
                "operation": "append_record",
                "collection": "granted",
                "write_enabled": True,
            },
            agent_version_id=None,
        )
        published = self.plane.create_studio_flow(
            self.workspace_id,
            name="Granted delivery",
            slug="granted-delivery",
            description="A Flow whose store Action carries its write authority.",
            input_schema=OBJECT,
            start_node_id="deliver",
            nodes=[
                {
                    "id": "deliver",
                    "type": "action",
                    "version_id": action["version"]["id"],
                    "input_mapping": {"value": {"source": "input", "path": "value"}},
                }
            ],
            routes=[],
        )
        self.assertEqual(published.get("advisories") or [], [])

    def test_principles_are_derived_and_never_stored_as_mutable_state(self) -> None:
        for marker in ("a", "b", "c"):
            self._fail(self._denied_store_flow(marker), "release-1")

        with self.store.read() as connection:
            tables = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        self.assertNotIn("automation_principles", tables)


if __name__ == "__main__":
    unittest.main()
