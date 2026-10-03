from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence

from info_system.construction.statement_candidate_batch_pipeline_v3 import (
    BATCH_OUTPUT_VERSION,
    CONTRACT_VERSION,
    PROMPT_VERSION,
    validate_statement_candidate_batch_v3_output,
)
from info_system.construction.statement_candidate_pipeline import (
    _child_id,
    _statement_id,
    _upsert_classifications,
    _upsert_conditions,
    _upsert_entities,
    _upsert_groups,
    _upsert_values,
)
from info_system.db.schema import SCHEMA_VERSION


class StatementCandidateImportV3Error(RuntimeError):
    pass


@dataclass(frozen=True)
class StatementCandidateBatchV3Import:
    candidate_package: dict[str, Any]
    manifest: dict[str, Any]
    package_dir: Path
    raw_output_json: str


@dataclass(frozen=True)
class StatementCandidateImportV3Result:
    batch_count: int
    candidate_count: int
    relation_count: int
    inserted_batch_count: int
    unchanged_batch_count: int
    dry_run: bool


@dataclass(frozen=True)
class _PreparedBatch:
    source: StatementCandidateBatchV3Import
    raw_output_sha256: str
    batch_import_id: str
    extraction_run_id: str
    document_version_pk: int
    node_pk_by_id: dict[str, int]


@dataclass
class _ImportedBatch:
    prepared: _PreparedBatch
    batch_pk: int
    extraction_run_pk: int
    existed: bool
    statement_pk_by_candidate: dict[str, int]
    candidate_pk_by_id: dict[str, int]


def import_statement_candidate_batches_v3_atomic(
    db_path: str | Path,
    *,
    batches: Sequence[StatementCandidateBatchV3Import],
    dry_run: bool = False,
) -> StatementCandidateImportV3Result:
    connection = sqlite3.connect(Path(db_path))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("BEGIN IMMEDIATE")
    try:
        result = import_statement_candidate_batches_v3(connection, batches=batches)
        if dry_run:
            connection.execute("ROLLBACK")
        else:
            connection.execute("COMMIT")
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()
    return StatementCandidateImportV3Result(
        batch_count=result.batch_count,
        candidate_count=result.candidate_count,
        relation_count=result.relation_count,
        inserted_batch_count=result.inserted_batch_count,
        unchanged_batch_count=result.unchanged_batch_count,
        dry_run=dry_run,
    )


def import_statement_candidate_batches_v3(
    connection: sqlite3.Connection,
    *,
    batches: Sequence[StatementCandidateBatchV3Import],
) -> StatementCandidateImportV3Result:
    if not connection.in_transaction:
        raise StatementCandidateImportV3Error(
            "v3 import requires a caller-controlled active transaction"
        )
    if not batches:
        raise StatementCandidateImportV3Error("v3 import requires at least one batch")
    if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise StatementCandidateImportV3Error("foreign keys must be enabled")
    user_version = connection.execute("PRAGMA user_version").fetchone()[0]
    if user_version != SCHEMA_VERSION:
        raise StatementCandidateImportV3Error(
            f"v3 import requires database schema {SCHEMA_VERSION}, got {user_version}"
        )

    prepared = _prepare_batches(connection, batches)
    timestamp = datetime.now(UTC).isoformat()
    imported = [
        _import_batch_candidates(connection, item, timestamp=timestamp)
        for item in prepared
    ]
    for item in imported:
        _import_batch_relations(connection, item, timestamp=timestamp)
        _verify_imported_batch_counts(connection, item)

    return StatementCandidateImportV3Result(
        batch_count=len(imported),
        candidate_count=sum(
            len(item.prepared.source.candidate_package["candidates"])
            for item in imported
        ),
        relation_count=sum(
            len(item.prepared.source.candidate_package["relations"])
            for item in imported
        ),
        inserted_batch_count=sum(not item.existed for item in imported),
        unchanged_batch_count=sum(item.existed for item in imported),
        dry_run=False,
    )


