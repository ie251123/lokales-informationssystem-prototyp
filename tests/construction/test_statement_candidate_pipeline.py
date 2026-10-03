from __future__ import annotations

import copy
import json
import sqlite3
from pathlib import Path

import pytest
from jsonschema import ValidationError

from info_system.cli import main
from info_system.construction.document_pipeline import (
    export_document_nodes_package,
    import_document_nodes_from_pdf,
)
from info_system.construction.statement_candidate_pipeline import (
    StatementCandidateError,
    build_statement_candidate_input_package,
    export_statement_review_package,
    import_statement_candidate_package,
    validate_statement_candidate_package,
    write_statement_candidate_prompt_package,
)
from info_system.construction.text_extraction import ExtractedPage

from tests.construction.test_document_pipeline import _manifest, _write_pdf


def test_builds_prompt_input_from_citable_document_nodes_only(tmp_path) -> None:
    db_path, document_export = _document_export(tmp_path)

    del db_path
    package = build_statement_candidate_input_package(document_export)

    assert package["package_version"] == "statement_candidate_input_v1"
    assert package["document_version_id"] == document_export["document_version_id"]
    assert [node["page"] for node in package["source_nodes"]] == [1]
    assert package["source_nodes"][0]["source_text"] == "Rettungsweg 1,20 m"
    assert any(node["element_type"] == "empty_page" for node in package["excluded_nodes"])
    assert "gold" not in json.dumps(package, ensure_ascii=False).lower()
    assert "hold" not in json.dumps(package, ensure_ascii=False).lower()


def test_candidate_schema_rejects_unknown_fields_and_confirmed_status(tmp_path) -> None:
    db_path, document_export = _document_export(tmp_path)
    candidate_package = _candidate_package(document_export)
    unknown_field = copy.deepcopy(candidate_package)
    unknown_field["candidates"][0]["unexpected"] = "nope"
    confirmed = copy.deepcopy(candidate_package)
    confirmed["candidates"][0]["review_status"] = "confirmed"

    validate_statement_candidate_package(
        candidate_package,
        db_path=db_path,
        document_nodes_export=document_export,
    )
    with pytest.raises(ValidationError):
        validate_statement_candidate_package(
            unknown_field,
            db_path=db_path,
            document_nodes_export=document_export,
        )
    with pytest.raises(ValidationError):
        validate_statement_candidate_package(
            confirmed,
            db_path=db_path,
            document_nodes_export=document_export,
        )


def test_candidate_validation_rejects_bad_sources_statuses_and_relations(tmp_path) -> None:
    db_path, document_export = _document_export(tmp_path)

    bad_source = _candidate_package(document_export)
    bad_source["candidates"][0]["evidence"]["source_text"] = "Rohtext nicht Quelle"
    with pytest.raises(StatementCandidateError, match="exakte source_text"):
        validate_statement_candidate_package(
            bad_source,
            db_path=db_path,
            document_nodes_export=document_export,
        )

    bad_status_coverage = _candidate_package(document_export)
    bad_status_coverage["node_processing"] = []
    with pytest.raises(StatementCandidateError, match="processing status"):
        validate_statement_candidate_package(
            bad_status_coverage,
            db_path=db_path,
            document_nodes_export=document_export,
        )

    bad_relation = _candidate_package(
        document_export,
        include_second=True,
        relation_type="fulfills",
    )
    with pytest.raises(ValidationError):
        validate_statement_candidate_package(
            bad_relation,
            db_path=db_path,
            document_nodes_export=document_export,
        )

    self_relation = _candidate_package(document_export, include_second=True)
    self_relation["relations"][0]["source_candidate_id"] = "cand-1"
    self_relation["relations"][0]["target_candidate_id"] = "cand-1"
    with pytest.raises(StatementCandidateError, match="Selbstrelation"):
        validate_statement_candidate_package(
            self_relation,
            db_path=db_path,
            document_nodes_export=document_export,
        )


