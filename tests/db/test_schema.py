from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from info_system.db.connection import connect
from info_system.db import initialize_database
from info_system.db.schema import SCHEMA_VERSION


EXPECTED_TABLES = {
    "projects",
    "documents",
    "document_versions",
    "document_nodes",
    "document_references",
    "document_nodes_fts",
    "statements",
    "statement_sources",
    "entities",
    "entity_aliases",
    "statement_entities",
    "statement_values",
    "statement_conditions",
    "classifications",
    "statement_classifications",
    "statement_groups",
    "statement_group_members",
    "statement_relations",
    "statement_candidate_batches_v3",
    "statement_candidate_node_processing_v3",
    "statement_candidates_v3",
    "statement_candidate_document_references_v3",
    "statement_relations_v3",
    "candidate_review_overlays",
    "candidate_review_evidence_passages",
    "relation_review_overlays",
    "relation_review_candidate_dependencies",
    "query_runs",
    "evidence_items",
    "answers",
    "source_segments",
    "source_segments_fts",
    "extracted_records",
    "extraction_runs",
    "retrieval_runs",
    "retrieval_hits",
    "tool_search_hits",
}

EXPECTED_REVIEW_VIEWS = {
    "clean_reviewed_candidates",
    "clean_reviewed_candidate_evidence",
    "clean_reviewed_relations",
}


def table_names(db_path) -> set[str]:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'virtual table')"
        ).fetchall()
    return {row[0] for row in rows}


def view_names(db_path) -> set[str]:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'view'"
        ).fetchall()
    return {row[0] for row in rows}


def test_initialize_database_creates_minimal_schema(tmp_path) -> None:
    db_path = tmp_path / "prototype.sqlite"

    initialize_database(db_path)

    with sqlite3.connect(db_path) as connection:
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]
        document_node_columns = {
            row[1]: row
            for row in connection.execute("PRAGMA table_info(document_nodes)")
        }

    assert EXPECTED_TABLES.issubset(table_names(db_path))
    assert EXPECTED_REVIEW_VIEWS.issubset(view_names(db_path))
    assert user_version == SCHEMA_VERSION
    assert "element_type" in document_node_columns
    assert document_node_columns["element_type"][2] == "TEXT"


def test_initialize_database_is_idempotent(tmp_path) -> None:
    db_path = tmp_path / "prototype.sqlite"

    initialize_database(db_path)
    initialize_database(db_path)

    assert db_path.exists()


def test_application_connection_enables_foreign_keys(tmp_path) -> None:
    db_path = initialize_database(tmp_path / "prototype.sqlite")

    with connect(db_path) as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_migrates_representative_legacy_schema_without_data_loss(tmp_path) -> None:
    db_path = tmp_path / "legacy.sqlite"
    _create_legacy_database(db_path)

    initialize_database(db_path)

    with sqlite3.connect(db_path) as connection:
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]
        tables = table_names(db_path)
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "documents",
                "source_segments",
                "extracted_records",
                "legacy_v0_documents",
                "legacy_v0_source_segments",
                "legacy_v0_extracted_records",
            )
        }
        document_version_count = connection.execute(
            "SELECT COUNT(*) FROM document_versions"
        ).fetchone()[0]
        document_node_count = connection.execute(
            "SELECT COUNT(*) FROM document_nodes"
        ).fetchone()[0]
        audit_count = connection.execute(
            "SELECT COUNT(*) FROM schema_migration_audit"
        ).fetchone()[0]

    backups = list(tmp_path.glob("legacy.sqlite.v0.*.bak"))
    assert user_version == SCHEMA_VERSION
    assert "legacy_v0_documents" in tables
    assert counts == {
        "documents": 1,
        "source_segments": 1,
        "extracted_records": 1,
        "legacy_v0_documents": 1,
        "legacy_v0_source_segments": 1,
        "legacy_v0_extracted_records": 1,
    }
    assert document_version_count == 1
    assert document_node_count == 1
    assert audit_count == 1
    assert backups


