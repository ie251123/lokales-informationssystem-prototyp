from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

import info_system.construction.review_writeback as review_writeback_module
from info_system.construction.review_writeback import (
    ReviewWritebackError,
    apply_review_writeback,
    apply_schema4_review_correction,
    protected_v3_fingerprints,
    schema4_review_correction_fingerprints,
    schema4_review_correction_plan_sha256,
)
from info_system.db import initialize_database
from info_system.operation.clean_set_retrieval import (
    SearchIntent,
    find_evidence,
)


def test_applies_review_overlays_atomically_and_exposes_clean_set(tmp_path: Path) -> None:
    db_path = _seed_v3_database(tmp_path / "review.sqlite")
    candidate_plan, relation_plan = _plans()
    before_fingerprints = _fingerprints(db_path)

    result = apply_review_writeback(
        db_path,
        candidate_plan=candidate_plan,
        relation_plan=relation_plan,
        relation_catalog_path=_relation_catalog(tmp_path),
        candidate_review_source="candidate_review_completed.csv",
        candidate_review_version="sha256:candidate-review",
        relation_review_source="relation_review_normalized.csv",
        relation_review_version="sha256:relation-review",
        relation_reviewed_by="reviewer_alpha",
        relation_reviewed_at="2026-08-10",
    )

    with sqlite3.connect(db_path) as connection:
        statuses = dict(
            connection.execute(
                """
                SELECT review_status, COUNT(*)
                FROM candidate_review_overlays
                GROUP BY review_status
                """
            )
        )
        clean_candidates = connection.execute(
            "SELECT candidate_id FROM clean_reviewed_candidates ORDER BY candidate_id"
        ).fetchall()
        passages = connection.execute(
            """
            SELECT passage_order, exact_text
            FROM clean_reviewed_candidate_evidence
            WHERE candidate_id = 'batch_001:candidate_001'
            ORDER BY passage_order
            """
        ).fetchall()
        clean_relations = connection.execute(
            "SELECT relation_id FROM clean_reviewed_relations"
        ).fetchall()
        dependency_count = connection.execute(
            "SELECT COUNT(*) FROM relation_review_candidate_dependencies"
        ).fetchone()[0]
        raw_statement_statuses = connection.execute(
            "SELECT DISTINCT review_status FROM statements"
        ).fetchall()
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]

    assert statuses == {"confirmed": 2, "needs_review": 1, "rejected": 1}
    assert clean_candidates == [
        ("batch_001:candidate_001",),
        ("batch_001:candidate_002",),
    ]
    assert passages == [(1, "Alpha evidence."), (2, "Omega evidence.")]
    assert "Alpha evidence. Omega evidence." not in {row[1] for row in passages}
    assert clean_relations == [("batch_001:relation_001",)]
    assert dependency_count == 1
    assert raw_statement_statuses == [("needs_review",)]
    assert user_version == 4
    assert _fingerprints(db_path) == before_fingerprints
    assert result["protected_v3_fingerprints_unchanged"] is True
    assert result["clean_set_counts"] == {"candidates": 2, "relations": 1}
    assert Path(result["backup_path"]).read_bytes() != db_path.read_bytes()
    assert result["backup_sha256"] == result["database_sha256_before"]


def test_rolls_back_migration_and_overlays_when_evidence_is_not_exact(
    tmp_path: Path,
) -> None:
    db_path = _seed_v3_database(tmp_path / "invalid.sqlite")
    candidate_plan, relation_plan = _plans()
    invalid_plan = copy.deepcopy(candidate_plan)
    invalid_plan["records"][0]["evidence_passages"][0]["source_text"] = "Invented"
    before_hash = hashlib.sha256(db_path.read_bytes()).hexdigest()
    before_fingerprints = _fingerprints(db_path)

    with pytest.raises(ReviewWritebackError, match="exact source_text passage"):
        apply_review_writeback(
            db_path,
            candidate_plan=invalid_plan,
            relation_plan=relation_plan,
            relation_catalog_path=_relation_catalog(tmp_path),
            candidate_review_source="candidate_review_completed.csv",
            candidate_review_version="sha256:candidate-review",
            relation_review_source="relation_review_normalized.csv",
            relation_review_version="sha256:relation-review",
            relation_reviewed_by="reviewer_alpha",
            relation_reviewed_at="2026-08-10",
        )

    with sqlite3.connect(db_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name = 'candidate_review_overlays'"
        ).fetchone()[0] == 0
    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == before_hash
    assert _fingerprints(db_path) == before_fingerprints
    assert list(tmp_path.glob("invalid.sqlite.v3.*.bak"))