def test_candidate_validation_requires_units_for_dimensional_numbers(tmp_path) -> None:
    db_path, document_export = _document_export(tmp_path)
    candidate_package = _candidate_package(document_export)
    candidate_package["candidates"][0]["values"][0]["unit"] = None

    with pytest.raises(StatementCandidateError, match="unit"):
        validate_statement_candidate_package(
            candidate_package,
            db_path=db_path,
            document_nodes_export=document_export,
        )


def test_candidate_validation_rejects_obsolete_page_23_caption_variant(tmp_path) -> None:
    pdf_path = _write_pdf(tmp_path, b"%PDF obsolete caption")
    db_path = tmp_path / "prototype.sqlite"
    manifest = _manifest(expected_page_count=1)
    result = import_document_nodes_from_pdf(
        pdf_path,
        db_path=db_path,
        manifest=manifest,
        pages=[ExtractedPage(1, "0,30 m ueber Dach gefuehrt")],
    )
    document_export = export_document_nodes_package(
        db_path,
        document_version_id=result.document_version_id,
    )
    candidate_package = _candidate_package(document_export)
    candidate_package["candidates"][0]["evidence"]["source_text"] = (
        "0,30 m ueber Dach gefuehrt"
    )

    with pytest.raises(StatementCandidateError, match="Seite 23"):
        validate_statement_candidate_package(
            candidate_package,
            db_path=db_path,
            document_nodes_export=document_export,
        )


def test_import_logs_raw_output_imports_relations_and_is_idempotent(tmp_path) -> None:
    db_path, document_export = _document_export(tmp_path)
    raw_output = json.dumps(
        _candidate_package(document_export, include_second=True),
        ensure_ascii=False,
        indent=2,
    )

    first = import_statement_candidate_package(
        db_path,
        raw_output_json=raw_output,
        document_nodes_export=document_export,
        model_name="frontier-visible-model",
        prompt_text="prompt-v1",
    )
    second = import_statement_candidate_package(
        db_path,
        raw_output_json=raw_output,
        document_nodes_export=document_export,
        model_name="frontier-visible-model",
        prompt_text="prompt-v1",
    )

    with sqlite3.connect(db_path) as connection:
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "extraction_runs",
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
            )
        }
        status_counts = dict(
            connection.execute(
                "SELECT review_status, COUNT(*) FROM statements GROUP BY review_status"
            ).fetchall()
        )
        raw_model_output_json = connection.execute(
            "SELECT raw_model_output_json FROM extraction_runs"
        ).fetchone()[0]

    assert first.extraction_run_id == second.extraction_run_id
    assert counts == {
        "extraction_runs": 1,
        "statements": 2,
        "statement_sources": 2,
        "entities": 1,
        "entity_aliases": 1,
        "statement_entities": 2,
        "statement_values": 1,
        "statement_conditions": 1,
        "classifications": 1,
        "statement_classifications": 1,
        "statement_groups": 1,
        "statement_group_members": 2,
        "statement_relations": 1,
    }
    assert status_counts == {"needs_review": 2}
    assert raw_model_output_json == raw_output


def test_invalid_candidate_package_logs_failed_run_without_partial_import(tmp_path) -> None:
    db_path, document_export = _document_export(tmp_path)
    invalid = _candidate_package(document_export, include_second=True)
    invalid["candidates"][1]["evidence"]["source_text"] = "nicht im source_text"
    raw_output = json.dumps(invalid, ensure_ascii=False)

    with pytest.raises(StatementCandidateError):
        import_statement_candidate_package(
            db_path,
            raw_output_json=raw_output,
            document_nodes_export=document_export,
            model_name="frontier-visible-model",
            prompt_text="prompt-v1",
        )

    with sqlite3.connect(db_path) as connection:
        run_status = connection.execute(
            "SELECT status FROM extraction_runs"
        ).fetchone()[0]
        assert run_status == "validation_failed"
        assert connection.execute("SELECT COUNT(*) FROM statements").fetchone()[0] == 0


