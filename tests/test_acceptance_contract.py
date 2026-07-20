"""The acceptance contract a Flow version declares, and the two refusals it earns.

Criteria follow the `outcomes` precedent exactly: normalized in `contracts.py`,
threaded into the version material, and therefore pinned by the version
fingerprint. What is new here is that a declaration can be *refused at
publication* — before any Run exists — when the node a criterion pins could not
possibly mint the evidence it demands, or when the declared judge is one of the
Agents it would be judging.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.contracts import (
    ContractViolation,
    fingerprint,
    normalize_acceptance_criteria,
)
from backend.service import ControlPlane
from backend.store import Store


VALUE_SCHEMA = {
    "type": "object",
    "properties": {"value": {"type": "string"}},
    "required": ["value"],
    "additionalProperties": False,
}
TEXT_OUTPUT = {
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
APPROVAL_OUTPUT = {
    "type": "object",
    "properties": {"approved": {"type": "boolean"}, "reason": {"type": "string"}},
    "required": ["approved", "reason"],
    "additionalProperties": False,
}
NO_RETRY = {
    "max_attempts": 1,
    "backoff_seconds": 0,
    "retry_on": [],
    "on_error": "fail",
}
VALUE_MAPPING = {"value": {"source": "input", "path": "value"}}


def criterion(
    criterion_id: str,
    evidence_kind: str,
    *node_ids: str,
    statement: str = "The declared work was performed.",
) -> dict[str, object]:
    return {
        "id": criterion_id,
        "statement": statement,
        "evidence_kind": evidence_kind,
        "node_ids": list(node_ids),
    }


class NoModelClient:
    def create(self, payload: dict[str, object]) -> dict[str, object]:
        del payload
        raise AssertionError("publication must never call a model")


class AcceptanceCriteriaNormalizationTest(unittest.TestCase):
    def test_absent_criteria_normalize_to_an_empty_contract(self) -> None:
        self.assertEqual(normalize_acceptance_criteria(None, "Flow criteria"), [])
        self.assertEqual(normalize_acceptance_criteria([], "Flow criteria"), [])

    def test_a_declared_criterion_keeps_its_id_statement_kind_and_nodes(self) -> None:
        normalized = normalize_acceptance_criteria(
            [
                {
                    "id": "record-written",
                    "statement": "  The launch record was written to the store.  ",
                    "evidence_kind": "effect",
                    "node_ids": ["writer"],
                }
            ],
            "Flow criteria",
        )
        self.assertEqual(
            normalized,
            [
                {
                    "id": "record-written",
                    "statement": "The launch record was written to the store.",
                    "evidence_kind": "effect",
                    "node_ids": ["writer"],
                }
            ],
        )

    def test_declared_sites_are_a_set_so_their_order_cannot_move_a_fingerprint(self) -> None:
        """`node_ids` is semantically a set, so the material must canonicalize it."""

        one = normalize_acceptance_criteria(
            [criterion("c", "effect", "west", "east")], "Flow criteria"
        )
        other = normalize_acceptance_criteria(
            [criterion("c", "effect", "east", "west")], "Flow criteria"
        )
        self.assertEqual(one, other)
        self.assertEqual(one[0]["node_ids"], ["east", "west"])

    def test_a_criterion_must_declare_at_least_one_site(self) -> None:
        with self.assertRaises(ContractViolation):
            normalize_acceptance_criteria([criterion("c", "effect")], "Flow criteria")

    def test_a_criterion_may_not_name_the_same_site_twice(self) -> None:
        with self.assertRaises(ContractViolation):
            normalize_acceptance_criteria(
                [criterion("c", "effect", "writer", "writer")], "Flow criteria"
            )

    def test_every_kind_in_the_closed_vocabulary_is_accepted(self) -> None:
        for kind in ("effect", "receipt", "approval", "step"):
            with self.subTest(kind=kind):
                normalized = normalize_acceptance_criteria(
                    [criterion("c", kind, "writer")], "Flow criteria"
                )
                self.assertEqual(normalized[0]["evidence_kind"], kind)

    def test_an_unknown_evidence_kind_is_refused(self) -> None:
        with self.assertRaises(ContractViolation):
            normalize_acceptance_criteria(
                [criterion("c", "vibes", "writer")], "Flow criteria"
            )

    def test_criterion_ids_must_be_unique_within_the_flow(self) -> None:
        with self.assertRaises(ContractViolation):
            normalize_acceptance_criteria(
                [criterion("c", "step", "one"), criterion("c", "step", "two")],
                "Flow criteria",
            )

    def test_a_criterion_may_only_pin_nodes_the_flow_declares(self) -> None:
        self.assertEqual(
            len(
                normalize_acceptance_criteria(
                    [criterion("c", "step", "writer", "gate")],
                    "Flow criteria",
                    node_ids={"writer", "gate"},
                )
            ),
            1,
        )
        with self.assertRaises(ContractViolation) as caught:
            normalize_acceptance_criteria(
                [criterion("c", "step", "writer", "ghost")],
                "Flow criteria",
                node_ids={"writer", "gate"},
            )
        self.assertIn("ghost", str(caught.exception))

    def test_an_unbounded_contract_is_refused(self) -> None:
        with self.assertRaises(ContractViolation):
            normalize_acceptance_criteria(
                [criterion(f"c{index}", "step", "writer") for index in range(9)],
                "Flow criteria",
            )

    def test_an_invalid_criterion_shape_is_refused(self) -> None:
        for item in (
            "record-written",
            {"id": "c", "statement": "A claim.", "evidence_kind": "step"},
            {**criterion("c", "step", "writer"), "tone": "ai"},
            {**criterion("c", "step", "writer"), "id": "Not A Slug"},
            {**criterion("c", "step", "writer"), "statement": ""},
            {**criterion("c", "step", "writer"), "node_ids": ["Not A Node"]},
            {**criterion("c", "step", "writer"), "node_ids": "writer"},
        ):
            with self.subTest(item=item):
                with self.assertRaises(ContractViolation):
                    normalize_acceptance_criteria([item], "Flow criteria")


class AcceptanceContractHarness(unittest.TestCase):
    """Four probe Actions over one input contract, so any of them composes into
    any graph shape and only the pinned capability under test varies."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = Store(Path(self.temporary.name) / "acceptance.sqlite3")
        self.store.initialize()
        self.plane = ControlPlane(self.store, NoModelClient())
        self.bootstrap = self.plane.create_workspace(seed=True)
        self.workspace_id = self.bootstrap["workspace_id"]
        self.agents = {
            agent["slug"]: agent["version"]
            for agent in self.bootstrap["snapshot"]["agents"]
        }
        self.seeded_actions = {
            action["slug"]: action["version"]
            for action in self.bootstrap["snapshot"]["studio"]["actions"]
        }
        self.judge = self.agents["run-forensicist"]["id"]
        self.probes = {
            "quiet": self._probe(
                "probe-quiet",
                kind="template",
                output_schema=TEXT_OUTPUT,
                config={"template": "{{value}}"},
            ),
            "writer": self._probe(
                "probe-writer",
                kind="data_store",
                output_schema=STORE_OUTPUT,
                config={
                    "operation": "append_record",
                    "collection": "probe-evidence",
                    "write_enabled": True,
                },
            ),
            "denied-writer": self._probe(
                "probe-denied-writer",
                kind="data_store",
                output_schema=STORE_OUTPUT,
                config={
                    "operation": "append_record",
                    "collection": "probe-denied",
                    "write_enabled": False,
                },
            ),
            "approver": self._probe(
                "probe-approver",
                kind="approval",
                output_schema=APPROVAL_OUTPUT,
                config={"message_template": "Approve {{value}}?"},
            ),
        }

    def _probe(
        self,
        slug: str,
        *,
        kind: str,
        output_schema: dict[str, object],
        config: dict[str, object],
    ) -> str:
        action = self.plane.create_action(
            self.workspace_id,
            name=f"Probe {slug}",
            slug=slug,
            description="A probe Action pinned to exercise one publication guard.",
            kind=kind,
            input_schema=VALUE_SCHEMA,
            output_schema=output_schema,
            config=config,
            agent_version_id=None,
        )
        return str(action["version"]["id"])

    def node(self, probe: str, node_id: str) -> dict[str, object]:
        return {
            "id": node_id,
            "type": "action",
            "version_id": self.probes[probe],
            "input_mapping": dict(VALUE_MAPPING),
            "settings": dict(NO_RETRY),
        }

    def publish(self, slug: str, nodes: list[dict[str, object]], **extra: object):
        return self.plane.create_studio_flow(
            self.workspace_id,
            name=f"Flow {slug}",
            slug=slug,
            description="A Flow published to exercise its declared acceptance contract.",
            input_schema=VALUE_SCHEMA,
            start_node_id=str(nodes[0]["id"]),
            nodes=nodes,
            routes=extra.pop("routes", []),
            **extra,
        )


