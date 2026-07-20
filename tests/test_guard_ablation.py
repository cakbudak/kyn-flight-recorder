"""Guard ablation suite — proof that each guard is the *reason* the system holds.

A passing test proves a system works. It does not prove *why*. This suite takes
each documented guard, disables exactly that guard inside an isolated harness,
and asserts that one specific **product-level** violation becomes reachable: an
effect committed that should not be, an unauthorized Action executed, a terminal
Run mutated, a stale proposal applied, a doctored ledger accepted, a refused Run
executing. Every assertion is about durable product state — Run rows, statuses,
Action receipts, sandbox effects, diagnosis rows, published versions — never
about the patched function's return value. A guard whose ablation changes
nothing is decorative, and this suite says so out loud.

Each guard follows one pattern:

1. BASELINE — with the guard intact, attempt the violation and prove it is
   prevented, in product state.
2. ABLATED — remove exactly that guard, attempt the same violation, prove it now
   lands, in product state.

Both halves run in every test, so the test proves the guard is the reason for the
baseline outcome rather than merely observing a passing baseline.

Safety
------
Ablation is **test-local only**. It is performed by recompiling the product
function's own source with one guard expression deleted and binding the result
over the class for the duration of a `with` block, or by dropping a trigger on a
throwaway SQLite file. No ablation path exists in `serve.py`, the HTTP API, or
any runtime module: a public deployment contains no way to turn off its own
authority gate. `_ablate` refuses to run if the guard text it expects is not
present exactly once, so a moved or rewritten guard fails loudly instead of
silently ablating nothing.
"""

from __future__ import annotations

import copy
import inspect
import json
import sys
import tempfile
import textwrap
import unittest
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence
from unittest import mock

from backend import contracts, stop_seam
from backend.contracts import (
    BrakeEngaged,
    Conflict,
    ContractViolation,
    verify_event_chain,
)
from backend.service import ControlPlane
from backend.store import Store
from backend.studio_runtime import StudioRuntime
from backend.studio_store import StudioStore


# ---------------------------------------------------------------------------
# Result registry — consumed by scripts/ablation_verify.py to render the table.
# ---------------------------------------------------------------------------


@dataclass
class GuardOutcome:
    """One ablation experiment, in the words a reader needs to judge it."""

    guard: str
    site: str
    violation: str
    baseline: str
    ablated: str
    load_bearing: bool
    #: A redundancy probe deliberately expects the violation to stay prevented,
    #: because a *different* guard also covers it. It is reported, not failed.
    redundancy_probe: bool = False
    note: str = ""


OUTCOMES: list[GuardOutcome] = []

#: Reading order for the report. Test execution order is alphabetical by class
#: and carries no meaning; this is the order a reader should meet the guards in.
GUARD_ORDER: tuple[str, ...] = (
    "Skill authority intersection",
    "Tool-call budget",
    "Evidence citation subset",
    "Repair revision fence",
    "Terminal absorption trigger (alone)",
    "Terminal absorption (database status triggers)",
    "Event hash chain",
    "Ratification brake",
    "Goal-Judge anti-fabrication gate",
    "Anchor site check",
    "Anchor state check",
)


def record(outcome: GuardOutcome) -> None:
    OUTCOMES.append(outcome)


def ordered_outcomes() -> list[GuardOutcome]:
    def key(outcome: GuardOutcome) -> int:
        try:
            return GUARD_ORDER.index(outcome.guard)
        except ValueError:  # a new guard nobody added to the reading order
            return len(GUARD_ORDER)

    return sorted(OUTCOMES, key=key)


# ---------------------------------------------------------------------------
# The ablation mechanism.
# ---------------------------------------------------------------------------


def _ablate(
    function: Callable[..., Any],
    edits: Sequence[tuple[str, str]],
    *,
    inject: dict[str, Any] | None = None,
) -> Callable[..., Any]:
    """Recompile a product function with exactly the named guard text removed.

    The ablated function is the product's own source minus one expression, so an
    ablation cannot accidentally become a hand-written stub that proves nothing.
    Every edit must match exactly once; anything else means the guard site moved
    and the experiment is no longer describing the shipped code.
    """

    module = sys.modules[function.__module__]
    raw = inspect.getsource(function)
    first_line = raw.split("\n", 1)[0]
    indent = len(first_line) - len(first_line.lstrip())

    def shift(text: str) -> str:
        """Re-indent guard text to match the dedented function body."""

        prefix = " " * indent
        return "".join(
            line[indent:] if line.startswith(prefix) else line
            for line in text.splitlines(keepends=True)
        )

    source = "from __future__ import annotations\n" + textwrap.dedent(raw)
    for raw_old, raw_new in edits:
        old, new = shift(raw_old), shift(raw_new)
        found = source.count(old)
        if found != 1:
            raise AssertionError(
                f"ablation of {function.__qualname__} expected exactly one "
                f"occurrence of {old!r} but found {found}. The guard site moved — "
                "re-verify it before trusting this suite."
            )
        source = source.replace(old, new)
    namespace: dict[str, Any] = dict(vars(module))
    namespace.update(inject or {})
    exec(compile(source, f"<ablation:{function.__qualname__}>", "exec"), namespace)  # noqa: S102 - repository-owned source
    return namespace[function.__name__]


@contextmanager
def ablated(
    owner: Any,
    attribute: str,
    edits: Sequence[tuple[str, str]],
    *,
    inject: dict[str, Any] | None = None,
) -> Iterator[None]:
    """Bind the ablated function over the product one for this block only."""

    replacement = _ablate(getattr(owner, attribute), edits, inject=inject)
    with mock.patch.object(owner, attribute, replacement):
        yield


# ---------------------------------------------------------------------------
# Schemas and deterministic model seams.
# ---------------------------------------------------------------------------


VALUE_SCHEMA = {
    "type": "object",
    "properties": {"value": {"type": "string"}},
    "required": ["value"],
    "additionalProperties": False,
}
TEXT_SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
    "additionalProperties": False,
}
EFFECT_SCHEMA = {
    "type": "object",
    "properties": {"effect_id": {"type": "string"}, "collection": {"type": "string"}},
    "required": ["effect_id", "collection"],
    "additionalProperties": False,
}
BRIEF_SCHEMA = {
    "type": "object",
    "properties": {"brief": {"type": "string", "minLength": 12, "maxLength": 2000}},
    "required": ["brief"],
    "additionalProperties": False,
}
SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
    "additionalProperties": False,
}
CONDITION_SCHEMA = {
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


class NoModelClient:
    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        del payload
        raise AssertionError("this ablation must not reach a model")


@dataclass
class ScriptedResponsesClient:
    """A provider seam that emits a scripted sequence of tool turns.

    `tool_calls` is the exact list of `(name, arguments)` the model asks for, one
    per turn. Once exhausted it returns the strict final object. The seam never
    consults `tool_choice`, which is the point: authority and budget must be
    enforced by Kyn, not by the model's cooperation.
    """

    tool_calls: list[tuple[str, dict[str, Any]]]
    final: dict[str, Any] = field(default_factory=lambda: {"summary": "Bounded."})
    requests: list[dict[str, Any]] = field(default_factory=list)

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(json.loads(json.dumps(payload)))
        index = len(self.requests) - 1
        base = {
            "id": f"resp_{index}",
            "status": "completed",
            "model": "gpt-5.6",
            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        }
        if index < len(self.tool_calls):
            name, arguments = self.tool_calls[index]
            base["output"] = [
                {
                    "id": f"fc_{index}",
                    "type": "function_call",
                    "call_id": f"call_{index}",
                    "name": name,
                    "arguments": json.dumps(arguments),
                    "status": "completed",
                }
            ]
            return base
        base["output"] = [
            {
                "id": f"msg_{index}",
                "type": "message",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(self.final),
                        "annotations": [],
                    }
                ],
            }
        ]
        return base


@dataclass
class ScriptedGoalJudgeClient:
    """A Goal-Judge that anchors whatever the experiment tells it to anchor.

    `chooser(criterion, run_evidence)` returns the anchor ids the judge claims.
    The seam never consults the judge's honesty, which is the point: a completion
    must be admitted by resolved evidence, not by the judge's cooperation.
    """

    chooser: Callable[[dict[str, Any], dict[str, Any]], Sequence[str]]
    requests: list[dict[str, Any]] = field(default_factory=list)

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(json.loads(json.dumps(payload)))
        if payload.get("metadata", {}).get("operation") != "adjudication":
            raise AssertionError("this experiment makes no model call but the adjudication")
        question = json.loads(payload["input"][0]["content"])
        verdict = {
            "assessment": "A scripted adjudication replayed by the ablation suite.",
            "criteria": [
                {
                    "criterion_id": item["criterion_id"],
                    "unevidenced": False,
                    "anchors": list(self.chooser(item, question["run_evidence"])),
                    "reason": "A scripted judgement pinned by the experiment.",
                }
                for item in question["acceptance_criteria"]
            ],
        }
        return {
            "id": f"resp_judge_{len(self.requests)}",
            "status": "completed",
            "model": "gpt-5.6",
            "usage": {"input_tokens": 20, "output_tokens": 12, "total_tokens": 32},
            "output": [
                {
                    "id": f"msg_judge_{len(self.requests)}",
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(verdict),
                            "annotations": [],
                        }
                    ],
                }
            ],
        }


