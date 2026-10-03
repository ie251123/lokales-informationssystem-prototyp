from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from info_system.construction.statement_candidate_batch_pipeline_v3 import (
    write_statement_candidate_batch_v3_package,
)
from info_system.construction.statement_candidate_import_v3 import (
    StatementCandidateBatchV3Import,
    StatementCandidateImportV3Error,
    import_statement_candidate_batches_v3,
    import_statement_candidate_batches_v3_atomic,
)
from info_system.db import initialize_database


def test_imports_v3_candidate_losslessly_and_resolves_relation(tmp_path: Path) -> None:
    db_path, batches = _import_fixture(tmp_path)

    result = import_statement_candidate_batches_v3_atomic(db_path, batches=batches)

    with sqlite3.connect(db_path) as connection:
        statement = connection.execute(
            """
            SELECT statement_type, text, binding_status, lifecycle_status,
                   review_status, action_state, creation_method
            FROM statements
            ORDER BY statement_pk
            LIMIT 1
            """
        ).fetchone()
        archived = connection.execute(
            """
            SELECT candidate_json, evidence_source_text, char_start, char_end,
                   source_role, statement_subtype, output_constraint,
                   review_notes_json, validation_warnings_json
            FROM statement_candidates_v3
            ORDER BY statement_candidate_v3_pk
            LIMIT 1
            """
        ).fetchone()
        relation = connection.execute(
            """
            SELECT source.candidate_id, target.candidate_id, relations.relation_type
            FROM statement_relations_v3 AS relations
            JOIN statement_candidates_v3 AS source
              ON source.statement_candidate_v3_pk = relations.source_candidate_v3_pk
            JOIN statement_candidates_v3 AS target
              ON target.statement_candidate_v3_pk = relations.target_candidate_v3_pk
            """
        ).fetchone()
        normalized_relation_count = connection.execute(
            "SELECT COUNT(*) FROM statement_relations"
        ).fetchone()[0]
        document_reference_count = connection.execute(
            "SELECT COUNT(*) FROM statement_candidate_document_references_v3"
        ).fetchone()[0]
        batch_archive = connection.execute(
            """
            SELECT package_version, contract_version, prompt_version, batch_id,
                   batch_manifest_sha256, input_sha256, prompt_sha256,
                   batch_sha256, raw_output_sha256, binding_json,
                   node_processing_json, raw_output_json
            FROM statement_candidate_batches_v3
            """
        ).fetchone()

    candidate = batches[0].candidate_package["candidates"][0]
    assert result.candidate_count == 2
    assert result.relation_count == 1
    assert statement == (
        "finding",
        "Synthetic finding one.",
        "unknown",
        "active",
        "needs_review",
        "documented",
        "model_candidate",
    )
    assert json.loads(archived[0]) == candidate
    assert archived[1:] == (
        candidate["evidence"]["source_text"],
        0,
        len(candidate["evidence"]["source_text"]),
        "primary",
        "technical_value",
        "Keep unchanged.",
        '["Manual review required."]',
        '["Synthetic warning."]',
    )
    assert relation == (
        "batch_001:candidate_001",
        "batch_001:candidate_002",
        "derived_from",
    )
    assert normalized_relation_count == 1
    assert document_reference_count == 1
    binding = batches[0].candidate_package["batch_binding"]
    assert batch_archive[:4] == (
        "statement_candidates_batch_v3",
        "statement_candidates_batch_v3",
        "statement_candidates_frontier_v3",
        "batch_001",
    )
    assert batch_archive[4:8] == (
        binding["batch_manifest_sha256"],
        binding["input_sha256"],
        binding["prompt_sha256"],
        binding["batch_sha256"],
    )
    assert batch_archive[8] == hashlib.sha256(
        batches[0].raw_output_json.encode("utf-8")
    ).hexdigest()
    assert json.loads(batch_archive[9]) == binding
    assert json.loads(batch_archive[10]) == batches[0].candidate_package["node_processing"]
    assert batch_archive[11] == batches[0].raw_output_json


