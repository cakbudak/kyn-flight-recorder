"""The shipped seed reaches the stop seam, both ways, on one pinned version.

`tests/test_stop_seam_runtime.py` proves the seam against Flows it publishes for
itself. That leaves the thing a judge actually sees untested: a fresh workspace
seeds Flows, and until this suite existed none of them declared an acceptance
contract, so nothing in the demo could ever reach the seam. A feature nobody can
reach is not shipped.

The claim under test is the spec's central distinction, made concrete in seeded
data: *the same pinned Flow version both refuses and admits, decided by nothing
but the Run input.* That is why `completion_unevidenced` is non-ratifiable — it
is a property of the data, not of the definition — and it is why the refusal is
honest rather than staged: the evidence genuinely does not exist on the branch
the refusing input takes.

The judge here is the same deterministic provider-shaped seam the rest of the
suite uses. It reads the candidate set and anchors what is there, so a refusal
below is caused by the absence of a record and never by a scripted verdict.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from backend.contracts import verify_event_chain
from backend.service import ControlPlane
from backend.store import Store
from tests.test_runtime_contract import ScriptedResponsesClient


CONTRACTED_SLUG = "contracted-evidence-publication"
LAUNCH_SLUG = "agent-reviewed-launch"
RECORD = "Public Build Week launch note, ready for the evidence ledger."


class SeededContractCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = Store(Path(self.temporary.name) / "seeded-contract.sqlite3")
        self.store.initialize()
        self.client = ScriptedResponsesClient(self.store)
        self.plane = ControlPlane(self.store, self.client)
        self.bootstrap = self.plane.create_workspace(seed=True)
        self.workspace_id = self.bootstrap["workspace_id"]

    def flow(self, slug: str) -> dict[str, Any]:
        return next(
            item
            for item in self.bootstrap["snapshot"]["studio"]["flows"]
            if item["slug"] == slug
        )

    def start(self, readiness: float, key: str) -> dict[str, Any]:
        return self.plane.start_studio_run(
            self.workspace_id,
            self.flow(CONTRACTED_SLUG)["id"],
            input_data={"record": RECORD, "readiness": readiness},
            idempotency_key=key,
        )

    @staticmethod
    def status_history(run: dict[str, Any]) -> list[str]:
        return [
            event["payload"]["to"]
            for event in run["events"]
            if event["type"] == "run.status_changed"
        ]

    @staticmethod
    def completion_event(run: dict[str, Any]) -> dict[str, Any]:
        events = [
            event for event in run["events"] if event["type"].startswith("completion.")
        ]
        if len(events) != 1:
            raise AssertionError(
                f"exactly one adjudication belongs in a Run's ledger, got {len(events)}"
            )
        return events[0]


class SeededDeclarationTest(SeededContractCase):
    def test_the_seed_publishes_one_contracted_flow_beside_the_untouched_others(
        self,
    ) -> None:
        flows = self.bootstrap["snapshot"]["studio"]["flows"]
        contracted = self.flow(CONTRACTED_SLUG)
        version = contracted["version"]

        # The pre-existing Flow is still first and still declares nothing, so the
        # inertness guarantee survives the addition.
        self.assertEqual(flows[0]["slug"], LAUNCH_SLUG)
        self.assertEqual(flows[0]["version"]["acceptance_criteria"], [])
        self.assertIsNone(flows[0]["version"]["judge_agent_version_id"])

        self.assertEqual(
            [item["id"] for item in version["acceptance_criteria"]],
            ["record-in-ledger", "ledger-write-succeeded"],
        )
        self.assertEqual(
            [item["evidence_kind"] for item in version["acceptance_criteria"]],
            ["effect", "receipt"],
        )
        for criterion in version["acceptance_criteria"]:
            self.assertEqual(criterion["node_ids"], ["publish-to-ledger"])
            # A statement a reader can act on without opening documentation.
            self.assertGreaterEqual(len(criterion["statement"]), 40)
        self.assertIsNotNone(version["judge_agent_version_id"])

    def test_the_declared_judge_is_cast_by_no_node_of_the_flow_it_judges(self) -> None:
        """Independence is a property of the casting, not of the prompt.

        Publication already refuses self-adjudication, so this asserts the seed
        stays on the right side of that guard rather than re-testing the guard.
        """

        version = self.flow(CONTRACTED_SLUG)["version"]
        judge = version["judge_agent_version_id"]
        cast: set[str] = set()
        for node in version["nodes"]:
            self.assertEqual(node["type"], "action")
            action = self.plane.studio.get_action_version(
                self.workspace_id, node["version_id"]
            )
            if action["agent_version_id"]:
                cast.add(str(action["agent_version_id"]))
        self.assertNotIn(judge, cast)

        # And the judge is a real Agent of this workspace, not a dangling id.
        self.assertEqual(
            self.plane.studio.get_agent_runtime(self.workspace_id, judge)["id"], judge
        )

    def test_the_contracted_flow_pins_no_model_backed_node(self) -> None:
        """So the whole model cost of an adjudicated Run is the adjudication.

        Stated as a test because it is a budget guarantee, not an accident of the
        current graph: the live host caps model calls, and the refuse-then-admit
        beat has to fit inside that cap.

        Asserted over the pinned node kinds rather than over `requires_model`.
        The two used to agree and no longer do: a Flow that declares a
        Goal-Judge requires a model even when no node is model-backed, because
        the judge is cast on the Flow. Reading the budget guarantee off
        `requires_model` would now silently assert the opposite of what this
        test is named for.
        """

        version = self.flow(CONTRACTED_SLUG)["version"]
        kinds = {
            self.plane.studio.get_action_version(
                self.workspace_id, node["version_id"]
            )["kind"]
            for node in version["nodes"]
            if node["type"] == "action"
        }
        self.assertNotIn("ai", kinds)

    def test_declaring_a_judge_makes_the_flow_require_a_model(self) -> None:
        """The operator must be told a key is needed before the Run needs it.

        `requires_model` drives the Run modal's credential copy. A judge-only
        Flow that reported itself deterministic would tell a visitor no key was
        required and then fail at the stop seam for want of that exact
        credential — the product contradicting itself one screen later.
        """

        self.assertTrue(self.flow(CONTRACTED_SLUG)["version"]["requires_model"])


class SeededRefuseThenAdmitTest(SeededContractCase):
    def test_an_input_that_skips_the_declared_site_never_reaches_completed(
        self,
    ) -> None:
        run = self.start(0.31, "seeded-refusal")

        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["error_code"], "completion_unevidenced")
        self.assertNotIn("completed", self.status_history(run))
        self.assertEqual(self.status_history(run), ["running", "failed"])
        self.assertIsNone(run["output"])
        self.assertTrue(verify_event_chain(run["events"]))

        refused = self.completion_event(run)
        self.assertEqual(refused["type"], "completion.refused")
        self.assertFalse(refused["payload"]["admitted"])
        self.assertEqual(
            refused["payload"]["unevidenced"],
            ["record-in-ledger", "ledger-write-succeeded"],
        )
        for criterion in refused["payload"]["criteria"]:
            self.assertFalse(criterion["holds"])
            self.assertEqual(criterion["surviving"], [])
            self.assertEqual(criterion["declared_sites"], ["publish-to-ledger"])

        # The refusal is recorded while the Run is still running, which is what
        # makes it a refusal rather than a comment on a decision already taken.
        types = [event["type"] for event in run["events"]]
        self.assertLess(types.index("completion.refused"), len(types) - 1)
        self.assertEqual(types[-1], "run.status_changed")

        # Nothing was rolled back: the work this Run did do is still on record,
        # and the work it never did is genuinely absent.
        self.assertEqual(run["effects"], [])
        self.assertEqual(
            [step["node_id"] for step in run["steps"]],
            ["readiness-gate", "hold-for-revision"],
        )
        self.assertEqual(len(run["model_calls"]), 1)

    def test_the_same_pinned_version_admits_when_the_run_reaches_the_declared_site(
        self,
    ) -> None:
        refused = self.start(0.31, "seeded-refusal")
        admitted = self.start(0.92, "seeded-admission")

        # The control: one immutable version, two inputs, two honest verdicts.
        self.assertEqual(refused["flow_version_id"], admitted["flow_version_id"])
        self.assertEqual(refused["flow_fingerprint"], admitted["flow_fingerprint"])

        self.assertEqual(admitted["status"], "completed")
        self.assertIsNone(admitted["error_code"])
        self.assertEqual(self.status_history(admitted), ["running", "completed"])
        self.assertTrue(verify_event_chain(admitted["events"]))
        self.assertEqual(len(admitted["effects"]), 1)
        self.assertEqual(admitted["effects"][0]["collection"], "published-evidence")

        event = self.completion_event(admitted)
        self.assertEqual(event["type"], "completion.admitted")
        self.assertEqual(event["payload"]["unevidenced"], [])
        for criterion in event["payload"]["criteria"]:
            self.assertTrue(criterion["holds"])
            self.assertTrue(criterion["surviving"])
            self.assertEqual(criterion["discarded"], [])

        # Two adjudicated Runs, two model calls. The beat's whole spend.
        self.assertEqual(len(admitted["model_calls"]), 1)
        self.assertEqual(
            len(
                [
                    request
                    for request in self.client.requests
                    if (request.get("metadata") or {}).get("operation") == "adjudication"
                ]
            ),
            2,
        )


class SeedAdditivityTest(unittest.TestCase):
    """Seeds are pinned, fingerprinted data, so adding one must move nothing.

    A Flow version's fingerprint embeds randomly-minted pinned resource ids, so
    two workspaces seeded by identical code already disagree on the raw digest.
    The invariant that *is* meaningful is the material the digest is taken over,
    read with those ids replaced by the stable slug of the resource they name.
    """

    def canonical_launch_material(self, *, contracted: bool) -> str:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        store = Store(Path(temporary.name) / "additivity.sqlite3")
        store.initialize()
        plane = ControlPlane(store, ScriptedResponsesClient(store))
        if contracted:
            snapshot = plane.create_workspace(seed=True)["snapshot"]
        else:
            with mock.patch.object(
                ControlPlane, "_seed_contracted_flow", lambda *_: None
            ):
                snapshot = plane.create_workspace(seed=True)["snapshot"]

        names: dict[str, str] = {}
        for action in snapshot["studio"]["actions"]:
            for version in action["versions"]:
                names[version["id"]] = f"action:{action['slug']}:v{version['version']}"
        for agent in snapshot["agents"]:
            for version in agent["versions"]:
                names[version["id"]] = f"agent:{agent['slug']}:v{version['version']}"

        def canonicalize(value: Any) -> Any:
            if isinstance(value, dict):
                # Pinned fingerprints are dropped rather than compared: they are
                # themselves taken over ids this workspace minted at random, so
                # they differ between two runs of identical code. The version_id
                # beside each one is canonicalized instead, which says the same
                # thing without the randomness.
                return {
                    key: canonicalize(item)
                    for key, item in value.items()
                    if key != "fingerprint"
                }
            if isinstance(value, list):
                return [canonicalize(item) for item in value]
            return names.get(value, value) if isinstance(value, str) else value

        version = next(
            flow["version"]
            for flow in snapshot["studio"]["flows"]
            if flow["slug"] == LAUNCH_SLUG
        )
        return json.dumps(
            canonicalize(
                {
                    key: version[key]
                    for key in (
                        "input_schema",
                        "output_schema",
                        "outcomes",
                        "start_node_id",
                        "nodes",
                        "routes",
                        "pinned_resources",
                        "acceptance_criteria",
                        "judge_agent_version_id",
                    )
                }
            ),
            sort_keys=True,
        )

    def test_adding_the_contracted_flow_does_not_move_the_launch_flows_material(
        self,
    ) -> None:
        self.assertEqual(
            self.canonical_launch_material(contracted=False),
            self.canonical_launch_material(contracted=True),
        )


if __name__ == "__main__":
    unittest.main()
