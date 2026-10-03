from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from info_system.construction.document_pipeline import (
    SourceExcerpt,
    export_document_nodes_package,
    import_document_nodes_from_pdf,
)
from info_system.construction.statement_candidate_batch_pipeline_v3 import (
    write_statement_candidate_batch_v3_package,
)
from info_system.construction.statement_candidate_import_v3 import (
    StatementCandidateBatchV3Import,
    import_statement_candidate_batches_v3_atomic,
)
from info_system.construction.text_extraction import ExtractedPage
from info_system.db import initialize_database
from info_system.operation.clean_set_retrieval import SearchIntent, find_evidence


@dataclass(frozen=True)
class SyntheticSource:
    document_id: str
    title: str
    filename: str
    profile_id: str
    locator: str
    passage: str
    normalized_statement: str
    statement_type: str
    binding_status: str
    action_state: str | None
    component_label: str
    raw_value: str | None = None


SYNTHETIC_SOURCES = (
    SyntheticSource(
        document_id="document_rule_alpha",
        title="Erfundene Regelquelle Alpha",
        filename="synthetic_rule_alpha.pdf",
        profile_id="synthetic_rule_alpha_profile_v1",
        locator="page=1;section=rule-alpha",
        passage="Die Prüfklappe Alpha bleibt während der Ruheprüfung geschlossen.",
        normalized_statement=(
            "Prüfklappe Alpha muss während der Ruheprüfung geschlossen bleiben."
        ),
        statement_type="requirement",
        binding_status="binding",
        action_state=None,
        component_label="Prüfklappe Alpha",
    ),
    SyntheticSource(
        document_id="document_plan_beta",
        title="Erfundene Planungsnotiz Beta",
        filename="synthetic_plan_beta.pdf",
        profile_id="synthetic_plan_beta_profile_v1",
        locator="page=1;section=plan-beta",
        passage=(
            "Im Mustergebäude Alpha ist eine automatische Schließung der "
            "Prüfklappe Alpha geplant."
        ),
        normalized_statement=(
            "Für Prüfklappe Alpha ist eine automatische Schließung geplant."
        ),
        statement_type="planning_measure",
        binding_status="unknown",
        action_state="planned",
        component_label="Prüfklappe Alpha",
    ),
    SyntheticSource(
        document_id="document_component_gamma",
        title="Erfundene Bauteilliste Gamma",
        filename="synthetic_component_gamma.pdf",
        profile_id="synthetic_component_gamma_profile_v1",
        locator="page=1;table=components;row=X-01",
        passage="Bauteil X-01 ist mit der vollständig erfundenen Kennung K-7 erfasst.",
        normalized_statement="Bauteil X-01 hat die Kennung K-7.",
        statement_type="finding",
        binding_status="unknown",
        action_state="documented",
        component_label="Bauteil X-01",
        raw_value="K-7",
    ),
)


