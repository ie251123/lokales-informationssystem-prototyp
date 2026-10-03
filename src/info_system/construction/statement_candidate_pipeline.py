from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from info_system.construction.document_pipeline import validate_export_package

INPUT_PACKAGE_VERSION = "statement_candidate_input_v1"
CANDIDATE_PACKAGE_VERSION = "statement_candidates_v1"
REVIEW_EXPORT_VERSION = "statement_review_export_v1"
CONTRACT_VERSION = "statement_candidates_v1"
PROMPT_VERSION = "statement_candidates_frontier_v1"

DIMENSIONAL_VALUE_NAMES = frozenset(
    {"maximum_distance", "height", "cantilever_each_side"}
)
PROHIBITED_OBSOLETE_PAGE_23_TEXT_VARIANTS = frozenset(
    {
        "0,30 m über dach geführt",
        "0,30 m ueber dach gefuehrt",
        "0,30 m uber dach gefuhrt",
    }
)


class StatementCandidateError(RuntimeError):
    pass


@dataclass(frozen=True)
class PromptPackageResult:
    output_dir: Path
    input_sha256: str
    repeat_input_sha256: str
    prompt_sha256: str
    repeat_prompt_sha256: str
    request_payload_sha256: str


@dataclass(frozen=True)
class StatementCandidateImportResult:
    extraction_run_id: str
    raw_output_sha256: str
    candidate_count: int
    imported_statement_count: int
    status: str


def build_statement_candidate_input_package(
    document_nodes_export: dict[str, Any],
) -> dict[str, Any]:
    validate_export_package(document_nodes_export)
    source_nodes = []
    excluded_nodes = []
    for node in document_nodes_export["nodes"]:
        common = {
            "document_node_id": node["node_id"],
            "node_path": node["node_path"],
            "element_type": node["element_type"],
            "page": node["page_start"],
            "section_number": node["section_number"],
            "heading": node["heading"],
            "locator": node["locator"],
        }
        if node["is_citable"] and node["element_type"] == "page_text":
            source_nodes.append({**common, "source_text": node["source_text"]})
        else:
            excluded_nodes.append(
                {
                    **common,
                    "exclusion_reason": (
                        "non_citable"
                        if not node["is_citable"]
                        else "not_a_page_text_node"
                    ),
                }
            )

    package: dict[str, Any] = {
        "package_version": INPUT_PACKAGE_VERSION,
        "contract_version": CONTRACT_VERSION,
        "prompt_version": PROMPT_VERSION,
        "project_id": document_nodes_export["project_id"],
        "document_id": document_nodes_export["document_id"],
        "document_version_id": document_nodes_export["document_version_id"],
        "document_sha256": document_nodes_export["document_sha256"],
        "document_nodes_package_sha256": document_nodes_export["package_sha256"],
        "source_node_count": len(source_nodes),
        "source_nodes": source_nodes,
        "excluded_nodes": excluded_nodes,
        "instructions": {
            "review_status": "needs_review",
            "allowed_statement_types": [
                "requirement",
                "planning_measure",
                "finding",
                "decision",
            ],
            "forbidden_relation_types": ["fulfills"],
            "source_text_only": True,
        },
    }
    package["package_sha256"] = _sha256_json(package)
    _validator("schemas/construction/statement_candidate_input_v1.schema.json").validate(
        package
    )
    return package


def write_statement_candidate_prompt_package(
    document_nodes_export: dict[str, Any],
    *,
    output_dir: str | Path,
    model_name: str,
) -> PromptPackageResult:
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    input_package = build_statement_candidate_input_package(document_nodes_export)
    input_text = _canonical_json(input_package) + "\n"
    prompt_text = _render_prompt(input_package=input_package)
    request_payload = _request_payload(
        model_name=model_name,
        prompt_text=prompt_text,
        input_package=input_package,
    )
    request_text = _canonical_json(request_payload) + "\n"

    (path / "input_package.json").write_text(input_text, encoding="utf-8")
    (path / "prompt.md").write_text(prompt_text, encoding="utf-8")
    (path / "request_payload.json").write_text(request_text, encoding="utf-8")

    repeat_input = build_statement_candidate_input_package(document_nodes_export)
    repeat_prompt_text = _render_prompt(input_package=repeat_input)
    return PromptPackageResult(
        output_dir=path,
        input_sha256=_sha256_text(input_text),
        repeat_input_sha256=_sha256_text(_canonical_json(repeat_input) + "\n"),
        prompt_sha256=_sha256_text(prompt_text),
        repeat_prompt_sha256=_sha256_text(repeat_prompt_text),
        request_payload_sha256=_sha256_text(request_text),
    )