def test_schema4_review_correction_preserves_history_and_reopens_retrieval(
    tmp_path: Path,
) -> None:
    db_path = _seed_schema4_review_database(tmp_path / "review-v4.sqlite")
    plan_path = _schema4_correction_plan(db_path, tmp_path / "correction.json")
    before_fingerprints = _fingerprints(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        old_before = dict(
            connection.execute(
                "SELECT * FROM candidate_review_overlays WHERE candidate_review_pk = 3"
            ).fetchone()
        )
        old_passages = connection.execute(
            """
            SELECT passage_order, document_pk, document_node_pk, locator,
                   exact_text, char_start_in_node, char_end_in_node, source_role,
                   source_candidate_v3_pk
            FROM candidate_review_evidence_passages
            WHERE candidate_review_pk = 3
            ORDER BY passage_order
            """
        ).fetchall()
        relation_before = connection.execute(
            "SELECT * FROM relation_review_overlays ORDER BY relation_review_pk"
        ).fetchall()

    before = find_evidence(
        db_path,
        SearchIntent.from_dict(
            {"terms": ["Model three"], "retrieval_path": "fts5", "max_results": 10}
        ),
    )
    assert before.evidence == []

    result = apply_schema4_review_correction(db_path, correction_plan_path=plan_path)

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        reviews = connection.execute(
            """
            SELECT * FROM candidate_review_overlays
            WHERE statement_candidate_v3_pk = 3
            ORDER BY candidate_review_pk
            """
        ).fetchall()
        old_after = dict(reviews[0])
        new_review = dict(reviews[1])
        new_passages = connection.execute(
            """
            SELECT passage_order, document_pk, document_node_pk, locator,
                   exact_text, char_start_in_node, char_end_in_node, source_role,
                   source_candidate_v3_pk
            FROM candidate_review_evidence_passages
            WHERE candidate_review_pk = ?
            ORDER BY passage_order
            """,
            (new_review["candidate_review_pk"],),
        ).fetchall()
        current_count = connection.execute(
            """
            SELECT COUNT(*) FROM candidate_review_overlays
            WHERE statement_candidate_v3_pk = 3 AND is_current = 1
            """
        ).fetchone()[0]
        relation_after = connection.execute(
            "SELECT * FROM relation_review_overlays ORDER BY relation_review_pk"
        ).fetchall()
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    assert {**old_after, "is_current": 1} == old_before
    assert new_review["review_decision"] == "nicht_entschieden"
    assert new_review["review_status"] == "needs_review"
    assert new_review["is_current"] == 1
    assert new_review["review_source"] == str(plan_path)
    assert new_review["review_version"] == f"sha256:{json.loads(plan_path.read_text(encoding='utf-8'))['plan_sha256']}"
    assert new_passages == old_passages
    assert current_count == 1
    assert relation_after == relation_before
    assert _fingerprints(db_path) == before_fingerprints
    assert result["protected_v3_fingerprints_unchanged"] is True
    assert result["relation_review_overlays_unchanged"] is True
    assert hashlib.sha256(Path(result["backup_path"]).read_bytes()).hexdigest() == result[
        "database_sha256_before"
    ]

    reopened = find_evidence(
        db_path,
        SearchIntent.from_dict(
            {"terms": ["Model three"], "retrieval_path": "fts5", "max_results": 10}
        ),
    )
    still_rejected = find_evidence(
        db_path,
        SearchIntent.from_dict(
            {"terms": ["Model four"], "retrieval_path": "fts5", "max_results": 10}
        ),
    )
    assert [(item.candidate_id, item.review_status, item.value_origin) for item in reopened.evidence] == [
        ("batch_001:candidate_003", "needs_review", "raw_candidate")
    ]
    assert still_rejected.evidence == []


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda plan: plan.__setitem__("reason", ""), "reason"),
        (
            lambda plan: plan.__setitem__(
                "candidate_id", "batch_999:candidate_missing"
            ),
            "candidate_id must resolve exactly once",
        ),
        (
            lambda plan: plan["expected_current_review"].__setitem__(
                "review_status", "needs_review"
            ),
            "expected current review",
        ),
    ],
)
def test_schema4_review_correction_rejects_invalid_or_stale_plan(
    tmp_path: Path, mutate, message: str
) -> None:
    db_path = _seed_schema4_review_database(tmp_path / "review-v4.sqlite")
    plan_path = _schema4_correction_plan(db_path, tmp_path / "correction.json")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    mutate(plan)
    plan["plan_sha256"] = schema4_review_correction_plan_sha256(plan)
    plan_path.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    with pytest.raises(ReviewWritebackError, match=message):
        apply_schema4_review_correction(db_path, correction_plan_path=plan_path)

    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM candidate_review_overlays WHERE statement_candidate_v3_pk = 3"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT is_current FROM candidate_review_overlays WHERE candidate_review_pk = 3"
        ).fetchone()[0] == 1


