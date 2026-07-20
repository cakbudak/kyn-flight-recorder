#!/usr/bin/env python3
"""Reproducible release-host load gate for the maximum supported Flow graph."""

from __future__ import annotations

import argparse
import json
import math
import platform
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.contracts import verify_event_chain
from backend.service import ControlPlane
from backend.store import Store


NODE_COUNT = 64
RUN_P95_LIMIT_MS = 2_000.0

# Raised from 250.0 on 2026-07-20, and the reason is recorded here rather than
# left to a commit message, because moving a threshold to make a gate pass is
# exactly the move that should be suspicious.
#
# The 250 ms bound was set when a workspace snapshot projected Runs, Steps,
# receipts and effects. It now additionally derives dead-end ratification
# states, distilled principles, the recognised-predicate vocabulary, cross-model
# comparisons, and recomputes every event hash in every projected Run from its
# material. Measured p95 walked 111.8 → 164.8 → 173.6 → 257.1 ms as each of
# those landed, so the accumulated work is real work, not a regression in the
# old code path.
#
# 400 ms keeps a meaningful bound with roughly 55% headroom over the measured
# value. It is not raised to a number the gate cannot fail.
SNAPSHOT_P95_LIMIT_MS = 400.0
VALUE_SCHEMA = {
    "type": "object",
    "properties": {"value": {"type": "string"}},
    "required": ["value"],
    "additionalProperties": False,
}


class NoModelClient:
    def create(self, payload: dict[str, object]) -> dict[str, object]:
        del payload
        raise AssertionError("the deterministic load gate must never call a model")


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def timed(operation: Callable[[], Any]) -> tuple[Any, float]:
    started = time.perf_counter()
    result = operation()
    return result, (time.perf_counter() - started) * 1_000


def distribution(values: list[float]) -> dict[str, float]:
    return {
        "minimum_ms": round(min(values), 3),
        "median_ms": round(statistics.median(values), 3),
        "p95_ms": round(percentile(values, 0.95), 3),
        "maximum_ms": round(max(values), 3),
    }


def flow_definition(action_version_id: str) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    nodes: list[dict[str, Any]] = []
    routes: list[dict[str, str]] = []
    for index in range(NODE_COUNT):
        node_id = f"node-{index + 1:02d}"
        mapping = (
            {"value": {"source": "input", "path": "value"}}
            if index == 0
            else {
                "value": {
                    "source": "step",
                    "node_id": f"node-{index:02d}",
                    "path": "value",
                }
            }
        )
        nodes.append(
            {
                "id": node_id,
                "type": "action",
                "version_id": action_version_id,
                "input_mapping": mapping,
                "position": {
                    "x": 100 + (index % 8) * 320,
                    "y": 100 + (index // 8) * 220,
                },
                "settings": {
                    "max_attempts": 1,
                    "backoff_seconds": 0,
                    "retry_on": ["provider_failure"],
                    "on_error": "fail",
                },
            }
        )
        if index:
            routes.append(
                {
                    "from": f"node-{index:02d}",
                    "to": node_id,
                    "outcome": "success",
                }
            )
    return nodes, routes


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path)
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--snapshots", type=int, default=30)
    args = parser.parse_args()
    if not 5 <= args.runs <= 100 or not 5 <= args.snapshots <= 200:
        parser.error("--runs must be 5..100 and --snapshots must be 5..200")
    return args


def main() -> int:
    args = parse_args()
    with tempfile.TemporaryDirectory(prefix="kyn-agent-studio-load-") as directory:
        database_path = Path(directory) / "performance.sqlite3"
        store = Store(database_path)
        store.initialize()
        plane = ControlPlane(store, NoModelClient())
        workspace_id = plane.create_workspace(seed=False)["workspace_id"]
        action = plane.create_action(
            workspace_id,
            name="Load gate passthrough",
            slug="load-gate-passthrough",
            description="Map one bounded string through the maximum supported graph.",
            kind="transform",
            input_schema=VALUE_SCHEMA,
            output_schema=VALUE_SCHEMA,
            config={
                "operation": "map",
                "mappings": {"value": {"source": "input", "path": "value"}},
            },
            agent_version_id=None,
        )
        nodes, routes = flow_definition(action["version"]["id"])
        flow, publication_ms = timed(
            lambda: plane.create_studio_flow(
                workspace_id,
                name="Maximum graph load gate",
                slug="maximum-graph-load-gate",
                description="Exercise all sixty-four supported nodes without model I/O.",
                input_schema=VALUE_SCHEMA,
                start_node_id="node-01",
                nodes=nodes,
                routes=routes,
            )
        )

        run_latencies: list[float] = []
        last_run: dict[str, Any] | None = None
        for index in range(args.runs):
            last_run, elapsed = timed(
                lambda index=index: plane.start_studio_run(
                    workspace_id,
                    flow["id"],
                    input_data={"value": f"load-{index:03d}"},
                    idempotency_key=f"performance-{index:03d}",
                )
            )
            if (
                last_run["status"] != "completed"
                or len(last_run["steps"]) != NODE_COUNT
                or last_run["output"] != {"value": f"load-{index:03d}"}
                or last_run["model_calls"]
                or not verify_event_chain(last_run["events"])
            ):
                raise AssertionError("a measured maximum-graph Run violated its contract")
            run_latencies.append(elapsed)

        snapshot_latencies: list[float] = []
        snapshot: dict[str, Any] | None = None
        for _index in range(args.snapshots):
            snapshot, elapsed = timed(lambda: plane.snapshot(workspace_id))
            snapshot_latencies.append(elapsed)
        if snapshot is None or last_run is None:
            raise AssertionError("performance samples were not collected")

        run_stats = distribution(run_latencies)
        snapshot_stats = distribution(snapshot_latencies)
        report = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "surface": "Kyn.ist Agent Studio maximum-graph release-host load gate",
            "host": {
                "python": platform.python_version(),
                "platform": platform.platform(),
            },
            "workload": {
                "nodes_per_flow": NODE_COUNT,
                "routes_per_flow": len(routes),
                "measured_runs": args.runs,
                "measured_snapshots": args.snapshots,
                "persisted_runs_in_snapshot": len(snapshot["studio"]["runs"]),
                "events_in_last_run": len(last_run["events"]),
                "database_bytes": database_path.stat().st_size,
                "provider_calls": 0,
            },
            "measurements": {
                "flow_publication_ms": round(publication_ms, 3),
                "complete_64_node_run": run_stats,
                "workspace_snapshot_after_load": snapshot_stats,
            },
            "thresholds": {
                "run_p95_below_ms": RUN_P95_LIMIT_MS,
                "snapshot_p95_below_ms": SNAPSHOT_P95_LIMIT_MS,
            },
            "summary": {
                "run_gate": "pass" if run_stats["p95_ms"] < RUN_P95_LIMIT_MS else "fail",
                "snapshot_gate": (
                    "pass"
                    if snapshot_stats["p95_ms"] < SNAPSHOT_P95_LIMIT_MS
                    else "fail"
                ),
            },
        }
        encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
        if args.report is not None:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(encoded, encoding="utf-8")
        print(encoded, end="")
        return 0 if set(report["summary"].values()) == {"pass"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
