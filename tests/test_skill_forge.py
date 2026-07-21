from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.contracts import ContractViolation
from backend.service import ControlPlane
from backend.store import Store


class ForgeResponsesClient:
    """Provider-shaped seam for a normal Run followed by Skill distillation."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self.requests: list[dict[str, object]] = []
        self.citation_override: str | None = None

    def create(self, payload: dict[str, object]) -> dict[str, object]:
        if self.store.in_write_transaction():
            raise AssertionError("provider I/O happened inside a SQLite write transaction")
        self.requests.append(json.loads(json.dumps(payload)))
        metadata = payload.get("metadata")
        if isinstance(metadata, dict) and metadata.get("operation") == "skill_distillation":
            envelope = json.loads(payload["input"][0]["content"])
            evidence_id = envelope["source"]["evidence_ledger"][0]["id"]
            result = {
                "name": "Evidence-bounded readiness reasoning",
                "instructions": (
                    "Evaluate readiness only against explicit typed criteria in the "
                    "current request. Separate observed evidence from assumptions, "
                    "name unresolved risk, and leave authorization to the human gate."
                ),
                "rationale": (
                    "The completed source Step used an explicit readiness contract and "
                    "kept the later bounded effect behind human approval."
                ),
                "evidence_event_ids": [self.citation_override or evidence_id],
            }
        elif isinstance(metadata, dict) and metadata.get("operation") == "adjudication":
            result = {
                "assessment": "The declared ledger evidence was not produced.",
                "criteria": [
                    {
                        "criterion_id": "record-in-ledger",
                        "unevidenced": True,
                        "anchors": [],
                        "reason": "No effect at the declared ledger node exists.",
                    },
                    {
                        "criterion_id": "ledger-write-succeeded",
                        "unevidenced": True,
                        "anchors": [],
                        "reason": "No successful receipt at the declared node exists.",
                    },
                ],
            }
        else:
            result = {
                "summary": "The bounded launch is ready for human review.",
                "score": 0.91,
                "risks": ["The sandbox effect still requires human approval."],
            }
        return {
            "id": f"resp_forge_{len(self.requests)}",
            "status": "completed",
            "model": payload.get("model", "gpt-5.6"),
            "usage": {"input_tokens": 80, "output_tokens": 30, "total_tokens": 110},
            "output": [
                {
                    "id": f"msg_forge_{len(self.requests)}",
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


class CapabilityForgeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = Store(Path(self.temporary.name) / "forge.sqlite3")
        self.store.initialize()
        self.client = ForgeResponsesClient(self.store)
        self.plane = ControlPlane(self.store, self.client)
        self.bootstrap = self.plane.create_workspace(seed=True)
        self.workspace_id = self.bootstrap["workspace_id"]

        flow = self.bootstrap["snapshot"]["studio"]["flows"][0]
        waiting = self.plane.start_studio_run(
            self.workspace_id,
            flow["id"],
            input_data={
                "brief": (
                    "Launch a typed automation with explicit evidence, one human "
                    "authority gate, and a bounded sandbox effect."
                )
            },
        )
        self.run = self.plane.decide_studio_approval(
            self.workspace_id,
            waiting["pending_approval"]["id"],
            approved=True,
            actor="forge-test",
            reason="The bounded effect and evidence contract are acceptable.",
        )
        self.source_call = self.run["model_calls"][0]
        self.distiller_agent = self.bootstrap["snapshot"]["agents"][0]["version"]

    def draft(self) -> dict[str, object]:
        return self.plane.draft_skill_candidate(
            self.workspace_id,
            source_run_id=self.run["id"],
            source_model_call_id=self.source_call["id"],
            distiller_agent_version_id=self.distiller_agent["id"],
            client=self.client,
        )

    def test_candidate_is_grounded_quarantined_and_authority_free(self) -> None:
        candidate = self.draft()

        self.assertEqual(candidate["status"], "quarantined")
        self.assertEqual(candidate["source"]["run_id"], self.run["id"])
        self.assertEqual(candidate["source"]["model_call_id"], self.source_call["id"])
        self.assertEqual(candidate["authority"]["allowed_tools"], [])
        self.assertEqual(candidate["authority"]["allowed_action_version_ids"], [])
        self.assertEqual(len(candidate["fingerprint"]), 64)
        request = self.client.requests[-1]
        self.assertEqual(request["tool_choice"], "none")
        self.assertTrue(request["text"]["format"]["strict"])
        self.assertEqual(request["metadata"]["operation"], "skill_distillation")

        snapshot = self.plane.snapshot(self.workspace_id)
        self.assertEqual(snapshot["workspace"]["model_calls_used"], 2)
        self.assertEqual(snapshot["studio"]["skill_candidates"][0]["id"], candidate["id"])

    def test_qualification_proves_provenance_not_performance(self) -> None:
        candidate = self.draft()
        qualified = self.plane.qualify_skill_candidate(
            self.workspace_id, candidate["id"]
        )

        self.assertEqual(qualified["status"], "qualified")
        self.assertTrue(qualified["qualification"]["passed"])
        check_ids = {check["id"] for check in qualified["qualification"]["checks"]}
        self.assertEqual(
            check_ids,
            {
                "terminal-source",
                "ledger-chain",
                "model-step",
                "source-snapshot",
                "bounded-citations",
                "candidate-fingerprint",
                "independent-distiller",
                "zero-authority-delta",
            },
        )
        repeated = self.plane.qualify_skill_candidate(
            self.workspace_id, candidate["id"]
        )
        self.assertEqual(
            repeated["qualification"]["id"], qualified["qualification"]["id"]
        )

    def test_human_promotion_creates_skill_without_changing_any_agent(self) -> None:
        candidate = self.draft()
        self.plane.qualify_skill_candidate(self.workspace_id, candidate["id"])
        before = self.plane.snapshot(self.workspace_id)
        promoted = self.plane.promote_skill_candidate(
            self.workspace_id,
            candidate["id"],
            name="Evidence-bounded readiness reasoning",
            slug="evidence-bounded-readiness-reasoning",
            actor="capability-owner",
            reason="The provenance gates passed and the narrow instruction is reusable.",
            acknowledged=True,
        )
        after = self.plane.snapshot(self.workspace_id)

        self.assertEqual(promoted["status"], "promoted")
        self.assertIsNotNone(promoted["promoted_skill"])
        skill = next(
            item
            for item in after["skills"]
            if item["id"] == promoted["promoted_skill"]["skill_id"]
        )
        self.assertEqual(skill["version"]["instructions"], candidate["instructions"])
        self.assertEqual(skill["version"]["allowed_tools"], [])
        self.assertEqual(skill["version"]["allowed_action_version_ids"], [])
        self.assertEqual(
            [(agent["id"], agent["current_version"]) for agent in after["agents"]],
            [(agent["id"], agent["current_version"]) for agent in before["agents"]],
        )

    def test_promotion_before_qualification_is_refused(self) -> None:
        candidate = self.draft()
        with self.assertRaisesRegex(ContractViolation, "must pass provenance"):
            self.plane.promote_skill_candidate(
                self.workspace_id,
                candidate["id"],
                name="Premature Skill",
                slug="premature-skill",
                actor="operator",
                reason="This should be refused before the qualification exists.",
                acknowledged=True,
            )

    def test_candidate_tables_are_append_only(self) -> None:
        candidate = self.draft()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
            with self.store.write() as connection:
                connection.execute(
                    "UPDATE skill_candidates SET name = 'rewritten' WHERE id = ?",
                    (candidate["id"],),
                )

    def test_failed_run_cannot_become_a_source(self) -> None:
        contracted = next(
            flow
            for flow in self.plane.snapshot(self.workspace_id)["studio"]["flows"]
            if flow["slug"] == "contracted-evidence-publication"
        )
        failed = self.plane.start_studio_run(
            self.workspace_id,
            contracted["id"],
            input_data={"record": "Not ready", "readiness": 0.2},
        )
        self.assertEqual(failed["status"], "failed")
        call = failed["model_calls"][0]
        with self.assertRaisesRegex(ContractViolation, "completed source Run"):
            self.plane.draft_skill_candidate(
                self.workspace_id,
                source_run_id=failed["id"],
                source_model_call_id=call["id"],
                distiller_agent_version_id=self.distiller_agent["id"],
                client=self.client,
            )

    def test_successor_version_of_source_agent_is_not_independent(self) -> None:
        source_agent = next(
            agent
            for agent in self.plane.snapshot(self.workspace_id)["agents"]
            if any(
                version["id"] == self.source_call["agent_version_id"]
                for version in agent["versions"]
            )
        )
        current = source_agent["version"]
        successor = self.plane.revise_agent(
            self.workspace_id,
            source_agent["id"],
            expected_version=current["version"],
            name=source_agent["name"],
            role=current["role"],
            model=current["model"],
            instructions=f"{current['instructions']}\nRemain independent in name only.",
            prompt_version_id=current["prompt_version_id"],
            skill_version_ids=current["skill_version_ids"],
        )

        before_calls = len(self.client.requests)
        with self.assertRaisesRegex(ContractViolation, "must be independent"):
            self.plane.draft_skill_candidate(
                self.workspace_id,
                source_run_id=self.run["id"],
                source_model_call_id=self.source_call["id"],
                distiller_agent_version_id=successor["version"]["id"],
                client=self.client,
            )
        self.assertEqual(len(self.client.requests), before_calls)

    def test_candidate_cannot_cite_event_outside_source_envelope(self) -> None:
        self.client.citation_override = "evt_not_in_the_source_envelope"
        with self.assertRaisesRegex(ContractViolation, "outside its source envelope"):
            self.draft()

        snapshot = self.plane.snapshot(self.workspace_id)
        self.assertEqual(snapshot["studio"]["skill_candidates"], [])
        self.assertEqual(snapshot["workspace"]["model_calls_used"], 2)
        with self.store.read() as connection:
            receipt = connection.execute(
                "SELECT status FROM skill_distillation_model_calls"
            ).fetchone()
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt["status"], "completed")


if __name__ == "__main__":
    unittest.main()