class InertWithoutCriteriaTest(AcceptanceContractHarness):
    def test_a_flow_that_declares_nothing_publishes_with_an_empty_contract(self) -> None:
        flow = self.publish("inert-flow", [self.node("writer", "writer")])
        self.assertEqual(flow["version"]["acceptance_criteria"], [])
        self.assertIsNone(flow["version"]["judge_agent_version_id"])

    def test_a_criteria_free_flow_fingerprints_exactly_as_it_did_before(self) -> None:
        """Inertness proved where it is load-bearing: the version fingerprint.

        A Flow that declares no contract must hash the same material it hashed
        before this feature existed, or every pinned version in the world moves.
        """

        flow = self.publish("unpinned-flow", [self.node("writer", "writer")])
        version = flow["version"]
        self.assertEqual(
            version["fingerprint"],
            fingerprint(
                {
                    "input_schema": version["input_schema"],
                    "output_schema": version["output_schema"],
                    "outcomes": version["outcomes"],
                    "start_node_id": version["start_node_id"],
                    "nodes": version["nodes"],
                    "routes": version["routes"],
                    "pinned_resources": version["pinned_resources"],
                }
            ),
        )

    def test_a_declared_contract_enters_the_version_fingerprint(self) -> None:
        bare = self.publish("bare-flow", [self.node("writer", "writer")])
        contracted = self.publish(
            "contracted-flow",
            [self.node("writer", "writer")],
            acceptance_criteria=[criterion("record-written", "effect", "writer")],
            judge_agent_version_id=self.judge,
        )
        self.assertNotEqual(
            bare["version"]["fingerprint"], contracted["version"]["fingerprint"]
        )
        self.assertEqual(
            contracted["version"]["acceptance_criteria"],
            [criterion("record-written", "effect", "writer")],
        )

    def test_a_revision_pins_its_own_contract(self) -> None:
        flow = self.publish("revised-flow", [self.node("writer", "writer")])
        revised = self.plane.revise_studio_flow(
            self.workspace_id,
            flow["id"],
            expected_revision=flow["revision"],
            input_schema=VALUE_SCHEMA,
            start_node_id="writer",
            nodes=[self.node("writer", "writer")],
            routes=[],
            acceptance_criteria=[criterion("record-written", "effect", "writer")],
            judge_agent_version_id=self.judge,
        )
        self.assertEqual(revised["version"]["version"], 2)
        self.assertEqual(len(revised["version"]["acceptance_criteria"]), 1)
        self.assertNotEqual(
            revised["version"]["fingerprint"], flow["version"]["fingerprint"]
        )

    def test_a_revision_that_omits_the_contract_carries_it_forward(self) -> None:
        """Silence must not retire a safety contract, and `[]` must still clear it."""

        flow = self.publish(
            "carried-flow",
            [self.node("writer", "writer")],
            acceptance_criteria=[criterion("record-written", "effect", "writer")],
            judge_agent_version_id=self.judge,
        )

        def revise(revision: int, **extra: object):
            return self.plane.revise_studio_flow(
                self.workspace_id,
                flow["id"],
                expected_revision=revision,
                input_schema=VALUE_SCHEMA,
                start_node_id="writer",
                nodes=[self.node("writer", "writer")],
                routes=[],
                **extra,
            )

        carried = revise(flow["revision"])
        self.assertEqual(
            carried["version"]["acceptance_criteria"],
            [criterion("record-written", "effect", "writer")],
        )
        self.assertEqual(carried["version"]["judge_agent_version_id"], self.judge)

        cleared = revise(carried["revision"], acceptance_criteria=[])
        self.assertEqual(cleared["version"]["acceptance_criteria"], [])
        self.assertIsNone(cleared["version"]["judge_agent_version_id"])