def test_synthetic_three_source_import_is_idempotent_and_retrievable(
    tmp_path: Path,
) -> None:
    untouched_database = initialize_database(tmp_path / "untouched-source.sqlite")
    untouched_hash = _sha256_file(untouched_database)
    db_path = initialize_database(tmp_path / "synthetic-operational.sqlite")

    prepared: list[tuple[SyntheticSource, dict[str, Any], StatementCandidateBatchV3Import]] = []
    for index, source in enumerate(SYNTHETIC_SOURCES, start=1):
        first_result, document_export = _import_synthetic_document(
            tmp_path,
            db_path,
            source,
        )
        second_result, second_export = _import_synthetic_document(
            tmp_path,
            db_path,
            source,
        )
        assert first_result.document_version_id == second_result.document_version_id
        assert document_export == second_export
        prepared.append(
            (
                source,
                document_export,
                _candidate_batch(tmp_path, source, document_export, index=index),
            )
        )

    batches = [batch for _, _, batch in prepared]
    first_import = import_statement_candidate_batches_v3_atomic(
        db_path,
        batches=batches,
    )
    counts_after_first = _all_table_counts(db_path)
    snapshot_after_first = _source_snapshot(db_path)

    second_import = import_statement_candidate_batches_v3_atomic(
        db_path,
        batches=batches,
    )

    assert first_import.inserted_batch_count == 3
    assert second_import.unchanged_batch_count == 3
    assert _all_table_counts(db_path) == counts_after_first
    assert _source_snapshot(db_path) == snapshot_after_first
    assert _sha256_file(untouched_database) == untouched_hash

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT documents.document_id, versions.version_label, nodes.locator,
                   nodes.source_text, candidates.candidate_id,
                   statements.text, statements.review_status
            FROM documents
            JOIN document_versions AS versions USING (document_pk)
            JOIN document_nodes AS nodes USING (document_version_pk)
            JOIN statement_candidates_v3 AS candidates USING (document_node_pk)
            JOIN statements USING (statement_pk)
            ORDER BY documents.document_id
            """
        ).fetchall()
        component_value = connection.execute(
            """
            SELECT values_table.raw_value
            FROM statement_values AS values_table
            JOIN statements USING (statement_pk)
            JOIN statement_candidates_v3 USING (statement_pk)
            WHERE candidate_id = 'batch_001:candidate_syn_003'
            """
        ).fetchone()
        relation_count = connection.execute(
            "SELECT COUNT(*) FROM statement_relations"
        ).fetchone()[0]

    assert {
        row["document_id"]: (
            row["version_label"],
            row["locator"],
            row["source_text"],
            row["candidate_id"],
            row["text"],
            row["review_status"],
        )
        for row in rows
    } == {
        source.document_id: (
            source.profile_id,
            source.locator,
            source.passage,
            f"batch_001:candidate_syn_{index:03d}",
            source.normalized_statement,
            "needs_review",
        )
        for index, source in enumerate(SYNTHETIC_SOURCES, start=1)
    }
    assert component_value is not None and component_value["raw_value"] == "K-7"
    assert relation_count == 0

    package = find_evidence(
        db_path,
        SearchIntent.from_dict(
            {
                "terms": [
                    "Ruheprüfung geschlossen",
                    "automatische Schließung",
                    "Bauteil X-01",
                ],
                "retrieval_path": "fts5",
                "max_results": 20,
            }
        ),
    )
    by_document: dict[str, list[Any]] = {}
    for item in package.evidence:
        by_document.setdefault(item.document_id, []).append(item)

    assert set(by_document) == {source.document_id for source in SYNTHETIC_SOURCES}
    for source in SYNTHETIC_SOURCES:
        evidence = by_document[source.document_id][0]
        assert evidence.locator == source.locator
        assert evidence.document_version_label == source.profile_id
        assert evidence.review_status_label == "automatisch_extrahiert_ungeprüft"
        assert evidence.value_origin == "raw_candidate"
    assert package.assessment_boundary == (
        "retrieval_only; no compliance, conformity, legal, or fulfilment assessment"
    )


def _import_synthetic_document(
    tmp_path: Path,
    db_path: Path,
    source: SyntheticSource,
):
    pdf_path = tmp_path / source.filename
    pdf_path.write_bytes(f"%PDF synthetic {source.document_id}".encode("utf-8"))
    manifest = {
        "manifest_version": "document_import_manifest_v1",
        "project": {
            "project_id": "project_synthetic",
            "name": "Synthetisches Testprojekt",
        },
        "document": {
            "document_id": source.document_id,
            "title": source.title,
            "file_type": "pdf",
            "source_path": f"synthetic/{source.filename}",
        },
        "import": {
            "set_current": True,
            "allow_new_version_for_same_document": False,
        },
        "profile": {
            "profile_id": source.profile_id,
            "expected_page_count": 1,
            "text_repair_rules_version": "source_text_repair_v1",
            "search_normalization_rules_version": "search_text_normalization_v1",
            "sections": [
                {
                    "section_number": "1",
                    "heading": "Synthetischer Abschnitt",
                    "page_start": 1,
                    "page_end": 1,
                }
            ],
            "empty_pages": [],
            "non_evidence_pages": [],
            "excluded_text_blocks": [],
            "glyph_repairs": [],
        },
    }
    result = import_document_nodes_from_pdf(
        pdf_path,
        db_path=db_path,
        manifest=manifest,
        pages=[ExtractedPage(1, source.passage)],
        source_excerpt=SourceExcerpt(
            source_text=source.passage,
            locator=source.locator,
            page_start=1,
            page_end=1,
        ),
    )
    return result, export_document_nodes_package(
        db_path,
        document_version_id=result.document_version_id,
    )


def _candidate_batch(
    tmp_path: Path,
    source: SyntheticSource,
    document_export: dict[str, Any],
    *,
    index: int,
) -> StatementCandidateBatchV3Import:
    package_root = tmp_path / "candidate-packages" / source.document_id
    generated = write_statement_candidate_batch_v3_package(
        document_export,
        output_dir=package_root,
        model_name="synthetic-test-importer",
    )
    manifest = _read_json(generated.manifest_path)
    binding = _read_json(generated.request_payload_path)["metadata"][
        "response_binding"
    ]
    citable_nodes = [node for node in document_export["nodes"] if node["is_citable"]]
    assert len(citable_nodes) == 1
    node = citable_nodes[0]
    candidate_id = f"batch_001:candidate_syn_{index:03d}"
    values = []
    if source.raw_value is not None:
        values.append(
            {
                "value_name": "raw_table_value",
                "raw_value": source.raw_value,
                "text_value": source.raw_value,
                "numeric_value": None,
                "unit": None,
                "comparison_operator": "equals",
                "is_approximate": False,
                "spatial_reference": "synthetic component list",
                "value_order": 1,
            }
        )
    candidate = {
        "candidate_id": candidate_id,
        "document_node_id": node["node_id"],
        "evidence": {
            "source_text": source.passage,
            "char_start": 0,
            "char_end": len(source.passage),
            "source_role": "primary",
        },
        "statement_type": source.statement_type,
        "statement_subtype": "technical_value",
        "normalized_text": source.normalized_statement,
        "binding_status": source.binding_status,
        "lifecycle_status": "active",
        "review_status": "needs_review",
        "action_state": source.action_state,
        "output_constraint": "Nur als vollständig erfundenes Testbeispiel verwenden.",
        "objects": [
            {
                "object_id": f"{candidate_id}:object_001",
                "entity_type": "building_component",
                "canonical_label": source.component_label,
                "aliases": [],
                "role": "subject",
            }
        ],
        "values": values,
        "conditions": [],
        "classifications": [],
        "groups": [],
        "relations": [],
        "document_references": [],
        "review_notes": [],
        "validation_warnings": [],
    }
    candidate_package = {
        "package_version": "statement_candidates_batch_v3",
        "contract_version": "statement_candidates_batch_v3",
        "batch_binding": binding,
        "node_processing": [
            {
                "document_node_id": node["node_id"],
                "status": "processed_with_candidates",
                "candidate_ids": [candidate_id],
            }
        ],
        "candidates": [candidate],
        "relations": [],
    }
    raw_output_json = json.dumps(
        candidate_package,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return StatementCandidateBatchV3Import(
        candidate_package=candidate_package,
        manifest=manifest,
        package_dir=generated.input_path.parent,
        raw_output_json=raw_output_json,
    )


def _source_snapshot(db_path: Path) -> list[tuple[Any, ...]]:
    with sqlite3.connect(db_path) as connection:
        return connection.execute(
            """
            SELECT documents.document_id, versions.document_version_id,
                   nodes.document_node_id, nodes.locator, nodes.source_text,
                   candidates.candidate_id, candidates.evidence_source_text,
                   statements.text, statements.review_status
            FROM documents
            JOIN document_versions AS versions USING (document_pk)
            JOIN document_nodes AS nodes USING (document_version_pk)
            LEFT JOIN statement_candidates_v3 AS candidates USING (document_node_pk)
            LEFT JOIN statements USING (statement_pk)
            ORDER BY documents.document_id, nodes.node_rank, candidates.candidate_id
            """
        ).fetchall()


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


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