def anchors_of_kind_anywhere(
    criterion: dict[str, Any], evidence: dict[str, Any]
) -> list[str]:
    """Site-blind: every record of the declared kind, wherever it was minted."""

    return [
        record["id"]
        for records in evidence.values()
        for record in records
        if record["kind"] == criterion["evidence_kind"]
    ]


def anchors_at_site_in_any_state(
    criterion: dict[str, Any], evidence: dict[str, Any]
) -> list[str]:
    """State-blind: every record of the declared kind at a declared site."""

    return [
        record["id"]
        for records in evidence.values()
        for record in records
        if record["kind"] == criterion["evidence_kind"]
        and record["site"] in criterion["declared_sites"]
    ]


def anchors_fabricated(
    criterion: dict[str, Any], evidence: dict[str, Any]
) -> list[str]:
    """An id no record in this workspace carries. Text, not evidence."""

    del criterion, evidence
    return ["astep_" + "0" * 32]


@dataclass
class DiagnosticianClient:
    """A diagnostician that cites whatever event id it is told to cite."""

    evidence_event_ids: list[str]
    requests: list[dict[str, Any]] = field(default_factory=list)

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(json.loads(json.dumps(payload)))
        body = {
            "root_cause": "The pinned Data Store Action denies its own bounded write.",
            "explanation": (
                "The cited evidence records the denied invocation and the absent effect."
            ),
            "confidence": 0.9,
            "evidence_event_ids": list(self.evidence_event_ids),
        }
        return {
            "id": "resp_diag",
            "status": "completed",
            "model": "gpt-5.6",
            "usage": {"input_tokens": 12, "output_tokens": 8, "total_tokens": 20},
            "output": [
                {
                    "id": "msg_diag",
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(body),
                            "annotations": [],
                        }
                    ],
                }
            ],
        }


# ---------------------------------------------------------------------------
# Fixtures. Every half of every experiment gets its own database, so a baseline
# can never leak state into the ablated half or the other way round.
# ---------------------------------------------------------------------------


