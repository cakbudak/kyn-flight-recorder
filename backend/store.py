"""Authoritative SQLite store and the only persistence mutation seam."""

from __future__ import annotations

import json
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from .contracts import (
    GENESIS_HASH,
    Conflict,
    ContractViolation,
    NotFound,
    Unauthorized,
    canonical_json,
    compute_event_hash,
    fingerprint,
    hash_text,
    new_id,
    redact,
    utc_now,
)
from .schema import SCHEMA_SQL


TERMINAL_RUN_STATUSES = frozenset({"blocked", "completed", "failed"})


def _decode(value: str) -> Any:
    return json.loads(value)


def _row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


class Store:
    """Connection-per-operation store with explicit, short write transactions."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self._transaction_state = threading.local()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect()
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.executescript(SCHEMA_SQL)
            skill_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(skill_versions)")
            }
            if "allowed_action_version_ids_json" not in skill_columns:
                connection.execute(
                    "ALTER TABLE skill_versions "
                    "ADD COLUMN allowed_action_version_ids_json TEXT NOT NULL DEFAULT '[]'"
                )
            action_version_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(action_versions)")
            }
            if "executor_kind" not in action_version_columns:
                connection.execute("ALTER TABLE action_versions ADD COLUMN executor_kind TEXT")
            if "outcomes_json" not in action_version_columns:
                connection.execute("ALTER TABLE action_versions ADD COLUMN outcomes_json TEXT")
            flow_version_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(automation_flow_versions)"
                )
            }
            if "output_schema_json" not in flow_version_columns:
                connection.execute(
                    "ALTER TABLE automation_flow_versions ADD COLUMN output_schema_json TEXT"
                )
            if "outcomes_json" not in flow_version_columns:
                connection.execute(
                    "ALTER TABLE automation_flow_versions ADD COLUMN outcomes_json TEXT"
                )
            run_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(automation_runs)")
            }
            if "parent_step_id" not in run_columns:
                connection.execute(
                    "ALTER TABLE automation_runs ADD COLUMN parent_step_id TEXT "
                    "REFERENCES automation_run_steps(id) ON DELETE RESTRICT"
                )
            if "relation_kind" not in run_columns:
                connection.execute(
                    "ALTER TABLE automation_runs ADD COLUMN relation_kind TEXT NOT NULL "
                    "DEFAULT 'root' CHECK (relation_kind IN "
                    "('root', 'rerun', 'proof', 'subflow'))"
                )
            if "outcome" not in run_columns:
                connection.execute("ALTER TABLE automation_runs ADD COLUMN outcome TEXT")
            self._migrate_studio_step_node_types(connection)
        finally:
            connection.close()

    @staticmethod
    def _migrate_studio_step_node_types(connection: sqlite3.Connection) -> None:
        """Add the truthful `flow` Step kind without rewriting any Step row."""

        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' "
            "AND name = 'automation_run_steps'"
        ).fetchone()
        if row is None or "'flow'" in str(row["sql"]):
            return
        connection.execute("PRAGMA foreign_keys=OFF")
        try:
            connection.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE automation_run_steps_v4 (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
                    run_id TEXT NOT NULL REFERENCES automation_runs(id) ON DELETE RESTRICT,
                    node_id TEXT NOT NULL,
                    node_type TEXT NOT NULL CHECK (node_type IN ('action', 'agent', 'flow')),
                    target_version_id TEXT NOT NULL,
                    attempt INTEGER NOT NULL CHECK (attempt >= 1),
                    status TEXT NOT NULL CHECK (status IN (
                        'running', 'waiting_approval', 'completed', 'blocked', 'failed', 'skipped'
                    )),
                    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
                    input_json TEXT NOT NULL,
                    output_json TEXT,
                    route_outcome TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    UNIQUE (run_id, node_id, attempt)
                );
                INSERT INTO automation_run_steps_v4 (
                    id, workspace_id, run_id, node_id, node_type, target_version_id,
                    attempt, status, revision, input_json, output_json, route_outcome,
                    error_code, error_message, started_at, finished_at
                )
                SELECT
                    id, workspace_id, run_id, node_id, node_type, target_version_id,
                    attempt, status, revision, input_json, output_json, route_outcome,
                    error_code, error_message, started_at, finished_at
                FROM automation_run_steps;
                DROP TABLE automation_run_steps;
                ALTER TABLE automation_run_steps_v4 RENAME TO automation_run_steps;
                CREATE INDEX ix_automation_steps_run
                ON automation_run_steps(run_id, started_at, id);
                CREATE TRIGGER trg_automation_steps_transition_shape
                BEFORE UPDATE OF status ON automation_run_steps
                WHEN NEW.status <> OLD.status
                AND NOT (
                    (OLD.status = 'running' AND NEW.status IN (
                        'waiting_approval', 'completed', 'blocked', 'failed', 'skipped'
                    )) OR
                    (OLD.status = 'waiting_approval' AND NEW.status IN ('completed', 'blocked'))
                )
                BEGIN SELECT RAISE(ABORT, 'illegal automation step status transition'); END;
                CREATE TRIGGER trg_automation_steps_revision_fence
                BEFORE UPDATE OF status ON automation_run_steps
                WHEN NEW.status <> OLD.status AND NEW.revision <> OLD.revision + 1
                BEGIN SELECT RAISE(ABORT, 'automation step transition must advance one revision'); END;
                COMMIT;
                """
            )
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.execute("PRAGMA foreign_keys=ON")
        violation = connection.execute("PRAGMA foreign_key_check").fetchone()
        if violation is not None:
            raise RuntimeError("Studio Step migration violated a foreign key")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=5.0,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        if self.in_write_transaction():
            raise RuntimeError("nested SQLite write transactions are forbidden")
        connection = self._connect()
        connection.execute("BEGIN IMMEDIATE")
        self._transaction_state.write_depth = 1
        try:
            yield connection
        except BaseException:
            connection.rollback()
            raise
        else:
            connection.commit()
        finally:
            self._transaction_state.write_depth = 0
            connection.close()

    def in_write_transaction(self) -> bool:
        return bool(getattr(self._transaction_state, "write_depth", 0))

    def table_names(self) -> set[str]:
        with self.read() as connection:
            return {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            }

    def count_rows(self, table: str) -> int:
        if table not in self.table_names():
            raise ContractViolation("unknown table")
        with self.read() as connection:
            row = connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()
            return int(row[0])

    # -- workspace ---------------------------------------------------------

    def create_workspace(self, *, lifetime_hours: int = 24) -> dict[str, Any]:
        workspace_id = new_id("ws")
        token = secrets.token_urlsafe(32)
        created_at = utc_now()
        expires_at = (
            datetime.now(UTC) + timedelta(hours=lifetime_hours)
        ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        with self.write() as connection:
            connection.execute(
                """
                INSERT INTO workspaces (id, token_hash, created_at, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                (workspace_id, hash_text(token), created_at, expires_at),
            )
        return {
            "id": workspace_id,
            "token": token,
            "created_at": created_at,
            "expires_at": expires_at,
        }

    def resolve_workspace(self, token: str) -> str:
        with self.read() as connection:
            row = connection.execute(
                """
                SELECT id FROM workspaces
                WHERE token_hash = ? AND expires_at > ?
                """,
                (hash_text(token), utc_now()),
            ).fetchone()
        if row is None:
            raise Unauthorized("workspace token is missing, invalid, or expired")
        return str(row["id"])

    @staticmethod
    def _require_workspace(connection: sqlite3.Connection, workspace_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT id, created_at, expires_at, model_calls_used FROM workspaces WHERE id = ?",
            (workspace_id,),
        ).fetchone()
        if row is None:
            raise NotFound("workspace was not found")
        return row

    # -- versioned resources ---------------------------------------------

    def create_prompt(
        self,
        workspace_id: str,
        *,
        name: str,
        slug: str,
        template: str,
        variables: Sequence[str],
    ) -> dict[str, Any]:
        with self.write() as connection:
            prompt_id = self._insert_prompt(
                connection,
                workspace_id,
                name=name,
                slug=slug,
                template=template,
                variables=variables,
            )
        return self.get_prompt(workspace_id, prompt_id)

    def _insert_prompt(
        self,
        connection: sqlite3.Connection,
        workspace_id: str,
        *,
        name: str,
        slug: str,
        template: str,
        variables: Sequence[str],
    ) -> str:
        self._require_workspace(connection, workspace_id)
        prompt_id = new_id("prm")
        version_id = new_id("prmv")
        created_at = utc_now()
        material = {"template": template, "variables": list(variables)}
        connection.execute(
            """
            INSERT INTO prompts (id, workspace_id, slug, name, current_version, created_at)
            VALUES (?, ?, ?, ?, 1, ?)
            """,
            (prompt_id, workspace_id, slug, name, created_at),
        )
        connection.execute(
            """
            INSERT INTO prompt_versions
                (id, workspace_id, prompt_id, version, template, variables_json, fingerprint, created_at)
            VALUES (?, ?, ?, 1, ?, ?, ?, ?)
            """,
            (
                version_id,
                workspace_id,
                prompt_id,
                template,
                canonical_json(list(variables)),
                fingerprint(material),
                created_at,
            ),
        )
        return prompt_id

    def get_prompt(self, workspace_id: str, prompt_id: str) -> dict[str, Any]:
        with self.read() as connection:
            row = connection.execute(
                "SELECT * FROM prompts WHERE id = ? AND workspace_id = ?",
                (prompt_id, workspace_id),
            ).fetchone()
            if row is None:
                raise NotFound("prompt was not found")
            return self._prompt_projection(connection, row)

    @staticmethod
    def _prompt_version_projection(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "version": row["version"],
            "template": row["template"],
            "variables": _decode(row["variables_json"]),
            "fingerprint": row["fingerprint"],
            "created_at": row["created_at"],
        }

    def _prompt_projection(
        self, connection: sqlite3.Connection, row: sqlite3.Row
    ) -> dict[str, Any]:
        version = connection.execute(
            "SELECT * FROM prompt_versions WHERE prompt_id = ? AND version = ?",
            (row["id"], row["current_version"]),
        ).fetchone()
        if version is None:
            raise RuntimeError("prompt current version is missing")
        return {
            "id": row["id"],
            "slug": row["slug"],
            "name": row["name"],
            "current_version": row["current_version"],
            "created_at": row["created_at"],
            "version": self._prompt_version_projection(version),
            "versions": [
                self._prompt_version_projection(item)
                for item in connection.execute(
                    "SELECT * FROM prompt_versions WHERE prompt_id = ? "
                    "ORDER BY version DESC",
                    (row["id"],),
                )
            ],
        }

    def revise_prompt(
        self,
        workspace_id: str,
        prompt_id: str,
        *,
        expected_version: int,
        name: str,
        template: str,
        variables: Sequence[str],
    ) -> dict[str, Any]:
        with self.write() as connection:
            prompt = connection.execute(
                "SELECT * FROM prompts WHERE id = ? AND workspace_id = ?",
                (prompt_id, workspace_id),
            ).fetchone()
            if prompt is None:
                raise NotFound("prompt was not found")
            if int(prompt["current_version"]) != expected_version:
                raise Conflict("prompt version changed")
            now = utc_now()
            material = {"template": template, "variables": list(variables)}
            connection.execute(
                """
                INSERT INTO prompt_versions
                    (id, workspace_id, prompt_id, version, template, variables_json,
                     fingerprint, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("prmv"),
                    workspace_id,
                    prompt_id,
                    expected_version + 1,
                    template,
                    canonical_json(list(variables)),
                    fingerprint(material),
                    now,
                ),
            )
            cursor = connection.execute(
                "UPDATE prompts SET name = ?, current_version = current_version + 1 "
                "WHERE id = ? AND workspace_id = ? AND current_version = ?",
                (name, prompt_id, workspace_id, expected_version),
            )
            if cursor.rowcount != 1:
                raise Conflict("prompt version changed")
        return self.get_prompt(workspace_id, prompt_id)

    def create_skill(
        self,
        workspace_id: str,
        *,
        name: str,
        slug: str,
        instructions: str,
        allowed_tools: Sequence[str],
        allowed_action_version_ids: Sequence[str] = (),
    ) -> dict[str, Any]:
        with self.write() as connection:
            skill_id = self._insert_skill(
                connection,
                workspace_id,
                name=name,
                slug=slug,
                instructions=instructions,
                allowed_tools=allowed_tools,
                allowed_action_version_ids=allowed_action_version_ids,
            )
        return self.get_skill(workspace_id, skill_id)

    def _insert_skill(
        self,
        connection: sqlite3.Connection,
        workspace_id: str,
        *,
        name: str,
        slug: str,
        instructions: str,
        allowed_tools: Sequence[str],
        allowed_action_version_ids: Sequence[str] = (),
    ) -> str:
        self._require_workspace(connection, workspace_id)
        skill_id = new_id("skl")
        version_id = new_id("sklv")
        created_at = utc_now()
        for action_version_id in allowed_action_version_ids:
            action = connection.execute(
                "SELECT id FROM action_versions WHERE id = ? AND workspace_id = ?",
                (action_version_id, workspace_id),
            ).fetchone()
            if action is None:
                raise ContractViolation("Skill Action version does not belong to the workspace")
        material = {
            "instructions": instructions,
            "allowed_tools": list(allowed_tools),
            "allowed_action_version_ids": list(allowed_action_version_ids),
        }
        connection.execute(
            """
            INSERT INTO skills (id, workspace_id, slug, name, current_version, created_at)
            VALUES (?, ?, ?, ?, 1, ?)
            """,
            (skill_id, workspace_id, slug, name, created_at),
        )
        connection.execute(
            """
            INSERT INTO skill_versions
                (id, workspace_id, skill_id, version, instructions, allowed_tools_json,
                 allowed_action_version_ids_json, fingerprint, created_at)
            VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?)
            """,
            (
                version_id,
                workspace_id,
                skill_id,
                instructions,
                canonical_json(list(allowed_tools)),
                canonical_json(list(allowed_action_version_ids)),
                fingerprint(material),
                created_at,
            ),
        )
        return skill_id

    def get_skill(self, workspace_id: str, skill_id: str) -> dict[str, Any]:
        with self.read() as connection:
            row = connection.execute(
                "SELECT * FROM skills WHERE id = ? AND workspace_id = ?",
                (skill_id, workspace_id),
            ).fetchone()
            if row is None:
                raise NotFound("skill was not found")
            return self._skill_projection(connection, row)

    @staticmethod
    def _skill_version_projection(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "version": row["version"],
            "instructions": row["instructions"],
            "allowed_tools": _decode(row["allowed_tools_json"]),
            "allowed_action_version_ids": _decode(
                row["allowed_action_version_ids_json"]
            ),
            "fingerprint": row["fingerprint"],
            "created_at": row["created_at"],
        }

    def _skill_projection(
        self, connection: sqlite3.Connection, row: sqlite3.Row
    ) -> dict[str, Any]:
        version = connection.execute(
            "SELECT * FROM skill_versions WHERE skill_id = ? AND version = ?",
            (row["id"], row["current_version"]),
        ).fetchone()
        if version is None:
            raise RuntimeError("skill current version is missing")
        return {
            "id": row["id"],
            "slug": row["slug"],
            "name": row["name"],
            "current_version": row["current_version"],
            "created_at": row["created_at"],
            "version": self._skill_version_projection(version),
            "versions": [
                self._skill_version_projection(item)
                for item in connection.execute(
                    "SELECT * FROM skill_versions WHERE skill_id = ? "
                    "ORDER BY version DESC",
                    (row["id"],),
                )
            ],
        }

    def revise_skill(
        self,
        workspace_id: str,
        skill_id: str,
        *,
        expected_version: int,
        name: str,
        instructions: str,
        allowed_tools: Sequence[str],
        allowed_action_version_ids: Sequence[str],
    ) -> dict[str, Any]:
        with self.write() as connection:
            skill = connection.execute(
                "SELECT * FROM skills WHERE id = ? AND workspace_id = ?",
                (skill_id, workspace_id),
            ).fetchone()
            if skill is None:
                raise NotFound("skill was not found")
            if int(skill["current_version"]) != expected_version:
                raise Conflict("skill version changed")
            for version_id in allowed_action_version_ids:
                owned = connection.execute(
                    "SELECT id FROM action_versions WHERE id = ? AND workspace_id = ?",
                    (version_id, workspace_id),
                ).fetchone()
                if owned is None:
                    raise ContractViolation(
                        "Skill Action version does not belong to the workspace"
                    )
            now = utc_now()
            material = {
                "instructions": instructions,
                "allowed_tools": list(allowed_tools),
                "allowed_action_version_ids": list(allowed_action_version_ids),
            }
            connection.execute(
                """
                INSERT INTO skill_versions
                    (id, workspace_id, skill_id, version, instructions,
                     allowed_tools_json, allowed_action_version_ids_json,
                     fingerprint, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("sklv"),
                    workspace_id,
                    skill_id,
                    expected_version + 1,
                    instructions,
                    canonical_json(list(allowed_tools)),
                    canonical_json(list(allowed_action_version_ids)),
                    fingerprint(material),
                    now,
                ),
            )
            cursor = connection.execute(
                "UPDATE skills SET name = ?, current_version = current_version + 1 "
                "WHERE id = ? AND workspace_id = ? AND current_version = ?",
                (name, skill_id, workspace_id, expected_version),
            )
            if cursor.rowcount != 1:
                raise Conflict("skill version changed")
        return self.get_skill(workspace_id, skill_id)

    def create_agent(
        self,
        workspace_id: str,
        *,
        name: str,
        slug: str,
        role: str,
        model: str,
        instructions: str,
        prompt_version_id: str,
        skill_version_ids: Sequence[str],
    ) -> dict[str, Any]:
        with self.write() as connection:
            agent_id = self._insert_agent(
                connection,
                workspace_id,
                name=name,
                slug=slug,
                role=role,
                model=model,
                instructions=instructions,
                prompt_version_id=prompt_version_id,
                skill_version_ids=skill_version_ids,
            )
        return self.get_agent(workspace_id, agent_id)

    def _insert_agent(
        self,
        connection: sqlite3.Connection,
        workspace_id: str,
        *,
        name: str,
        slug: str,
        role: str,
        model: str,
        instructions: str,
        prompt_version_id: str,
        skill_version_ids: Sequence[str],
    ) -> str:
        self._require_workspace(connection, workspace_id)
        prompt = connection.execute(
            "SELECT id, fingerprint FROM prompt_versions WHERE id = ? AND workspace_id = ?",
            (prompt_version_id, workspace_id),
        ).fetchone()
        if prompt is None:
            raise ContractViolation("agent prompt version does not belong to the workspace")
        skills: list[sqlite3.Row] = []
        for skill_version_id in skill_version_ids:
            skill = connection.execute(
                "SELECT id, fingerprint FROM skill_versions WHERE id = ? AND workspace_id = ?",
                (skill_version_id, workspace_id),
            ).fetchone()
            if skill is None:
                raise ContractViolation("agent skill version does not belong to the workspace")
            skills.append(skill)

        agent_id = new_id("agt")
        version_id = new_id("agtv")
        created_at = utc_now()
        material = {
            "role": role,
            "model": model,
            "instructions": instructions,
            "prompt": {"id": prompt_version_id, "fingerprint": prompt["fingerprint"]},
            "skills": [
                {"id": row["id"], "fingerprint": row["fingerprint"]} for row in skills
            ],
        }
        connection.execute(
            """
            INSERT INTO agents (id, workspace_id, slug, name, current_version, created_at)
            VALUES (?, ?, ?, ?, 1, ?)
            """,
            (agent_id, workspace_id, slug, name, created_at),
        )
        connection.execute(
            """
            INSERT INTO agent_versions
                (id, workspace_id, agent_id, version, role, model, instructions,
                 prompt_version_id, skill_version_ids_json, fingerprint, created_at)
            VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version_id,
                workspace_id,
                agent_id,
                role,
                model,
                instructions,
                prompt_version_id,
                canonical_json(list(skill_version_ids)),
                fingerprint(material),
                created_at,
            ),
        )
        return agent_id

    def get_agent(self, workspace_id: str, agent_id: str) -> dict[str, Any]:
        with self.read() as connection:
            row = connection.execute(
                "SELECT * FROM agents WHERE id = ? AND workspace_id = ?",
                (agent_id, workspace_id),
            ).fetchone()
            if row is None:
                raise NotFound("agent was not found")
            return self._agent_projection(connection, row)

    def _effective_tools(
        self, connection: sqlite3.Connection, skill_version_ids: Sequence[str]
    ) -> list[str]:
        tools: set[str] = set()
        for skill_version_id in skill_version_ids:
            row = connection.execute(
                "SELECT allowed_tools_json FROM skill_versions WHERE id = ?",
                (skill_version_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError("pinned skill version is missing")
            tools.update(_decode(row["allowed_tools_json"]))
        return sorted(tools)

    def _effective_actions(
        self, connection: sqlite3.Connection, skill_version_ids: Sequence[str]
    ) -> list[str]:
        action_versions: set[str] = set()
        for skill_version_id in skill_version_ids:
            row = connection.execute(
                "SELECT allowed_action_version_ids_json FROM skill_versions WHERE id = ?",
                (skill_version_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError("pinned skill version is missing")
            action_versions.update(_decode(row["allowed_action_version_ids_json"]))
        return sorted(action_versions)

    def _agent_version_projection(
        self, connection: sqlite3.Connection, row: sqlite3.Row
    ) -> dict[str, Any]:
        skill_ids = _decode(row["skill_version_ids_json"])
        return {
            "id": row["id"],
            "version": row["version"],
            "role": row["role"],
            "model": row["model"],
            "instructions": row["instructions"],
            "prompt_version_id": row["prompt_version_id"],
            "skill_version_ids": skill_ids,
            "effective_tools": self._effective_tools(connection, skill_ids),
            "effective_action_version_ids": self._effective_actions(
                connection, skill_ids
            ),
            "fingerprint": row["fingerprint"],
            "created_at": row["created_at"],
        }

    def _agent_projection(
        self, connection: sqlite3.Connection, row: sqlite3.Row
    ) -> dict[str, Any]:
        version = connection.execute(
            "SELECT * FROM agent_versions WHERE agent_id = ? AND version = ?",
            (row["id"], row["current_version"]),
        ).fetchone()
        if version is None:
            raise RuntimeError("agent current version is missing")
        return {
            "id": row["id"],
            "slug": row["slug"],
            "name": row["name"],
            "current_version": row["current_version"],
            "created_at": row["created_at"],
            "version": self._agent_version_projection(connection, version),
            "versions": [
                self._agent_version_projection(connection, item)
                for item in connection.execute(
                    "SELECT * FROM agent_versions WHERE agent_id = ? "
                    "ORDER BY version DESC",
                    (row["id"],),
                )
            ],
        }

    def revise_agent(
        self,
        workspace_id: str,
        agent_id: str,
        *,
        expected_version: int,
        name: str,
        role: str,
        model: str,
        instructions: str,
        prompt_version_id: str,
        skill_version_ids: Sequence[str],
    ) -> dict[str, Any]:
        with self.write() as connection:
            agent = connection.execute(
                "SELECT * FROM agents WHERE id = ? AND workspace_id = ?",
                (agent_id, workspace_id),
            ).fetchone()
            if agent is None:
                raise NotFound("agent was not found")
            if int(agent["current_version"]) != expected_version:
                raise Conflict("agent version changed")
            prompt = connection.execute(
                "SELECT id, fingerprint FROM prompt_versions "
                "WHERE id = ? AND workspace_id = ?",
                (prompt_version_id, workspace_id),
            ).fetchone()
            if prompt is None:
                raise ContractViolation(
                    "agent prompt version does not belong to the workspace"
                )
            skills: list[sqlite3.Row] = []
            for version_id in skill_version_ids:
                skill = connection.execute(
                    "SELECT id, fingerprint FROM skill_versions "
                    "WHERE id = ? AND workspace_id = ?",
                    (version_id, workspace_id),
                ).fetchone()
                if skill is None:
                    raise ContractViolation(
                        "agent skill version does not belong to the workspace"
                    )
                skills.append(skill)
            now = utc_now()
            material = {
                "role": role,
                "model": model,
                "instructions": instructions,
                "prompt": {
                    "id": prompt_version_id,
                    "fingerprint": prompt["fingerprint"],
                },
                "skills": [
                    {"id": row["id"], "fingerprint": row["fingerprint"]}
                    for row in skills
                ],
            }
            connection.execute(
                """
                INSERT INTO agent_versions
                    (id, workspace_id, agent_id, version, role, model, instructions,
                     prompt_version_id, skill_version_ids_json, fingerprint, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("agtv"),
                    workspace_id,
                    agent_id,
                    expected_version + 1,
                    role,
                    model,
                    instructions,
                    prompt_version_id,
                    canonical_json(list(skill_version_ids)),
                    fingerprint(material),
                    now,
                ),
            )
            cursor = connection.execute(
                "UPDATE agents SET name = ?, current_version = current_version + 1 "
                "WHERE id = ? AND workspace_id = ? AND current_version = ?",
                (name, agent_id, workspace_id, expected_version),
            )
            if cursor.rowcount != 1:
                raise Conflict("agent version changed")
        return self.get_agent(workspace_id, agent_id)

    def create_flow(
        self,
        workspace_id: str,
        *,
        name: str,
        slug: str,
        executor_agent_version_id: str,
        diagnostician_agent_version_id: str,
        repairer_agent_version_id: str,
        request: Mapping[str, Any],
        policy: Mapping[str, Any],
        repair_policy: Mapping[str, Any],
        created_by: str = "user",
    ) -> dict[str, Any]:
        with self.write() as connection:
            flow_id = self._insert_flow(
                connection,
                workspace_id,
                name=name,
                slug=slug,
                executor_agent_version_id=executor_agent_version_id,
                diagnostician_agent_version_id=diagnostician_agent_version_id,
                repairer_agent_version_id=repairer_agent_version_id,
                request=request,
                policy=policy,
                repair_policy=repair_policy,
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
        executor_agent_version_id: str,
        diagnostician_agent_version_id: str,
        repairer_agent_version_id: str,
        request: Mapping[str, Any],
        policy: Mapping[str, Any],
        repair_policy: Mapping[str, Any],
        created_by: str,
    ) -> str:
        self._require_workspace(connection, workspace_id)
        role_ids = {
            "executor": executor_agent_version_id,
            "diagnostician": diagnostician_agent_version_id,
            "repairer": repairer_agent_version_id,
        }
        agent_material: dict[str, Any] = {}
        for role, version_id in role_ids.items():
            row = connection.execute(
                """
                SELECT id, role, fingerprint FROM agent_versions
                WHERE id = ? AND workspace_id = ?
                """,
                (version_id, workspace_id),
            ).fetchone()
            if row is None or row["role"] != role:
                raise ContractViolation(f"flow {role} pin is invalid")
            agent_material[role] = {"id": row["id"], "fingerprint": row["fingerprint"]}

        flow_id = new_id("flow")
        version_id = new_id("flowv")
        created_at = utc_now()
        material = self._flow_material(
            agent_material,
            request=request,
            policy=policy,
            repair_policy=repair_policy,
        )
        connection.execute(
            """
            INSERT INTO flows
                (id, workspace_id, slug, name, revision, current_version, created_at, updated_at)
            VALUES (?, ?, ?, ?, 1, 1, ?, ?)
            """,
            (flow_id, workspace_id, slug, name, created_at, created_at),
        )
        connection.execute(
            """
            INSERT INTO flow_versions
                (id, workspace_id, flow_id, version, executor_agent_version_id,
                 diagnostician_agent_version_id, repairer_agent_version_id, request_json,
                 policy_json, repair_policy_json, fingerprint, parent_version_id,
                 created_by, created_at)
            VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
            """,
            (
                version_id,
                workspace_id,
                flow_id,
                executor_agent_version_id,
                diagnostician_agent_version_id,
                repairer_agent_version_id,
                canonical_json(dict(request)),
                canonical_json(dict(policy)),
                canonical_json(dict(repair_policy)),
                fingerprint(material),
                created_by,
                created_at,
            ),
        )
        return flow_id

    @staticmethod
    def _flow_material(
        agents: Mapping[str, Any],
        *,
        request: Mapping[str, Any],
        policy: Mapping[str, Any],
        repair_policy: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            "agents": dict(agents),
            "request": dict(request),
            "policy": dict(policy),
            "repair_policy": dict(repair_policy),
        }

    def _flow_version_projection(
        self, connection: sqlite3.Connection, row: sqlite3.Row
    ) -> dict[str, Any]:
        return {
            "id": row["id"],
            "version": row["version"],
            "executor_agent_version_id": row["executor_agent_version_id"],
            "diagnostician_agent_version_id": row["diagnostician_agent_version_id"],
            "repairer_agent_version_id": row["repairer_agent_version_id"],
            "request": _decode(row["request_json"]),
            "policy": _decode(row["policy_json"]),
            "repair_policy": _decode(row["repair_policy_json"]),
            "fingerprint": row["fingerprint"],
            "parent_version_id": row["parent_version_id"],
            "created_by": row["created_by"],
            "created_at": row["created_at"],
        }

    def _flow_projection(
        self, connection: sqlite3.Connection, row: sqlite3.Row
    ) -> dict[str, Any]:
        version = connection.execute(
            "SELECT * FROM flow_versions WHERE flow_id = ? AND version = ?",
            (row["id"], row["current_version"]),
        ).fetchone()
        if version is None:
            raise RuntimeError("flow current version is missing")
        return {
            "id": row["id"],
            "slug": row["slug"],
            "name": row["name"],
            "revision": row["revision"],
            "current_version": row["current_version"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "version": self._flow_version_projection(connection, version),
        }

    def get_flow(self, workspace_id: str, flow_id: str) -> dict[str, Any]:
        with self.read() as connection:
            row = connection.execute(
                "SELECT * FROM flows WHERE id = ? AND workspace_id = ?",
                (flow_id, workspace_id),
            ).fetchone()
            if row is None:
                raise NotFound("flow was not found")
            return self._flow_projection(connection, row)

    def get_flow_version(
        self, workspace_id: str, flow_id: str, version: int
    ) -> dict[str, Any]:
        with self.read() as connection:
            row = connection.execute(
                """
                SELECT * FROM flow_versions
                WHERE workspace_id = ? AND flow_id = ? AND version = ?
                """,
                (workspace_id, flow_id, version),
            ).fetchone()
            if row is None:
                raise NotFound("flow version was not found")
            return self._flow_version_projection(connection, row)

    def seed_default_lab(self, workspace_id: str, *, model: str) -> str:
        with self.write() as connection:
            executor_prompt_id = self._insert_prompt(
                connection,
                workspace_id,
                name="Release execution prompt",
                slug="release-execution",
                template=(
                    "Goal: {{goal}}\nArtifact: {{artifact}}\nRequested environment: "
                    "{{requested_environment}}\nPinned policy: {{policy_json}}\n"
                    "Inspect the policy, then request the sandbox release tool. Treat tool receipts as truth."
                ),
                variables=["goal", "artifact", "requested_environment", "policy_json"],
            )
            diagnosis_prompt_id = self._insert_prompt(
                connection,
                workspace_id,
                name="Evidence diagnosis prompt",
                slug="evidence-diagnosis",
                template=(
                    "Analyze only this deterministic candidate: {{candidate_json}}\n"
                    "Citable events: {{evidence_json}}\nReturn the required structured diagnosis."
                ),
                variables=["candidate_json", "evidence_json"],
            )
            repair_prompt_id = self._insert_prompt(
                connection,
                workspace_id,
                name="Bounded repair prompt",
                slug="bounded-repair",
                template=(
                    "Diagnosis: {{diagnosis_json}}\nCurrent manifest: {{manifest_json}}\n"
                    "Repair policy: {{repair_policy_json}}\nPropose the smallest permitted patch."
                ),
                variables=["diagnosis_json", "manifest_json", "repair_policy_json"],
            )

            executor_skill_id = self._insert_skill(
                connection,
                workspace_id,
                name="Safe release staging",
                slug="safe-release-staging",
                instructions=(
                    "Always inspect the pinned release policy before staging. Request exactly the declared "
                    "environment and artifact. Never infer success from prose; use the tool receipt."
                ),
                allowed_tools=["inspect_release_policy", "stage_release"],
            )
            diagnosis_skill_id = self._insert_skill(
                connection,
                workspace_id,
                name="Evidence forensics",
                slug="evidence-forensics",
                instructions=(
                    "Every causal claim must cite event ids in the supplied packet. Explain why retrying "
                    "unchanged configuration cannot repair a deterministic policy denial."
                ),
                allowed_tools=[],
            )
            repair_skill_id = self._insert_skill(
                connection,
                workspace_id,
                name="Bounded manifest repair",
                slug="bounded-manifest-repair",
                instructions=(
                    "Change only an explicitly permitted manifest path, preserve existing access, and emit "
                    "one minimal structured patch. You cannot apply it."
                ),
                allowed_tools=[],
            )

            prompt_versions = {
                row["prompt_id"]: row["id"]
                for row in connection.execute(
                    "SELECT id, prompt_id FROM prompt_versions WHERE workspace_id = ?",
                    (workspace_id,),
                )
            }
            skill_versions = {
                row["skill_id"]: row["id"]
                for row in connection.execute(
                    "SELECT id, skill_id FROM skill_versions WHERE workspace_id = ?",
                    (workspace_id,),
                )
            }

            executor_id = self._insert_agent(
                connection,
                workspace_id,
                name="Release Sentinel",
                slug="release-sentinel",
                role="executor",
                model=model,
                instructions="Execute one safe release task through the granted local tools.",
                prompt_version_id=prompt_versions[executor_prompt_id],
                skill_version_ids=[skill_versions[executor_skill_id]],
            )
            diagnostician_id = self._insert_agent(
                connection,
                workspace_id,
                name="Run Forensicist",
                slug="run-forensicist",
                role="diagnostician",
                model=model,
                instructions="Diagnose the recorded failure without inventing evidence.",
                prompt_version_id=prompt_versions[diagnosis_prompt_id],
                skill_version_ids=[skill_versions[diagnosis_skill_id]],
            )
            repairer_id = self._insert_agent(
                connection,
                workspace_id,
                name="Manifest Repairer",
                slug="manifest-repairer",
                role="repairer",
                model=model,
                instructions="Propose, but never authorize, the smallest evidence-backed repair.",
                prompt_version_id=prompt_versions[repair_prompt_id],
                skill_version_ids=[skill_versions[repair_skill_id]],
            )
            agent_versions = {
                row["agent_id"]: row["id"]
                for row in connection.execute(
                    "SELECT id, agent_id FROM agent_versions WHERE workspace_id = ?",
                    (workspace_id,),
                )
            }
            return self._insert_flow(
                connection,
                workspace_id,
                name="Production release guard",
                slug="production-release-guard",
                executor_agent_version_id=agent_versions[executor_id],
                diagnostician_agent_version_id=agent_versions[diagnostician_id],
                repairer_agent_version_id=agent_versions[repairer_id],
                request={
                    "goal": "Stage the Build Week release in the requested sandbox environment.",
                    "artifact": "kyn-console@buildweek",
                    "environment": "production",
                },
                policy={"allowed_environments": ["staging"]},
                repair_policy={
                    "allowed_paths": ["/policy/allowed_environments"],
                    "allowed_operations": ["replace"],
                    "max_operations": 1,
                },
                created_by="bootstrap",
            )

    def workspace_snapshot(self, workspace_id: str) -> dict[str, Any]:
        with self.read() as connection:
            workspace = self._require_workspace(connection, workspace_id)
            prompts = [
                self._prompt_projection(connection, row)
                for row in connection.execute(
                    "SELECT * FROM prompts WHERE workspace_id = ? ORDER BY created_at, id",
                    (workspace_id,),
                )
            ]
            skills = [
                self._skill_projection(connection, row)
                for row in connection.execute(
                    "SELECT * FROM skills WHERE workspace_id = ? ORDER BY created_at, id",
                    (workspace_id,),
                )
            ]
            agents = [
                self._agent_projection(connection, row)
                for row in connection.execute(
                    "SELECT * FROM agents WHERE workspace_id = ? ORDER BY created_at, id",
                    (workspace_id,),
                )
            ]
            flows = [
                self._flow_projection(connection, row)
                for row in connection.execute(
                    "SELECT * FROM flows WHERE workspace_id = ? ORDER BY created_at, id",
                    (workspace_id,),
                )
            ]
            run_ids = [
                row["id"]
                for row in connection.execute(
                    "SELECT id FROM runs WHERE workspace_id = ? ORDER BY created_at DESC LIMIT 20",
                    (workspace_id,),
                )
            ]
        return {
            "workspace": {
                "id": workspace["id"],
                "created_at": workspace["created_at"],
                "expires_at": workspace["expires_at"],
                "model_calls_used": workspace["model_calls_used"],
            },
            "prompts": prompts,
            "skills": skills,
            "agents": agents,
            "flows": flows,
            "runs": [self.get_run(workspace_id, run_id) for run_id in run_ids],
        }

    # -- runtime context and ledger --------------------------------------

    def flow_runtime_context(
        self, workspace_id: str, flow_id: str, version: int | None = None
    ) -> dict[str, Any]:
        with self.read() as connection:
            flow = connection.execute(
                "SELECT * FROM flows WHERE id = ? AND workspace_id = ?",
                (flow_id, workspace_id),
            ).fetchone()
            if flow is None:
                raise NotFound("flow was not found")
            selected_version = int(version or flow["current_version"])
            flow_version = connection.execute(
                "SELECT * FROM flow_versions WHERE flow_id = ? AND version = ?",
                (flow_id, selected_version),
            ).fetchone()
            if flow_version is None:
                raise NotFound("flow version was not found")
            projection = self._flow_version_projection(connection, flow_version)
            agents: dict[str, Any] = {}
            for role in ("executor", "diagnostician", "repairer"):
                version_id = flow_version[f"{role}_agent_version_id"]
                agents[role] = self._agent_runtime_projection(connection, version_id)
            return {
                "flow": {
                    "id": flow["id"],
                    "name": flow["name"],
                    "slug": flow["slug"],
                    "revision": flow["revision"],
                    "current_version": flow["current_version"],
                },
                "version": projection,
                "agents": agents,
            }

    def _agent_runtime_projection(
        self, connection: sqlite3.Connection, version_id: str
    ) -> dict[str, Any]:
        version = connection.execute(
            "SELECT * FROM agent_versions WHERE id = ?",
            (version_id,),
        ).fetchone()
        if version is None:
            raise RuntimeError("pinned agent version is missing")
        agent = connection.execute(
            "SELECT name, slug FROM agents WHERE id = ?",
            (version["agent_id"],),
        ).fetchone()
        prompt = connection.execute(
            "SELECT * FROM prompt_versions WHERE id = ?",
            (version["prompt_version_id"],),
        ).fetchone()
        if agent is None or prompt is None:
            raise RuntimeError("pinned agent resource is incomplete")
        skill_ids = _decode(version["skill_version_ids_json"])
        skills: list[dict[str, Any]] = []
        for skill_id in skill_ids:
            skill = connection.execute(
                "SELECT * FROM skill_versions WHERE id = ?",
                (skill_id,),
            ).fetchone()
            if skill is None:
                raise RuntimeError("pinned skill version is missing")
            skills.append(self._skill_version_projection(skill))
        return {
            "id": version["id"],
            "agent_id": version["agent_id"],
            "name": agent["name"],
            "slug": agent["slug"],
            "version": version["version"],
            "role": version["role"],
            "model": version["model"],
            "instructions": version["instructions"],
            "fingerprint": version["fingerprint"],
            "prompt": self._prompt_version_projection(prompt),
            "skills": skills,
            "effective_tools": self._effective_tools(connection, skill_ids),
            "effective_action_version_ids": self._effective_actions(
                connection, skill_ids
            ),
        }

    def create_run(
        self,
        workspace_id: str,
        flow_id: str,
        *,
        flow_version: int | None = None,
        parent_run_id: str | None = None,
        correlation_id: str | None = None,
    ) -> tuple[str, bool]:
        with self.write() as connection:
            self._require_workspace(connection, workspace_id)
            flow = connection.execute(
                "SELECT * FROM flows WHERE id = ? AND workspace_id = ?",
                (flow_id, workspace_id),
            ).fetchone()
            if flow is None:
                raise NotFound("flow was not found")
            selected_version = int(flow_version or flow["current_version"])
            version = connection.execute(
                "SELECT * FROM flow_versions WHERE flow_id = ? AND version = ?",
                (flow_id, selected_version),
            ).fetchone()
            if version is None:
                raise NotFound("flow version was not found")

            parent: sqlite3.Row | None = None
            if parent_run_id is not None:
                parent = connection.execute(
                    "SELECT * FROM runs WHERE id = ? AND workspace_id = ? AND flow_id = ?",
                    (parent_run_id, workspace_id, flow_id),
                ).fetchone()
                if parent is None:
                    raise ContractViolation("parent run does not belong to this flow and workspace")
                existing_child = connection.execute(
                    "SELECT id FROM runs WHERE parent_run_id = ? AND workspace_id = ?",
                    (parent_run_id, workspace_id),
                ).fetchone()
                if existing_child is not None:
                    return str(existing_child["id"]), False

            request = _decode(version["request_json"])
            run_id = new_id("run")
            now = utc_now()
            correlation = correlation_id or (parent["correlation_id"] if parent else new_id("corr"))
            connection.execute(
                """
                INSERT INTO runs
                    (id, workspace_id, flow_id, flow_version_id, parent_run_id, correlation_id,
                     status, revision, goal, requested_environment, created_at, started_at)
                VALUES (?, ?, ?, ?, ?, ?, 'running', 1, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    workspace_id,
                    flow_id,
                    version["id"],
                    parent_run_id,
                    correlation,
                    request["goal"],
                    request["environment"],
                    now,
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
                    "flow_version": selected_version,
                    "flow_fingerprint": version["fingerprint"],
                    "parent_run_id": parent_run_id,
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
                    "executor_agent_version_id": version["executor_agent_version_id"],
                    "diagnostician_agent_version_id": version["diagnostician_agent_version_id"],
                    "repairer_agent_version_id": version["repairer_agent_version_id"],
                },
            )
        return run_id, True

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
        event_id: str | None = None,
    ) -> dict[str, Any]:
        run = connection.execute(
            "SELECT id FROM runs WHERE id = ? AND workspace_id = ?",
            (run_id, workspace_id),
        ).fetchone()
        if run is None:
            raise NotFound("run was not found")
        previous = connection.execute(
            "SELECT sequence, event_hash FROM events WHERE run_id = ? ORDER BY sequence DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        sequence = int(previous["sequence"]) + 1 if previous else 1
        prev_hash = str(previous["event_hash"]) if previous else GENESIS_HASH
        safe_payload = redact(dict(payload))
        event = {
            "id": event_id or new_id("evt"),
            "run_id": run_id,
            "sequence": sequence,
            "occurred_at": utc_now(),
            "type": event_type,
            "actor_type": actor_type,
            "actor_id": actor_id,
            "payload": safe_payload,
            "prev_hash": prev_hash,
        }
        event["event_hash"] = compute_event_hash(event)
        connection.execute(
            """
            INSERT INTO events
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
                event["actor_type"],
                event["actor_id"],
                canonical_json(event["payload"]),
                event["prev_hash"],
                event["event_hash"],
            ),
        )
        return event

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
        with self.write() as connection:
            return self._append_event(
                connection,
                workspace_id,
                run_id,
                event_type=event_type,
                actor_type=actor_type,
                actor_id=actor_id,
                payload=payload,
            )

    def record_model_call(
        self,
        workspace_id: str,
        run_id: str,
        *,
        agent_version_id: str,
        role: str,
        provider_response_id: str,
        status: str,
        model: str,
        input_hash: str,
        output_hash: str,
        usage: Mapping[str, Any],
    ) -> str:
        call_id = new_id("mdl")
        created_at = utc_now()
        safe_usage = {
            key: int(value)
            for key, value in usage.items()
            if key in {"input_tokens", "output_tokens", "total_tokens"}
            and isinstance(value, int)
            and value >= 0
        }
        with self.write() as connection:
            connection.execute(
                """
                INSERT INTO model_calls
                    (id, workspace_id, run_id, agent_version_id, role, provider_response_id,
                     status, model, input_hash, output_hash, usage_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    call_id,
                    workspace_id,
                    run_id,
                    agent_version_id,
                    role,
                    provider_response_id,
                    status,
                    model,
                    input_hash,
                    output_hash,
                    canonical_json(safe_usage),
                    created_at,
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
                event_type="model.response_recorded",
                actor_type="agent",
                actor_id=agent_version_id,
                payload={
                    "model_call_id": call_id,
                    "role": role,
                    "provider_response_id": provider_response_id,
                    "model": model,
                    "status": status,
                    "input_hash": input_hash,
                    "output_hash": output_hash,
                    "usage": safe_usage,
                },
            )
        return call_id

    def record_tool_receipt(
        self,
        workspace_id: str,
        run_id: str,
        *,
        agent_version_id: str,
        flow_version_id: str,
        call_id: str,
        tool_name: str,
        arguments: Mapping[str, Any],
        outcome: str,
        error_code: str | None,
        result: Mapping[str, Any],
        effect: Mapping[str, str] | None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        with self.write() as connection:
            existing = connection.execute(
                "SELECT * FROM tool_receipts WHERE run_id = ? AND idempotency_key = ?",
                (run_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                return self._tool_receipt_projection(existing)

            receipt_id = new_id("rcpt")
            event_id = new_id("evt")
            created_at = utc_now()
            safe_arguments = redact(dict(arguments))
            safe_result = redact(dict(result))
            effect_kind = "sandbox_release" if effect else "none"
            event_type = "tool.denied" if outcome == "denied" else "tool.succeeded" if outcome == "succeeded" else "tool.failed"
            self._append_event(
                connection,
                workspace_id,
                run_id,
                event_type=event_type,
                actor_type="tool",
                actor_id=tool_name,
                event_id=event_id,
                payload={
                    "receipt_id": receipt_id,
                    "call_id": call_id,
                    "tool_name": tool_name,
                    "arguments": safe_arguments,
                    "outcome": outcome,
                    "error_code": error_code,
                    "result": safe_result,
                    "effect_kind": effect_kind,
                },
            )
            connection.execute(
                """
                INSERT INTO tool_receipts
                    (id, workspace_id, run_id, agent_version_id, event_id, call_id, tool_name,
                     arguments_json, outcome, error_code, result_json, effect_kind,
                     idempotency_key, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt_id,
                    workspace_id,
                    run_id,
                    agent_version_id,
                    event_id,
                    call_id,
                    tool_name,
                    canonical_json(safe_arguments),
                    outcome,
                    error_code,
                    canonical_json(safe_result),
                    effect_kind,
                    idempotency_key,
                    created_at,
                ),
            )
            if effect is not None:
                connection.execute(
                    """
                    INSERT INTO sandbox_releases
                        (id, workspace_id, run_id, flow_version_id, environment, artifact,
                         idempotency_key, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_id("rel"),
                        workspace_id,
                        run_id,
                        flow_version_id,
                        effect["environment"],
                        effect["artifact"],
                        idempotency_key,
                        created_at,
                    ),
                )
            row = connection.execute(
                "SELECT * FROM tool_receipts WHERE id = ?",
                (receipt_id,),
            ).fetchone()
            assert row is not None
            return self._tool_receipt_projection(row)

    def transition_run(
        self,
        workspace_id: str,
        run_id: str,
        *,
        status: str,
        error_code: str | None,
        agent_version_id: str,
        summary_hash: str | None = None,
    ) -> None:
        if status not in TERMINAL_RUN_STATUSES:
            raise ContractViolation("run terminal status is invalid")
        with self.write() as connection:
            row = connection.execute(
                "SELECT status, revision FROM runs WHERE id = ? AND workspace_id = ?",
                (run_id, workspace_id),
            ).fetchone()
            if row is None:
                raise NotFound("run was not found")
            if row["status"] in TERMINAL_RUN_STATUSES:
                if row["status"] == status:
                    return
                raise Conflict("terminal run status is absorbing")
            self._append_event(
                connection,
                workspace_id,
                run_id,
                event_type="agent.completed" if status in {"blocked", "completed"} else "agent.failed",
                actor_type="agent",
                actor_id=agent_version_id,
                payload={"status": status, "error_code": error_code, "summary_hash": summary_hash},
            )
            self._append_event(
                connection,
                workspace_id,
                run_id,
                event_type="run.terminal",
                actor_type="runtime",
                actor_id=None,
                payload={"status": status, "error_code": error_code},
            )
            cursor = connection.execute(
                """
                UPDATE runs
                SET status = ?, error_code = ?, revision = revision + 1, finished_at = ?
                WHERE id = ? AND workspace_id = ? AND status = 'running' AND revision = ?
                """,
                (status, error_code, utc_now(), run_id, workspace_id, row["revision"]),
            )
            if cursor.rowcount != 1:
                raise Conflict("run revision changed before terminal transition")

    # -- diagnosis and repair --------------------------------------------

    def diagnosable_event_ids(self, run_id: str) -> list[str]:
        with self.read() as connection:
            return [
                row["event_id"]
                for row in connection.execute(
                    """
                    SELECT event_id FROM tool_receipts
                    WHERE run_id = ? AND (
                        (tool_name = 'inspect_release_policy' AND outcome = 'succeeded') OR
                        (tool_name = 'stage_release' AND outcome = 'denied' AND error_code = 'policy_mismatch')
                    )
                    ORDER BY created_at, id
                    """,
                    (run_id,),
                )
            ]

    def diagnosis_candidate(self, workspace_id: str, run_id: str) -> dict[str, Any]:
        run = self.get_run(workspace_id, run_id)
        if run["status"] != "blocked":
            raise Conflict("only a blocked run can be diagnosed")
        inspect = next(
            (
                receipt
                for receipt in run["tool_receipts"]
                if receipt["tool_name"] == "inspect_release_policy" and receipt["outcome"] == "succeeded"
            ),
            None,
        )
        denied = next(
            (
                receipt
                for receipt in run["tool_receipts"]
                if receipt["tool_name"] == "stage_release"
                and receipt["outcome"] == "denied"
                and receipt["error_code"] == "policy_mismatch"
            ),
            None,
        )
        if inspect is None or denied is None:
            raise ContractViolation("run has no supported deterministic diagnosis candidate")
        evidence_ids = [inspect["event_id"], denied["event_id"]]
        events = [event for event in run["events"] if event["id"] in set(evidence_ids)]
        return {
            "fault_class": "policy_mismatch",
            "requested_environment": denied["result"]["requested_environment"],
            "allowed_environments": inspect["result"]["allowed_environments"],
            "repair_path": "/policy/allowed_environments",
            "evidence_event_ids": evidence_ids,
            "events": events,
        }

    def create_diagnosis(
        self,
        workspace_id: str,
        run_id: str,
        *,
        agent_version_id: str,
        model_call_id: str,
        fault_class: str,
        summary: str,
        evidence_event_ids: Sequence[str],
        confidence: str,
        why_not_retry: str,
        repair_path: str,
    ) -> dict[str, Any]:
        diagnosis_id = new_id("diag")
        created_at = utc_now()
        material = {
            "run_id": run_id,
            "fault_class": fault_class,
            "summary": summary,
            "evidence_event_ids": list(evidence_event_ids),
            "confidence": confidence,
            "why_not_retry": why_not_retry,
            "repair_path": repair_path,
        }
        with self.write() as connection:
            existing = connection.execute(
                "SELECT id FROM diagnoses WHERE run_id = ? AND workspace_id = ?",
                (run_id, workspace_id),
            ).fetchone()
            if existing is not None:
                return self._diagnosis_projection(connection, existing["id"])
            owned = {
                row["id"]
                for row in connection.execute(
                    "SELECT id FROM events WHERE run_id = ? AND workspace_id = ?",
                    (run_id, workspace_id),
                )
            }
            if not set(evidence_event_ids).issubset(owned):
                raise ContractViolation("diagnosis evidence is not owned by the run")
            connection.execute(
                """
                INSERT INTO diagnoses
                    (id, workspace_id, run_id, agent_version_id, model_call_id, fault_class,
                     summary, evidence_event_ids_json, confidence, why_not_retry, repair_path,
                     fingerprint, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    diagnosis_id,
                    workspace_id,
                    run_id,
                    agent_version_id,
                    model_call_id,
                    fault_class,
                    summary,
                    canonical_json(list(evidence_event_ids)),
                    confidence,
                    why_not_retry,
                    repair_path,
                    fingerprint(material),
                    created_at,
                ),
            )
            self._append_event(
                connection,
                workspace_id,
                run_id,
                event_type="diagnosis.accepted",
                actor_type="agent",
                actor_id=agent_version_id,
                payload={
                    "diagnosis_id": diagnosis_id,
                    "fault_class": fault_class,
                    "evidence_event_ids": list(evidence_event_ids),
                    "confidence": confidence,
                    "repair_path": repair_path,
                    "fingerprint": fingerprint(material),
                },
            )
            self._append_event(
                connection,
                workspace_id,
                run_id,
                event_type="agent.completed",
                actor_type="agent",
                actor_id=agent_version_id,
                payload={"role": "diagnostician", "diagnosis_id": diagnosis_id},
            )
            return self._diagnosis_projection(connection, diagnosis_id)

    def get_diagnosis(self, workspace_id: str, diagnosis_id: str) -> dict[str, Any]:
        with self.read() as connection:
            row = connection.execute(
                "SELECT id FROM diagnoses WHERE id = ? AND workspace_id = ?",
                (diagnosis_id, workspace_id),
            ).fetchone()
            if row is None:
                raise NotFound("diagnosis was not found")
            return self._diagnosis_projection(connection, diagnosis_id)

    def _diagnosis_projection(
        self, connection: sqlite3.Connection, diagnosis_id: str
    ) -> dict[str, Any]:
        row = connection.execute(
            "SELECT * FROM diagnoses WHERE id = ?",
            (diagnosis_id,),
        ).fetchone()
        if row is None:
            raise NotFound("diagnosis was not found")
        return {
            "id": row["id"],
            "run_id": row["run_id"],
            "agent_version_id": row["agent_version_id"],
            "model_call_id": row["model_call_id"],
            "fault_class": row["fault_class"],
            "summary": row["summary"],
            "evidence_event_ids": _decode(row["evidence_event_ids_json"]),
            "confidence": row["confidence"],
            "why_not_retry": row["why_not_retry"],
            "repair_path": row["repair_path"],
            "fingerprint": row["fingerprint"],
            "created_at": row["created_at"],
        }

    def create_repair(
        self,
        workspace_id: str,
        diagnosis_id: str,
        *,
        agent_version_id: str,
        model_call_id: str,
        patch: Sequence[Mapping[str, Any]],
        summary: str,
        risk: str,
    ) -> dict[str, Any]:
        with self.write() as connection:
            existing = connection.execute(
                "SELECT id FROM repairs WHERE diagnosis_id = ? AND workspace_id = ?",
                (diagnosis_id, workspace_id),
            ).fetchone()
            if existing is not None:
                return self._repair_projection(connection, existing["id"])
            diagnosis = connection.execute(
                "SELECT * FROM diagnoses WHERE id = ? AND workspace_id = ?",
                (diagnosis_id, workspace_id),
            ).fetchone()
            if diagnosis is None:
                raise NotFound("diagnosis was not found")
            run = connection.execute(
                "SELECT * FROM runs WHERE id = ? AND workspace_id = ?",
                (diagnosis["run_id"], workspace_id),
            ).fetchone()
            if run is None:
                raise RuntimeError("diagnosed run is missing")
            flow = connection.execute(
                "SELECT * FROM flows WHERE id = ? AND workspace_id = ?",
                (run["flow_id"], workspace_id),
            ).fetchone()
            version = connection.execute(
                "SELECT version FROM flow_versions WHERE id = ?",
                (run["flow_version_id"],),
            ).fetchone()
            if flow is None or version is None:
                raise RuntimeError("diagnosed flow is missing")
            if flow["current_version"] != version["version"]:
                raise Conflict("flow changed after the diagnosed run; rerun before repairing")

            repair_id = new_id("rpr")
            expected_revision = int(flow["revision"])
            material = {
                "diagnosis_id": diagnosis_id,
                "flow_id": flow["id"],
                "expected_flow_revision": expected_revision,
                "patch": [dict(operation) for operation in patch],
            }
            proposal_hash = fingerprint(material)
            created_at = utc_now()
            connection.execute(
                """
                INSERT INTO repairs
                    (id, workspace_id, diagnosis_id, flow_id, agent_version_id, model_call_id,
                     expected_flow_revision, patch_json, summary, risk, proposal_hash, status,
                     created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?)
                """,
                (
                    repair_id,
                    workspace_id,
                    diagnosis_id,
                    flow["id"],
                    agent_version_id,
                    model_call_id,
                    expected_revision,
                    canonical_json([dict(operation) for operation in patch]),
                    summary,
                    risk,
                    proposal_hash,
                    created_at,
                ),
            )
            self._append_event(
                connection,
                workspace_id,
                run["id"],
                event_type="repair.proposed",
                actor_type="agent",
                actor_id=agent_version_id,
                payload={
                    "repair_id": repair_id,
                    "diagnosis_id": diagnosis_id,
                    "proposal_hash": proposal_hash,
                    "expected_flow_revision": expected_revision,
                    "patch": [dict(operation) for operation in patch],
                },
            )
            self._append_event(
                connection,
                workspace_id,
                run["id"],
                event_type="agent.completed",
                actor_type="agent",
                actor_id=agent_version_id,
                payload={"role": "repairer", "repair_id": repair_id},
            )
            return self._repair_projection(connection, repair_id)

    def get_repair(self, workspace_id: str, repair_id: str) -> dict[str, Any]:
        with self.read() as connection:
            row = connection.execute(
                "SELECT id FROM repairs WHERE id = ? AND workspace_id = ?",
                (repair_id, workspace_id),
            ).fetchone()
            if row is None:
                raise NotFound("repair was not found")
            return self._repair_projection(connection, repair_id)

    def _repair_projection(
        self, connection: sqlite3.Connection, repair_id: str
    ) -> dict[str, Any]:
        row = connection.execute(
            "SELECT * FROM repairs WHERE id = ?",
            (repair_id,),
        ).fetchone()
        if row is None:
            raise NotFound("repair was not found")
        approval = connection.execute(
            "SELECT * FROM repair_approvals WHERE repair_id = ?",
            (repair_id,),
        ).fetchone()
        return {
            "id": row["id"],
            "diagnosis_id": row["diagnosis_id"],
            "flow_id": row["flow_id"],
            "agent_version_id": row["agent_version_id"],
            "model_call_id": row["model_call_id"],
            "expected_flow_revision": row["expected_flow_revision"],
            "patch": _decode(row["patch_json"]),
            "summary": row["summary"],
            "risk": row["risk"],
            "proposal_hash": row["proposal_hash"],
            "status": row["status"],
            "applied_flow_version_id": row["applied_flow_version_id"],
            "created_at": row["created_at"],
            "applied_at": row["applied_at"],
            "approval": self._approval_projection(approval) if approval else None,
        }

    @staticmethod
    def _approval_projection(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "repair_id": row["repair_id"],
            "proposal_hash": row["proposal_hash"],
            "expected_flow_revision": row["expected_flow_revision"],
            "actor": row["actor"],
            "reason": row["reason"],
            "acknowledged": bool(row["acknowledged"]),
            "applied_flow_version_id": row["applied_flow_version_id"],
            "created_at": row["created_at"],
        }

    def apply_repair(
        self,
        workspace_id: str,
        repair_id: str,
        *,
        proposal_hash: str,
        expected_flow_revision: int,
        actor: str,
        reason: str,
        acknowledged: bool,
    ) -> dict[str, Any]:
        with self.write() as connection:
            repair = connection.execute(
                "SELECT * FROM repairs WHERE id = ? AND workspace_id = ?",
                (repair_id, workspace_id),
            ).fetchone()
            if repair is None:
                raise NotFound("repair was not found")
            if repair["status"] == "applied":
                approval = connection.execute(
                    "SELECT * FROM repair_approvals WHERE repair_id = ?",
                    (repair_id,),
                ).fetchone()
                if approval is None:
                    raise RuntimeError("applied repair has no approval")
                same = (
                    approval["proposal_hash"] == proposal_hash
                    and approval["expected_flow_revision"] == expected_flow_revision
                    and approval["actor"] == actor
                    and approval["reason"] == reason
                    and bool(approval["acknowledged"]) == acknowledged
                )
                if not same:
                    raise Conflict("repair was already applied with a different command")
                return self._applied_projection(connection, repair, approval)

            if not acknowledged:
                raise ContractViolation("repair acknowledgement is required")
            if proposal_hash != repair["proposal_hash"]:
                raise Conflict("repair proposal hash does not match")
            if expected_flow_revision != repair["expected_flow_revision"]:
                raise Conflict("repair expected revision does not match its proposal")

            flow = connection.execute(
                "SELECT * FROM flows WHERE id = ? AND workspace_id = ?",
                (repair["flow_id"], workspace_id),
            ).fetchone()
            if flow is None:
                raise RuntimeError("repair flow is missing")
            if flow["revision"] != expected_flow_revision:
                raise Conflict("flow revision is stale")
            current = connection.execute(
                "SELECT * FROM flow_versions WHERE flow_id = ? AND version = ?",
                (flow["id"], flow["current_version"]),
            ).fetchone()
            if current is None:
                raise RuntimeError("flow current version is missing")

            patch = _decode(repair["patch_json"])
            if len(patch) != 1 or patch[0].get("op") != "replace" or patch[0].get("path") != "/policy/allowed_environments":
                raise ContractViolation("stored repair no longer satisfies the bounded patch contract")
            policy = _decode(current["policy_json"])
            policy["allowed_environments"] = patch[0]["value"]
            request = _decode(current["request_json"])
            repair_policy = _decode(current["repair_policy_json"])
            role_columns = {
                "executor": current["executor_agent_version_id"],
                "diagnostician": current["diagnostician_agent_version_id"],
                "repairer": current["repairer_agent_version_id"],
            }
            agent_material: dict[str, Any] = {}
            for role, agent_version_id in role_columns.items():
                agent = connection.execute(
                    "SELECT fingerprint FROM agent_versions WHERE id = ?",
                    (agent_version_id,),
                ).fetchone()
                if agent is None:
                    raise RuntimeError("pinned agent version is missing")
                agent_material[role] = {
                    "id": agent_version_id,
                    "fingerprint": agent["fingerprint"],
                }
            material = self._flow_material(
                agent_material,
                request=request,
                policy=policy,
                repair_policy=repair_policy,
            )
            next_version = int(flow["current_version"]) + 1
            next_revision = int(flow["revision"]) + 1
            version_id = new_id("flowv")
            applied_at = utc_now()
            connection.execute(
                """
                INSERT INTO flow_versions
                    (id, workspace_id, flow_id, version, executor_agent_version_id,
                     diagnostician_agent_version_id, repairer_agent_version_id, request_json,
                     policy_json, repair_policy_json, fingerprint, parent_version_id,
                     created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version_id,
                    workspace_id,
                    flow["id"],
                    next_version,
                    current["executor_agent_version_id"],
                    current["diagnostician_agent_version_id"],
                    current["repairer_agent_version_id"],
                    canonical_json(request),
                    canonical_json(policy),
                    canonical_json(repair_policy),
                    fingerprint(material),
                    current["id"],
                    f"repair:{repair_id}",
                    applied_at,
                ),
            )
            cursor = connection.execute(
                """
                UPDATE flows
                SET revision = ?, current_version = ?, updated_at = ?
                WHERE id = ? AND workspace_id = ? AND revision = ? AND current_version = ?
                """,
                (
                    next_revision,
                    next_version,
                    applied_at,
                    flow["id"],
                    workspace_id,
                    expected_flow_revision,
                    flow["current_version"],
                ),
            )
            if cursor.rowcount != 1:
                raise Conflict("flow revision changed while applying repair")
            approval_id = new_id("appr")
            connection.execute(
                """
                INSERT INTO repair_approvals
                    (id, workspace_id, repair_id, proposal_hash, expected_flow_revision,
                     actor, reason, acknowledged, applied_flow_version_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    approval_id,
                    workspace_id,
                    repair_id,
                    proposal_hash,
                    expected_flow_revision,
                    actor,
                    reason,
                    version_id,
                    applied_at,
                ),
            )
            connection.execute(
                """
                UPDATE repairs
                SET status = 'applied', applied_flow_version_id = ?, applied_at = ?
                WHERE id = ? AND status = 'proposed'
                """,
                (version_id, applied_at, repair_id),
            )
            diagnosis = connection.execute(
                "SELECT run_id FROM diagnoses WHERE id = ?",
                (repair["diagnosis_id"],),
            ).fetchone()
            if diagnosis is None:
                raise RuntimeError("repair diagnosis is missing")
            self._append_event(
                connection,
                workspace_id,
                diagnosis["run_id"],
                event_type="repair.approved",
                actor_type="human",
                actor_id=actor,
                payload={
                    "approval_id": approval_id,
                    "repair_id": repair_id,
                    "proposal_hash": proposal_hash,
                    "expected_flow_revision": expected_flow_revision,
                    "reason": reason,
                    "acknowledged": True,
                },
            )
            self._append_event(
                connection,
                workspace_id,
                diagnosis["run_id"],
                event_type="flow.version_created",
                actor_type="runtime",
                actor_id=None,
                payload={
                    "flow_id": flow["id"],
                    "flow_revision": next_revision,
                    "flow_version": next_version,
                    "flow_version_id": version_id,
                    "parent_version_id": current["id"],
                    "fingerprint": fingerprint(material),
                },
            )
            updated_repair = connection.execute(
                "SELECT * FROM repairs WHERE id = ?",
                (repair_id,),
            ).fetchone()
            approval = connection.execute(
                "SELECT * FROM repair_approvals WHERE id = ?",
                (approval_id,),
            ).fetchone()
            assert updated_repair is not None and approval is not None
            return self._applied_projection(connection, updated_repair, approval)

    @staticmethod
    def _applied_projection(
        connection: sqlite3.Connection,
        repair: sqlite3.Row,
        approval: sqlite3.Row,
    ) -> dict[str, Any]:
        flow = connection.execute(
            "SELECT revision, current_version FROM flows WHERE id = ?",
            (repair["flow_id"],),
        ).fetchone()
        if flow is None:
            raise RuntimeError("applied flow is missing")
        return {
            "repair_id": repair["id"],
            "approval_id": approval["id"],
            "flow_id": repair["flow_id"],
            "flow_revision": flow["revision"],
            "flow_version": flow["current_version"],
            "flow_version_id": approval["applied_flow_version_id"],
            "applied_at": approval["created_at"],
        }

    def rerun_target(self, workspace_id: str, run_id: str) -> dict[str, Any]:
        with self.read() as connection:
            run = connection.execute(
                "SELECT * FROM runs WHERE id = ? AND workspace_id = ?",
                (run_id, workspace_id),
            ).fetchone()
            if run is None:
                raise NotFound("run was not found")
            if run["status"] != "blocked":
                raise Conflict("only a blocked run can be rerun through the repair loop")
            row = connection.execute(
                """
                SELECT fv.version AS flow_version
                FROM diagnoses d
                JOIN repairs r ON r.diagnosis_id = d.id
                JOIN repair_approvals a ON a.repair_id = r.id
                JOIN flow_versions fv ON fv.id = a.applied_flow_version_id
                WHERE d.run_id = ? AND r.status = 'applied'
                """,
                (run_id,),
            ).fetchone()
            if row is None:
                raise Conflict("run has no applied repair")
            return {
                "flow_id": run["flow_id"],
                "flow_version": row["flow_version"],
                "parent_run_id": run_id,
                "correlation_id": run["correlation_id"],
            }

    def existing_child_run(self, workspace_id: str, parent_run_id: str) -> dict[str, Any] | None:
        with self.read() as connection:
            child = connection.execute(
                "SELECT id FROM runs WHERE parent_run_id = ? AND workspace_id = ?",
                (parent_run_id, workspace_id),
            ).fetchone()
        if child is None:
            return None
        return self.get_run(workspace_id, str(child["id"]))

    # -- projections ------------------------------------------------------

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
    def _model_call_projection(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "agent_version_id": row["agent_version_id"],
            "role": row["role"],
            "provider_response_id": row["provider_response_id"],
            "status": row["status"],
            "model": row["model"],
            "input_hash": row["input_hash"],
            "output_hash": row["output_hash"],
            "usage": _decode(row["usage_json"]),
            "created_at": row["created_at"],
        }

    @staticmethod
    def _tool_receipt_projection(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "event_id": row["event_id"],
            "agent_version_id": row["agent_version_id"],
            "call_id": row["call_id"],
            "tool_name": row["tool_name"],
            "arguments": _decode(row["arguments_json"]),
            "outcome": row["outcome"],
            "error_code": row["error_code"],
            "result": _decode(row["result_json"]),
            "effect_kind": row["effect_kind"],
            "idempotency_key": row["idempotency_key"],
            "created_at": row["created_at"],
        }

    @staticmethod
    def _sandbox_release_projection(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "run_id": row["run_id"],
            "flow_version_id": row["flow_version_id"],
            "environment": row["environment"],
            "artifact": row["artifact"],
            "created_at": row["created_at"],
        }

    def get_run(self, workspace_id: str, run_id: str) -> dict[str, Any]:
        with self.read() as connection:
            row = connection.execute(
                """
                SELECT r.*, fv.version AS flow_version_number, fv.fingerprint AS flow_fingerprint
                FROM runs r
                JOIN flow_versions fv ON fv.id = r.flow_version_id
                WHERE r.id = ? AND r.workspace_id = ?
                """,
                (run_id, workspace_id),
            ).fetchone()
            if row is None:
                raise NotFound("run was not found")
            events = [
                self._event_projection(event)
                for event in connection.execute(
                    "SELECT * FROM events WHERE run_id = ? ORDER BY sequence",
                    (run_id,),
                )
            ]
            model_calls = [
                self._model_call_projection(call)
                for call in connection.execute(
                    "SELECT * FROM model_calls WHERE run_id = ? ORDER BY created_at, id",
                    (run_id,),
                )
            ]
            receipts = [
                self._tool_receipt_projection(receipt)
                for receipt in connection.execute(
                    "SELECT * FROM tool_receipts WHERE run_id = ? ORDER BY created_at, id",
                    (run_id,),
                )
            ]
            effects = [
                self._sandbox_release_projection(effect)
                for effect in connection.execute(
                    "SELECT * FROM sandbox_releases WHERE run_id = ? ORDER BY created_at, id",
                    (run_id,),
                )
            ]
            diagnosis_row = connection.execute(
                "SELECT id FROM diagnoses WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            diagnosis = self._diagnosis_projection(connection, diagnosis_row["id"]) if diagnosis_row else None
            repair: dict[str, Any] | None = None
            if diagnosis is not None:
                repair_row = connection.execute(
                    "SELECT id FROM repairs WHERE diagnosis_id = ?",
                    (diagnosis["id"],),
                ).fetchone()
                repair = self._repair_projection(connection, repair_row["id"]) if repair_row else None
            return {
                "id": row["id"],
                "flow_id": row["flow_id"],
                "flow_version_id": row["flow_version_id"],
                "flow_version": row["flow_version_number"],
                "flow_fingerprint": row["flow_fingerprint"],
                "parent_run_id": row["parent_run_id"],
                "correlation_id": row["correlation_id"],
                "status": row["status"],
                "revision": row["revision"],
                "goal": row["goal"],
                "requested_environment": row["requested_environment"],
                "error_code": row["error_code"],
                "created_at": row["created_at"],
                "started_at": row["started_at"],
                "finished_at": row["finished_at"],
                "events": events,
                "model_calls": model_calls,
                "tool_receipts": receipts,
                "sandbox_effects": effects,
                "diagnosis": diagnosis,
                "repair": repair,
            }
