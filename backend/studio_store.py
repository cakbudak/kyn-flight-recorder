"""Flat SQLite repository for the configurable Kyn.ist Agent Studio surface."""

from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any, Mapping, Sequence

from .contracts import (
    GENESIS_HASH,
    Conflict,
    ContractViolation,
    NotFound,
    canonical_json,
    compute_event_hash,
    default_outcomes_for_kind,
    fingerprint,
    hash_text,
    new_id,
    redact,
    utc_now,
)
from .store import Store


TERMINAL_STATUSES = frozenset({"completed", "blocked", "failed", "cancelled"})


def _decode(value: str | None) -> Any:
    return json.loads(value) if value is not None else None


def _after_minutes(minutes: int) -> str:
    return (
        datetime.now(UTC) + timedelta(minutes=minutes)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class StudioStore:
    """Product-facing persistence seam; no private Kyn ontology is represented here."""

    def __init__(self, store: Store) -> None:
        self.store = store

    # -- Actions -------------------------------------------------------

    def create_action(
        self,
        workspace_id: str,
        *,
        name: str,
        slug: str,
        description: str,
        kind: str,
        input_schema: Mapping[str, Any],
        output_schema: Mapping[str, Any],
        outcomes: Sequence[Mapping[str, Any]],
        config: Mapping[str, Any],
        agent_version_id: str | None,
        effect_level: str,
        created_by: str = "user",
        executor_kind: str | None = None,
    ) -> dict[str, Any]:
        with self.store.write() as connection:
            action_id = self._insert_action(
                connection,
                workspace_id,
                name=name,
                slug=slug,
                description=description,
                kind=kind,
                input_schema=input_schema,
                output_schema=output_schema,
                outcomes=outcomes,
                config=config,
                agent_version_id=agent_version_id,
                effect_level=effect_level,
                created_by=created_by,
                executor_kind=executor_kind,
            )
        return self.get_action(workspace_id, action_id)

    def _insert_action(
        self,
        connection: sqlite3.Connection,
        workspace_id: str,
        *,
        name: str,
        slug: str,
        description: str,
        kind: str,
        input_schema: Mapping[str, Any],
        output_schema: Mapping[str, Any],
        outcomes: Sequence[Mapping[str, Any]] | None = None,
        config: Mapping[str, Any],
        agent_version_id: str | None,
        effect_level: str,
        created_by: str,
        executor_kind: str | None = None,
    ) -> str:
        self.store._require_workspace(connection, workspace_id)
        agent_material: dict[str, str] | None = None
        if agent_version_id is not None:
            row = connection.execute(
                "SELECT id, fingerprint FROM agent_versions WHERE id = ? AND workspace_id = ?",
                (agent_version_id, workspace_id),
            ).fetchone()
            if row is None:
                raise ContractViolation("Action Agent version does not belong to the workspace")
            agent_material = {"id": row["id"], "fingerprint": row["fingerprint"]}
        action_id = new_id("act")
        version_id = new_id("actv")
        now = utc_now()
        material = {
            "kind": executor_kind or kind,
            "storage_kind": kind,
            "input_schema": dict(input_schema),
            "output_schema": dict(output_schema),
            "outcomes": [dict(item) for item in (
                outcomes or default_outcomes_for_kind(executor_kind or kind)
            )],
            "config": dict(config),
            "agent": agent_material,
            "effect_level": effect_level,
        }
        connection.execute(
            """
            INSERT INTO actions
                (id, workspace_id, slug, name, description, current_version, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (action_id, workspace_id, slug, name, description, now, now),
        )
        connection.execute(
            """
            INSERT INTO action_versions
                (id, workspace_id, action_id, version, kind, executor_kind, input_schema_json,
                 output_schema_json, outcomes_json, config_json, agent_version_id, effect_level,
                 fingerprint, created_by, created_at)
            VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version_id,
                workspace_id,
                action_id,
                kind,
                executor_kind,
                canonical_json(dict(input_schema)),
                canonical_json(dict(output_schema)),
                canonical_json(material["outcomes"]),
                canonical_json(dict(config)),
                agent_version_id,
                effect_level,
                fingerprint(material),
                created_by,
                now,
            ),
        )
        return action_id

    @staticmethod
    def _action_version_projection(row: sqlite3.Row) -> dict[str, Any]:
        logical_kind = row["executor_kind"] or row["kind"]
        outcomes = (
            _decode(row["outcomes_json"])
            if "outcomes_json" in row.keys() and row["outcomes_json"]
            else default_outcomes_for_kind(logical_kind)
        )
        return {
            "id": row["id"],
            "version": row["version"],
            "kind": logical_kind,
            "storage_kind": row["kind"],
            "input_schema": _decode(row["input_schema_json"]),
            "output_schema": _decode(row["output_schema_json"]),
            "outcomes": outcomes,
            "config": _decode(row["config_json"]),
            "agent_version_id": row["agent_version_id"],
            "effect_level": row["effect_level"],
            "fingerprint": row["fingerprint"],
            "created_by": row["created_by"],
            "created_at": row["created_at"],
        }

    def _action_projection(
        self, connection: sqlite3.Connection, row: sqlite3.Row
    ) -> dict[str, Any]:
        version = connection.execute(
            "SELECT * FROM action_versions WHERE action_id = ? AND version = ?",
            (row["id"], row["current_version"]),
        ).fetchone()
        if version is None:
            raise RuntimeError("Action current version is missing")
        return {
            "id": row["id"],
            "slug": row["slug"],
            "name": row["name"],
            "description": row["description"],
            "current_version": row["current_version"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "version": self._action_version_projection(version),
            "versions": [
                self._action_version_projection(item)
                for item in connection.execute(
                    "SELECT * FROM action_versions WHERE action_id = ? "
                    "ORDER BY version DESC",
                    (row["id"],),
                )
            ],
        }

    def revise_action(
        self,
        workspace_id: str,
        action_id: str,
        *,
        expected_version: int,
        name: str,
        description: str,
        kind: str,
        input_schema: Mapping[str, Any],
        output_schema: Mapping[str, Any],
        outcomes: Sequence[Mapping[str, Any]],
        config: Mapping[str, Any],
        agent_version_id: str | None,
        effect_level: str,
        created_by: str = "user",
        executor_kind: str | None = None,
    ) -> dict[str, Any]:
        with self.store.write() as connection:
            action = connection.execute(
                "SELECT * FROM actions WHERE id = ? AND workspace_id = ?",
                (action_id, workspace_id),
            ).fetchone()
            if action is None:
                raise NotFound("Action was not found")
            if int(action["current_version"]) != expected_version:
                raise Conflict("Action version changed")
            agent_material: dict[str, str] | None = None
            if agent_version_id is not None:
                agent = connection.execute(
                    "SELECT id, fingerprint FROM agent_versions "
                    "WHERE id = ? AND workspace_id = ?",
                    (agent_version_id, workspace_id),
                ).fetchone()
                if agent is None:
                    raise ContractViolation(
                        "Action Agent version does not belong to the workspace"
                    )
                agent_material = {
                    "id": str(agent["id"]),
                    "fingerprint": str(agent["fingerprint"]),
                }
            next_version = expected_version + 1
            version_id = new_id("actv")
            now = utc_now()
            material = {
                "kind": executor_kind or kind,
                "storage_kind": kind,
                "input_schema": dict(input_schema),
                "output_schema": dict(output_schema),
                "outcomes": [dict(item) for item in outcomes],
                "config": dict(config),
                "agent": agent_material,
                "effect_level": effect_level,
                "parent_version": action["current_version"],
            }
            connection.execute(
                """
                INSERT INTO action_versions
                    (id, workspace_id, action_id, version, kind, executor_kind,
                     input_schema_json, output_schema_json, outcomes_json, config_json,
                     agent_version_id, effect_level, fingerprint, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version_id,
                    workspace_id,
                    action_id,
                    next_version,
                    kind,
                    executor_kind,
                    canonical_json(dict(input_schema)),
                    canonical_json(dict(output_schema)),
                    canonical_json([dict(item) for item in outcomes]),
                    canonical_json(dict(config)),
                    agent_version_id,
                    effect_level,
                    fingerprint(material),
                    created_by,
                    now,
                ),
            )
            cursor = connection.execute(
                """
                UPDATE actions
                SET name = ?, description = ?, current_version = current_version + 1,
                    updated_at = ?
                WHERE id = ? AND workspace_id = ? AND current_version = ?
                """,
                (name, description, now, action_id, workspace_id, expected_version),
            )
            if cursor.rowcount != 1:
                raise Conflict("Action version changed")
        return self.get_action(workspace_id, action_id)

    def get_action(self, workspace_id: str, action_id: str) -> dict[str, Any]:
        with self.store.read() as connection:
            row = connection.execute(
                "SELECT * FROM actions WHERE id = ? AND workspace_id = ?",
                (action_id, workspace_id),
            ).fetchone()
            if row is None:
                raise NotFound("Action was not found")
            return self._action_projection(connection, row)

    def get_action_version(
        self, workspace_id: str, version_id: str
    ) -> dict[str, Any]:
        with self.store.read() as connection:
            row = connection.execute(
                "SELECT * FROM action_versions WHERE id = ? AND workspace_id = ?",
                (version_id, workspace_id),
            ).fetchone()
            if row is None:
                raise NotFound("Action version was not found")
            action = connection.execute(
                "SELECT name, slug, description FROM actions WHERE id = ?",
                (row["action_id"],),
            ).fetchone()
            if action is None:
                raise RuntimeError("Action resource is missing")
            return {
                **self._action_version_projection(row),
                "action_id": row["action_id"],
                "name": action["name"],
                "slug": action["slug"],
                "description": action["description"],
            }

    def get_agent_runtime(
        self, workspace_id: str, version_id: str
    ) -> dict[str, Any]:
        with self.store.read() as connection:
            owned = connection.execute(
                "SELECT id FROM agent_versions WHERE id = ? AND workspace_id = ?",
                (version_id, workspace_id),
            ).fetchone()
            if owned is None:
                raise NotFound("Agent version was not found")
            return self.store._agent_runtime_projection(connection, version_id)

    def find_agent_runtime_by_role(
        self, workspace_id: str, role: str
    ) -> dict[str, Any]:
        with self.store.read() as connection:
            row = connection.execute(
                """
                SELECT av.id
                FROM agent_versions av
                JOIN agents a ON a.id = av.agent_id AND a.current_version = av.version
                WHERE av.workspace_id = ? AND av.role = ?
                ORDER BY a.created_at, a.id
                LIMIT 1
                """,
                (workspace_id, role),
            ).fetchone()
            if row is None:
                raise NotFound(f"No {role} Agent is available")
            return self.store._agent_runtime_projection(connection, row["id"])

    # -- Flows ---------------------------------------------------------

    def create_flow(
        self,
        workspace_id: str,
        *,
        name: str,
        slug: str,
        description: str,
        input_schema: Mapping[str, Any],
        output_schema: Mapping[str, Any],
        outcomes: Sequence[Mapping[str, Any]],
        start_node_id: str,
        nodes: Sequence[Mapping[str, Any]],
        routes: Sequence[Mapping[str, Any]],
        created_by: str = "user",
    ) -> dict[str, Any]:
        with self.store.write() as connection:
            flow_id = self._insert_flow(
                connection,
                workspace_id,
                name=name,
                slug=slug,
                description=description,
                input_schema=input_schema,
                output_schema=output_schema,
                outcomes=outcomes,
                start_node_id=start_node_id,
                nodes=nodes,
                routes=routes,
                created_by=created_by,
            )
        return self.get_flow(workspace_id, flow_id)

    def _insert_flow(
        self,
        connection: sqlite3.Connection,
        workspace_id: str,
        *,
        name: str,
        slug: str,
        description: str,
        input_schema: Mapping[str, Any],
        output_schema: Mapping[str, Any],
        outcomes: Sequence[Mapping[str, Any]],
        start_node_id: str,
        nodes: Sequence[Mapping[str, Any]],
        routes: Sequence[Mapping[str, Any]],
        created_by: str,
    ) -> str:
        self.store._require_workspace(connection, workspace_id)
        flow_id = new_id("aflow")
        pinned, requires_model = self._resolve_flow_pins(
            connection, workspace_id, nodes, owner_flow_id=flow_id
        )

        material = {
            "input_schema": dict(input_schema),
            "output_schema": dict(output_schema),
            "outcomes": [dict(item) for item in outcomes],
            "start_node_id": start_node_id,
            "nodes": [dict(node) for node in nodes],
            "routes": [dict(route) for route in routes],
            "pinned_resources": pinned,
        }
        version_id = new_id("aflowv")
        now = utc_now()
        connection.execute(
            """
            INSERT INTO automation_flows
                (id, workspace_id, slug, name, description, revision, current_version,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 1, 1, ?, ?)
            """,
            (flow_id, workspace_id, slug, name, description, now, now),
        )
        connection.execute(
            """
            INSERT INTO automation_flow_versions
                (id, workspace_id, flow_id, version, input_schema_json, output_schema_json,
                 outcomes_json, start_node_id, nodes_json, routes_json,
                 pinned_resources_json, requires_model,
                 fingerprint, parent_version_id, created_by, created_at)
            VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
            """,
            (
                version_id,
                workspace_id,
                flow_id,
                canonical_json(dict(input_schema)),
                canonical_json(dict(output_schema)),
                canonical_json([dict(item) for item in outcomes]),
                start_node_id,
                canonical_json([dict(node) for node in nodes]),
                canonical_json([dict(route) for route in routes]),
                canonical_json(pinned),
                int(requires_model),
                fingerprint(material),
                created_by,
                now,
            ),
        )
        return flow_id

    @staticmethod
    def _resolve_flow_pins(
        connection: sqlite3.Connection,
        workspace_id: str,
        nodes: Sequence[Mapping[str, Any]],
        *,
        owner_flow_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], bool]:
        pinned: list[dict[str, Any]] = []
        requires_model = False
        expanded_nodes = len(nodes)
        for node in nodes:
            version_id = str(node["version_id"])
            if node["type"] == "action":
                row = connection.execute(
                    "SELECT id, kind, executor_kind, fingerprint FROM action_versions WHERE id = ? AND workspace_id = ?",
                    (version_id, workspace_id),
                ).fetchone()
                if row is None:
                    raise ContractViolation("Flow Action version does not belong to the workspace")
                pinned.append(
                    {
                        "node_id": node["id"],
                        "type": "action",
                        "version_id": row["id"],
                        "fingerprint": row["fingerprint"],
                    }
                )
                requires_model = requires_model or (row["executor_kind"] or row["kind"]) == "ai"
            elif node["type"] == "agent":
                row = connection.execute(
                    "SELECT id, fingerprint FROM agent_versions WHERE id = ? AND workspace_id = ?",
                    (version_id, workspace_id),
                ).fetchone()
                if row is None:
                    raise ContractViolation("Flow Agent version does not belong to the workspace")
                pinned.append(
                    {
                        "node_id": node["id"],
                        "type": "agent",
                        "version_id": row["id"],
                        "fingerprint": row["fingerprint"],
                    }
                )
                requires_model = True
            else:
                row = connection.execute(
                    """
                    SELECT afv.id, afv.flow_id, afv.version, afv.fingerprint,
                           afv.requires_model, afv.pinned_resources_json
                    FROM automation_flow_versions afv
                    JOIN automation_flows af ON af.id = afv.flow_id
                    WHERE afv.id = ? AND afv.workspace_id = ?
                    """,
                    (version_id, workspace_id),
                ).fetchone()
                if row is None:
                    raise ContractViolation(
                        "Flow node version does not belong to the workspace"
                    )
                if owner_flow_id is not None and StudioStore._flow_version_reaches_flow(
                    connection, str(row["id"]), owner_flow_id
                ):
                    raise ContractViolation("Flow reuse dependency would create a cycle")
                pinned.append(
                    {
                        "node_id": node["id"],
                        "type": "flow",
                        "version_id": row["id"],
                        "flow_id": row["flow_id"],
                        "version": int(row["version"]),
                        "fingerprint": row["fingerprint"],
                    }
                )
                requires_model = requires_model or bool(row["requires_model"])
                expanded_nodes += StudioStore._flow_version_node_count(
                    connection, str(row["id"])
                )
                if expanded_nodes > 200:
                    raise ContractViolation(
                        "Flow and its pinned subflows exceed two hundred nodes"
                    )
        return pinned, requires_model

    @staticmethod
    def _flow_version_node_count(
        connection: sqlite3.Connection,
        version_id: str,
        stack: set[str] | None = None,
    ) -> int:
        active = stack if stack is not None else set()
        if version_id in active:
            raise ContractViolation("Flow reuse dependency contains a cycle")
        active.add(version_id)
        row = connection.execute(
            "SELECT nodes_json, pinned_resources_json "
            "FROM automation_flow_versions WHERE id = ?",
            (version_id,),
        ).fetchone()
        if row is None:
            raise ContractViolation("Pinned subflow version is missing")
        total = len(_decode(row["nodes_json"]))
        for pin in _decode(row["pinned_resources_json"]):
            if pin.get("type") == "flow":
                total += StudioStore._flow_version_node_count(
                    connection, str(pin["version_id"]), active
                )
        active.remove(version_id)
        return total

    @staticmethod
    def _flow_version_reaches_flow(
        connection: sqlite3.Connection,
        version_id: str,
        target_flow_id: str,
        seen: set[str] | None = None,
    ) -> bool:
        visited = seen if seen is not None else set()
        if version_id in visited:
            return False
        visited.add(version_id)
        row = connection.execute(
            "SELECT flow_id, pinned_resources_json FROM automation_flow_versions "
            "WHERE id = ?",
            (version_id,),
        ).fetchone()
        if row is None:
            raise ContractViolation("Pinned subflow version is missing")
        if str(row["flow_id"]) == target_flow_id:
            return True
        for pin in _decode(row["pinned_resources_json"]):
            if pin.get("type") == "flow" and StudioStore._flow_version_reaches_flow(
                connection, str(pin["version_id"]), target_flow_id, visited
            ):
                return True
        return False

    def revise_flow(
        self,
        workspace_id: str,
        flow_id: str,
        *,
        expected_revision: int,
        input_schema: Mapping[str, Any],
        output_schema: Mapping[str, Any],
        outcomes: Sequence[Mapping[str, Any]],
        start_node_id: str,
        nodes: Sequence[Mapping[str, Any]],
        routes: Sequence[Mapping[str, Any]],
        created_by: str = "user",
    ) -> dict[str, Any]:
        with self.store.write() as connection:
            flow = connection.execute(
                "SELECT * FROM automation_flows WHERE id = ? AND workspace_id = ?",
                (flow_id, workspace_id),
            ).fetchone()
            if flow is None:
                raise NotFound("Automation Flow was not found")
            if int(flow["revision"]) != expected_revision:
                raise Conflict("Automation Flow revision changed")
            parent = connection.execute(
                "SELECT * FROM automation_flow_versions WHERE flow_id = ? AND version = ?",
                (flow_id, flow["current_version"]),
            ).fetchone()
            if parent is None:
                raise RuntimeError("Automation Flow current version is missing")
            pinned, requires_model = self._resolve_flow_pins(
                connection, workspace_id, nodes, owner_flow_id=flow_id
            )
            next_version = int(flow["current_version"]) + 1
            version_id = new_id("aflowv")
            now = utc_now()
            material = {
                "input_schema": dict(input_schema),
                "output_schema": dict(output_schema),
                "outcomes": [dict(item) for item in outcomes],
                "start_node_id": start_node_id,
                "nodes": [dict(node) for node in nodes],
                "routes": [dict(route) for route in routes],
                "pinned_resources": pinned,
                "parent_version_id": parent["id"],
            }
            connection.execute(
                """
                INSERT INTO automation_flow_versions
                    (id, workspace_id, flow_id, version, input_schema_json,
                     output_schema_json, outcomes_json, start_node_id,
                     nodes_json, routes_json, pinned_resources_json, requires_model,
                     fingerprint, parent_version_id, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version_id,
                    workspace_id,
                    flow_id,
                    next_version,
                    canonical_json(dict(input_schema)),
                    canonical_json(dict(output_schema)),
                    canonical_json([dict(item) for item in outcomes]),
                    start_node_id,
                    canonical_json([dict(node) for node in nodes]),
                    canonical_json([dict(route) for route in routes]),
                    canonical_json(pinned),
                    int(requires_model),
                    fingerprint(material),
                    parent["id"],
                    created_by,
                    now,
                ),
            )
            cursor = connection.execute(
                """
                UPDATE automation_flows
                SET revision = revision + 1, current_version = current_version + 1,
                    updated_at = ?
                WHERE id = ? AND workspace_id = ? AND revision = ?
                """,
                (now, flow_id, workspace_id, expected_revision),
            )
            if cursor.rowcount != 1:
                raise Conflict("Automation Flow revision changed")
        return self.get_flow(workspace_id, flow_id)

    @staticmethod
    def _flow_version_projection(row: sqlite3.Row) -> dict[str, Any]:
        output_schema = (
            _decode(row["output_schema_json"])
            if "output_schema_json" in row.keys() and row["output_schema_json"]
            else None
        )
        outcomes = (
            _decode(row["outcomes_json"])
            if "outcomes_json" in row.keys() and row["outcomes_json"]
            else default_outcomes_for_kind("flow")
        )
        return {
            "id": row["id"],
            "version": row["version"],
            "input_schema": _decode(row["input_schema_json"]),
            "output_schema": output_schema,
            "outcomes": outcomes,
            "start_node_id": row["start_node_id"],
            "nodes": _decode(row["nodes_json"]),
            "routes": _decode(row["routes_json"]),
            "pinned_resources": _decode(row["pinned_resources_json"]),
            "requires_model": bool(row["requires_model"]),
            "fingerprint": row["fingerprint"],
            "parent_version_id": row["parent_version_id"],
            "created_by": row["created_by"],
            "created_at": row["created_at"],
        }

    def _flow_projection(
        self, connection: sqlite3.Connection, row: sqlite3.Row
    ) -> dict[str, Any]:
        version = connection.execute(
            "SELECT * FROM automation_flow_versions WHERE flow_id = ? AND version = ?",
            (row["id"], row["current_version"]),
        ).fetchone()
        if version is None:
            raise RuntimeError("Automation Flow current version is missing")
        return {
            "id": row["id"],
            "slug": row["slug"],
            "name": row["name"],
            "description": row["description"],
            "revision": row["revision"],
            "current_version": row["current_version"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "version": self._flow_version_projection(version),
            "versions": [
                self._flow_version_projection(item)
                for item in connection.execute(
                    "SELECT * FROM automation_flow_versions WHERE flow_id = ? "
                    "ORDER BY version DESC",
                    (row["id"],),
                )
            ],
        }

    def get_flow(self, workspace_id: str, flow_id: str) -> dict[str, Any]:
        with self.store.read() as connection:
            row = connection.execute(
                "SELECT * FROM automation_flows WHERE id = ? AND workspace_id = ?",
                (flow_id, workspace_id),
            ).fetchone()
            if row is None:
                raise NotFound("Automation Flow was not found")
            return self._flow_projection(connection, row)

    def flow_context(
        self, workspace_id: str, flow_id: str, version: int | None = None
    ) -> dict[str, Any]:
        with self.store.read() as connection:
            flow = connection.execute(
                "SELECT * FROM automation_flows WHERE id = ? AND workspace_id = ?",
                (flow_id, workspace_id),
            ).fetchone()
            if flow is None:
                raise NotFound("Automation Flow was not found")
            selected = int(version or flow["current_version"])
            flow_version = connection.execute(
                "SELECT * FROM automation_flow_versions WHERE flow_id = ? AND version = ?",
                (flow_id, selected),
            ).fetchone()
            if flow_version is None:
                raise NotFound("Automation Flow version was not found")
            return {
                "flow": self._flow_projection(connection, flow),
                "version": self._flow_version_projection(flow_version),
            }

    def get_flow_version_by_id(
        self, workspace_id: str, version_id: str
    ) -> dict[str, Any]:
        with self.store.read() as connection:
            row = connection.execute(
                "SELECT * FROM automation_flow_versions "
                "WHERE id = ? AND workspace_id = ?",
                (version_id, workspace_id),
            ).fetchone()
            if row is None:
                raise NotFound("Automation Flow version was not found")
            flow = connection.execute(
                "SELECT id, slug, name, description FROM automation_flows WHERE id = ?",
                (row["flow_id"],),
            ).fetchone()
            if flow is None:
                raise RuntimeError("Automation Flow resource is missing")
            return {
                **self._flow_version_projection(row),
                "flow_id": flow["id"],
                "slug": flow["slug"],
                "name": flow["name"],
                "description": flow["description"],
            }

    # -- Trigger bindings ---------------------------------------------

    @staticmethod
    def _trigger_projection(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "flow_id": row["flow_id"],
            "flow_version_id": row["flow_version_id"],
            "name": row["name"],
            "trigger_type": row["trigger_type"],
            "config": _decode(row["config_json"]),
            "token_hint": row["token_hint"],
            "enabled": bool(row["enabled"]),
            "revision": row["revision"],
            "next_fire_at": row["next_fire_at"],
            "last_fired_at": row["last_fired_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def create_trigger(
        self,
        workspace_id: str,
        flow_id: str,
        *,
        name: str,
        trigger_type: str,
        config: Mapping[str, Any],
    ) -> dict[str, Any]:
        secret: str | None = None
        with self.store.write() as connection:
            flow = connection.execute(
                "SELECT * FROM automation_flows WHERE id = ? AND workspace_id = ?",
                (flow_id, workspace_id),
            ).fetchone()
            if flow is None:
                raise NotFound("Automation Flow was not found")
            version = connection.execute(
                "SELECT id FROM automation_flow_versions WHERE flow_id = ? AND version = ?",
                (flow_id, flow["current_version"]),
            ).fetchone()
            if version is None:
                raise RuntimeError("Automation Flow current version is missing")
            if trigger_type == "webhook":
                secret = f"hook_{secrets.token_urlsafe(32)}"
            trigger_id = new_id("atrg")
            now = utc_now()
            next_fire_at = (
                _after_minutes(int(config["interval_minutes"]))
                if trigger_type == "schedule"
                else None
            )
            connection.execute(
                """
                INSERT INTO automation_trigger_bindings
                    (id, workspace_id, flow_id, flow_version_id, name, trigger_type,
                     config_json, token_hash, token_hint, enabled, revision,
                     next_fire_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, ?, ?, ?)
                """,
                (
                    trigger_id,
                    workspace_id,
                    flow_id,
                    version["id"],
                    name,
                    trigger_type,
                    canonical_json(dict(config)),
                    hash_text(secret) if secret is not None else None,
                    secret[-6:] if secret is not None else None,
                    next_fire_at,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM automation_trigger_bindings WHERE id = ?",
                (trigger_id,),
            ).fetchone()
            assert row is not None
            result = self._trigger_projection(row)
        if secret is not None:
            result["secret"] = secret
            result["hook_path"] = f"/api/v1/hooks/{secret}"
        return result

    def resolve_webhook(self, secret: str) -> dict[str, Any]:
        with self.store.read() as connection:
            row = connection.execute(
                """
                SELECT * FROM automation_trigger_bindings
                WHERE token_hash = ? AND trigger_type = 'webhook' AND enabled = 1
                """,
                (hash_text(secret),),
            ).fetchone()
            if row is None:
                raise NotFound("Webhook trigger was not found")
            version = connection.execute(
                "SELECT version, requires_model FROM automation_flow_versions WHERE id = ?",
                (row["flow_version_id"],),
            ).fetchone()
            if version is None:
                raise RuntimeError("Webhook Flow version is missing")
            return {
                **self._trigger_projection(row),
                "workspace_id": row["workspace_id"],
                "flow_version": int(version["version"]),
                "requires_model": bool(version["requires_model"]),
            }

    def mark_trigger_fired(self, trigger_id: str) -> None:
        with self.store.write() as connection:
            now = utc_now()
            cursor = connection.execute(
                """
                UPDATE automation_trigger_bindings
                SET last_fired_at = ?, updated_at = ?
                WHERE id = ? AND enabled = 1
                """,
                (now, now, trigger_id),
            )
            if cursor.rowcount != 1:
                raise Conflict("Trigger is no longer enabled")

    def set_trigger_enabled(
        self,
        workspace_id: str,
        trigger_id: str,
        *,
        enabled: bool,
        expected_revision: int,
    ) -> dict[str, Any]:
        with self.store.write() as connection:
            row = connection.execute(
                "SELECT * FROM automation_trigger_bindings WHERE id = ? AND workspace_id = ?",
                (trigger_id, workspace_id),
            ).fetchone()
            if row is None:
                raise NotFound("Automation trigger was not found")
            if int(row["revision"]) != expected_revision:
                raise Conflict("Automation trigger revision is stale")
            if bool(row["enabled"]) == enabled:
                return self._trigger_projection(row)
            now = utc_now()
            config = _decode(row["config_json"])
            next_fire_at = (
                _after_minutes(int(config["interval_minutes"]))
                if enabled and row["trigger_type"] == "schedule"
                else None
            )
            connection.execute(
                """
                UPDATE automation_trigger_bindings
                SET enabled = ?, revision = revision + 1, next_fire_at = ?, updated_at = ?
                WHERE id = ? AND workspace_id = ? AND revision = ?
                """,
                (
                    int(enabled),
                    next_fire_at,
                    now,
                    trigger_id,
                    workspace_id,
                    expected_revision,
                ),
            )
            updated = connection.execute(
                "SELECT * FROM automation_trigger_bindings WHERE id = ?",
                (trigger_id,),
            ).fetchone()
            assert updated is not None
            return self._trigger_projection(updated)

    def claim_due_schedules(self, *, limit: int = 20) -> list[dict[str, Any]]:
        if not 1 <= limit <= 100:
            raise ContractViolation("schedule claim limit is invalid")
        claimed: list[dict[str, Any]] = []
        with self.store.write() as connection:
            now = utc_now()
            rows = list(
                connection.execute(
                    """
                    SELECT * FROM automation_trigger_bindings
                    WHERE trigger_type = 'schedule' AND enabled = 1
                      AND next_fire_at IS NOT NULL AND next_fire_at <= ?
                    ORDER BY next_fire_at, id
                    LIMIT ?
                    """,
                    (now, limit),
                )
            )
            for row in rows:
                config = _decode(row["config_json"])
                interval = int(config["interval_minutes"])
                connection.execute(
                    """
                    UPDATE automation_trigger_bindings
                    SET last_fired_at = ?, next_fire_at = ?, updated_at = ?
                    WHERE id = ? AND revision = ? AND enabled = 1
                    """,
                    (
                        now,
                        _after_minutes(interval),
                        now,
                        row["id"],
                        row["revision"],
                    ),
                )
                version = connection.execute(
                    "SELECT version, requires_model FROM automation_flow_versions WHERE id = ?",
                    (row["flow_version_id"],),
                ).fetchone()
                if version is None:
                    continue
                claimed.append(
                    {
                        **self._trigger_projection(row),
                        "workspace_id": row["workspace_id"],
                        "flow_version": int(version["version"]),
                        "requires_model": bool(version["requires_model"]),
                    }
                )
        return claimed

    # -- Workspace seed and snapshot ----------------------------------

    def seed_default(self, workspace_id: str, *, model: str) -> str:
        object_empty = {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        }
        analysis_input = {
            "type": "object",
            "properties": {"brief": {"type": "string", "minLength": 20, "maxLength": 4000}},
            "required": ["brief"],
            "additionalProperties": False,
        }
        analysis_output = {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "score": {"type": "number", "minimum": 0, "maximum": 1},
                "risks": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
            },
            "required": ["summary", "score", "risks"],
            "additionalProperties": False,
        }
        with self.store.write() as connection:
            needs_work_id = self._insert_action(
                connection,
                workspace_id,
                name="Needs-work response",
                slug="needs-work-response",
                description="Deterministically turns a low quality score into a bounded next action.",
                kind="template",
                input_schema={
                    "type": "object",
                    "properties": {"summary": {"type": "string"}},
                    "required": ["summary"],
                    "additionalProperties": False,
                },
                output_schema={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                    "additionalProperties": False,
                },
                config={"template": "Needs revision: {{summary}}"},
                agent_version_id=None,
                effect_level="none",
                created_by="bootstrap",
            )
            gate_id = self._insert_action(
                connection,
                workspace_id,
                name="Quality gate",
                slug="quality-gate",
                description="Routes launch briefs by the scored evidence returned by the Agent.",
                kind="condition",
                input_schema={
                    "type": "object",
                    "properties": {"score": {"type": "number", "minimum": 0, "maximum": 1}},
                    "required": ["score"],
                    "additionalProperties": False,
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "matched": {"type": "boolean"},
                        "actual": {"type": "number"},
                    },
                    "required": ["matched", "actual"],
                    "additionalProperties": False,
                },
                config={"path": "score", "operator": "gte", "value": 0.75},
                agent_version_id=None,
                effect_level="none",
                created_by="bootstrap",
            )
            approval_id = self._insert_action(
                connection,
                workspace_id,
                name="Human launch approval",
                slug="human-launch-approval",
                description="Pauses a Run and requires an attributable human decision.",
                kind="approval",
                input_schema=analysis_output,
                output_schema={
                    "type": "object",
                    "properties": {"approved": {"type": "boolean"}, "reason": {"type": "string"}},
                    "required": ["approved", "reason"],
                    "additionalProperties": False,
                },
                config={"message_template": "Approve this launch analysis? {{summary}}"},
                agent_version_id=None,
                effect_level="approval",
                created_by="bootstrap",
            )
            sandbox_id = self._insert_action(
                connection,
                workspace_id,
                name="Publish sandbox launch",
                slug="publish-sandbox-launch",
                description="Appends one idempotent launch record to the local sandbox ledger.",
                kind="sandbox",
                input_schema=analysis_output,
                output_schema={
                    "type": "object",
                    "properties": {
                        "effect_id": {"type": "string"},
                        "collection": {"type": "string"},
                    },
                    "required": ["effect_id", "collection"],
                    "additionalProperties": False,
                },
                config={"operation": "append_record", "collection": "approved_launches"},
                agent_version_id=None,
                effect_level="sandbox_write",
                created_by="bootstrap",
            )
            self._insert_action(
                connection,
                workspace_id,
                name="Normalize webhook intake",
                slug="normalize-webhook-intake",
                description="Maps an incoming value into a stable downstream contract.",
                kind="template",
                executor_kind="transform",
                input_schema={
                    "type": "object",
                    "properties": {
                        "value": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 4000,
                        }
                    },
                    "required": ["value"],
                    "additionalProperties": False,
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "normalized": {"type": "string"},
                        "source": {"type": "string"},
                    },
                    "required": ["normalized", "source"],
                    "additionalProperties": False,
                },
                config={
                    "operation": "map",
                    "mappings": {
                        "normalized": {"source": "input", "path": "value"},
                        "source": {"source": "literal", "value": "agent-studio"},
                    },
                },
                agent_version_id=None,
                effect_level="none",
                created_by="bootstrap",
            )
            self._insert_action(
                connection,
                workspace_id,
                name="Bounded cooldown",
                slug="bounded-cooldown",
                description="Pauses one worker briefly, then passes the validated input through.",
                kind="template",
                executor_kind="delay",
                input_schema={
                    "type": "object",
                    "properties": {
                        "value": {"type": "string", "maxLength": 4000}
                    },
                    "required": ["value"],
                    "additionalProperties": False,
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "value": {"type": "string", "maxLength": 4000}
                    },
                    "required": ["value"],
                    "additionalProperties": False,
                },
                config={"milliseconds": 250},
                agent_version_id=None,
                effect_level="none",
                created_by="bootstrap",
            )
            self._insert_action(
                connection,
                workspace_id,
                name="Readiness assertion",
                slug="readiness-assertion",
                description="Blocks execution when an explicit readiness threshold is not met.",
                kind="condition",
                executor_kind="assert",
                input_schema={
                    "type": "object",
                    "properties": {
                        "score": {"type": "number", "minimum": 0, "maximum": 1}
                    },
                    "required": ["score"],
                    "additionalProperties": False,
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "passed": {"type": "boolean"},
                        "actual": {"type": "number"},
                    },
                    "required": ["passed", "actual"],
                    "additionalProperties": False,
                },
                config={
                    "path": "score",
                    "operator": "gte",
                    "value": 0.75,
                    "message": "The readiness score is below the approved threshold.",
                },
                agent_version_id=None,
                effect_level="none",
                created_by="bootstrap",
            )
            self._insert_action(
                connection,
                workspace_id,
                name="Workspace evidence store",
                slug="workspace-evidence-store",
                description=(
                    "Appends one idempotent record to this workspace's SQLite sandbox."
                ),
                kind="sandbox",
                executor_kind="data_store",
                input_schema={
                    "type": "object",
                    "properties": {"record": {"type": "string", "maxLength": 4000}},
                    "required": ["record"],
                    "additionalProperties": False,
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "effect_id": {"type": "string"},
                        "collection": {"type": "string"},
                    },
                    "required": ["effect_id", "collection"],
                    "additionalProperties": False,
                },
                config={
                    "operation": "append_record",
                    "collection": "workspace-evidence",
                    "write_enabled": True,
                },
                agent_version_id=None,
                effect_level="sandbox_write",
                created_by="bootstrap",
            )
            version_by_action = {
                row["action_id"]: row["id"]
                for row in connection.execute(
                    "SELECT action_id, id FROM action_versions WHERE workspace_id = ?",
                    (workspace_id,),
                )
            }
            prompt_id = self.store._insert_prompt(
                connection,
                workspace_id,
                name="Launch analysis prompt",
                slug="launch-analysis",
                template=(
                    "Analyze this automation launch brief against an explicit readiness rubric: "
                    "target audience, typed input/output contract, deterministic decision boundary, "
                    "human authority boundary, bounded effect scope, inspectable evidence, and a "
                    "measurable success condition. When all seven are explicit and mutually "
                    "consistent, assign a score of at least 0.8. Return a concise summary, a "
                    "readiness score from 0 to 1, and bounded risks.\n\n"
                    "Brief: {{brief}}"
                ),
                variables=["brief"],
            )
            prompt_version = connection.execute(
                "SELECT id FROM prompt_versions WHERE prompt_id = ?", (prompt_id,)
            ).fetchone()
            if prompt_version is None:
                raise RuntimeError("seeded Prompt version is missing")
            skill_id = self.store._insert_skill(
                connection,
                workspace_id,
                name="Evidence-first launch analysis",
                slug="evidence-first-launch-analysis",
                instructions=(
                    "Treat Flow input and Action receipts as evidence. Never claim a side effect. "
                    "A human gate owns authorization."
                ),
                allowed_tools=[],
                allowed_action_version_ids=[version_by_action[needs_work_id]],
            )
            skill_version = connection.execute(
                "SELECT id FROM skill_versions WHERE skill_id = ?", (skill_id,)
            ).fetchone()
            if skill_version is None:
                raise RuntimeError("seeded Skill version is missing")
            agent_id = self.store._insert_agent(
                connection,
                workspace_id,
                name="Launch Analyst",
                slug="launch-analyst",
                role="executor",
                model=model,
                instructions="Analyze one launch brief and return only the pinned output contract.",
                prompt_version_id=prompt_version["id"],
                skill_version_ids=[skill_version["id"]],
            )
            agent_version = connection.execute(
                "SELECT id FROM agent_versions WHERE agent_id = ?", (agent_id,)
            ).fetchone()
            if agent_version is None:
                raise RuntimeError("seeded Agent version is missing")
            analyze_id = self._insert_action(
                connection,
                workspace_id,
                name="AI launch analysis",
                slug="ai-launch-analysis",
                description="Runs a pinned Agent, Prompt, and Skill through OpenAI Responses.",
                kind="ai",
                input_schema=analysis_input,
                output_schema=analysis_output,
                config={"max_tool_calls": 2, "reasoning_effort": "medium"},
                agent_version_id=agent_version["id"],
                effect_level="model",
                created_by="bootstrap",
            )
            version_by_action = {
                row["action_id"]: row["id"]
                for row in connection.execute(
                    "SELECT action_id, id FROM action_versions WHERE workspace_id = ?",
                    (workspace_id,),
                )
            }
            return self._insert_flow(
                connection,
                workspace_id,
                name="Agent-reviewed launch",
                slug="agent-reviewed-launch",
                description=(
                    "AI analysis → deterministic quality route → human approval → idempotent sandbox effect."
                ),
                input_schema=analysis_input,
                output_schema={
                    "type": "object",
                    "properties": {
                        "effect_id": {"type": "string"},
                        "collection": {"type": "string"},
                        "text": {"type": "string"},
                    },
                    "required": [],
                    "additionalProperties": False,
                },
                outcomes=default_outcomes_for_kind("flow"),
                start_node_id="analyze",
                nodes=[
                    {
                        "id": "analyze",
                        "type": "action",
                        "version_id": version_by_action[analyze_id],
                        "input_mapping": {"brief": {"source": "input", "path": "brief"}},
                    },
                    {
                        "id": "quality-gate",
                        "type": "action",
                        "version_id": version_by_action[gate_id],
                        "input_mapping": {
                            "score": {"source": "step", "node_id": "analyze", "path": "score"}
                        },
                    },
                    {
                        "id": "human-approval",
                        "type": "action",
                        "version_id": version_by_action[approval_id],
                        "input_mapping": {
                            key: {"source": "step", "node_id": "analyze", "path": key}
                            for key in ("summary", "score", "risks")
                        },
                    },
                    {
                        "id": "publish-sandbox",
                        "type": "action",
                        "version_id": version_by_action[sandbox_id],
                        "input_mapping": {
                            key: {"source": "step", "node_id": "analyze", "path": key}
                            for key in ("summary", "score", "risks")
                        },
                    },
                    {
                        "id": "needs-work",
                        "type": "action",
                        "version_id": version_by_action[needs_work_id],
                        "input_mapping": {
                            "summary": {"source": "step", "node_id": "analyze", "path": "summary"}
                        },
                    },
                ],
                routes=[
                    {"from": "analyze", "to": "quality-gate", "outcome": "success"},
                    {"from": "quality-gate", "to": "human-approval", "outcome": "true"},
                    {"from": "quality-gate", "to": "needs-work", "outcome": "false"},
                    {"from": "human-approval", "to": "publish-sandbox", "outcome": "approved"},
                ],
                created_by="bootstrap",
            )

    def snapshot(self, workspace_id: str) -> dict[str, Any]:
        with self.store.read() as connection:
            self.store._require_workspace(connection, workspace_id)
            actions = [
                self._action_projection(connection, row)
                for row in connection.execute(
                    "SELECT * FROM actions WHERE workspace_id = ? ORDER BY created_at, id",
                    (workspace_id,),
                )
            ]
            flows = [
                self._flow_projection(connection, row)
                for row in connection.execute(
                    "SELECT * FROM automation_flows WHERE workspace_id = ? ORDER BY created_at, id",
                    (workspace_id,),
                )
            ]
            triggers = [
                self._trigger_projection(row)
                for row in connection.execute(
                    "SELECT * FROM automation_trigger_bindings WHERE workspace_id = ? ORDER BY created_at, id",
                    (workspace_id,),
                )
            ]
            run_ids = [
                row["id"]
                for row in connection.execute(
                    "SELECT id FROM automation_runs WHERE workspace_id = ? ORDER BY created_at DESC LIMIT 30",
                    (workspace_id,),
                )
            ]
        return {
            "actions": actions,
            "flows": flows,
            "triggers": triggers,
            "runs": [self.get_run(workspace_id, run_id) for run_id in run_ids],
            "action_kinds": [
                "ai",
                "template",
                "transform",
                "delay",
                "condition",
                "router",
                "assert",
                "approval",
                "data_store",
            ],
            "route_outcomes": sorted(
                {
                    outcome["id"]
                    for action in actions
                    for outcome in action["version"]["outcomes"]
                }
            ),
        }

    # -- Runs, Steps, and evidence ------------------------------------

    def create_run(
        self,
        workspace_id: str,
        flow_id: str,
        *,
        input_data: Mapping[str, Any],
        flow_version: int | None = None,
        parent_run_id: str | None = None,
        parent_step_id: str | None = None,
        relation_kind: str | None = None,
        correlation_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[str, bool]:
        with self.store.write() as connection:
            flow = connection.execute(
                "SELECT * FROM automation_flows WHERE id = ? AND workspace_id = ?",
                (flow_id, workspace_id),
            ).fetchone()
            if flow is None:
                raise NotFound("Automation Flow was not found")
            if idempotency_key is not None:
                existing = connection.execute(
                    "SELECT id FROM automation_runs WHERE workspace_id = ? AND idempotency_key = ?",
                    (workspace_id, idempotency_key),
                ).fetchone()
                if existing is not None:
                    return str(existing["id"]), False
            selected = int(flow_version or flow["current_version"])
            version = connection.execute(
                "SELECT * FROM automation_flow_versions WHERE flow_id = ? AND version = ?",
                (flow_id, selected),
            ).fetchone()
            if version is None:
                raise NotFound("Automation Flow version was not found")
            parent = None
            normalized_relation = relation_kind or (
                "rerun" if parent_run_id is not None else "root"
            )
            if normalized_relation not in {"root", "rerun", "proof", "subflow"}:
                raise ContractViolation("Run relation kind is invalid")
            if parent_run_id is not None:
                parent = connection.execute(
                    "SELECT * FROM automation_runs WHERE id = ? AND workspace_id = ?",
                    (parent_run_id, workspace_id),
                ).fetchone()
                if parent is None:
                    raise ContractViolation("parent Run does not belong to the workspace")
                if normalized_relation == "subflow":
                    if parent["status"] != "running" or parent_step_id is None:
                        raise ContractViolation(
                            "subflow parent must be a running Run with a parent Step"
                        )
                    parent_step = connection.execute(
                        "SELECT id FROM automation_run_steps "
                        "WHERE id = ? AND run_id = ? AND workspace_id = ? AND status = 'running'",
                        (parent_step_id, parent_run_id, workspace_id),
                    ).fetchone()
                    if parent_step is None:
                        raise ContractViolation("subflow parent Step is not running")
                    depth = 1
                    ancestor_id = parent_run_id
                    while ancestor_id is not None:
                        ancestor = connection.execute(
                            "SELECT parent_run_id, relation_kind FROM automation_runs WHERE id = ?",
                            (ancestor_id,),
                        ).fetchone()
                        if ancestor is None or ancestor["parent_run_id"] is None:
                            break
                        if ancestor["relation_kind"] == "subflow":
                            depth += 1
                        ancestor_id = ancestor["parent_run_id"]
                    if depth > 4:
                        raise ContractViolation("subflow nesting exceeds four levels")
                elif (
                    parent["status"] not in TERMINAL_STATUSES
                    or parent["flow_id"] != flow_id
                ):
                    raise ContractViolation(
                        "rerun or proof parent must be a terminal Run of this Flow"
                    )
                if normalized_relation != "subflow" and parent_step_id is not None:
                    raise ContractViolation("only subflow Runs may pin a parent Step")
            elif normalized_relation != "root" or parent_step_id is not None:
                raise ContractViolation("root Run relation fields are inconsistent")
            run_id = new_id("arun")
            now = utc_now()
            correlation = correlation_id or (
                parent["correlation_id"] if parent is not None else new_id("corr")
            )
            connection.execute(
                """
                INSERT INTO automation_runs
                    (id, workspace_id, flow_id, flow_version_id, parent_run_id,
                     parent_step_id, relation_kind, correlation_id, idempotency_key,
                     status, revision, input_json,
                     current_node_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'created', 1, ?, ?, ?)
                """,
                (
                    run_id,
                    workspace_id,
                    flow_id,
                    version["id"],
                    parent_run_id,
                    parent_step_id,
                    normalized_relation,
                    correlation,
                    idempotency_key,
                    canonical_json(dict(input_data)),
                    version["start_node_id"],
                    now,
                ),
            )
            self._append_event(
                connection,
                workspace_id,
                run_id,
                event_type="run.created",
                actor_type="runtime",
                actor_id=None,
                payload={
                    "flow_id": flow_id,
                    "flow_version_id": version["id"],
                    "flow_version": selected,
                    "flow_fingerprint": version["fingerprint"],
                    "parent_run_id": parent_run_id,
                    "parent_step_id": parent_step_id,
                    "relation_kind": normalized_relation,
                    "correlation_id": correlation,
                },
            )
            self._append_event(
                connection,
                workspace_id,
                run_id,
                event_type="flow.version_pinned",
                actor_type="runtime",
                actor_id=None,
                payload={
                    "flow_version_id": version["id"],
                    "pinned_resources": _decode(version["pinned_resources_json"]),
                },
            )
        return run_id, True

    def transition_run(
        self,
        workspace_id: str,
        run_id: str,
        *,
        status: str,
        current_node_id: str | None = None,
        output: Any = None,
        outcome: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        with self.store.write() as connection:
            row = connection.execute(
                "SELECT * FROM automation_runs WHERE id = ? AND workspace_id = ?",
                (run_id, workspace_id),
            ).fetchone()
            if row is None:
                raise NotFound("Automation Run was not found")
            if row["status"] == status:
                return
            now = utc_now()
            started_at = row["started_at"] or (now if status == "running" else None)
            finished_at = now if status in TERMINAL_STATUSES else None
            cursor = connection.execute(
                """
                UPDATE automation_runs
                SET status = ?, revision = revision + 1, current_node_id = ?,
                    output_json = ?, outcome = ?, error_code = ?, error_message = ?,
                    started_at = ?, finished_at = ?
                WHERE id = ? AND workspace_id = ? AND revision = ?
                """,
                (
                    status,
                    current_node_id,
                    canonical_json(output) if output is not None else row["output_json"],
                    outcome if outcome is not None else row["outcome"],
                    error_code,
                    error_message,
                    started_at,
                    finished_at,
                    run_id,
                    workspace_id,
                    row["revision"],
                ),
            )
            if cursor.rowcount != 1:
                raise Conflict("Automation Run revision changed")
            self._append_event(
                connection,
                workspace_id,
                run_id,
                event_type="run.status_changed",
                actor_type="runtime",
                actor_id=None,
                payload={
                    "from": row["status"],
                    "to": status,
                    "revision": int(row["revision"]) + 1,
                    "current_node_id": current_node_id,
                    "error_code": error_code,
                    "outcome": outcome,
                },
            )

    def cancel_run(
        self, workspace_id: str, run_id: str, *, actor: str, reason: str
    ) -> dict[str, Any]:
        with self.store.write() as connection:
            run = connection.execute(
                "SELECT * FROM automation_runs WHERE id = ? AND workspace_id = ?",
                (run_id, workspace_id),
            ).fetchone()
            if run is None:
                raise NotFound("Automation Run was not found")
            if run["status"] in TERMINAL_STATUSES:
                return self.get_run(workspace_id, run_id)
            now = utc_now()
            step = connection.execute(
                """
                SELECT * FROM automation_run_steps
                WHERE run_id = ? AND status IN ('running', 'waiting_approval')
                ORDER BY started_at DESC, id DESC LIMIT 1
                """,
                (run_id,),
            ).fetchone()
            if step is not None:
                step_status = "failed" if step["status"] == "running" else "blocked"
                connection.execute(
                    """
                    UPDATE automation_run_steps
                    SET status = ?, revision = revision + 1, route_outcome = 'error',
                        error_code = 'cancelled', error_message = ?, finished_at = ?
                    WHERE id = ? AND revision = ?
                    """,
                    (step_status, reason, now, step["id"], step["revision"]),
                )
                self._append_event(
                    connection,
                    workspace_id,
                    run_id,
                    event_type="step.cancelled",
                    actor_type="human",
                    actor_id=actor,
                    payload={"step_id": step["id"], "node_id": step["node_id"], "reason": reason},
                )
            connection.execute(
                """
                UPDATE automation_runs
                SET status = 'cancelled', revision = revision + 1, current_node_id = NULL,
                    outcome = 'cancelled', error_code = 'cancelled',
                    error_message = ?, finished_at = ?
                WHERE id = ? AND revision = ?
                """,
                (reason, now, run_id, run["revision"]),
            )
            self._append_event(
                connection,
                workspace_id,
                run_id,
                event_type="run.cancelled",
                actor_type="human",
                actor_id=actor,
                payload={"reason": reason, "revision": int(run["revision"]) + 1},
            )
        return self.get_run(workspace_id, run_id)

    def start_step(
        self,
        workspace_id: str,
        run_id: str,
        *,
        node_id: str,
        node_type: str,
        target_version_id: str,
        input_data: Mapping[str, Any],
    ) -> str:
        with self.store.write() as connection:
            run = connection.execute(
                "SELECT status FROM automation_runs WHERE id = ? AND workspace_id = ?",
                (run_id, workspace_id),
            ).fetchone()
            if run is None:
                raise NotFound("Automation Run was not found")
            if run["status"] != "running":
                raise Conflict("Automation Run is not running")
            prior = connection.execute(
                "SELECT COALESCE(MAX(attempt), 0) AS attempt FROM automation_run_steps WHERE run_id = ? AND node_id = ?",
                (run_id, node_id),
            ).fetchone()
            attempt = int(prior["attempt"]) + 1
            step_id = new_id("astep")
            now = utc_now()
            connection.execute(
                """
                INSERT INTO automation_run_steps
                    (id, workspace_id, run_id, node_id, node_type, target_version_id,
                     attempt, status, revision, input_json, started_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'running', 1, ?, ?)
                """,
                (
                    step_id,
                    workspace_id,
                    run_id,
                    node_id,
                    node_type,
                    target_version_id,
                    attempt,
                    canonical_json(dict(input_data)),
                    now,
                ),
            )
            self._append_event(
                connection,
                workspace_id,
                run_id,
                event_type="step.started",
                actor_type="runtime",
                actor_id=None,
                payload={
                    "step_id": step_id,
                    "node_id": node_id,
                    "node_type": node_type,
                    "target_version_id": target_version_id,
                    "attempt": attempt,
                    "input_fingerprint": fingerprint(dict(input_data)),
                },
            )
            return step_id

    def finish_step(
        self,
        workspace_id: str,
        run_id: str,
        step_id: str,
        *,
        status: str,
        output: Any,
        route_outcome: str | None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        with self.store.write() as connection:
            row = connection.execute(
                "SELECT * FROM automation_run_steps WHERE id = ? AND run_id = ? AND workspace_id = ?",
                (step_id, run_id, workspace_id),
            ).fetchone()
            if row is None:
                raise NotFound("Automation Step was not found")
            cursor = connection.execute(
                """
                UPDATE automation_run_steps
                SET status = ?, revision = revision + 1, output_json = ?, route_outcome = ?,
                    error_code = ?, error_message = ?, finished_at = ?
                WHERE id = ? AND revision = ?
                """,
                (
                    status,
                    canonical_json(output) if output is not None else None,
                    route_outcome,
                    error_code,
                    error_message,
                    None if status == "waiting_approval" else utc_now(),
                    step_id,
                    row["revision"],
                ),
            )
            if cursor.rowcount != 1:
                raise Conflict("Automation Step revision changed")
            self._append_event(
                connection,
                workspace_id,
                run_id,
                event_type=f"step.{status}",
                actor_type="runtime",
                actor_id=None,
                payload={
                    "step_id": step_id,
                    "node_id": row["node_id"],
                    "attempt": row["attempt"],
                    "route_outcome": route_outcome,
                    "output_fingerprint": fingerprint(output) if output is not None else None,
                    "error_code": error_code,
                },
            )

    def append_event(
        self,
        workspace_id: str,
        run_id: str,
        *,
        event_type: str,
        actor_type: str,
        actor_id: str | None,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        with self.store.write() as connection:
            return self._append_event(
                connection,
                workspace_id,
                run_id,
                event_type=event_type,
                actor_type=actor_type,
                actor_id=actor_id,
                payload=payload,
            )

    def _append_event(
        self,
        connection: sqlite3.Connection,
        workspace_id: str,
        run_id: str,
        *,
        event_type: str,
        actor_type: str,
        actor_id: str | None,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        run = connection.execute(
            "SELECT id FROM automation_runs WHERE id = ? AND workspace_id = ?",
            (run_id, workspace_id),
        ).fetchone()
        if run is None:
            raise NotFound("Automation Run was not found")
        previous = connection.execute(
            "SELECT sequence, event_hash FROM automation_events WHERE run_id = ? ORDER BY sequence DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        event = {
            "id": new_id("aevt"),
            "run_id": run_id,
            "sequence": int(previous["sequence"]) + 1 if previous else 1,
            "occurred_at": utc_now(),
            "type": event_type,
            "actor_type": actor_type,
            "actor_id": actor_id,
            "payload": redact(dict(payload)),
            "prev_hash": previous["event_hash"] if previous else GENESIS_HASH,
        }
        event["event_hash"] = compute_event_hash(event)
        connection.execute(
            """
            INSERT INTO automation_events
                (id, workspace_id, run_id, sequence, occurred_at, type, actor_type,
                 actor_id, payload_json, prev_hash, event_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["id"],
                workspace_id,
                run_id,
                event["sequence"],
                event["occurred_at"],
                event["type"],
                actor_type,
                actor_id,
                canonical_json(event["payload"]),
                event["prev_hash"],
                event["event_hash"],
            ),
        )
        return event

    def record_model_call(
        self,
        workspace_id: str,
        run_id: str,
        step_id: str,
        *,
        agent_version_id: str,
        provider_response_id: str,
        status: str,
        model: str,
        input_hash: str,
        output_hash: str,
        usage: Mapping[str, Any],
        request_id: str | None,
    ) -> str:
        call_id = new_id("amcall")
        with self.store.write() as connection:
            connection.execute(
                """
                INSERT INTO automation_model_calls
                    (id, workspace_id, run_id, step_id, agent_version_id,
                     provider_response_id, status, model, input_hash, output_hash,
                     usage_json, request_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    call_id,
                    workspace_id,
                    run_id,
                    step_id,
                    agent_version_id,
                    provider_response_id,
                    status,
                    model,
                    input_hash,
                    output_hash,
                    canonical_json(dict(usage)),
                    request_id,
                    utc_now(),
                ),
            )
            connection.execute(
                "UPDATE workspaces SET model_calls_used = model_calls_used + 1 WHERE id = ?",
                (workspace_id,),
            )
            self._append_event(
                connection,
                workspace_id,
                run_id,
                event_type="model.completed" if status == "completed" else "model.failed",
                actor_type="agent",
                actor_id=agent_version_id,
                payload={
                    "model_call_id": call_id,
                    "step_id": step_id,
                    "provider_response_id": provider_response_id,
                    "status": status,
                    "model": model,
                    "usage": dict(usage),
                    "request_id": request_id,
                },
            )
        return call_id

    def record_receipt(
        self,
        workspace_id: str,
        run_id: str,
        step_id: str,
        *,
        node_id: str,
        action_version_id: str,
        attempt: int,
        outcome: str,
        input_data: Mapping[str, Any],
        output: Any,
        error_code: str | None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        with self.store.write() as connection:
            existing = connection.execute(
                "SELECT * FROM automation_action_receipts WHERE run_id = ? AND idempotency_key = ?",
                (run_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                return self._receipt_projection(existing)
            receipt_id = new_id("arcpt")
            now = utc_now()
            connection.execute(
                """
                INSERT INTO automation_action_receipts
                    (id, workspace_id, run_id, step_id, node_id, action_version_id,
                     attempt, outcome, input_json, output_json, error_code,
                     idempotency_key, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt_id,
                    workspace_id,
                    run_id,
                    step_id,
                    node_id,
                    action_version_id,
                    attempt,
                    outcome,
                    canonical_json(dict(input_data)),
                    canonical_json(output),
                    error_code,
                    idempotency_key,
                    now,
                ),
            )
            self._append_event(
                connection,
                workspace_id,
                run_id,
                event_type="action.receipted",
                actor_type="action",
                actor_id=action_version_id,
                payload={
                    "receipt_id": receipt_id,
                    "step_id": step_id,
                    "node_id": node_id,
                    "action_version_id": action_version_id,
                    "attempt": attempt,
                    "outcome": outcome,
                    "error_code": error_code,
                    "output_fingerprint": fingerprint(output),
                },
            )
            row = connection.execute(
                "SELECT * FROM automation_action_receipts WHERE id = ?", (receipt_id,)
            ).fetchone()
            assert row is not None
            return self._receipt_projection(row)

    def create_approval_request(
        self,
        workspace_id: str,
        run_id: str,
        step_id: str,
        *,
        node_id: str,
        message: str,
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        with self.store.write() as connection:
            existing = connection.execute(
                "SELECT * FROM automation_approval_requests WHERE step_id = ?",
                (step_id,),
            ).fetchone()
            if existing is not None:
                return self._approval_projection(connection, existing)
            request_id = new_id("areq")
            now = utc_now()
            connection.execute(
                """
                INSERT INTO automation_approval_requests
                    (id, workspace_id, run_id, step_id, node_id, message, context_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    workspace_id,
                    run_id,
                    step_id,
                    node_id,
                    message,
                    canonical_json(dict(context)),
                    now,
                ),
            )
            self._append_event(
                connection,
                workspace_id,
                run_id,
                event_type="approval.requested",
                actor_type="action",
                actor_id=None,
                payload={
                    "approval_request_id": request_id,
                    "step_id": step_id,
                    "node_id": node_id,
                    "message": message,
                },
            )
            row = connection.execute(
                "SELECT * FROM automation_approval_requests WHERE id = ?", (request_id,)
            ).fetchone()
            assert row is not None
            return self._approval_projection(connection, row)

    def decide_approval(
        self,
        workspace_id: str,
        request_id: str,
        *,
        approved: bool,
        actor: str,
        reason: str,
    ) -> str:
        with self.store.write() as connection:
            request = connection.execute(
                "SELECT * FROM automation_approval_requests WHERE id = ? AND workspace_id = ?",
                (request_id, workspace_id),
            ).fetchone()
            if request is None:
                raise NotFound("Approval request was not found")
            existing = connection.execute(
                "SELECT * FROM automation_approval_decisions WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if existing is not None:
                if (
                    bool(existing["approved"]) != approved
                    or existing["actor"] != actor
                    or existing["reason"] != reason
                ):
                    raise Conflict("Approval request already has a different decision")
                return str(request["run_id"])
            run = connection.execute(
                "SELECT * FROM automation_runs WHERE id = ? AND workspace_id = ?",
                (request["run_id"], workspace_id),
            ).fetchone()
            step = connection.execute(
                "SELECT * FROM automation_run_steps WHERE id = ?", (request["step_id"],)
            ).fetchone()
            if run is None or step is None:
                raise RuntimeError("Approval Run or Step is missing")
            if run["status"] != "waiting_approval" or step["status"] != "waiting_approval":
                raise Conflict("Approval request is no longer pending")
            decision_id = new_id("adec")
            now = utc_now()
            connection.execute(
                """
                INSERT INTO automation_approval_decisions
                    (id, workspace_id, request_id, approved, actor, reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (decision_id, workspace_id, request_id, int(approved), actor, reason, now),
            )
            step_status = "completed" if approved else "blocked"
            route = "approved" if approved else "rejected"
            connection.execute(
                """
                UPDATE automation_run_steps
                SET status = ?, revision = revision + 1, output_json = ?,
                    route_outcome = ?, finished_at = ?
                WHERE id = ? AND revision = ?
                """,
                (
                    step_status,
                    canonical_json({"approved": approved, "reason": reason}),
                    route,
                    now,
                    step["id"],
                    step["revision"],
                ),
            )
            next_status = "running" if approved else "blocked"
            next_node = run["current_node_id"] if approved else None
            connection.execute(
                """
                UPDATE automation_runs
                SET status = ?, revision = revision + 1, current_node_id = ?,
                    outcome = ?, error_code = ?, error_message = ?, finished_at = ?
                WHERE id = ? AND revision = ?
                """,
                (
                    next_status,
                    next_node,
                    None if approved else "rejected",
                    None if approved else "approval_rejected",
                    None if approved else reason,
                    None if approved else now,
                    run["id"],
                    run["revision"],
                ),
            )
            self._append_event(
                connection,
                workspace_id,
                run["id"],
                event_type="approval.decided",
                actor_type="human",
                actor_id=actor,
                payload={
                    "approval_request_id": request_id,
                    "approval_decision_id": decision_id,
                    "approved": approved,
                    "reason": reason,
                },
            )
            self._append_event(
                connection,
                workspace_id,
                run["id"],
                event_type="run.status_changed",
                actor_type="runtime",
                actor_id=None,
                payload={
                    "from": "waiting_approval",
                    "to": next_status,
                    "revision": int(run["revision"]) + 1,
                    "current_node_id": next_node,
                },
            )
            return str(run["id"])

    def get_approval_request(
        self, workspace_id: str, request_id: str
    ) -> dict[str, Any]:
        with self.store.read() as connection:
            row = connection.execute(
                "SELECT * FROM automation_approval_requests WHERE id = ? AND workspace_id = ?",
                (request_id, workspace_id),
            ).fetchone()
            if row is None:
                raise NotFound("Approval request was not found")
            return self._approval_projection(connection, row)

    def create_effect(
        self,
        workspace_id: str,
        run_id: str,
        step_id: str,
        *,
        action_version_id: str,
        collection: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        with self.store.write() as connection:
            existing = connection.execute(
                "SELECT * FROM automation_effects WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                return self._effect_projection(existing)
            effect_id = new_id("aeff")
            now = utc_now()
            connection.execute(
                """
                INSERT INTO automation_effects
                    (id, workspace_id, run_id, step_id, action_version_id,
                     collection, payload_json, idempotency_key, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    effect_id,
                    workspace_id,
                    run_id,
                    step_id,
                    action_version_id,
                    collection,
                    canonical_json(dict(payload)),
                    idempotency_key,
                    now,
                ),
            )
            self._append_event(
                connection,
                workspace_id,
                run_id,
                event_type="effect.committed",
                actor_type="action",
                actor_id=action_version_id,
                payload={
                    "effect_id": effect_id,
                    "step_id": step_id,
                    "collection": collection,
                    "payload_fingerprint": fingerprint(dict(payload)),
                },
            )
            row = connection.execute(
                "SELECT * FROM automation_effects WHERE id = ?", (effect_id,)
            ).fetchone()
            assert row is not None
            return self._effect_projection(row)

    # -- Evidence-bound maintenance ----------------------------------

    @staticmethod
    def _diagnosis_projection(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "run_id": row["run_id"],
            "failed_step_id": row["failed_step_id"],
            "failed_node_id": None,
            "action_version_id": row["action_version_id"],
            "fault_class": row["fault_class"],
            "root_cause": row["root_cause"],
            "explanation": row["explanation"],
            "confidence": int(row["confidence_milli"]) / 1_000,
            "evidence_event_ids": _decode(row["evidence_event_ids_json"]),
            "created_by_agent_version_id": row["created_by_agent_version_id"],
            "created_at": row["created_at"],
        }

    def record_diagnosis(
        self,
        workspace_id: str,
        run_id: str,
        *,
        failed_step_id: str,
        failed_node_id: str,
        action_version_id: str,
        fault_class: str,
        root_cause: str,
        explanation: str,
        confidence_milli: int,
        evidence_event_ids: Sequence[str],
        created_by_agent_version_id: str | None = None,
    ) -> dict[str, Any]:
        with self.store.write() as connection:
            existing = connection.execute(
                "SELECT * FROM automation_diagnoses WHERE run_id = ? AND workspace_id = ?",
                (run_id, workspace_id),
            ).fetchone()
            if existing is not None:
                projection = self._diagnosis_projection(existing)
                projection["failed_node_id"] = failed_node_id
                return projection
            run = connection.execute(
                "SELECT status FROM automation_runs WHERE id = ? AND workspace_id = ?",
                (run_id, workspace_id),
            ).fetchone()
            if run is None:
                raise NotFound("Automation Run was not found")
            if run["status"] not in {"blocked", "failed"}:
                raise ContractViolation("only a blocked or failed Run can be diagnosed")
            step = connection.execute(
                "SELECT node_id FROM automation_run_steps WHERE id = ? AND run_id = ?",
                (failed_step_id, run_id),
            ).fetchone()
            if step is None or step["node_id"] != failed_node_id:
                raise ContractViolation("diagnosis Step does not belong to the Run")
            owned_event_ids = {
                row["id"]
                for row in connection.execute(
                    "SELECT id FROM automation_events WHERE run_id = ?",
                    (run_id,),
                )
            }
            if not evidence_event_ids or not set(evidence_event_ids).issubset(owned_event_ids):
                raise ContractViolation("diagnosis cites evidence outside its Run")
            diagnosis_id = new_id("adiag")
            now = utc_now()
            connection.execute(
                """
                INSERT INTO automation_diagnoses
                    (id, workspace_id, run_id, failed_step_id, action_version_id,
                     fault_class, root_cause, explanation, confidence_milli,
                     evidence_event_ids_json, created_by_agent_version_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    diagnosis_id,
                    workspace_id,
                    run_id,
                    failed_step_id,
                    action_version_id,
                    fault_class,
                    root_cause,
                    explanation,
                    confidence_milli,
                    canonical_json(list(evidence_event_ids)),
                    created_by_agent_version_id,
                    now,
                ),
            )
            self._append_event(
                connection,
                workspace_id,
                run_id,
                event_type="maintenance.diagnosed",
                actor_type="agent" if created_by_agent_version_id else "runtime",
                actor_id=created_by_agent_version_id,
                payload={
                    "diagnosis_id": diagnosis_id,
                    "failed_step_id": failed_step_id,
                    "failed_node_id": failed_node_id,
                    "fault_class": fault_class,
                    "evidence_event_ids": list(evidence_event_ids),
                },
            )
            row = connection.execute(
                "SELECT * FROM automation_diagnoses WHERE id = ?", (diagnosis_id,)
            ).fetchone()
            assert row is not None
            projection = self._diagnosis_projection(row)
            projection["failed_node_id"] = failed_node_id
            return projection

    def get_diagnosis(self, workspace_id: str, diagnosis_id: str) -> dict[str, Any]:
        with self.store.read() as connection:
            row = connection.execute(
                "SELECT * FROM automation_diagnoses WHERE id = ? AND workspace_id = ?",
                (diagnosis_id, workspace_id),
            ).fetchone()
            if row is None:
                raise NotFound("Automation diagnosis was not found")
            step = connection.execute(
                "SELECT node_id FROM automation_run_steps WHERE id = ?",
                (row["failed_step_id"],),
            ).fetchone()
            projection = self._diagnosis_projection(row)
            projection["failed_node_id"] = step["node_id"] if step else None
            return projection

    def find_run_diagnosis(
        self, workspace_id: str, run_id: str
    ) -> dict[str, Any] | None:
        """Return the immutable diagnosis for a Run without creating another model call."""

        with self.store.read() as connection:
            row = connection.execute(
                "SELECT * FROM automation_diagnoses WHERE run_id = ? AND workspace_id = ?",
                (run_id, workspace_id),
            ).fetchone()
            if row is None:
                return None
            step = connection.execute(
                "SELECT node_id FROM automation_run_steps WHERE id = ?",
                (row["failed_step_id"],),
            ).fetchone()
            projection = self._diagnosis_projection(row)
            projection["failed_node_id"] = step["node_id"] if step else None
            return projection

    @staticmethod
    def _repair_projection(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "diagnosis_id": row["diagnosis_id"],
            "flow_id": row["flow_id"],
            "action_id": row["action_id"],
            "expected_flow_revision": row["expected_flow_revision"],
            "expected_action_version": row["expected_action_version"],
            "patch": _decode(row["patch_json"]),
            "proposal_hash": row["proposal_hash"],
            "status": row["status"],
            "applied_action_version_id": row["applied_action_version_id"],
            "applied_flow_version_id": row["applied_flow_version_id"],
            "created_at": row["created_at"],
            "applied_at": row["applied_at"],
        }

    def propose_repair(
        self, workspace_id: str, diagnosis_id: str
    ) -> dict[str, Any]:
        with self.store.write() as connection:
            existing = connection.execute(
                "SELECT * FROM automation_repair_proposals WHERE diagnosis_id = ? AND workspace_id = ?",
                (diagnosis_id, workspace_id),
            ).fetchone()
            if existing is not None:
                return self._repair_projection(existing)
            diagnosis = connection.execute(
                "SELECT * FROM automation_diagnoses WHERE id = ? AND workspace_id = ?",
                (diagnosis_id, workspace_id),
            ).fetchone()
            if diagnosis is None:
                raise NotFound("Automation diagnosis was not found")
            run = connection.execute(
                "SELECT * FROM automation_runs WHERE id = ?",
                (diagnosis["run_id"],),
            ).fetchone()
            action_version = connection.execute(
                "SELECT * FROM action_versions WHERE id = ? AND workspace_id = ?",
                (diagnosis["action_version_id"], workspace_id),
            ).fetchone()
            if run is None or action_version is None:
                raise RuntimeError("diagnosed Run or Action is missing")
            logical_kind = action_version["executor_kind"] or action_version["kind"]
            config = _decode(action_version["config_json"])
            if (
                diagnosis["fault_class"] != "authority_policy"
                or logical_kind != "data_store"
                or config.get("write_enabled") is not False
            ):
                raise ContractViolation("diagnosis has no bounded automatic repair")
            flow = connection.execute(
                "SELECT * FROM automation_flows WHERE id = ? AND workspace_id = ?",
                (run["flow_id"], workspace_id),
            ).fetchone()
            action = connection.execute(
                "SELECT * FROM actions WHERE id = ? AND workspace_id = ?",
                (action_version["action_id"], workspace_id),
            ).fetchone()
            if flow is None or action is None:
                raise RuntimeError("repair target is missing")
            patch = [
                {"op": "replace", "path": "/config/write_enabled", "value": True}
            ]
            material = {
                "diagnosis_id": diagnosis_id,
                "flow_id": flow["id"],
                "action_id": action["id"],
                "expected_flow_revision": int(flow["revision"]),
                "expected_action_version": int(action["current_version"]),
                "patch": patch,
            }
            proposal_id = new_id("arep")
            proposal_hash = fingerprint(material)
            now = utc_now()
            connection.execute(
                """
                INSERT INTO automation_repair_proposals
                    (id, workspace_id, diagnosis_id, flow_id, action_id,
                     expected_flow_revision, expected_action_version, patch_json,
                     proposal_hash, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?)
                """,
                (
                    proposal_id,
                    workspace_id,
                    diagnosis_id,
                    flow["id"],
                    action["id"],
                    flow["revision"],
                    action["current_version"],
                    canonical_json(patch),
                    proposal_hash,
                    now,
                ),
            )
            self._append_event(
                connection,
                workspace_id,
                run["id"],
                event_type="maintenance.repair_proposed",
                actor_type="runtime",
                actor_id=None,
                payload={
                    "proposal_id": proposal_id,
                    "proposal_hash": proposal_hash,
                    "expected_flow_revision": flow["revision"],
                    "expected_action_version": action["current_version"],
                    "patch": patch,
                },
            )
            row = connection.execute(
                "SELECT * FROM automation_repair_proposals WHERE id = ?",
                (proposal_id,),
            ).fetchone()
            assert row is not None
            return self._repair_projection(row)

    def get_repair(self, workspace_id: str, proposal_id: str) -> dict[str, Any]:
        with self.store.read() as connection:
            row = connection.execute(
                "SELECT * FROM automation_repair_proposals WHERE id = ? AND workspace_id = ?",
                (proposal_id, workspace_id),
            ).fetchone()
            if row is None:
                raise NotFound("Automation repair proposal was not found")
            projection = self._repair_projection(row)
            if row["applied_action_version_id"]:
                applied_action = connection.execute(
                    "SELECT version FROM action_versions WHERE id = ?",
                    (row["applied_action_version_id"],),
                ).fetchone()
                projection["applied_action_version"] = (
                    int(applied_action["version"]) if applied_action else None
                )
            if row["applied_flow_version_id"]:
                applied_flow = connection.execute(
                    "SELECT version FROM automation_flow_versions WHERE id = ?",
                    (row["applied_flow_version_id"],),
                ).fetchone()
                projection["applied_flow_version"] = (
                    int(applied_flow["version"]) if applied_flow else None
                )
            return projection

    def apply_repair(
        self,
        workspace_id: str,
        proposal_id: str,
        *,
        proposal_hash: str,
        expected_flow_revision: int,
        expected_action_version: int,
        actor: str,
        reason: str,
        acknowledged: bool,
    ) -> dict[str, Any]:
        with self.store.write() as connection:
            proposal = connection.execute(
                "SELECT * FROM automation_repair_proposals WHERE id = ? AND workspace_id = ?",
                (proposal_id, workspace_id),
            ).fetchone()
            if proposal is None:
                raise NotFound("Automation repair proposal was not found")
            if proposal["status"] == "applied":
                pass
            else:
                if (
                    proposal_hash != proposal["proposal_hash"]
                    or expected_flow_revision != proposal["expected_flow_revision"]
                    or expected_action_version != proposal["expected_action_version"]
                    or not acknowledged
                ):
                    raise Conflict("repair proposal hash, revision fence, or acknowledgement changed")
                flow = connection.execute(
                    "SELECT * FROM automation_flows WHERE id = ? AND workspace_id = ?",
                    (proposal["flow_id"], workspace_id),
                ).fetchone()
                action = connection.execute(
                    "SELECT * FROM actions WHERE id = ? AND workspace_id = ?",
                    (proposal["action_id"], workspace_id),
                ).fetchone()
                if flow is None or action is None:
                    raise RuntimeError("repair target is missing")
                if (
                    int(flow["revision"]) != expected_flow_revision
                    or int(action["current_version"]) != expected_action_version
                ):
                    raise Conflict("repair target advanced after proposal")
                old_action_version = connection.execute(
                    "SELECT * FROM action_versions WHERE action_id = ? AND version = ?",
                    (action["id"], action["current_version"]),
                ).fetchone()
                old_flow_version = connection.execute(
                    "SELECT * FROM automation_flow_versions WHERE flow_id = ? AND version = ?",
                    (flow["id"], flow["current_version"]),
                ).fetchone()
                diagnosis = connection.execute(
                    "SELECT * FROM automation_diagnoses WHERE id = ?",
                    (proposal["diagnosis_id"],),
                ).fetchone()
                if old_action_version is None or old_flow_version is None or diagnosis is None:
                    raise RuntimeError("repair pinned definition is missing")
                config = _decode(old_action_version["config_json"])
                if config.get("write_enabled") is not False:
                    raise Conflict("repair target no longer has the diagnosed policy")
                config["write_enabled"] = True
                next_action_version = int(action["current_version"]) + 1
                action_version_id = new_id("actv")
                now = utc_now()
                action_material = {
                    "kind": old_action_version["executor_kind"] or old_action_version["kind"],
                    "storage_kind": old_action_version["kind"],
                    "input_schema": _decode(old_action_version["input_schema_json"]),
                    "output_schema": _decode(old_action_version["output_schema_json"]),
                    "outcomes": (
                        _decode(old_action_version["outcomes_json"])
                        if old_action_version["outcomes_json"]
                        else default_outcomes_for_kind(
                            old_action_version["executor_kind"]
                            or old_action_version["kind"]
                        )
                    ),
                    "config": config,
                    "agent": None,
                    "effect_level": old_action_version["effect_level"],
                    "parent_version_id": old_action_version["id"],
                }
                connection.execute(
                    """
                    INSERT INTO action_versions
                        (id, workspace_id, action_id, version, kind, executor_kind,
                         input_schema_json, output_schema_json, outcomes_json, config_json,
                         agent_version_id, effect_level, fingerprint, created_by, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        action_version_id,
                        workspace_id,
                        action["id"],
                        next_action_version,
                        old_action_version["kind"],
                        old_action_version["executor_kind"],
                        old_action_version["input_schema_json"],
                        old_action_version["output_schema_json"],
                        old_action_version["outcomes_json"],
                        canonical_json(config),
                        old_action_version["agent_version_id"],
                        old_action_version["effect_level"],
                        fingerprint(action_material),
                        actor,
                        now,
                    ),
                )
                connection.execute(
                    "UPDATE actions SET current_version = current_version + 1, updated_at = ? WHERE id = ?",
                    (now, action["id"]),
                )
                nodes = _decode(old_flow_version["nodes_json"])
                failed_step = connection.execute(
                    "SELECT node_id FROM automation_run_steps WHERE id = ?",
                    (diagnosis["failed_step_id"],),
                ).fetchone()
                if failed_step is None:
                    raise RuntimeError("diagnosed Step is missing")
                replaced = 0
                for node in nodes:
                    if (
                        node["id"] == failed_step["node_id"]
                        and node["version_id"] == old_action_version["id"]
                    ):
                        node["version_id"] = action_version_id
                        replaced += 1
                if replaced != 1:
                    raise Conflict("repair target node is no longer uniquely pinned")
                pinned, requires_model = self._resolve_flow_pins(
                    connection, workspace_id, nodes, owner_flow_id=flow["id"]
                )
                next_flow_version = int(flow["current_version"]) + 1
                flow_version_id = new_id("aflowv")
                flow_material = {
                    "input_schema": _decode(old_flow_version["input_schema_json"]),
                    "output_schema": _decode(old_flow_version["output_schema_json"]),
                    "outcomes": (
                        _decode(old_flow_version["outcomes_json"])
                        if old_flow_version["outcomes_json"]
                        else default_outcomes_for_kind("flow")
                    ),
                    "start_node_id": old_flow_version["start_node_id"],
                    "nodes": nodes,
                    "routes": _decode(old_flow_version["routes_json"]),
                    "pinned_resources": pinned,
                    "parent_version_id": old_flow_version["id"],
                }
                connection.execute(
                    """
                    INSERT INTO automation_flow_versions
                        (id, workspace_id, flow_id, version, input_schema_json,
                         output_schema_json, outcomes_json, start_node_id, nodes_json,
                         routes_json, pinned_resources_json, requires_model, fingerprint,
                         parent_version_id, created_by, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        flow_version_id,
                        workspace_id,
                        flow["id"],
                        next_flow_version,
                        old_flow_version["input_schema_json"],
                        old_flow_version["output_schema_json"],
                        old_flow_version["outcomes_json"],
                        old_flow_version["start_node_id"],
                        canonical_json(nodes),
                        old_flow_version["routes_json"],
                        canonical_json(pinned),
                        int(requires_model),
                        fingerprint(flow_material),
                        old_flow_version["id"],
                        actor,
                        now,
                    ),
                )
                connection.execute(
                    """
                    UPDATE automation_flows
                    SET revision = revision + 1, current_version = current_version + 1,
                        updated_at = ?
                    WHERE id = ? AND revision = ?
                    """,
                    (now, flow["id"], expected_flow_revision),
                )
                decision_id = new_id("ardec")
                connection.execute(
                    """
                    INSERT INTO automation_repair_decisions
                        (id, workspace_id, proposal_id, proposal_hash, actor, reason,
                         acknowledged, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                    """,
                    (
                        decision_id,
                        workspace_id,
                        proposal_id,
                        proposal_hash,
                        actor,
                        reason,
                        now,
                    ),
                )
                connection.execute(
                    """
                    UPDATE automation_repair_proposals
                    SET status = 'applied', applied_action_version_id = ?,
                        applied_flow_version_id = ?, applied_at = ?
                    WHERE id = ? AND status = 'proposed'
                    """,
                    (action_version_id, flow_version_id, now, proposal_id),
                )
                self._append_event(
                    connection,
                    workspace_id,
                    diagnosis["run_id"],
                    event_type="maintenance.repair_applied",
                    actor_type="human",
                    actor_id=actor,
                    payload={
                        "proposal_id": proposal_id,
                        "proposal_hash": proposal_hash,
                        "action_version_id": action_version_id,
                        "flow_version_id": flow_version_id,
                        "reason": reason,
                    },
                )
        return self.get_repair(workspace_id, proposal_id)

    # -- Projections ---------------------------------------------------

    @staticmethod
    def _event_projection(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "run_id": row["run_id"],
            "sequence": row["sequence"],
            "occurred_at": row["occurred_at"],
            "type": row["type"],
            "actor_type": row["actor_type"],
            "actor_id": row["actor_id"],
            "payload": _decode(row["payload_json"]),
            "prev_hash": row["prev_hash"],
            "event_hash": row["event_hash"],
        }

    @staticmethod
    def _step_projection(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "node_id": row["node_id"],
            "node_type": row["node_type"],
            "target_version_id": row["target_version_id"],
            "attempt": row["attempt"],
            "status": row["status"],
            "revision": row["revision"],
            "input": _decode(row["input_json"]),
            "output": _decode(row["output_json"]),
            "route_outcome": row["route_outcome"],
            "error_code": row["error_code"],
            "error_message": row["error_message"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
        }

    @staticmethod
    def _model_call_projection(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "step_id": row["step_id"],
            "agent_version_id": row["agent_version_id"],
            "provider_response_id": row["provider_response_id"],
            "status": row["status"],
            "model": row["model"],
            "input_hash": row["input_hash"],
            "output_hash": row["output_hash"],
            "usage": _decode(row["usage_json"]),
            "request_id": row["request_id"],
            "created_at": row["created_at"],
        }

    @staticmethod
    def _receipt_projection(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "step_id": row["step_id"],
            "node_id": row["node_id"],
            "action_version_id": row["action_version_id"],
            "attempt": row["attempt"],
            "outcome": row["outcome"],
            "input": _decode(row["input_json"]),
            "output": _decode(row["output_json"]),
            "error_code": row["error_code"],
            "idempotency_key": row["idempotency_key"],
            "created_at": row["created_at"],
        }

    @staticmethod
    def _effect_projection(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "run_id": row["run_id"],
            "step_id": row["step_id"],
            "action_version_id": row["action_version_id"],
            "collection": row["collection"],
            "payload": _decode(row["payload_json"]),
            "created_at": row["created_at"],
        }

    def _approval_projection(
        self, connection: sqlite3.Connection, row: sqlite3.Row
    ) -> dict[str, Any]:
        decision = connection.execute(
            "SELECT * FROM automation_approval_decisions WHERE request_id = ?",
            (row["id"],),
        ).fetchone()
        return {
            "id": row["id"],
            "run_id": row["run_id"],
            "step_id": row["step_id"],
            "node_id": row["node_id"],
            "message": row["message"],
            "context": _decode(row["context_json"]),
            "created_at": row["created_at"],
            "decision": (
                {
                    "id": decision["id"],
                    "approved": bool(decision["approved"]),
                    "actor": decision["actor"],
                    "reason": decision["reason"],
                    "created_at": decision["created_at"],
                }
                if decision is not None
                else None
            ),
        }

    def get_run(self, workspace_id: str, run_id: str) -> dict[str, Any]:
        with self.store.read() as connection:
            row = connection.execute(
                """
                SELECT r.*, fv.version AS flow_version_number,
                       fv.fingerprint AS flow_fingerprint,
                       fv.start_node_id AS flow_start_node_id,
                       fv.output_schema_json AS flow_output_schema_json,
                       fv.outcomes_json AS flow_outcomes_json,
                       fv.nodes_json AS flow_nodes_json,
                       fv.routes_json AS flow_routes_json
                FROM automation_runs r
                JOIN automation_flow_versions fv ON fv.id = r.flow_version_id
                WHERE r.id = ? AND r.workspace_id = ?
                """,
                (run_id, workspace_id),
            ).fetchone()
            if row is None:
                raise NotFound("Automation Run was not found")
            steps = [
                self._step_projection(item)
                for item in connection.execute(
                    "SELECT * FROM automation_run_steps WHERE run_id = ? ORDER BY started_at, id",
                    (run_id,),
                )
            ]
            events = [
                self._event_projection(item)
                for item in connection.execute(
                    "SELECT * FROM automation_events WHERE run_id = ? ORDER BY sequence",
                    (run_id,),
                )
            ]
            calls = [
                self._model_call_projection(item)
                for item in connection.execute(
                    "SELECT * FROM automation_model_calls WHERE run_id = ? ORDER BY created_at, id",
                    (run_id,),
                )
            ]
            receipts = [
                self._receipt_projection(item)
                for item in connection.execute(
                    "SELECT * FROM automation_action_receipts WHERE run_id = ? ORDER BY created_at, id",
                    (run_id,),
                )
            ]
            approvals = [
                self._approval_projection(connection, item)
                for item in connection.execute(
                    "SELECT * FROM automation_approval_requests WHERE run_id = ? ORDER BY created_at, id",
                    (run_id,),
                )
            ]
            effects = [
                self._effect_projection(item)
                for item in connection.execute(
                    "SELECT * FROM automation_effects WHERE run_id = ? ORDER BY created_at, id",
                    (run_id,),
                )
            ]
            children = [
                {
                    "id": item["id"],
                    "flow_id": item["flow_id"],
                    "flow_version_id": item["flow_version_id"],
                    "parent_step_id": item["parent_step_id"],
                    "relation_kind": item["relation_kind"],
                    "status": item["status"],
                    "outcome": item["outcome"],
                    "error_code": item["error_code"],
                    "created_at": item["created_at"],
                    "finished_at": item["finished_at"],
                }
                for item in connection.execute(
                    "SELECT * FROM automation_runs WHERE parent_run_id = ? "
                    "ORDER BY created_at, id",
                    (run_id,),
                )
            ]
            pending = next(
                (approval for approval in reversed(approvals) if approval["decision"] is None),
                None,
            )
            diagnosis_row = connection.execute(
                "SELECT * FROM automation_diagnoses WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            diagnosis = None
            repair = None
            if diagnosis_row is not None:
                diagnosis = self._diagnosis_projection(diagnosis_row)
                diagnosed_step = connection.execute(
                    "SELECT node_id FROM automation_run_steps WHERE id = ?",
                    (diagnosis_row["failed_step_id"],),
                ).fetchone()
                diagnosis["failed_node_id"] = (
                    diagnosed_step["node_id"] if diagnosed_step is not None else None
                )
                repair_row = connection.execute(
                    "SELECT * FROM automation_repair_proposals WHERE diagnosis_id = ?",
                    (diagnosis_row["id"],),
                ).fetchone()
                if repair_row is not None:
                    repair = self._repair_projection(repair_row)
                    if repair_row["applied_action_version_id"]:
                        applied_action = connection.execute(
                            "SELECT version FROM action_versions WHERE id = ?",
                            (repair_row["applied_action_version_id"],),
                        ).fetchone()
                        repair["applied_action_version"] = (
                            int(applied_action["version"]) if applied_action else None
                        )
                    if repair_row["applied_flow_version_id"]:
                        applied_flow = connection.execute(
                            "SELECT version FROM automation_flow_versions WHERE id = ?",
                            (repair_row["applied_flow_version_id"],),
                        ).fetchone()
                        repair["applied_flow_version"] = (
                            int(applied_flow["version"]) if applied_flow else None
                        )
            return {
                "id": row["id"],
                "flow_id": row["flow_id"],
                "flow_version_id": row["flow_version_id"],
                "flow_version": row["flow_version_number"],
                "flow_fingerprint": row["flow_fingerprint"],
                "flow_graph": {
                    "start_node_id": row["flow_start_node_id"],
                    "output_schema": _decode(row["flow_output_schema_json"]),
                    "outcomes": (
                        _decode(row["flow_outcomes_json"])
                        if row["flow_outcomes_json"]
                        else default_outcomes_for_kind("flow")
                    ),
                    "nodes": _decode(row["flow_nodes_json"]),
                    "routes": _decode(row["flow_routes_json"]),
                },
                "parent_run_id": row["parent_run_id"],
                "parent_step_id": row["parent_step_id"],
                "relation_kind": row["relation_kind"],
                "correlation_id": row["correlation_id"],
                "status": row["status"],
                "revision": row["revision"],
                "input": _decode(row["input_json"]),
                "output": _decode(row["output_json"]),
                "outcome": row["outcome"],
                "current_node_id": row["current_node_id"],
                "error_code": row["error_code"],
                "error_message": row["error_message"],
                "created_at": row["created_at"],
                "started_at": row["started_at"],
                "finished_at": row["finished_at"],
                "steps": steps,
                "events": events,
                "model_calls": calls,
                "action_receipts": receipts,
                "approvals": approvals,
                "pending_approval": pending,
                "effects": effects,
                "children": children,
                "diagnosis": diagnosis,
                "repair": repair,
            }
