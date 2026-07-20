"""Integrity gates on a cross-model sweep.

`test_model_comparison.py` fixes the control: one pinned Flow version, one input,
N siblings, and the model as the only recorded delta. These tests cover what that
control is worth only if it is also *checked*:

* the model that answered is the model that was asked for, or the sibling is
  void — a silent provider fallback would leave every other number looking
  healthy while the one variable under test was never varied;
* the payload states which controls were enforced and verified and which are
  simply not reachable through this surface, rather than implying everything was
  held constant;
* repetitions are kept raw next to their aggregate, and the harness measures its
  own spread before any between-model difference is allowed to be called a
  result.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.contracts import ContractViolation
from backend.http_api import ApiApplication, ApiRequest
from backend.model_comparison import EVIDENCE_CLASS
from backend.service import ControlPlane
from backend.store import Store


BRIEF = (
    "Launch a public Build Week preview that demonstrates configurable Actions, "
    "Agents, Flows, approvals, and authoritative Runs."
)

ANALYSIS = {
    "summary": "The launch brief was assessed against the pinned rubric.",
    "score": 0.91,
    "risks": ["A human must still authorize the public sandbox record."],
}


class ScriptedClient:
    """A seam whose usage, and whose answering model, are both scriptable."""

    def __init__(
        self,
        store: Store,
        *,
        tokens: dict[str, list[int]] | None = None,
        answered_model: str | None = None,
        omit_model: bool = False,
        omit_usage: bool = False,
        scores: dict[str, float] | None = None,
    ) -> None:
        self.store = store
        self.tokens = tokens or {}
        self.answered_model = answered_model
        self.omit_model = omit_model
        self.omit_usage = omit_usage
        self.scores = scores or {}
        self.calls: list[str] = []

    def create(self, payload: dict[str, object]) -> dict[str, object]:
        if self.store.in_write_transaction():
            raise AssertionError("provider I/O happened inside a SQLite write transaction")
        model = str(payload.get("model"))
        index = len([item for item in self.calls if item == model])
        self.calls.append(model)
        schedule = self.tokens.get(model, [71])
        total = schedule[index % len(schedule)]
        result = {**ANALYSIS, "score": self.scores.get(model, 0.91)}
        response: dict[str, object] = {
            "id": f"resp_{model}_{len(self.calls)}",
            "status": "completed",
            "output": [
                {
                    "id": f"msg_{len(self.calls)}",
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
        if not self.omit_model:
            response["model"] = self.answered_model or model
        if not self.omit_usage:
            response["usage"] = {
                "input_tokens": 44,
                "output_tokens": max(total - 44, 0),
                "total_tokens": total,
            }
        return response


class ComparisonIntegrityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = Store(Path(self.temporary.name) / "integrity.sqlite3")
        self.store.initialize()

    def _plane(self, client: ScriptedClient) -> tuple[ControlPlane, str, str]:
        plane = ControlPlane(self.store, client)
        bootstrap = plane.create_workspace(seed=True)
        return (
            plane,
            bootstrap["workspace_id"],
            bootstrap["snapshot"]["studio"]["flows"][0]["id"],
        )

    def _compare(self, client: ScriptedClient, **kwargs) -> dict[str, object]:
        plane, workspace_id, flow_id = self._plane(client)
        self.plane = plane
        self.workspace_id = workspace_id
        return plane.compare_studio_models(
            workspace_id,
            flow_id,
            input_data={"brief": BRIEF},
            client=client,
            **kwargs,
        )

    # -- the model that actually answered ----------------------------------

    def test_a_provider_answering_with_another_model_voids_the_sibling(self) -> None:
        client = ScriptedClient(self.store, answered_model="gpt-5.6-luna")
        comparison = self._compare(client, models=["gpt-5.6", "gpt-5.6-terra"])

        self.assertFalse(comparison["usable"], "a fallback must never pass silently")
        codes = {problem["code"] for problem in comparison["integrity_problems"]}
        self.assertIn("response_model_mismatch", codes)
        mismatch = next(
            problem
            for problem in comparison["integrity_problems"]
            if problem["code"] == "response_model_mismatch"
        )
        self.assertEqual(mismatch["answered"], "gpt-5.6-luna")
        self.assertIn(mismatch["requested"], {"gpt-5.6", "gpt-5.6-terra"})
        verified = next(
            control
            for control in comparison["control"]["enforced_and_verified"]
            if control["control"] == "response_model"
        )
        self.assertFalse(verified["verified"])

    def test_a_response_without_a_model_is_an_error_not_a_pass(self) -> None:
        client = ScriptedClient(self.store, omit_model=True)
        comparison = self._compare(client, models=["gpt-5.6", "gpt-5.6-terra"])

        self.assertFalse(comparison["usable"])
        self.assertIn(
            "response_model_missing",
            {problem["code"] for problem in comparison["integrity_problems"]},
        )

    def test_missing_usage_is_an_error_not_a_zero(self) -> None:
        client = ScriptedClient(self.store, omit_usage=True)
        comparison = self._compare(client, models=["gpt-5.6", "gpt-5.6-terra"])

        self.assertFalse(comparison["usable"])
        self.assertIn(
            "usage_missing",
            {problem["code"] for problem in comparison["integrity_problems"]},
        )

    def test_a_faithful_provider_leaves_the_comparison_usable(self) -> None:
        client = ScriptedClient(self.store)
        comparison = self._compare(client, models=["gpt-5.6", "gpt-5.6-terra"])

        self.assertTrue(comparison["usable"])
        self.assertEqual(comparison["integrity_problems"], [])
        self.assertTrue(
            all(sibling["response_model_verified"] for sibling in comparison["siblings"])
        )

    # -- claimed controls versus real ones ---------------------------------

    def test_the_payload_separates_enforced_controls_from_uncontrolled_ones(self) -> None:
        client = ScriptedClient(self.store)
        comparison = self._compare(client, models=["gpt-5.6", "gpt-5.6-terra"])
        control = comparison["control"]

        self.assertEqual(set(control), {"enforced_and_verified", "not_controllable_here"})
        enforced = {item["control"] for item in control["enforced_and_verified"]}
        self.assertEqual(enforced, {"flow_version_id", "input", "response_model"})
        for item in control["enforced_and_verified"]:
            self.assertIn("verified", item)
            self.assertIn("method", item)

        uncontrolled = {item["variable"] for item in control["not_controllable_here"]}
        # Sampling controls are not reachable through this invocation surface, so
        # they are named as uncontrolled rather than quietly implied to be held.
        self.assertLessEqual(
            {"temperature", "top_p", "seed"},
            uncontrolled,
        )
        self.assertTrue(all(item["reason"] for item in control["not_controllable_here"]))
        self.assertFalse(uncontrolled & enforced)

    def test_a_sweep_is_marked_as_its_own_evidence_class_and_never_a_baseline(self) -> None:
        client = ScriptedClient(self.store)
        comparison = self._compare(client, models=["gpt-5.6", "gpt-5.6-terra"])

        self.assertEqual(comparison["evidence_class"], EVIDENCE_CLASS)
        self.assertFalse(comparison["usable_as_baseline"])
        self.assertIn("baseline", comparison["baseline_note"].lower())

    def test_no_currency_figure_appears_anywhere_in_the_payload(self) -> None:
        client = ScriptedClient(self.store)
        comparison = self._compare(client, models=["gpt-5.6", "gpt-5.6-terra"], repetitions=2)
        rendered = json.dumps(comparison).lower()

        for forbidden in ("cost_usd", "price", "usd", "dollar", "$"):
            self.assertNotIn(forbidden, rendered)

    # -- repetitions and the noise band ------------------------------------

    def test_repetitions_keep_every_raw_run_beside_the_aggregate(self) -> None:
        client = ScriptedClient(
            self.store, tokens={"gpt-5.6": [70, 80, 75], "gpt-5.6-terra": [72, 78, 75]}
        )
        comparison = self._compare(
            client, models=["gpt-5.6", "gpt-5.6-terra"], repetitions=3
        )

        self.assertEqual(comparison["repetitions"], 3)
        self.assertEqual(len(client.calls), 6)
        for sibling in comparison["siblings"]:
            self.assertEqual(sibling["repetitions"], 3)
            self.assertEqual(len(sibling["runs"]), 3)
            self.assertEqual(len({run["run_id"] for run in sibling["runs"]}), 3)
            self.assertEqual(len(sibling["tokens"]["values"]), 3)
            self.assertIsNotNone(sibling["tokens"]["population_variance"])
            self.assertIsNotNone(sibling["tokens"]["population_stdev"])

        first = next(s for s in comparison["siblings"] if s["model"] == "gpt-5.6")
        self.assertEqual(sorted(first["tokens"]["values"]), [70, 75, 80])
        self.assertEqual(first["tokens"]["mean"], 75)
        # Population variance of {70, 75, 80} is 50/1.5 -> ((25 + 0 + 25) / 3).
        self.assertAlmostEqual(float(first["tokens"]["population_variance"]), 50 / 3, places=4)

    def test_a_difference_inside_the_noise_band_is_not_reported_as_a_result(self) -> None:
        # Each model's own repetitions swing by 10 and 8 tokens on identical
        # input, so the instrument's spread is 10. The 5-token gap between the
        # two models is smaller than that, and therefore says nothing.
        client = ScriptedClient(
            self.store, tokens={"gpt-5.6": [70, 80], "gpt-5.6-terra": [76, 84]}
        )
        comparison = self._compare(
            client, models=["gpt-5.6", "gpt-5.6-terra"], repetitions=2
        )

        band = comparison["noise_band"]
        self.assertTrue(band["measured"])
        self.assertEqual(band["total_tokens"], 10)

        spread = comparison["spread"]["total_tokens"]
        self.assertEqual(spread["difference"], 5)
        self.assertEqual(spread["classification"], "within_noise")
        self.assertTrue(spread["within_noise"])

    def test_a_difference_larger_than_the_noise_band_may_be_called_signal(self) -> None:
        client = ScriptedClient(
            self.store, tokens={"gpt-5.6": [70, 72], "gpt-5.6-terra": [120, 122]}
        )
        comparison = self._compare(
            client, models=["gpt-5.6", "gpt-5.6-terra"], repetitions=2
        )

        self.assertEqual(comparison["noise_band"]["total_tokens"], 2)
        spread = comparison["spread"]["total_tokens"]
        self.assertEqual(spread["difference"], 50)
        self.assertEqual(spread["classification"], "signal")
        self.assertFalse(spread["within_noise"])

    def test_a_single_repetition_admits_it_has_not_measured_itself(self) -> None:
        client = ScriptedClient(
            self.store, tokens={"gpt-5.6": [70], "gpt-5.6-terra": [900]}
        )
        comparison = self._compare(
            client, models=["gpt-5.6", "gpt-5.6-terra"], repetitions=1
        )

        # A huge apparent gap, and still not a finding: with one run per model
        # the harness has no idea how much of it is its own noise.
        self.assertFalse(comparison["noise_band"]["measured"])
        self.assertIsNone(comparison["noise_band"]["total_tokens"])
        spread = comparison["spread"]["total_tokens"]
        self.assertEqual(spread["difference"], 830)
        self.assertEqual(spread["classification"], "unmeasured")
        self.assertIsNone(spread["within_noise"])

    def test_repetitions_are_bounded_and_must_be_a_positive_integer(self) -> None:
        for bad in (0, -1, 6, "3", 2.0, True):
            with self.assertRaises(ContractViolation):
                self._compare(
                    ScriptedClient(self.store),
                    models=["gpt-5.6", "gpt-5.6-terra"],
                    repetitions=bad,
                )

    # -- the headline: the scaffold, not the brain -------------------------

    def test_guards_stay_invariant_while_token_usage_varies(self) -> None:
        client = ScriptedClient(
            self.store, tokens={"gpt-5.6": [70, 70], "gpt-5.6-terra": [400, 400]}
        )
        comparison = self._compare(
            client, models=["gpt-5.6", "gpt-5.6-terra"], repetitions=2
        )

        invariance = comparison["invariance"]
        self.assertTrue(invariance["routed_outcome"]["invariant"])
        self.assertTrue(invariance["terminal_status"]["invariant"])
        self.assertTrue(invariance["guard_behaviour"]["invariant"])
        self.assertFalse(comparison["disagreed"])
        # Same scaffold behaviour, very different spend: that is the claim.
        self.assertEqual(comparison["spread"]["total_tokens"]["classification"], "signal")

    def test_a_model_that_disagrees_with_itself_cannot_be_called_invariant(self) -> None:
        # Both models route the same way on their first repetition, so a naive
        # reading would report perfect invariance. Their second repetitions route
        # differently, which means neither brain agreed with anything — including
        # itself — and the apparent agreement was an artefact of picking one run.
        client = ScriptedClient(self.store)
        plane, workspace_id, flow_id = self._plane(client)
        original = client.create

        def flaky(payload: dict[str, object]) -> dict[str, object]:
            response = original(payload)
            model = str(payload.get("model"))
            if len([item for item in client.calls if item == model]) == 2:
                text = json.dumps(
                    {**ANALYSIS, "score": 0.28}, separators=(",", ":")
                )
                response["output"][0]["content"][0]["text"] = text
            return response

        client.create = flaky  # type: ignore[method-assign]
        comparison = plane.compare_studio_models(
            workspace_id,
            flow_id,
            input_data={"brief": BRIEF},
            models=["gpt-5.6", "gpt-5.6-terra"],
            repetitions=2,
            client=client,
        )

        for sibling in comparison["siblings"]:
            self.assertFalse(sibling["stable_across_repetitions"]["outcome"])
        invariance = comparison["invariance"]
        self.assertFalse(invariance["routed_outcome"]["stable_within_each_model"])
        self.assertFalse(invariance["routed_outcome"]["invariant"])
        self.assertFalse(invariance["guard_behaviour"]["invariant"])
        self.assertTrue(comparison["disagreed"])

    def test_a_routing_disagreement_is_surfaced_as_non_invariant_guards(self) -> None:
        client = ScriptedClient(
            self.store, scores={"gpt-5.6": 0.91, "gpt-5.6-terra": 0.28}
        )
        comparison = self._compare(client, models=["gpt-5.6", "gpt-5.6-terra"])

        self.assertTrue(comparison["disagreed"])
        self.assertFalse(comparison["invariance"]["routed_outcome"]["invariant"])
        self.assertFalse(comparison["invariance"]["guard_behaviour"]["invariant"])

    # -- containment -------------------------------------------------------

    def test_the_runtime_refuses_an_override_outside_a_comparison(self) -> None:
        client = ScriptedClient(self.store)
        plane, workspace_id, flow_id = self._plane(client)

        with self.assertRaises(ContractViolation):
            plane.studio_runtime.prepare(
                workspace_id,
                flow_id,
                input_data={"brief": BRIEF},
                relation_kind="root",
                model_override="gpt-5.6-luna",
                comparison_id="cmp_manual",
                pinned_model="gpt-5.6",
            )
        with self.assertRaises(ContractViolation):
            plane.studio_runtime.prepare(
                workspace_id,
                flow_id,
                input_data={"brief": BRIEF},
                relation_kind="comparison",
                model_override="gpt-5.6-luna",
                comparison_id=None,
                pinned_model="gpt-5.6",
            )
        runs = plane.snapshot(workspace_id)["studio"]["runs"]
        self.assertEqual([run for run in runs if run["model_override"]], [])

    def test_the_forecast_covers_every_model_and_every_repetition(self) -> None:
        client = ScriptedClient(self.store)
        plane, workspace_id, flow_id = self._plane(client)

        single = plane.studio_flow_model_call_forecast(workspace_id, flow_id)
        self.assertGreaterEqual(single, 1)
        self.assertEqual(
            plane.studio_comparison_model_call_forecast(
                workspace_id, flow_id, models=["gpt-5.6", "gpt-5.6-terra"], repetitions=3
            ),
            single * 2 * 3,
        )

    def test_the_snapshot_carries_the_comparisons_for_a_later_surface(self) -> None:
        client = ScriptedClient(self.store)
        comparison = self._compare(
            client, models=["gpt-5.6", "gpt-5.6-terra"], repetitions=2
        )
        snapshot = self.plane.snapshot(self.workspace_id)["studio"]

        self.assertEqual([item["id"] for item in snapshot["comparisons"]], [comparison["id"]])
        siblings = snapshot["comparisons"][0]["siblings"]
        self.assertEqual(len(siblings), 2)
        self.assertEqual(len({sibling["flow_version_id"] for sibling in siblings}), 1)
        for run in snapshot["runs"]:
            if run["comparison_id"] == comparison["id"]:
                self.assertEqual(run["relation_kind"], "comparison")
                self.assertIsNotNone(run["model_override"])


class ComparisonHttpSurfaceTest(unittest.TestCase):
    """The route is thin: it forecasts, charges the budget, and delegates."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = Store(Path(self.temporary.name) / "http.sqlite3")
        self.store.initialize()
        self.client = ScriptedClient(
            self.store, tokens={"gpt-5.6": [70, 80], "gpt-5.6-terra": [76, 84]}
        )
        self.plane = ControlPlane(self.store, self.client)
        self.app = ApiApplication(self.plane, workspace_model_call_limit=60)
        created = self.app.dispatch(self._request("POST", "/api/v1/workspaces"))
        self.token = created.headers["Set-Cookie"].split("=", 1)[1].split(";")[0]
        self.workspace_id = created.payload["data"]["workspace_id"]
        self.flow_id = created.payload["data"]["snapshot"]["studio"]["flows"][0]["id"]

    def _request(self, method: str, path: str, body: dict | None = None) -> ApiRequest:
        headers = {
            "Origin": "https://runtime.test",
            "Sec-Fetch-Site": "same-origin",
            "Content-Type": "application/json",
            "X-OpenAI-API-Key": "test-browser-owned-key-1234567890",
        }
        if getattr(self, "token", None):
            headers["Cookie"] = f"kyn_workspace={self.token}"
        return ApiRequest(
            method=method,
            path=path,
            headers=headers,
            body=json.dumps(body or {}).encode() if method == "POST" else b"",
            remote_address="192.0.2.10",
            scheme="https",
            host="runtime.test",
        )

    def test_the_route_creates_a_comparison_and_reads_it_back(self) -> None:
        created = self.app.dispatch(
            self._request(
                "POST",
                f"/api/v1/studio/flows/{self.flow_id}/comparisons",
                {
                    "input": {"brief": BRIEF},
                    "models": ["gpt-5.6", "gpt-5.6-terra"],
                    "repetitions": 2,
                },
            )
        )
        self.assertEqual(created.status, 201)
        comparison = created.payload["data"]
        self.assertEqual(len(comparison["siblings"]), 2)
        self.assertEqual(
            len({sibling["flow_version_id"] for sibling in comparison["siblings"]}), 1
        )

        listed = self.app.dispatch(self._request("GET", "/api/v1/studio/comparisons"))
        self.assertEqual(listed.status, 200)
        self.assertEqual(len(listed.payload["data"]["comparisons"]), 1)

        single = self.app.dispatch(
            self._request("GET", f"/api/v1/studio/comparisons/{comparison['id']}")
        )
        self.assertEqual(single.payload["data"]["id"], comparison["id"])

        snapshot = self.app.dispatch(self._request("GET", "/api/v1/studio"))
        self.assertEqual(len(snapshot.payload["data"]["comparisons"]), 1)

    def test_an_unaffordable_sweep_is_refused_before_a_single_sibling_runs(self) -> None:
        refused = self.app.dispatch(
            self._request(
                "POST",
                f"/api/v1/studio/flows/{self.flow_id}/comparisons",
                {
                    "input": {"brief": BRIEF},
                    "models": ["gpt-5.6", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"],
                    "repetitions": 5,
                },
            )
        )
        self.assertEqual(refused.status, 429)
        self.assertEqual(self.client.calls, [])
        runs = self.plane.snapshot(self.workspace_id)["studio"]["runs"]
        self.assertEqual([run for run in runs if run["comparison_id"]], [])


if __name__ == "__main__":
    unittest.main()
