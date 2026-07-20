"""Derive a controlled cross-model comparison from its sibling Runs.

There is no mutable comparison row. A comparison is the immutable expected
sibling manifest pinned into one Run's hash ledger plus the Runs that share its
`comparison_id`; every number a scoreboard shows is recomputed from that
evidence. The manifest is written before provider I/O, so a crash cannot shrink
the requested experiment into a smaller one that looks complete. A derived
scoreboard cannot drift from the Runs it describes, because it is rebuilt from
the manifest and those Runs on every read.

Two things this module refuses to do:

* **It never prints money.** A currency figure would need a hardcoded rate table
  that is stale the week it ships, and a wrong money number is worse than none.
  Provider-reported tokens and wall-clock milliseconds are what the provider
  actually reports, so those are what this reports.
* **It never claims a control it did not enforce.** The pinned Flow version, the
  input, and the model that actually answered are checked and reported as
  enforced. Sampling controls are not reachable through this invocation surface,
  so they are reported, by name, as not controlled here.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence

from .contracts import fingerprint


# A cross-model sweep answers "is this scaffold invariant across brains?". It
# cannot answer "how good is this workflow?", because every quality number in it
# is entangled with whichever brain produced it. Marking the class structurally
# is what stops a downstream reader from quietly promoting a sweep to a
# reference measurement it was never controlled to be.
EVIDENCE_CLASS = "cross_model_sweep"

BASELINE_NOTE = (
    "A baseline is model-pinned by definition. This record varies the model on "
    "purpose, so it is evidence about the scaffold's invariance and must never "
    "be read as a reference score for the Flow."
)


# Stated once, by name, so the claim in the payload is auditable against the
# code that makes it rather than against a sentence in a design document.
UNCONTROLLED_VARIABLES: tuple[dict[str, str], ...] = (
    {
        "variable": "temperature",
        "reason": "not settable through this bounded invocation surface",
    },
    {
        "variable": "top_p",
        "reason": "not settable through this bounded invocation surface",
    },
    {
        "variable": "seed",
        "reason": "not settable through this bounded invocation surface",
    },
    {
        "variable": "provider_side_sampling",
        "reason": (
            "the provider may sample differently on identical input, so repeated "
            "identical calls are not guaranteed to agree"
        ),
    },
    {
        "variable": "provider_side_routing",
        "reason": (
            "serving stack, hardware and capacity are chosen by the provider and "
            "are not observable from a response"
        ),
    },
)


def _parse(timestamp: Any) -> datetime | None:
    if not isinstance(timestamp, str) or not timestamp:
        return None
    try:
        return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None


def _duration_ms(run: Mapping[str, Any]) -> int | None:
    """Wall-clock from the Run's own recorded times, never from a stopwatch.

    A Run paused at a human gate has no `finished_at`, and inventing one would
    silently bill a reviewer's lunch break to the model. The last recorded event
    is the last moment the runtime was demonstrably working.
    """

    started = _parse(run.get("started_at")) or _parse(run.get("created_at"))
    if started is None:
        return None
    finished = _parse(run.get("finished_at"))
    if finished is None:
        events = run.get("events") or []
        stamps = [
            parsed
            for parsed in (_parse(event.get("occurred_at")) for event in events)
            if parsed is not None
        ]
        finished = max(stamps) if stamps else None
    if finished is None:
        return None
    return max(int((finished - started).total_seconds() * 1000), 0)


def _usage_total(call: Mapping[str, Any], key: str) -> int | None:
    usage = call.get("usage")
    if not isinstance(usage, Mapping):
        return None
    value = usage.get(key)
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


def _run_integrity(
    run: Mapping[str, Any], requested_model: str
) -> tuple[bool, list[dict[str, Any]]]:
    """Check that the brain that answered is the brain that was asked for.

    A silent provider fallback would leave every other field in this record
    looking perfectly healthy while the one variable under test was not actually
    varied. It is invisible unless asserted, so it is asserted here, and a
    missing model or missing usage is an error rather than a convenient zero.
    """

    problems: list[dict[str, Any]] = []
    if run.get("ledger_verified") is not True:
        problems.append(
            {
                "code": "ledger_unverified",
                "detail": (
                    "the Run's append-only event chain did not verify, so its "
                    "measurements cannot support a controlled comparison"
                ),
            }
        )
    calls = [call for call in (run.get("model_calls") or [])]
    if not calls:
        problems.append(
            {
                "code": "no_model_call",
                "detail": "the Run recorded no model call, so no model was measured",
            }
        )
    for call in calls:
        reported = call.get("model")
        if not isinstance(reported, str) or not reported or reported == "unknown":
            problems.append(
                {
                    "code": "response_model_missing",
                    "call_id": call.get("id"),
                    "detail": "the provider response carried no usable model name",
                }
            )
        elif reported != requested_model:
            problems.append(
                {
                    "code": "response_model_mismatch",
                    "call_id": call.get("id"),
                    "requested": requested_model,
                    "answered": reported,
                    "detail": (
                        "the provider answered with a different model than the one "
                        "requested, so this sibling did not test the model it claims"
                    ),
                }
            )
        if call.get("status") == "completed" and _usage_total(call, "total_tokens") is None:
            problems.append(
                {
                    "code": "usage_missing",
                    "call_id": call.get("id"),
                    "detail": "the provider reported no token usage, which is not zero",
                }
            )
    return not problems, problems


def _guard_trace(run: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The deterministic part of the traversal — the part the brain must not move.

    Steps that made a model call are excluded: those are the brain. What is left
    is gates, asserts, routers, templates and approvals, whose behaviour is the
    thing a comparison is really testing for invariance.
    """

    model_step_ids = {
        call.get("step_id") for call in (run.get("model_calls") or [])
    }
    return [
        {
            "node_id": step.get("node_id"),
            "status": step.get("status"),
            "route_outcome": step.get("route_outcome"),
            "error_code": step.get("error_code"),
        }
        for step in (run.get("steps") or [])
        if step.get("id") not in model_step_ids
    ]


