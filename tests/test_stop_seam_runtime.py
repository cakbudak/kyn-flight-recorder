"""The stop seam, exercised through the real runtime rather than the pure resolver.

`tests/test_stop_seam.py` proves the resolver in isolation: injected criteria,
injected anchors, injected evidence. That is the right shape for a pure module
and it proves nothing about whether the runtime ever consults it, whether the
evidence it consults is the Run's own, or whether a refused completion actually
stops a Run from being recorded as finished.

This suite runs whole Runs. Every assertion below is about durable product state
— the Run row, its status history, the ledger, committed effects — never about a
function's return value. The claim under test is the product-level one:

    A Run reports `completed` only when every declared acceptance criterion is
    carried by evidence this Run actually minted, at a site the criterion pinned,
    in a state that can carry the claim.

The judge is a scripted provider seam, following `tests/test_studio_contract.py`:
it asserts no provider I/O happens inside a SQLite write transaction, and it
refuses to answer any model call that is not the adjudication, so a fixture that
accidentally reaches a model for some other reason fails loudly instead of
quietly inflating the call count this suite measures.
"""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from unittest import mock

from backend.contracts import ProviderFailure, verify_event_chain
from backend.service import ControlPlane
from backend.store import Store
from backend.stop_seam import (
    ANCHOR_FOREIGN_RUN,
    ANCHOR_NODE_MISMATCH,
    ANCHOR_STATE_MISMATCH,
    ANCHOR_UNRESOLVABLE,
    adjudicate,
)
from backend.studio_runtime import StudioRuntime


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
CONDITION_OUTPUT = {
    "type": "object",
    "properties": {"matched": {"type": "boolean"}, "actual": {"type": "string"}},
    "required": ["matched", "actual"],
    "additionalProperties": False,
}
CONDITION_OUTCOMES = [
    {"id": "true", "label": "True", "description": "", "tone": "success"},
    {"id": "false", "label": "False", "description": "", "tone": "warning"},
    {"id": "error", "label": "Error", "description": "", "tone": "danger"},
]
NO_RETRY = {"max_attempts": 1, "backoff_seconds": 0, "retry_on": [], "on_error": "fail"}
CONTINUE_ON_ERROR = {
    "max_attempts": 1,
    "backoff_seconds": 0,
    "retry_on": [],
    "on_error": "continue",
}
VALUE_MAPPING = {"value": {"source": "input", "path": "value"}}

#: The state each evidence kind must be in before it can carry a claim, in the
#: shape the judge is shown. Read here rather than imported so the fixture's idea
#: of "honest" is written down beside the tests that rely on it.
ADMISSIBLE_STATE: Mapping[str, Any] = {
    "receipt": "succeeded",
    "step": "completed",
    "approval": True,
}


# ---------------------------------------------------------------------------
# The scripted Goal-Judge seam.
# ---------------------------------------------------------------------------


Chooser = Callable[[Mapping[str, Any], Mapping[str, Any]], Iterable[str]]


class ScriptedGoalJudgeClient:
    """Provider-shaped deterministic seam standing in for the pinned Goal-Judge.

    The seam never decides anything. It replays whatever verdict the test
    scripted, which is the point: the suite is about what the *runtime* does with
    a verdict, including verdicts an honest judge would never emit.
    """

    def __init__(self, store: Store, strategy: Callable[..., Any] | None = None) -> None:
        self.store = store
        self.strategy = strategy
        self.requests: list[dict[str, Any]] = []
        self.questions: list[dict[str, Any]] = []

    @property
    def adjudications(self) -> int:
        return len(self.questions)

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.store.in_write_transaction():
            raise AssertionError("provider I/O happened inside a SQLite write transaction")
        self.requests.append(json.loads(json.dumps(payload)))
        metadata = payload.get("metadata")
        if not isinstance(metadata, dict) or metadata.get("kyn_surface") != "agent-studio":
            raise AssertionError("Studio model calls must identify their runtime surface")
        if metadata.get("operation") != "adjudication":
            raise AssertionError(
                "these fixtures make no model call but the adjudication; got "
                f"operation {metadata.get('operation')!r}"
            )
        if self.strategy is None:
            raise AssertionError(
                "the runtime adjudicated a Run this test scripted no judgement for"
            )
        question = json.loads(payload["input"][0]["content"])
        self.questions.append(question)
        verdict = {
            "assessment": (
                "A scripted adjudication replayed verbatim by the stop-seam suite."
            ),
            "criteria": list(self.strategy(question)),
        }
        return {
            "id": f"resp_judge_{len(self.requests)}",
            "status": "completed",
            "model": "gpt-5.6",
            "usage": {"input_tokens": 32, "output_tokens": 18, "total_tokens": 50},
            "output": [
                {
                    "id": f"msg_judge_{len(self.requests)}",
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(verdict, separators=(",", ":")),
                            "annotations": [],
                        }
                    ],
                }
            ],
        }


