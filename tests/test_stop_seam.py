from __future__ import annotations

import unittest

from backend.contracts import ContractViolation
from backend.stop_seam import (
    ANCHOR_FOREIGN_RUN,
    ANCHOR_KIND_INADMISSIBLE,
    ANCHOR_NODE_MISMATCH,
    ANCHOR_NODE_UNATTRIBUTABLE,
    ANCHOR_REFUSALS,
    ANCHOR_STATE_MISMATCH,
    ANCHOR_UNRESOLVABLE,
    COMPLETION_UNEVIDENCED,
    EVIDENCE_KINDS,
    AcceptanceCriterion,
    EvidenceBundle,
    EvidenceRecord,
    adjudicate,
    resolve_anchors,
)


RUN = "arun_this"
OTHER_RUN = "arun_other"

NODE = "publish-sandbox"
OTHER_NODE = "notify-team"


def criterion(
    criterion_id: str,
    evidence_kind: str,
    node_ids: tuple[str, ...] = (NODE,),
) -> AcceptanceCriterion:
    return AcceptanceCriterion(
        id=criterion_id,
        statement=f"the Run must produce {evidence_kind} evidence",
        evidence_kind=evidence_kind,
        node_ids=node_ids,
    )


def bundle(**overrides: object) -> EvidenceBundle:
    """A Run carrying one admissible record of every declared kind.

    Every test that wants a refusal starts from evidence that would otherwise
    admit, so the refusal is attributable to the one thing the test changed.
    """

    defaults: dict[str, object] = {
        "run_id": RUN,
        "effects": (EvidenceRecord(id="aeff_ok", run_id=RUN, node_id=NODE),),
        "receipts": (
            EvidenceRecord(id="arcpt_ok", run_id=RUN, node_id=NODE, state="succeeded"),
        ),
        "approvals": (
            EvidenceRecord(id="adec_ok", run_id=RUN, node_id=NODE, state=True),
        ),
        "steps": (
            EvidenceRecord(id="astep_ok", run_id=RUN, node_id=NODE, state="completed"),
        ),
    }
    defaults.update(overrides)
    return EvidenceBundle(**defaults)  # type: ignore[arg-type]


def refusals_for(resolution) -> list[str]:
    return [item.refusal for item in resolution.discarded]


class StopSeamVocabularyTest(unittest.TestCase):
    """The vocabulary is closed, and the closure has to be visible.

    A judge whose approval is worthless on its own is only worth anything if
    the grounds for discarding it are published rather than improvised.
    """

    def test_exactly_the_four_declared_evidence_kinds_exist(self) -> None:
        names = {kind.name for kind in EVIDENCE_KINDS}
        self.assertEqual(names, {"effect", "receipt", "approval", "step"})

    def test_every_kind_declares_a_collection_and_a_rationale(self) -> None:
        for kind in EVIDENCE_KINDS:
            self.assertTrue(kind.collection, kind.name)
            self.assertTrue(kind.rationale.strip(), kind.name)
            self.assertGreater(len(kind.rationale), 30, kind.name)

    def test_only_an_effect_is_admissible_in_any_state(self) -> None:
        """An effect exists only because it happened; the rest can lie."""

        stateless = {kind.name for kind in EVIDENCE_KINDS if not kind.admissible_states}
        self.assertEqual(stateless, {"effect"})

    def test_an_approval_recorded_as_an_integer_is_not_an_approval(self) -> None:
        """Python makes `True == 1`. A projection bug must not read as consent."""

        approval = next(kind for kind in EVIDENCE_KINDS if kind.name == "approval")
        self.assertTrue(approval.admits_state(True))
        self.assertFalse(approval.admits_state(1))
        self.assertFalse(approval.admits_state("true"))

    def test_every_refusal_code_carries_a_named_reason(self) -> None:
        for code in (
            ANCHOR_UNRESOLVABLE,
            ANCHOR_FOREIGN_RUN,
            ANCHOR_KIND_INADMISSIBLE,
            ANCHOR_NODE_MISMATCH,
            ANCHOR_NODE_UNATTRIBUTABLE,
            ANCHOR_STATE_MISMATCH,
        ):
            self.assertIn(code, ANCHOR_REFUSALS)
        for code, reason in ANCHOR_REFUSALS.items():
            self.assertTrue(reason.strip(), code)
            self.assertGreater(len(reason), 30, code)


