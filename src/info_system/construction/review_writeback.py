from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from info_system.db.schema import apply_v3_to_v4_migration


PROTECTED_V3_TABLES = (
    "statement_candidate_batches_v3",
    "statement_candidate_node_processing_v3",
    "statement_candidates_v3",
    "statement_candidate_document_references_v3",
    "statement_relations_v3",
    "statements",
    "statement_sources",
    "statement_relations",
)

_CANDIDATE_DECISIONS = {
    "akzeptieren",
    "korrigieren",
    "verwerfen",
    "unklar",
}
_ACTIVE_RELATION_STATUS = "activate_after_candidate_writeback"
_RELATION_STATUS_MAP = {
    _ACTIVE_RELATION_STATUS: "active",
    "excluded_rejected_or_unresolved_endpoint": "inactive_rejected_endpoint",
    "accepted_not_activated_catalog_conflict": "inactive_catalog_conflict",
}
_REVIEWED_FIELD_CHANGES = {
    "statement_type",
    "normalized_text",
    "binding_status",
}


class ReviewWritebackError(RuntimeError):
    """Raised when the controlled review writeback cannot satisfy its invariants."""


def apply_review_writeback(
    db_path: str | Path,
    *,
    candidate_plan: Mapping[str, Any],
    relation_plan: Mapping[str, Any],
    relation_catalog_path: str | Path,
    candidate_review_source: str,
    candidate_review_version: str,
    relation_review_source: str,
    relation_review_version: str,
    relation_reviewed_by: str,
    relation_reviewed_at: str,
) -> dict[str, Any]:
    """Back up a v3 database, then migrate and write reviewed overlays atomically."""
    path = Path(db_path).resolve()
    catalog_path = Path(relation_catalog_path).resolve()
    if not path.is_file():
        raise ReviewWritebackError(f"database does not exist: {path}")
    _require_nonempty(candidate_review_source, "candidate_review_source")
    _require_nonempty(candidate_review_version, "candidate_review_version")
    _require_nonempty(relation_review_source, "relation_review_source")
    _require_nonempty(relation_review_version, "relation_review_version")
    _require_nonempty(relation_reviewed_by, "relation_reviewed_by")
    _require_nonempty(relation_reviewed_at, "relation_reviewed_at")

    database_sha256_before = _sha256_file(path)
    backup_path = _create_byte_identical_backup(path)
    backup_sha256 = _sha256_file(backup_path)
    if backup_sha256 != database_sha256_before:
        raise ReviewWritebackError("database backup is not byte-identical")

    catalog = _parse_relation_catalog(catalog_path)
    created_at = datetime.now(UTC).isoformat()
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        if connection.execute("PRAGMA user_version").fetchone()[0] != 3:
            raise ReviewWritebackError("controlled review writeback requires schema version 3")
        protected_before = protected_v3_fingerprints(connection)
        connection.execute("BEGIN IMMEDIATE")
        try:
            apply_v3_to_v4_migration(
                connection,
                path,
                database_hash_before=database_sha256_before,
                migrated_at=created_at,
            )
            candidate_state = _insert_candidate_reviews(
                connection,
                candidate_plan,
                review_source=candidate_review_source,
                review_version=candidate_review_version,
                created_at=created_at,
            )
            relation_state = _insert_relation_reviews(
                connection,
                relation_plan,
                catalog=catalog,
                candidate_state=candidate_state,
                review_source=relation_review_source,
                review_version=relation_review_version,
                reviewed_by=relation_reviewed_by,
                reviewed_at=relation_reviewed_at,
                created_at=created_at,
            )
            validation = _validate_writeback(
                connection,
                candidate_plan=candidate_plan,
                relation_plan=relation_plan,
                protected_before=protected_before,
            )
            connection.execute("COMMIT")
        except Exception as error:
            connection.execute("ROLLBACK")
            if isinstance(error, ReviewWritebackError):
                raise
            raise ReviewWritebackError(str(error)) from error

    return {
        "schema_version_before": 3,
        "schema_version_after": 4,
        "database_path": str(path),
        "database_sha256_before": database_sha256_before,
        "database_sha256_after": _sha256_file(path),
        "backup_path": str(backup_path),
        "backup_sha256": backup_sha256,
        "protected_v3_fingerprints_before": protected_before,
        "protected_v3_fingerprints_after": validation["protected_after"],
        "protected_v3_fingerprints_unchanged": True,
        "candidate_review_counts": candidate_state["status_counts"],
        "evidence_passage_count": candidate_state["evidence_passage_count"],
        "relation_review_counts": relation_state["activation_counts"],
        "relation_dependency_count": relation_state["dependency_count"],
        "clean_set_counts": validation["clean_set_counts"],
        "foreign_key_check": [],
        "integrity_check": "ok",
        "transaction_committed": True,
    }


