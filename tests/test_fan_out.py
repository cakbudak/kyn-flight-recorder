from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from backend.contracts import Conflict, ContractViolation, ProviderFailure, verify_event_chain
from backend.service import ControlPlane
from backend.store import Store


SUCCESS_ERROR = [
    {"id": "success", "label": "Success", "description": "", "tone": "success"},
    {"id": "error", "label": "Error", "description": "", "tone": "danger"},
]
COUNCIL_OUTCOMES = [
    {"id": "converged", "label": "Converged", "description": "", "tone": "success"},
    {"id": "review", "label": "Review", "description": "", "tone": "warning"},
    {"id": "error", "label": "Error", "description": "", "tone": "danger"},
]
INPUT_SCHEMA = {
    "type": "object",
    "properties": {"brief": {"type": "string", "maxLength": 4000}},
    "required": ["brief"],
    "additionalProperties": False,
}
PARTICIPANT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["commit", "challenge", "abstain"],
        },
        "analysis": {"type": "string", "maxLength": 2000},
        "recommendations": {
            "type": "array",
            "items": {"type": "string", "maxLength": 500},
            "maxItems": 8,
        },
        "risks": {
            "type": "array",
            "items": {"type": "string", "maxLength": 500},
            "maxItems": 8,
        },
        "citations": {
            "type": "array",
            "items": {"type": "string", "maxLength": 160},
            "maxItems": 12,
        },
    },
    "required": ["verdict", "analysis", "recommendations", "risks", "citations"],
    "additionalProperties": False,
}