class AnchorResolutionTest(unittest.TestCase):
    """Each refusal code proved independently, from evidence that would admit."""

    # -- the five refusals -------------------------------------------------

    def test_a_fabricated_anchor_id_is_unresolvable(self) -> None:
        """A judge that invents an evidence id has produced text, not evidence."""

        resolved = resolve_anchors(
            [criterion("c1", "effect")],
            {"c1": ["aeff_invented"]},
            bundle(),
        )
        self.assertEqual(refusals_for(resolved[0]), [ANCHOR_UNRESOLVABLE])
        self.assertEqual(resolved[0].surviving, ())
        self.assertFalse(resolved[0].holds)

    def test_an_anchor_from_another_run_is_refused(self) -> None:
        borrowed = bundle(
            effects=(EvidenceRecord(id="aeff_borrowed", run_id=OTHER_RUN, node_id=NODE),)
        )
        resolved = resolve_anchors(
            [criterion("c1", "effect")],
            {"c1": ["aeff_borrowed"]},
            borrowed,
        )
        self.assertEqual(refusals_for(resolved[0]), [ANCHOR_FOREIGN_RUN])

    def test_an_anchor_of_the_wrong_kind_is_inadmissible(self) -> None:
        """A completed step is real, run-owned, and answers a different question."""

        resolved = resolve_anchors(
            [criterion("c1", "effect")],
            {"c1": ["astep_ok"]},
            bundle(),
        )
        self.assertEqual(refusals_for(resolved[0]), [ANCHOR_KIND_INADMISSIBLE])

    def test_an_anchor_at_a_node_the_criterion_never_pinned_is_refused(self) -> None:
        """Right kind, right Run, right state — and still the wrong site."""

        elsewhere = bundle(
            effects=(EvidenceRecord(id="aeff_elsewhere", run_id=RUN, node_id=OTHER_NODE),)
        )
        resolved = resolve_anchors(
            [criterion("c1", "effect", node_ids=(NODE,))],
            {"c1": ["aeff_elsewhere"]},
            elsewhere,
        )
        self.assertEqual(refusals_for(resolved[0]), [ANCHOR_NODE_MISMATCH])
        self.assertFalse(resolved[0].holds)

    def test_a_failed_step_cited_as_success_is_a_state_mismatch(self) -> None:
        failed = bundle(
            steps=(
                EvidenceRecord(id="astep_bad", run_id=RUN, node_id=NODE, state="failed"),
            )
        )
        resolved = resolve_anchors(
            [criterion("c1", "step")],
            {"c1": ["astep_bad"]},
            failed,
        )
        self.assertEqual(refusals_for(resolved[0]), [ANCHOR_STATE_MISMATCH])

    def test_a_refused_approval_cited_as_permission_is_a_state_mismatch(self) -> None:
        refused = bundle(
            approvals=(
                EvidenceRecord(id="adec_no", run_id=RUN, node_id=NODE, state=False),
            )
        )
        resolved = resolve_anchors(
            [criterion("c1", "approval")],
            {"c1": ["adec_no"]},
            refused,
        )
        self.assertEqual(refusals_for(resolved[0]), [ANCHOR_STATE_MISMATCH])

    def test_a_failed_receipt_cited_as_success_is_a_state_mismatch(self) -> None:
        failed = bundle(
            receipts=(
                EvidenceRecord(id="arcpt_bad", run_id=RUN, node_id=NODE, state="failed"),
            )
        )
        resolved = resolve_anchors(
            [criterion("c1", "receipt")],
            {"c1": ["arcpt_bad"]},
            failed,
        )
        self.assertEqual(refusals_for(resolved[0]), [ANCHOR_STATE_MISMATCH])

    def test_every_declared_refusal_code_is_reachable_by_some_input(self) -> None:
        """The table cannot rot into codes nothing can produce.

        `anchor_node_unattributable` is constructed explicitly here because
        today's schema cannot produce it — the step and request foreign keys
        are NOT NULL, so the assembler always has a node to write. That does
        not make it dead code. The resolver is a pure function over injected
        data and must be correct for input the current assembler happens never
        to produce; it is the assembler's failure mode, and the day someone
        mints an effect outside a step context it is the difference between a
        clear diagnosis and a confusing one.
        """

        cases = (
            ([criterion("c", "effect")], {"c": ["aeff_missing"]}, bundle()),
            (
                [criterion("c", "effect")],
                {"c": ["aeff_far"]},
                bundle(
                    effects=(
                        EvidenceRecord(id="aeff_far", run_id=OTHER_RUN, node_id=NODE),
                    )
                ),
            ),
            ([criterion("c", "effect")], {"c": ["astep_ok"]}, bundle()),
            (
                [criterion("c", "effect", node_ids=(NODE,))],
                {"c": ["aeff_elsewhere"]},
                bundle(
                    effects=(
                        EvidenceRecord(
                            id="aeff_elsewhere", run_id=RUN, node_id=OTHER_NODE
                        ),
                    )
                ),
            ),
            (
                [criterion("c", "effect", node_ids=(NODE,))],
                {"c": ["aeff_orphan"]},
                bundle(
                    effects=(
                        EvidenceRecord(id="aeff_orphan", run_id=RUN, node_id=None),
                    )
                ),
            ),
            (
                [criterion("c", "step")],
                {"c": ["astep_bad"]},
                bundle(
                    steps=(
                        EvidenceRecord(
                            id="astep_bad", run_id=RUN, node_id=NODE, state="failed"
                        ),
                    )
                ),
            ),
        )
        observed = set()
        for criteria, claimed, evidence in cases:
            for resolution in resolve_anchors(criteria, claimed, evidence):
                observed.update(refusals_for(resolution))
        self.assertEqual(observed, set(ANCHOR_REFUSALS))

    def test_the_site_check_precedes_the_state_check(self) -> None:
        """A record at the wrong site is a site problem, whatever its state."""

        wrong_both = bundle(
            steps=(
                EvidenceRecord(
                    id="astep_wrong", run_id=RUN, node_id=OTHER_NODE, state="failed"
                ),
            )
        )
        resolved = resolve_anchors(
            [criterion("c1", "step", node_ids=(NODE,))],
            {"c1": ["astep_wrong"]},
            wrong_both,
        )
        self.assertEqual(refusals_for(resolved[0]), [ANCHOR_NODE_MISMATCH])

    # -- pinned sites ------------------------------------------------------

    def test_an_effect_criterion_is_not_satisfied_by_an_effect_at_another_node(
        self,
    ) -> None:
        """Regression: a kind-only criterion was satisfied by any effect at all.

        `effect` is the one kind admissible in any state, so before criteria
        pinned their sites this criterion asked only whether the Run had
        written *something*. A Flow with any writing node satisfied every
        effect criterion it could declare, and the judge did not need to be
        honest — only to point at an effect. Pinning the site closes that.
        """

        criteria = [criterion("c1", "effect", node_ids=("node-a",))]
        elsewhere = bundle(
            effects=(EvidenceRecord(id="aeff_at_b", run_id=RUN, node_id="node-b"),)
        )
        decision = adjudicate(criteria, {"c1": ["aeff_at_b"]}, elsewhere)
        self.assertFalse(decision.admitted)
        self.assertEqual(decision.error_code, COMPLETION_UNEVIDENCED)
        self.assertEqual(decision.unevidenced, ("c1",))
        self.assertEqual(refusals_for(decision.resolutions[0]), [ANCHOR_NODE_MISMATCH])

        # The same effect minted at the pinned node satisfies it.
        at_a = bundle(
            effects=(EvidenceRecord(id="aeff_at_a", run_id=RUN, node_id="node-a"),)
        )
        self.assertTrue(adjudicate(criteria, {"c1": ["aeff_at_a"]}, at_a).admitted)

    def test_a_criterion_naming_two_nodes_is_satisfied_by_either(self) -> None:
        """A Flow that branches must not suffer a structural false refusal.

        Either branch may legitimately do the work, so pinning a single node
        would refuse a Run that genuinely satisfied the contract via the other.
        """

        criteria = [criterion("c1", "effect", node_ids=("node-a", "node-b"))]
        for node_id in ("node-a", "node-b"):
            with self.subTest(node_id=node_id):
                evidence = bundle(
                    effects=(
                        EvidenceRecord(id="aeff_branch", run_id=RUN, node_id=node_id),
                    )
                )
                self.assertTrue(
                    adjudicate(criteria, {"c1": ["aeff_branch"]}, evidence).admitted
                )

        # A third node neither branch names still refuses.
        elsewhere = bundle(
            effects=(EvidenceRecord(id="aeff_branch", run_id=RUN, node_id="node-c"),)
        )
        self.assertFalse(
            adjudicate(criteria, {"c1": ["aeff_branch"]}, elsewhere).admitted
        )

    def test_a_record_with_no_attributable_node_can_satisfy_nothing(self) -> None:
        """An assembler that could not attribute a record must not admit it.

        `effect` admits every state, so an unattributed record that fell
        through to the state check would be admitted on the *absence* of a
        site — the same defect a kind-only criterion had, wearing a different
        hat. Attribution is therefore checked before state, not after.
        """

        unattributed = bundle(
            effects=(EvidenceRecord(id="aeff_orphan", run_id=RUN, node_id=None),)
        )
        resolved = resolve_anchors(
            [criterion("c1", "effect")],
            {"c1": ["aeff_orphan"]},
            unattributed,
        )
        self.assertEqual(refusals_for(resolved[0]), [ANCHOR_NODE_UNATTRIBUTABLE])
        self.assertFalse(resolved[0].holds)

    def test_an_unattributable_record_is_not_reported_as_a_wrong_site(self) -> None:
        """The two node refusals blame different parties and must stay apart.

        A wrong site is the judge pointing somewhere it should not have. An
        unattributable record is this system failing to attribute evidence at
        all. Reporting the second as the first blames a model for a runtime
        defect, in a product whose entire claim is that evidence must be
        attributed correctly.
        """

        elsewhere = bundle(
            effects=(EvidenceRecord(id="aeff_x", run_id=RUN, node_id=OTHER_NODE),)
        )
        orphan = bundle(
            effects=(EvidenceRecord(id="aeff_x", run_id=RUN, node_id=None),)
        )
        criteria = [criterion("c1", "effect", node_ids=(NODE,))]
        claim = {"c1": ["aeff_x"]}

        wrong_site = resolve_anchors(criteria, claim, elsewhere)[0]
        unattributed = resolve_anchors(criteria, claim, orphan)[0]

        self.assertEqual(refusals_for(wrong_site), [ANCHOR_NODE_MISMATCH])
        self.assertEqual(refusals_for(unattributed), [ANCHOR_NODE_UNATTRIBUTABLE])
        # Both refuse. Only the name and the message differ.
        self.assertFalse(wrong_site.holds)
        self.assertFalse(unattributed.holds)
        self.assertNotEqual(
            wrong_site.discarded[0].reason, unattributed.discarded[0].reason
        )

    def test_a_criterion_that_pins_no_node_can_never_hold(self) -> None:
        """`node_ids` is never empty. An empty one refuses rather than admits."""

        resolved = resolve_anchors(
            [criterion("c1", "effect", node_ids=())],
            {"c1": ["aeff_ok"]},
            bundle(),
        )
        self.assertEqual(refusals_for(resolved[0]), [ANCHOR_NODE_MISMATCH])
        self.assertFalse(resolved[0].holds)

    def test_a_criterion_pinning_a_bare_string_pins_no_node(self) -> None:
        """Iterating a string would pin one node per character."""

        resolved = resolve_anchors(
            [
                {
                    "id": "c1",
                    "statement": "an effect was written",
                    "evidence_kind": "effect",
                    "node_ids": NODE,
                }
            ],
            {"c1": ["aeff_ok"]},
            bundle(),
        )
        self.assertEqual(refusals_for(resolved[0]), [ANCHOR_NODE_MISMATCH])

    def test_a_resolution_reports_the_sites_its_criterion_pinned(self) -> None:
        """A refusal that hides where the work should have happened is unusable."""

        resolved = resolve_anchors(
            [criterion("c1", "effect", node_ids=("node-a", "node-b"))],
            {"c1": []},
            bundle(),
        )
        self.assertEqual(resolved[0].node_ids, ("node-a", "node-b"))

    # -- narrowing --------------------------------------------------------

    def test_some_anchors_survive_while_others_are_refused(self) -> None:
        """Resolution is per anchor. One bad citation does not poison a good one."""

        evidence = bundle(
            effects=(
                EvidenceRecord(id="aeff_ok", run_id=RUN, node_id=NODE),
                EvidenceRecord(id="aeff_second", run_id=RUN, node_id=NODE),
                EvidenceRecord(id="aeff_far", run_id=OTHER_RUN, node_id=NODE),
                EvidenceRecord(id="aeff_elsewhere", run_id=RUN, node_id=OTHER_NODE),
            )
        )
        resolved = resolve_anchors(
            [criterion("c1", "effect")],
            {
                "c1": [
                    "aeff_ok",
                    "aeff_far",
                    "aeff_invented",
                    "aeff_elsewhere",
                    "aeff_second",
                ]
            },
            evidence,
        )
        self.assertEqual(resolved[0].surviving, ("aeff_ok", "aeff_second"))
        self.assertEqual(
            [(item.anchor_id, item.refusal) for item in resolved[0].discarded],
            [
                ("aeff_far", ANCHOR_FOREIGN_RUN),
                ("aeff_invented", ANCHOR_UNRESOLVABLE),
                ("aeff_elsewhere", ANCHOR_NODE_MISMATCH),
            ],
        )
        self.assertTrue(resolved[0].holds)

    def test_every_surviving_anchor_was_claimed(self) -> None:
        """The resolver may only remove. It may never add, infer, or repair."""

        claimed = ["aeff_ok", "astep_ok", "aeff_invented"]
        resolved = resolve_anchors(
            [criterion("c1", "effect")], {"c1": claimed}, bundle()
        )
        self.assertTrue(set(resolved[0].surviving) <= set(claimed))
        accounted = set(resolved[0].surviving) | {
            item.anchor_id for item in resolved[0].discarded
        }
        self.assertEqual(accounted, set(claimed))

    def test_every_discarded_anchor_carries_its_prose_reason(self) -> None:
        """The discarded set is the product, not a dropped element."""

        resolved = resolve_anchors(
            [criterion("c1", "effect")], {"c1": ["aeff_invented"]}, bundle()
        )
        discarded = resolved[0].discarded[0]
        self.assertEqual(discarded.anchor_id, "aeff_invented")
        self.assertEqual(discarded.reason, ANCHOR_REFUSALS[ANCHOR_UNRESOLVABLE])

    # -- fail closed ------------------------------------------------------

    def test_a_criterion_declaring_an_unknown_kind_admits_nothing(self) -> None:
        """A fifth kind cannot be smuggled in by declaring one."""

        resolved = resolve_anchors(
            [criterion("c1", "telemetry")],
            {"c1": ["aeff_ok"]},
            bundle(),
        )
        self.assertEqual(refusals_for(resolved[0]), [ANCHOR_KIND_INADMISSIBLE])
        self.assertFalse(resolved[0].holds)

    def test_a_criterion_with_no_claimed_anchors_does_not_hold(self) -> None:
        resolved = resolve_anchors([criterion("c1", "effect")], {}, bundle())
        self.assertEqual(resolved[0].surviving, ())
        self.assertEqual(resolved[0].discarded, ())
        self.assertFalse(resolved[0].holds)

    def test_an_empty_anchor_list_does_not_hold(self) -> None:
        resolved = resolve_anchors([criterion("c1", "effect")], {"c1": []}, bundle())
        self.assertFalse(resolved[0].holds)

    def test_a_malformed_claim_refuses_rather_than_admitting(self) -> None:
        """Absence of a well-formed check is never grounds for admission."""

        for claim in (None, [], "aeff_ok", {"c1": "aeff_ok"}, {"c1": None}, {"c1": 7}):
            with self.subTest(claim=claim):
                decision = adjudicate([criterion("c1", "effect")], claim, bundle())
                self.assertFalse(decision.admitted)
                self.assertEqual(decision.error_code, COMPLETION_UNEVIDENCED)

    def test_a_bare_string_claim_is_not_iterated_into_anchors(self) -> None:
        """Iterating a string would manufacture one anchor per character."""

        resolved = resolve_anchors(
            [criterion("c1", "effect")], {"c1": "aeff_ok"}, bundle()
        )
        self.assertEqual(resolved[0].surviving, ())
        self.assertEqual(resolved[0].discarded, ())

    def test_a_non_string_anchor_is_refused_rather_than_dropped(self) -> None:
        resolved = resolve_anchors(
            [criterion("c1", "effect")], {"c1": [None, 42, ""]}, bundle()
        )
        self.assertEqual(resolved[0].surviving, ())
        self.assertEqual(refusals_for(resolved[0]), [ANCHOR_UNRESOLVABLE] * 3)

    def test_a_criterion_with_no_id_can_never_hold(self) -> None:
        resolved = resolve_anchors(
            [
                AcceptanceCriterion(
                    id="", statement="", evidence_kind="effect", node_ids=(NODE,)
                )
            ],
            {"": ["aeff_ok"]},
            bundle(),
        )
        self.assertFalse(resolved[0].holds)

    def test_a_repeated_anchor_id_is_resolved_once(self) -> None:
        resolved = resolve_anchors(
            [criterion("c1", "effect")],
            {"c1": ["aeff_ok", "aeff_ok"]},
            bundle(),
        )
        self.assertEqual(resolved[0].surviving, ("aeff_ok",))

    def test_an_ambiguous_evidence_bundle_is_a_contract_violation(self) -> None:
        """One id in two collections would let kind be chosen after the fact."""

        ambiguous = bundle(
            effects=(EvidenceRecord(id="shared", run_id=RUN, node_id=NODE),),
            steps=(
                EvidenceRecord(id="shared", run_id=RUN, node_id=NODE, state="completed"),
            ),
        )
        with self.assertRaises(ContractViolation):
            resolve_anchors([criterion("c1", "effect")], {"c1": ["shared"]}, ambiguous)

    def test_a_criterion_declared_as_a_plain_mapping_resolves(self) -> None:
        """Criteria arrive normalized off the Flow version, as plain data."""

        resolved = resolve_anchors(
            [
                {
                    "id": "c1",
                    "statement": "an effect was written",
                    "evidence_kind": "effect",
                    "node_ids": [NODE],
                }
            ],
            {"c1": ["aeff_ok"]},
            bundle(),
        )
        self.assertTrue(resolved[0].holds)
        self.assertEqual(resolved[0].criterion_id, "c1")
        self.assertEqual(resolved[0].node_ids, (NODE,))