def test_schema4_review_correction_rejects_bad_hash_wrong_schema_and_duplicate(
    tmp_path: Path,
) -> None:
    db_path = _seed_schema4_review_database(tmp_path / "review-v4.sqlite")
    plan_path = _schema4_correction_plan(db_path, tmp_path / "correction.json")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["reason"] += " verändert"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ReviewWritebackError, match="plan hash"):
        apply_schema4_review_correction(db_path, correction_plan_path=plan_path)

    plan_path = _schema4_correction_plan(db_path, tmp_path / "correction.json")
    v3_path = _seed_v3_database(tmp_path / "review-v3.sqlite")
    with pytest.raises(ReviewWritebackError, match="schema version 4"):
        apply_schema4_review_correction(v3_path, correction_plan_path=plan_path)

    apply_schema4_review_correction(db_path, correction_plan_path=plan_path)
    with pytest.raises(ReviewWritebackError, match="database fingerprint|expected current review"):
        apply_schema4_review_correction(db_path, correction_plan_path=plan_path)


def test_schema4_review_correction_rolls_back_insert_and_evidence_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    duplicate_db = _seed_schema4_review_database(tmp_path / "duplicate.sqlite")
    duplicate_plan_path = _schema4_correction_plan(
        duplicate_db, tmp_path / "duplicate-correction.json"
    )
    duplicate_plan = json.loads(duplicate_plan_path.read_text(encoding="utf-8"))
    duplicate_plan["target_review"]["candidate_review_id"] = (
        "candidate-review-v1:batch_001:candidate_003"
    )
    duplicate_plan["plan_sha256"] = schema4_review_correction_plan_sha256(
        duplicate_plan
    )
    duplicate_plan_path.write_text(
        json.dumps(duplicate_plan, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ReviewWritebackError):
        apply_schema4_review_correction(
            duplicate_db, correction_plan_path=duplicate_plan_path
        )
    _assert_original_review_is_still_current(duplicate_db)

    evidence_db = _seed_schema4_review_database(tmp_path / "evidence.sqlite")
    with sqlite3.connect(evidence_db) as connection:
        connection.execute(
            "UPDATE document_nodes SET source_text = 'Changed source.' WHERE document_node_pk = 1"
        )
    evidence_plan_path = _schema4_correction_plan(
        evidence_db, tmp_path / "evidence-correction.json"
    )
    with pytest.raises(ReviewWritebackError, match="exact source_text passage"):
        apply_schema4_review_correction(
            evidence_db, correction_plan_path=evidence_plan_path
        )
    _assert_original_review_is_still_current(evidence_db)

    foreign_key_db = _seed_schema4_review_database(tmp_path / "foreign-key.sqlite")
    with sqlite3.connect(foreign_key_db) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(
            """
            INSERT INTO relation_review_candidate_dependencies (
                relation_review_pk, candidate_review_pk, dependency_order,
                endpoint_role, dependency_reason
            ) VALUES (999, 999, 1, 'source', 'synthetic broken dependency')
            """
        )
    foreign_key_plan_path = _schema4_correction_plan(
        foreign_key_db, tmp_path / "foreign-key-correction.json"
    )
    with pytest.raises(ReviewWritebackError, match="foreign_key_check"):
        apply_schema4_review_correction(
            foreign_key_db, correction_plan_path=foreign_key_plan_path
        )
    _assert_original_review_is_still_current(foreign_key_db)

    integrity_db = _seed_schema4_review_database(tmp_path / "integrity.sqlite")
    integrity_plan_path = _schema4_correction_plan(
        integrity_db, tmp_path / "integrity-correction.json"
    )
    original_connect = sqlite3.connect

    class IntegrityFailureConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if sql.strip() == "PRAGMA integrity_check" and self.in_transaction:
                return super().execute("SELECT 'synthetic integrity failure'")
            return super().execute(sql, parameters)

    def connect_with_integrity_failure(*args, **kwargs):
        return original_connect(*args, factory=IntegrityFailureConnection, **kwargs)

    monkeypatch.setattr(
        review_writeback_module.sqlite3,
        "connect",
        connect_with_integrity_failure,
    )
    with pytest.raises(ReviewWritebackError, match="integrity_check"):
        apply_schema4_review_correction(
            integrity_db, correction_plan_path=integrity_plan_path
        )
    _assert_original_review_is_still_current(integrity_db)


def _fingerprints(db_path: Path) -> dict[str, str]:
    with sqlite3.connect(db_path) as connection:
        return protected_v3_fingerprints(connection)


def _seed_schema4_review_database(db_path: Path) -> Path:
    _seed_v3_database(db_path)
    candidate_plan, relation_plan = _plans()
    fourth = candidate_plan["records"][3]
    fourth.update(
        {
            "disposition": "exclude_rejected",
            "raw_review_decision": "verwerfen",
            "target_review_status": "rejected",
            "target_reviewed_at": "2026-08-10",
            "target_reviewed_by": "reviewer_alpha",
        }
    )
    candidate_plan["disposition_counts"] = {
        "include_confirmed": 2,
        "exclude_rejected": 2,
    }
    apply_review_writeback(
        db_path,
        candidate_plan=candidate_plan,
        relation_plan=relation_plan,
        relation_catalog_path=_relation_catalog(db_path.parent),
        candidate_review_source="candidate_review_completed.csv",
        candidate_review_version="sha256:candidate-review",
        relation_review_source="relation_review_normalized.csv",
        relation_review_version="sha256:relation-review",
        relation_reviewed_by="reviewer_alpha",
        relation_reviewed_at="2026-08-10",
    )
    return db_path


def _schema4_correction_plan(db_path: Path, plan_path: Path) -> Path:
    plan = {
        "schema_version": "candidate_review_correction_plan_v1",
        "plan_version": "1.0",
        "correction_id": "test-reopen-candidate-003",
        "candidate_id": "batch_001:candidate_003",
        "expected_current_review": {
            "candidate_review_id": "candidate-review-v1:batch_001:candidate_003",
            "review_version": "sha256:candidate-review",
            "review_decision": "verwerfen",
            "review_status": "rejected",
            "is_current": 1,
        },
        "target_review": {
            "candidate_review_id": (
                "candidate-review-correction-v1:test-reopen-candidate-003"
            ),
            "review_decision": "nicht_entschieden",
            "review_status": "needs_review",
            "is_current": 1,
            "reviewer": "reviewer_alpha",
            "correction_date": "2026-08-14",
        },
        "supersedes_review": {
            "candidate_review_id": "candidate-review-v1:batch_001:candidate_003",
            "review_version": "sha256:candidate-review",
        },
        "reason": (
            "The earlier rejection was incorrect; the source-bound finding "
            "requires renewed professional review."
        ),
        "review_source": str(plan_path),
        "integrity": schema4_review_correction_fingerprints(
            db_path, "batch_001:candidate_003"
        ),
        "plan_sha256": "",
    }
    plan["plan_sha256"] = schema4_review_correction_plan_sha256(plan)
    plan_path.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return plan_path


def _assert_original_review_is_still_current(db_path: Path) -> None:
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            """
            SELECT review_decision, review_status, is_current
            FROM candidate_review_overlays WHERE candidate_review_pk = 3
            """
        ).fetchone() == ("verwerfen", "rejected", 1)
        assert connection.execute(
            "SELECT COUNT(*) FROM candidate_review_overlays WHERE statement_candidate_v3_pk = 3"
        ).fetchone()[0] == 1