def schema4_review_correction_plan_sha256(plan: Mapping[str, Any]) -> str:
    """Hash the canonical correction plan payload without its self-hash field."""
    payload = dict(plan)
    payload.pop("plan_sha256", None)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def schema4_review_correction_fingerprints(
    db_path: str | Path,
    candidate_id: str,
) -> dict[str, Any]:
    """Return the immutable preconditions for one schema-v4 review correction."""
    path = Path(db_path).resolve()
    if not path.is_file():
        raise ReviewWritebackError(f"database does not exist: {path}")
    _require_nonempty(candidate_id, "candidate_id")
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        _require_schema4(connection)
        candidate, current_review, passages = _schema4_review_state(
            connection, candidate_id
        )
        return {
            "database_sha256_before": _sha256_file(path),
            "candidate_sha256": candidate["candidate_sha256"],
            "candidate_row_sha256": _rows_sha256(
                connection,
                "statement_candidates_v3",
                "statement_candidate_v3_pk = ?",
                (candidate["statement_candidate_v3_pk"],),
            ),
            "current_review_row_sha256": _rows_sha256(
                connection,
                "candidate_review_overlays",
                "candidate_review_pk = ?",
                (current_review["candidate_review_pk"],),
            ),
            "evidence_passages_sha256": _rows_payload_sha256(passages),
            "protected_v3_fingerprints": protected_v3_fingerprints(connection),
        }