def _prepare_batches(
    connection: sqlite3.Connection,
    batches: Sequence[StatementCandidateBatchV3Import],
) -> list[_PreparedBatch]:
    prepared: list[_PreparedBatch] = []
    seen_batch_import_ids: set[str] = set()
    seen_candidate_ids: set[str] = set()
    all_candidate_ids: set[str] = set()
    for source in batches:
        try:
            parsed = json.loads(source.raw_output_json)
        except json.JSONDecodeError as error:
            raise StatementCandidateImportV3Error(f"invalid raw v3 JSON: {error}") from error
        if not isinstance(parsed, dict) or parsed != source.candidate_package:
            raise StatementCandidateImportV3Error(
                "raw v3 JSON does not exactly represent candidate_package"
            )
        validate_statement_candidate_batch_v3_output(
            source.candidate_package,
            manifest=source.manifest,
            package_dir=source.package_dir,
        )
        package = source.candidate_package
        if package["package_version"] != BATCH_OUTPUT_VERSION:
            raise StatementCandidateImportV3Error("wrong v3 package_version")
        if package["contract_version"] != CONTRACT_VERSION:
            raise StatementCandidateImportV3Error("wrong v3 contract_version")
        binding = package["batch_binding"]
        if binding["prompt_version"] != PROMPT_VERSION:
            raise StatementCandidateImportV3Error("wrong v3 prompt_version")
        batch_import_id = _stable_id(
            "batch-v3", binding["batch_manifest_sha256"], binding["batch_id"]
        )
        if batch_import_id in seen_batch_import_ids:
            raise StatementCandidateImportV3Error("duplicate v3 batch in one import")
        seen_batch_import_ids.add(batch_import_id)
        ids = {str(candidate["candidate_id"]) for candidate in package["candidates"]}
        duplicates = seen_candidate_ids.intersection(ids)
        if duplicates:
            raise StatementCandidateImportV3Error(
                f"candidate IDs are not globally unique: {sorted(duplicates)}"
            )
        seen_candidate_ids.update(ids)
        all_candidate_ids.update(ids)
        raw_sha = _sha256_text(source.raw_output_json)
        extraction_run_id = _stable_id(
            "run-v3",
            binding["document_version_id"],
            binding["batch_manifest_sha256"],
            binding["batch_id"],
            raw_sha,
        )
        document_version_pk, node_pk_by_id = _validate_database_binding(
            connection, binding
        )
        prepared.append(
            _PreparedBatch(
                source=source,
                raw_output_sha256=raw_sha,
                batch_import_id=batch_import_id,
                extraction_run_id=extraction_run_id,
                document_version_pk=document_version_pk,
                node_pk_by_id=node_pk_by_id,
            )
        )

    for item in prepared:
        for relation in item.source.candidate_package["relations"]:
            if (
                relation["source_candidate_id"] not in all_candidate_ids
                or relation["target_candidate_id"] not in all_candidate_ids
            ):
                raise StatementCandidateImportV3Error(
                    "v3 relation contains an unresolved candidate reference"
                )
    return prepared


def _validate_database_binding(
    connection: sqlite3.Connection, binding: dict[str, Any]
) -> tuple[int, dict[str, int]]:
    row = connection.execute(
        """
        SELECT versions.document_version_pk, versions.content_hash,
               documents.document_id, projects.project_id
        FROM document_versions AS versions
        JOIN documents USING (document_pk)
        LEFT JOIN projects USING (project_pk)
        WHERE versions.document_version_id = ?
        """,
        (binding["document_version_id"],),
    ).fetchone()
    if row is None:
        raise StatementCandidateImportV3Error("bound document version is missing")
    if row[1] != binding["document_sha256"]:
        raise StatementCandidateImportV3Error("document SHA-256 conflicts with database")
    if row[2] != binding["document_id"] or row[3] != binding["project_id"]:
        raise StatementCandidateImportV3Error("document or project binding conflicts")
    selected_ids = list(binding["target_node_ids"]) + list(binding["context_node_ids"])
    placeholders = ",".join("?" for _ in selected_ids)
    rows = connection.execute(
        f"""
        SELECT document_node_id, document_node_pk, is_citable, document_version_pk
        FROM document_nodes
        WHERE document_node_id IN ({placeholders})
        """,
        selected_ids,
    ).fetchall()
    nodes = {str(item[0]): item for item in rows}
    if set(nodes) != set(selected_ids):
        raise StatementCandidateImportV3Error("bound document node is missing")
    if any(item[3] != row[0] for item in rows):
        raise StatementCandidateImportV3Error("bound document node belongs to another version")
    if any(nodes[node_id][2] != 1 for node_id in binding["target_node_ids"]):
        raise StatementCandidateImportV3Error("target node is not citable")
    return int(row[0]), {node_id: int(item[1]) for node_id, item in nodes.items()}