class ConcurrentCouncilClient:
    """A provider seam that fails unless all participant calls truly overlap."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self.verdicts: dict[str, str] = {}
        self.fail_agent_id: str | None = None
        self.requests: list[dict[str, object]] = []
        self._lock = threading.Lock()
        self._barrier = threading.Barrier(3)

    def configure(
        self, verdicts: dict[str, str], *, fail_agent_id: str | None = None
    ) -> None:
        self.verdicts = dict(verdicts)
        self.fail_agent_id = fail_agent_id
        self.requests = []
        self._barrier = threading.Barrier(len(verdicts))

    def create(self, payload: dict[str, object]) -> dict[str, object]:
        if self.store.in_write_transaction():
            raise AssertionError("fan-out provider I/O happened under a write transaction")
        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            raise AssertionError("fan-out model call has no metadata")
        agent_id = str(metadata["agent_version_id"])
        with self._lock:
            self.requests.append(json.loads(json.dumps(payload)))
        try:
            self._barrier.wait(timeout=2)
        except threading.BrokenBarrierError as error:
            raise AssertionError("participant model calls executed sequentially") from error
        if agent_id == self.fail_agent_id:
            raise ProviderFailure(
                "OpenAI request failed with status 503",
                detail={
                    "provider_code": "service_unavailable",
                    "status": 503,
                    "request_id": f"req_{agent_id}",
                },
            )
        verdict = self.verdicts[agent_id]
        output = {
            "verdict": verdict,
            "analysis": f"Independent analysis from {agent_id}.",
            "recommendations": ["Keep the evidence boundary explicit."],
            "risks": (["One dissent remains visible."] if verdict == "challenge" else []),
            "citations": ["brief:L1-L1"],
        }
        return {
            "id": f"resp_{agent_id}",
            "status": "completed",
            "model": "gpt-5.6",
            "usage": {"input_tokens": 50, "output_tokens": 30, "total_tokens": 80},
            "output": [
                {
                    "id": f"msg_{agent_id}",
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(output),
                            "annotations": [],
                        }
                    ],
                }
            ],
        }


class BoardRoomClient:
    """Strict participant/editor seam for the product-level BoardRoom proof."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self.verdicts: dict[str, str] = {}
        self.requests: list[dict[str, object]] = []
        self.editor_requests: list[dict[str, object]] = []
        self.minimum_editor_output_tokens = 0
        self._lock = threading.Lock()
        self._barrier = threading.Barrier(2)

    def configure(self, verdicts: dict[str, str]) -> None:
        self.verdicts = dict(verdicts)
        self.requests = []
        self.editor_requests = []
        self._barrier = threading.Barrier(len(verdicts))

    def create(self, payload: dict[str, object]) -> dict[str, object]:
        if self.store.in_write_transaction():
            raise AssertionError("BoardRoom provider I/O happened in a write transaction")
        request = json.loads(json.dumps(payload))
        with self._lock:
            self.requests.append(request)
        metadata = payload.get("metadata")
        text = payload.get("text")
        if not isinstance(metadata, dict) or not isinstance(text, dict):
            raise AssertionError("BoardRoom model call has no strict contract metadata")
        output_format = text.get("format")
        if not isinstance(output_format, dict):
            raise AssertionError("BoardRoom model call has no strict output schema")
        schema = output_format.get("schema")
        if not isinstance(schema, dict) or not isinstance(schema.get("properties"), dict):
            raise AssertionError("BoardRoom model call output schema is malformed")
        agent_id = str(metadata["agent_version_id"])
        if "verdict" in schema["properties"]:
            try:
                self._barrier.wait(timeout=2)
            except threading.BrokenBarrierError as error:
                raise AssertionError("BoardRoom participants did not run independently") from error
            verdict = self.verdicts[agent_id]
            output = {
                "verdict": verdict,
                "analysis": f"Independent evidence review by {agent_id}.",
                "recommendations": ["Ship only with the evidence loop visible."],
                "risks": (["A material challenge remains."] if verdict == "challenge" else []),
                "citations": ["launch-brief@v1:L1-L3"],
            }
        elif "decision" in schema["properties"]:
            with self._lock:
                self.editor_requests.append(request)
            output_budget = payload.get("max_output_tokens")
            if (
                isinstance(output_budget, int)
                and output_budget < self.minimum_editor_output_tokens
            ):
                return {
                    "id": f"resp_{len(self.requests)}",
                    "status": "incomplete",
                    "incomplete_details": {"reason": "max_output_tokens"},
                    "model": payload["model"],
                    "usage": {
                        "input_tokens": 2_705,
                        "output_tokens": output_budget,
                        "total_tokens": 2_705 + output_budget,
                    },
                    "output": [],
                }
            output = {
                "decision": "Proceed with a governed context-to-decision demonstration.",
                "consensus": ["The runtime evidence must remain inspectable."],
                "dissent": ["A material challenge remains."],
                "open_questions": ["Which benchmark should the judges repeat?"],
                "citations": ["launch-brief@v1:L1-L3"],
            }
        else:
            raise AssertionError("unexpected BoardRoom output contract")
        return {
            "id": f"resp_{len(self.requests)}",
            "status": "completed",
            "model": payload["model"],
            "usage": {"input_tokens": 70, "output_tokens": 40, "total_tokens": 110},
            "output": [
                {
                    "id": f"msg_{len(self.requests)}",
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(output),
                            "annotations": [],
                        }
                    ],
                }
            ],
        }


class FanOutRuntimeContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.database = Path(self.temporary.name) / "fan-out.sqlite3"
        self.store = Store(self.database)
        self.store.initialize()
        self.client = ConcurrentCouncilClient(self.store)
        self.plane = ControlPlane(self.store, self.client)
        self.workspace_id = self.plane.create_workspace(seed=False)["workspace_id"]
        self.participants = self._participants()

    def _participants(self) -> list[dict[str, object]]:
        actions: list[dict[str, object]] = []
        for role in ("product", "risk", "operations"):
            prompt = self.plane.create_prompt(
                self.workspace_id,
                name=f"{role.title()} council prompt",
                slug=f"{role}-council-prompt",
                template=f"Review this brief independently as {role}: {{{{brief}}}}",
                variables=["brief"],
            )
            agent = self.plane.create_agent(
                self.workspace_id,
                name=f"{role.title()} council participant",
                slug=f"{role}-council-participant",
                role="executor",
                model="gpt-5.6",
                instructions=(
                    f"Represent the {role} perspective. Do not coordinate with other participants."
                ),
                prompt_version_id=prompt["version"]["id"],
                skill_version_ids=[],
            )
            action = self.plane.create_action(
                self.workspace_id,
                name=f"{role.title()} independent review",
                slug=f"{role}-independent-review",
                description=f"Produces the {role} vote and analysis as strict data.",
                kind="ai",
                input_schema=INPUT_SCHEMA,
                output_schema=PARTICIPANT_SCHEMA,
                outcomes=SUCCESS_ERROR,
                config={"max_tool_calls": 0, "reasoning_effort": "low"},
                agent_version_id=agent["version"]["id"],
            )
            actions.append({"role": role, "agent": agent, "action": action})
        return actions

    def _node(self, *, error_policy: str = "isolate", quorum: int = 2) -> dict[str, object]:
        return {
            "id": "council",
            "type": "fan_out",
            "version_id": "fanout-v1",
            "input_mapping": {"brief": {"source": "input", "path": "brief"}},
            "members": [
                {
                    "id": item["role"],
                    "type": "action",
                    "version_id": item["action"]["version"]["id"],
                }
                for item in self.participants
            ],
            "barrier": {
                "mode": "quorum",
                "quorum": quorum,
                "verdict_path": "verdict",
                "affirmative_values": ["commit"],
                "on_member_error": error_policy,
            },
        }

    def _flow(self, *, slug: str, error_policy: str = "isolate") -> dict[str, object]:
        return self.plane.create_studio_flow(
            self.workspace_id,
            name=slug.replace("-", " ").title(),
            slug=slug,
            description="Executes independent participants and a deterministic quorum barrier.",
            input_schema=INPUT_SCHEMA,
            outcomes=COUNCIL_OUTCOMES,
            start_node_id="council",
            nodes=[self._node(error_policy=error_policy)],
            routes=[],
        )

    def _verdict_map(self, values: tuple[str, str, str]) -> dict[str, str]:
        return {
            item["agent"]["version"]["id"]: verdict
            for item, verdict in zip(self.participants, values, strict=True)
        }

    def test_independent_model_calls_overlap_and_quorum_preserves_dissent(self) -> None:
        flow = self._flow(slug="parallel-launch-council")
        self.client.configure(self._verdict_map(("commit", "challenge", "commit")))
        self.assertEqual(
            self.plane.studio_flow_model_call_forecast(
                self.workspace_id, flow["id"]
            ),
            3,
        )
        started = time.monotonic()
        run = self.plane.start_studio_run(
            self.workspace_id,
            flow["id"],
            input_data={"brief": "Ship the cited context-to-decision loop."},
        )
        elapsed = time.monotonic() - started

        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["outcome"], "converged")
        self.assertLess(elapsed, 2)
        self.assertEqual(len(self.client.requests), 3)
        self.assertEqual(len(run["model_calls"]), 3)
        self.assertEqual(len(run["action_receipts"]), 3)
        self.assertEqual(len(run["steps"]), 4)
        graph_step = next(step for step in run["steps"] if step["member_id"] is None)
        member_steps = [step for step in run["steps"] if step["member_id"] is not None]
        self.assertEqual(graph_step["node_type"], "fan_out")
        self.assertTrue(
            all(step["parent_step_id"] == graph_step["id"] for step in member_steps)
        )
        self.assertEqual(
            {step["member_id"] for step in member_steps},
            {"product", "risk", "operations"},
        )
        barrier = run["output"]["barrier"]
        self.assertEqual(barrier["affirmative"], 2)
        self.assertEqual(barrier["failed"], 0)
        self.assertEqual(barrier["dissenting_members"], ["risk"])
        self.assertTrue(barrier["converged"])
        self.assertIn("fan_out.dispatched", [event["type"] for event in run["events"]])
        self.assertIn("fan_out.barrier_reached", [event["type"] for event in run["events"]])
        self.assertTrue(verify_event_chain(run["events"]))
        pin_types = [item["type"] for item in flow["version"]["pinned_resources"]]
        self.assertEqual(pin_types.count("fan_out"), 1)
        self.assertEqual(pin_types.count("action"), 3)

    def test_isolated_member_failure_is_visible_and_cannot_vote(self) -> None:
        flow = self._flow(slug="isolated-failure-council")
        verdicts = self._verdict_map(("commit", "challenge", "commit"))
        failed_agent_id = self.participants[1]["agent"]["version"]["id"]
        self.client.configure(verdicts, fail_agent_id=failed_agent_id)
        run = self.plane.start_studio_run(
            self.workspace_id,
            flow["id"],
            input_data={"brief": "Continue only if two independent members commit."},
        )
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["outcome"], "converged")
        self.assertEqual(run["output"]["barrier"]["affirmative"], 2)
        self.assertEqual(run["output"]["barrier"]["failed"], 1)
        failed = run["output"]["members"]["risk"]
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["error_code"], "provider_failure")
        self.assertNotIn(f"req_{failed_agent_id}", failed["error"])
        self.assertEqual(
            len([step for step in run["steps"] if step["status"] == "failed"]), 1
        )
        self.assertEqual(
            len(
                [
                    receipt
                    for receipt in run["action_receipts"]
                    if receipt["outcome"] == "failed"
                ]
            ),
            1,
        )
        self.assertTrue(verify_event_chain(run["events"]))

    def test_fail_fast_policy_closes_the_parent_after_member_evidence_is_written(self) -> None:
        flow = self._flow(
            slug="fail-closed-council", error_policy="fail_fast"
        )
        verdicts = self._verdict_map(("commit", "challenge", "commit"))
        failed_agent_id = self.participants[1]["agent"]["version"]["id"]
        self.client.configure(verdicts, fail_agent_id=failed_agent_id)
        run = self.plane.start_studio_run(
            self.workspace_id,
            flow["id"],
            input_data={"brief": "No participant failure may be hidden."},
        )
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["error_code"], "contract_violation")
        self.assertEqual(len(run["steps"]), 4)
        self.assertEqual(
            next(step for step in run["steps"] if step["member_id"] is None)["status"],
            "failed",
        )
        self.assertIn("fan_out.barrier_reached", [event["type"] for event in run["events"]])
        self.assertTrue(verify_event_chain(run["events"]))

    def test_publication_rejects_quorum_stuffing_unsafe_writes_and_bad_contracts(self) -> None:
        duplicate = self._node()
        duplicate["members"][1]["version_id"] = duplicate["members"][0]["version_id"]
        with self.assertRaisesRegex(ContractViolation, "distinct target"):
            self.plane.create_studio_flow(
                self.workspace_id,
                name="Duplicate council",
                slug="duplicate-council",
                description="Must reject duplicated votes.",
                input_schema=INPUT_SCHEMA,
                outcomes=COUNCIL_OUTCOMES,
                start_node_id="council",
                nodes=[duplicate],
                routes=[],
            )

        impossible = self._node(quorum=2)
        impossible["barrier"]["quorum"] = 4
        with self.assertRaisesRegex(ContractViolation, "quorum"):
            self.plane.create_studio_flow(
                self.workspace_id,
                name="Impossible council",
                slug="impossible-council",
                description="Must reject an impossible quorum.",
                input_schema=INPUT_SCHEMA,
                outcomes=COUNCIL_OUTCOMES,
                start_node_id="council",
                nodes=[impossible],
                routes=[],
            )

        write = self.plane.create_action(
            self.workspace_id,
            name="Unsafe concurrent write",
            slug="unsafe-concurrent-write",
            description="A writing Action that belongs after the barrier.",
            kind="data_store",
            input_schema=INPUT_SCHEMA,
            output_schema={
                "type": "object",
                "properties": {
                    "effect_id": {"type": "string"},
                    "collection": {"type": "string"},
                },
                "required": ["effect_id", "collection"],
                "additionalProperties": False,
            },
            outcomes=SUCCESS_ERROR,
            config={
                "operation": "append_record",
                "collection": "unsafe-council-writes",
                "write_enabled": True,
            },
            agent_version_id=None,
        )
        unsafe = self._node()
        unsafe["members"][0] = {
            "id": "writer",
            "type": "action",
            "version_id": write["version"]["id"],
        }
        with self.assertRaisesRegex(ContractViolation, "may not pause or mint effects"):
            self.plane.create_studio_flow(
                self.workspace_id,
                name="Unsafe write council",
                slug="unsafe-write-council",
                description="Must route writes after the deterministic barrier.",
                input_schema=INPUT_SCHEMA,
                outcomes=COUNCIL_OUTCOMES,
                start_node_id="council",
                nodes=[unsafe],
                routes=[],
            )

        different_schema = {
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
            "additionalProperties": False,
        }
        prompt = self.plane.create_prompt(
            self.workspace_id,
            name="Different input prompt",
            slug="different-input-prompt",
            template="{{question}}",
            variables=["question"],
        )
        agent = self.plane.create_agent(
            self.workspace_id,
            name="Different input Agent",
            slug="different-input-agent",
            role="executor",
            model="gpt-5.6",
            instructions="Uses a deliberately incompatible input contract.",
            prompt_version_id=prompt["version"]["id"],
            skill_version_ids=[],
        )
        action = self.plane.create_action(
            self.workspace_id,
            name="Different input review",
            slug="different-input-review",
            description="Uses a deliberately incompatible input contract.",
            kind="ai",
            input_schema=different_schema,
            output_schema=PARTICIPANT_SCHEMA,
            outcomes=SUCCESS_ERROR,
            config={"max_tool_calls": 0, "reasoning_effort": "low"},
            agent_version_id=agent["version"]["id"],
        )
        incompatible = self._node()
        incompatible["members"][0] = {
            "id": "different",
            "type": "action",
            "version_id": action["version"]["id"],
        }
        with self.assertRaisesRegex(ContractViolation, "identical mapped input"):
            self.plane.create_studio_flow(
                self.workspace_id,
                name="Incompatible council",
                slug="incompatible-council",
                description="Must reject member input drift.",
                input_schema=INPUT_SCHEMA,
                outcomes=COUNCIL_OUTCOMES,
                start_node_id="council",
                nodes=[incompatible],
                routes=[],
            )

    def test_goal_judge_cannot_be_one_of_the_participants(self) -> None:
        agents: list[dict[str, object]] = []
        for index in range(3):
            prompt = self.plane.create_prompt(
                self.workspace_id,
                name=f"Empty council prompt {index}",
                slug=f"empty-council-prompt-{index}",
                template="Judge only supplied runtime evidence.",
                variables=[],
            )
            agents.append(
                self.plane.create_agent(
                    self.workspace_id,
                    name=f"Empty council Agent {index}",
                    slug=f"empty-council-agent-{index}",
                    role="executor",
                    model="gpt-5.6",
                    instructions="Return a bounded textual judgement.",
                    prompt_version_id=prompt["version"]["id"],
                    skill_version_ids=[],
                )
            )
        judge_id = agents[0]["version"]["id"]
        empty_schema = {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        }
        node = {
            "id": "council",
            "type": "fan_out",
            "version_id": "fanout-v1",
            "input_mapping": {},
            "members": [
                {
                    "id": f"member-{index}",
                    "type": "agent",
                    "version_id": agent["version"]["id"],
                }
                for index, agent in enumerate(agents)
            ],
            "barrier": {
                "mode": "quorum",
                "quorum": 2,
                "verdict_path": "text",
                "affirmative_values": ["commit"],
                "on_member_error": "isolate",
            },
        }
        with self.assertRaisesRegex(ContractViolation, "adjudicate its own work"):
            self.plane.create_studio_flow(
                self.workspace_id,
                name="Self judged council",
                slug="self-judged-council",
                description="The participant may not adjudicate its own council.",
                input_schema=empty_schema,
                outcomes=COUNCIL_OUTCOMES,
                acceptance_criteria=[
                    {
                        "id": "council-receipts",
                        "statement": "The council participant Steps executed.",
                        "evidence_kind": "step",
                        "node_ids": ["council"],
                    }
                ],
                judge_agent_version_id=judge_id,
                start_node_id="council",
                nodes=[node],
                routes=[],
            )


class BoardRoomProductContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = Store(Path(self.temporary.name) / "boardroom.sqlite3")
        self.store.initialize()
        self.client = BoardRoomClient(self.store)
        self.plane = ControlPlane(self.store, self.client)
        self.workspace_id = self.plane.create_workspace(seed=False)["workspace_id"]

    @staticmethod
    def _participants() -> list[dict[str, object]]:
        return [
            {
                "id": identifier,
                "name": name,
                "perspective": perspective,
                "model": "gpt-5.6",
                "instructions": "Test the brief against supplied evidence and expose uncertainty.",
                "allowed_action_version_ids": [],
                "max_tool_calls": 0,
                "reasoning_effort": "low",
            }
            for identifier, name, perspective in (
                ("product", "Product Steward", "User value and product coherence."),
                ("risk", "Risk Challenger", "Failure modes and unsupported claims."),
                ("operations", "Runtime Operator", "Repeatability and operational proof."),
            )
        ]

    @staticmethod
    def _editor() -> dict[str, str]:
        return {
            "name": "Dissent Editor",
            "model": "gpt-5.6",
            "instructions": "Synthesize without erasing challenges or inventing consensus.",
            "reasoning_effort": "medium",
        }

    def _room(
        self,
        *,
        slug: str,
        approval_mode: str = "none",
        write_collection: str | None = None,
    ) -> dict[str, object]:
        return self.plane.create_boardroom(
            self.workspace_id,
            name=slug.replace("-", " ").title(),
            slug=slug,
            purpose="Reach an evidence-bound decision while preserving independent dissent.",
            participants=self._participants(),
            editor=self._editor(),
            quorum=2,
            error_policy="isolate",
            approval_mode=approval_mode,
            write_collection=write_collection,
        )

    def _configure(self, room: dict[str, object]) -> None:
        votes = ("commit", "challenge", "commit")
        resources = room["participant_resources"]
        self.client.configure(
            {
                item["agent"]["version"]["id"]: verdict
                for item, verdict in zip(resources, votes, strict=True)
            }
        )

    def test_factory_builds_an_editable_parallel_flow_and_preserves_dissent(self) -> None:
        room = self._room(slug="launch-evidence-council")
        # Regression for the live 2026-07-21 failure: three participant records
        # made the synthesis request materially larger than an individual vote.
        # A 1,500-token ceiling let OpenAI return status=incomplete before the
        # strict editor object existed. The provider-shaped seam reproduces that
        # exact status until the generated editor carries its larger budget.
        self.client.minimum_editor_output_tokens = 4_000
        self._configure(room)
        flow = room["flow"]
        node_types = [node["type"] for node in flow["version"]["nodes"]]

        self.assertEqual(node_types, ["fan_out", "action", "action"])
        self.assertEqual(len(room["participant_resources"]), 3)
        self.assertEqual(room["runtime"]["composition"], "fan_out")
        boardroom = self.plane.studio_snapshot(self.workspace_id)["boardrooms"][0]
        self.assertEqual(boardroom["flow_id"], flow["id"])
        self.assertEqual(boardroom["barrier"]["quorum"], 2)
        self.assertEqual(len(boardroom["members"]), 3)
        self.assertTrue(boardroom["editable_in_flow_studio"])
        self.assertEqual(
            self.plane.studio_flow_model_call_forecast(
                self.workspace_id, flow["id"]
            ),
            4,
        )
        started = time.monotonic()
        run = self.plane.start_studio_run(
            self.workspace_id,
            flow["id"],
            input_data={
                "brief": "Show an award jury the complete, inspectable runtime loop.",
                "context": "launch-brief@v1:L1-L3 — every claim needs replayable evidence.",
            },
        )
        elapsed = time.monotonic() - started

        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["output"]["status"], "synthesized")
        self.assertEqual(run["output"]["dissent"], ["A material challenge remains."])
        self.assertEqual(run["output"]["approval_reason"], "")
        self.assertEqual(len(run["model_calls"]), 4)
        self.assertEqual(len(self.client.editor_requests), 1)
        self.assertEqual(
            room["editor"]["action"]["version"]["config"]["max_output_tokens"],
            4_000,
        )
        self.assertEqual(
            {
                item["action"]["version"]["config"]["max_output_tokens"]
                for item in room["participant_resources"]
            },
            {2_000},
        )
        self.assertEqual(
            self.client.editor_requests[0]["max_output_tokens"], 4_000
        )
        self.assertLess(elapsed, 2)
        editor_prompt = self.client.editor_requests[0]["input"][0]["content"]
        self.assertIn('"verdict":"challenge"', editor_prompt)
        self.assertNotIn("'verdict':", editor_prompt)
        self.assertTrue(verify_event_chain(run["events"]))

    def test_published_member_ids_are_stable_downstream_schema_keys(self) -> None:
        room = self._room(slug="stable-member-council")
        flow = room["flow"]
        nodes = json.loads(json.dumps(flow["version"]["nodes"]))
        nodes[0]["members"][1]["id"] = "risk-review"

        with self.assertRaisesRegex(ContractViolation, "member IDs are immutable"):
            self.plane.revise_studio_flow(
                self.workspace_id,
                flow["id"],
                expected_revision=flow["revision"],
                input_schema=flow["version"]["input_schema"],
                output_schema=flow["version"]["output_schema"],
                outcomes=flow["version"]["outcomes"],
                start_node_id=flow["version"]["start_node_id"],
                nodes=nodes,
                routes=flow["version"]["routes"],
            )

    def test_editor_token_ceiling_is_reported_as_an_actionable_provider_failure(self) -> None:
        room = self._room(slug="bounded-editor-council")
        self.client.minimum_editor_output_tokens = 4_001
        self._configure(room)

        run = self.plane.start_studio_run(
            self.workspace_id,
            room["flow"]["id"],
            input_data={
                "brief": "Preserve all material dissent in a bounded synthesis.",
                "context": "launch-brief@v1:L1-L3",
            },
        )

        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["error_code"], "provider_failure")
        self.assertIn("configured output-token limit (4,000 tokens)", run["error_message"])
        self.assertEqual(run["model_calls"][-1]["status"], "incomplete")
        self.assertEqual(run["model_calls"][-1]["usage"]["output_tokens"], 4_000)

    def test_human_gate_precedes_a_bounded_write_and_rejection_has_no_effect(self) -> None:
        room = self._room(
            slug="governed-launch-council",
            approval_mode="human",
            write_collection="approved-decisions",
        )
        flow = room["flow"]
        self._configure(room)
        rejected_run = self.plane.start_studio_run(
            self.workspace_id,
            flow["id"],
            input_data={"brief": "Review launch.", "context": "launch-brief@v1:L1-L3"},
        )
        self.assertEqual(rejected_run["status"], "waiting_approval")
        self.assertEqual(rejected_run["effects"], [])
        rejected = self.plane.decide_studio_approval(
            self.workspace_id,
            rejected_run["pending_approval"]["id"],
            approved=False,
            actor="award-operator",
            reason="The material dissent needs another evidence pass.",
        )
        self.assertEqual(rejected["status"], "completed")
        self.assertEqual(rejected["output"]["status"], "rejected")
        self.assertEqual(rejected["effects"], [])

        self._configure(room)
        approved_run = self.plane.start_studio_run(
            self.workspace_id,
            flow["id"],
            input_data={"brief": "Review launch.", "context": "launch-brief@v1:L1-L3"},
        )
        approved = self.plane.decide_studio_approval(
            self.workspace_id,
            approved_run["pending_approval"]["id"],
            approved=True,
            actor="award-operator",
            reason="The decision, dissent, citations, and bounded write are acceptable.",
        )
        self.assertEqual(approved["status"], "completed")
        self.assertEqual(approved["output"]["status"], "written")
        self.assertEqual(approved["output"]["collection"], "approved-decisions")
        self.assertEqual(len(approved["effects"]), 1)
        self.assertEqual(approved["effects"][0]["collection"], "approved-decisions")
        self.assertTrue(verify_event_chain(approved["events"]))

    def test_factory_refuses_write_authority_before_publishing_resources(self) -> None:
        before = self.plane.snapshot(self.workspace_id)
        with self.assertRaisesRegex(ContractViolation, "human approval"):
            self._room(
                slug="unsafe-boardroom",
                approval_mode="none",
                write_collection="decisions",
            )
        after = self.plane.snapshot(self.workspace_id)
        for collection in ("prompts", "skills", "agents"):
            self.assertEqual(len(after[collection]), len(before[collection]))
        for collection in ("actions", "flows"):
            self.assertEqual(
                len(after["studio"][collection]),
                len(before["studio"][collection]),
            )

    def test_factory_refuses_a_slug_collision_before_partial_publication(self) -> None:
        self._room(slug="stable-boardroom")
        before = self.plane.snapshot(self.workspace_id)
        with self.assertRaisesRegex(Conflict, "already exists"):
            self._room(slug="stable-boardroom")
        after = self.plane.snapshot(self.workspace_id)
        for collection in ("prompts", "skills", "agents"):
            self.assertEqual(len(after[collection]), len(before[collection]))
        for collection in ("actions", "flows"):
            self.assertEqual(
                len(after["studio"][collection]),
                len(before["studio"][collection]),
            )

    def test_unrouted_human_rejection_remains_fail_closed(self) -> None:
        approval = self.plane.create_action(
            self.workspace_id,
            name="Fail closed approval",
            slug="fail-closed-approval",
            description="Has no explicit rejected successor.",
            kind="approval",
            input_schema={
                "type": "object",
                "properties": {"decision": {"type": "string"}},
                "required": ["decision"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "approved": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
                "required": ["approved", "reason"],
                "additionalProperties": False,
            },
            outcomes=None,
            config={"message_template": "Approve {{decision}}"},
            agent_version_id=None,
        )
        flow = self.plane.create_studio_flow(
            self.workspace_id,
            name="Fail closed gate",
            slug="fail-closed-gate",
            description="A denial with no branch must remain blocked.",
            input_schema={
                "type": "object",
                "properties": {"decision": {"type": "string"}},
                "required": ["decision"],
                "additionalProperties": False,
            },
            start_node_id="approval",
            nodes=[
                {
                    "id": "approval",
                    "type": "action",
                    "version_id": approval["version"]["id"],
                    "input_mapping": {
                        "decision": {"source": "input", "path": "decision"}
                    },
                }
            ],
            routes=[],
        )
        waiting = self.plane.start_studio_run(
            self.workspace_id, flow["id"], input_data={"decision": "publish"}
        )
        rejected = self.plane.decide_studio_approval(
            self.workspace_id,
            waiting["pending_approval"]["id"],
            approved=False,
            actor="safety-reviewer",
            reason="No rejected branch was explicitly designed for this denial.",
        )
        self.assertEqual(rejected["status"], "blocked")
        self.assertEqual(rejected["error_code"], "approval_rejected")


if __name__ == "__main__":
    unittest.main()
