from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path

from info_system.db.connection import connect

SCHEMA_VERSION = 4

LEGACY_TABLES = (
    "documents",
    "source_segments",
    "extraction_runs",
    "extracted_records",
    "retrieval_runs",
    "retrieval_hits",
    "tool_search_hits",
)


def load_schema_sql() -> str:
    return files("info_system.db").joinpath("schema.sql").read_text(encoding="utf-8")


def initialize_database(db_path: str | Path) -> Path:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    needs_migration = (
        path.exists() and path.stat().st_size > 0 and _needs_migration(path)
    )
    if needs_migration:
        _backup_database(path)

    with connect(path) as connection:
        if _is_legacy_v0_schema(connection):
            _migrate_legacy_v0_to_current(connection, path)
        elif _user_version(connection) == 1:
            _migrate_v1_to_v2(connection, path)
            _migrate_v2_to_v3(connection, path)
            _migrate_v3_to_v4(connection, path)
        elif _user_version(connection) == 2:
            _migrate_v2_to_v3(connection, path)
            _migrate_v3_to_v4(connection, path)
        elif _user_version(connection) == 3:
            _migrate_v3_to_v4(connection, path)
        else:
            connection.executescript(load_schema_sql())
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    return path


def _needs_migration(path: Path) -> bool:
    with sqlite3.connect(path) as connection:
        user_version = _user_version(connection)
        return user_version < SCHEMA_VERSION and _has_user_tables(connection)


def _user_version(connection: sqlite3.Connection) -> int:
    return connection.execute("PRAGMA user_version").fetchone()[0]


def _has_user_tables(connection: sqlite3.Connection) -> bool:
    return (
        connection.execute(
            """
            SELECT COUNT(*)
            FROM sqlite_master
            WHERE type = 'table'
            AND name NOT LIKE 'sqlite_%'
            """
        ).fetchone()[0]
        > 0
    )


def _is_legacy_v0_schema(connection: sqlite3.Connection) -> bool:
    if _user_version(connection) != 0:
        return False
    columns = _table_columns(connection, "documents")
    return "document_id" in columns and "document_pk" not in columns


def _table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row[1] for row in rows}


def _backup_database(path: Path) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    with sqlite3.connect(path) as connection:
        source_version = _user_version(connection)
    backup_path = path.with_name(f"{path.name}.v{source_version}.{timestamp}.bak")
    counter = 1
    while backup_path.exists():
        backup_path = path.with_name(
            f"{path.name}.v{source_version}.{timestamp}.{counter}.bak"
        )
        counter += 1
    shutil.copy2(path, backup_path)
    return backup_path


def _migrate_legacy_v0_to_current(connection: sqlite3.Connection, path: Path) -> None:
    legacy_schema = _legacy_schema(connection)
    legacy_counts = _legacy_counts(connection)
    database_hash = _sha256_file(path)
    migrated_at = datetime.now(UTC).isoformat()

    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute("BEGIN IMMEDIATE")
    try:
        for table in LEGACY_TABLES:
            if _table_exists(connection, table):
                connection.execute(
                    f"CREATE TABLE IF NOT EXISTS legacy_v0_{table} AS SELECT * FROM {table}"
                )

        _drop_legacy_runtime_tables(connection)
        _execute_sql_script(connection, load_schema_sql())
        _copy_legacy_rows_into_v1(connection)
        connection.execute(
            "INSERT INTO source_segments_fts(source_segments_fts) VALUES ('rebuild')"
        )
        connection.execute(
            "INSERT INTO document_nodes_fts(document_nodes_fts) VALUES ('rebuild')"
        )
        connection.execute(
            """
            INSERT INTO schema_migration_audit (
                migration_id,
                from_user_version,
                to_user_version,
                database_sha256_before,
                legacy_schema_json,
                legacy_table_counts_json,
                migrated_at
            )
            VALUES (?, 0, ?, ?, ?, ?, ?)
            """,
            (
                f"legacy-v0-to-v{SCHEMA_VERSION}",
                SCHEMA_VERSION,
                database_hash,
                json.dumps(legacy_schema, ensure_ascii=False, sort_keys=True),
                json.dumps(legacy_counts, ensure_ascii=False, sort_keys=True),
                migrated_at,
            ),
        )
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    finally:
        connection.execute("PRAGMA foreign_keys = ON")