def test_review_export_is_deterministic_and_checksum_bound(tmp_path) -> None:
    db_path, document_export = _document_export(tmp_path)
    raw_output = json.dumps(_candidate_package(document_export), ensure_ascii=False)
    result = import_statement_candidate_package(
        db_path,
        raw_output_json=raw_output,
        document_nodes_export=document_export,
        model_name="frontier-visible-model",
        prompt_text="prompt-v1",
    )

    first = export_statement_review_package(
        db_path,
        extraction_run_id=result.extraction_run_id,
    )
    second = export_statement_review_package(
        db_path,
        extraction_run_id=result.extraction_run_id,
    )

    assert first == second
    assert first["raw_model_output_sha256"] == result.raw_output_sha256
    assert first["status_counts"] == {"needs_review": 1}
    assert first["candidates"][0]["review_status"] == "needs_review"


def test_writes_reproducible_prompt_package(tmp_path) -> None:
    _, document_export = _document_export(tmp_path)
    output_dir = tmp_path / "prompt-package"

    result = write_statement_candidate_prompt_package(
        document_export,
        output_dir=output_dir,
        model_name="frontier-visible-model",
    )

    assert result.input_sha256 == result.repeat_input_sha256
    assert result.prompt_sha256 == result.repeat_prompt_sha256
    assert (output_dir / "input_package.json").exists()
    assert (output_dir / "prompt.md").exists()
    assert (output_dir / "request_payload.json").exists()