def validate_statement_candidate_package(
    candidate_package: dict[str, Any],
    *,
    db_path: str | Path,
    document_nodes_export: dict[str, Any],
) -> None:
    _validator(
        "docs/extraction_contracts/schemas/statement_candidates_v1.schema.json"
    ).validate(candidate_package)
    input_package = build_statement_candidate_input_package(document_nodes_export)
    _validate_candidate_metadata(candidate_package, document_nodes_export, input_package)
    source_nodes = {
        node["document_node_id"]: node for node in input_package["source_nodes"]
    }
    _validate_node_references_in_database(
        db_path,
        document_version_id=str(candidate_package["document_version_id"]),
        source_node_ids=set(source_nodes),
    )
    _validate_processing_coverage(candidate_package, source_nodes)
    _validate_candidates(candidate_package, source_nodes)
    _validate_relations(candidate_package)


def import_statement_candidate_package(
    db_path: str | Path,
    *,
    raw_output_json: str,
    document_nodes_export: dict[str, Any],
    model_name: str,
    prompt_text: str,
) -> StatementCandidateImportResult:
    raw_sha = _sha256_text(raw_output_json)
    input_package = build_statement_candidate_input_package(document_nodes_export)
    prompt_sha = _sha256_text(prompt_text)
    extraction_run_id = _run_id(
        document_version_id=str(document_nodes_export["document_version_id"]),
        input_sha256=str(input_package["package_sha256"]),
        prompt_sha256=prompt_sha,
        raw_output_sha256=raw_sha,
    )
    try:
        candidate_package = _parse_raw_output(raw_output_json)
        validate_statement_candidate_package(
            candidate_package,
            db_path=db_path,
            document_nodes_export=document_nodes_export,
        )
    except Exception as error:
        _log_extraction_run(
            db_path,
            extraction_run_id=extraction_run_id,
            document_id=str(document_nodes_export["document_id"]),
            model_name=model_name,
            prompt_version=PROMPT_VERSION,
            status="validation_failed",
            raw_output_json=raw_output_json,
            parameters={
                "contract_version": CONTRACT_VERSION,
                "document_version_id": document_nodes_export["document_version_id"],
                "input_sha256": input_package["package_sha256"],
                "prompt_sha256": prompt_sha,
                "raw_output_sha256": raw_sha,
                "validation_error": str(error),
            },
        )
        if isinstance(error, (StatementCandidateError, sqlite3.Error)):
            raise StatementCandidateError(str(error)) from error
        raise

    imported = _insert_valid_candidate_package(
        db_path,
        extraction_run_id=extraction_run_id,
        candidate_package=candidate_package,
        document_nodes_export=document_nodes_export,
        model_name=model_name,
        prompt_sha=prompt_sha,
        raw_sha=raw_sha,
        raw_output_json=raw_output_json,
    )
    return StatementCandidateImportResult(
        extraction_run_id=extraction_run_id,
        raw_output_sha256=raw_sha,
        candidate_count=len(candidate_package["candidates"]),
        imported_statement_count=imported,
        status="completed",
    )


