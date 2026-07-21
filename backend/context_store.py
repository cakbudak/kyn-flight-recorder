"""Flat SQLite repository for Knowledge, SmartRead, and governed Memory."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Mapping, Sequence

from .context_layer import (
    MAX_PASSAGE_RESULTS,
    MAX_SOURCE_COUNT,
    build_passages,
    citation,
    memory_candidate_fingerprint,
    normalize_source_text,
    query_terms,
    score_text,
    smart_read,
    source_snapshot_hash,
)
from .contracts import (
    Conflict,
    ContractViolation,
    NotFound,
    canonical_json,
    fingerprint,
    new_id,
    utc_now,
)
from .store import Store
from .studio_store import StudioStore


def _decode(value: str | None) -> Any:
    return json.loads(value) if value is not None else None


class ContextStore:
    """Product-facing persistence seam with no private Kyn ontology."""

    def __init__(self, store: Store, studio: StudioStore) -> None:
        self.store = store
        self.studio = studio

    # -- Knowledge ----------------------------------------------------

    def create_source(
        self,
        workspace_id: str,
        *,
        name: str,
        slug: str,
        description: str,
        filename: str,
        media_type: str,
        content: str,
        created_by: str,
    ) -> dict[str, Any]:
        normalized, lines = normalize_source_text(content)
        passages = build_passages(lines)
        with self.store.write() as connection:
            self.store._require_workspace(connection, workspace_id)
            count = connection.execute(
                "SELECT COUNT(*) FROM knowledge_sources WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchone()[0]
            if int(count) >= MAX_SOURCE_COUNT:
                raise ContractViolation("workspace Knowledge Source limit is reached")
            source_id = new_id("ksrc")
            version_id = new_id("ksv")
            now = utc_now()
            version_material = {
                "source_id": source_id,
                "version": 1,
                "filename": filename,
                "media_type": media_type,
                "content": normalized,
            }
            version_fingerprint = fingerprint(version_material)
            connection.execute(
                """
                INSERT INTO knowledge_sources
                    (id, workspace_id, slug, name, description, current_version,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (source_id, workspace_id, slug, name, description, now, now),
            )
            connection.execute(
                """
                INSERT INTO knowledge_source_versions
                    (id, workspace_id, source_id, version, filename, media_type,
                     content, byte_count, line_count, fingerprint, created_by, created_at)
                VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version_id,
                    workspace_id,
                    source_id,
                    filename,
                    media_type,
                    normalized,
                    len(normalized.encode("utf-8")),
                    len(lines),
                    version_fingerprint,
                    created_by,
                    now,
                ),
            )
            self._insert_passages(connection, workspace_id, version_id, passages)
        return self.get_source(workspace_id, source_id)

    def revise_source(
        self,
        workspace_id: str,
        source_id: str,
        *,
        expected_version: int,
        name: str,
        description: str,
        filename: str,
        media_type: str,
        content: str,
        created_by: str,
    ) -> dict[str, Any]:
        normalized, lines = normalize_source_text(content)
        passages = build_passages(lines)
        with self.store.write() as connection:
            source = connection.execute(
                "SELECT * FROM knowledge_sources WHERE id = ? AND workspace_id = ?",
                (source_id, workspace_id),
            ).fetchone()
            if source is None:
                raise NotFound("Knowledge Source was not found")
            if int(source["current_version"]) != expected_version:
                raise Conflict("Knowledge Source version changed")
            version = expected_version + 1
            version_id = new_id("ksv")
            now = utc_now()
            version_material = {
                "source_id": source_id,
                "version": version,
                "filename": filename,
                "media_type": media_type,
                "content": normalized,
            }
            connection.execute(
                """
                INSERT INTO knowledge_source_versions
                    (id, workspace_id, source_id, version, filename, media_type,
                     content, byte_count, line_count, fingerprint, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version_id,
                    workspace_id,
                    source_id,
                    version,
                    filename,
                    media_type,
                    normalized,
                    len(normalized.encode("utf-8")),
                    len(lines),
                    fingerprint(version_material),
                    created_by,
                    now,
                ),
            )
            self._insert_passages(connection, workspace_id, version_id, passages)
            cursor = connection.execute(
                """
                UPDATE knowledge_sources
                SET name = ?, description = ?, current_version = ?, updated_at = ?
                WHERE id = ? AND current_version = ?
                """,
                (name, description, version, now, source_id, expected_version),
            )
            if cursor.rowcount != 1:
                raise Conflict("Knowledge Source version changed")
        return self.get_source(workspace_id, source_id)

    @staticmethod
    def _insert_passages(
        connection: sqlite3.Connection,
        workspace_id: str,
        version_id: str,
        passages: Sequence[Mapping[str, Any]],
    ) -> None:
        for passage in passages:
            connection.execute(
                """
                INSERT INTO knowledge_passages
                    (id, workspace_id, source_version_id, ordinal, line_start,
                     line_end, text, fingerprint)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("kpass"),
                    workspace_id,
                    version_id,
                    passage["ordinal"],
                    passage["line_start"],
                    passage["line_end"],
                    passage["text"],
                    passage["fingerprint"],
                ),
            )

    def get_source(self, workspace_id: str, source_id: str) -> dict[str, Any]:
        with self.store.read() as connection:
            source = connection.execute(
                "SELECT * FROM knowledge_sources WHERE id = ? AND workspace_id = ?",
                (source_id, workspace_id),
            ).fetchone()
            if source is None:
                raise NotFound("Knowledge Source was not found")
            versions = connection.execute(
                """
                SELECT * FROM knowledge_source_versions
                WHERE source_id = ? AND workspace_id = ?
                ORDER BY version DESC
                """,
                (source_id, workspace_id),
            ).fetchall()
            return self._source_projection(source, versions)

    def get_source_version(self, workspace_id: str, version_id: str) -> dict[str, Any]:
        with self.store.read() as connection:
            row = connection.execute(
                """
                SELECT v.*, s.name AS source_name, s.slug AS source_slug
                FROM knowledge_source_versions v
                JOIN knowledge_sources s ON s.id = v.source_id
                WHERE v.id = ? AND v.workspace_id = ?
                """,
                (version_id, workspace_id),
            ).fetchone()
            if row is None:
                raise NotFound("Knowledge Source version was not found")
            return dict(row)

    def list_sources(self, workspace_id: str) -> list[dict[str, Any]]:
        with self.store.read() as connection:
            rows = connection.execute(
                "SELECT * FROM knowledge_sources WHERE workspace_id = ? ORDER BY updated_at DESC, id",
                (workspace_id,),
            ).fetchall()
            return [
                self._source_projection(
                    row,
                    connection.execute(
                        "SELECT * FROM knowledge_source_versions WHERE source_id = ? ORDER BY version DESC",
                        (row["id"],),
                    ).fetchall(),
                )
                for row in rows
            ]

    @staticmethod
    def _source_projection(
        source: sqlite3.Row, versions: Sequence[sqlite3.Row]
    ) -> dict[str, Any]:
        projected_versions = [
            {
                "id": row["id"],
                "version": int(row["version"]),
                "filename": row["filename"],
                "media_type": row["media_type"],
                "byte_count": int(row["byte_count"]),
                "line_count": int(row["line_count"]),
                "fingerprint": row["fingerprint"],
                "created_by": row["created_by"],
                "created_at": row["created_at"],
            }
            for row in versions
        ]
        return {
            "id": source["id"],
            "slug": source["slug"],
            "name": source["name"],
            "description": source["description"],
            "current_version": int(source["current_version"]),
            "created_at": source["created_at"],
            "updated_at": source["updated_at"],
            "version": projected_versions[0],
            "versions": projected_versions,
        }

    def smart_read(
        self,
        workspace_id: str,
        version_id: str,
        **options: Any,
    ) -> dict[str, Any]:
        version = self.get_source_version(workspace_id, version_id)
        return smart_read(version, **options)

    def search_knowledge(
        self, workspace_id: str, query: str, *, max_results: int = 12
    ) -> dict[str, Any]:
        if not isinstance(max_results, int) or isinstance(max_results, bool) or not 1 <= max_results <= MAX_PASSAGE_RESULTS:
            raise ContractViolation("Knowledge search max_results is invalid")
        terms = query_terms(query)
        clauses = " OR ".join("LOWER(p.text) LIKE ? ESCAPE '\\'" for _ in terms)
        patterns = [f"%{term.replace('%', r'\%').replace('_', r'\_')}%" for term in terms]
        with self.store.read() as connection:
            rows = connection.execute(
                f"""
                SELECT p.*, v.version, v.filename, v.media_type,
                       v.fingerprint AS source_fingerprint, s.id AS source_id,
                       s.name AS source_name
                FROM knowledge_passages p
                JOIN knowledge_source_versions v ON v.id = p.source_version_id
                JOIN knowledge_sources s ON s.id = v.source_id
                WHERE p.workspace_id = ? AND v.version = s.current_version
                  AND ({clauses})
                ORDER BY s.updated_at DESC, p.ordinal
                LIMIT 5000
                """,
                (workspace_id, *patterns),
            ).fetchall()
        ranked: list[dict[str, Any]] = []
        for row in rows:
            score, matched = score_text(str(row["text"]), terms)
            if score <= 0:
                continue
            version = {
                "id": row["source_version_id"],
                "source_id": row["source_id"],
                "version": row["version"],
                "source_name": row["source_name"],
                "filename": row["filename"],
                "fingerprint": row["source_fingerprint"],
            }
            ranked.append(
                {
                    "passage_id": row["id"],
                    "text": row["text"],
                    "score": score,
                    "matched_terms": matched,
                    "citation": citation(version, int(row["line_start"]), int(row["line_end"])),
                    "passage_fingerprint": row["fingerprint"],
                }
            )
        ranked.sort(key=lambda item: (-item["score"], item["citation"]["label"]))
        result = {"query": query.strip(), "terms": terms, "results": ranked[:max_results]}
        return {**result, "result_fingerprint": fingerprint(result)}

    # -- Governed Memory ----------------------------------------------

    def create_human_candidate(
        self,
        workspace_id: str,
        *,
        source_run_id: str,
        title: str,
        content: str,
        rationale: str,
        tags: Sequence[str],
        evidence_event_ids: Sequence[str],
    ) -> dict[str, Any]:
        run = self._eligible_source_run(workspace_id, source_run_id, evidence_event_ids)
        snapshot_hash = source_snapshot_hash(run, evidence_event_ids)
        material = {
            "source_run_id": source_run_id,
            "author_kind": "human",
            "title": title,
            "content": content,
            "rationale": rationale,
            "tags": list(tags),
            "evidence_event_ids": list(evidence_event_ids),
            "source_snapshot_hash": snapshot_hash,
        }
        candidate_id = new_id("memc")
        with self.store.write() as connection:
            connection.execute(
                """
                INSERT INTO memory_candidates
                    (id, workspace_id, source_run_id, distillation_model_call_id,
                     author_kind, title, content, rationale, tags_json,
                     evidence_event_ids_json, source_snapshot_hash, fingerprint, created_at)
                VALUES (?, ?, ?, NULL, 'human', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate_id,
                    workspace_id,
                    source_run_id,
                    title,
                    content,
                    rationale,
                    canonical_json(list(tags)),
                    canonical_json(list(evidence_event_ids)),
                    snapshot_hash,
                    memory_candidate_fingerprint(material),
                    utc_now(),
                ),
            )
        return self.get_candidate(workspace_id, candidate_id)

    def create_model_candidate(
        self,
        workspace_id: str,
        *,
        source_run_id: str,
        distiller_agent_version_id: str,
        title: str,
        content: str,
        rationale: str,
        tags: Sequence[str],
        evidence_event_ids: Sequence[str],
        provider_response_id: str,
        status: str,
        model: str,
        input_hash: str,
        output_hash: str,
        usage: Mapping[str, Any],
        request_id: str | None,
    ) -> dict[str, Any]:
        run = self._eligible_source_run(workspace_id, source_run_id, evidence_event_ids)
        snapshot_hash = source_snapshot_hash(run, evidence_event_ids)
        material = {
            "source_run_id": source_run_id,
            "author_kind": "model",
            "title": title,
            "content": content,
            "rationale": rationale,
            "tags": list(tags),
            "evidence_event_ids": list(evidence_event_ids),
            "source_snapshot_hash": snapshot_hash,
        }
        call_id = new_id("memcall")
        candidate_id = new_id("memc")
        with self.store.write() as connection:
            agent = connection.execute(
                "SELECT id FROM agent_versions WHERE id = ? AND workspace_id = ?",
                (distiller_agent_version_id, workspace_id),
            ).fetchone()
            if agent is None:
                raise ContractViolation("Memory distiller Agent does not belong to the workspace")
            connection.execute(
                """
                INSERT INTO memory_distillation_model_calls
                    (id, workspace_id, source_run_id, distiller_agent_version_id,
                     provider_response_id, status, model, input_hash, output_hash,
                     usage_json, request_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    call_id,
                    workspace_id,
                    source_run_id,
                    distiller_agent_version_id,
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
                """
                INSERT INTO memory_candidates
                    (id, workspace_id, source_run_id, distillation_model_call_id,
                     author_kind, title, content, rationale, tags_json,
                     evidence_event_ids_json, source_snapshot_hash, fingerprint, created_at)
                VALUES (?, ?, ?, ?, 'model', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate_id,
                    workspace_id,
                    source_run_id,
                    call_id,
                    title,
                    content,
                    rationale,
                    canonical_json(list(tags)),
                    canonical_json(list(evidence_event_ids)),
                    snapshot_hash,
                    memory_candidate_fingerprint(material),
                    utc_now(),
                ),
            )
        return self.get_candidate(workspace_id, candidate_id)

    def _eligible_source_run(
        self,
        workspace_id: str,
        source_run_id: str,
        evidence_event_ids: Sequence[str],
    ) -> dict[str, Any]:
        run = self.studio.get_run(workspace_id, source_run_id)
        if run["status"] != "completed" or run.get("ledger_verified") is not True:
            raise ContractViolation("Memory source Run must be completed with a verified ledger")
        owned = {event["id"] for event in run["events"]}
        if not evidence_event_ids or not set(evidence_event_ids).issubset(owned):
            raise ContractViolation("Memory evidence must cite events owned by its source Run")
        return run

    def qualify_candidate(self, workspace_id: str, candidate_id: str) -> dict[str, Any]:
        candidate = self.get_candidate(workspace_id, candidate_id)
        if candidate.get("qualification") is not None:
            return candidate
        try:
            run = self.studio.get_run(workspace_id, candidate["source_run_id"])
            owned = {event["id"] for event in run["events"]}
            observed = source_snapshot_hash(run, candidate["evidence_event_ids"])
            checks = {
                "source_run_completed": run["status"] == "completed",
                "ledger_verified": run.get("ledger_verified") is True,
                "citations_owned": bool(candidate["evidence_event_ids"])
                and set(candidate["evidence_event_ids"]).issubset(owned),
                "snapshot_unchanged": observed == candidate["source_snapshot_hash"],
                "candidate_authority_free": True,
            }
        except NotFound:
            observed = fingerprint({"missing_source_run": candidate["source_run_id"]})
            checks = {
                "source_run_completed": False,
                "ledger_verified": False,
                "citations_owned": False,
                "snapshot_unchanged": False,
                "candidate_authority_free": True,
            }
        passed = all(checks.values())
        with self.store.write() as connection:
            connection.execute(
                """
                INSERT INTO memory_candidate_qualifications
                    (id, workspace_id, candidate_id, passed, checks_json,
                     observed_source_snapshot_hash, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("memq"),
                    workspace_id,
                    candidate_id,
                    int(passed),
                    canonical_json(checks),
                    observed,
                    utc_now(),
                ),
            )
        return self.get_candidate(workspace_id, candidate_id)

    def promote_candidate(
        self,
        workspace_id: str,
        candidate_id: str,
        *,
        slug: str,
        actor: str,
        reason: str,
        candidate_fingerprint_value: str,
    ) -> dict[str, Any]:
        with self.store.write() as connection:
            candidate = connection.execute(
                "SELECT * FROM memory_candidates WHERE id = ? AND workspace_id = ?",
                (candidate_id, workspace_id),
            ).fetchone()
            if candidate is None:
                raise NotFound("Memory candidate was not found")
            existing = connection.execute(
                "SELECT id FROM memory_candidate_decisions WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
            if existing is not None:
                raise Conflict("Memory candidate already has a decision")
            qualification = connection.execute(
                "SELECT * FROM memory_candidate_qualifications WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
            if qualification is None or not bool(qualification["passed"]):
                raise ContractViolation("Memory promotion requires a passing qualification")
            if candidate_fingerprint_value != candidate["fingerprint"]:
                raise Conflict("Memory candidate fingerprint changed")
            memory_id = new_id("mem")
            version_id = new_id("memv")
            now = utc_now()
            version_material = {
                "memory_id": memory_id,
                "version": 1,
                "candidate_fingerprint": candidate["fingerprint"],
                "title": candidate["title"],
                "content": candidate["content"],
                "tags": _decode(candidate["tags_json"]),
            }
            connection.execute(
                """
                INSERT INTO memories
                    (id, workspace_id, slug, name, current_version, created_at)
                VALUES (?, ?, ?, ?, 1, ?)
                """,
                (memory_id, workspace_id, slug, candidate["title"], now),
            )
            connection.execute(
                """
                INSERT INTO memory_versions
                    (id, workspace_id, memory_id, version, source_candidate_id,
                     title, content, tags_json, source_run_id,
                     evidence_event_ids_json, fingerprint, created_at)
                VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version_id,
                    workspace_id,
                    memory_id,
                    candidate_id,
                    candidate["title"],
                    candidate["content"],
                    candidate["tags_json"],
                    candidate["source_run_id"],
                    candidate["evidence_event_ids_json"],
                    fingerprint(version_material),
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO memory_state_events
                    (id, workspace_id, memory_id, state, actor, reason, created_at)
                VALUES (?, ?, ?, 'active', ?, ?, ?)
                """,
                (new_id("mems"), workspace_id, memory_id, actor, reason, now),
            )
            connection.execute(
                """
                INSERT INTO memory_candidate_decisions
                    (id, workspace_id, candidate_id, qualification_id, decision,
                     actor, reason, acknowledged, candidate_fingerprint,
                     memory_version_id, created_at)
                VALUES (?, ?, ?, ?, 'promoted', ?, ?, 1, ?, ?, ?)
                """,
                (
                    new_id("memd"),
                    workspace_id,
                    candidate_id,
                    qualification["id"],
                    actor,
                    reason,
                    candidate["fingerprint"],
                    version_id,
                    now,
                ),
            )
        return self.get_memory(workspace_id, memory_id)

    def reject_candidate(
        self,
        workspace_id: str,
        candidate_id: str,
        *,
        actor: str,
        reason: str,
        candidate_fingerprint_value: str,
    ) -> dict[str, Any]:
        with self.store.write() as connection:
            candidate = connection.execute(
                "SELECT * FROM memory_candidates WHERE id = ? AND workspace_id = ?",
                (candidate_id, workspace_id),
            ).fetchone()
            if candidate is None:
                raise NotFound("Memory candidate was not found")
            if candidate_fingerprint_value != candidate["fingerprint"]:
                raise Conflict("Memory candidate fingerprint changed")
            connection.execute(
                """
                INSERT INTO memory_candidate_decisions
                    (id, workspace_id, candidate_id, qualification_id, decision,
                     actor, reason, acknowledged, candidate_fingerprint,
                     memory_version_id, created_at)
                VALUES (?, ?, ?, NULL, 'rejected', ?, ?, 1, ?, NULL, ?)
                """,
                (
                    new_id("memd"),
                    workspace_id,
                    candidate_id,
                    actor,
                    reason,
                    candidate["fingerprint"],
                    utc_now(),
                ),
            )
        return self.get_candidate(workspace_id, candidate_id)

    def retire_memory(
        self, workspace_id: str, memory_id: str, *, actor: str, reason: str
    ) -> dict[str, Any]:
        with self.store.write() as connection:
            memory = connection.execute(
                "SELECT id FROM memories WHERE id = ? AND workspace_id = ?",
                (memory_id, workspace_id),
            ).fetchone()
            if memory is None:
                raise NotFound("Memory was not found")
            latest = connection.execute(
                "SELECT state FROM memory_state_events WHERE memory_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (memory_id,),
            ).fetchone()
            if latest is None or latest["state"] != "active":
                raise Conflict("Memory is not active")
            connection.execute(
                """
                INSERT INTO memory_state_events
                    (id, workspace_id, memory_id, state, actor, reason, created_at)
                VALUES (?, ?, ?, 'retired', ?, ?, ?)
                """,
                (new_id("mems"), workspace_id, memory_id, actor, reason, utc_now()),
            )
        return self.get_memory(workspace_id, memory_id)

    def get_candidate(self, workspace_id: str, candidate_id: str) -> dict[str, Any]:
        with self.store.read() as connection:
            row = connection.execute(
                "SELECT * FROM memory_candidates WHERE id = ? AND workspace_id = ?",
                (candidate_id, workspace_id),
            ).fetchone()
            if row is None:
                raise NotFound("Memory candidate was not found")
            return self._candidate_projection(connection, row)

    def list_candidates(self, workspace_id: str) -> list[dict[str, Any]]:
        with self.store.read() as connection:
            rows = connection.execute(
                "SELECT * FROM memory_candidates WHERE workspace_id = ? ORDER BY created_at DESC, id",
                (workspace_id,),
            ).fetchall()
            return [self._candidate_projection(connection, row) for row in rows]

    @staticmethod
    def _candidate_projection(connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        qualification = connection.execute(
            "SELECT * FROM memory_candidate_qualifications WHERE candidate_id = ?",
            (row["id"],),
        ).fetchone()
        decision = connection.execute(
            "SELECT * FROM memory_candidate_decisions WHERE candidate_id = ?",
            (row["id"],),
        ).fetchone()
        return {
            "id": row["id"],
            "source_run_id": row["source_run_id"],
            "distillation_model_call_id": row["distillation_model_call_id"],
            "author_kind": row["author_kind"],
            "title": row["title"],
            "content": row["content"],
            "rationale": row["rationale"],
            "tags": _decode(row["tags_json"]),
            "evidence_event_ids": _decode(row["evidence_event_ids_json"]),
            "source_snapshot_hash": row["source_snapshot_hash"],
            "fingerprint": row["fingerprint"],
            "created_at": row["created_at"],
            "qualification": (
                {
                    "id": qualification["id"],
                    "passed": bool(qualification["passed"]),
                    "checks": _decode(qualification["checks_json"]),
                    "observed_source_snapshot_hash": qualification["observed_source_snapshot_hash"],
                    "created_at": qualification["created_at"],
                }
                if qualification is not None
                else None
            ),
            "decision": (
                {
                    "id": decision["id"],
                    "decision": decision["decision"],
                    "actor": decision["actor"],
                    "reason": decision["reason"],
                    "candidate_fingerprint": decision["candidate_fingerprint"],
                    "memory_version_id": decision["memory_version_id"],
                    "created_at": decision["created_at"],
                }
                if decision is not None
                else None
            ),
        }

    def get_memory(self, workspace_id: str, memory_id: str) -> dict[str, Any]:
        with self.store.read() as connection:
            memory = connection.execute(
                "SELECT * FROM memories WHERE id = ? AND workspace_id = ?",
                (memory_id, workspace_id),
            ).fetchone()
            if memory is None:
                raise NotFound("Memory was not found")
            version = connection.execute(
                "SELECT * FROM memory_versions WHERE memory_id = ? AND version = ?",
                (memory_id, memory["current_version"]),
            ).fetchone()
            states = connection.execute(
                "SELECT * FROM memory_state_events WHERE memory_id = ? ORDER BY created_at, rowid",
                (memory_id,),
            ).fetchall()
            return self._memory_projection(memory, version, states)

    def list_memories(self, workspace_id: str) -> list[dict[str, Any]]:
        with self.store.read() as connection:
            rows = connection.execute(
                "SELECT * FROM memories WHERE workspace_id = ? ORDER BY created_at DESC, id",
                (workspace_id,),
            ).fetchall()
            result: list[dict[str, Any]] = []
            for memory in rows:
                version = connection.execute(
                    "SELECT * FROM memory_versions WHERE memory_id = ? AND version = ?",
                    (memory["id"], memory["current_version"]),
                ).fetchone()
                states = connection.execute(
                    "SELECT * FROM memory_state_events WHERE memory_id = ? ORDER BY created_at, rowid",
                    (memory["id"],),
                ).fetchall()
                result.append(self._memory_projection(memory, version, states))
            return result

    @staticmethod
    def _memory_projection(
        memory: sqlite3.Row, version: sqlite3.Row, states: Sequence[sqlite3.Row]
    ) -> dict[str, Any]:
        state_events = [
            {
                "id": row["id"],
                "state": row["state"],
                "actor": row["actor"],
                "reason": row["reason"],
                "created_at": row["created_at"],
            }
            for row in states
        ]
        return {
            "id": memory["id"],
            "slug": memory["slug"],
            "name": memory["name"],
            "current_version": int(memory["current_version"]),
            "created_at": memory["created_at"],
            "state": state_events[-1]["state"],
            "state_events": state_events,
            "version": {
                "id": version["id"],
                "version": int(version["version"]),
                "source_candidate_id": version["source_candidate_id"],
                "title": version["title"],
                "content": version["content"],
                "tags": _decode(version["tags_json"]),
                "source_run_id": version["source_run_id"],
                "evidence_event_ids": _decode(version["evidence_event_ids_json"]),
                "fingerprint": version["fingerprint"],
                "created_at": version["created_at"],
            },
        }

    def search_memories(
        self, workspace_id: str, query: str, *, max_results: int = 12
    ) -> dict[str, Any]:
        if not isinstance(max_results, int) or isinstance(max_results, bool) or not 1 <= max_results <= 30:
            raise ContractViolation("Memory recall max_results is invalid")
        terms = query_terms(query)
        ranked: list[dict[str, Any]] = []
        for memory in self.list_memories(workspace_id):
            if memory["state"] != "active":
                continue
            version = memory["version"]
            haystack = f"{version['title']}\n{version['content']}\n{' '.join(version['tags'])}"
            score, matched = score_text(haystack, terms)
            if score <= 0:
                continue
            ranked.append(
                {
                    "memory_id": memory["id"],
                    "memory_version_id": version["id"],
                    "title": version["title"],
                    "content": version["content"],
                    "tags": version["tags"],
                    "score": score,
                    "matched_terms": matched,
                    "fingerprint": version["fingerprint"],
                    "provenance": {
                        "source_candidate_id": version["source_candidate_id"],
                        "source_run_id": version["source_run_id"],
                        "evidence_event_ids": version["evidence_event_ids"],
                    },
                }
            )
        ranked.sort(key=lambda item: (-item["score"], item["title"], item["memory_id"]))
        result = {"query": query.strip(), "terms": terms, "results": ranked[:max_results]}
        return {**result, "result_fingerprint": fingerprint(result)}

    def snapshot(self, workspace_id: str) -> dict[str, Any]:
        return {
            "knowledge_sources": self.list_sources(workspace_id),
            "memory_candidates": self.list_candidates(workspace_id),
            "memories": self.list_memories(workspace_id),
        }