def test_migrates_v1_schema_to_v2_with_deterministic_element_types(tmp_path) -> None:
    db_path = tmp_path / "v1.sqlite"
    _create_v1_database(db_path)

    initialize_database(db_path)

    with sqlite3.connect(db_path) as connection:
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]
        columns = {row[1] for row in connection.execute("PRAGMA table_info(document_nodes)")}
        element_types = connection.execute(
            """
            SELECT document_node_id, element_type
            FROM document_nodes
            ORDER BY node_rank
            """
        ).fetchall()
        audit = connection.execute(
            """
            SELECT from_user_version, to_user_version
            FROM schema_migration_audit
            WHERE migration_id = 'v1-to-v2-add-document-node-element-type'
            """
        ).fetchone()
        fts_count = connection.execute(
            "SELECT COUNT(*) FROM document_nodes_fts"
        ).fetchone()[0]

    assert user_version == SCHEMA_VERSION
    assert "element_type" in columns
    assert element_types == [
        ("root", "document_root"),
        ("section", "section"),
        ("page", "page_text"),
        ("empty", "empty_page"),
        ("caption", "non_citable_text"),
    ]
    assert audit == (1, 2)
    assert fts_count == 5


def test_migrates_v2_schema_additively_for_lossless_v3_candidate_import(
    tmp_path,
) -> None:
    db_path = initialize_database(tmp_path / "v2.sqlite")
    v3_tables = (
        "statement_relations_v3",
        "statement_candidate_document_references_v3",
        "statement_candidate_node_processing_v3",
        "statement_candidates_v3",
        "statement_candidate_batches_v3",
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        for table in v3_tables:
            connection.execute(f"DROP TABLE {table}")
        connection.execute("PRAGMA user_version = 2")

    initialize_database(db_path)

    with sqlite3.connect(db_path) as connection:
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]
        audit = connection.execute(
            """
            SELECT from_user_version, to_user_version
            FROM schema_migration_audit
            WHERE migration_id = 'v2-to-v3-add-lossless-statement-candidate-import'
            """
        ).fetchone()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]

    assert user_version == SCHEMA_VERSION
    assert set(v3_tables).issubset(table_names(db_path))
    assert audit == (2, 3)
    assert integrity == "ok"
    assert list(tmp_path.glob("v2.sqlite.v2.*.bak"))


def test_migrates_v3_schema_additively_for_review_overlays(tmp_path) -> None:
    db_path = initialize_database(tmp_path / "v3.sqlite")
    review_views = (
        "clean_reviewed_relations",
        "clean_reviewed_candidate_evidence",
        "clean_reviewed_candidates",
    )
    review_tables = (
        "relation_review_candidate_dependencies",
        "relation_review_overlays",
        "candidate_review_evidence_passages",
        "candidate_review_overlays",
    )
    with sqlite3.connect(db_path) as connection:
        for view in review_views:
            connection.execute(f"DROP VIEW {view}")
        for table in review_tables:
            connection.execute(f"DROP TABLE {table}")
        connection.execute("PRAGMA user_version = 3")

    initialize_database(db_path)

    with sqlite3.connect(db_path) as connection:
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]
        audit = connection.execute(
            """
            SELECT from_user_version, to_user_version
            FROM schema_migration_audit
            WHERE migration_id = 'v3-to-v4-add-reviewed-clean-set-overlays'
            """
        ).fetchone()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]

    assert user_version == SCHEMA_VERSION
    assert set(review_tables).issubset(table_names(db_path))
    assert set(review_views).issubset(view_names(db_path))
    assert audit == (3, 4)
    assert integrity == "ok"
    assert list(tmp_path.glob("v3.sqlite.v3.*.bak"))