def _import_batch_candidates(
    connection: sqlite3.Connection,
    prepared: _PreparedBatch,
    *,
    timestamp: str,
) -> _ImportedBatch:
    package = prepared.source.candidate_package
    binding = package["batch_binding"]
    extraction_run_pk = _ensure_extraction_run(
        connection, prepared, binding=binding, timestamp=timestamp
    )
    batch_pk, existed = _ensure_batch(
        connection,
        prepared,
        extraction_run_pk=extraction_run_pk,
        timestamp=timestamp,
    )
    _ensure_node_processing(
        connection, prepared, batch_pk=batch_pk, timestamp=timestamp
    )
    statement_pk_by_candidate: dict[str, int] = {}
    candidate_pk_by_id: dict[str, int] = {}
    for candidate in package["candidates"]:
        candidate_id = str(candidate["candidate_id"])
        statement_id = _statement_id(prepared.extraction_run_id, candidate_id)
        statement_pk = _ensure_statement(
            connection,
            statement_id=statement_id,
            candidate=candidate,
            timestamp=timestamp,
        )
        statement_pk_by_candidate[candidate_id] = statement_pk
        _ensure_statement_source(
            connection,
            statement_id=statement_id,
            statement_pk=statement_pk,
            node_pk=prepared.node_pk_by_id[str(candidate["document_node_id"])],
            source_role=str(candidate["evidence"]["source_role"]),
            timestamp=timestamp,
        )
        normalized_candidate = dict(candidate)
        normalized_candidate["groups"] = [
            {
                **group,
                "group_id": f"{prepared.batch_import_id}:{group['group_id']}",
            }
            for group in candidate["groups"]
        ]
        _upsert_entities(
            connection, statement_id, statement_pk, normalized_candidate, timestamp
        )
        _upsert_values(
            connection, statement_id, statement_pk, normalized_candidate, timestamp
        )
        _upsert_conditions(
            connection, statement_id, statement_pk, normalized_candidate, timestamp
        )
        _upsert_classifications(
            connection, statement_id, statement_pk, normalized_candidate, timestamp
        )
        _upsert_groups(
            connection, statement_id, statement_pk, normalized_candidate, timestamp
        )
        candidate_pk = _ensure_candidate_archive(
            connection,
            prepared,
            batch_pk=batch_pk,
            statement_pk=statement_pk,
            candidate=candidate,
            timestamp=timestamp,
        )
        candidate_pk_by_id[candidate_id] = candidate_pk
        _ensure_document_references(
            connection,
            candidate_pk=candidate_pk,
            candidate_archive_id=_stable_id(
                "candidate-v3", prepared.batch_import_id, candidate_id
            ),
            references=candidate["document_references"],
            timestamp=timestamp,
        )
    return _ImportedBatch(
        prepared=prepared,
        batch_pk=batch_pk,
        extraction_run_pk=extraction_run_pk,
        existed=existed,
        statement_pk_by_candidate=statement_pk_by_candidate,
        candidate_pk_by_id=candidate_pk_by_id,
    )