class UnsatisfiableContractTest(AcceptanceContractHarness):
    """A Flow may not declare a contract its own pinned graph cannot satisfy."""

    def test_an_effect_criterion_pinned_to_a_non_writing_node_is_refused(self) -> None:
        with self.assertRaises(ContractViolation) as caught:
            self.publish(
                "quiet-effect",
                [self.node("quiet", "gate")],
                acceptance_criteria=[criterion("record-written", "effect", "gate")],
                judge_agent_version_id=self.judge,
            )
        self.assertIn("record-written", str(caught.exception))

    def test_the_guard_reads_the_pinned_node_not_merely_some_node(self) -> None:
        """The decisive test for the sharper guard.

        This graph *does* contain a writing node, so an existential check would
        admit it. The criterion pins the quiet node, which can never mint an
        effect, so the contract is still unsatisfiable and must be refused.
        """

        with self.assertRaises(ContractViolation) as caught:
            self.publish(
                "misattributed-effect",
                [self.node("quiet", "gate"), self.node("writer", "writer")],
                routes=[{"from": "gate", "to": "writer", "outcome": "success"}],
                acceptance_criteria=[criterion("record-written", "effect", "gate")],
                judge_agent_version_id=self.judge,
            )
        self.assertIn("gate", str(caught.exception))

    def test_an_effect_criterion_pinned_to_a_writing_node_publishes(self) -> None:
        flow = self.publish(
            "attributed-effect",
            [self.node("quiet", "gate"), self.node("writer", "writer")],
            routes=[{"from": "gate", "to": "writer", "outcome": "success"}],
            acceptance_criteria=[criterion("record-written", "effect", "writer")],
            judge_agent_version_id=self.judge,
        )
        self.assertEqual(
            flow["version"]["acceptance_criteria"][0]["node_ids"], ["writer"]
        )

    def test_a_store_node_whose_policy_forbids_writing_cannot_satisfy_an_effect(self) -> None:
        """`can write` is the runtime's predicate, not the Action's kind.

        A Data Store Action pinned with `write_enabled: false` is blocked on
        every attempt, so it can never mint an effect and can never satisfy an
        effect criterion.
        """

        with self.assertRaises(ContractViolation):
            self.publish(
                "denied-effect",
                [self.node("denied-writer", "writer")],
                acceptance_criteria=[criterion("record-written", "effect", "writer")],
                judge_agent_version_id=self.judge,
            )

    def test_an_approval_criterion_pinned_to_a_non_approval_node_is_refused(self) -> None:
        with self.assertRaises(ContractViolation) as caught:
            self.publish(
                "unapproved",
                [self.node("writer", "writer")],
                acceptance_criteria=[criterion("human-agreed", "approval", "writer")],
                judge_agent_version_id=self.judge,
            )
        self.assertIn("human-agreed", str(caught.exception))

    def test_an_approval_criterion_pinned_to_a_human_approval_node_publishes(self) -> None:
        flow = self.publish(
            "approved",
            [self.node("approver", "approver")],
            acceptance_criteria=[criterion("human-agreed", "approval", "approver")],
            judge_agent_version_id=self.judge,
        )
        self.assertEqual(len(flow["version"]["acceptance_criteria"]), 1)

    def test_receipt_and_step_criteria_are_satisfiable_by_any_node(self) -> None:
        for kind in ("receipt", "step"):
            with self.subTest(kind=kind):
                flow = self.publish(
                    f"any-node-{kind}",
                    [self.node("quiet", "gate")],
                    acceptance_criteria=[criterion("work-happened", kind, "gate")],
                    judge_agent_version_id=self.judge,
                )
                self.assertEqual(len(flow["version"]["acceptance_criteria"]), 1)

    def test_a_criterion_may_name_several_capable_sites(self) -> None:
        """The branching case the single-site shape could not express.

        Either writer legitimately does the work, so a Run taking either branch
        must be able to satisfy the criterion.
        """

        flow = self.publish(
            "either-writer",
            [
                self.node("quiet", "gate"),
                self.node("writer", "west"),
                self.node("writer", "east"),
            ],
            routes=[
                {"from": "gate", "to": "west", "outcome": "success"},
                {"from": "gate", "to": "east", "outcome": "error"},
            ],
            acceptance_criteria=[criterion("record-written", "effect", "west", "east")],
            judge_agent_version_id=self.judge,
        )
        self.assertEqual(
            flow["version"]["acceptance_criteria"][0]["node_ids"], ["east", "west"]
        )

    def test_every_named_site_must_be_capable_not_merely_one(self) -> None:
        """Naming an incapable site beside a capable one can only mislead.

        A reader seeing two declared sites is owed the promise that either one
        could carry the claim. Admitting this would make the contract read as
        stronger than it is.
        """

        with self.assertRaises(ContractViolation) as caught:
            self.publish(
                "one-capable-one-not",
                [
                    self.node("quiet", "gate"),
                    self.node("writer", "writer"),
                ],
                routes=[{"from": "gate", "to": "writer", "outcome": "success"}],
                acceptance_criteria=[
                    criterion("record-written", "effect", "writer", "gate")
                ],
                judge_agent_version_id=self.judge,
            )
        self.assertIn("gate", str(caught.exception))
        self.assertIn("record-written", str(caught.exception))

    def test_a_criterion_pinned_to_an_undeclared_node_is_refused(self) -> None:
        with self.assertRaises(ContractViolation) as caught:
            self.publish(
                "ghost-node",
                [self.node("writer", "writer")],
                acceptance_criteria=[criterion("record-written", "effect", "ghost")],
                judge_agent_version_id=self.judge,
            )
        self.assertIn("ghost", str(caught.exception))