def test_statement_status_source_and_relation_constraints(tmp_path) -> None:
    db_path = initialize_database(tmp_path / "prototype.sqlite")

    with sqlite3.connect(db_path) as connection:
        statement_pk = _insert_statement(connection, "stmt-1")
        other_statement_pk = _insert_statement(connection, "stmt-2")

        for statement_type in (
            "requirement",
            "planning_measure",
            "decision",
            "finding",
        ):
            _insert_statement(connection, f"stmt-type-{statement_type}", statement_type)

        with pytest.raises(sqlite3.IntegrityError):
            _insert_statement(connection, "stmt-bad-type", "wish")

        for relation_type in (
            "addresses",
            "derived_from",
            "implements_decision",
            "supersedes",
            "conflicts_with",
        ):
            connection.execute(
                """
                INSERT INTO statement_relations (
                    statement_relation_id,
                    source_statement_pk,
                    target_statement_pk,
                    relation_type,
                    created_at
                )
                VALUES (?, ?, ?, ?, '2026-08-07T00:00:00Z')
                """,
                (
                    f"rel-{relation_type}",
                    statement_pk,
                    other_statement_pk,
                    relation_type,
                ),
            )

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO statement_relations (
                    statement_relation_id,
                    source_statement_pk,
                    target_statement_pk,
                    relation_type,
                    created_at
                )
                VALUES ('rel-fulfills', ?, ?, 'fulfills', '2026-08-07T00:00:00Z')
                """,
                (statement_pk, other_statement_pk),
            )

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO statement_relations (
                    statement_relation_id,
                    source_statement_pk,
                    target_statement_pk,
                    relation_type,
                    created_at
                )
                VALUES ('rel-self', ?, ?, 'addresses', '2026-08-07T00:00:00Z')
                """,
                (statement_pk, statement_pk),
            )

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                UPDATE statements
                SET review_status = 'confirmed',
                    reviewed_by = 'tester',
                    reviewed_at = '2026-08-07T00:00:00Z'
                WHERE statement_pk = ?
                """,
                (statement_pk,),
            )

        document_node_pk = _insert_document_node(connection)
        source_pk = _insert_statement_source(connection, statement_pk, document_node_pk)
        connection.execute(
            """
            UPDATE statements
            SET review_status = 'confirmed',
                reviewed_by = 'tester',
                reviewed_at = '2026-08-07T00:00:00Z'
            WHERE statement_pk = ?
            """,
            (statement_pk,),
        )

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "DELETE FROM statement_sources WHERE statement_source_pk = ?",
                (source_pk,),
            )


def test_document_version_locator_json_evidence_and_value_constraints(tmp_path) -> None:
    db_path = initialize_database(tmp_path / "prototype.sqlite")

    with sqlite3.connect(db_path) as connection:
        document_pk = _insert_document(connection)
        _insert_document_version(connection, document_pk, "version-current", is_current=1)

        with pytest.raises(sqlite3.IntegrityError):
            _insert_document_version(connection, document_pk, "version-current-2", is_current=1)

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO document_nodes (
                    document_node_id,
                    document_version_pk,
                    node_path,
                    node_rank,
                    page_start,
                    page_end,
                    char_start,
                    char_end,
                    raw_extracted_text,
                    source_text,
                    search_text,
                    is_citable,
                    created_at
                )
                VALUES (
                    'bad-node', 1, '/bad', 0, 2, 1, 10, 5,
                    'raw', 'source', 'search', 1, '2026-08-07T00:00:00Z'
                )
                """
            )

        statement_pk = _insert_statement(connection, "stmt-json")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO query_runs (
                    query_run_id,
                    question_text,
                    search_intent_json,
                    created_at,
                    status
                )
                VALUES ('query-bad-json', 'Frage?', '{bad', '2026-08-07T00:00:00Z', 'planned')
                """
            )

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO statement_values (
                    statement_value_id,
                    statement_pk,
                    value_name,
                    raw_value,
                    created_at
                )
                VALUES ('value-empty', ?, 'height', '10 m', '2026-08-07T00:00:00Z')
                """,
                (statement_pk,),
            )

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO statement_values (
                    statement_value_id,
                    statement_pk,
                    value_name,
                    raw_value,
                    numeric_value,
                    created_at
                )
                VALUES ('value-no-unit', ?, 'height', '10 m', 10, '2026-08-07T00:00:00Z')
                """,
                (statement_pk,),
            )

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO evidence_items (
                    evidence_item_id,
                    query_run_pk,
                    rank,
                    excerpt,
                    created_at
                )
                VALUES ('evidence-empty', NULL, 1, 'Text', '2026-08-07T00:00:00Z')
                """
            )