def _seed_v3_database(db_path: Path) -> Path:
    initialize_database(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            "INSERT INTO projects (project_id, name, created_at) VALUES ('p', 'P', 'now')"
        )
        connection.execute(
            """
            INSERT INTO documents (
                document_id, project_pk, filename, file_type, source_path, imported_at
            ) VALUES ('doc', 1, 'doc.pdf', 'pdf', 'doc.pdf', 'now')
            """
        )
        connection.execute(
            """
            INSERT INTO document_versions (
                document_version_id, document_pk, content_hash, is_current, created_at
            ) VALUES ('doc:v1', 1, 'hash', 1, 'now')
            """
        )
        connection.execute(
            """
            INSERT INTO document_nodes (
                document_node_id, document_version_pk, node_path, node_rank,
                element_type, locator, page_start, page_end, raw_extracted_text,
                source_text, search_text, is_citable, created_at
            ) VALUES (
                'node-1', 1, '/p1', 1, 'page_text', 'page=1', 1, 1,
                'Alpha evidence. Middle. Omega evidence.',
                'Alpha evidence. Middle. Omega evidence.',
                'alpha evidence middle omega', 1, 'now'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO extraction_runs (
                extraction_run_id, document_id, started_at, method, status
            ) VALUES ('run', 'doc', 'now', 'model', 'completed')
            """
        )
        connection.execute(
            """
            INSERT INTO statement_candidate_batches_v3 (
                batch_import_id, extraction_run_pk, document_version_pk,
                package_version, contract_version, prompt_version, batch_id,
                batch_number, total_batches, model_name, batch_manifest_sha256,
                input_sha256, prompt_sha256, batch_sha256, raw_output_sha256,
                binding_json, node_processing_json, raw_output_json,
                candidate_count, relation_count, created_at
            ) VALUES (
                'batch-import', 1, 1, 'statement_candidates_batch_v3',
                'statement_candidates_batch_v3', 'statement_candidates_frontier_v3',
                'batch_001', 1, 1, 'model', ?, ?, ?, ?, ?, '{}', '[]', '{}', 4, 1, 'now'
            )
            """,
            tuple(letter * 64 for letter in "abcde"),
        )
        types = ("finding", "finding", "finding", "finding")
        texts = ("Model one", "Model two", "Model three", "Model four")
        for index, (statement_type, text) in enumerate(zip(types, texts), start=1):
            statement_pk = connection.execute(
                """
                INSERT INTO statements (
                    statement_id, statement_type, text, binding_status,
                    lifecycle_status, review_status, creation_method, created_at
                ) VALUES (?, ?, ?, 'unknown', 'active', 'needs_review', 'model_candidate', 'now')
                RETURNING statement_pk
                """,
                (f"statement-{index}", statement_type, text),
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO statement_sources (
                    statement_source_id, statement_pk, document_node_pk,
                    source_role, created_at
                ) VALUES (?, ?, 1, 'primary', 'now')
                """,
                (f"source-{index}", statement_pk),
            )
            candidate_id = f"batch_001:candidate_{index:03d}"
            connection.execute(
                """
                INSERT INTO statement_candidates_v3 (
                    statement_candidate_v3_id, statement_candidate_batch_v3_pk,
                    candidate_id, candidate_sha256, statement_pk, document_node_pk,
                    evidence_source_text, source_role, statement_subtype,
                    candidate_json, review_notes_json, validation_warnings_json, created_at
                ) VALUES (?, 1, ?, ?, ?, 1, 'Alpha evidence.', 'primary', 'test',
                          '{}', '[]', '[]', 'now')
                """,
                (f"candidate-v3-{index}", candidate_id, str(index) * 64, statement_pk),
            )
        relation_pk = connection.execute(
            """
            INSERT INTO statement_relations (
                statement_relation_id, source_statement_pk, target_statement_pk,
                relation_type, created_at
            ) VALUES ('relation-1', 1, 2, 'addresses', 'now')
            RETURNING statement_relation_pk
            """
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO statement_relations_v3 (
                statement_relation_v3_id, statement_candidate_batch_v3_pk,
                relation_id, relation_sha256, statement_relation_pk,
                source_candidate_v3_pk, target_candidate_v3_pk,
                relation_type, relation_json, created_at
            ) VALUES (
                'relation-v3-1', 1, 'batch_001:relation_001', ?, ?, 1, 2,
                'addresses', '{}', 'now'
            )
            """,
            ("f" * 64, relation_pk),
        )
        for view in (
            "clean_reviewed_relations",
            "clean_reviewed_candidate_evidence",
            "clean_reviewed_candidates",
        ):
            connection.execute(f"DROP VIEW {view}")
        for table in (
            "relation_review_candidate_dependencies",
            "relation_review_overlays",
            "candidate_review_evidence_passages",
            "candidate_review_overlays",
        ):
            connection.execute(f"DROP TABLE {table}")
        connection.execute("PRAGMA user_version = 3")
    return db_path


