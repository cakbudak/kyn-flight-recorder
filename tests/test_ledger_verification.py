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


class NoModelClient:
    def create(self, payload: dict[str, object]) -> dict[str, object]:
        del payload
        raise AssertionError("deterministic ledger tests must not call a model")


class LedgerVerificationContractTest(unittest.TestCase):
    """The Run projection carries a server-computed chain verdict.

    The guard ablation suite exposed that `verify_event_chain` had no caller in
    the product: the browser could only check that links joined up, which a
    rewritten payload survives. The verdict is recomputed from event material
    server-side so a tampered payload cannot present as intact.
    """

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = Store(Path(self.temporary.name) / "ledger.sqlite3")
        self.store.initialize()
        self.plane = ControlPlane(self.store, NoModelClient())
        bootstrap = self.plane.create_workspace(seed=False)
        self.workspace_id = bootstrap["workspace_id"]

    def _completed_run(self) -> dict[str, object]:
        action = self.plane.create_action(
            self.workspace_id,
            name="Echo value",
            slug="echo-value",
            description="A deterministic formatter used to produce a clean ledger.",
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
        flow = self.plane.create_studio_flow(
            self.workspace_id,
            name="Ledger sample",
            slug="ledger-sample",
            description="A Flow whose Run produces a verifiable event chain.",
            input_schema=OBJECT,
            start_node_id="echo",
            nodes=[
                {
                    "id": "echo",
                    "type": "action",
                    "version_id": action["version"]["id"],
                    "input_mapping": {"value": {"source": "input", "path": "value"}},
                }
            ],
            routes=[],
        )
        return self.plane.start_studio_run(
            self.workspace_id, flow["id"], input_data={"value": "ledger"}
        )

    def test_a_clean_run_reports_a_verified_ledger(self) -> None:
        run = self._completed_run()
        self.assertEqual(run["status"], "completed")
        self.assertTrue(run["ledger_verified"])

    def test_a_rewritten_payload_is_detected_even_though_the_links_still_join(self) -> None:
        run = self._completed_run()
        self.assertTrue(run["ledger_verified"])

        # Model the only threat the chain exists for: an actor with direct
        # database write access. The append-only trigger stops the product from
        # doing this, so it has to be dropped first — which is precisely why the
        # chain is a second, independent line of defence.
        with self.store.write() as connection:
            connection.execute("DROP TRIGGER trg_automation_events_no_update")
            connection.execute(
                "UPDATE automation_events SET payload_json = ? "
                "WHERE run_id = ? AND sequence = 1",
                ('{"tampered": true}', run["id"]),
            )

        tampered = self.plane.get_studio_run(self.workspace_id, run["id"])
        events = tampered["events"]
        links_intact = all(
            (index == 0 or event["prev_hash"] == events[index - 1]["event_hash"])
            and event["sequence"] == index + 1
            for index, event in enumerate(events)
        )
        self.assertTrue(links_intact, "the link-only view must still look clean")
        self.assertFalse(tampered["ledger_verified"])


if __name__ == "__main__":
    unittest.main()