def test_document_nodes_fts_tracks_source_and_search_text_only(tmp_path) -> None:
    db_path = initialize_database(tmp_path / "prototype.sqlite")

    with sqlite3.connect(db_path) as connection:
        node_pk = _insert_document_node(
            connection,
            raw_text="rohgeheimnis",
            source_text="Brandschutzkonzept Treppe",
            search_text="brandschutz treppe",
        )
        assert _fts_count(connection, "Brandschutzkonzept") == 1
        assert _fts_count(connection, "rohgeheimnis") == 0

        connection.execute(
            """
            UPDATE document_nodes
            SET source_text = 'Rettungsweg',
                search_text = 'rettungsweg'
            WHERE document_node_pk = ?
            """,
            (node_pk,),
        )
        assert _fts_count(connection, "Rettungsweg") == 1
        assert _fts_count(connection, "Brandschutzkonzept") == 0

        connection.execute(
            "INSERT INTO document_nodes_fts(document_nodes_fts) VALUES ('rebuild')"
        )
        assert _fts_count(connection, "Rettungsweg") == 1

        connection.execute(
            "DELETE FROM document_nodes WHERE document_node_pk = ?",
            (node_pk,),
        )
        assert _fts_count(connection, "Rettungsweg") == 0

        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def _create_legacy_database(db_path: Path) -> None:
    connection = sqlite3.connect(db_path)
    connection.executescript(
        """
        CREATE TABLE documents (
            document_id TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            file_type TEXT NOT NULL,
            source_path TEXT NOT NULL,
            processed_text_path TEXT,
            imported_at TEXT NOT NULL,
            title TEXT,
            document_hash TEXT
        );
        CREATE TABLE source_segments (
            segment_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            page_number INTEGER,
            section_label TEXT,
            segment_order INTEGER NOT NULL,
            text TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (document_id) REFERENCES documents(document_id)
        );
        CREATE VIRTUAL TABLE source_segments_fts USING fts5(
            segment_id UNINDEXED,
            document_id UNINDEXED,
            text,
            content='source_segments',
            content_rowid='rowid'
        );
        CREATE TABLE extraction_runs (
            extraction_run_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            method TEXT NOT NULL,
            model_name TEXT,
            prompt_version TEXT,
            status TEXT NOT NULL,
            FOREIGN KEY (document_id) REFERENCES documents(document_id)
        );
        CREATE TABLE extracted_records (
            record_id TEXT PRIMARY KEY,
            record_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            source_document_id TEXT NOT NULL,
            source_chunk_id TEXT NOT NULL,
            source_page INTEGER,
            source_section TEXT,
            confidence REAL NOT NULL,
            validation_status TEXT NOT NULL,
            extraction_run_id TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (source_document_id) REFERENCES documents(document_id),
            FOREIGN KEY (source_chunk_id) REFERENCES source_segments(segment_id),
            FOREIGN KEY (extraction_run_id) REFERENCES extraction_runs(extraction_run_id)
        );
        INSERT INTO documents VALUES (
            'doc-1', 'doc.pdf', 'pdf', 'raw/doc.pdf', 'text/doc.txt',
            '2026-08-07T00:00:00Z', 'Titel', 'abc'
        );
        INSERT INTO source_segments VALUES (
            'seg-1', 'doc-1', 2, 'A', 1, 'Ein Rettungsweg.', '2026-08-07T00:01:00Z'
        );
        INSERT INTO extraction_runs VALUES (
            'run-1', 'doc-1', '2026-08-07T00:02:00Z', NULL,
            'manual', NULL, 'prompt', 'completed'
        );
        INSERT INTO extracted_records VALUES (
            'rec-1', 'fire_safety_requirement', '{"text":"Ein Rettungsweg."}',
            'doc-1', 'seg-1', 2, 'A', 0.9, 'valid', 'run-1',
            '2026-08-07T00:03:00Z'
        );
        INSERT INTO source_segments_fts(source_segments_fts) VALUES ('rebuild');
        """
    )
    connection.close()


def _create_v1_database(db_path: Path) -> None:
    connection = sqlite3.connect(db_path)
    connection.executescript(
        """
        CREATE TABLE document_nodes (
            document_node_pk INTEGER PRIMARY KEY,
            document_node_id TEXT NOT NULL UNIQUE,
            document_version_pk INTEGER NOT NULL,
            parent_document_node_pk INTEGER,
            node_path TEXT NOT NULL,
            node_rank INTEGER NOT NULL,
            heading TEXT,
            locator TEXT,
            page_start INTEGER,
            page_end INTEGER,
            char_start INTEGER,
            char_end INTEGER,
            raw_extracted_text TEXT NOT NULL,
            source_text TEXT NOT NULL,
            search_text TEXT NOT NULL,
            is_citable INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );
        CREATE VIRTUAL TABLE document_nodes_fts USING fts5(
            heading,
            source_text,
            search_text,
            content = 'document_nodes',
            content_rowid = 'document_node_pk'
        );
        CREATE TABLE schema_migration_audit (
            schema_migration_audit_pk INTEGER PRIMARY KEY,
            migration_id TEXT NOT NULL UNIQUE,
            from_user_version INTEGER NOT NULL,
            to_user_version INTEGER NOT NULL,
            database_sha256_before TEXT,
            legacy_schema_json TEXT,
            legacy_table_counts_json TEXT,
            migrated_at TEXT NOT NULL
        );
        INSERT INTO document_nodes VALUES
            (1, 'root', 1, NULL, '/', 1, 'Root', NULL, NULL, NULL, NULL, NULL, 'Root', 'Root', 'root', 0, '2026-08-07T00:00:00Z'),
            (2, 'section', 1, 1, '/1', 2, 'Section', 'section=1', NULL, NULL, NULL, NULL, 'Section', 'Section', 'section', 0, '2026-08-07T00:00:00Z'),
            (3, 'page', 1, 2, '/1/p1', 3, 'Section', 'page=1;section=1', 1, 1, NULL, NULL, 'Text', 'Text', 'text', 1, '2026-08-07T00:00:00Z'),
            (4, 'empty', 1, 2, '/1/p2', 4, 'Section', 'page=2;section=1', 2, 2, NULL, NULL, '', '', '', 0, '2026-08-07T00:00:00Z'),
            (5, 'caption', 1, 2, '/1/p3/excluded1', 5, 'Caption', 'page=3;section=1;excluded=1', 3, 3, NULL, NULL, 'Caption', 'Caption', 'caption', 0, '2026-08-07T00:00:00Z');
        INSERT INTO document_nodes_fts(document_nodes_fts) VALUES ('rebuild');
        PRAGMA user_version = 1;
        """
    )
    connection.close()