def export_statement_review_package(
    db_path: str | Path,
    *,
    extraction_run_id: str,
) -> dict[str, Any]:
    with sqlite3.connect(Path(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        run = connection.execute(
            """
            SELECT extraction_run_id, document_id, model_name, prompt_version,
                   status, parameters_json, raw_model_output_json
            FROM extraction_runs
            WHERE extraction_run_id = ?
            """,
            (extraction_run_id,),
        ).fetchone()
        if run is None:
            raise StatementCandidateError(f"extraction_run_id nicht gefunden: {extraction_run_id}")
        raw_package = _parse_raw_output(str(run["raw_model_output_json"]))
        statement_ids = [
            _statement_id(extraction_run_id, str(candidate["candidate_id"]))
            for candidate in raw_package["candidates"]
        ]
        statements = _rows_by_id(
            connection,
            """
            SELECT statement_id, statement_type, text, binding_status,
                   lifecycle_status, review_status, action_state
            FROM statements
            WHERE statement_id IN ({placeholders})
            """,
            statement_ids,
            "statement_id",
        )
        node_rows = _document_nodes_by_id(connection, raw_package)
        candidates = []
        for candidate in sorted(raw_package["candidates"], key=lambda item: item["candidate_id"]):
            candidate_id = str(candidate["candidate_id"])
            statement_id = _statement_id(extraction_run_id, candidate_id)
            statement = statements[statement_id]
            node = node_rows[str(candidate["document_node_id"])]
            candidates.append(
                {
                    "candidate_id": candidate_id,
                    "statement_id": statement_id,
                    "document_id": raw_package["document_id"],
                    "document_version_id": raw_package["document_version_id"],
                    "document_node_id": candidate["document_node_id"],
                    "page": node["page_start"],
                    "section_number": _locator_part(node["locator"], "section"),
                    "element_type": node["element_type"],
                    "evidence_source_text": candidate["evidence"]["source_text"],
                    "normalized_text": statement["text"],
                    "statement_type": statement["statement_type"],
                    "statement_subtype": candidate["statement_subtype"],
                    "binding_status": statement["binding_status"],
                    "lifecycle_status": statement["lifecycle_status"],
                    "review_status": statement["review_status"],
                    "action_state": statement["action_state"],
                    "output_constraint": candidate.get("output_constraint"),
                    "objects": candidate["objects"],
                    "values": candidate["values"],
                    "conditions": candidate["conditions"],
                    "classifications": candidate["classifications"],
                    "groups": candidate["groups"],
                    "relations": _relations_for_candidate(raw_package, candidate_id),
                    "review_notes": candidate["review_notes"],
                    "validation_warnings": candidate["validation_warnings"],
                    "raw_model_output_sha256": _sha256_text(
                        str(run["raw_model_output_json"])
                    ),
                }
            )
        status_counts: dict[str, int] = {}
        for statement in statements.values():
            status = str(statement["review_status"])
            status_counts[status] = status_counts.get(status, 0) + 1
        package: dict[str, Any] = {
            "package_version": REVIEW_EXPORT_VERSION,
            "extraction_run_id": extraction_run_id,
            "document_id": raw_package["document_id"],
            "document_version_id": raw_package["document_version_id"],
            "model_name": run["model_name"],
            "prompt_version": run["prompt_version"],
            "raw_model_output_sha256": _sha256_text(str(run["raw_model_output_json"])),
            "run_status": run["status"],
            "status_counts": dict(sorted(status_counts.items())),
            "candidates": candidates,
        }
    package["package_sha256"] = _sha256_json(package)
    return package


def _validate_candidate_metadata(
    candidate_package: dict[str, Any],
    document_nodes_export: dict[str, Any],
    input_package: dict[str, Any],
) -> None:
    for field in (
        "project_id",
        "document_id",
        "document_version_id",
        "document_sha256",
    ):
        if candidate_package[field] != document_nodes_export[field]:
            raise StatementCandidateError(f"Kandidatenpaket passt nicht zu {field}")
    if candidate_package["contract_version"] != CONTRACT_VERSION:
        raise StatementCandidateError("Kandidatenpaket passt nicht zu contract_version")
    if candidate_package["prompt_version"] != PROMPT_VERSION:
        raise StatementCandidateError("Kandidatenpaket passt nicht zu prompt_version")
    if candidate_package["input_sha256"] != input_package["package_sha256"]:
        raise StatementCandidateError("Kandidatenpaket passt nicht zu input_sha256")


def _validate_node_references_in_database(
    db_path: str | Path,
    *,
    document_version_id: str,
    source_node_ids: set[str],
) -> None:
    with sqlite3.connect(Path(db_path)) as connection:
        rows = connection.execute(
            """
            SELECT document_nodes.document_node_id, document_nodes.is_citable
            FROM document_nodes
            JOIN document_versions USING (document_version_pk)
            WHERE document_versions.document_version_id = ?
            """,
            (document_version_id,),
        ).fetchall()
    db_nodes = {row[0]: row[1] for row in rows}
    missing = sorted(source_node_ids - set(db_nodes))
    if missing:
        raise StatementCandidateError(f"document_nodes nicht gefunden: {missing}")
    non_citable = sorted(node_id for node_id in source_node_ids if db_nodes[node_id] != 1)
    if non_citable:
        raise StatementCandidateError(f"document_node ist nicht zitierfähig: {non_citable}")


def _validate_processing_coverage(
    candidate_package: dict[str, Any],
    source_nodes: dict[str, dict[str, Any]],
) -> None:
    seen: set[str] = set()
    candidate_ids = {candidate["candidate_id"] for candidate in candidate_package["candidates"]}
    for item in candidate_package["node_processing"]:
        node_id = item["document_node_id"]
        if node_id not in source_nodes:
            raise StatementCandidateError(
                f"Verarbeitungsstatus referenziert unbekannten Knoten: {node_id}"
            )
        if node_id in seen:
            raise StatementCandidateError(f"doppelter Verarbeitungsstatus: {node_id}")
        seen.add(node_id)
        ids = item.get("candidate_ids", [])
        status = item["status"]
        if status == "processed_with_candidates" and not ids:
            raise StatementCandidateError(
                "processed_with_candidates erfordert candidate_ids"
            )
        if status == "processed_without_candidate" and ids:
            raise StatementCandidateError(
                "processed_without_candidate darf keine candidate_ids enthalten"
            )
        unknown = sorted(set(ids) - candidate_ids)
        if unknown:
            raise StatementCandidateError(
                f"Verarbeitungsstatus referenziert unbekannte Kandidaten: {unknown}"
            )
    expected = set(source_nodes)
    if seen != expected:
        raise StatementCandidateError(
            "jeder zitierfähige source_node benötigt genau einen processing status"
        )


def _validate_candidates(
    candidate_package: dict[str, Any],
    source_nodes: dict[str, dict[str, Any]],
) -> None:
    seen: set[str] = set()
    for candidate in candidate_package["candidates"]:
        candidate_id = candidate["candidate_id"]
        if candidate_id in seen:
            raise StatementCandidateError(f"doppelte candidate_id: {candidate_id}")
        seen.add(candidate_id)
        node = source_nodes.get(candidate["document_node_id"])
        if node is None:
            raise StatementCandidateError(
                "Kandidat referenziert einen nicht zitierfähigen document_node"
            )
        evidence = candidate["evidence"]
        source_text = str(evidence["source_text"])
        node_source = str(node["source_text"])
        if source_text not in node_source:
            raise StatementCandidateError(
                "evidence muss eine exakte source_text-Passage sein"
            )
        if _looks_like_obsolete_page_23_caption(source_text):
            raise StatementCandidateError(
                "überholte Bildbeschriftung auf Seite 23 ist nicht zulässig"
            )
        if evidence.get("char_start") is not None or evidence.get("char_end") is not None:
            start = int(evidence["char_start"])
            end = int(evidence["char_end"])
            if node_source[start:end] != source_text:
                raise StatementCandidateError(
                    "Zeichenbereich der evidence passt nicht zu source_text"
                )
        if (
            candidate["statement_subtype"] == "source_assessment"
            and candidate["statement_type"] != "finding"
        ):
            raise StatementCandidateError("source_assessment muss statement_type finding haben")
        for value in candidate["values"]:
            if (
                value["value_name"] in DIMENSIONAL_VALUE_NAMES
                and value.get("numeric_value") is not None
                and value.get("unit") is None
            ):
                raise StatementCandidateError("dimensionsbezogener Wert benötigt unit")


def _validate_relations(candidate_package: dict[str, Any]) -> None:
    candidate_ids = {candidate["candidate_id"] for candidate in candidate_package["candidates"]}
    for relation in candidate_package["relations"]:
        source_id = relation["source_candidate_id"]
        target_id = relation["target_candidate_id"]
        if source_id not in candidate_ids or target_id not in candidate_ids:
            raise StatementCandidateError("Relation referenziert unbekannten Kandidaten")
        if source_id == target_id:
            raise StatementCandidateError("Selbstrelation ist nicht zulässig")
        if relation["relation_type"] == "conflicts_with" and source_id > target_id:
            raise StatementCandidateError(
                "conflicts_with muss die kanonische Kandidatenreihenfolge verwenden"
            )


def _insert_valid_candidate_package(
    db_path: str | Path,
    *,
    extraction_run_id: str,
    candidate_package: dict[str, Any],
    document_nodes_export: dict[str, Any],
    model_name: str,
    prompt_sha: str,
    raw_sha: str,
    raw_output_json: str,
) -> int:
    timestamp = _utc_now()
    statement_pk_by_candidate: dict[str, int] = {}
    with sqlite3.connect(Path(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        try:
            _insert_extraction_run(
                connection,
                extraction_run_id=extraction_run_id,
                document_id=str(candidate_package["document_id"]),
                model_name=model_name,
                prompt_version=PROMPT_VERSION,
                status="completed",
                raw_output_json=raw_output_json,
                parameters={
                    "contract_version": CONTRACT_VERSION,
                    "document_version_id": candidate_package["document_version_id"],
                    "document_nodes_package_sha256": document_nodes_export["package_sha256"],
                    "input_sha256": candidate_package["input_sha256"],
                    "prompt_sha256": prompt_sha,
                    "raw_output_sha256": raw_sha,
                    "validation_status": "valid",
                },
                timestamp=timestamp,
            )
            node_pk_by_id = _node_pk_by_id(connection, candidate_package)
            for candidate in candidate_package["candidates"]:
                candidate_id = str(candidate["candidate_id"])
                statement_id = _statement_id(extraction_run_id, candidate_id)
                statement_pk = _upsert_statement(
                    connection,
                    statement_id=statement_id,
                    candidate=candidate,
                    timestamp=timestamp,
                )
                statement_pk_by_candidate[candidate_id] = statement_pk
                _upsert_statement_source(
                    connection,
                    statement_id=statement_id,
                    statement_pk=statement_pk,
                    document_node_pk=node_pk_by_id[str(candidate["document_node_id"])],
                    source_role=str(candidate["evidence"]["source_role"]),
                    timestamp=timestamp,
                )
                _upsert_entities(connection, statement_id, statement_pk, candidate, timestamp)
                _upsert_values(connection, statement_id, statement_pk, candidate, timestamp)
                _upsert_conditions(connection, statement_id, statement_pk, candidate, timestamp)
                _upsert_classifications(
                    connection, statement_id, statement_pk, candidate, timestamp
                )
                _upsert_groups(connection, statement_id, statement_pk, candidate, timestamp)
            for relation in candidate_package["relations"]:
                _upsert_relation(
                    connection,
                    extraction_run_id=extraction_run_id,
                    relation=relation,
                    statement_pk_by_candidate=statement_pk_by_candidate,
                    timestamp=timestamp,
                )
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
    return len(candidate_package["candidates"])


def _log_extraction_run(
    db_path: str | Path,
    *,
    extraction_run_id: str,
    document_id: str,
    model_name: str,
    prompt_version: str,
    status: str,
    raw_output_json: str,
    parameters: dict[str, Any],
) -> None:
    with sqlite3.connect(Path(db_path)) as connection:
        _insert_extraction_run(
            connection,
            extraction_run_id=extraction_run_id,
            document_id=document_id,
            model_name=model_name,
            prompt_version=prompt_version,
            status=status,
            raw_output_json=raw_output_json,
            parameters=parameters,
            timestamp=_utc_now(),
        )


def _insert_extraction_run(
    connection: sqlite3.Connection,
    *,
    extraction_run_id: str,
    document_id: str,
    model_name: str,
    prompt_version: str,
    status: str,
    raw_output_json: str,
    parameters: dict[str, Any],
    timestamp: str,
) -> None:
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
            status,
            parameters_json,
            raw_model_output_json
        )
        VALUES (?, ?, ?, ?, 'frontier_file_workflow', ?, ?, ?, ?, ?)
        ON CONFLICT(extraction_run_id) DO NOTHING
        """,
        (
            extraction_run_id,
            document_id,
            timestamp,
            timestamp,
            model_name,
            prompt_version,
            status,
            _canonical_json(parameters),
            raw_output_json,
        ),
    )


def _upsert_statement(
    connection: sqlite3.Connection,
    *,
    statement_id: str,
    candidate: dict[str, Any],
    timestamp: str,
) -> int:
    connection.execute(
        """
        INSERT INTO statements (
            statement_id,
            statement_type,
            text,
            binding_status,
            lifecycle_status,
            review_status,
            action_state,
            creation_method,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, 'needs_review', ?, 'model_candidate', ?)
        ON CONFLICT(statement_id) DO NOTHING
        """,
        (
            statement_id,
            candidate["statement_type"],
            candidate["normalized_text"],
            candidate["binding_status"],
            candidate["lifecycle_status"],
            candidate.get("action_state"),
            timestamp,
        ),
    )
    return connection.execute(
        "SELECT statement_pk FROM statements WHERE statement_id = ?",
        (statement_id,),
    ).fetchone()[0]


def _upsert_statement_source(
    connection: sqlite3.Connection,
    *,
    statement_id: str,
    statement_pk: int,
    document_node_pk: int,
    source_role: str,
    timestamp: str,
) -> None:
    connection.execute(
        """
        INSERT INTO statement_sources (
            statement_source_id,
            statement_pk,
            document_node_pk,
            source_role,
            created_at
        )
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(statement_pk, document_node_pk, source_role) DO NOTHING
        """,
        (
            _child_id("source", statement_id, source_role),
            statement_pk,
            document_node_pk,
            source_role,
            timestamp,
        ),
    )


def _upsert_entities(
    connection: sqlite3.Connection,
    statement_id: str,
    statement_pk: int,
    candidate: dict[str, Any],
    timestamp: str,
) -> None:
    for item in candidate["objects"]:
        entity_id = _entity_id(str(item["entity_type"]), str(item["canonical_label"]))
        connection.execute(
            """
            INSERT INTO entities (entity_id, entity_type, preferred_label, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(entity_id) DO NOTHING
            """,
            (entity_id, item["entity_type"], item["canonical_label"], timestamp),
        )
        entity_pk = connection.execute(
            "SELECT entity_pk FROM entities WHERE entity_id = ?",
            (entity_id,),
        ).fetchone()[0]
        for alias in item["aliases"]:
            connection.execute(
                """
                INSERT INTO entity_aliases (entity_alias_id, entity_pk, alias, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(entity_pk, alias) DO NOTHING
                """,
                (_child_id("alias", entity_id, str(alias)), entity_pk, alias, timestamp),
            )
        connection.execute(
            """
            INSERT INTO statement_entities (
                statement_entity_id,
                statement_pk,
                entity_pk,
                role,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(statement_pk, entity_pk, role) DO NOTHING
            """,
            (
                _child_id("statement-entity", statement_id, entity_id, str(item["role"])),
                statement_pk,
                entity_pk,
                item["role"],
                timestamp,
            ),
        )


def _upsert_values(
    connection: sqlite3.Connection,
    statement_id: str,
    statement_pk: int,
    candidate: dict[str, Any],
    timestamp: str,
) -> None:
    for index, value in enumerate(candidate["values"], start=1):
        connection.execute(
            """
            INSERT INTO statement_values (
                statement_value_id,
                statement_pk,
                value_name,
                raw_value,
                text_value,
                numeric_value,
                unit,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(statement_value_id) DO NOTHING
            """,
            (
                _child_id("value", statement_id, str(index), str(value["value_name"])),
                statement_pk,
                value["value_name"],
                value["raw_value"],
                value.get("text_value"),
                value.get("numeric_value"),
                value.get("unit"),
                timestamp,
            ),
        )


def _upsert_conditions(
    connection: sqlite3.Connection,
    statement_id: str,
    statement_pk: int,
    candidate: dict[str, Any],
    timestamp: str,
) -> None:
    for index, condition in enumerate(candidate["conditions"], start=1):
        connection.execute(
            """
            INSERT INTO statement_conditions (
                statement_condition_id,
                statement_pk,
                condition_text,
                parameters_json,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(statement_condition_id) DO NOTHING
            """,
            (
                _child_id("condition", statement_id, str(index)),
                statement_pk,
                condition["condition_text"],
                _canonical_json(condition.get("parameters", {})),
                timestamp,
            ),
        )


def _upsert_classifications(
    connection: sqlite3.Connection,
    statement_id: str,
    statement_pk: int,
    candidate: dict[str, Any],
    timestamp: str,
) -> None:
    for item in candidate["classifications"]:
        classification_id = _classification_id(str(item["scheme"]), str(item["code"]))
        connection.execute(
            """
            INSERT INTO classifications (classification_id, scheme, code, label, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(scheme, code) DO NOTHING
            """,
            (
                classification_id,
                item["scheme"],
                item["code"],
                item.get("label"),
                timestamp,
            ),
        )
        classification_pk = connection.execute(
            """
            SELECT classification_pk
            FROM classifications
            WHERE scheme = ? AND code = ?
            """,
            (item["scheme"], item["code"]),
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO statement_classifications (
                statement_classification_id,
                statement_pk,
                classification_pk,
                created_at
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(statement_pk, classification_pk) DO NOTHING
            """,
            (
                _child_id("statement-classification", statement_id, classification_id),
                statement_pk,
                classification_pk,
                timestamp,
            ),
        )


def _upsert_groups(
    connection: sqlite3.Connection,
    statement_id: str,
    statement_pk: int,
    candidate: dict[str, Any],
    timestamp: str,
) -> None:
    for group in candidate["groups"]:
        group_id = _child_id("group", str(group["group_id"]))
        connection.execute(
            """
            INSERT INTO statement_groups (
                statement_group_id,
                group_type,
                label,
                created_at
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(statement_group_id) DO NOTHING
            """,
            (group_id, group["group_type"], group["label"], timestamp),
        )
        group_pk = connection.execute(
            "SELECT statement_group_pk FROM statement_groups WHERE statement_group_id = ?",
            (group_id,),
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO statement_group_members (
                statement_group_member_id,
                statement_group_pk,
                statement_pk,
                member_rank,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(statement_group_pk, statement_pk) DO NOTHING
            """,
            (
                _child_id("group-member", group_id, statement_id),
                group_pk,
                statement_pk,
                group["member_rank"],
                timestamp,
            ),
        )


def _upsert_relation(
    connection: sqlite3.Connection,
    *,
    extraction_run_id: str,
    relation: dict[str, Any],
    statement_pk_by_candidate: dict[str, int],
    timestamp: str,
) -> None:
    source_pk = statement_pk_by_candidate[str(relation["source_candidate_id"])]
    target_pk = statement_pk_by_candidate[str(relation["target_candidate_id"])]
    connection.execute(
        """
        INSERT INTO statement_relations (
            statement_relation_id,
            source_statement_pk,
            target_statement_pk,
            relation_type,
            created_at
        )
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(source_statement_pk, target_statement_pk, relation_type) DO NOTHING
        """,
        (
            _child_id("relation", extraction_run_id, str(relation["relation_id"])),
            source_pk,
            target_pk,
            relation["relation_type"],
            timestamp,
        ),
    )


def _node_pk_by_id(
    connection: sqlite3.Connection,
    candidate_package: dict[str, Any],
) -> dict[str, int]:
    node_ids = sorted(
        {str(candidate["document_node_id"]) for candidate in candidate_package["candidates"]}
    )
    return _rows_by_id(
        connection,
        """
        SELECT document_node_id, document_node_pk
        FROM document_nodes
        WHERE document_node_id IN ({placeholders})
        """,
        node_ids,
        "document_node_id",
        value_column="document_node_pk",
    )


def _document_nodes_by_id(
    connection: sqlite3.Connection,
    raw_package: dict[str, Any],
) -> dict[str, sqlite3.Row]:
    node_ids = sorted(
        {str(candidate["document_node_id"]) for candidate in raw_package["candidates"]}
    )
    return _rows_by_id(
        connection,
        """
        SELECT document_node_id, page_start, locator, element_type
        FROM document_nodes
        WHERE document_node_id IN ({placeholders})
        """,
        node_ids,
        "document_node_id",
    )


def _rows_by_id(
    connection: sqlite3.Connection,
    sql_template: str,
    ids: list[str],
    key_column: str,
    *,
    value_column: str | None = None,
) -> dict[str, Any]:
    if not ids:
        return {}
    sql = sql_template.format(placeholders=", ".join("?" for _ in ids))
    rows = connection.execute(sql, ids).fetchall()
    if value_column is None:
        return {str(row[key_column]): row for row in rows}
    return {str(row[key_column]): row[value_column] for row in rows}


def _relations_for_candidate(
    raw_package: dict[str, Any],
    candidate_id: str,
) -> list[dict[str, Any]]:
    return [
        relation
        for relation in raw_package["relations"]
        if relation["source_candidate_id"] == candidate_id
        or relation["target_candidate_id"] == candidate_id
    ]


def _parse_raw_output(raw_output_json: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw_output_json)
    except json.JSONDecodeError as error:
        raise StatementCandidateError("rohe Modellausgabe ist kein gültiges JSON") from error
    if not isinstance(parsed, dict):
        raise StatementCandidateError("rohe Modellausgabe muss ein JSON-Objekt sein")
    return parsed


def _render_prompt(*, input_package: dict[str, Any]) -> str:
    contract = _contract_path().read_text(encoding="utf-8")
    schema = json.loads(_candidate_schema_path().read_text(encoding="utf-8"))
    prompt_template = _prompt_template_path().read_text(encoding="utf-8")
    return (
        prompt_template.replace("{{CONTRACT_MARKDOWN}}", contract)
        .replace("{{JSON_SCHEMA}}", _canonical_json(schema))
        .replace("{{INPUT_PACKAGE}}", _canonical_json(input_package))
    )


def _request_payload(
    *,
    model_name: str,
    prompt_text: str,
    input_package: dict[str, Any],
) -> dict[str, Any]:
    return {
        "model": model_name,
        "temperature": 0,
        "top_p": 1,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Gib exakt ein JSON-Objekt zurück, das dem bereitgestellten "
                    "Aussagekandidatenschema entspricht. Verwende kein Markdown "
                    "und keine erläuternde Prosa."
                ),
            },
            {
                "role": "user",
                "content": prompt_text,
            },
        ],
        "metadata": {
            "contract_version": CONTRACT_VERSION,
            "prompt_version": PROMPT_VERSION,
            "input_sha256": input_package["package_sha256"],
        },
    }


def _looks_like_obsolete_page_23_caption(source_text: str) -> bool:
    normalized = source_text.casefold().replace("\n", " ")
    normalized = " ".join(normalized.split())
    return any(
        phrase in normalized for phrase in PROHIBITED_OBSOLETE_PAGE_23_TEXT_VARIANTS
    )


def _validator(relative_path: str) -> Draft202012Validator:
    path = _repo_root() / relative_path
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _candidate_schema_path() -> Path:
    return _repo_root() / "docs" / "extraction_contracts" / "schemas" / "statement_candidates_v1.schema.json"


def _contract_path() -> Path:
    return _repo_root() / "docs" / "extraction_contracts" / "statement_candidates_v1.md"


def _prompt_template_path() -> Path:
    return _repo_root() / "prompts" / "statement_candidates_frontier_v1.md"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _statement_id(extraction_run_id: str, candidate_id: str) -> str:
    return "stmt:" + _sha256_text(f"{extraction_run_id}|{candidate_id}")[:32]


def _run_id(
    *,
    document_version_id: str,
    input_sha256: str,
    prompt_sha256: str,
    raw_output_sha256: str,
) -> str:
    return "run:" + _sha256_text(
        f"{document_version_id}|{input_sha256}|{prompt_sha256}|{raw_output_sha256}"
    )[:32]


def _entity_id(entity_type: str, canonical_label: str) -> str:
    return "entity:" + _sha256_text(
        f"{entity_type}|{canonical_label.casefold()}"
    )[:32]


def _classification_id(scheme: str, code: str) -> str:
    return "class:" + _sha256_text(f"{scheme}|{code}")[:32]


def _child_id(prefix: str, *parts: str) -> str:
    return prefix + ":" + _sha256_text("|".join(parts))[:32]


def _locator_part(locator: str | None, key: str) -> str | None:
    if locator is None:
        return None
    for part in locator.split(";"):
        name, _, value = part.partition("=")
        if name == key:
            return value
    return None


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_json(data: dict[str, Any]) -> str:
    return _sha256_text(_canonical_json(data))


def _canonical_json(data: Any) -> str:
    return json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