def _execute_sql_script(connection: sqlite3.Connection, sql_script: str) -> None:
    statement_lines: list[str] = []
    for line in sql_script.splitlines():
        statement_lines.append(line)
        statement = "\n".join(statement_lines).strip()
        if sqlite3.complete_statement(statement):
            connection.execute(statement)
            statement_lines.clear()
    remaining = "\n".join(statement_lines).strip()
    if remaining:
        raise sqlite3.OperationalError("incomplete SQL statement in schema script")


def _legacy_schema(connection: sqlite3.Connection) -> dict[str, str | None]:
    rows = connection.execute(
        """
        SELECT name, sql
        FROM sqlite_master
        WHERE type IN ('table', 'index', 'trigger')
        AND name NOT LIKE 'sqlite_%'
        ORDER BY type, name
        """
    ).fetchall()
    return {row[0]: row[1] for row in rows}


def _legacy_counts(connection: sqlite3.Connection) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in LEGACY_TABLES:
        if _table_exists(connection, table):
            counts[table] = connection.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]
    return counts


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    return (
        connection.execute(
            """
            SELECT COUNT(*)
            FROM sqlite_master
            WHERE type IN ('table', 'view')
            AND name = ?
            """,
            (table_name,),
        ).fetchone()[0]
        == 1
    )


def _drop_legacy_runtime_tables(connection: sqlite3.Connection) -> None:
    for table in (
        "source_segments_fts",
        "tool_search_hits",
        "retrieval_hits",
        "retrieval_runs",
        "extracted_records",
        "extraction_runs",
        "source_segments",
        "documents",
    ):
        if _table_exists(connection, table):
            connection.execute(f"DROP TABLE {table}")


