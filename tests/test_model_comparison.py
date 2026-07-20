from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.contracts import ContractViolation, verify_event_chain
from backend.service import ControlPlane
from backend.store import Store


class DisagreeingModelClient:
    """A provider seam where the brain, and only the brain, changes the answer.

    Two models clear the seeded quality gate and two do not, so a comparison has
    something real to disagree about.
    """

    HIGH_SCORE = {"gpt-5.6", "gpt-5.6-sol"}

    def __init__(self, store: Store) -> None:
        self.store = store
        self.models: list[str] = []

    def create(self, payload: dict[str, object]) -> dict[str, object]:
        if self.store.in_write_transaction():
            raise AssertionError("provider I/O happened inside a SQLite write transaction")
        model = str(payload.get("model"))
        self.models.append(model)
        confident = model in self.HIGH_SCORE
        result = {
            "summary": "The launch brief was assessed against the pinned rubric.",
            "score": 0.91 if confident else 0.28,
            "risks": ["A human must still authorize the public sandbox record."],
        }
        return {
            "id": f"resp_{model}_{len(self.models)}",
            "status": "completed",
            "model": model,
            "usage": {
                "input_tokens": 44,
                "output_tokens": 27 if confident else 19,
                "total_tokens": 71 if confident else 63,
            },
            "output": [
                {
                    "id": f"msg_{len(self.models)}",
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


BRIEF = (
    "Launch a public Build Week preview that demonstrates configurable Actions, "
    "Agents, Flows, approvals, and authoritative Runs."
)


class ModelComparisonContractTest(unittest.TestCase):
    """A comparison is only worth anything if it is provably controlled.

    Every sibling pins the identical Flow version, so every Action, Agent,
    Prompt, Skill and schema in the graph is the same. The single recorded delta
    is the model. Anyone can show a table of models; the pinning is what turns
    that table into evidence.
    """

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = Store(Path(self.temporary.name) / "comparison.sqlite3")
        self.store.initialize()
        self.client = DisagreeingModelClient(self.store)
        self.plane = ControlPlane(self.store, self.client)
        self.bootstrap = self.plane.create_workspace(seed=True)
        self.workspace_id = self.bootstrap["workspace_id"]
        self.flow_id = self.bootstrap["snapshot"]["studio"]["flows"][0]["id"]

    def _compare(self, models: list[str]) -> dict[str, object]:
        return self.plane.compare_studio_models(
            self.workspace_id,
            self.flow_id,
            input_data={"brief": BRIEF},
            models=models,
            client=self.client,
        )

    # -- the control -------------------------------------------------------

    def test_every_sibling_pins_the_identical_flow_version(self) -> None:
        comparison = self._compare(["gpt-5.6", "gpt-5.6-sol", "gpt-5.6-terra"])
        siblings = comparison["siblings"]

        self.assertEqual(len(siblings), 3)
        pinned = {sibling["flow_version_id"] for sibling in siblings}
        self.assertEqual(len(pinned), 1, "a comparison must vary only the model")
        self.assertEqual(comparison["flow_version_id"], pinned.pop())
        self.assertEqual(len({sibling["input_fingerprint"] for sibling in siblings}), 1)

    def test_each_sibling_records_the_model_it_actually_ran(self) -> None:
        comparison = self._compare(["gpt-5.6", "gpt-5.6-terra"])
        by_model = {sibling["model"]: sibling for sibling in comparison["siblings"]}

        self.assertEqual(set(by_model), {"gpt-5.6", "gpt-5.6-terra"})
        self.assertEqual(sorted(self.client.models), ["gpt-5.6", "gpt-5.6-terra"])
        for model, sibling in by_model.items():
            run = self.plane.get_studio_run(self.workspace_id, sibling["run_id"])
            self.assertEqual(run["model_override"], model)
            self.assertEqual(run["relation_kind"], "comparison")
            self.assertEqual(run["comparison_id"], comparison["id"])

    def test_the_override_is_written_into_the_hash_linked_chain(self) -> None:
        comparison = self._compare(["gpt-5.6", "gpt-5.6-luna"])

        for sibling in comparison["siblings"]:
            run = self.plane.get_studio_run(self.workspace_id, sibling["run_id"])
            self.assertTrue(verify_event_chain(run["events"]))
            overrides = [
                event for event in run["events"] if event["type"] == "run.model_overridden"
            ]
            self.assertEqual(len(overrides), 1)
            self.assertEqual(overrides[0]["payload"]["model"], sibling["model"])
            self.assertEqual(
                overrides[0]["payload"]["pinned_model"],
                comparison["pinned_model"],
            )

    # -- containment of the exception --------------------------------------

    def test_a_normal_run_can_never_carry_a_model_override(self) -> None:
        with self.assertRaises(TypeError):
            self.plane.start_studio_run(
                self.workspace_id,
                self.flow_id,
                input_data={"brief": BRIEF},
                client=self.client,
                model_override="gpt-5.6-luna",
            )

        run = self.plane.start_studio_run(
            self.workspace_id, self.flow_id, input_data={"brief": BRIEF}, client=self.client
        )
        self.assertIsNone(run["model_override"])
        self.assertEqual(run["relation_kind"], "root")

    def test_a_model_outside_the_supported_set_is_refused(self) -> None:
        with self.assertRaises(ContractViolation):
            self._compare(["gpt-5.6", "definitely-not-a-model"])

        # Nothing partial: a refused comparison creates no Runs at all.
        runs = self.plane.snapshot(self.workspace_id)["studio"]["runs"]
        self.assertEqual([run for run in runs if run["comparison_id"]], [])

    def test_a_comparison_needs_at_least_two_distinct_models(self) -> None:
        for models in ([], ["gpt-5.6"], ["gpt-5.6", "gpt-5.6"]):
            with self.assertRaises(ContractViolation):
                self._compare(models)

    def test_a_flow_that_calls_no_model_cannot_be_compared(self) -> None:
        deterministic = self.plane.create_studio_flow(
            self.workspace_id,
            name="Deterministic only",
            slug="deterministic-only",
            description="A Flow with no AI node, so no brain to vary.",
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
            start_node_id="echo",
            nodes=[
                {
                    "id": "echo",
                    "type": "action",
                    "version_id": next(
                        action["version"]["id"]
                        for action in self.plane.snapshot(self.workspace_id)["studio"]["actions"]
                        if action["version"]["kind"] == "template"
                    ),
                    "input_mapping": {"summary": {"source": "input", "path": "value"}},
                }
            ],
            routes=[],
        )
        with self.assertRaises(ContractViolation):
            self.plane.compare_studio_models(
                self.workspace_id,
                deterministic["id"],
                input_data={"value": "nothing to compare"},
                models=["gpt-5.6", "gpt-5.6-sol"],
                client=self.client,
            )

    # -- the scoreboard ----------------------------------------------------

    def test_the_scoreboard_reports_only_what_the_provider_actually_returned(self) -> None:
        comparison = self._compare(["gpt-5.6", "gpt-5.6-terra"])
        sibling = comparison["siblings"][0]

        for field in ("status", "outcome", "total_tokens", "duration_ms", "effect_count"):
            self.assertIn(field, sibling)
        self.assertEqual(sibling["total_tokens"], 71)
        # A wrong cost figure is worse than none, so none is printed.
        self.assertNotIn("cost_usd", sibling)
        self.assertNotIn("price", json.dumps(comparison).lower())

    def test_disagreement_between_brains_is_the_headline(self) -> None:
        comparison = self._compare(
            ["gpt-5.6", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"]
        )

        self.assertTrue(comparison["disagreed"])
        outcomes = {sibling["model"]: sibling["outcome"] for sibling in comparison["siblings"]}
        self.assertEqual(outcomes["gpt-5.6"], outcomes["gpt-5.6-sol"])
        self.assertEqual(outcomes["gpt-5.6-terra"], outcomes["gpt-5.6-luna"])
        self.assertNotEqual(outcomes["gpt-5.6"], outcomes["gpt-5.6-terra"])

    def test_agreeing_brains_are_reported_as_agreeing(self) -> None:
        comparison = self._compare(["gpt-5.6", "gpt-5.6-sol"])
        self.assertFalse(comparison["disagreed"])
        self.assertEqual(len({s["outcome"] for s in comparison["siblings"]}), 1)

    def test_the_comparison_is_readable_again_from_the_workspace(self) -> None:
        created = self._compare(["gpt-5.6", "gpt-5.6-terra"])
        listed = self.plane.list_comparisons(self.workspace_id)

        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["id"], created["id"])
        self.assertEqual(listed[0]["flow_version_id"], created["flow_version_id"])
        self.assertEqual(len(listed[0]["siblings"]), 2)


if __name__ == "__main__":
    unittest.main()