@pytest.mark.parametrize(
    "mutation",
    [
        lambda package: package.update(contract_version="statement_candidates_v1"),
        lambda package: package["batch_binding"].update(batch_sha256="0" * 64),
    ],
)
def test_rejects_wrong_contract_or_batch_binding_without_writes(
    tmp_path: Path, mutation
) -> None:
    db_path, batches = _import_fixture(tmp_path)
    changed = copy.deepcopy(batches[0].candidate_package)
    mutation(changed)
    invalid = _replace_package(batches[0], changed)

    with pytest.raises(Exception):
        import_statement_candidate_batches_v3_atomic(db_path, batches=[invalid])

    assert _candidate_counts(db_path) == (0, 0, 0)


def test_rejects_changed_handoff_file_without_writes(tmp_path: Path) -> None:
    db_path, batches = _import_fixture(tmp_path)
    prompt_path = batches[0].package_dir / "prompt.md"
    prompt_path.write_text(prompt_path.read_text(encoding="utf-8") + "changed", encoding="utf-8")

    with pytest.raises(Exception):
        import_statement_candidate_batches_v3_atomic(db_path, batches=batches)

    assert _candidate_counts(db_path) == (0, 0, 0)


def test_rolls_back_earlier_batch_when_later_batch_conflicts(tmp_path: Path) -> None:
    db_path, batches = _import_fixture(tmp_path, node_count=2, max_target_nodes=1)
    import_statement_candidate_batches_v3_atomic(db_path, batches=[batches[1]])
    with sqlite3.connect(db_path) as connection:
        statement_pk = connection.execute(
            """
            SELECT statements.statement_pk
            FROM statements
            JOIN statement_candidates_v3 USING (statement_pk)
            WHERE candidate_id = 'batch_002:candidate_001'
            """
        ).fetchone()[0]
        connection.execute(
            "UPDATE statements SET text = 'tampered normalized text' WHERE statement_pk = ?",
            (statement_pk,),
        )

    with pytest.raises(StatementCandidateImportV3Error, match="conflict"):
        import_statement_candidate_batches_v3_atomic(db_path, batches=batches)

    with sqlite3.connect(db_path) as connection:
        batch_ids = connection.execute(
            "SELECT batch_id FROM statement_candidate_batches_v3 ORDER BY batch_id"
        ).fetchall()
        text = connection.execute(
            "SELECT text FROM statements WHERE statement_pk = ?", (statement_pk,)
        ).fetchone()[0]
    assert batch_ids == [("batch_002",)]
    assert text == "tampered normalized text"


def test_identical_repeat_is_idempotent_but_changed_candidate_conflicts(
    tmp_path: Path,
) -> None:
    db_path, batches = _import_fixture(tmp_path)
    first = import_statement_candidate_batches_v3_atomic(db_path, batches=batches)
    before = _all_table_counts(db_path)

    second = import_statement_candidate_batches_v3_atomic(db_path, batches=batches)

    assert _all_table_counts(db_path) == before
    assert first.candidate_count == second.candidate_count == 2
    assert second.unchanged_batch_count == 1

    changed = copy.deepcopy(batches[0].candidate_package)
    changed["candidates"][0]["normalized_text"] = "Changed but schema-valid text."
    conflict = _replace_package(batches[0], changed)
    with pytest.raises(StatementCandidateImportV3Error, match="conflict"):
        import_statement_candidate_batches_v3_atomic(db_path, batches=[conflict])
    assert _all_table_counts(db_path) == before


def test_caller_controlled_transaction_can_be_rolled_back(tmp_path: Path) -> None:
    db_path, batches = _import_fixture(tmp_path)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("BEGIN IMMEDIATE")
    try:
        result = import_statement_candidate_batches_v3(connection, batches=batches)
        assert result.candidate_count == 2
        connection.execute("ROLLBACK")
    finally:
        connection.close()

    assert _candidate_counts(db_path) == (0, 0, 0)


def test_import_target_does_not_change_separate_original_database(tmp_path: Path) -> None:
    original_path, _ = _import_fixture(tmp_path / "original")
    target_path, batches = _import_fixture(tmp_path / "target")
    original_hash = _sha256_file(original_path)

    import_statement_candidate_batches_v3_atomic(target_path, batches=batches)

    assert _sha256_file(original_path) == original_hash
    assert _candidate_counts(original_path) == (0, 0, 0)
    assert _candidate_counts(target_path) == (2, 1, 2)