class Harness:
    """One isolated workspace on one throwaway SQLite file."""

    def __init__(self, directory: Path, name: str, *, seed: bool = False) -> None:
        self.store = Store(directory / f"{name}.sqlite3")
        self.store.initialize()
        self.plane = ControlPlane(self.store, NoModelClient())
        self.workspace_id = self.plane.create_workspace(seed=seed)["workspace_id"]

    # -- deterministic building blocks --------------------------------------

    def sandbox_action(
        self, *, slug: str, collection: str, write_enabled: bool = True
    ) -> dict[str, Any]:
        config: dict[str, Any] = {"operation": "append_record", "collection": collection}
        if not write_enabled:
            config["write_enabled"] = False
        return self.plane.create_action(
            self.workspace_id,
            name=slug.replace("-", " ").title(),
            slug=slug,
            description="A bounded sandbox collection write used by the ablation suite.",
            kind="sandbox" if write_enabled else "data_store",
            input_schema=VALUE_SCHEMA,
            output_schema=EFFECT_SCHEMA,
            config=config,
            agent_version_id=None,
        )

    def template_action(self, *, slug: str, template: str) -> dict[str, Any]:
        return self.plane.create_action(
            self.workspace_id,
            name=slug.replace("-", " ").title(),
            slug=slug,
            description="A deterministic template Action used by the ablation suite.",
            kind="template",
            input_schema=VALUE_SCHEMA,
            output_schema=TEXT_SCHEMA,
            config={"template": template},
            agent_version_id=None,
        )

    def agent(
        self,
        *,
        slug: str,
        role: str = "executor",
        granted_action_version_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        prompt = self.plane.create_prompt(
            self.workspace_id,
            name=f"{slug} prompt",
            slug=f"{slug}-prompt",
            template="Handle {{brief}} within the pinned grant.",
            variables=["brief"],
        )
        skill = self.plane.create_skill(
            self.workspace_id,
            name=f"{slug} skill",
            slug=f"{slug}-skill",
            instructions="Use only the Actions this Skill grants.",
            allowed_tools=[],
            allowed_action_version_ids=granted_action_version_ids or [],
        )
        return self.plane.create_agent(
            self.workspace_id,
            name=f"{slug} agent",
            slug=slug,
            role=role,
            model="gpt-5.6",
            instructions="Return contract-bound output.",
            prompt_version_id=prompt["version"]["id"],
            skill_version_ids=[skill["version"]["id"]],
        )

    def ai_flow(
        self, *, agent_version_id: str, max_tool_calls: int
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        action = self.plane.create_action(
            self.workspace_id,
            name="Bounded analysis",
            slug="bounded-analysis",
            description="Runs a pinned Agent whose authority is the pinned Skill grant.",
            kind="ai",
            input_schema=BRIEF_SCHEMA,
            output_schema=SUMMARY_SCHEMA,
            config={"max_tool_calls": max_tool_calls, "reasoning_effort": "medium"},
            agent_version_id=agent_version_id,
        )
        flow = self.plane.create_studio_flow(
            self.workspace_id,
            name="Bounded analysis flow",
            slug="bounded-analysis-flow",
            description="One AI node whose authority comes only from its pinned Skill.",
            input_schema=BRIEF_SCHEMA,
            start_node_id="analyze",
            nodes=[
                {
                    "id": "analyze",
                    "type": "action",
                    "version_id": action["version"]["id"],
                    "input_mapping": {"brief": {"source": "input", "path": "brief"}},
                    "position": {"x": 120, "y": 120},
                }
            ],
            routes=[],
        )
        return action, flow

    def denied_delivery_flow(self) -> tuple[dict[str, Any], dict[str, Any]]:
        action = self.sandbox_action(
            slug="denied-delivery-store",
            collection="denied-deliveries",
            write_enabled=False,
        )
        flow = self.plane.create_studio_flow(
            self.workspace_id,
            name="Repeatable denial",
            slug="repeatable-denial",
            description="A policy-blocked Flow used by the ablation suite.",
            input_schema=VALUE_SCHEMA,
            start_node_id="deliver",
            nodes=[
                {
                    "id": "deliver",
                    "type": "action",
                    "version_id": action["version"]["id"],
                    "input_mapping": {"value": {"source": "input", "path": "value"}},
                    "position": {"x": 280, "y": 220},
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
        return action, flow

    def completed_flow(self) -> dict[str, Any]:
        action = self.template_action(slug="greeter", template="Hello {{value}}")
        return self.plane.create_studio_flow(
            self.workspace_id,
            name="Deterministic greeting",
            slug="deterministic-greeting",
            description="A one-node Flow that always completes.",
            input_schema=VALUE_SCHEMA,
            start_node_id="greet",
            nodes=[
                {
                    "id": "greet",
                    "type": "action",
                    "version_id": action["version"]["id"],
                    "input_mapping": {"value": {"source": "input", "path": "value"}},
                    "position": {"x": 100, "y": 100},
                }
            ],
            routes=[],
        )

    # -- stop-seam building blocks ------------------------------------------

    def condition_action(self, *, slug: str, matches: str) -> dict[str, Any]:
        """A branch point, so a Flow can route *away* from a criterion's site."""

        return self.plane.create_action(
            self.workspace_id,
            name=slug.replace("-", " ").title(),
            slug=slug,
            description="A deterministic branch used by the ablation suite.",
            kind="condition",
            input_schema=VALUE_SCHEMA,
            output_schema=CONDITION_SCHEMA,
            outcomes=copy.deepcopy(CONDITION_OUTCOMES),
            config={"path": "value", "operator": "equals", "value": matches},
            agent_version_id=None,
        )

    def failing_action(self, *, slug: str) -> dict[str, Any]:
        """Reads a path its validated input never carries, so the Step fails.

        The Step is minted, runs, and finishes `failed`; the receipt is minted
        `failed` rather than merely absent. That is exactly the record a state
        check exists to refuse.
        """

        return self.plane.create_action(
            self.workspace_id,
            name=slug.replace("-", " ").title(),
            slug=slug,
            description="An Action that always fails inside its own executor.",
            kind="transform",
            input_schema=VALUE_SCHEMA,
            output_schema=TEXT_SCHEMA,
            config={
                "operation": "map",
                "mappings": {"text": {"source": "input", "path": "absent"}},
            },
            agent_version_id=None,
        )

    def judge_agent(self, *, slug: str = "stop-seam-judge") -> str:
        prompt = self.plane.create_prompt(
            self.workspace_id,
            name=f"{slug} prompt",
            slug=f"{slug}-prompt",
            template="Adjudicate the completion claim against the supplied evidence.",
            variables=[],
        )
        agent = self.plane.create_agent(
            self.workspace_id,
            name=f"{slug} agent",
            slug=slug,
            role="executor",
            model="gpt-5.6",
            instructions="Judge completion claims against supplied Run evidence only.",
            prompt_version_id=prompt["version"]["id"],
            skill_version_ids=[],
        )
        return str(agent["version"]["id"])

    @staticmethod
    def action_node(
        node_id: str, version_id: str, *, settings: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return {
            "id": node_id,
            "type": "action",
            "version_id": version_id,
            "input_mapping": dict(VALUE_MAPPING),
            "settings": dict(settings or NO_RETRY),
        }

    def contracted_flow(
        self,
        *,
        slug: str,
        nodes: list[dict[str, Any]],
        routes: list[dict[str, Any]],
        criteria: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self.plane.create_studio_flow(
            self.workspace_id,
            name=slug.replace("-", " ").title(),
            slug=slug,
            description="A Flow declaring an acceptance contract over its own work.",
            input_schema=VALUE_SCHEMA,
            start_node_id=nodes[0]["id"],
            nodes=nodes,
            routes=routes,
            acceptance_criteria=criteria,
            judge_agent_version_id=self.judge_agent(),
        )

    # -- observation --------------------------------------------------------

    def status_history(self, run_id: str) -> list[str]:
        """Every status a Run was ever in, read off its append-only ledger."""

        run = self.plane.get_studio_run(self.workspace_id, run_id)
        return [
            event["payload"]["to"]
            for event in run["events"]
            if event["type"] == "run.status_changed"
        ]

    @staticmethod
    def completion_event(run: dict[str, Any]) -> dict[str, Any] | None:
        for event in run["events"]:
            if event["type"].startswith("completion."):
                return event
        return None

    def effects(self) -> list[dict[str, Any]]:
        with self.store.read() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM automation_effects ORDER BY created_at, id"
                )
            ]

    def receipts(self) -> list[dict[str, Any]]:
        with self.store.read() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM automation_action_receipts ORDER BY created_at, id"
                )
            ]

    def runs(self) -> list[dict[str, Any]]:
        with self.store.read() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM automation_runs ORDER BY created_at, id"
                )
            ]

    def count(self, table: str) -> int:
        with self.store.read() as connection:
            return int(
                connection.execute(f"SELECT COUNT(*) AS total FROM {table}").fetchone()[
                    "total"
                ]
            )


class AblationCase(unittest.TestCase):
    """Base class handing out isolated harnesses."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self._harness_count = 0

    def harness(self, *, seed: bool = False) -> Harness:
        self._harness_count += 1
        return Harness(self.directory, f"h{self._harness_count}", seed=seed)


# ---------------------------------------------------------------------------
# Guard 1 — Skill authority intersection.
# ---------------------------------------------------------------------------


AUTHORITY_EDITS = (
    ("                    or name not in tool_by_name\n", ""),
    (
        "                    action=tool_by_name[name],",
        "                    action=_ablation_resolve_any_action(self, workspace_id, name),",
    ),
)


def _ablation_resolve_any_action(
    runtime: StudioRuntime, workspace_id: str, name: str
) -> dict[str, Any]:
    """Dispatch by model-supplied name against the whole workspace catalogue.

    This is what the guard's absence actually means: the name the model emitted
    is resolved from everything that exists rather than from the pinned Skill
    grant. Nothing here relaxes an Action's own contract — the ungranted Action
    still validates its input and output schemas — only the authority
    intersection is gone.
    """

    with runtime.repository.store.read() as connection:
        row = connection.execute(
            """
            SELECT av.id
            FROM action_versions av
            JOIN actions a ON a.id = av.action_id AND a.current_version = av.version
            WHERE av.workspace_id = ? AND a.slug = ?
            """,
            (workspace_id, name),
        ).fetchone()
    if row is None:
        raise ContractViolation("Agent requested an Action that does not exist")
    return runtime.repository.get_action_version(workspace_id, row["id"])


class SkillAuthorityIntersectionAblation(AblationCase):
    """The pinned Skill grant is the only source of model Action authority."""

    UNGRANTED_SLUG = "public-broadcast-store"
    UNGRANTED_COLLECTION = "public-broadcasts"

    def _fixture(self) -> tuple[Harness, dict[str, Any], ScriptedResponsesClient]:
        harness = self.harness()
        granted = harness.template_action(
            slug="granted-note", template="Noted {{value}}"
        )
        harness.sandbox_action(
            slug=self.UNGRANTED_SLUG, collection=self.UNGRANTED_COLLECTION
        )
        agent = harness.agent(
            slug="bounded-executor",
            granted_action_version_ids=[granted["version"]["id"]],
        )
        _, flow = harness.ai_flow(
            agent_version_id=agent["version"]["id"], max_tool_calls=2
        )
        client = ScriptedResponsesClient(
            tool_calls=[(self.UNGRANTED_SLUG, {"value": "exfiltrated-payload"})]
        )
        return harness, flow, client

    def _attempt(
        self, harness: Harness, flow: dict[str, Any], client: ScriptedResponsesClient
    ) -> dict[str, Any]:
        return harness.plane.start_studio_run(
            harness.workspace_id,
            flow["id"],
            input_data={"brief": "Summarize the bounded launch brief for review."},
            client=client,
        )

    def test_pinned_skill_grant_is_the_reason_an_ungranted_action_cannot_run(
        self,
    ) -> None:
        # BASELINE ---------------------------------------------------------
        harness, flow, client = self._fixture()
        baseline_run = self._attempt(harness, flow, client)

        self.assertEqual(baseline_run["status"], "failed")
        self.assertEqual(baseline_run["error_code"], "contract_violation")
        self.assertEqual(
            harness.effects(),
            [],
            "an ungranted Action must not commit a sandbox effect",
        )
        self.assertEqual(
            [receipt["outcome"] for receipt in harness.receipts()],
            ["failed"],
            "the only receipt is the AI Action's own refusal",
        )
        self.assertEqual(
            harness.count("automation_effects"),
            0,
            "no effect row exists anywhere in the workspace",
        )
        baseline = (
            f"prevented — Run {baseline_run['status']}/"
            f"{baseline_run['error_code']}, 0 sandbox effects, 0 receipts for the "
            "ungranted Action"
        )

        # ABLATED ----------------------------------------------------------
        harness, flow, client = self._fixture()
        with ablated(
            StudioRuntime,
            "_invoke_ai_action",
            AUTHORITY_EDITS,
            inject={"_ablation_resolve_any_action": _ablation_resolve_any_action},
        ):
            ablated_run = self._attempt(harness, flow, client)

        effects = harness.effects()
        succeeded = [
            receipt
            for receipt in harness.receipts()
            if receipt["outcome"] == "succeeded"
        ]

        self.assertEqual(ablated_run["status"], "completed")
        self.assertEqual(
            len(effects),
            1,
            "the ungranted Action committed a durable sandbox effect",
        )
        self.assertEqual(effects[0]["collection"], self.UNGRANTED_COLLECTION)
        self.assertIn(
            "exfiltrated-payload",
            effects[0]["payload_json"],
            "the model's own arguments were written into the sandbox collection",
        )
        self.assertTrue(
            any(
                receipt["node_id"] == "analyze"
                and json.loads(receipt["output_json"]).get("collection")
                == self.UNGRANTED_COLLECTION
                for receipt in succeeded
            ),
            "a receipt now attests an Action the pinned Skill never granted",
        )
        record(
            GuardOutcome(
                guard="Skill authority intersection",
                site="studio_runtime.StudioRuntime._invoke_ai_action (dispatch name re-check)",
                violation="Model invokes an Action its pinned Skill never granted",
                baseline=baseline,
                ablated=(
                    f"VIOLATED — Run completed, 1 sandbox effect in "
                    f"'{self.UNGRANTED_COLLECTION}', receipt written for the "
                    "ungranted Action"
                ),
                load_bearing=True,
            )
        )


# ---------------------------------------------------------------------------
# Guard 2 — Tool-call budget.
# ---------------------------------------------------------------------------


BUDGET_EDITS = (
    (
        '                payload["tool_choice"] = (\n'
        '                    "auto" if used_tool_calls < max_tool_calls else "none"\n'
        "                )",
        '                payload["tool_choice"] = "auto"',
    ),
    (
        "            if used_tool_calls + len(calls) > max_tool_calls:\n"
        '                raise ContractViolation("Agent exceeded the pinned Action-call budget")\n',
        "",
    ),
)


class ToolCallBudgetAblation(AblationCase):
    """The pinned budget, not the model's restraint, bounds tool turns."""

    BUDGET = 1
    ATTEMPTED_CALLS = 5

    def _fixture(self) -> tuple[Harness, dict[str, Any], ScriptedResponsesClient]:
        harness = self.harness()
        granted = harness.template_action(
            slug="granted-note", template="Noted {{value}}"
        )
        self.granted_version_id = granted["version"]["id"]
        agent = harness.agent(
            slug="bounded-executor",
            granted_action_version_ids=[granted["version"]["id"]],
        )
        _, flow = harness.ai_flow(
            agent_version_id=agent["version"]["id"], max_tool_calls=self.BUDGET
        )
        client = ScriptedResponsesClient(
            tool_calls=[
                ("granted-note", {"value": f"turn-{index}"})
                for index in range(self.ATTEMPTED_CALLS)
            ]
        )
        return harness, flow, client

    def _granted_receipts(self, harness: Harness) -> list[dict[str, Any]]:
        """Receipts for Actions the *model* asked for, not the AI node itself."""

        return [
            receipt
            for receipt in harness.receipts()
            if receipt["outcome"] == "succeeded"
            and receipt["action_version_id"] == self.granted_version_id
        ]

    def _attempt(
        self, harness: Harness, flow: dict[str, Any], client: ScriptedResponsesClient
    ) -> dict[str, Any]:
        return harness.plane.start_studio_run(
            harness.workspace_id,
            flow["id"],
            input_data={"brief": "Summarize the bounded launch brief for review."},
            client=client,
        )

    def test_budget_is_the_reason_tool_turns_stay_bounded(self) -> None:
        # BASELINE ---------------------------------------------------------
        harness, flow, client = self._fixture()
        baseline_run = self._attempt(harness, flow, client)
        baseline_receipts = self._granted_receipts(harness)
        baseline_calls = len(client.requests)

        self.assertEqual(baseline_run["status"], "failed")
        self.assertEqual(baseline_run["error_code"], "contract_violation")
        self.assertLessEqual(
            len(baseline_receipts),
            self.BUDGET,
            "no more model-requested Actions executed than the pinned budget",
        )
        baseline = (
            f"prevented — Run failed at the budget, {len(baseline_receipts)} "
            f"model-requested Action receipt(s) for a budget of {self.BUDGET}, "
            f"{baseline_calls} model turns"
        )

        # ABLATED ----------------------------------------------------------
        harness, flow, client = self._fixture()
        with ablated(StudioRuntime, "_invoke_ai_action", BUDGET_EDITS):
            ablated_run = self._attempt(harness, flow, client)
        ablated_receipts = self._granted_receipts(harness)
        ablated_calls = len(client.requests)

        self.assertEqual(ablated_run["status"], "completed")
        self.assertEqual(
            len(ablated_receipts),
            self.ATTEMPTED_CALLS,
            "every model-requested Action executed, budget notwithstanding",
        )
        self.assertGreater(len(ablated_receipts), self.BUDGET)
        self.assertGreater(ablated_calls, baseline_calls)
        self.assertEqual(
            len(ablated_run["model_calls"]),
            self.ATTEMPTED_CALLS + 1,
            "the Run's durable model-call ledger records every unbounded turn",
        )

        record(
            GuardOutcome(
                guard="Tool-call budget",
                site="studio_runtime.StudioRuntime._invoke_ai_action (tool_choice flip + budget check)",
                violation="Tool turns exceed the pinned per-Action budget",
                baseline=baseline,
                ablated=(
                    f"VIOLATED — Run completed with {len(ablated_receipts)} "
                    f"model-requested Action receipts (budget {self.BUDGET}) and "
                    f"{ablated_calls} model turns"
                ),
                load_bearing=True,
            )
        )


# ---------------------------------------------------------------------------
# Guard 3 — Evidence citation subset check.
# ---------------------------------------------------------------------------


CITATION_EDITS = (
    (
        "            if not set(cited).issubset(set(evidence)):\n"
        "                raise ContractViolation(\n"
        '                    "diagnostician cited evidence outside the code-owned candidate"\n'
        "                )\n",
        "",
    ),
)


class EvidenceCitationSubsetAblation(AblationCase):
    """A diagnosis may cite only the bounded evidence the code offered it."""

    def _fixture(self) -> tuple[Harness, dict[str, Any], dict[str, Any]]:
        harness = self.harness()
        harness.agent(slug="grounded-diagnostician", role="diagnostician")
        _, flow = harness.denied_delivery_flow()
        run = harness.plane.start_studio_run(
            harness.workspace_id, flow["id"], input_data={"value": "release-1"}
        )
        self.assertEqual(run["status"], "blocked")
        return harness, flow, run

    @staticmethod
    def _candidate_types() -> set[str]:
        return {"action.receipted", "step.failed", "step.blocked", "run.status_changed"}

    def _outside_candidate_event(self, run: dict[str, Any]) -> dict[str, Any]:
        """An event of this Run that the code deliberately did not offer."""

        outside = [
            event
            for event in run["events"]
            if event["type"] not in self._candidate_types()
        ]
        self.assertTrue(outside, "the fixture must contain a non-candidate event")
        return outside[0]

    def test_subset_check_is_the_reason_a_diagnosis_stays_inside_its_window(
        self,
    ) -> None:
        # BASELINE ---------------------------------------------------------
        harness, _, run = self._fixture()
        smuggled = self._outside_candidate_event(run)
        client = DiagnosticianClient(evidence_event_ids=[smuggled["id"]])

        with self.assertRaises(ContractViolation):
            harness.plane.diagnose_studio_run(
                harness.workspace_id, run["id"], client=client
            )
        self.assertEqual(
            harness.count("automation_diagnoses"),
            0,
            "no diagnosis row survives a citation outside the bounded candidate",
        )
        self.assertIsNone(
            harness.plane.studio.find_run_diagnosis(harness.workspace_id, run["id"])
        )
        baseline = (
            f"prevented — refused a citation of '{smuggled['type']}', "
            "0 diagnosis rows written"
        )

        # ABLATED ----------------------------------------------------------
        harness, flow, run = self._fixture()
        smuggled = self._outside_candidate_event(run)
        client = DiagnosticianClient(evidence_event_ids=[smuggled["id"]])
        with ablated(ControlPlane, "diagnose_studio_run", CITATION_EDITS):
            diagnosis = harness.plane.diagnose_studio_run(
                harness.workspace_id, run["id"], client=client
            )

        self.assertEqual(harness.count("automation_diagnoses"), 1)
        self.assertEqual(
            diagnosis["evidence_event_ids"],
            [smuggled["id"]],
            "the persisted diagnosis now cites an event the code never offered",
        )
        persisted = harness.plane.studio.find_run_diagnosis(
            harness.workspace_id, run["id"]
        )
        self.assertEqual(persisted["evidence_event_ids"], [smuggled["id"]])

        # Defense in depth: even with this guard gone, `record_diagnosis` still
        # refuses evidence from a *different* Run. That narrower violation is
        # therefore not this guard's to prevent, and the suite says so.
        other = harness.plane.start_studio_run(
            harness.workspace_id, flow["id"], input_data={"value": "release-2"}
        )
        self.assertEqual(other["status"], "blocked")
        foreign = DiagnosticianClient(evidence_event_ids=[run["events"][0]["id"]])
        with ablated(ControlPlane, "diagnose_studio_run", CITATION_EDITS):
            with self.assertRaises(ContractViolation) as caught:
                harness.plane.diagnose_studio_run(
                    harness.workspace_id, other["id"], client=foreign
                )
        self.assertIn("outside its Run", str(caught.exception))
        self.assertEqual(harness.count("automation_diagnoses"), 1)

        record(
            GuardOutcome(
                guard="Evidence citation subset",
                site="service.ControlPlane.diagnose_studio_run (cited ⊆ code-owned candidate)",
                violation="Diagnosis cites evidence the code never offered it",
                baseline=baseline,
                ablated=(
                    f"VIOLATED — diagnosis row persisted citing '{smuggled['type']}', "
                    "an event outside the failed Step's bounded window"
                ),
                load_bearing=True,
                note=(
                    "Cross-Run citation is NOT reachable by ablating this guard alone: "
                    "studio_store.record_diagnosis independently refuses evidence from "
                    "another Run. Asserted above."
                ),
            )
        )


# ---------------------------------------------------------------------------
# Guard 4 — Repair revision fence.
# ---------------------------------------------------------------------------


REVISION_FENCE_EDITS = (
    (
        "                if (\n"
        '                    int(flow["revision"]) != expected_flow_revision\n'
        '                    or int(action["current_version"]) != expected_action_version\n'
        "                ):\n"
        '                    raise Conflict("repair target advanced after proposal")\n',
        "",
    ),
)


class RepairRevisionFenceAblation(AblationCase):
    """An acknowledged repair applies to the definition it was acknowledged against."""

    NODES = [
        {
            "id": "deliver",
            "type": "action",
            "input_mapping": {"value": {"source": "input", "path": "value"}},
            "position": {"x": 280, "y": 220},
            "settings": {
                "max_attempts": 1,
                "backoff_seconds": 0,
                "retry_on": [],
                "on_error": "fail",
            },
        }
    ]

    def _fixture(self) -> tuple[Harness, dict[str, Any], dict[str, Any], dict[str, Any]]:
        """Blocked Run → diagnosis → proposal, then the target moves underneath."""

        harness = self.harness()
        action, flow = harness.denied_delivery_flow()
        run = harness.plane.start_studio_run(
            harness.workspace_id, flow["id"], input_data={"value": "release-1"}
        )
        self.assertEqual(run["status"], "blocked")
        diagnosis = harness.plane.diagnose_studio_run(harness.workspace_id, run["id"])
        proposal = harness.plane.propose_studio_repair(
            harness.workspace_id, diagnosis["id"]
        )
        self.assertEqual(proposal["expected_flow_revision"], 1)

        # A second author publishes a successor Flow version after the operator
        # saw the proposal. The diagnosed policy still holds and the computed
        # patch is unchanged, so only the revision fence stands between a stale
        # acknowledgement and a live publish.
        node = dict(self.NODES[0])
        node["version_id"] = action["version"]["id"]
        revised = harness.plane.revise_studio_flow(
            harness.workspace_id,
            flow["id"],
            expected_revision=1,
            name="Repeatable denial",
            description="A colleague relaid this Flow after the proposal was shown.",
            input_schema=VALUE_SCHEMA,
            start_node_id="deliver",
            nodes=[{**node, "position": {"x": 320, "y": 260}}],
            routes=[],
        )
        self.assertEqual(revised["revision"], 2)
        self.assertEqual(revised["current_version"], 2)
        return harness, action, flow, proposal

    def _apply(self, harness: Harness, proposal: dict[str, Any]) -> dict[str, Any]:
        return harness.plane.apply_studio_repair(
            harness.workspace_id,
            proposal["id"],
            proposal_hash=proposal["proposal_hash"],
            expected_flow_revision=proposal["expected_flow_revision"],
            expected_action_version=proposal["expected_action_version"],
            actor="workflow-operator",
            reason="The cited denial proves the missing bounded write authority.",
            acknowledged=True,
        )

    def _proposal_status(self, harness: Harness, proposal_id: str) -> str:
        with harness.store.read() as connection:
            return connection.execute(
                "SELECT status FROM automation_repair_proposals WHERE id = ?",
                (proposal_id,),
            ).fetchone()["status"]

    def _rerun_still_fails(self, harness: Harness, flow: dict[str, Any]) -> str:
        """What the Flow actually does after the repair claims to have applied."""

        run = harness.plane.start_studio_run(
            harness.workspace_id, flow["id"], input_data={"value": "release-2"}
        )
        return run["status"]

    def test_revision_fence_is_the_reason_a_stale_proposal_cannot_apply(self) -> None:
        # BASELINE ---------------------------------------------------------
        harness, _, flow, proposal = self._fixture()
        flow_versions_before = harness.count("automation_flow_versions")

        with self.assertRaises(Conflict) as caught:
            self._apply(harness, proposal)

        self.assertIn("advanced after proposal", str(caught.exception))
        self.assertEqual(
            harness.count("automation_flow_versions"), flow_versions_before
        )
        self.assertEqual(harness.count("automation_repair_decisions"), 0)
        self.assertEqual(self._proposal_status(harness, proposal["id"]), "proposed")
        baseline = (
            "prevented — Conflict, 0 successor versions published, "
            "proposal still 'proposed', operator must re-propose"
        )

        # ABLATED ----------------------------------------------------------
        harness, _, flow, proposal = self._fixture()
        flow_versions_before = harness.count("automation_flow_versions")

        with ablated(StudioStore, "apply_repair", REVISION_FENCE_EDITS):
            applied = self._apply(harness, proposal)

        self.assertEqual(applied["status"], "applied")
        self.assertEqual(self._proposal_status(harness, proposal["id"]), "applied")
        self.assertEqual(harness.count("automation_repair_decisions"), 1)
        self.assertEqual(
            harness.count("automation_flow_versions"), flow_versions_before + 1
        )

        # The proposal was fenced to Flow revision 1; it applied on top of the
        # colleague's revision 2. The successor Flow version was written, but the
        # Flow row's own compare-and-swap targeted the stale revision, so the
        # Flow still points at the unrepaired version.
        live = harness.plane.get_studio_flow(harness.workspace_id, flow["id"])
        with harness.store.read() as connection:
            highest = int(
                connection.execute(
                    "SELECT MAX(version) AS top FROM automation_flow_versions "
                    "WHERE flow_id = ?",
                    (flow["id"],),
                ).fetchone()["top"]
            )
        self.assertEqual(highest, 3)
        self.assertEqual(
            live["current_version"],
            2,
            "the Flow still serves the version the stale repair did not fix",
        )
        self.assertEqual(
            self._rerun_still_fails(harness, flow),
            "blocked",
            "the product reports the repair applied while the Flow fails identically",
        )

        record(
            GuardOutcome(
                guard="Repair revision fence",
                site="studio_store.StudioStore.apply_repair (live flow/action revision recheck)",
                violation="A stale proposal applies after the repair target changed",
                baseline=baseline,
                ablated=(
                    "VIOLATED — proposal fenced to Flow revision 1 applied on top of "
                    "revision 2: marked 'applied', decision recorded, orphan Flow "
                    "version 3 written, and the next Run is still blocked"
                ),
                load_bearing=True,
                note=(
                    "Observed while building this experiment: with the fence ablated "
                    "and the Action (not the Flow) advanced instead, the node-uniqueness "
                    "recheck refuses independently. The canonical_json patch-equality "
                    "recheck is a third layer that no state admitted by the fence can "
                    "trip, because both admitted repair policies compute the same patch "
                    "for any such state."
                ),
            )
        )


# ---------------------------------------------------------------------------
# Guard 5 — Terminal absorption at the database layer.
# ---------------------------------------------------------------------------


class TerminalAbsorptionAblation(AblationCase):
    """A terminal Run is absorbing: its status never changes again."""

    def _completed_run(self) -> tuple[Harness, dict[str, Any]]:
        harness = self.harness()
        flow = harness.completed_flow()
        run = harness.plane.start_studio_run(
            harness.workspace_id, flow["id"], input_data={"value": "Ada"}
        )
        self.assertEqual(run["status"], "completed")
        return harness, run

    @staticmethod
    def _attempt_resurrection(harness: Harness, run_id: str) -> str | None:
        """Try to move a completed Run back to running. Returns the abort text."""

        try:
            with harness.store.write() as connection:
                connection.execute(
                    "UPDATE automation_runs SET status = 'running', "
                    "revision = revision + 1, finished_at = NULL WHERE id = ?",
                    (run_id,),
                )
        except Exception as error:  # noqa: BLE001 - the abort text is the evidence
            return str(error)
        return None

    @staticmethod
    def _drop(harness: Harness, trigger: str) -> None:
        with harness.store.write() as connection:
            connection.execute(f"DROP TRIGGER {trigger}")

    def test_terminal_absorption_is_the_reason_a_finished_run_stays_finished(
        self,
    ) -> None:
        # BASELINE ---------------------------------------------------------
        harness, run = self._completed_run()
        abort = self._attempt_resurrection(harness, run["id"])
        self.assertIsNotNone(abort)
        self.assertEqual(
            harness.plane.get_studio_run(harness.workspace_id, run["id"])["status"],
            "completed",
        )
        baseline = "prevented — SQLite ABORT, Run still 'completed'"

        # ABLATED (single trigger) -----------------------------------------
        # Dropping only the absorbing trigger. This is a redundancy probe: the
        # transition-shape trigger admits no transition out of a terminal state
        # either, so the property survives.
        harness, run = self._completed_run()
        self._drop(harness, "trg_automation_runs_terminal_absorbing")
        single_abort = self._attempt_resurrection(harness, run["id"])
        single_status = harness.plane.get_studio_run(
            harness.workspace_id, run["id"]
        )["status"]
        self.assertIsNotNone(
            single_abort,
            "dropping the absorbing trigger alone is expected to change nothing",
        )
        self.assertIn("illegal automation run status transition", single_abort)
        self.assertEqual(single_status, "completed")

        record(
            GuardOutcome(
                guard="Terminal absorption trigger (alone)",
                site="schema.trg_automation_runs_terminal_absorbing",
                violation="A terminal Run transitions again",
                baseline=baseline,
                ablated="still prevented — trg_automation_runs_transition_shape absorbs it",
                load_bearing=False,
                redundancy_probe=True,
                note=(
                    "REDUNDANT, not decorative. The shape trigger admits transitions "
                    "only out of created/running/waiting_approval, so it already "
                    "forbids everything the absorbing trigger forbids. The property "
                    "remains enforced; see the row below."
                ),
            )
        )

        # ABLATED (the property) -------------------------------------------
        harness, run = self._completed_run()
        self._drop(harness, "trg_automation_runs_terminal_absorbing")
        self._drop(harness, "trg_automation_runs_transition_shape")
        pair_abort = self._attempt_resurrection(harness, run["id"])
        self.assertIsNone(
            pair_abort, f"expected the mutation to land, got {pair_abort!r}"
        )
        resurrected = harness.plane.get_studio_run(harness.workspace_id, run["id"])
        self.assertEqual(
            resurrected["status"],
            "running",
            "a Run the product reported as finished is live again",
        )
        self.assertIsNone(resurrected["finished_at"])

        record(
            GuardOutcome(
                guard="Terminal absorption (database status triggers)",
                site="schema.trg_automation_runs_terminal_absorbing + _transition_shape",
                violation="A terminal Run transitions again",
                baseline=baseline,
                ablated="VIOLATED — completed Run is 'running' again, finished_at cleared",
                load_bearing=True,
            )
        )


# ---------------------------------------------------------------------------
# Guard 6 — Event hash chain binds payload content.
# ---------------------------------------------------------------------------


HASH_MATERIAL_EDITS = (('        "payload": event["payload"],\n', ""),)


class EventHashChainAblation(AblationCase):
    """The ledger hash binds what each event says, not merely its position."""

    @staticmethod
    def _doctor(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
        """Rewrite a denied Action receipt into a successful one."""

        doctored = copy.deepcopy(events)
        for event in doctored:
            if (
                event["type"] == "action.receipted"
                and event["payload"].get("outcome") == "denied"
            ):
                event["payload"]["outcome"] = "succeeded"
                event["payload"]["error_code"] = None
                return doctored, event["id"]
        raise AssertionError("the fixture must contain a denied Action receipt")

    def _blocked_run_events(self) -> tuple[Harness, list[dict[str, Any]]]:
        harness = self.harness()
        _, flow = harness.denied_delivery_flow()
        run = harness.plane.start_studio_run(
            harness.workspace_id, flow["id"], input_data={"value": "release-1"}
        )
        self.assertEqual(run["status"], "blocked")
        exported = harness.plane.get_studio_run(harness.workspace_id, run["id"])
        return harness, exported["events"]

    def test_hash_chain_is_the_reason_a_doctored_evidence_export_is_caught(
        self,
    ) -> None:
        # BASELINE ---------------------------------------------------------
        _, events = self._blocked_run_events()
        self.assertTrue(
            verify_event_chain(events), "the honest export must verify intact"
        )
        doctored, _ = self._doctor(events)
        self.assertFalse(
            verify_event_chain(doctored),
            "rewriting a denied receipt into a success must break the chain",
        )
        self.assertEqual(
            [event["event_hash"] for event in doctored],
            [event["event_hash"] for event in events],
            "the doctored export carries the original hashes, as a forger would",
        )
        baseline = "prevented — doctored export fails chain verification"

        # ABLATED ----------------------------------------------------------
        # The ledger is *built* and verified with a hash that no longer covers
        # payload content, which is what neutralizing the hash actually means.
        with mock.patch.object(
            contracts,
            "event_hash_material",
            _ablate(contracts.event_hash_material, HASH_MATERIAL_EDITS),
        ):
            _, ablated_events = self._blocked_run_events()
            self.assertTrue(verify_event_chain(ablated_events))
            ablated_doctored, tampered_id = self._doctor(ablated_events)
            verified = verify_event_chain(ablated_doctored)

        self.assertTrue(
            verified,
            "with the payload unbound, the tampered ledger verifies as intact",
        )
        tampered = next(
            event for event in ablated_doctored if event["id"] == tampered_id
        )
        self.assertEqual(
            tampered["payload"]["outcome"],
            "succeeded",
            "the accepted export claims a write the sandbox never performed",
        )

        record(
            GuardOutcome(
                guard="Event hash chain",
                site="contracts.event_hash_material / compute_event_hash / verify_event_chain",
                violation="A tampered evidence export verifies as intact",
                baseline=baseline,
                ablated=(
                    "VIOLATED — export rewriting a denied write into 'succeeded' "
                    "verifies as intact"
                ),
                load_bearing=True,
                note=(
                    "StudioStore recomputes this verdict from event material on every "
                    "Run projection. The browser displays that server-owned verdict, "
                    "and a controlled comparison now refuses every sibling whose "
                    "projected ledger does not verify."
                ),
            )
        )


# ---------------------------------------------------------------------------
# Guard 7 — Ratification brake.
# ---------------------------------------------------------------------------


BRAKE_EDITS = (
    ('        self._enforce_brake(workspace_id, context["version"])\n', ""),
)


class RatificationBrakeAblation(AblationCase):
    """A canonical dead end refuses the Run before the Run exists."""

    def _canonical_dead_end(self) -> tuple[Harness, dict[str, Any]]:
        harness = self.harness()
        _, flow = harness.denied_delivery_flow()
        for index in range(3):
            run = harness.plane.start_studio_run(
                harness.workspace_id, flow["id"], input_data={"value": f"release-{index}"}
            )
            self.assertEqual(run["status"], "blocked")
        records = harness.plane.list_dead_ends(harness.workspace_id)
        self.assertEqual(records[0]["ratification_state"], "canonical")
        return harness, flow

    def test_brake_is_the_reason_a_canonical_dead_end_is_not_re_executed(self) -> None:
        # BASELINE ---------------------------------------------------------
        harness, flow = self._canonical_dead_end()
        runs_before = harness.count("automation_runs")
        steps_before = harness.count("automation_run_steps")
        events_before = harness.count("automation_events")

        with self.assertRaises(BrakeEngaged):
            harness.plane.start_studio_run(
                harness.workspace_id, flow["id"], input_data={"value": "release-4"}
            )

        self.assertEqual(harness.count("automation_runs"), runs_before)
        self.assertEqual(harness.count("automation_run_steps"), steps_before)
        self.assertEqual(harness.count("automation_events"), events_before)
        self.assertFalse(
            any(
                json.loads(run["input_json"]).get("value") == "release-4"
                for run in harness.runs()
            )
        )
        baseline = (
            f"prevented — BrakeEngaged, Run rows stay at {runs_before}, "
            "no new Step, no new event"
        )

        # ABLATED ----------------------------------------------------------
        harness, flow = self._canonical_dead_end()
        runs_before = harness.count("automation_runs")
        steps_before = harness.count("automation_run_steps")

        with ablated(StudioRuntime, "prepare", BRAKE_EDITS):
            fourth = harness.plane.start_studio_run(
                harness.workspace_id, flow["id"], input_data={"value": "release-4"}
            )

        self.assertEqual(harness.count("automation_runs"), runs_before + 1)
        self.assertGreater(harness.count("automation_run_steps"), steps_before)
        self.assertEqual(fourth["input"], {"value": "release-4"})
        self.assertEqual(fourth["status"], "blocked")
        self.assertTrue(
            any(
                json.loads(run["input_json"]).get("value") == "release-4"
                for run in harness.runs()
            ),
            "a Run row exists for work the system had canonically proven futile",
        )
        self.assertEqual(
            harness.plane.list_dead_ends(harness.workspace_id)[0]["distinct_runs"],
            4,
            "the futile Run even ratified the dead end further",
        )

        record(
            GuardOutcome(
                guard="Ratification brake",
                site="studio_runtime.StudioRuntime.prepare (_enforce_brake before create_run)",
                violation="A canonical dead end executes another Run",
                baseline=baseline,
                ablated=(
                    "VIOLATED — a fourth Run row, Steps and events created for a "
                    "path three Runs had already proven fails"
                ),
                load_bearing=True,
            )
        )


# ---------------------------------------------------------------------------
# Guards 8-10 — the stop seam. Three checks stand between a model saying it is
# finished and a Run being recorded as finished. Each is ablated separately, and
# the violation is stated once, in product terms, for all three:
#
#     A Run reports `completed` while a declared acceptance criterion is unmet.
#
# Two of the three make that reachable. One does not, and this suite says so.
# ---------------------------------------------------------------------------


STOP_SEAM_VIOLATION = "A Run reports completed while a declared acceptance criterion is unmet"


def criterion(
    criterion_id: str, evidence_kind: str, *node_ids: str, statement: str
) -> dict[str, Any]:
    return {
        "id": criterion_id,
        "statement": statement,
        "evidence_kind": evidence_kind,
        "node_ids": list(node_ids),
    }


# ---------------------------------------------------------------------------
# Guard 8 — the seam's anti-fabrication gate (narrowing gate).
# ---------------------------------------------------------------------------


FABRICATION_GATE_EDITS = (
    (
        "        for anchors in claimed.values():\n"
        "            outside = [anchor for anchor in anchors if anchor not in offered]\n"
        "            if outside:\n"
        "                raise ContractViolation(\n"
        '                    "Goal-Judge cited evidence outside the code-owned candidate"\n'
        "                )\n",
        "",
    ),
)


class GoalJudgeFabricationGateAblation(AblationCase):
    """The judge may only speak about evidence code handed it.

    This is a **redundancy probe**, and the design says so in advance: gates four
    and five are deliberately overlapping, one narrow check against what code
    offered and one independent check against ground truth. The experiment below
    measures whether that overlap is real, and it is: with the seam's gate gone,
    a fabricated anchor reaches the resolver and the resolver refuses it, so the
    product-level violation stays out of reach.

    What the ablation *does* change is legibility. With the gate intact a
    fabricated anchor is a broken contract and the Run says so. With it removed
    the Run fails for the blander reason that its completion went unevidenced,
    and a reader can no longer tell a judge that invented an id from a judge that
    honestly found nothing.
    """

    def _fixture(
        self, chooser: Callable[..., Sequence[str]]
    ) -> tuple[Harness, dict[str, Any], ScriptedGoalJudgeClient]:
        harness = self.harness()
        work = harness.template_action(slug="declared-work", template="Done {{value}}")
        flow = harness.contracted_flow(
            slug="fabrication-probe",
            nodes=[harness.action_node("work", work["version"]["id"])],
            routes=[],
            criteria=[
                criterion(
                    "work-completed",
                    "step",
                    "work",
                    statement="The work node completed its pinned Action.",
                )
            ],
        )
        return harness, flow, ScriptedGoalJudgeClient(chooser=chooser)

    def _attempt(
        self,
        harness: Harness,
        flow: dict[str, Any],
        client: ScriptedGoalJudgeClient,
        *,
        value: str = "release-1",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return harness.plane.start_studio_run(
            harness.workspace_id,
            flow["id"],
            input_data={"value": value},
            client=client,
            idempotency_key=idempotency_key,
        )

    def test_the_anti_fabrication_gate_is_redundant_with_the_resolver(self) -> None:
        # BASELINE ---------------------------------------------------------
        harness, flow, client = self._fixture(anchors_fabricated)
        baseline_run = self._attempt(harness, flow, client)

        self.assertEqual(baseline_run["status"], "failed")
        self.assertEqual(baseline_run["error_code"], "contract_violation")
        self.assertNotIn("completed", harness.status_history(baseline_run["id"]))
        self.assertIsNone(
            harness.completion_event(baseline_run),
            "a contract violation is refused before any adjudication is recorded",
        )
        baseline = (
            "prevented — Run failed/contract_violation, never 'completed', "
            "no adjudication recorded"
        )

        # ABLATED ----------------------------------------------------------
        harness, flow, client = self._fixture(anchors_fabricated)
        with ablated(StudioRuntime, "_adjudicate_completion", FABRICATION_GATE_EDITS):
            ablated_run = self._attempt(harness, flow, client)

        # The violation is NOT reachable. The fabricated anchor reached the
        # resolver, which is what the ablation was meant to prove — and the
        # resolver refused it, because an id no record carries resolves to
        # nothing.
        self.assertEqual(
            ablated_run["status"],
            "failed",
            "the resolver independently refuses a fabricated anchor",
        )
        self.assertEqual(ablated_run["error_code"], "completion_unevidenced")
        self.assertNotIn("completed", harness.status_history(ablated_run["id"]))

        recorded = harness.completion_event(ablated_run)
        self.assertIsNotNone(recorded)
        self.assertEqual(recorded["type"], "completion.refused")
        resolved = recorded["payload"]["criteria"][0]
        self.assertEqual(resolved["surviving"], [])
        self.assertEqual(
            [item["refusal"] for item in resolved["discarded"]],
            [stop_seam.ANCHOR_UNRESOLVABLE],
            "the fabricated anchor did reach the resolver, and was refused there",
        )

        # Defense in depth, second case: an anchor belonging to a *different*
        # Run of the same Flow. With this gate gone the resolver still refuses
        # it, so this narrower violation is not this guard's to prevent either —
        # and it now refuses it *by name*, because the seam resolves the claimed
        # ids workspace-wide and the resolver can therefore see the record whose
        # ownership it is rejecting. Asserting the refusal code rather than only
        # the outcome is what makes that difference visible here: before the
        # anchor-aware lookup existed, this same borrowed anchor reported the
        # misleading `anchor_unresolvable`.
        harness, flow, client = self._fixture(anchors_at_site_in_any_state)
        first = self._attempt(harness, flow, client, idempotency_key="first")
        self.assertEqual(first["status"], "completed")
        borrowed = harness.plane.studio.adjudication_evidence(
            harness.workspace_id, first["id"]
        )["candidates"]["steps"][0]["id"]

        foreign_client = ScriptedGoalJudgeClient(
            chooser=lambda item, evidence: [borrowed]
        )
        with ablated(StudioRuntime, "_adjudicate_completion", FABRICATION_GATE_EDITS):
            second = self._attempt(
                harness, flow, foreign_client, idempotency_key="second"
            )
        self.assertNotEqual(second["id"], first["id"])
        self.assertEqual(second["status"], "failed")
        self.assertEqual(second["error_code"], "completion_unevidenced")
        self.assertNotIn("completed", harness.status_history(second["id"]))

        borrowed_refusal = harness.completion_event(second)
        self.assertEqual(borrowed_refusal["type"], "completion.refused")
        borrowed_resolved = borrowed_refusal["payload"]["criteria"][0]
        self.assertEqual(borrowed_resolved["surviving"], [])
        self.assertEqual(
            [item["refusal"] for item in borrowed_resolved["discarded"]],
            [stop_seam.ANCHOR_FOREIGN_RUN],
            "the resolver names the borrowed anchor for what is wrong with it",
        )

        record(
            GuardOutcome(
                guard="Goal-Judge anti-fabrication gate",
                site="studio_runtime.StudioRuntime._adjudicate_completion (claimed ⊆ code-owned candidate)",
                violation=STOP_SEAM_VIOLATION,
                baseline=baseline,
                ablated=(
                    "still prevented — the anchor reaches the resolver and "
                    "stop_seam._resolve_one_anchor refuses it: a fabricated id as "
                    "'anchor_unresolvable', an id borrowed from another Run as "
                    "'anchor_foreign_run'; Run failed/completion_unevidenced, "
                    "never 'completed'"
                ),
                load_bearing=False,
                redundancy_probe=True,
                note=(
                    "REDUNDANT, not decorative, and deliberately so: the design "
                    "double-gates this seam exactly as the diagnosis path is "
                    "double-gated. The covering guard is the resolution gate in "
                    "stop_seam._resolve_one_anchor — an id no record carries "
                    "resolves to nothing, and an id another Run owns fails the "
                    "ownership check — so no anchor the seam gate would have "
                    "caught can survive resolution. Both asserted above. The "
                    "ownership half is only measurable because the seam resolves "
                    "the claimed ids workspace-wide before gate two: with a "
                    "Run-scoped lookup a borrowed anchor reported the misleading "
                    "'anchor_unresolvable' and this ablation could not tell the "
                    "two apart. What gate one uniquely buys is the *earlier* "
                    "diagnosis: with it a Run that cited evidence code never "
                    "offered reports 'contract_violation' before any adjudication "
                    "is recorded; without it the same Run reports the blander "
                    "'completion_unevidenced' and reads like an honest "
                    "empty-handed judgement."
                ),
            )
        )


# ---------------------------------------------------------------------------
# Guard 9 — the anchor site check (anti-irrelevance).
# ---------------------------------------------------------------------------


ANCHOR_SITE_EDITS = (
    (
        "    if record.node_id not in node_ids:\n"
        "        return ANCHOR_NODE_MISMATCH\n",
        "",
    ),
)


class AnchorSiteCheckAblation(AblationCase):
    """A criterion is satisfied by work at the site it pinned, not by any work.

    The Flow below branches to one of two writing nodes. The criterion pins the
    node this Run routes *away* from, so the declared work genuinely did not
    happen — and the Run genuinely wrote an effect somewhere else. Every property
    of the anchor except its site is correct: real record, this Run's own, right
    kind, minted by the runtime. Only the site check stands between an unrelated
    write and a completion.
    """

    DECLARED_COLLECTION = "declared-launch-records"
    DECOY_COLLECTION = "unrelated-scratch-writes"

    def _fixture(self) -> tuple[Harness, dict[str, Any], ScriptedGoalJudgeClient]:
        harness = self.harness()
        gate = harness.condition_action(slug="route-gate", matches="declared")
        declared = harness.sandbox_action(
            slug="declared-writer", collection=self.DECLARED_COLLECTION
        )
        decoy = harness.sandbox_action(
            slug="decoy-writer", collection=self.DECOY_COLLECTION
        )
        flow = harness.contracted_flow(
            slug="site-probe",
            nodes=[
                harness.action_node("gate", gate["version"]["id"]),
                harness.action_node("declared-writer", declared["version"]["id"]),
                harness.action_node("decoy-writer", decoy["version"]["id"]),
            ],
            routes=[
                {"from": "gate", "to": "declared-writer", "outcome": "true"},
                {"from": "gate", "to": "decoy-writer", "outcome": "false"},
            ],
            criteria=[
                criterion(
                    "record-published",
                    "effect",
                    "declared-writer",
                    statement="The launch record was published by the declared writer.",
                )
            ],
        )
        return harness, flow, ScriptedGoalJudgeClient(chooser=anchors_of_kind_anywhere)

    def _attempt(
        self,
        harness: Harness,
        flow: dict[str, Any],
        client: ScriptedGoalJudgeClient,
    ) -> dict[str, Any]:
        return harness.plane.start_studio_run(
            harness.workspace_id,
            flow["id"],
            input_data={"value": "decoy"},
            client=client,
        )

    def test_site_check_is_the_reason_an_unrelated_write_cannot_satisfy_a_criterion(
        self,
    ) -> None:
        # BASELINE ---------------------------------------------------------
        harness, flow, client = self._fixture()
        baseline_run = self._attempt(harness, flow, client)

        # The declared site never executed, and the Run wrote somewhere else.
        self.assertNotIn(
            "declared-writer", [step["node_id"] for step in baseline_run["steps"]]
        )
        self.assertEqual(
            [effect["collection"] for effect in harness.effects()],
            [self.DECOY_COLLECTION],
        )
        self.assertEqual(baseline_run["status"], "failed")
        self.assertEqual(baseline_run["error_code"], "completion_unevidenced")
        self.assertNotIn("completed", harness.status_history(baseline_run["id"]))
        refused = harness.completion_event(baseline_run)
        self.assertEqual(refused["type"], "completion.refused")
        self.assertEqual(
            [item["refusal"] for item in refused["payload"]["criteria"][0]["discarded"]],
            [stop_seam.ANCHOR_NODE_MISMATCH],
        )
        baseline = (
            "prevented — Run failed/completion_unevidenced, never 'completed', "
            f"the anchor refused 'anchor_node_mismatch', 0 writes in "
            f"'{self.DECLARED_COLLECTION}'"
        )

        # ABLATED ----------------------------------------------------------
        harness, flow, client = self._fixture()
        with ablated(stop_seam, "_resolve_one_anchor", ANCHOR_SITE_EDITS):
            ablated_run = self._attempt(harness, flow, client)

        self.assertEqual(
            ablated_run["status"],
            "completed",
            "the Run is recorded as finished with its declared work undone",
        )
        self.assertIsNone(ablated_run["error_code"])
        self.assertIn("completed", harness.status_history(ablated_run["id"]))
        self.assertNotIn(
            "declared-writer",
            [step["node_id"] for step in ablated_run["steps"]],
            "the node whose work the criterion demanded never ran at all",
        )
        collections = [effect["collection"] for effect in harness.effects()]
        self.assertEqual(collections, [self.DECOY_COLLECTION])
        self.assertNotIn(self.DECLARED_COLLECTION, collections)

        admitted = harness.completion_event(ablated_run)
        self.assertEqual(admitted["type"], "completion.admitted")
        resolved = admitted["payload"]["criteria"][0]
        self.assertTrue(resolved["holds"])
        self.assertEqual(resolved["declared_sites"], ["declared-writer"])
        surviving = resolved["surviving"]
        self.assertEqual(len(surviving), 1)
        with harness.store.read() as connection:
            minted_at = connection.execute(
                "SELECT s.node_id AS node_id FROM automation_effects e "
                "JOIN automation_run_steps s ON s.id = e.step_id WHERE e.id = ?",
                (surviving[0],),
            ).fetchone()["node_id"]
        self.assertEqual(
            minted_at,
            "decoy-writer",
            "the ledger admits a criterion pinned to one node on a write from another",
        )

        record(
            GuardOutcome(
                guard="Anchor site check",
                site="stop_seam._resolve_one_anchor (record.node_id ∈ criterion.node_ids)",
                violation=STOP_SEAM_VIOLATION,
                baseline=baseline,
                ablated=(
                    "VIOLATED — Run completed, 'completion.admitted' in the ledger, "
                    f"the criterion pinned to 'declared-writer' carried by an effect "
                    f"minted at 'decoy-writer'; the declared node never executed and "
                    f"'{self.DECLARED_COLLECTION}' holds 0 writes"
                ),
                load_bearing=True,
                note=(
                    "This is the irrelevance defect the pinned site exists to close. "
                    "The seam's anti-fabrication gate cannot cover it: the anchor is "
                    "this Run's own evidence and therefore inside the candidate set "
                    "code offered. Nothing else in the system checks where a claim's "
                    "evidence was minted."
                ),
            )
        )


# ---------------------------------------------------------------------------
# Guard 10 — the anchor state check.
# ---------------------------------------------------------------------------


ANCHOR_STATE_EDITS = (
    (
        "    if not kind.admits_state(record.state):\n"
        "        return ANCHOR_STATE_MISMATCH\n",
        "",
    ),
)


class AnchorStateCheckAblation(AblationCase):
    """A Step survives its own failure; only a completed one evidences success.

    The Flow below runs a node that fails inside its own executor and continues
    down an error route, so the Run reaches the stop seam holding a Step that is
    real, this Run's own, of the declared kind, minted at exactly the site the
    criterion pinned — and `failed`. Every check but one admits it.
    """

    def _fixture(self) -> tuple[Harness, dict[str, Any], ScriptedGoalJudgeClient]:
        harness = self.harness()
        failing = harness.failing_action(slug="always-fails")
        tail = harness.template_action(slug="tail-note", template="Noted {{value}}")
        flow = harness.contracted_flow(
            slug="state-probe",
            nodes=[
                harness.action_node(
                    "deliver",
                    failing["version"]["id"],
                    settings=CONTINUE_ON_ERROR,
                ),
                harness.action_node("tail", tail["version"]["id"]),
            ],
            routes=[{"from": "deliver", "to": "tail", "outcome": "error"}],
            criteria=[
                criterion(
                    "delivery-completed",
                    "step",
                    "deliver",
                    statement="The delivery node completed its work rather than attempting it.",
                )
            ],
        )
        return harness, flow, ScriptedGoalJudgeClient(
            chooser=anchors_at_site_in_any_state
        )

    def _attempt(
        self,
        harness: Harness,
        flow: dict[str, Any],
        client: ScriptedGoalJudgeClient,
    ) -> dict[str, Any]:
        return harness.plane.start_studio_run(
            harness.workspace_id,
            flow["id"],
            input_data={"value": "release-1"},
            client=client,
        )

    @staticmethod
    def _delivery_step(run: dict[str, Any]) -> dict[str, Any]:
        return next(step for step in run["steps"] if step["node_id"] == "deliver")

    def test_state_check_is_the_reason_a_failed_step_cannot_evidence_success(
        self,
    ) -> None:
        # BASELINE ---------------------------------------------------------
        harness, flow, client = self._fixture()
        baseline_run = self._attempt(harness, flow, client)

        # The fixture must really contain a failed Step at the pinned site.
        self.assertEqual(self._delivery_step(baseline_run)["status"], "failed")
        self.assertEqual(baseline_run["status"], "failed")
        self.assertEqual(baseline_run["error_code"], "completion_unevidenced")
        self.assertNotIn("completed", harness.status_history(baseline_run["id"]))
        refused = harness.completion_event(baseline_run)
        self.assertEqual(refused["type"], "completion.refused")
        self.assertEqual(
            [item["refusal"] for item in refused["payload"]["criteria"][0]["discarded"]],
            [stop_seam.ANCHOR_STATE_MISMATCH],
        )
        baseline = (
            "prevented — Run failed/completion_unevidenced, never 'completed', "
            "the failed Step refused 'anchor_state_mismatch'"
        )

        # ABLATED ----------------------------------------------------------
        harness, flow, client = self._fixture()
        with ablated(stop_seam, "_resolve_one_anchor", ANCHOR_STATE_EDITS):
            ablated_run = self._attempt(harness, flow, client)

        self.assertEqual(
            ablated_run["status"],
            "completed",
            "the Run is recorded as finished on work that failed",
        )
        self.assertIsNone(ablated_run["error_code"])
        self.assertIn("completed", harness.status_history(ablated_run["id"]))

        failed_step = self._delivery_step(ablated_run)
        self.assertEqual(failed_step["status"], "failed")
        admitted = harness.completion_event(ablated_run)
        self.assertEqual(admitted["type"], "completion.admitted")
        resolved = admitted["payload"]["criteria"][0]
        self.assertTrue(resolved["holds"])
        self.assertEqual(
            resolved["surviving"],
            [failed_step["id"]],
            "the admitted anchor is the Step the database still records as failed",
        )
        with harness.store.read() as connection:
            persisted = connection.execute(
                "SELECT status, error_code FROM automation_run_steps WHERE id = ?",
                (failed_step["id"],),
            ).fetchone()
        self.assertEqual(persisted["status"], "failed")
        self.assertIsNotNone(persisted["error_code"])

        record(
            GuardOutcome(
                guard="Anchor state check",
                site="stop_seam._resolve_one_anchor (EvidenceKind.admits_state)",
                violation=STOP_SEAM_VIOLATION,
                baseline=baseline,
                ablated=(
                    "VIOLATED — Run completed, 'completion.admitted' in the ledger, "
                    "and the criterion demanding a completed Step is carried by the "
                    "Step the database still records as 'failed' with an error code"
                ),
                load_bearing=True,
                note=(
                    "Not covered by the seam's anti-fabrication gate: the failed "
                    "Step is this Run's own evidence, so it is inside the candidate "
                    "set code offered. The candidate set is deliberately not "
                    "pre-filtered to qualifying records — pre-filtering it there "
                    "would pre-decide the question the judge exists to answer and "
                    "would make this ablation report redundant while the property "
                    "went unenforced."
                ),
            )
        )


if __name__ == "__main__":
    unittest.main()