def _copy_legacy_rows_into_v1(connection: sqlite3.Connection) -> None:
    if _table_exists(connection, "legacy_v0_documents"):
        connection.execute(
            """
            INSERT INTO documents (
                document_id,
                filename,
                file_type,
                source_path,
                processed_text_path,
                imported_at,
                title,
                document_hash
            )
            SELECT
                document_id,
                filename,
                file_type,
                source_path,
                processed_text_path,
                imported_at,
                title,
                document_hash
            FROM legacy_v0_documents
            """
        )
        connection.execute(
            """
            INSERT INTO document_versions (
                document_version_id,
                document_pk,
                version_label,
                content_hash,
                is_current,
                created_at
            )
            SELECT
                document_id || ':v1',
                document_pk,
                'legacy-v0',
                document_hash,
                1,
                imported_at
            FROM documents
            """
        )

    if _table_exists(connection, "legacy_v0_source_segments"):
        connection.execute(
            """
            INSERT INTO source_segments (
                segment_id,
                document_id,
                page_number,
                section_label,
                segment_order,
                text,
                created_at
            )
            SELECT
                segment_id,
                document_id,
                page_number,
                section_label,
                segment_order,
                text,
                created_at
            FROM legacy_v0_source_segments
            """
        )
        connection.execute(
            """
            INSERT INTO document_nodes (
                document_node_id,
                document_version_pk,
                node_path,
                node_rank,
                element_type,
                heading,
                locator,
                page_start,
                page_end,
                raw_extracted_text,
                source_text,
                search_text,
                is_citable,
                created_at
            )
            SELECT
                legacy.segment_id,
                versions.document_version_pk,
                '/' || legacy.segment_order,
                legacy.segment_order,
                'page_text',
                legacy.section_label,
                'page=' || legacy.page_number || ';segment=' || legacy.segment_order,
                legacy.page_number,
                legacy.page_number,
                legacy.text,
                legacy.text,
                legacy.text,
                1,
                legacy.created_at
            FROM legacy_v0_source_segments AS legacy
            JOIN documents
                ON documents.document_id = legacy.document_id
            JOIN document_versions AS versions
                ON versions.document_pk = documents.document_pk
                AND versions.is_current = 1
            """
        )

    if _table_exists(connection, "legacy_v0_extraction_runs"):
        connection.execute(
            """
            INSERT INTO extraction_runs (
                extraction_run_id,
                document_id,
                started_at,
                completed_at,
                method,
                model_name,
                prompt_version,
                status
            )
            SELECT
                extraction_run_id,
                document_id,
                started_at,
                completed_at,
                method,
                model_name,
                prompt_version,
                status
            FROM legacy_v0_extraction_runs
            """
        )

    if _table_exists(connection, "legacy_v0_extracted_records"):
        connection.execute(
            """
            INSERT INTO extracted_records (
                record_id,
                record_type,
                payload_json,
                source_document_id,
                source_chunk_id,
                source_page,
                source_section,
                confidence,
                validation_status,
                extraction_run_id,
                created_at
            )
            SELECT
                record_id,
                record_type,
                payload_json,
                source_document_id,
                source_chunk_id,
                source_page,
                source_section,
                confidence,
                validation_status,
                extraction_run_id,
                created_at
            FROM legacy_v0_extracted_records
            WHERE json_valid(payload_json)
            """
        )

    if _table_exists(connection, "legacy_v0_retrieval_runs"):
        connection.execute(
            """
            INSERT INTO retrieval_runs (
                retrieval_run_id,
                query_text,
                retrieval_mode,
                top_k,
                prompt_version,
                llm_provider,
                answer_mode,
                answer_text,
                started_at,
                completed_at,
                status
            )
            SELECT
                retrieval_run_id,
                query_text,
                retrieval_mode,
                top_k,
                prompt_version,
                llm_provider,
                answer_mode,
                answer_text,
                started_at,
                completed_at,
                status
            FROM legacy_v0_retrieval_runs
            """
        )

    if _table_exists(connection, "legacy_v0_retrieval_hits"):
        connection.execute(
            """
            INSERT INTO retrieval_hits (
                hit_id,
                retrieval_run_id,
                retrieval_method,
                document_id,
                chunk_id,
                rank,
                score,
                snippet,
                created_at
            )
            SELECT
                hit_id,
                retrieval_run_id,
                retrieval_method,
                document_id,
                chunk_id,
                rank,
                score,
                snippet,
                created_at
            FROM legacy_v0_retrieval_hits
            """
        )

    if _table_exists(connection, "legacy_v0_tool_search_hits"):
        connection.execute(
            """
            INSERT INTO tool_search_hits (
                tool_hit_id,
                retrieval_run_id,
                tool_name,
                search_pattern,
                document_id,
                segment_id,
                source_path,
                page_number,
                line_number,
                context_before,
                matched_text,
                context_after
            )
            SELECT
                tool_hit_id,
                retrieval_run_id,
                tool_name,
                search_pattern,
                document_id,
                segment_id,
                source_path,
                page_number,
                line_number,
                context_before,
                matched_text,
                context_after
            FROM legacy_v0_tool_search_hits
            """
        )


def _migrate_v1_to_v2(connection: sqlite3.Connection, path: Path) -> None:
    legacy_schema = _legacy_schema(connection)
    legacy_counts = _legacy_counts(connection)
    database_hash = _sha256_file(path)
    migrated_at = datetime.now(UTC).isoformat()

    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute("BEGIN IMMEDIATE")
    try:
        columns = _table_columns(connection, "document_nodes")
        if "element_type" not in columns:
            connection.execute(
                """
                ALTER TABLE document_nodes
                ADD COLUMN element_type TEXT NOT NULL DEFAULT 'page_text'
                CHECK (
                    element_type IN (
                        'document_root',
                        'section',
                        'page_text',
                        'empty_page',
                        'non_citable_text'
                    )
                )
                """
            )
        connection.execute(
            """
            UPDATE document_nodes
            SET element_type = CASE
                WHEN parent_document_node_pk IS NULL THEN 'document_root'
                WHEN page_start IS NULL AND page_end IS NULL THEN 'section'
                WHEN is_citable = 0 AND source_text = '' THEN 'empty_page'
                WHEN is_citable = 0 THEN 'non_citable_text'
                ELSE 'page_text'
            END
            """
        )
        connection.execute(
            "INSERT INTO document_nodes_fts(document_nodes_fts) VALUES ('rebuild')"
        )
        connection.execute(
            """
            INSERT INTO schema_migration_audit (
                migration_id,
                from_user_version,
                to_user_version,
                database_sha256_before,
                legacy_schema_json,
                legacy_table_counts_json,
                migrated_at
            )
            VALUES (?, 1, 2, ?, ?, ?, ?)
            ON CONFLICT(migration_id) DO NOTHING
            """,
            (
                "v1-to-v2-add-document-node-element-type",
                database_hash,
                json.dumps(legacy_schema, ensure_ascii=False, sort_keys=True),
                json.dumps(legacy_counts, ensure_ascii=False, sort_keys=True),
                migrated_at,
            ),
        )
        connection.execute("PRAGMA user_version = 2")
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    finally:
        connection.execute("PRAGMA foreign_keys = ON")