def _import_fixture(
    tmp_path: Path,
    *,
    node_count: int = 1,
    max_target_nodes: int = 8,
) -> tuple[Path, list[StatementCandidateBatchV3Import]]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    document_export = _document_export(node_count)
    db_path = initialize_database(tmp_path / "prototype.sqlite")
    _seed_document_export(db_path, document_export)
    package_root = tmp_path / "packages"
    batches = []
    for batch_number in range(1, node_count + 1 if max_target_nodes == 1 else 2):
        result = write_statement_candidate_batch_v3_package(
            document_export,
            output_dir=package_root,
            model_name="frontier-test-model",
            batch_number=batch_number,
            max_target_nodes=max_target_nodes,
        )
        manifest = _read_json(result.manifest_path)
        package = _valid_response(package_root, manifest, batch_number=batch_number)
        batches.append(
            StatementCandidateBatchV3Import(
                candidate_package=package,
                manifest=manifest,
                package_dir=package_root / f"batch_{batch_number:03d}",
                raw_output_json=_canonical_json(package),
            )
        )
    return db_path, batches


def _valid_response(
    package_root: Path, manifest: dict[str, Any], *, batch_number: int
) -> dict[str, Any]:
    batch = manifest["batches"][batch_number - 1]
    package_dir = package_root / batch["batch_id"]
    binding = _read_json(package_dir / "request_payload.json")["metadata"][
        "response_binding"
    ]
    input_package = _read_json(package_dir / "input_package.json")
    target_id = batch["target_node_ids"][0]
    source_text = next(
        node["source_text"]
        for node in input_package["source_nodes"]
        if node["document_node_id"] == target_id
    )
    first_id = f"{batch['batch_id']}:candidate_001"
    second_id = f"{batch['batch_id']}:candidate_002"
    candidates = [
        _candidate(
            first_id,
            target_id,
            source_text,
            normalized_text="Synthetic finding one.",
            rich=True,
        ),
        _candidate(
            second_id,
            target_id,
            source_text,
            normalized_text="Synthetic finding two.",
            rich=False,
        ),
    ]
    return {
        "package_version": "statement_candidates_batch_v3",
        "contract_version": "statement_candidates_batch_v3",
        "batch_binding": binding,
        "node_processing": [
            {
                "document_node_id": node_id,
                "status": (
                    "processed_with_candidates"
                    if node_id == target_id
                    else "processed_without_candidate"
                ),
                "candidate_ids": [first_id, second_id] if node_id == target_id else [],
            }
            for node_id in batch["target_node_ids"]
        ],
        "candidates": candidates,
        "relations": [
            {
                "relation_id": f"{batch['batch_id']}:relation_001",
                "source_candidate_id": first_id,
                "target_candidate_id": second_id,
                "relation_type": "derived_from",
            }
        ],
    }


def _candidate(
    candidate_id: str,
    node_id: str,
    source_text: str,
    *,
    normalized_text: str,
    rich: bool,
) -> dict[str, Any]:
    return {
        "candidate_id": candidate_id,
        "document_node_id": node_id,
        "evidence": {
            "source_text": source_text,
            "char_start": 0,
            "char_end": len(source_text),
            "source_role": "primary",
        },
        "statement_type": "finding",
        "statement_subtype": "technical_value",
        "normalized_text": normalized_text,
        "binding_status": "unknown",
        "lifecycle_status": "active",
        "review_status": "needs_review",
        "action_state": "documented",
        "output_constraint": "Keep unchanged." if rich else None,
        "objects": (
            [
                {
                    "object_id": f"{candidate_id}:object_001",
                    "entity_type": "building_component",
                    "canonical_label": "Synthetic component",
                    "aliases": ["Component alias"],
                    "role": "subject",
                }
            ]
            if rich
            else []
        ),
        "values": (
            [
                {
                    "value_name": "height",
                    "raw_value": "2 m",
                    "text_value": None,
                    "numeric_value": 2,
                    "unit": "m",
                    "comparison_operator": "equals",
                    "is_approximate": False,
                    "spatial_reference": "test reference",
                    "value_order": 1,
                }
            ]
            if rich
            else []
        ),
        "conditions": (
            [{"condition_text": "Synthetic condition", "parameters": {"key": "value"}}]
            if rich
            else []
        ),
        "classifications": (
            [
                {
                    "scheme": "synthetic",
                    "code": "S-1",
                    "label": "Synthetic",
                    "role": "topic",
                }
            ]
            if rich
            else []
        ),
        "groups": [],
        "relations": ([{"note": "Candidate-local relation note."}] if rich else []),
        "document_references": (
            [{"reference_text": "Synthetic norm", "reference_type": "normative"}]
            if rich
            else []
        ),
        "review_notes": (["Manual review required."] if rich else []),
        "validation_warnings": (["Synthetic warning."] if rich else []),
    }