def judgement(chooser: Chooser, *, unevidenced: bool | None = None) -> Callable[..., Any]:
    """Build a judge strategy from a per-criterion anchor chooser.

    `unevidenced` defaults to the honest reading — a criterion with no anchors is
    unevidenced — and is overridable so a test can script the hedge a real judge
    could emit: prose that refuses while the data still points at evidence.
    """

    def strategy(question: Mapping[str, Any]) -> list[dict[str, Any]]:
        judged: list[dict[str, Any]] = []
        for criterion in question["acceptance_criteria"]:
            anchors = list(chooser(criterion, question["run_evidence"]))
            judged.append(
                {
                    "criterion_id": criterion["criterion_id"],
                    "unevidenced": (not anchors) if unevidenced is None else unevidenced,
                    "anchors": anchors,
                    "reason": "A scripted judgement pinned by the test that wrote it.",
                }
            )
        return judged

    return strategy


def _records(evidence: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [record for records in evidence.values() for record in records]


def honestly(criterion: Mapping[str, Any], evidence: Mapping[str, Any]) -> list[str]:
    """Every record of the declared kind, at a declared site, in an admitting state."""

    admissible = ADMISSIBLE_STATE.get(criterion["evidence_kind"], None)
    return [
        record["id"]
        for record in _records(evidence)
        if record["kind"] == criterion["evidence_kind"]
        and record["site"] in criterion["declared_sites"]
        and (admissible is None or record["state"] == admissible)
    ]


def of_kind_anywhere(criterion: Mapping[str, Any], evidence: Mapping[str, Any]) -> list[str]:
    """Every record of the declared kind, wherever it was minted. Site-blind."""

    return [
        record["id"]
        for record in _records(evidence)
        if record["kind"] == criterion["evidence_kind"]
    ]


def at_site_whatever_the_state(
    criterion: Mapping[str, Any], evidence: Mapping[str, Any]
) -> list[str]:
    """Every record of the declared kind at a declared site, state ignored."""

    return [
        record["id"]
        for record in _records(evidence)
        if record["kind"] == criterion["evidence_kind"]
        and record["site"] in criterion["declared_sites"]
    ]


def anchoring_nothing(
    criterion: Mapping[str, Any], evidence: Mapping[str, Any]
) -> list[str]:
    del criterion, evidence
    return []


def anchoring(*anchor_ids: str) -> Chooser:
    def chooser(
        criterion: Mapping[str, Any], evidence: Mapping[str, Any]
    ) -> list[str]:
        del criterion, evidence
        return list(anchor_ids)

    return chooser


# ---------------------------------------------------------------------------
# Fixture.
# ---------------------------------------------------------------------------


class StopSeamRuntimeCase(unittest.TestCase):
    """One workspace, five probe Actions over one input contract, one judge.

    Every probe shares `VALUE_SCHEMA`, so any of them composes into any graph
    shape and only the pinned capability under test varies — the same trick
    `tests/test_acceptance_contract.py` uses at publication time, carried into
    execution.
    """

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = Store(Path(self.temporary.name) / "stop-seam-runtime.sqlite3")
        self.store.initialize()
        self.client = ScriptedGoalJudgeClient(self.store)
        self.plane = ControlPlane(self.store, self.client)
        self.workspace_id = self.plane.create_workspace(seed=False)["workspace_id"]
        self.probes = {
            "writer": self._probe(
                "probe-writer",
                kind="data_store",
                output_schema=STORE_OUTPUT,
                config={
                    "operation": "append_record",
                    "collection": "declared-evidence",
                    "write_enabled": True,
                },
            ),
            "decoy": self._probe(
                "probe-decoy",
                kind="data_store",
                output_schema=STORE_OUTPUT,
                config={
                    "operation": "append_record",
                    "collection": "decoy-evidence",
                    "write_enabled": True,
                },
            ),
            "quiet": self._probe(
                "probe-quiet",
                kind="template",
                output_schema=TEXT_OUTPUT,
                config={"template": "{{value}}"},
            ),
            "gate": self._probe(
                "probe-gate",
                kind="condition",
                output_schema=CONDITION_OUTPUT,
                config={"path": "value", "operator": "equals", "value": "declared"},
                outcomes=CONDITION_OUTCOMES,
            ),
            # Reads a path its own validated input never carries, so the Action
            # raises inside the executor: the Step fails and the receipt is
            # minted `failed` rather than merely absent.
            "broken": self._probe(
                "probe-broken",
                kind="transform",
                output_schema=TEXT_OUTPUT,
                config={
                    "operation": "map",
                    "mappings": {"text": {"source": "input", "path": "absent"}},
                },
            ),
        }
        self.judge = self._judge_agent()

    # -- building blocks ----------------------------------------------------

    def _probe(
        self,
        slug: str,
        *,
        kind: str,
        output_schema: dict[str, Any],
        config: dict[str, Any],
        outcomes: list[dict[str, Any]] | None = None,
    ) -> str:
        action = self.plane.create_action(
            self.workspace_id,
            name=f"Probe {slug}",
            slug=slug,
            description="A probe Action pinned to exercise one stop-seam behaviour.",
            kind=kind,
            input_schema=VALUE_SCHEMA,
            output_schema=output_schema,
            outcomes=outcomes,
            config=config,
            agent_version_id=None,
        )
        return str(action["version"]["id"])

    def _judge_agent(self) -> str:
        prompt = self.plane.create_prompt(
            self.workspace_id,
            name="Goal-Judge prompt",
            slug="goal-judge-prompt",
            template="Adjudicate the completion claim against the supplied evidence.",
            variables=[],
        )
        agent = self.plane.create_agent(
            self.workspace_id,
            name="Stop seam Goal-Judge",
            slug="stop-seam-goal-judge",
            role="executor",
            model="gpt-5.6",
            instructions="Judge completion claims against supplied Run evidence only.",
            prompt_version_id=prompt["version"]["id"],
            skill_version_ids=[],
        )
        return str(agent["version"]["id"])

    def node(
        self, probe: str, node_id: str, *, settings: Mapping[str, Any] = NO_RETRY
    ) -> dict[str, Any]:
        return {
            "id": node_id,
            "type": "action",
            "version_id": self.probes[probe],
            "input_mapping": dict(VALUE_MAPPING),
            "settings": dict(settings),
        }

    def publish(
        self,
        slug: str,
        nodes: Sequence[Mapping[str, Any]],
        *,
        routes: Sequence[Mapping[str, Any]] = (),
        criteria: Sequence[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        return self.plane.create_studio_flow(
            self.workspace_id,
            name=f"Flow {slug}",
            slug=slug,
            description="A Flow published to exercise the stop seam end to end.",
            input_schema=VALUE_SCHEMA,
            start_node_id=str(nodes[0]["id"]),
            nodes=[dict(node) for node in nodes],
            routes=[dict(route) for route in routes],
            acceptance_criteria=[dict(item) for item in criteria] or None,
            judge_agent_version_id=self.judge if criteria else None,
        )

    def start(
        self,
        flow: Mapping[str, Any],
        *,
        value: str,
        chooser: Chooser | None = None,
        unevidenced: bool | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        self.client.strategy = (
            None if chooser is None else judgement(chooser, unevidenced=unevidenced)
        )
        return self.plane.start_studio_run(
            self.workspace_id,
            flow["id"],
            input_data={"value": value},
            idempotency_key=idempotency_key,
        )

    # -- observation --------------------------------------------------------

    @staticmethod
    def status_history(run: Mapping[str, Any]) -> list[str]:
        """Every status this Run was ever in, read off its own append-only ledger.

        The final row says where a Run ended. Only the ledger says where it has
        been, and the claim this suite defends is about the whole path.
        """

        return [
            event["payload"]["to"]
            for event in run["events"]
            if event["type"] == "run.status_changed"
        ]

    @staticmethod
    def completion_events(run: Mapping[str, Any]) -> list[dict[str, Any]]:
        return [
            event for event in run["events"] if event["type"].startswith("completion.")
        ]

    def sole_completion_event(self, run: Mapping[str, Any]) -> dict[str, Any]:
        events = self.completion_events(run)
        self.assertEqual(
            len(events), 1, "exactly one adjudication belongs in a Run's ledger"
        )
        return events[0]

    @staticmethod
    def resolution(event: Mapping[str, Any], criterion_id: str) -> dict[str, Any]:
        return next(
            item
            for item in event["payload"]["criteria"]
            if item["criterion_id"] == criterion_id
        )

    def evidence(self, run_id: str, **kwargs: Any) -> dict[str, Any]:
        return self.plane.studio.adjudication_evidence(
            self.workspace_id, run_id, **kwargs
        )

    def effects(self) -> list[str]:
        with self.store.read() as connection:
            return [
                row["collection"]
                for row in connection.execute(
                    "SELECT collection FROM automation_effects ORDER BY created_at, id"
                )
            ]

    def assert_never_completed(self, run: Mapping[str, Any]) -> None:
        """The whole claim: completion never happened, not that it was undone."""

        history = self.status_history(run)
        self.assertNotIn(
            "completed",
            history,
            f"the Run entered 'completed' at some point: {history}",
        )
        self.assertNotEqual(run["status"], "completed")
        self.assertIsNone(run["output"])
        self.assertTrue(verify_event_chain(run["events"]))


# ---------------------------------------------------------------------------
# 1 + 2 — the two directions of the seam.
# ---------------------------------------------------------------------------


def criterion(
    criterion_id: str,
    evidence_kind: str,
    *node_ids: str,
    statement: str = "The declared work was performed at a declared site.",
) -> dict[str, Any]:
    return {
        "id": criterion_id,
        "statement": statement,
        "evidence_kind": evidence_kind,
        "node_ids": list(node_ids),
    }


class MetContractTest(StopSeamRuntimeCase):
    def test_a_met_contract_completes_and_the_ledger_names_the_surviving_anchors(
        self,
    ) -> None:
        flow = self.publish(
            "met-contract",
            [self.node("writer", "writer")],
            criteria=[
                criterion("record-written", "effect", "writer"),
                criterion("write-succeeded", "receipt", "writer"),
            ],
        )
        run = self.start(flow, value="declared", chooser=honestly)

        self.assertEqual(run["status"], "completed")
        self.assertIsNone(run["error_code"])
        self.assertEqual(self.effects(), ["declared-evidence"])
        self.assertEqual(self.client.adjudications, 1)

        admitted = self.sole_completion_event(run)
        self.assertEqual(admitted["type"], "completion.admitted")
        self.assertTrue(admitted["payload"]["admitted"])
        self.assertEqual(admitted["payload"]["unevidenced"], [])

        # The surviving anchors are the ids the runtime itself minted, not text
        # the judge produced. Read them back off the store and compare.
        candidates = self.evidence(run["id"])["candidates"]
        self.assertEqual(
            self.resolution(admitted, "record-written")["surviving"],
            [record["id"] for record in candidates["effects"]],
        )
        self.assertEqual(
            self.resolution(admitted, "write-succeeded")["surviving"],
            [record["id"] for record in candidates["receipts"]],
        )
        for criterion_id in ("record-written", "write-succeeded"):
            resolved = self.resolution(admitted, criterion_id)
            self.assertTrue(resolved["holds"])
            self.assertEqual(resolved["discarded"], [])

        # Admission is the last thing that happens before the Run turns terminal.
        types = [event["type"] for event in run["events"]]
        self.assertLess(
            types.index("completion.admitted"),
            len(types) - 1 - types[::-1].index("run.status_changed"),
        )
        self.assertEqual(self.status_history(run)[-1], "completed")
        self.assertTrue(verify_event_chain(run["events"]))


class UnmetContractTest(StopSeamRuntimeCase):
    """The Run must never have been `completed`, not merely not be `completed` now.

    A post-hoc annotation on a Run that already completed would satisfy a test
    that only reads the final row, and would satisfy nothing a user cares about:
    the work would already have been recorded as finished. So the assertion is
    over the status history in the append-only ledger.
    """

    def _branching_flow(self) -> dict[str, Any]:
        """Two writers; the criterion pins the one this input routes away from."""

        return self.publish(
            "unmet-contract",
            [
                self.node("gate", "gate"),
                self.node("writer", "declared-writer"),
                self.node("decoy", "decoy-writer"),
            ],
            routes=[
                {"from": "gate", "to": "declared-writer", "outcome": "true"},
                {"from": "gate", "to": "decoy-writer", "outcome": "false"},
            ],
            criteria=[criterion("record-written", "effect", "declared-writer")],
        )

    def test_an_unmet_contract_fails_unevidenced_and_never_reaches_completed(
        self,
    ) -> None:
        flow = self._branching_flow()
        run = self.start(flow, value="decoy", chooser=anchoring_nothing)

        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["error_code"], "completion_unevidenced")
        self.assertIn("record-written", run["error_message"])
        self.assert_never_completed(run)
        self.assertEqual(self.status_history(run), ["running", "failed"])

        refused = self.sole_completion_event(run)
        self.assertEqual(refused["type"], "completion.refused")
        self.assertFalse(refused["payload"]["admitted"])
        self.assertEqual(refused["payload"]["unevidenced"], ["record-written"])
        resolved = self.resolution(refused, "record-written")
        self.assertFalse(resolved["holds"])
        self.assertEqual(resolved["surviving"], [])
        self.assertEqual(resolved["declared_sites"], ["declared-writer"])

        # The refusal is recorded before the Run turns terminal, which is what
        # makes it a refusal rather than a comment on a decision already taken.
        types = [event["type"] for event in run["events"]]
        self.assertLess(types.index("completion.refused"), len(types) - 1)
        self.assertEqual(types[-1], "run.status_changed")

        # The work the Run *did* do is still on the record. A refused completion
        # is not a rollback: the decoy write happened and the ledger says so.
        self.assertEqual(self.effects(), ["decoy-evidence"])

    def test_the_same_flow_completes_when_the_run_actually_reaches_the_declared_site(
        self,
    ) -> None:
        """The refusal above is about this Run's data, not the Flow's shape.

        Without this half, the refusal could be explained by a Flow that can
        never satisfy its own contract — which is a publication defect, not a
        stop-seam one.
        """

        flow = self._branching_flow()
        run = self.start(flow, value="declared", chooser=honestly)

        self.assertEqual(run["status"], "completed")
        self.assertEqual(self.effects(), ["declared-evidence"])
        self.assertEqual(
            self.sole_completion_event(run)["type"], "completion.admitted"
        )


# ---------------------------------------------------------------------------
# 3 — the ledger stays verifiable across an adjudicated Run.
# ---------------------------------------------------------------------------


class AdjudicatedLedgerTest(StopSeamRuntimeCase):
    def _adjudicated_runs(self) -> tuple[dict[str, Any], dict[str, Any]]:
        flow = self.publish(
            "chained-adjudication",
            [self.node("writer", "writer")],
            criteria=[criterion("record-written", "effect", "writer")],
        )
        admitted = self.start(
            flow, value="declared", chooser=honestly, idempotency_key="admitted"
        )
        refused = self.start(
            flow, value="declared", chooser=anchoring_nothing, idempotency_key="refused"
        )
        return admitted, refused

    def test_the_hash_chain_verifies_across_an_admitted_and_a_refused_run(self) -> None:
        admitted, refused = self._adjudicated_runs()

        self.assertEqual(admitted["status"], "completed")
        self.assertEqual(refused["status"], "failed")
        self.assertEqual(refused["error_code"], "completion_unevidenced")
        self.assertTrue(verify_event_chain(admitted["events"]))
        self.assertTrue(verify_event_chain(refused["events"]))

        # Re-export from the store rather than trusting the returned projection.
        for run in (admitted, refused):
            exported = self.plane.get_studio_run(self.workspace_id, run["id"])
            self.assertTrue(verify_event_chain(exported["events"]))
            self.assertEqual(len(self.completion_events(exported)), 1)

    def test_the_adjudication_event_is_bound_by_the_chain_it_sits_in(self) -> None:
        """A refusal nobody can tamper with is the only refusal worth recording.

        Rewriting a refused adjudication into an admitted one must break
        verification, or the ledger records the seam's verdict without binding it.
        """

        _, refused = self._adjudicated_runs()
        doctored = copy.deepcopy(refused["events"])
        for event in doctored:
            if event["type"] == "completion.refused":
                event["type"] = "completion.admitted"
                event["payload"]["admitted"] = True
                event["payload"]["unevidenced"] = []
                break
        else:  # pragma: no cover - the fixture guarantees the event
            self.fail("the fixture must contain a refused adjudication")

        self.assertEqual(
            [event["event_hash"] for event in doctored],
            [event["event_hash"] for event in refused["events"]],
            "the doctored export carries the original hashes, as a forger would",
        )
        self.assertFalse(verify_event_chain(doctored))


# ---------------------------------------------------------------------------
# 4 — inertness. Zero criteria costs zero model calls.
# ---------------------------------------------------------------------------


class NoCriteriaTest(StopSeamRuntimeCase):
    def test_a_flow_with_no_criteria_performs_zero_adjudication_model_calls(
        self,
    ) -> None:
        """Measured as a call count, not inferred from the Run having completed.

        A judge call that happened and was ignored would still be spend, still be
        latency, and still be a model consulted about a Flow that never asked for
        one. `strategy=None` makes any such call raise, and the count proves none
        was attempted.
        """

        flow = self.publish("inert-flow", [self.node("writer", "writer")])
        self.assertEqual(flow["version"]["acceptance_criteria"], [])
        self.assertIsNone(flow["version"]["judge_agent_version_id"])

        run = self.start(flow, value="declared", chooser=None)

        self.assertEqual(len(self.client.requests), 0)
        self.assertEqual(self.client.adjudications, 0)
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["model_calls"], [])
        self.assertEqual(self.completion_events(run), [])
        self.assertEqual(self.effects(), ["declared-evidence"])
        self.assertEqual(self.status_history(run), ["running", "completed"])
        self.assertTrue(verify_event_chain(run["events"]))

    def test_the_same_flow_with_a_contract_does_call_the_judge_exactly_once(
        self,
    ) -> None:
        """Without this, a zero count would prove the fixture, not the shortcut."""

        flow = self.publish(
            "contracted-twin",
            [self.node("writer", "writer")],
            criteria=[criterion("record-written", "effect", "writer")],
        )
        run = self.start(flow, value="declared", chooser=honestly)
        self.assertEqual(run["status"], "completed")
        self.assertEqual(self.client.adjudications, 1)
        self.assertEqual(len(run["model_calls"]), 1)


class JudgeProviderFailureTest(StopSeamRuntimeCase):
    def test_a_goal_judge_provider_failure_stays_a_provider_failure_at_the_stop_seam(
        self,
    ) -> None:
        """A transient judge outage must not become an unexplained worker fault.

        The model attempt is evidence too. The Run must retain its safe provider
        classification and failed attempt receipt while never passing through
        `completed`; the asynchronous worker may then return normally instead of
        flattening a known failure into `worker_failure`.
        """

        flow = self.publish(
            "judge-provider-failure",
            [self.node("quiet", "work")],
            criteria=[criterion("work-succeeded", "receipt", "work")],
        )

        def provider_fails(
            criterion: Mapping[str, Any], evidence: Mapping[str, Any]
        ) -> list[str]:
            del criterion, evidence
            raise ProviderFailure(
                "OpenAI request failed with status 503",
                detail={
                    "provider_code": "service_unavailable",
                    "provider_type": "server_error",
                    "status": 503,
                    "request_id": "req_stop_seam_transient",
                },
            )

        run = self.start(flow, value="declared", chooser=provider_fails)

        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["error_code"], "provider_failure")
        self.assertIn("service_unavailable", run["error_message"])
        self.assert_never_completed(run)
        self.assertEqual(self.completion_events(run), [])
        self.assertNotIn(
            "run.worker_failed", [event["type"] for event in run["events"]]
        )
        self.assertEqual(self.client.adjudications, 1)
        self.assertEqual(len(run["model_calls"]), 1)
        self.assertEqual(run["model_calls"][0]["status"], "failed")
        self.assertEqual(
            run["model_calls"][0]["request_id"], "req_stop_seam_transient"
        )


# ---------------------------------------------------------------------------
# 5 + 6 + 7 — the three ways an anchor can be real and still refuse.
# ---------------------------------------------------------------------------


class WrongStateAnchorTest(StopSeamRuntimeCase):
    """A receipt is minted for every attempt, including the ones that failed."""

    def _flow_with_a_failing_node(self) -> dict[str, Any]:
        return self.publish(
            "failed-receipt",
            [
                self.node("broken", "flaky", settings=CONTINUE_ON_ERROR),
                self.node("quiet", "tail"),
            ],
            routes=[{"from": "flaky", "to": "tail", "outcome": "error"}],
            criteria=[criterion("work-succeeded", "receipt", "flaky")],
        )

    def test_a_receipt_whose_outcome_is_not_succeeded_cannot_evidence_success(
        self,
    ) -> None:
        flow = self._flow_with_a_failing_node()
        run = self.start(flow, value="declared", chooser=at_site_whatever_the_state)

        # The fixture must actually contain the failed receipt under test, or
        # this proves nothing about state.
        receipts = self.evidence(run["id"])["candidates"]["receipts"]
        failed = [item for item in receipts if item["node_id"] == "flaky"]
        self.assertEqual([item["state"] for item in failed], ["failed"])

        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["error_code"], "completion_unevidenced")
        self.assert_never_completed(run)

        refused = self.sole_completion_event(run)
        self.assertEqual(refused["type"], "completion.refused")
        resolved = self.resolution(refused, "work-succeeded")
        self.assertEqual(resolved["surviving"], [])
        self.assertEqual(
            [item["refusal"] for item in resolved["discarded"]],
            [ANCHOR_STATE_MISMATCH],
        )
        self.assertEqual(
            [item["anchor_id"] for item in resolved["discarded"]],
            [failed[0]["id"]],
        )