def _insert_document(connection: sqlite3.Connection) -> int:
    cursor = connection.execute(
        """
        INSERT INTO projects (project_id, name, created_at)
        VALUES ('project-1', 'Projekt 1', '2026-08-07T00:00:00Z')
        ON CONFLICT(project_id) DO NOTHING
        """
    )
    del cursor
    return connection.execute(
        """
        INSERT INTO documents (
            document_id,
            project_pk,
            filename,
            file_type,
            source_path,
            imported_at
        )
        VALUES (
            'doc-schema', 1, 'schema.pdf', 'pdf', 'raw/schema.pdf',
            '2026-08-07T00:00:00Z'
        )
        ON CONFLICT(document_id) DO UPDATE SET filename = excluded.filename
        RETURNING document_pk
        """
    ).fetchone()[0]


def _insert_document_version(
    connection: sqlite3.Connection,
    document_pk: int,
    document_version_id: str,
    *,
    is_current: int,
) -> int:
    return connection.execute(
        """
        INSERT INTO document_versions (
            document_version_id,
            document_pk,
            version_label,
            content_hash,
            is_current,
            created_at
        )
        VALUES (?, ?, 'v1', ?, ?, '2026-08-07T00:00:00Z')
        RETURNING document_version_pk
        """,
        (document_version_id, document_pk, document_version_id, is_current),
    ).fetchone()[0]


def _insert_document_node(
    connection: sqlite3.Connection,
    *,
    raw_text: str = "raw",
    source_text: str = "source",
    search_text: str = "search",
) -> int:
    document_pk = _insert_document(connection)
    document_version_pk = _insert_document_version(
        connection, document_pk, f"version-node-{raw_text}", is_current=0
    )
    return connection.execute(
        """
        INSERT INTO document_nodes (
            document_node_id,
            document_version_pk,
            node_path,
            node_rank,
            page_start,
            page_end,
            char_start,
            char_end,
            raw_extracted_text,
            source_text,
            search_text,
            is_citable,
            created_at
        )
        VALUES (
            ?, ?, '/1', 1, 1, 1, 0, 10,
            ?, ?, ?, 1, '2026-08-07T00:00:00Z'
        )
        RETURNING document_node_pk
        """,
        (f"node-{raw_text}", document_version_pk, raw_text, source_text, search_text),
    ).fetchone()[0]


def _insert_statement(
    connection: sqlite3.Connection,
    statement_id: str,
    statement_type: str = "requirement",
) -> int:
    return connection.execute(
        """
        INSERT INTO statements (
            statement_id,
            statement_type,
            text,
            binding_status,
            lifecycle_status,
            review_status,
            creation_method,
            created_at
        )
        VALUES (?, ?, 'Aussage', 'unknown', 'active', 'needs_review', 'manual', '2026-08-07T00:00:00Z')
        RETURNING statement_pk
        """,
        (statement_id, statement_type),
    ).fetchone()[0]


def _insert_statement_source(
    connection: sqlite3.Connection,
    statement_pk: int,
    document_node_pk: int,
) -> int:
    return connection.execute(
        """
        INSERT INTO statement_sources (
            statement_source_id,
            statement_pk,
            document_node_pk,
            source_role,
            created_at
        )
        VALUES ('source-1', ?, ?, 'primary', '2026-08-07T00:00:00Z')
        RETURNING statement_source_pk
        """,
        (statement_pk, document_node_pk),
    ).fetchone()[0]


def _fts_count(connection: sqlite3.Connection, term: str) -> int:
    return connection.execute(
        "SELECT COUNT(*) FROM document_nodes_fts WHERE document_nodes_fts MATCH ?",
        (term,),
    ).fetchone()[0]