def _ensure_extraction_run(
    connection: sqlite3.Connection,
    prepared: _PreparedBatch,
    *,
    binding: dict[str, Any],
    timestamp: str,
) -> int:
    parameters = _canonical_json(
        {
            "contract_version": CONTRACT_VERSION,
            "document_version_id": binding["document_version_id"],
            "batch_manifest_sha256": binding["batch_manifest_sha256"],
            "batch_id": binding["batch_id"],
            "input_sha256": binding["input_sha256"],
            "prompt_sha256": binding["prompt_sha256"],
            "batch_sha256": binding["batch_sha256"],
            "raw_output_sha256": prepared.raw_output_sha256,
            "validation_status": "valid",
        }
    )
    expected = (
        binding["document_id"],
        "frontier_batch_v3_file_workflow",
        binding["model_name"],
        PROMPT_VERSION,
        "completed",
        parameters,
        prepared.source.raw_output_json,
    )
    row = connection.execute(
        """
        SELECT extraction_run_pk, document_id, method, model_name, prompt_version,
               status, parameters_json, raw_model_output_json
        FROM extraction_runs WHERE extraction_run_id = ?
        """,
        (prepared.extraction_run_id,),
    ).fetchone()
    if row is None:
        connection.execute(
            """
            INSERT INTO extraction_runs (
                extraction_run_id, document_id, started_at, completed_at, method,
                model_name, prompt_version, status, parameters_json,
                raw_model_output_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                prepared.extraction_run_id,
                binding["document_id"],
                timestamp,
                timestamp,
                "frontier_batch_v3_file_workflow",
                binding["model_name"],
                PROMPT_VERSION,
                "completed",
                parameters,
                prepared.source.raw_output_json,
            ),
        )
        return int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
    if tuple(row[1:]) != expected:
        raise StatementCandidateImportV3Error("extraction run conflict")
    return int(row[0])


def _ensure_batch(
    connection: sqlite3.Connection,
    prepared: _PreparedBatch,
    *,
    extraction_run_pk: int,
    timestamp: str,
) -> tuple[int, bool]:
    package = prepared.source.candidate_package
    binding = package["batch_binding"]
    binding_json = _canonical_json(binding)
    processing_json = _canonical_json(package["node_processing"])
    expected = (
        extraction_run_pk,
        prepared.document_version_pk,
        package["package_version"],
        package["contract_version"],
        binding["prompt_version"],
        binding["batch_id"],
        binding["batch_number"],
        binding["total_batches"],
        binding["model_name"],
        binding["batch_manifest_sha256"],
        binding["input_sha256"],
        binding["prompt_sha256"],
        binding["batch_sha256"],
        prepared.raw_output_sha256,
        binding_json,
        processing_json,
        prepared.source.raw_output_json,
        len(package["candidates"]),
        len(package["relations"]),
    )
    row = connection.execute(
        """
        SELECT statement_candidate_batch_v3_pk, extraction_run_pk,
               document_version_pk, package_version, contract_version,
               prompt_version, batch_id, batch_number, total_batches, model_name,
               batch_manifest_sha256, input_sha256, prompt_sha256, batch_sha256,
               raw_output_sha256, binding_json, node_processing_json,
               raw_output_json, candidate_count, relation_count
        FROM statement_candidate_batches_v3 WHERE batch_import_id = ?
        """,
        (prepared.batch_import_id,),
    ).fetchone()
    if row is not None:
        if tuple(row[1:]) != expected:
            raise StatementCandidateImportV3Error("batch import conflict")
        return int(row[0]), True
    connection.execute(
        """
        INSERT INTO statement_candidate_batches_v3 (
            batch_import_id, extraction_run_pk, document_version_pk,
            package_version, contract_version, prompt_version, batch_id,
            batch_number, total_batches, model_name, batch_manifest_sha256,
            input_sha256, prompt_sha256, batch_sha256, raw_output_sha256,
            binding_json, node_processing_json, raw_output_json,
            candidate_count, relation_count, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (prepared.batch_import_id, *expected, timestamp),
    )
    return int(connection.execute("SELECT last_insert_rowid()").fetchone()[0]), False


def _ensure_node_processing(
    connection: sqlite3.Connection,
    prepared: _PreparedBatch,
    *,
    batch_pk: int,
    timestamp: str,
) -> None:
    for order, item in enumerate(
        prepared.source.candidate_package["node_processing"], start=1
    ):
        row_id = _stable_id(
            "node-processing-v3", prepared.batch_import_id, str(order)
        )
        expected = (
            batch_pk,
            prepared.node_pk_by_id[str(item["document_node_id"])],
            order,
            item["status"],
            _canonical_json(item["candidate_ids"]),
            item.get("error"),
        )
        _insert_or_assert_same(
            connection,
            table="statement_candidate_node_processing_v3",
            id_column="node_processing_id",
            row_id=row_id,
            columns=(
                "statement_candidate_batch_v3_pk",
                "document_node_pk",
                "processing_order",
                "status",
                "candidate_ids_json",
                "error",
            ),
            values=expected,
            timestamp=timestamp,
        )


def _ensure_statement(
    connection: sqlite3.Connection,
    *,
    statement_id: str,
    candidate: dict[str, Any],
    timestamp: str,
) -> int:
    expected = (
        candidate["statement_type"],
        candidate["normalized_text"],
        candidate["binding_status"],
        candidate["lifecycle_status"],
        candidate["review_status"],
        candidate.get("action_state"),
        "model_candidate",
        None,
        None,
    )
    row = connection.execute(
        """
        SELECT statement_pk, statement_type, text, binding_status,
               lifecycle_status, review_status, action_state, creation_method,
               reviewed_by, reviewed_at
        FROM statements WHERE statement_id = ?
        """,
        (statement_id,),
    ).fetchone()
    if row is None:
        connection.execute(
            """
            INSERT INTO statements (
                statement_id, statement_type, text, binding_status,
                lifecycle_status, review_status, action_state, creation_method,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (statement_id, *expected[:7], timestamp),
        )
        return int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
    if tuple(row[1:]) != expected:
        raise StatementCandidateImportV3Error("statement conflict")
    return int(row[0])


def _ensure_statement_source(
    connection: sqlite3.Connection,
    *,
    statement_id: str,
    statement_pk: int,
    node_pk: int,
    source_role: str,
    timestamp: str,
) -> None:
    _insert_or_assert_same(
        connection,
        table="statement_sources",
        id_column="statement_source_id",
        row_id=_child_id("source", statement_id, source_role),
        columns=("statement_pk", "document_node_pk", "source_role"),
        values=(statement_pk, node_pk, source_role),
        timestamp=timestamp,
    )


def _ensure_candidate_archive(
    connection: sqlite3.Connection,
    prepared: _PreparedBatch,
    *,
    batch_pk: int,
    statement_pk: int,
    candidate: dict[str, Any],
    timestamp: str,
) -> int:
    candidate_id = str(candidate["candidate_id"])
    archive_id = _stable_id("candidate-v3", prepared.batch_import_id, candidate_id)
    candidate_json = _canonical_json(candidate)
    evidence = candidate["evidence"]
    expected = (
        batch_pk,
        candidate_id,
        _sha256_text(candidate_json),
        statement_pk,
        prepared.node_pk_by_id[str(candidate["document_node_id"])],
        evidence["source_text"],
        evidence["char_start"],
        evidence["char_end"],
        evidence["source_role"],
        candidate["statement_subtype"],
        candidate["output_constraint"],
        candidate_json,
        _canonical_json(candidate["review_notes"]),
        _canonical_json(candidate["validation_warnings"]),
    )
    columns = (
        "statement_candidate_batch_v3_pk",
        "candidate_id",
        "candidate_sha256",
        "statement_pk",
        "document_node_pk",
        "evidence_source_text",
        "char_start",
        "char_end",
        "source_role",
        "statement_subtype",
        "output_constraint",
        "candidate_json",
        "review_notes_json",
        "validation_warnings_json",
    )
    _insert_or_assert_same(
        connection,
        table="statement_candidates_v3",
        id_column="statement_candidate_v3_id",
        row_id=archive_id,
        columns=columns,
        values=expected,
        timestamp=timestamp,
    )
    return int(
        connection.execute(
            """
            SELECT statement_candidate_v3_pk FROM statement_candidates_v3
            WHERE statement_candidate_v3_id = ?
            """,
            (archive_id,),
        ).fetchone()[0]
    )


def _ensure_document_references(
    connection: sqlite3.Connection,
    *,
    candidate_pk: int,
    candidate_archive_id: str,
    references: list[dict[str, Any]],
    timestamp: str,
) -> None:
    for order, reference in enumerate(references, start=1):
        target_document_id = reference.get("target_document_id")
        target_node_id = reference.get("target_document_node_id")
        target_document_pk = _optional_pk(
            connection,
            table="documents",
            id_column="document_id",
            pk_column="document_pk",
            value=target_document_id,
        )
        target_node_pk = _optional_pk(
            connection,
            table="document_nodes",
            id_column="document_node_id",
            pk_column="document_node_pk",
            value=target_node_id,
        )
        if target_document_id is not None and target_document_pk is None:
            raise StatementCandidateImportV3Error("document reference target is missing")
        if target_node_id is not None and target_node_pk is None:
            raise StatementCandidateImportV3Error("document node reference target is missing")
        row_id = _stable_id("document-reference-v3", candidate_archive_id, str(order))
        _insert_or_assert_same(
            connection,
            table="statement_candidate_document_references_v3",
            id_column="statement_candidate_document_reference_v3_id",
            row_id=row_id,
            columns=(
                "statement_candidate_v3_pk",
                "reference_order",
                "reference_text",
                "reference_type",
                "target_document_id",
                "target_document_node_id",
                "target_document_pk",
                "target_document_node_pk",
                "reference_json",
            ),
            values=(
                candidate_pk,
                order,
                reference["reference_text"],
                reference["reference_type"],
                target_document_id,
                target_node_id,
                target_document_pk,
                target_node_pk,
                _canonical_json(reference),
            ),
            timestamp=timestamp,
        )


def _import_batch_relations(
    connection: sqlite3.Connection,
    imported: _ImportedBatch,
    *,
    timestamp: str,
) -> None:
    for relation in imported.prepared.source.candidate_package["relations"]:
        source_id = str(relation["source_candidate_id"])
        target_id = str(relation["target_candidate_id"])
        source_statement_pk = imported.statement_pk_by_candidate[source_id]
        target_statement_pk = imported.statement_pk_by_candidate[target_id]
        if relation["relation_type"] == "conflicts_with" and source_statement_pk > target_statement_pk:
            normalized_source_pk, normalized_target_pk = (
                target_statement_pk,
                source_statement_pk,
            )
        else:
            normalized_source_pk, normalized_target_pk = (
                source_statement_pk,
                target_statement_pk,
            )
        normalized_id = _stable_id(
            "statement-relation",
            imported.prepared.batch_import_id,
            str(relation["relation_id"]),
        )
        _insert_or_assert_same(
            connection,
            table="statement_relations",
            id_column="statement_relation_id",
            row_id=normalized_id,
            columns=("source_statement_pk", "target_statement_pk", "relation_type"),
            values=(
                normalized_source_pk,
                normalized_target_pk,
                relation["relation_type"],
            ),
            timestamp=timestamp,
        )
        statement_relation_pk = int(
            connection.execute(
                "SELECT statement_relation_pk FROM statement_relations WHERE statement_relation_id = ?",
                (normalized_id,),
            ).fetchone()[0]
        )
        relation_json = _canonical_json(relation)
        relation_archive_id = _stable_id(
            "relation-v3",
            imported.prepared.batch_import_id,
            str(relation["relation_id"]),
        )
        _insert_or_assert_same(
            connection,
            table="statement_relations_v3",
            id_column="statement_relation_v3_id",
            row_id=relation_archive_id,
            columns=(
                "statement_candidate_batch_v3_pk",
                "relation_id",
                "relation_sha256",
                "statement_relation_pk",
                "source_candidate_v3_pk",
                "target_candidate_v3_pk",
                "relation_type",
                "relation_json",
            ),
            values=(
                imported.batch_pk,
                relation["relation_id"],
                _sha256_text(relation_json),
                statement_relation_pk,
                imported.candidate_pk_by_id[source_id],
                imported.candidate_pk_by_id[target_id],
                relation["relation_type"],
                relation_json,
            ),
            timestamp=timestamp,
        )


def _verify_imported_batch_counts(
    connection: sqlite3.Connection, imported: _ImportedBatch
) -> None:
    candidate_count = connection.execute(
        """
        SELECT COUNT(*) FROM statement_candidates_v3
        WHERE statement_candidate_batch_v3_pk = ?
        """,
        (imported.batch_pk,),
    ).fetchone()[0]
    relation_count = connection.execute(
        """
        SELECT COUNT(*) FROM statement_relations_v3
        WHERE statement_candidate_batch_v3_pk = ?
        """,
        (imported.batch_pk,),
    ).fetchone()[0]
    package = imported.prepared.source.candidate_package
    if candidate_count != len(package["candidates"]):
        raise StatementCandidateImportV3Error("candidate count conflict after import")
    if relation_count != len(package["relations"]):
        raise StatementCandidateImportV3Error("relation count conflict after import")


def _insert_or_assert_same(
    connection: sqlite3.Connection,
    *,
    table: str,
    id_column: str,
    row_id: str,
    columns: tuple[str, ...],
    values: tuple[Any, ...],
    timestamp: str,
) -> None:
    selected = ", ".join(columns)
    row = connection.execute(
        f'SELECT {selected} FROM "{table}" WHERE "{id_column}" = ?',
        (row_id,),
    ).fetchone()
    if row is not None:
        if tuple(row) != values:
            raise StatementCandidateImportV3Error(f"{table} conflict")
        return
    insert_columns = ", ".join((id_column, *columns, "created_at"))
    placeholders = ", ".join("?" for _ in range(len(columns) + 2))
    connection.execute(
        f'INSERT INTO "{table}" ({insert_columns}) VALUES ({placeholders})',
        (row_id, *values, timestamp),
    )


def _optional_pk(
    connection: sqlite3.Connection,
    *,
    table: str,
    id_column: str,
    pk_column: str,
    value: str | None,
) -> int | None:
    if value is None:
        return None
    row = connection.execute(
        f'SELECT "{pk_column}" FROM "{table}" WHERE "{id_column}" = ?', (value,)
    ).fetchone()
    return None if row is None else int(row[0])


def _stable_id(prefix: str, *parts: str) -> str:
    return f"{prefix}:" + _sha256_text("|".join(parts))[:32]


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