class IrrelevantAnchorTest(StopSeamRuntimeCase):
    """The irrelevance defect the pinned site exists to close.

    Before criteria pinned a site, a criterion declared only a *kind*. An
    `effect` criterion reading "the launch record was published" was then
    satisfied by any effect the Run wrote — including a write at a node that has
    nothing to do with the claim. The resolver could filter fabricated anchors
    and foreign-Run anchors, but it could not filter irrelevance, and filtering
    fabrication while admitting irrelevance is not a contract; it is the
    appearance of one.

    The Run below genuinely writes an effect. It writes it at the wrong node.
    """

    def _flow_where_only_the_decoy_writes(self) -> dict[str, Any]:
        return self.publish(
            "irrelevant-anchor",
            [
                self.node("gate", "gate"),
                self.node("writer", "declared-writer"),
                self.node("decoy", "decoy-writer"),
            ],
            routes=[
                {"from": "gate", "to": "declared-writer", "outcome": "true"},
                {"from": "gate", "to": "decoy-writer", "outcome": "false"},
            ],
            criteria=[criterion("record-written", "effect", "declared-writer")],
        )

    def test_an_effect_minted_at_a_node_the_criterion_never_pinned_is_irrelevant_and_refuses(
        self,
    ) -> None:
        flow = self._flow_where_only_the_decoy_writes()
        run = self.start(flow, value="decoy", chooser=of_kind_anywhere)

        # The judge really did anchor a real, run-owned, succeeded effect. The
        # only thing wrong with it is where it was minted.
        effects = self.evidence(run["id"])["candidates"]["effects"]
        self.assertEqual([item["node_id"] for item in effects], ["decoy-writer"])
        self.assertEqual(self.effects(), ["decoy-evidence"])
        self.assertEqual(
            self.client.questions[0]["run_evidence"]["effects"][0]["site"],
            "decoy-writer",
        )

        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["error_code"], "completion_unevidenced")
        self.assert_never_completed(run)

        resolved = self.resolution(
            self.sole_completion_event(run), "record-written"
        )
        self.assertEqual(resolved["surviving"], [])
        self.assertEqual(
            [item["refusal"] for item in resolved["discarded"]],
            [ANCHOR_NODE_MISMATCH],
        )
        self.assertEqual(
            [item["anchor_id"] for item in resolved["discarded"]], [effects[0]["id"]]
        )

    def test_the_same_effect_minted_at_the_declared_site_admits(self) -> None:
        """Proves the refusal above is about the site and about nothing else."""

        flow = self._flow_where_only_the_decoy_writes()
        run = self.start(flow, value="declared", chooser=of_kind_anywhere)
        self.assertEqual(run["status"], "completed")
        self.assertEqual(self.effects(), ["declared-evidence"])
        self.assertEqual(
            self.sole_completion_event(run)["type"], "completion.admitted"
        )