class SelfAdjudicationTest(AcceptanceContractHarness):
    """Independence is a property of the casting, not of the prompt."""

    def agent_node(self, version_id: str, node_id: str = "analyst") -> dict[str, object]:
        return {
            "id": node_id,
            "type": "agent",
            "version_id": version_id,
            "input_mapping": {"brief": {"source": "input", "path": "value"}},
            "settings": dict(NO_RETRY),
        }

    def test_the_judge_may_not_be_an_agent_version_pinned_by_a_node(self) -> None:
        analyst = self.agents["launch-analyst"]["id"]
        with self.assertRaises(ContractViolation) as caught:
            self.publish(
                "self-judging",
                [self.agent_node(analyst)],
                acceptance_criteria=[criterion("work-happened", "step", "analyst")],
                judge_agent_version_id=analyst,
            )
        self.assertIn("judge", str(caught.exception).lower())

    def test_the_judge_may_not_be_the_agent_an_ai_action_pins(self) -> None:
        """The casting is checked through the Action, not only at the node.

        An AI Action pins an Agent version of its own. A judge hiding one
        indirection behind an Action is still grading its own homework.
        """

        ai_action = self.seeded_actions["ai-launch-analysis"]
        with self.assertRaises(ContractViolation):
            self.publish(
                "indirect-self-judging",
                [
                    {
                        "id": "analysis",
                        "type": "action",
                        "version_id": ai_action["id"],
                        "input_mapping": {"brief": {"source": "input", "path": "value"}},
                        "settings": dict(NO_RETRY),
                    }
                ],
                acceptance_criteria=[criterion("work-happened", "step", "analysis")],
                judge_agent_version_id=ai_action["agent_version_id"],
            )

    def test_an_independent_judge_publishes(self) -> None:
        flow = self.publish(
            "independent-judge",
            [self.agent_node(self.agents["launch-analyst"]["id"])],
            acceptance_criteria=[criterion("work-happened", "step", "analyst")],
            judge_agent_version_id=self.judge,
        )
        self.assertEqual(flow["version"]["judge_agent_version_id"], self.judge)