def _migrate_v2_to_v3(connection: sqlite3.Connection, path: Path) -> None:
    legacy_schema = _legacy_schema(connection)
    legacy_counts = _legacy_counts(connection)
    database_hash = _sha256_file(path)
    migrated_at = datetime.now(UTC).isoformat()

    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute("BEGIN IMMEDIATE")
    try:
        _execute_sql_script(connection, load_schema_sql())
        connection.execute(
            """
            INSERT INTO schema_migration_audit (
                migration_id,
                from_user_version,
                to_user_version,
                database_sha256_before,
                legacy_schema_json,
                legacy_table_counts_json,
                migrated_at
            )
            VALUES (?, 2, 3, ?, ?, ?, ?)
            ON CONFLICT(migration_id) DO NOTHING
            """,
            (
                "v2-to-v3-add-lossless-statement-candidate-import",
                database_hash,
                json.dumps(legacy_schema, ensure_ascii=False, sort_keys=True),
                json.dumps(legacy_counts, ensure_ascii=False, sort_keys=True),
                migrated_at,
            ),
        )
        connection.execute("PRAGMA user_version = 3")
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    finally:
        connection.execute("PRAGMA foreign_keys = ON")


def apply_v3_to_v4_migration(
    connection: sqlite3.Connection,
    path: Path,
    *,
    database_hash_before: str | None = None,
    migrated_at: str | None = None,
) -> None:
    """Apply the additive v3-to-v4 step inside the caller's transaction."""
    if _user_version(connection) != 3:
        raise ValueError("v3-to-v4 migration requires PRAGMA user_version = 3")
    schema_before = _legacy_schema(connection)
    v3_counts = {
        table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in (
            "statement_candidate_batches_v3",
            "statement_candidate_node_processing_v3",
            "statement_candidates_v3",
            "statement_candidate_document_references_v3",
            "statement_relations_v3",
        )
    }
    _execute_sql_script(connection, load_schema_sql())
    connection.execute(
        """
        INSERT INTO schema_migration_audit (
            migration_id,
            from_user_version,
            to_user_version,
            database_sha256_before,
            legacy_schema_json,
            legacy_table_counts_json,
            migrated_at
        )
        VALUES (?, 3, 4, ?, ?, ?, ?)
        ON CONFLICT(migration_id) DO NOTHING
        """,
        (
            "v3-to-v4-add-reviewed-clean-set-overlays",
            database_hash_before or _sha256_file(path),
            json.dumps(schema_before, ensure_ascii=False, sort_keys=True),
            json.dumps(v3_counts, ensure_ascii=False, sort_keys=True),
            migrated_at or datetime.now(UTC).isoformat(),
        ),
    )
    connection.execute("PRAGMA user_version = 4")


def _migrate_v3_to_v4(connection: sqlite3.Connection, path: Path) -> None:
    database_hash = _sha256_file(path)
    migrated_at = datetime.now(UTC).isoformat()
    connection.execute("BEGIN IMMEDIATE")
    try:
        apply_v3_to_v4_migration(
            connection,
            path,
            database_hash_before=database_hash,
            migrated_at=migrated_at,
        )
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