def _plans() -> tuple[dict, dict]:
    records = []
    statuses = ("confirmed", "confirmed", "rejected", "needs_review")
    decisions = ("korrigieren", "akzeptieren", "verwerfen", "unklar")
    dispositions = (
        "include_confirmed",
        "include_confirmed",
        "exclude_rejected",
        "exclude_unresolved",
    )
    reviewed_types = ("planning_measure", "requirement", "finding", "finding")
    for index in range(1, 5):
        candidate_id = f"batch_001:candidate_{index:03d}"
        passages = [
            {
                "document_node_id": "node-1",
                "source_candidate_id": candidate_id,
                "source_role": "primary",
                "source_text": "Alpha evidence.",
            }
        ]
        if index == 1:
            passages.append(
                {
                    "document_node_id": "node-1",
                    "source_candidate_id": candidate_id,
                    "source_role": "supporting",
                    "source_text": "Omega evidence.",
                }
            )
        records.append(
            {
                "candidate_id": candidate_id,
                "disposition": dispositions[index - 1],
                "evidence_passages": passages,
                "evidence_passages_changed": index == 1,
                "field_changes": {"statement_type": {"from": "finding", "to": reviewed_types[index - 1]}} if index == 1 else {},
                "raw_review_decision": decisions[index - 1],
                "target_binding_status": "unknown",
                "target_normalized_text": f"Reviewed {index}",
                "target_review_status": statuses[index - 1],
                "target_reviewed_at": None if index == 4 else "2026-08-10",
                "target_reviewed_by": None if index == 4 else "reviewer_alpha",
                "target_statement_type": reviewed_types[index - 1],
            }
        )
    return (
        {
            "plan_version": "test-candidate-plan",
            "records": records,
            "approved_clean_candidate_count": 2,
            "disposition_counts": {
                "include_confirmed": 2,
                "exclude_rejected": 1,
                "exclude_unresolved": 1,
            },
        },
        {
            "plan_version": "test-relation-plan",
            "professionally_accepted_count": 1,
            "active_clean_relation_count": 1,
            "records": [
                {
                    "acceptance_mode": "after_candidate_corrections",
                    "catalog_compatible_after_candidate_corrections": True,
                    "include_in_clean_relation_set": True,
                    "non_activation_reason": "",
                    "professional_review_decision": "akzeptieren",
                    "raw_review_code": "RELATION_DEPENDS_ON_CANDIDATE_CORRECTION: test",
                    "relation_id": "batch_001:relation_001",
                    "relation_type": "addresses",
                    "source_candidate_id": "batch_001:candidate_001",
                    "target_candidate_id": "batch_001:candidate_002",
                    "technical_activation_status": "activate_after_candidate_writeback",
                }
            ],
        },
    )


def _relation_catalog(tmp_path: Path) -> Path:
    path = tmp_path / "relation_catalog.yaml"
    path.write_text(
        """relations:
  - id: addresses
    allowed_source_statement_types:
      - planning_measure
    allowed_target_statement_types:
      - requirement
""",
        encoding="utf-8",
    )
    return path