def apply_schema4_review_correction(
    db_path: str | Path,
    *,
    correction_plan_path: str | Path,
) -> dict[str, Any]:
    """Supersede one current schema-v4 candidate review without losing history."""
    path = Path(db_path).resolve()
    plan_path = Path(correction_plan_path).resolve()
    if not path.is_file():
        raise ReviewWritebackError(f"database does not exist: {path}")
    plan = _load_schema4_review_correction_plan(plan_path)
    candidate_id = _required_string(plan, "candidate_id")
    database_sha256_before = _sha256_file(path)

    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        _require_schema4(connection)
        _validate_schema4_review_correction_preconditions(
            connection,
            plan,
            plan_path=plan_path,
            database_sha256=database_sha256_before,
        )

    correction_id = _required_string(plan, "correction_id")
    backup_path = _create_schema4_correction_backup(
        path,
        correction_id=correction_id,
        database_sha256=database_sha256_before,
    )
    backup_sha256 = _sha256_file(backup_path)
    if backup_sha256 != database_sha256_before:
        raise ReviewWritebackError("database backup is not byte-identical")
    if _sha256_file(path) != database_sha256_before:
        raise ReviewWritebackError("database changed between validation and correction")

    created_at = datetime.now(UTC).isoformat()
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        try:
            _require_schema4(connection)
            candidate, current_review, passages, protected_before = (
                _validate_schema4_review_correction_preconditions(
                    connection,
                    plan,
                    plan_path=plan_path,
                    database_sha256=database_sha256_before,
                )
            )
            relation_before = _relation_review_fingerprints(connection)
            old_review = dict(current_review)
            update = connection.execute(
                """
                UPDATE candidate_review_overlays
                SET is_current = 0
                WHERE candidate_review_pk = ? AND is_current = 1
                """,
                (current_review["candidate_review_pk"],),
            )
            if update.rowcount != 1:
                raise ReviewWritebackError("expected current review changed concurrently")

            target = plan["target_review"]
            plan_sha256 = _required_string(plan, "plan_sha256")
            new_review_pk = connection.execute(
                """
                INSERT INTO candidate_review_overlays (
                    candidate_review_id, statement_candidate_v3_pk,
                    review_decision, original_review_decision,
                    reviewed_statement_type, reviewed_normalized_text,
                    reviewed_binding_status, review_status,
                    review_source, review_version, reviewed_by, reviewed_at,
                    is_current, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                RETURNING candidate_review_pk
                """,
                (
                    _required_string(target, "candidate_review_id"),
                    candidate["statement_candidate_v3_pk"],
                    _required_string(target, "review_decision"),
                    _required_string(target, "review_decision"),
                    current_review["reviewed_statement_type"],
                    current_review["reviewed_normalized_text"],
                    current_review["reviewed_binding_status"],
                    _required_string(target, "review_status"),
                    _required_string(plan, "review_source"),
                    f"sha256:{plan_sha256}",
                    _required_string(target, "reviewer"),
                    _required_string(target, "correction_date"),
                    created_at,
                ),
            ).fetchone()[0]

            for passage in passages:
                connection.execute(
                    """
                    INSERT INTO candidate_review_evidence_passages (
                        candidate_review_evidence_passage_id,
                        candidate_review_pk, passage_order, document_pk,
                        document_node_pk, locator, exact_text,
                        char_start_in_node, char_end_in_node, source_role,
                        source_candidate_v3_pk, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "candidate-review-evidence-correction-v1:"
                        f"{correction_id}:{passage['passage_order']}",
                        new_review_pk,
                        passage["passage_order"],
                        passage["document_pk"],
                        passage["document_node_pk"],
                        passage["locator"],
                        passage["exact_text"],
                        passage["char_start_in_node"],
                        passage["char_end_in_node"],
                        passage["source_role"],
                        passage["source_candidate_v3_pk"],
                        created_at,
                    ),
                )

            current_count = connection.execute(
                """
                SELECT COUNT(*) FROM candidate_review_overlays
                WHERE statement_candidate_v3_pk = ? AND is_current = 1
                """,
                (candidate["statement_candidate_v3_pk"],),
            ).fetchone()[0]
            if current_count != 1:
                raise ReviewWritebackError("candidate must have exactly one current review")

            old_after = dict(
                connection.execute(
                    """
                    SELECT * FROM candidate_review_overlays
                    WHERE candidate_review_pk = ?
                    """,
                    (current_review["candidate_review_pk"],),
                ).fetchone()
            )
            if old_after.pop("is_current") != 0:
                raise ReviewWritebackError("superseded review remains current")
            expected_old = dict(old_review)
            expected_old.pop("is_current")
            if old_after != expected_old:
                raise ReviewWritebackError("superseded review content changed")

            protected_after = protected_v3_fingerprints(connection)
            if protected_after != protected_before:
                raise ReviewWritebackError("protected v3 or normalized raw rows changed")
            relation_after = _relation_review_fingerprints(connection)
            if relation_after != relation_before:
                raise ReviewWritebackError("relation review state changed")
            foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
            if foreign_keys:
                raise ReviewWritebackError("foreign_key_check is not empty")
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise ReviewWritebackError(f"integrity_check failed: {integrity}")
            connection.execute("COMMIT")
        except Exception as error:
            connection.execute("ROLLBACK")
            if isinstance(error, ReviewWritebackError):
                raise
            raise ReviewWritebackError(str(error)) from error

    return {
        "correction_id": correction_id,
        "candidate_id": candidate_id,
        "superseded_candidate_review_pk": old_review["candidate_review_pk"],
        "superseded_candidate_review_id": old_review["candidate_review_id"],
        "new_candidate_review_pk": new_review_pk,
        "new_candidate_review_id": _required_string(
            plan["target_review"], "candidate_review_id"
        ),
        "database_path": str(path),
        "database_sha256_before": database_sha256_before,
        "database_sha256_after": _sha256_file(path),
        "backup_path": str(backup_path),
        "backup_sha256": backup_sha256,
        "correction_plan_path": str(plan_path),
        "correction_plan_sha256": _required_string(plan, "plan_sha256"),
        "protected_v3_fingerprints_unchanged": True,
        "relation_review_overlays_unchanged": True,
        "foreign_key_check": [],
        "integrity_check": "ok",
        "transaction_committed": True,
    }


def protected_v3_fingerprints(connection: sqlite3.Connection) -> dict[str, str]:
    """Return deterministic content hashes for raw v3 and linked normalized rows."""
    fingerprints: dict[str, str] = {}
    for table in PROTECTED_V3_TABLES:
        columns = [
            row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')
        ]
        if not columns:
            raise ReviewWritebackError(f"protected table is missing: {table}")
        primary_key_columns = [
            row[1]
            for row in connection.execute(f'PRAGMA table_info("{table}")')
            if row[5]
        ]
        order_columns = primary_key_columns or columns
        quoted_order = ", ".join(f'"{column}"' for column in order_columns)
        rows = connection.execute(
            f'SELECT * FROM "{table}" ORDER BY {quoted_order}'
        ).fetchall()
        encoded_rows = [
            [_json_cell(value) for value in tuple(row)]
            for row in rows
        ]
        payload = json.dumps(
            {"columns": columns, "rows": encoded_rows},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        fingerprints[table] = hashlib.sha256(payload).hexdigest()
    return fingerprints


def _insert_candidate_reviews(
    connection: sqlite3.Connection,
    plan: Mapping[str, Any],
    *,
    review_source: str,
    review_version: str,
    created_at: str,
) -> dict[str, Any]:
    records = _mapping_records(plan, "candidate")
    if len({record.get("candidate_id") for record in records}) != len(records):
        raise ReviewWritebackError("candidate plan contains duplicate candidate_id values")
    expected_dispositions = plan.get("disposition_counts")
    actual_dispositions = Counter(str(record.get("disposition")) for record in records)
    if expected_dispositions is not None and dict(actual_dispositions) != dict(expected_dispositions):
        raise ReviewWritebackError("candidate disposition counts do not match the plan")

    database_candidates = {
        row["candidate_id"]: row
        for row in connection.execute(
            """
            SELECT candidates.statement_candidate_v3_pk, candidates.candidate_id,
                   candidates.statement_pk, candidates.document_node_pk,
                   candidates.evidence_source_text, candidates.char_start,
                   candidates.char_end
            FROM statement_candidates_v3 AS candidates
            """
        )
    }
    nodes = {
        row["document_node_id"]: row
        for row in connection.execute(
            """
            SELECT nodes.document_node_pk, nodes.document_node_id, nodes.locator,
                   nodes.source_text, versions.document_pk
            FROM document_nodes AS nodes
            JOIN document_versions AS versions
              ON versions.document_version_pk = nodes.document_version_pk
            """
        )
    }
    review_by_candidate: dict[str, int] = {}
    record_by_candidate: dict[str, Mapping[str, Any]] = {}
    status_counts: Counter[str] = Counter()
    evidence_passage_count = 0

    for record in records:
        candidate_id = _required_string(record, "candidate_id")
        database_candidate = database_candidates.get(candidate_id)
        if database_candidate is None:
            raise ReviewWritebackError(f"candidate does not resolve to v3 data: {candidate_id}")
        decision_raw = str(record.get("raw_review_decision", ""))
        decision = decision_raw.strip() or "nicht_entschieden"
        if decision not in _CANDIDATE_DECISIONS | {"nicht_entschieden"}:
            raise ReviewWritebackError(f"unsupported candidate review decision: {decision}")
        review_status = _required_string(record, "target_review_status")
        disposition = _required_string(record, "disposition")
        _validate_candidate_disposition(disposition, decision, review_status)
        reviewed_by = record.get("target_reviewed_by")
        reviewed_at = record.get("target_reviewed_at")
        candidate_review_id = f"candidate-review-v1:{candidate_id}"
        candidate_review_pk = connection.execute(
            """
            INSERT INTO candidate_review_overlays (
                candidate_review_id, statement_candidate_v3_pk, review_decision,
                original_review_decision, reviewed_statement_type,
                reviewed_normalized_text, reviewed_binding_status, review_status,
                review_source, review_version, reviewed_by, reviewed_at,
                is_current, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            RETURNING candidate_review_pk
            """,
            (
                candidate_review_id,
                database_candidate["statement_candidate_v3_pk"],
                decision,
                decision_raw,
                _required_string(record, "target_statement_type"),
                _required_string(record, "target_normalized_text"),
                _required_string(record, "target_binding_status"),
                review_status,
                review_source,
                review_version,
                reviewed_by,
                reviewed_at,
                created_at,
            ),
        ).fetchone()[0]
        review_by_candidate[candidate_id] = candidate_review_pk
        record_by_candidate[candidate_id] = record
        status_counts[review_status] += 1

        passages = record.get("evidence_passages")
        if not isinstance(passages, list) or not passages:
            raise ReviewWritebackError(f"candidate has no reviewed evidence: {candidate_id}")
        for passage_order, passage in enumerate(passages, start=1):
            if not isinstance(passage, Mapping):
                raise ReviewWritebackError("candidate evidence passage must be an object")
            node_id = _required_string(passage, "document_node_id")
            node = nodes.get(node_id)
            if node is None:
                raise ReviewWritebackError(f"review evidence node does not resolve: {node_id}")
            exact_text = _required_string(passage, "source_text", trim=False)
            source_text = node["source_text"]
            if exact_text not in source_text:
                raise ReviewWritebackError(
                    f"review evidence is not an exact source_text passage: {candidate_id}"
                )
            source_candidate_id = _required_string(passage, "source_candidate_id")
            source_candidate = database_candidates.get(source_candidate_id)
            if source_candidate is None:
                raise ReviewWritebackError(
                    f"evidence source candidate does not resolve: {source_candidate_id}"
                )
            char_start, char_end = _passage_positions(
                source_text,
                exact_text,
                source_candidate=source_candidate,
                node_pk=node["document_node_pk"],
            )
            connection.execute(
                """
                INSERT INTO candidate_review_evidence_passages (
                    candidate_review_evidence_passage_id, candidate_review_pk,
                    passage_order, document_pk, document_node_pk, locator,
                    exact_text, char_start_in_node, char_end_in_node, source_role,
                    source_candidate_v3_pk, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"candidate-review-evidence-v1:{candidate_id}:{passage_order}",
                    candidate_review_pk,
                    passage_order,
                    node["document_pk"],
                    node["document_node_pk"],
                    node["locator"],
                    exact_text,
                    char_start,
                    char_end,
                    _required_string(passage, "source_role"),
                    source_candidate["statement_candidate_v3_pk"],
                    created_at,
                ),
            )
            evidence_passage_count += 1

    expected_clean = plan.get("approved_clean_candidate_count")
    actual_clean = status_counts["confirmed"]
    if expected_clean is not None and actual_clean != expected_clean:
        raise ReviewWritebackError("confirmed candidate count does not match the plan")
    return {
        "review_by_candidate": review_by_candidate,
        "record_by_candidate": record_by_candidate,
        "status_counts": dict(status_counts),
        "evidence_passage_count": evidence_passage_count,
    }


def _insert_relation_reviews(
    connection: sqlite3.Connection,
    plan: Mapping[str, Any],
    *,
    catalog: Mapping[str, Mapping[str, set[str]]],
    candidate_state: Mapping[str, Any],
    review_source: str,
    review_version: str,
    reviewed_by: str,
    reviewed_at: str,
    created_at: str,
) -> dict[str, Any]:
    records = _mapping_records(plan, "relation")
    if len({record.get("relation_id") for record in records}) != len(records):
        raise ReviewWritebackError("relation plan contains duplicate relation_id values")
    database_relations = {
        row["relation_id"]: row
        for row in connection.execute(
            """
            SELECT relations.statement_relation_v3_pk, relations.relation_id,
                   relations.relation_type,
                   source.candidate_id AS source_candidate_id,
                   target.candidate_id AS target_candidate_id
            FROM statement_relations_v3 AS relations
            JOIN statement_candidates_v3 AS source
              ON source.statement_candidate_v3_pk = relations.source_candidate_v3_pk
            JOIN statement_candidates_v3 AS target
              ON target.statement_candidate_v3_pk = relations.target_candidate_v3_pk
            """
        )
    }
    review_by_candidate = candidate_state["review_by_candidate"]
    candidate_records = candidate_state["record_by_candidate"]
    activation_counts: Counter[str] = Counter()
    dependency_count = 0

    for record in records:
        relation_id = _required_string(record, "relation_id")
        database_relation = database_relations.get(relation_id)
        if database_relation is None:
            raise ReviewWritebackError(f"relation does not resolve to v3 data: {relation_id}")
        relation_type = _required_string(record, "relation_type")
        source_candidate_id = _required_string(record, "source_candidate_id")
        target_candidate_id = _required_string(record, "target_candidate_id")
        if (
            relation_type != database_relation["relation_type"]
            or source_candidate_id != database_relation["source_candidate_id"]
            or target_candidate_id != database_relation["target_candidate_id"]
        ):
            raise ReviewWritebackError(f"relation plan conflicts with v3 raw data: {relation_id}")
        professional_decision = _required_string(record, "professional_review_decision")
        raw_status = _required_string(record, "technical_activation_status")
        technical_status = _RELATION_STATUS_MAP.get(raw_status)
        if technical_status is None:
            raise ReviewWritebackError(f"unsupported relation activation status: {raw_status}")
        source_record = candidate_records.get(source_candidate_id)
        target_record = candidate_records.get(target_candidate_id)
        if source_record is None or target_record is None:
            raise ReviewWritebackError(f"relation endpoint is absent from candidate plan: {relation_id}")
        source_clean = source_record["target_review_status"] == "confirmed"
        target_clean = target_record["target_review_status"] == "confirmed"
        compatible = _catalog_compatible(
            catalog,
            relation_type,
            _required_string(source_record, "target_statement_type"),
            _required_string(target_record, "target_statement_type"),
        )
        _validate_relation_activation(
            relation_id,
            technical_status=technical_status,
            source_clean=source_clean,
            target_clean=target_clean,
            catalog_compatible=compatible,
        )
        if bool(record.get("catalog_compatible_after_candidate_corrections")) != compatible:
            raise ReviewWritebackError(f"catalog result differs from relation plan: {relation_id}")
        include_clean = bool(record.get("include_in_clean_relation_set"))
        if include_clean != (technical_status == "active"):
            raise ReviewWritebackError(f"clean relation flag differs from activation: {relation_id}")
        reason = str(record.get("non_activation_reason", "")).strip() or None
        if technical_status != "active" and reason is None:
            raise ReviewWritebackError(f"inactive relation lacks a reason: {relation_id}")
        relation_review_pk = connection.execute(
            """
            INSERT INTO relation_review_overlays (
                relation_review_id, statement_relation_v3_pk,
                professional_decision, original_review_code, acceptance_mode,
                technical_activation_status, non_activation_reason, review_source,
                review_version, reviewed_by, reviewed_at, is_current, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            RETURNING relation_review_pk
            """,
            (
                f"relation-review-v1:{relation_id}",
                database_relation["statement_relation_v3_pk"],
                professional_decision,
                _required_string(record, "raw_review_code"),
                _required_string(record, "acceptance_mode"),
                technical_status,
                reason,
                review_source,
                review_version,
                reviewed_by,
                reviewed_at,
                created_at,
            ),
        ).fetchone()[0]
        activation_counts[technical_status] += 1

        if record["acceptance_mode"] == "after_candidate_corrections":
            dependencies = []
            for endpoint_role, candidate_id, candidate_record in (
                ("source", source_candidate_id, source_record),
                ("target", target_candidate_id, target_record),
            ):
                changed_fields = set(candidate_record.get("field_changes", {}))
                if changed_fields & _REVIEWED_FIELD_CHANGES or bool(
                    candidate_record.get("evidence_passages_changed")
                ):
                    dependencies.append((endpoint_role, candidate_id))
            if not dependencies:
                raise ReviewWritebackError(
                    f"candidate-correction-dependent relation has no correction: {relation_id}"
                )
            for dependency_order, (endpoint_role, candidate_id) in enumerate(
                dependencies, start=1
            ):
                connection.execute(
                    """
                    INSERT INTO relation_review_candidate_dependencies (
                        relation_review_pk, candidate_review_pk, dependency_order,
                        endpoint_role, dependency_reason
                    ) VALUES (?, ?, ?, ?, 'approved candidate overlay required')
                    """,
                    (
                        relation_review_pk,
                        review_by_candidate[candidate_id],
                        dependency_order,
                        endpoint_role,
                    ),
                )
                dependency_count += 1

    expected_accepted = plan.get("professionally_accepted_count")
    actual_accepted = sum(
        record.get("professional_review_decision") == "akzeptieren" for record in records
    )
    if expected_accepted is not None and actual_accepted != expected_accepted:
        raise ReviewWritebackError("professional relation count does not match the plan")
    expected_active = plan.get("active_clean_relation_count")
    if expected_active is not None and activation_counts["active"] != expected_active:
        raise ReviewWritebackError("active relation count does not match the plan")
    return {
        "activation_counts": dict(activation_counts),
        "dependency_count": dependency_count,
    }


def _validate_writeback(
    connection: sqlite3.Connection,
    *,
    candidate_plan: Mapping[str, Any],
    relation_plan: Mapping[str, Any],
    protected_before: Mapping[str, str],
) -> dict[str, Any]:
    protected_after = protected_v3_fingerprints(connection)
    if dict(protected_before) != protected_after:
        raise ReviewWritebackError("protected v3 or normalized raw rows changed")
    foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_keys:
        raise ReviewWritebackError("foreign_key_check is not empty")
    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        raise ReviewWritebackError(f"integrity_check failed: {integrity}")
    candidate_count = connection.execute(
        "SELECT COUNT(*) FROM clean_reviewed_candidates"
    ).fetchone()[0]
    relation_count = connection.execute(
        "SELECT COUNT(*) FROM clean_reviewed_relations"
    ).fetchone()[0]
    if candidate_count != candidate_plan.get("approved_clean_candidate_count"):
        raise ReviewWritebackError("clean candidate view count differs from plan")
    if relation_count != relation_plan.get("active_clean_relation_count"):
        raise ReviewWritebackError("clean relation view count differs from plan")
    return {
        "protected_after": protected_after,
        "clean_set_counts": {
            "candidates": candidate_count,
            "relations": relation_count,
        },
    }


def _validate_candidate_disposition(
    disposition: str, decision: str, review_status: str
) -> None:
    valid = {
        "include_confirmed": (
            decision in {"akzeptieren", "korrigieren"} and review_status == "confirmed"
        ),
        "exclude_rejected": decision == "verwerfen" and review_status == "rejected",
        "exclude_unresolved": review_status == "needs_review",
    }
    if disposition not in valid or not valid[disposition]:
        raise ReviewWritebackError(
            f"candidate disposition conflicts with decision/status: {disposition}"
        )


def _validate_relation_activation(
    relation_id: str,
    *,
    technical_status: str,
    source_clean: bool,
    target_clean: bool,
    catalog_compatible: bool,
) -> None:
    if technical_status == "active" and not (
        source_clean and target_clean and catalog_compatible
    ):
        raise ReviewWritebackError(f"active relation violates clean-set rules: {relation_id}")
    if technical_status == "inactive_rejected_endpoint" and source_clean and target_clean:
        raise ReviewWritebackError(f"endpoint exclusion has no excluded endpoint: {relation_id}")
    if technical_status == "inactive_catalog_conflict" and (
        not source_clean or not target_clean or catalog_compatible
    ):
        raise ReviewWritebackError(f"catalog exclusion is inconsistent: {relation_id}")


def _passage_positions(
    node_source_text: str,
    exact_text: str,
    *,
    source_candidate: sqlite3.Row,
    node_pk: int,
) -> tuple[int | None, int | None]:
    raw_start = source_candidate["char_start"]
    raw_end = source_candidate["char_end"]
    if (
        source_candidate["document_node_pk"] == node_pk
        and source_candidate["evidence_source_text"] == exact_text
        and raw_start is not None
        and raw_end is not None
        and node_source_text[raw_start:raw_end] == exact_text
    ):
        return raw_start, raw_end
    if node_source_text.count(exact_text) == 1:
        start = node_source_text.index(exact_text)
        return start, start + len(exact_text)
    return None, None


def _parse_relation_catalog(path: Path) -> dict[str, dict[str, set[str]]]:
    if not path.is_file():
        raise ReviewWritebackError(f"relation catalog does not exist: {path}")
    catalog: dict[str, dict[str, set[str]]] = {}
    current_id: str | None = None
    current_list: str | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        id_match = re.match(r"^  - id: ([a-z_]+)$", line)
        if id_match:
            current_id = id_match.group(1)
            catalog[current_id] = {
                "allowed_source_statement_types": set(),
                "allowed_target_statement_types": set(),
            }
            current_list = None
            continue
        list_match = re.match(
            r"^    (allowed_source_statement_types|allowed_target_statement_types):$",
            line,
        )
        if list_match and current_id:
            current_list = list_match.group(1)
            continue
        value_match = re.match(r"^      - ([a-z_]+)$", line)
        if value_match and current_id and current_list:
            catalog[current_id][current_list].add(value_match.group(1))
    if not catalog:
        raise ReviewWritebackError("relation catalog contains no relations")
    return catalog


def _catalog_compatible(
    catalog: Mapping[str, Mapping[str, set[str]]],
    relation_type: str,
    source_type: str,
    target_type: str,
) -> bool:
    entry = catalog.get(relation_type)
    if entry is None:
        raise ReviewWritebackError(f"relation type is missing from catalog: {relation_type}")
    return (
        source_type in entry["allowed_source_statement_types"]
        and target_type in entry["allowed_target_statement_types"]
    )


def _load_schema4_review_correction_plan(path: Path) -> Mapping[str, Any]:
    if not path.is_file():
        raise ReviewWritebackError(f"correction plan does not exist: {path}")
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReviewWritebackError(f"correction plan is not valid JSON: {error}") from error
    if not isinstance(plan, Mapping):
        raise ReviewWritebackError("correction plan must be an object")
    expected_fields = {
        "schema_version",
        "plan_version",
        "correction_id",
        "candidate_id",
        "expected_current_review",
        "target_review",
        "supersedes_review",
        "reason",
        "review_source",
        "integrity",
        "plan_sha256",
    }
    if set(plan) != expected_fields:
        raise ReviewWritebackError("correction plan fields do not match the contract")
    if plan.get("schema_version") != "candidate_review_correction_plan_v1":
        raise ReviewWritebackError("unsupported correction plan schema version")
    if plan.get("plan_version") != "1.0":
        raise ReviewWritebackError("unsupported correction plan version")
    correction_id = _required_string(plan, "correction_id")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", correction_id) is None:
        raise ReviewWritebackError("correction_id contains unsupported characters")
    _required_string(plan, "candidate_id")
    _required_string(plan, "reason")
    _required_string(plan, "review_source")
    supplied_hash = _required_string(plan, "plan_sha256")
    if re.fullmatch(r"[0-9a-f]{64}", supplied_hash) is None:
        raise ReviewWritebackError("correction plan hash is not a SHA-256 value")
    if supplied_hash != schema4_review_correction_plan_sha256(plan):
        raise ReviewWritebackError("correction plan hash does not match its payload")

    expected = plan.get("expected_current_review")
    target = plan.get("target_review")
    supersedes = plan.get("supersedes_review")
    integrity = plan.get("integrity")
    if not all(isinstance(item, Mapping) for item in (expected, target, supersedes, integrity)):
        raise ReviewWritebackError("correction plan contains invalid nested objects")
    if set(expected) != {
        "candidate_review_id",
        "review_version",
        "review_decision",
        "review_status",
        "is_current",
    }:
        raise ReviewWritebackError("expected current review fields do not match the contract")
    if set(target) != {
        "candidate_review_id",
        "review_decision",
        "review_status",
        "is_current",
        "reviewer",
        "correction_date",
    }:
        raise ReviewWritebackError("target review fields do not match the contract")
    if set(supersedes) != {"candidate_review_id", "review_version"}:
        raise ReviewWritebackError("superseded review fields do not match the contract")
    for key in (
        "candidate_review_id",
        "review_version",
        "review_decision",
        "review_status",
    ):
        _required_string(expected, key)
    for key in (
        "candidate_review_id",
        "review_decision",
        "review_status",
        "reviewer",
        "correction_date",
    ):
        _required_string(target, key)
    for key in ("candidate_review_id", "review_version"):
        _required_string(supersedes, key)
    if expected["is_current"] != 1 or target["is_current"] != 1:
        raise ReviewWritebackError("correction plan must expect and create one current review")
    if (
        target["review_decision"] != "nicht_entschieden"
        or target["review_status"] != "needs_review"
    ):
        raise ReviewWritebackError(
            "schema-v4 correction target must be nicht_entschieden/needs_review"
        )
    if dict(supersedes) != {
        "candidate_review_id": expected["candidate_review_id"],
        "review_version": expected["review_version"],
    }:
        raise ReviewWritebackError("superseded review does not match expected current review")
    required_integrity = {
        "database_sha256_before",
        "candidate_sha256",
        "candidate_row_sha256",
        "current_review_row_sha256",
        "evidence_passages_sha256",
        "protected_v3_fingerprints",
    }
    if set(integrity) != required_integrity:
        raise ReviewWritebackError("correction integrity fields do not match the contract")
    for key in required_integrity - {"protected_v3_fingerprints"}:
        value = integrity.get(key)
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ReviewWritebackError(f"invalid correction integrity fingerprint: {key}")
    if not isinstance(integrity.get("protected_v3_fingerprints"), Mapping):
        raise ReviewWritebackError("protected v3 fingerprints must be an object")
    return plan


def _require_schema4(connection: sqlite3.Connection) -> None:
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version != 4:
        raise ReviewWritebackError(
            f"schema-v4 review correction requires schema version 4, found {version}"
        )


def _schema4_review_state(
    connection: sqlite3.Connection,
    candidate_id: str,
) -> tuple[sqlite3.Row, sqlite3.Row, list[sqlite3.Row]]:
    candidates = connection.execute(
        "SELECT * FROM statement_candidates_v3 WHERE candidate_id = ?",
        (candidate_id,),
    ).fetchall()
    if len(candidates) != 1:
        raise ReviewWritebackError(
            f"candidate_id must resolve exactly once: {candidate_id}"
        )
    candidate = candidates[0]
    current_reviews = connection.execute(
        """
        SELECT * FROM candidate_review_overlays
        WHERE statement_candidate_v3_pk = ? AND is_current = 1
        """,
        (candidate["statement_candidate_v3_pk"],),
    ).fetchall()
    if len(current_reviews) != 1:
        raise ReviewWritebackError(
            f"candidate must have exactly one current review: {candidate_id}"
        )
    current_review = current_reviews[0]
    passages = connection.execute(
        """
        SELECT * FROM candidate_review_evidence_passages
        WHERE candidate_review_pk = ? ORDER BY passage_order
        """,
        (current_review["candidate_review_pk"],),
    ).fetchall()
    if not passages:
        raise ReviewWritebackError("current review has no evidence passages")
    return candidate, current_review, passages


def _validate_schema4_review_correction_preconditions(
    connection: sqlite3.Connection,
    plan: Mapping[str, Any],
    *,
    plan_path: Path,
    database_sha256: str,
) -> tuple[sqlite3.Row, sqlite3.Row, list[sqlite3.Row], dict[str, str]]:
    configured_source = Path(_required_string(plan, "review_source")).resolve()
    if configured_source != plan_path:
        raise ReviewWritebackError("review_source does not resolve to correction plan")
    candidate_id = _required_string(plan, "candidate_id")
    candidate, current_review, passages = _schema4_review_state(
        connection, candidate_id
    )
    expected = plan["expected_current_review"]
    actual_expected = {
        "candidate_review_id": current_review["candidate_review_id"],
        "review_version": current_review["review_version"],
        "review_decision": current_review["review_decision"],
        "review_status": current_review["review_status"],
        "is_current": current_review["is_current"],
    }
    if dict(expected) != actual_expected:
        raise ReviewWritebackError("expected current review does not match database state")
    protected = protected_v3_fingerprints(connection)
    actual_integrity = {
        "database_sha256_before": database_sha256,
        "candidate_sha256": candidate["candidate_sha256"],
        "candidate_row_sha256": _rows_sha256(
            connection,
            "statement_candidates_v3",
            "statement_candidate_v3_pk = ?",
            (candidate["statement_candidate_v3_pk"],),
        ),
        "current_review_row_sha256": _rows_sha256(
            connection,
            "candidate_review_overlays",
            "candidate_review_pk = ?",
            (current_review["candidate_review_pk"],),
        ),
        "evidence_passages_sha256": _rows_payload_sha256(passages),
        "protected_v3_fingerprints": protected,
    }
    if dict(plan["integrity"]) != actual_integrity:
        raise ReviewWritebackError("database fingerprint does not match correction plan")
    return candidate, current_review, passages, protected


def _rows_sha256(
    connection: sqlite3.Connection,
    table: str,
    where_sql: str,
    parameters: tuple[Any, ...],
) -> str:
    if table not in {
        "statement_candidates_v3",
        "candidate_review_overlays",
        "relation_review_overlays",
        "relation_review_candidate_dependencies",
    }:
        raise ReviewWritebackError(f"unsupported fingerprint table: {table}")
    primary_keys = [
        row[1]
        for row in connection.execute(f'PRAGMA table_info("{table}")')
        if row[5]
    ]
    order_sql = ", ".join(f'"{column}"' for column in primary_keys) or "rowid"
    rows = connection.execute(
        f'SELECT * FROM "{table}" WHERE {where_sql} ORDER BY {order_sql}',
        parameters,
    ).fetchall()
    return _rows_payload_sha256(rows)


def _rows_payload_sha256(rows: list[sqlite3.Row]) -> str:
    columns = list(rows[0].keys()) if rows else []
    payload = {
        "columns": columns,
        "rows": [[_json_cell(value) for value in tuple(row)] for row in rows],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _relation_review_fingerprints(
    connection: sqlite3.Connection,
) -> dict[str, str]:
    return {
        table: _rows_sha256(connection, table, "1 = 1", ())
        for table in (
            "relation_review_overlays",
            "relation_review_candidate_dependencies",
        )
    }


def _mapping_records(plan: Mapping[str, Any], label: str) -> list[Mapping[str, Any]]:
    records = plan.get("records")
    if not isinstance(records, list) or not records:
        raise ReviewWritebackError(f"{label} plan has no records")
    if not all(isinstance(record, Mapping) for record in records):
        raise ReviewWritebackError(f"{label} plan record must be an object")
    return records


def _required_string(
    value: Mapping[str, Any], key: str, *, trim: bool = True
) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item or (trim and not item.strip()):
        raise ReviewWritebackError(f"missing non-empty string: {key}")
    return item.strip() if trim else item


def _require_nonempty(value: str, label: str) -> None:
    if not value.strip():
        raise ReviewWritebackError(f"{label} must not be empty")


def _create_byte_identical_backup(path: Path) -> Path:
    for suffix in ("-wal", "-journal"):
        sidecar = Path(f"{path}{suffix}")
        if sidecar.exists() and sidecar.stat().st_size:
            raise ReviewWritebackError(f"database has an active SQLite sidecar: {sidecar}")
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(f"{path.name}.v3.{timestamp}.bak")
    counter = 1
    while backup.exists():
        backup = path.with_name(f"{path.name}.v3.{timestamp}.{counter}.bak")
        counter += 1
    shutil.copy2(path, backup)
    return backup


def _create_schema4_correction_backup(
    path: Path,
    *,
    correction_id: str,
    database_sha256: str,
) -> Path:
    for suffix in ("-wal", "-journal"):
        sidecar = Path(f"{path}{suffix}")
        if sidecar.exists() and sidecar.stat().st_size:
            raise ReviewWritebackError(f"database has an active SQLite sidecar: {sidecar}")
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", correction_id).strip("-")
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    stem = (
        f"{path.name}.schema4-review-correction.{safe_id}."
        f"before-{database_sha256[:16]}.{timestamp}"
    )
    backup = path.with_name(f"{stem}.bak")
    counter = 1
    while backup.exists():
        backup = path.with_name(f"{stem}.{counter}.bak")
        counter += 1
    shutil.copy2(path, backup)
    return backup


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_cell(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"bytes_hex": value.hex()}
    return value