class ForeignRunAnchorTest(StopSeamRuntimeCase):
    """Evidence is run-owned: a completion claim may not be carried by another Run.

    Two Runs of one Flow in one workspace, and the second Run's judge cites the
    first Run's receipt. The record is real, succeeded, and minted at the very
    node the criterion pins — every property except ownership is satisfied — so
    the only thing that can refuse it is the ownership of the evidence.
    """

    def _flow(self) -> dict[str, Any]:
        return self.publish(
            "cross-run",
            [self.node("quiet", "work")],
            criteria=[criterion("work-succeeded", "receipt", "work")],
        )

    def _first_run_receipt(self, flow: Mapping[str, Any]) -> tuple[str, str]:
        first = self.start(
            flow, value="declared", chooser=honestly, idempotency_key="first"
        )
        self.assertEqual(first["status"], "completed")
        receipts = self.evidence(first["id"])["candidates"]["receipts"]
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0]["state"], "succeeded")
        self.assertEqual(receipts[0]["node_id"], "work")
        return str(first["id"]), str(receipts[0]["id"])

    def test_a_second_run_cannot_complete_on_the_first_runs_receipt(self) -> None:
        flow = self._flow()
        first_run_id, borrowed = self._first_run_receipt(flow)

        second = self.start(
            flow,
            value="declared",
            chooser=anchoring(borrowed),
            idempotency_key="second",
        )

        self.assertNotEqual(second["id"], first_run_id)
        self.assertEqual(second["status"], "failed")
        self.assert_never_completed(second)
        # The seam's anti-fabrication gate fires first: the borrowed id is not in
        # the candidate set code offered, so the claim is a broken contract
        # rather than a weak one. Recorded here as the *observed* refusal, not as
        # the only one that should be possible — see the ownership test below.
        self.assertEqual(second["error_code"], "contract_violation")

        # Whichever gate refused, the borrowed receipt never carried the claim.
        first = self.plane.get_studio_run(self.workspace_id, first_run_id)
        self.assertEqual(first["status"], "completed")
        self.assertTrue(verify_event_chain(second["events"]))

    def _recorded_evidence_calls(
        self, flow: Mapping[str, Any], *, chooser: Chooser, idempotency_key: str
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Run one adjudication, recording every evidence fetch the seam makes.

        The store call is wrapped rather than replaced, so the Run executes
        against real evidence and the recording observes the seam instead of
        standing in for it.
        """

        original = self.plane.studio.adjudication_evidence
        calls: list[dict[str, Any]] = []

        def recording(
            workspace_id: str, run_id: str, *, anchor_ids: Sequence[str] = ()
        ) -> dict[str, Any]:
            result = original(workspace_id, run_id, anchor_ids=anchor_ids)
            calls.append(
                {"run_id": run_id, "anchor_ids": list(anchor_ids), "result": result}
            )
            return result

        with mock.patch.object(
            self.plane.studio, "adjudication_evidence", recording
        ):
            run = self.start(
                flow,
                value="declared",
                chooser=chooser,
                idempotency_key=idempotency_key,
            )
        return run, calls

    def test_the_seam_passes_the_claimed_anchor_ids_to_a_second_lookup(self) -> None:
        """The ownership check has to be able to *see* the record it rejects.

        `StudioStore.adjudication_evidence` takes `anchor_ids` precisely so a
        claimed id can be resolved workspace-wide and be named foreign rather
        than vanish into `anchor_unresolvable`. A seam that fetched evidence only
        Run-scoped would make ownership true by construction: every record in the
        bundle would belong to this Run, `anchor_foreign_run` could never fire,
        and deleting the ownership check would break no test. That is not a
        hypothetical — it is the defect this suite found, and this test is what
        would have caught it.

        So the assertion is on the seam's two fetches: the first Run-scoped,
        because that is what the judge may speak about, and the second carrying
        exactly the ids the judge claimed. It fails if the seam stops passing
        them, which is the whole reason it exists.

        The admitted path is used deliberately. A borrowed anchor never reaches
        the second fetch, because the anti-fabrication gate refuses it first — so
        asserting this on a refusal would only ever prove that the second fetch
        is unreachable. The composed behaviour, gate one removed and a borrowed
        anchor named `anchor_foreign_run` through the seam, is measured in
        `tests/test_guard_ablation.py`.
        """

        flow = self._flow()
        run, calls = self._recorded_evidence_calls(
            flow, chooser=honestly, idempotency_key="admitted"
        )

        self.assertEqual(run["status"], "completed")
        self.assertEqual(
            len(calls), 2, "one adjudication fetches evidence exactly twice"
        )
        offered, resolved = calls
        self.assertEqual([call["run_id"] for call in calls], [run["id"]] * 2)

        # First fetch: Run-scoped, and nothing else. This is what the judge is
        # shown, so widening it would let a judge anchor another Run's work.
        self.assertEqual(offered["anchor_ids"], [])

        # Second fetch: exactly the ids the judge claimed, verbatim.
        anchored = self.resolution(
            self.sole_completion_event(run), "work-succeeded"
        )["surviving"]
        self.assertTrue(anchored)
        self.assertEqual(
            resolved["anchor_ids"],
            anchored,
            "the resolver is handed a lookup over what was actually claimed",
        )

        # Widening the lookup must not widen what the judge was offered.
        self.assertEqual(
            offered["result"]["candidates"], resolved["result"]["candidates"]
        )
        # For an own-Run anchor the two lookups agree, which is the point: the
        # second fetch costs the happy path no correctness, and buys the ability
        # to diagnose the unhappy one.
        self.assertEqual(
            offered["result"]["records"], resolved["result"]["records"]
        )

    def test_that_second_lookup_names_a_borrowed_anchor_foreign_rather_than_missing(
        self,
    ) -> None:
        """The lookup the seam now performs, fed the borrowed id it would carry.

        Run-scoped, the borrowed receipt is simply absent and the resolver can
        say only that it resolves to nothing. Anchor-aware, the same receipt
        arrives carrying the Run that actually owns it, and the refusal is named
        for what is wrong with it. The refusal is the product here, so the
        difference between `anchor_unresolvable` and `anchor_foreign_run` is a
        difference in what the ledger tells a reader.
        """

        flow = self._flow()
        _, borrowed = self._first_run_receipt(flow)
        second = self.start(
            flow,
            value="declared",
            chooser=anchoring(borrowed),
            idempotency_key="second",
        )
        declared = [criterion("work-succeeded", "receipt", "work")]
        claimed = {"work-succeeded": [borrowed]}

        anchor_aware = self.evidence(second["id"], anchor_ids=[borrowed])
        self.assertNotIn(
            borrowed,
            [item["id"] for item in anchor_aware["candidates"]["receipts"]],
            "the borrowed receipt is not this Run's own evidence",
        )
        foreign = next(
            item
            for item in anchor_aware["records"]["receipts"]
            if item["id"] == borrowed
        )
        self.assertNotEqual(foreign["run_id"], second["id"])

        decision = adjudicate(
            declared,
            claimed,
            StudioRuntime._evidence_bundle(second["id"], anchor_aware["records"]),
        )
        self.assertFalse(decision.admitted)
        self.assertEqual(
            [item.refusal for item in decision.resolutions[0].discarded],
            [ANCHOR_FOREIGN_RUN],
        )

        # The Run-scoped lookup, for contrast: same refusal, and it can only
        # report that the record is missing, because from there it is.
        run_scoped = adjudicate(
            declared,
            claimed,
            StudioRuntime._evidence_bundle(
                second["id"], self.evidence(second["id"])["records"]
            ),
        )
        self.assertFalse(run_scoped.admitted)
        self.assertEqual(
            [item.refusal for item in run_scoped.resolutions[0].discarded],
            [ANCHOR_UNRESOLVABLE],
        )


# ---------------------------------------------------------------------------
# 8 — a judge cannot hedge its way to an admission.
# ---------------------------------------------------------------------------


class HedgedJudgementTest(StopSeamRuntimeCase):
    """Refusing in prose while anchoring in data must refuse, not admit.

    `unevidenced: true` alongside anchors is the shape a miscalibrated or
    compromised judge most plausibly produces, because it is the one shape where
    the two halves of its own answer disagree. The seam resolves that
    disagreement in the safe direction unconditionally: the judge may always
    refuse, and its anchors are discarded before they are ever resolved.
    """

    def _flow(self) -> dict[str, Any]:
        return self.publish(
            "hedged-judgement",
            [self.node("writer", "writer")],
            criteria=[criterion("record-written", "effect", "writer")],
        )

    def test_marking_a_criterion_unevidenced_discards_its_anchors_entirely(
        self,
    ) -> None:
        flow = self._flow()
        run = self.start(
            flow, value="declared", chooser=honestly, unevidenced=True
        )

        # The judge really did supply an admissible anchor in the same breath.
        judged = self.client.questions[0]
        self.assertTrue(judged["run_evidence"]["effects"])

        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["error_code"], "completion_unevidenced")
        self.assert_never_completed(run)

        refused = self.sole_completion_event(run)
        self.assertEqual(refused["type"], "completion.refused")
        self.assertEqual(refused["payload"]["unevidenced"], ["record-written"])
        resolved = self.resolution(refused, "record-written")
        self.assertEqual(resolved["surviving"], [])
        self.assertEqual(
            resolved["discarded"],
            [],
            "the anchors were dropped before resolution, not refused by it",
        )

    def test_the_identical_anchors_admit_when_the_judge_does_not_hedge(self) -> None:
        """Proves the refusal is the hedge and not an unsatisfiable fixture."""

        flow = self._flow()
        run = self.start(flow, value="declared", chooser=honestly, unevidenced=False)
        self.assertEqual(run["status"], "completed")
        admitted = self.sole_completion_event(run)
        self.assertEqual(admitted["type"], "completion.admitted")
        self.assertTrue(self.resolution(admitted, "record-written")["surviving"])


if __name__ == "__main__":
    unittest.main()