def test_cli_prompt_validate_import_and_review_export(tmp_path, capsys) -> None:
    db_path, document_export = _document_export(tmp_path)
    export_path = tmp_path / "document_nodes.json"
    raw_path = tmp_path / "raw_candidates.json"
    prompt_dir = tmp_path / "prompt"
    review_path = tmp_path / "review.json"
    export_path.write_text(json.dumps(document_export, ensure_ascii=False), encoding="utf-8")
    raw_path.write_text(
        json.dumps(_candidate_package(document_export), ensure_ascii=False),
        encoding="utf-8",
    )

    assert (
        main(
            [
                "build-statement-candidate-prompt",
                "--document-nodes-export",
                str(export_path),
                "--output-dir",
                str(prompt_dir),
                "--model",
                "frontier-visible-model",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (
        main(
            [
                "validate-statement-candidates",
                "--db",
                str(db_path),
                "--document-nodes-export",
                str(export_path),
                "--raw-output",
                str(raw_path),
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (
        main(
            [
                "import-statement-candidates",
                "--db",
                str(db_path),
                "--document-nodes-export",
                str(export_path),
                "--raw-output",
                str(raw_path),
                "--model",
                "frontier-visible-model",
                "--prompt",
                str(prompt_dir / "prompt.md"),
            ]
        )
        == 0
    )
    import_output = capsys.readouterr().out
    extraction_run_id = [
        line.split(": ", 1)[1]
        for line in import_output.splitlines()
        if line.startswith("extraction_run_id: ")
    ][0]
    assert (
        main(
            [
                "export-statement-review",
                "--db",
                str(db_path),
                "--extraction-run-id",
                extraction_run_id,
                "--output",
                str(review_path),
            ]
        )
        == 0
    )

    review = json.loads(review_path.read_text(encoding="utf-8"))
    assert review["package_version"] == "statement_review_export_v1"
    assert review["status_counts"] == {"needs_review": 1}


def _document_export(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    pdf_path = _write_pdf(tmp_path, b"%PDF statement candidates")
    db_path = tmp_path / "prototype.sqlite"
    manifest = _manifest(
        expected_page_count=2,
        sections=[{"section_number": "5.3", "heading": "Rettungswege", "page_start": 1, "page_end": 2}],
        empty_pages=[2],
    )
    result = import_document_nodes_from_pdf(
        pdf_path,
        db_path=db_path,
        manifest=manifest,
        pages=[
            ExtractedPage(1, "Rettungsweg 1,20 m"),
            ExtractedPage(2, ""),
        ],
    )
    return db_path, export_document_nodes_package(
        db_path,
        document_version_id=result.document_version_id,
    )


def _candidate_package(
    document_export: dict[str, object],
    *,
    include_second: bool = False,
    relation_type: str = "addresses",
) -> dict[str, object]:
    input_package = build_statement_candidate_input_package(document_export)
    source_node = input_package["source_nodes"][0]
    node_id = source_node["document_node_id"]
    source_text = source_node["source_text"]
    package = {
        "package_version": "statement_candidates_v1",
        "contract_version": "statement_candidates_v1",
        "project_id": document_export["project_id"],
        "document_id": document_export["document_id"],
        "document_version_id": document_export["document_version_id"],
        "document_sha256": document_export["document_sha256"],
        "model_name": "frontier-visible-model",
        "prompt_version": "statement_candidates_frontier_v1",
        "input_sha256": input_package["package_sha256"],
        "node_processing": [
            {
                "document_node_id": node_id,
                "status": "processed_with_candidates",
                "candidate_ids": ["cand-1", "cand-2"] if include_second else ["cand-1"],
            }
        ],
        "candidates": [
            {
                "candidate_id": "cand-1",
                "document_node_id": node_id,
                "evidence": {
                    "source_text": source_text,
                    "char_start": 0,
                    "char_end": len(source_text),
                    "source_role": "primary",
                },
                "statement_type": "requirement",
                "statement_subtype": "egress_width",
                "normalized_text": "Rettungsweg mit Breite 1,20 m.",
                "binding_status": "unknown",
                "lifecycle_status": "active",
                "review_status": "needs_review",
                "action_state": None,
                "output_constraint": None,
                "objects": [
                    {
                        "object_id": "obj-1",
                        "entity_type": "building_component",
                        "canonical_label": "Rettungsweg",
                        "aliases": ["Fluchtweg"],
                        "role": "subject",
                    }
                ],
                "values": [
                    {
                        "value_name": "maximum_distance",
                        "raw_value": "1,20 m",
                        "text_value": None,
                        "numeric_value": 1.2,
                        "unit": "m",
                        "comparison_operator": "equals",
                        "is_approximate": False,
                        "spatial_reference": None,
                        "value_order": 1,
                    }
                ],
                "conditions": [
                    {
                        "condition_text": "wenn als Rettungsweg genutzt",
                        "parameters": {"use": "egress"},
                    }
                ],
                "classifications": [
                    {
                        "scheme": "fire_safety_topic",
                        "code": "egress",
                        "label": "Rettungsweg",
                        "role": "topic",
                    }
                ],
                "groups": [
                    {
                        "group_id": "grp-1",
                        "group_type": "topic_cluster",
                        "label": "Rettungsweggruppe",
                        "member_role": "primary",
                        "member_rank": 1,
                    }
                ],
                "relations": [],
                "document_references": [],
                "review_notes": ["synthetic needs review"],
                "validation_warnings": [],
            }
        ],
        "relations": [],
    }
    if include_second:
        second = copy.deepcopy(package["candidates"][0])
        second["candidate_id"] = "cand-2"
        second["statement_type"] = "planning_measure"
        second["statement_subtype"] = "planned_component"
        second["normalized_text"] = "Rettungsweg wird geplant."
        second["values"] = []
        second["conditions"] = []
        second["classifications"] = []
        second["groups"][0]["member_rank"] = 2
        package["candidates"].append(second)
        package["relations"] = [
            {
                "relation_id": "rel-1",
                "source_candidate_id": "cand-2",
                "target_candidate_id": "cand-1",
                "relation_type": relation_type,
            }
        ]
    return package