def run_measurement(run: Mapping[str, Any]) -> dict[str, Any]:
    """One raw repetition, kept whole. Aggregates never replace the runs."""

    requested = str(run.get("model_override") or "")
    verified, problems = _run_integrity(run, requested)
    calls = run.get("model_calls") or []
    totals = [_usage_total(call, "total_tokens") for call in calls]
    inputs = [_usage_total(call, "input_tokens") for call in calls]
    outputs = [_usage_total(call, "output_tokens") for call in calls]
    return {
        "run_id": run.get("id"),
        "model": requested,
        "status": run.get("status"),
        "outcome": run.get("outcome"),
        "flow_version_id": run.get("flow_version_id"),
        # Recomputed per sibling rather than copied from the command, so "they
        # all got the same input" is a checkable claim about stored Run rows and
        # not a promise the caller made about itself.
        "input_fingerprint": fingerprint(run.get("input")),
        "input_tokens": sum(value for value in inputs if value is not None),
        "output_tokens": sum(value for value in outputs if value is not None),
        "total_tokens": sum(value for value in totals if value is not None),
        "duration_ms": _duration_ms(run),
        "effect_count": len(run.get("effects") or []),
        "model_call_count": len(calls),
        "response_models": [call.get("model") for call in calls],
        "routed_path": [step.get("node_id") for step in (run.get("steps") or [])],
        "guard_trace": _guard_trace(run),
        "ledger_verified": bool(run.get("ledger_verified")),
        "integrity": {"verified": verified, "problems": problems},
    }


def _number(values: Sequence[Any]) -> list[float]:
    return [
        float(value)
        for value in values
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]


def _distribution(values: Sequence[Any]) -> dict[str, Any]:
    """Mean, range and *population* variance over the raw repetitions.

    Population, not sample: these repetitions are the entire set of observations
    the command made, not a draw from a larger pool it is trying to estimate.
    """

    numbers = _number(values)
    if not numbers:
        return {
            "values": [],
            "mean": None,
            "min": None,
            "max": None,
            "range": None,
            "population_variance": None,
            "population_stdev": None,
        }
    mean = sum(numbers) / len(numbers)
    variance = sum((value - mean) ** 2 for value in numbers) / len(numbers)
    return {
        "values": [_tidy(value) for value in numbers],
        "mean": _tidy(mean),
        "min": _tidy(min(numbers)),
        "max": _tidy(max(numbers)),
        "range": _tidy(max(numbers) - min(numbers)),
        "population_variance": _tidy(round(variance, 6)),
        "population_stdev": _tidy(round(variance**0.5, 6)),
    }


def _tidy(value: float) -> Any:
    """Report an integral measurement as an integer; tokens are not fractional."""

    return int(value) if float(value).is_integer() else value


def _consensus(values: Iterable[Any]) -> tuple[Any, bool]:
    listed = list(values)
    if not listed:
        return None, True
    first = listed[0]
    return first, all(value == first for value in listed)