class SubflowSelfAdjudicationTest(AcceptanceContractHarness):
    """Independence must survive one indirection through a pinned subflow.

    A Flow version pins its whole transitive set of resource versions, so a
    judge cast by a subflow is cast by the parent too. A guard that stopped at
    the parent's own nodes would name a guarantee it does not honour, which is
    worse than naming none.
    """

    def agent_node(self, version_id: str, node_id: str = "analyst") -> dict[str, object]:
        return {
            "id": node_id,
            "type": "agent",
            "version_id": version_id,
            "input_mapping": {"brief": {"source": "input", "path": "value"}},
            "settings": dict(NO_RETRY),
        }

    def flow_node(self, version_id: str, node_id: str = "child") -> dict[str, object]:
        return {
            "id": node_id,
            "type": "flow",
            "version_id": version_id,
            "input_mapping": dict(VALUE_MAPPING),
            "settings": dict(NO_RETRY),
        }

    def child_casting(self, slug: str, node: dict[str, object]) -> str:
        child = self.publish(slug, [node], output_schema=TEXT_OUTPUT)
        return str(child["version"]["id"])

    def test_a_judge_cast_by_a_pinned_subflows_agent_node_is_refused(self) -> None:
        analyst = self.agents["launch-analyst"]["id"]
        child = self.child_casting("child-agent-cast", self.agent_node(analyst))
        with self.assertRaises(ContractViolation) as caught:
            self.publish(
                "parent-agent-cast",
                [self.flow_node(child)],
                acceptance_criteria=[criterion("work-happened", "step", "child")],
                judge_agent_version_id=analyst,
            )
        self.assertIn("judge", str(caught.exception).lower())

    def test_a_judge_cast_by_an_ai_action_inside_a_pinned_subflow_is_refused(self) -> None:
        ai_action = self.seeded_actions["ai-launch-analysis"]
        child = self.child_casting(
            "child-action-cast",
            {
                "id": "analysis",
                "type": "action",
                "version_id": ai_action["id"],
                "input_mapping": {"brief": {"source": "input", "path": "value"}},
                "settings": dict(NO_RETRY),
            },
        )
        with self.assertRaises(ContractViolation):
            self.publish(
                "parent-action-cast",
                [self.flow_node(child)],
                acceptance_criteria=[criterion("work-happened", "step", "child")],
                judge_agent_version_id=ai_action["agent_version_id"],
            )

    def test_the_walk_reaches_a_judge_cast_two_levels_down(self) -> None:
        analyst = self.agents["launch-analyst"]["id"]
        grandchild = self.child_casting("grandchild-cast", self.agent_node(analyst))
        child = self.child_casting("child-wrapper", self.flow_node(grandchild))
        with self.assertRaises(ContractViolation):
            self.publish(
                "parent-two-levels",
                [self.flow_node(child)],
                acceptance_criteria=[criterion("work-happened", "step", "child")],
                judge_agent_version_id=analyst,
            )

    def test_a_judge_no_pinned_subflow_casts_still_publishes(self) -> None:
        child = self.child_casting(
            "child-independent", self.agent_node(self.agents["launch-analyst"]["id"])
        )
        flow = self.publish(
            "parent-independent",
            [self.flow_node(child)],
            acceptance_criteria=[criterion("work-happened", "step", "child")],
            judge_agent_version_id=self.judge,
        )
        self.assertEqual(flow["version"]["judge_agent_version_id"], self.judge)


class JudgeCastingRequirementTest(AcceptanceContractHarness):
    def test_a_declared_contract_without_a_judge_is_refused(self) -> None:
        with self.assertRaises(ContractViolation):
            self.publish(
                "judgeless",
                [self.node("writer", "writer")],
                acceptance_criteria=[criterion("record-written", "effect", "writer")],
            )

    def test_a_judge_without_a_contract_is_refused(self) -> None:
        with self.assertRaises(ContractViolation):
            self.publish(
                "contractless-judge",
                [self.node("writer", "writer")],
                judge_agent_version_id=self.judge,
            )

    def test_the_judge_must_be_an_agent_version_of_this_workspace(self) -> None:
        with self.assertRaises(ContractViolation):
            self.publish(
                "foreign-judge",
                [self.node("writer", "writer")],
                acceptance_criteria=[criterion("record-written", "effect", "writer")],
                judge_agent_version_id="agtv_00000000000000000000000000000000",
            )


if __name__ == "__main__":
    unittest.main()
