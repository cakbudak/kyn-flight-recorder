#!/usr/bin/env python3
"""Serve the real HTTP/UI stack with a deterministic provider-shaped test seam."""

from __future__ import annotations

import argparse
import json
from functools import partial
from pathlib import Path

from backend.service import ControlPlane
from backend.store import Store
from serve import DemoRequestHandler, DemoServer, ROOT
from tests.test_runtime_contract import ScriptedResponsesClient


# The one live provider behaviour a cross-model sweep cannot survive, reproduced
# deterministically: asking for this model gets you a differently-named sibling
# back, silently. It was measured against the real provider (`gpt-5.6` answering
# as `gpt-5.6-sol`), and it is invisible in every other field of the record --
# usage, status and output all look perfectly healthy. Pinning it to one model
# leaves every existing journey call byte-identical while giving the browser
# check a real unusable comparison to assert against.
SILENT_ALIAS = {"gpt-5.6-terra": "gpt-5.6-terra-sol"}

# The second thing a real sweep hit: a model that did not agree with *itself*
# across its own repetitions, which makes any cross-model agreement it appears
# to show an artefact of picking the run that agreed. Reproduced by swinging one
# model's analysis score across the seeded quality gate on alternate calls.
UNSTABLE_MODELS = frozenset({"gpt-5.6-sol"})
CONFIDENT_SCORE = 0.91
UNCONFIDENT_SCORE = 0.28


class AliasingResponsesClient(ScriptedResponsesClient):
    """The scripted seam, answering as whichever model was actually asked for.

    The base seam always names `gpt-5.6` in its response because nothing before
    the comparison surface varied the request. A sweep does vary it, and a
    response that echoes the request is what a healthy provider looks like, so
    the seam has to be able to look healthy before it can usefully look broken.

    Only the two designated models above deviate, so every call the rest of the
    product journey makes is byte-identical to what it was before.
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.calls_by_model: dict[str, int] = {}

    def create(self, payload: dict[str, object]) -> dict[str, object]:
        response = super().create(payload)
        metadata = payload.get("metadata")
        schema = (
            ((payload.get("text") or {}).get("format") or {}).get("schema")
            if isinstance(payload.get("text"), dict)
            else None
        )
        properties = set(schema.get("properties", {})) if isinstance(schema, dict) else set()
        if properties == {"verdict", "analysis", "recommendations", "risks", "citations"}:
            instructions = str(payload.get("instructions", "")).casefold()
            challenge = (
                "artificial consensus" in instructions
                or "unsupported claims" in instructions
            )
            self._set_json_output(
                response,
                {
                    "verdict": "challenge" if challenge else "commit",
                    "analysis": (
                        "The cited authority boundary is sound, but material dissent remains visible."
                        if challenge
                        else "The cited context supports a bounded, inspectable decision path."
                    ),
                    "recommendations": ["Retain exact citations and inspect the completed member Steps."],
                    "risks": ["Do not treat quorum as unanimity."] if challenge else [],
                    "citations": ["launch-evidence.md:L1-L10"],
                },
            )
        elif properties == {"decision", "consensus", "dissent", "open_questions", "citations"}:
            self._set_json_output(
                response,
                {
                    "decision": "Proceed through the explicit human gate with the cited runtime evidence attached.",
                    "consensus": ["The decision path is bounded and replayable."],
                    "dissent": ["Quorum does not erase the risk participant's challenge."],
                    "open_questions": ["Verify the final browser evidence after the human decision."],
                    "citations": ["launch-evidence.md:L1-L10"],
                },
            )
        elif isinstance(metadata, dict) and metadata.get("operation") == "memory-distillation":
            input_items = payload.get("input")
            evidence = json.loads(str(input_items[0]["content"])) if isinstance(input_items, list) and input_items else {}
            event_ids = [event["id"] for event in evidence.get("events", [])[:1]]
            self._set_json_output(
                response,
                {
                    "title": "Explicit authority follows verified evidence",
                    "content": "Future work should retain cited evidence and place effects behind an explicit human decision.",
                    "rationale": "The completed source Run records this boundary in its verified event ledger.",
                    "tags": ["evidence", "authority"],
                    "evidence_event_ids": event_ids,
                },
            )
        requested = payload.get("model")
        if not isinstance(requested, str) or not requested:
            return response
        ordinal = self.calls_by_model.get(requested, 0) + 1
        self.calls_by_model[requested] = ordinal
        response["model"] = SILENT_ALIAS.get(requested, requested)
        if requested in UNSTABLE_MODELS:
            self._set_score(
                response, CONFIDENT_SCORE if ordinal % 2 else UNCONFIDENT_SCORE
            )
        return response

    @staticmethod
    def _set_json_output(response: dict[str, object], value: dict[str, object]) -> None:
        encoded = json.dumps(value, separators=(",", ":"))
        for item in response.get("output") or []:
            for part in item.get("content") or []:
                if isinstance(part.get("text"), str):
                    part["text"] = encoded
                    return

    @staticmethod
    def _set_score(response: dict[str, object], score: float) -> None:
        for item in response.get("output") or []:
            for part in item.get("content") or []:
                text = part.get("text")
                if not isinstance(text, str):
                    continue
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if not isinstance(parsed, dict) or "score" not in parsed:
                    continue
                parsed["score"] = score
                part["text"] = json.dumps(parsed, separators=(",", ":"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--database", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    store = Store(args.database)
    store.initialize()
    client = AliasingResponsesClient(store)
    plane = ControlPlane(store, client)
    handler = partial(DemoRequestHandler, directory=str(ROOT))
    server = DemoServer(
        (args.host, args.port),
        handler,
        control_plane=plane,
        model_configured=True,
        # The journey exercises two comparison sweeps plus two genuine
        # four-call BoardRooms (the second is nested in the cited-context
        # Flow). Keep the public product defaults strict; only this isolated,
        # deterministic browser fixture gets enough headroom to finish every
        # model-backed seam in one workspace and from one loopback address.
        workspace_model_call_limit=40,
        address_model_call_limit_per_hour=48,
    )
    print(f"Browser test runtime: http://{args.host}:{server.server_port}/app/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