def _signature(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def model_measurement(model: str, runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    measurements = [run_measurement(run) for run in runs]
    outcome, outcome_stable = _consensus(item["outcome"] for item in measurements)
    status, status_stable = _consensus(item["status"] for item in measurements)
    effects, effects_stable = _consensus(item["effect_count"] for item in measurements)
    guard_signature, guards_stable = _consensus(
        _signature(item["guard_trace"]) for item in measurements
    )
    tokens = _distribution([item["total_tokens"] for item in measurements])
    duration = _distribution([item["duration_ms"] for item in measurements])
    problems = [
        problem
        for item in measurements
        for problem in item["integrity"]["problems"]
    ]
    return {
        "model": model,
        # The first repetition names the sibling; every repetition is kept in
        # `runs`, because an aggregate that discards its observations is an
        # opinion rather than a measurement.
        "run_id": measurements[0]["run_id"],
        "repetitions": len(measurements),
        "runs": measurements,
        "flow_version_id": measurements[0]["flow_version_id"],
        "input_fingerprint": measurements[0]["input_fingerprint"],
        "status": status,
        "outcome": outcome,
        "total_tokens": tokens["mean"],
        "duration_ms": duration["mean"],
        "effect_count": effects,
        "tokens": tokens,
        "duration": duration,
        "stable_across_repetitions": {
            "outcome": outcome_stable,
            "status": status_stable,
            "effect_count": effects_stable,
            "guard_trace": guards_stable,
        },
        "guard_trace": measurements[0]["guard_trace"],
        "guard_signature": guard_signature,
        "response_model_verified": not problems,
        "integrity": {"verified": not problems, "problems": problems},
    }


def _noise_band(siblings: Sequence[Mapping[str, Any]], metric: str) -> Any:
    """The instrument's own spread, measured on identical input and configuration.

    Repetitions of one model hold *everything* constant — same pinned version,
    same input, same brain — so whatever they disagree by is the harness, not the
    model. The widest such disagreement is the floor below which no between-model
    difference may be called a result.
    """

    ranges = [
        sibling[metric]["range"]
        for sibling in siblings
        if sibling["repetitions"] > 1 and sibling[metric]["range"] is not None
    ]
    return max(ranges) if ranges else None


def _spread(
    siblings: Sequence[Mapping[str, Any]], metric: str, band: Any
) -> dict[str, Any]:
    means = [
        sibling[metric]["mean"]
        for sibling in siblings
        if sibling[metric]["mean"] is not None
    ]
    if not means:
        return {
            "min": None,
            "max": None,
            "difference": None,
            "noise_band": band,
            "within_noise": None,
            "classification": "unmeasured",
        }
    difference = max(means) - min(means)
    if band is None:
        classification = "unmeasured"
        within = None
    elif difference <= band:
        classification = "within_noise"
        within = True
    else:
        classification = "signal"
        within = False
    return {
        "min": _tidy(min(means)),
        "max": _tidy(max(means)),
        "difference": _tidy(difference),
        "noise_band": band,
        "within_noise": within,
        "classification": classification,
    }


def build_comparison(
    *,
    comparison_id: str,
    created_at: Any,
    flow_id: str,
    flow_version_id: str,
    flow_version: Any,
    flow_fingerprint: Any,
    pinned_model: str,
    input_fingerprint: str,
    repetitions: int,
    runs_by_model: Sequence[tuple[str, Sequence[Mapping[str, Any]]]],
    manifests: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    siblings = [model_measurement(model, runs) for model, runs in runs_by_model]

    pinned_versions = {sibling["flow_version_id"] for sibling in siblings}
    version_controlled = pinned_versions == {flow_version_id}
    input_controlled = {sibling["input_fingerprint"] for sibling in siblings} == {
        input_fingerprint
    }

    outcome_by_model = {sibling["model"]: sibling["outcome"] for sibling in siblings}
    status_by_model = {sibling["model"]: sibling["status"] for sibling in siblings}
    guard_by_model = {
        sibling["model"]: sibling["guard_signature"] for sibling in siblings
    }
    # Invariance across brains is only meaningful if each brain is first
    # invariant against itself. A model that routed two ways across its own
    # repetitions has not agreed with anything, including itself, so its
    # apparent agreement with a sibling is an artefact of picking one run.
    def _stable(field: str) -> bool:
        return all(sibling["stable_across_repetitions"][field] for sibling in siblings)

    outcome_invariant = (
        len({_signature(value) for value in outcome_by_model.values()}) <= 1
        and _stable("outcome")
    )
    status_invariant = (
        len({_signature(value) for value in status_by_model.values()}) <= 1
        and _stable("status")
    )
    guard_invariant = (
        len(set(guard_by_model.values())) <= 1 and _stable("guard_trace")
    )

    token_band = _noise_band(siblings, "tokens")
    duration_band = _noise_band(siblings, "duration")

    integrity_problems = [
        {"model": sibling["model"], **problem}
        for sibling in siblings
        for problem in sibling["integrity"]["problems"]
    ]
    manifest: Mapping[str, Any] | None = None
    manifest_verified = False
    if not manifests:
        integrity_problems.append(
            {
                "code": "comparison_manifest_missing",
                "detail": (
                    "the sibling Runs do not carry an immutable declaration of the "
                    "models and repetitions the comparison was meant to execute"
                ),
            }
        )
    elif len(manifests) != 1:
        integrity_problems.append(
            {
                "code": "comparison_manifest_ambiguous",
                "detail": "a comparison must carry exactly one pinned manifest",
            }
        )
    elif not isinstance(manifests[0], Mapping):
        integrity_problems.append(
            {
                "code": "comparison_manifest_invalid",
                "detail": "the pinned comparison manifest is not an object",
            }
        )
    else:
        manifest = manifests[0]
        expected_models = manifest.get("models")
        expected_repetitions = manifest.get("repetitions")
        expected_siblings = manifest.get("siblings")
        valid_shape = (
            manifest.get("comparison_id") == comparison_id
            and manifest.get("flow_id") == flow_id
            and manifest.get("flow_version_id") == flow_version_id
            and manifest.get("flow_fingerprint") == flow_fingerprint
            and manifest.get("input_fingerprint") == input_fingerprint
            and manifest.get("pinned_model") == pinned_model
            and isinstance(expected_models, list)
            and len(expected_models) >= 2
            and all(isinstance(model, str) and model for model in expected_models)
            and len(set(expected_models)) == len(expected_models)
            and isinstance(expected_repetitions, int)
            and not isinstance(expected_repetitions, bool)
            and 1 <= expected_repetitions <= 5
            and isinstance(expected_siblings, list)
        )
        if not valid_shape:
            integrity_problems.append(
                {
                    "code": "comparison_manifest_invalid",
                    "detail": (
                        "the pinned comparison manifest does not match this "
                        "comparison's immutable Flow, input, or bounded sweep shape"
                    ),
                }
            )
        else:
            expected_rows: dict[str, tuple[str, int]] = {}
            rows_valid = len(expected_siblings) == (
                len(expected_models) * expected_repetitions
            )
            for item in expected_siblings:
                if not isinstance(item, Mapping):
                    rows_valid = False
                    continue
                run_id = item.get("run_id")
                model = item.get("model")
                repetition = item.get("repetition")
                if (
                    not isinstance(run_id, str)
                    or not run_id
                    or run_id in expected_rows
                    or model not in expected_models
                    or not isinstance(repetition, int)
                    or isinstance(repetition, bool)
                    or repetition < 1
                    or repetition > expected_repetitions
                ):
                    rows_valid = False
                    continue
                expected_rows[run_id] = (str(model), repetition)
            coverage = {
                model: sorted(
                    repetition
                    for expected_model, repetition in expected_rows.values()
                    if expected_model == model
                )
                for model in expected_models
            }
            if any(
                values != list(range(1, expected_repetitions + 1))
                for values in coverage.values()
            ):
                rows_valid = False
            if not rows_valid:
                integrity_problems.append(
                    {
                        "code": "comparison_manifest_invalid",
                        "detail": (
                            "the manifest does not name exactly one Run for every "
                            "declared model and repetition"
                        ),
                    }
                )
            else:
                actual_rows = {
                    str(run.get("id")): model
                    for model, runs in runs_by_model
                    for run in runs
                }
                expected_ids = set(expected_rows)
                actual_ids = set(actual_rows)
                if expected_ids != actual_ids:
                    integrity_problems.append(
                        {
                            "code": "comparison_sibling_set_incomplete",
                            "missing_run_ids": sorted(expected_ids - actual_ids),
                            "unexpected_run_ids": sorted(actual_ids - expected_ids),
                            "detail": (
                                "the observed sibling set differs from the immutable "
                                "set declared before provider execution"
                            ),
                        }
                    )
                elif any(
                    actual_rows[run_id] != expected_rows[run_id][0]
                    for run_id in expected_ids
                ):
                    integrity_problems.append(
                        {
                            "code": "comparison_sibling_model_mismatch",
                            "detail": (
                                "at least one manifested Run is grouped under a "
                                "different model than the manifest declared"
                            ),
                        }
                    )
                else:
                    manifest_verified = True
    if not version_controlled:
        integrity_problems.append(
            {
                "code": "flow_version_not_identical",
                "detail": (
                    "siblings did not all pin one immutable Flow version, so the "
                    "comparison was not controlled"
                ),
            }
        )
    if not input_controlled:
        integrity_problems.append(
            {
                "code": "input_not_identical",
                "detail": (
                    "siblings did not all receive the same input, so any difference "
                    "between them is not attributable to the model"
                ),
            }
        )
    usable = not integrity_problems

    return {
        "id": comparison_id,
        "created_at": created_at,
        "evidence_class": EVIDENCE_CLASS,
        "usable_as_baseline": False,
        "baseline_note": BASELINE_NOTE,
        "flow_id": flow_id,
        "flow_version_id": flow_version_id,
        "flow_version": flow_version,
        "flow_fingerprint": flow_fingerprint,
        "pinned_model": pinned_model,
        "input_fingerprint": input_fingerprint,
        "repetitions": repetitions,
        "models": [sibling["model"] for sibling in siblings],
        "manifest": {
            "pinned": manifest is not None,
            "verified": manifest_verified,
            "fingerprint": fingerprint(manifest) if manifest is not None else None,
            "expected_models": list(manifest.get("models") or []) if manifest else [],
            "expected_repetitions": manifest.get("repetitions") if manifest else None,
            "expected_run_ids": [
                item.get("run_id")
                for item in (manifest.get("siblings") or [])
                if isinstance(item, Mapping)
            ] if manifest else [],
        },
        "siblings": siblings,
        # The claim under test is that the scaffold dominates the brain: the
        # pinned graph decides routing and refusal, and swapping the model does
        # not move them. Invariance is therefore the headline, and the token and
        # latency spread is a footnote to it, never a ranking.
        "invariance": {
            "routed_outcome": {
                "invariant": outcome_invariant,
                "by_model": outcome_by_model,
                "stable_within_each_model": _stable("outcome"),
            },
            "terminal_status": {
                "invariant": status_invariant,
                "by_model": status_by_model,
                "stable_within_each_model": _stable("status"),
            },
            "guard_behaviour": {
                "invariant": guard_invariant,
                "by_model": guard_by_model,
                "stable_within_each_model": _stable("guard_trace"),
            },
        },
        "disagreed": not outcome_invariant,
        "noise_band": {
            "measured": token_band is not None or duration_band is not None,
            "basis": "within_model_repetitions",
            "repetitions": repetitions,
            "total_tokens": token_band,
            "duration_ms": duration_band,
            "note": (
                "The band is the widest spread observed between repetitions of one "
                "model on identical input and configuration. With a single "
                "repetition per model the harness has not measured itself, so no "
                "numeric difference is reported as a result."
            ),
        },
        "spread": {
            "total_tokens": _spread(siblings, "tokens", token_band),
            "duration_ms": _spread(siblings, "duration", duration_band),
        },
        "control": {
            "enforced_and_verified": [
                {
                    "control": "flow_version_id",
                    "value": flow_version_id,
                    "verified": version_controlled,
                    "method": (
                        "every sibling pins the same immutable Flow version, which "
                        "pins every transitive Action, Agent, Prompt, Skill and schema"
                    ),
                },
                {
                    "control": "input",
                    "value": input_fingerprint,
                    "verified": input_controlled,
                    "method": "one validated input object, hashed and reused verbatim",
                },
                {
                    "control": "comparison_manifest",
                    "value": fingerprint(manifest) if manifest is not None else None,
                    "verified": manifest_verified,
                    "method": (
                        "the complete expected model, repetition and Run-id set is "
                        "appended to a hash-chained Run ledger before provider I/O"
                    ),
                },
                {
                    "control": "response_model",
                    "value": None,
                    "verified": all(
                        sibling["response_model_verified"] for sibling in siblings
                    ),
                    "method": (
                        "the model named in every provider response is compared "
                        "against the model requested for that sibling, so a silent "
                        "provider fallback invalidates the sibling instead of passing"
                    ),
                },
            ],
            "not_controllable_here": list(UNCONTROLLED_VARIABLES),
        },
        "usable": usable,
        "integrity_problems": integrity_problems,
    }