def _seed_document_export(db_path: Path, package: dict[str, Any]) -> None:
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO projects (project_id, name, created_at) VALUES (?, ?, ?)",
            (package["project_id"], "Test project", "2026-01-01T00:00:00Z"),
        )
        project_pk = connection.execute("SELECT project_pk FROM projects").fetchone()[0]
        connection.execute(
            """
            INSERT INTO documents (
                document_id, project_pk, filename, file_type, source_path,
                imported_at, document_hash
            ) VALUES (?, ?, 'test.pdf', 'pdf', 'test.pdf', ?, ?)
            """,
            (
                package["document_id"],
                project_pk,
                "2026-01-01T00:00:00Z",
                package["document_sha256"],
            ),
        )
        document_pk = connection.execute("SELECT document_pk FROM documents").fetchone()[0]
        connection.execute(
            """
            INSERT INTO document_versions (
                document_version_id, document_pk, content_hash, is_current, created_at
            ) VALUES (?, ?, ?, 1, ?)
            """,
            (
                package["document_version_id"],
                document_pk,
                package["document_sha256"],
                "2026-01-01T00:00:00Z",
            ),
        )
        version_pk = connection.execute(
            "SELECT document_version_pk FROM document_versions"
        ).fetchone()[0]
        for node in package["nodes"]:
            connection.execute(
                """
                INSERT INTO document_nodes (
                    document_node_id, document_version_pk, node_path, node_rank,
                    element_type, heading, locator, page_start, page_end,
                    raw_extracted_text, source_text, search_text, is_citable, created_at
                ) VALUES (?, ?, ?, ?, 'page_text', ?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    node["node_id"],
                    version_pk,
                    node["node_path"],
                    node["rank"],
                    node["heading"],
                    node["locator"],
                    node["page_start"],
                    node["page_end"],
                    node["source_text"],
                    node["source_text"],
                    node["source_text"],
                    "2026-01-01T00:00:00Z",
                ),
            )


def _document_export(count: int) -> dict[str, Any]:
    nodes = []
    for index in range(1, count + 1):
        node = {
            "node_id": f"node-{index:03d}",
            "node_path": f"/sections/A/pages/{index}",
            "parent_node_id": "section-A",
            "rank": index,
            "element_type": "page_text",
            "page_start": index,
            "page_end": index,
            "section_number": "A",
            "heading": "Section A",
            "locator": f"page={index};section=A",
            "is_citable": True,
            "source_text": f"A-{index}",
        }
        node["content_sha256"] = _sha_json(node)
        nodes.append(node)
    package = {
        "package_version": "document_nodes_export_v1",
        "project_id": "project-test",
        "document_id": "document-test",
        "document_version_id": "document-test:version-1",
        "document_sha256": "a" * 64,
        "profile_id": "profile-test-v1",
        "text_repair_rules_version": "source_text_repair_v1",
        "search_normalization_rules_version": "search_text_normalization_v1",
        "nodes": nodes,
    }
    package["package_sha256"] = _sha_json(package)
    return package


def _replace_package(
    batch: StatementCandidateBatchV3Import, package: dict[str, Any]
) -> StatementCandidateBatchV3Import:
    return StatementCandidateBatchV3Import(
        candidate_package=package,
        manifest=batch.manifest,
        package_dir=batch.package_dir,
        raw_output_json=_canonical_json(package),
    )


def _candidate_counts(db_path: Path) -> tuple[int, int, int]:
    with sqlite3.connect(db_path) as connection:
        return tuple(
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "statement_candidates_v3",
                "statement_relations_v3",
                "statements",
            )
        )


def _all_table_counts(db_path: Path) -> dict[str, int]:
    with sqlite3.connect(db_path) as connection:
        tables = [
            row[0]
            for row in connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            )
        ]
        return {
            table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in tables
        }


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