class AdjudicationTest(unittest.TestCase):
    """Admission is unanimous across declared criteria, or it is refused."""

    def test_zero_criteria_admits_trivially(self) -> None:
        """The default is inert: no criteria means no behaviour change."""

        decision = adjudicate([], {}, bundle())
        self.assertTrue(decision.admitted)
        self.assertEqual(decision.resolutions, ())
        self.assertEqual(decision.unevidenced, ())
        self.assertIsNone(decision.error_code)

    def test_every_criterion_anchored_admits_the_completion(self) -> None:
        decision = adjudicate(
            [criterion("c1", "effect"), criterion("c2", "approval")],
            {"c1": ["aeff_ok"], "c2": ["adec_ok"]},
            bundle(),
        )
        self.assertTrue(decision.admitted)
        self.assertEqual(decision.unevidenced, ())
        self.assertIsNone(decision.error_code)

    def test_one_unevidenced_criterion_refuses_the_whole_completion(self) -> None:
        decision = adjudicate(
            [criterion("c1", "effect"), criterion("c2", "approval")],
            {"c1": ["aeff_ok"], "c2": ["adec_invented"]},
            bundle(),
        )
        self.assertFalse(decision.admitted)
        self.assertEqual(decision.unevidenced, ("c2",))
        self.assertEqual(decision.error_code, COMPLETION_UNEVIDENCED)

    def test_a_refused_adjudication_still_reports_what_did_survive(self) -> None:
        """A refusal that hides the passing criteria is not a usable refusal."""

        decision = adjudicate(
            [criterion("c1", "effect"), criterion("c2", "receipt")],
            {"c1": ["aeff_ok"], "c2": ["arcpt_invented"]},
            bundle(),
        )
        by_id = {item.criterion_id: item for item in decision.resolutions}
        self.assertEqual(by_id["c1"].surviving, ("aeff_ok",))
        self.assertEqual(refusals_for(by_id["c2"]), [ANCHOR_UNRESOLVABLE])

    def test_a_judge_that_anchors_nothing_cannot_admit(self) -> None:
        decision = adjudicate([criterion("c1", "effect")], {"c1": []}, bundle())
        self.assertFalse(decision.admitted)
        self.assertEqual(decision.unevidenced, ("c1",))

    def test_two_criteria_pinned_to_different_nodes_are_judged_separately(self) -> None:
        """Each criterion is answered by work at its own site, not by any work."""

        evidence = bundle(
            effects=(EvidenceRecord(id="aeff_a", run_id=RUN, node_id="node-a"),),
            receipts=(
                EvidenceRecord(
                    id="arcpt_b", run_id=RUN, node_id="node-b", state="succeeded"
                ),
            ),
        )
        decision = adjudicate(
            [
                criterion("c1", "effect", node_ids=("node-a",)),
                criterion("c2", "receipt", node_ids=("node-b",)),
            ],
            {"c1": ["aeff_a"], "c2": ["arcpt_b"]},
            evidence,
        )
        self.assertTrue(decision.admitted)

        # Pinning each criterion to the other's site refuses both.
        swapped = adjudicate(
            [
                criterion("c1", "effect", node_ids=("node-b",)),
                criterion("c2", "receipt", node_ids=("node-a",)),
            ],
            {"c1": ["aeff_a"], "c2": ["arcpt_b"]},
            evidence,
        )
        self.assertFalse(swapped.admitted)
        self.assertEqual(swapped.unevidenced, ("c1", "c2"))


class EvidenceSourceCoverageTest(unittest.TestCase):
    def test_the_store_can_supply_every_kind_the_vocabulary_declares(self) -> None:
        """A kind nobody can fetch is a criterion nobody can ever satisfy.

        The vocabulary lives here and the SQL that populates it lives in the
        store, so the two can drift in the one direction no test would
        otherwise catch: adding a kind, publishing a criterion against it, and
        discovering at the stop seam that the bundle has no such collection.
        Every criterion of that kind would then refuse forever, which reads
        exactly like a judge doing its job.
        """

        from backend.studio_store import StudioStore

        self.assertEqual(
            {kind.collection for kind in EVIDENCE_KINDS},
            set(StudioStore._ADJUDICATION_SOURCES),
        )


if __name__ == "__main__":
    unittest.main()
