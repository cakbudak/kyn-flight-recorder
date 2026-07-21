from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.contracts import Conflict, ContractViolation, NotFound, verify_event_chain
from backend.service import ControlPlane
from backend.store import Store


SUCCESS_ERROR = [
    {"id": "success", "label": "Success", "description": "", "tone": "success"},
    {"id": "error", "label": "Error", "description": "", "tone": "danger"},
]


class NoModelClient:
    def create(self, payload: dict[str, object]) -> dict[str, object]:
        del payload
        raise AssertionError("context contract tests must not call a model")


class MemoryDistillerClient:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.requests: list[dict[str, object]] = []

    def create(self, payload: dict[str, object]) -> dict[str, object]:
        if self.store.in_write_transaction():
            raise AssertionError("Memory distillation called a model under a write transaction")
        self.requests.append(json.loads(json.dumps(payload)))
        evidence = json.loads(payload["input"][0]["content"])
        event_id = evidence["events"][0]["id"]
        candidate = {
            "title": "Retain explicit approval",
            "content": "A public write remains gated by an explicit human decision.",
            "rationale": "The supplied completed Run records that governed boundary.",
            "tags": ["approval", "governance"],
            "evidence_event_ids": [event_id],
        }
        return {
            "id": "resp_memory_distillation",
            "status": "completed",
            "model": "gpt-5.6",
            "usage": {"input_tokens": 120, "output_tokens": 50, "total_tokens": 170},
            "output": [
                {
                    "id": "msg_memory_distillation",
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(candidate),
                            "annotations": [],
                        }
                    ],
                }
            ],
        }


def string_property(*, maximum: int = 20_000) -> dict[str, object]:
    return {"type": "string", "maxLength": maximum}


def citation_schema() -> dict[str, object]:
    properties = {
        "source_id": string_property(),
        "source_version_id": string_property(),
        "source_version": {"type": "integer"},
        "source_name": string_property(),
        "filename": string_property(),
        "fingerprint": string_property(),
        "line_start": {"type": "integer"},
        "line_end": {"type": "integer"},
        "label": string_property(),
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def smart_read_output_schema(*, headings: bool = False) -> dict[str, object]:
    source_properties = {
        "id": string_property(),
        "version_id": string_property(),
        "version": {"type": "integer"},
        "name": string_property(),
        "filename": string_property(),
        "media_type": string_property(),
        "fingerprint": string_property(),
        "line_count": {"type": "integer"},
        "byte_count": {"type": "integer"},
    }
    passage_properties = {
        "text": string_property(),
        "citation": citation_schema(),
    }
    properties: dict[str, object] = {
        "mode": string_property(),
        "source": {
            "type": "object",
            "properties": source_properties,
            "required": list(source_properties),
            "additionalProperties": False,
        },
        "passages": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": passage_properties,
                "required": list(passage_properties),
                "additionalProperties": False,
            },
            "maxItems": 100,
        },
        "result_fingerprint": string_property(),
    }
    if headings:
        properties["headings"] = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": passage_properties,
                "required": list(passage_properties),
                "additionalProperties": False,
            },
            "maxItems": 100,
        }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


class ContextLayerContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.database = Path(self.temporary.name) / "context.sqlite3"
        self.store = Store(self.database)
        self.store.initialize()
        self.plane = ControlPlane(self.store, NoModelClient())
        self.workspace_id = self.plane.create_workspace(seed=False)["workspace_id"]

    def _source(self) -> dict[str, object]:
        return self.plane.create_knowledge_source(
            self.workspace_id,
            name="Launch evidence",
            slug="launch-evidence",
            description="The bounded source used by the product council.",
            filename="launch-brief.md",
            media_type="text/markdown",
            content=(
                "# Launch brief\n\n"
                "## Goal\nShip the context-to-decision loop with cited evidence.\n\n"
                "## Risk\nA model may not promote its own memory.\n\n"
                "## Decision\nEvery write needs an explicit human gate."
            ),
            created_by="reviewer@example.test",
        )

    def _completed_run(self) -> dict[str, object]:
        input_schema = {
            "type": "object",
            "properties": {"brief": string_property()},
            "required": ["brief"],
            "additionalProperties": False,
        }
        output_schema = {
            "type": "object",
            "properties": {"text": string_property()},
            "required": ["text"],
            "additionalProperties": False,
        }
        action = self.plane.create_action(
            self.workspace_id,
            name="Capture decision",
            slug="capture-decision",
            description="Produces one deterministic, evidence-bearing decision.",
            kind="template",
            input_schema=input_schema,
            output_schema=output_schema,
            outcomes=SUCCESS_ERROR,
            config={"template": "Decision: {{brief}}"},
            agent_version_id=None,
        )
        flow = self.plane.create_studio_flow(
            self.workspace_id,
            name="Decision evidence",
            slug="decision-evidence",
            description="Produces a completed source Run for governed Memory.",
            input_schema=input_schema,
            output_schema=output_schema,
            outcomes=SUCCESS_ERROR,
            start_node_id="capture",
            nodes=[
                {
                    "id": "capture",
                    "type": "action",
                    "version_id": action["version"]["id"],
                    "input_mapping": {
                        "brief": {"source": "input", "path": "brief"}
                    },
                }
            ],
            routes=[],
        )
        return self.plane.start_studio_run(
            self.workspace_id,
            flow["id"],
            input_data={"brief": "human approval remains mandatory"},
        )

    def test_source_versions_are_immutable_and_every_read_is_cited(self) -> None:
        source = self._source()
        version = source["version"]
        glance = self.plane.smart_read(
            self.workspace_id,
            source_version_id=version["id"],
            mode="glance",
        )
        self.assertEqual(glance["source"]["fingerprint"], version["fingerprint"])
        self.assertTrue(glance["passages"])
        self.assertTrue(glance["headings"])
        for item in [*glance["passages"], *glance["headings"]]:
            self.assertEqual(item["citation"]["source_version_id"], version["id"])
            self.assertGreaterEqual(item["citation"]["line_start"], 1)

        grep = self.plane.smart_read(
            self.workspace_id,
            source_version_id=version["id"],
            mode="grep",
            query="human gate",
            max_results=4,
        )
        self.assertEqual(len(grep["passages"]), 1)
        self.assertIn("human gate", grep["passages"][0]["text"])
        search = self.plane.search_knowledge(
            self.workspace_id, query="cited evidence", max_results=5
        )
        self.assertTrue(search["results"])
        self.assertEqual(
            search["results"][0]["citation"]["source_version_id"], version["id"]
        )

        revised = self.plane.revise_knowledge_source(
            self.workspace_id,
            source["id"],
            expected_version=1,
            name=source["name"],
            description=source["description"],
            filename="launch-brief-v2.md",
            media_type="text/markdown",
            content="# Launch brief\n\nThe second immutable version.",
            created_by="reviewer@example.test",
        )
        self.assertEqual(revised["current_version"], 2)
        self.assertEqual(len(revised["versions"]), 2)
        old = self.plane.smart_read(
            self.workspace_id,
            source_version_id=version["id"],
            mode="full",
        )
        self.assertIn("Every write", old["passages"][0]["text"])
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.write() as connection:
                connection.execute(
                    "UPDATE knowledge_source_versions SET content = 'rewritten' WHERE id = ?",
                    (version["id"],),
                )

    def test_smart_read_action_uses_the_normal_action_receipt_path(self) -> None:
        source = self._source()
        input_schema = {
            "type": "object",
            "properties": {"source_version_id": string_property()},
            "required": ["source_version_id"],
            "additionalProperties": False,
        }
        action = self.plane.create_action(
            self.workspace_id,
            name="SmartRead glance",
            slug="smartread-glance",
            description="Reads one imported source version with line citations.",
            kind="smart_read",
            input_schema=input_schema,
            output_schema=smart_read_output_schema(headings=True),
            outcomes=SUCCESS_ERROR,
            config={"mode": "glance"},
            agent_version_id=None,
        )
        flow = self.plane.create_studio_flow(
            self.workspace_id,
            name="Cited source glance",
            slug="cited-source-glance",
            description="Runs SmartRead through the same pinned Action motor.",
            input_schema=input_schema,
            output_schema=action["version"]["output_schema"],
            outcomes=SUCCESS_ERROR,
            start_node_id="read",
            nodes=[
                {
                    "id": "read",
                    "type": "action",
                    "version_id": action["version"]["id"],
                    "input_mapping": {
                        "source_version_id": {
                            "source": "input",
                            "path": "source_version_id",
                        }
                    },
                }
            ],
            routes=[],
        )
        run = self.plane.start_studio_run(
            self.workspace_id,
            flow["id"],
            input_data={"source_version_id": source["version"]["id"]},
        )
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["steps"][0]["target_version_id"], action["version"]["id"])
        self.assertEqual(len(run["action_receipts"]), 1)
        self.assertEqual(run["action_receipts"][0]["outcome"], "succeeded")
        self.assertTrue(verify_event_chain(run["events"]))

    def test_memory_is_quarantined_qualified_promoted_recalled_and_retired(self) -> None:
        run = self._completed_run()
        evidence_id = next(
            event["id"] for event in run["events"] if event["type"] == "step.completed"
        )
        candidate = self.plane.create_human_memory_candidate(
            self.workspace_id,
            source_run_id=run["id"],
            title="Human gate is mandatory",
            content="Public writes require an explicit human approval decision.",
            rationale="The completed source Run recorded this boundary as its decision.",
            tags=["Approval", "Governance"],
            evidence_event_ids=[evidence_id],
        )
        self.assertIsNone(candidate["qualification"])
        self.assertIsNone(candidate["decision"])
        self.assertEqual(
            self.plane.search_memories(
                self.workspace_id, query="human approval", max_results=5
            )["results"],
            [],
        )

        qualified = self.plane.qualify_memory_candidate(
            self.workspace_id, candidate["id"]
        )
        self.assertTrue(qualified["qualification"]["passed"])
        self.assertTrue(all(qualified["qualification"]["checks"].values()))
        with self.assertRaisesRegex(Conflict, "fingerprint"):
            self.plane.promote_memory_candidate(
                self.workspace_id,
                candidate["id"],
                slug="human-gate-mandatory",
                actor="maintainer@example.test",
                reason="Promote this exact evidence-bound boundary for future recall.",
                acknowledged=True,
                candidate_fingerprint="0" * 64,
            )
        memory = self.plane.promote_memory_candidate(
            self.workspace_id,
            candidate["id"],
            slug="human-gate-mandatory",
            actor="maintainer@example.test",
            reason="Promote this exact evidence-bound boundary for future recall.",
            acknowledged=True,
            candidate_fingerprint=candidate["fingerprint"],
        )
        recalled = self.plane.search_memories(
            self.workspace_id, query="explicit approval", max_results=5
        )
        self.assertEqual(recalled["results"][0]["memory_id"], memory["id"])
        self.assertEqual(
            recalled["results"][0]["provenance"]["evidence_event_ids"],
            [evidence_id],
        )
        retired = self.plane.retire_memory(
            self.workspace_id,
            memory["id"],
            actor="maintainer@example.test",
            reason="A successor policy supersedes this recalled boundary for future work.",
        )
        self.assertEqual(retired["state"], "retired")
        self.assertEqual(
            self.plane.search_memories(
                self.workspace_id, query="explicit approval", max_results=5
            )["results"],
            [],
        )

    def test_paths_cross_workspace_reads_and_unverified_sources_fail_closed(self) -> None:
        with self.assertRaisesRegex(ContractViolation, "display name"):
            self.plane.create_knowledge_source(
                self.workspace_id,
                name="Forbidden path",
                slug="forbidden-path",
                description="Must not reach the server filesystem.",
                filename="../../etc/passwd",
                media_type="text/plain",
                content="never read",
                created_by="attacker",
            )
        source = self._source()
        other_workspace = self.plane.create_workspace(seed=False)["workspace_id"]
        with self.assertRaisesRegex(NotFound, "version was not found"):
            self.plane.smart_read(
                other_workspace,
                source_version_id=source["version"]["id"],
                mode="glance",
            )

        completed = self._completed_run()
        flow_id = completed["flow_id"]
        prepared = self.plane.prepare_studio_run(
            self.workspace_id,
            flow_id,
            input_data={"brief": "not executed"},
        )
        with self.assertRaisesRegex(ContractViolation, "completed"):
            self.plane.create_human_memory_candidate(
                self.workspace_id,
                source_run_id=prepared["id"],
                title="Invalid memory",
                content="A failed Run may not become future context.",
                rationale="This should be rejected before a candidate row exists.",
                tags=["invalid"],
                evidence_event_ids=[prepared["events"][0]["id"]],
            )

    def test_model_memory_is_strict_grounded_and_still_quarantined(self) -> None:
        run = self._completed_run()
        evidence_id = next(
            event["id"] for event in run["events"] if event["type"] == "step.completed"
        )
        prompt = self.plane.create_prompt(
            self.workspace_id,
            name="Memory distiller prompt",
            slug="memory-distiller-prompt",
            template="Distil only verified evidence.",
            variables=[],
        )
        agent = self.plane.create_agent(
            self.workspace_id,
            name="Independent memory distiller",
            slug="independent-memory-distiller",
            role="executor",
            model="gpt-5.6",
            instructions="Propose one reusable observation without granting authority.",
            prompt_version_id=prompt["version"]["id"],
            skill_version_ids=[],
        )
        client = MemoryDistillerClient(self.store)
        candidate = self.plane.draft_model_memory_candidate(
            self.workspace_id,
            source_run_id=run["id"],
            distiller_agent_version_id=agent["version"]["id"],
            evidence_event_ids=[evidence_id],
            client=client,
        )
        self.assertEqual(candidate["author_kind"], "model")
        self.assertEqual(candidate["evidence_event_ids"], [evidence_id])
        self.assertIsNone(candidate["qualification"])
        self.assertIsNone(candidate["decision"])
        request = client.requests[0]
        self.assertIs(request["store"], False)
        self.assertEqual(request["tool_choice"], "none")
        self.assertIs(request["text"]["format"]["strict"], True)
        self.assertEqual(
            self.store.count_rows("memory_distillation_model_calls"), 1
        )
        self.assertEqual(
            self.plane.search_memories(
                self.workspace_id, query="explicit approval", max_results=5
            )["results"],
            [],
        )


if __name__ == "__main__":
    unittest.main()
