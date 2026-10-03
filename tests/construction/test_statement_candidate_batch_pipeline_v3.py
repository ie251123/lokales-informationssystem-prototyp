from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest
from jsonschema.exceptions import ValidationError

from info_system.construction.statement_candidate_batch_pipeline_v3 import (
    CONTRACT_VERSION,
    PROMPT_VERSION,
    build_statement_candidate_batch_v3_output_skeleton,
    load_statement_candidate_batch_v3_schema,
    statement_candidate_batch_v3_output_validator,
    validate_statement_candidate_batch_v3_output,
    write_statement_candidate_batch_v3_package,
)


def test_request_payload_is_self_contained_and_has_no_unresolved_references(
    tmp_path: Path,
) -> None:
    result = write_statement_candidate_batch_v3_package(
        _document_export(9),
        output_dir=tmp_path,
        model_name="GPT 5.6 Sol",
        batch_number=1,
    )

    request = _read_json(tmp_path / "batch_001" / "request_payload.json")
    prompt = request["messages"][1]["content"]
    schema = load_statement_candidate_batch_v3_schema()
    schema_text = _canonical_json(schema)
    input_text = _canonical_json(_read_json(tmp_path / "batch_001" / "input_package.json"))

    assert request["model"] == "GPT 5.6 Sol"
    assert request["metadata"]["contract_version"] == CONTRACT_VERSION
    assert request["metadata"]["prompt_version"] == PROMPT_VERSION
    assert request["metadata"]["output_schema_sha256"] == _sha_text(schema_text)
    assert schema_text in prompt
    assert input_text in prompt
    assert not _find_keys(schema, "$ref")
    assert not re.search(
        r"https?://|file://|(?:docs|schemas|prompts)[/\\]|\.schema\.json",
        "\n".join(message["content"] for message in request["messages"]),
        flags=re.IGNORECASE,
    )
    assert (tmp_path / "batch_manifest.json") == result.manifest_path
    assert not (tmp_path / "batch_002").exists()


def test_embedded_schema_is_the_exact_validator_schema_and_exposes_all_definitions(
    tmp_path: Path,
) -> None:
    write_statement_candidate_batch_v3_package(
        _document_export(1), output_dir=tmp_path, model_name="GPT 5.6 Sol"
    )
    request = _read_json(tmp_path / "batch_001" / "request_payload.json")
    prompt = request["messages"][1]["content"]
    schema = load_statement_candidate_batch_v3_schema()
    validator = statement_candidate_batch_v3_output_validator()

    assert validator.schema == schema
    assert _canonical_json(validator.schema) in prompt
    assert schema["properties"]["node_processing"]["items"]["required"] == [
        "document_node_id",
        "status",
        "candidate_ids",
    ]
    candidate = schema["properties"]["candidates"]["items"]
    assert set(candidate["required"]) == {
        "candidate_id",
        "document_node_id",
        "evidence",
        "statement_type",
        "statement_subtype",
        "normalized_text",
        "binding_status",
        "lifecycle_status",
        "review_status",
        "action_state",
        "output_constraint",
        "objects",
        "values",
        "conditions",
        "classifications",
        "groups",
        "relations",
        "document_references",
        "review_notes",
        "validation_warnings",
    }
    assert candidate["properties"]["statement_type"]["enum"] == [
        "requirement",
        "planning_measure",
        "finding",
        "decision",
    ]
    assert schema["properties"]["relations"]["items"]["properties"][
        "relation_type"
    ]["enum"] == [
        "addresses",
        "derived_from",
        "implements_decision",
        "supersedes",
        "conflicts_with",
    ]
    for forbidden_short_field in (
        "processing_status",
        "source_node_id",
        "evidence_quote",
        "statement_text",
    ):
        assert forbidden_short_field in prompt


def test_prompt_contains_complete_neutral_output_skeleton(tmp_path: Path) -> None:
    write_statement_candidate_batch_v3_package(
        _document_export(1), output_dir=tmp_path, model_name="GPT 5.6 Sol"
    )
    request = _read_json(tmp_path / "batch_001" / "request_payload.json")
    prompt = request["messages"][1]["content"]
    skeleton = build_statement_candidate_batch_v3_output_skeleton()
    schema = load_statement_candidate_batch_v3_schema()

    assert _canonical_json(skeleton) in prompt
    assert set(skeleton) == set(schema["required"])
    assert set(skeleton["batch_binding"]) == set(
        schema["properties"]["batch_binding"]["required"]
    )
    assert set(skeleton["node_processing"][0]) == set(
        schema["properties"]["node_processing"]["items"]["required"]
    )
    assert set(skeleton["candidates"][0]) == set(
        schema["properties"]["candidates"]["items"]["required"]
    )
    candidate_schema = schema["properties"]["candidates"]["items"]
    candidate_skeleton = skeleton["candidates"][0]
    assert set(candidate_skeleton["evidence"]) == set(
        candidate_schema["properties"]["evidence"]["required"]
    )
    for field in (
        "objects",
        "values",
        "conditions",
        "classifications",
        "groups",
        "relations",
        "document_references",
    ):
        assert set(candidate_skeleton[field][0]) == set(
            candidate_schema["properties"][field]["items"]["required"]
        )
    assert set(skeleton["relations"][0]) == set(
        schema["properties"]["relations"]["items"]["required"]
    )
    assert candidate_skeleton["review_notes"] == []
    assert candidate_skeleton["validation_warnings"] == []


def test_small_complete_v3_response_is_accepted(tmp_path: Path) -> None:
    result = write_statement_candidate_batch_v3_package(
        _document_export(2), output_dir=tmp_path, model_name="GPT 5.6 Sol"
    )
    manifest = _read_json(result.manifest_path)
    response = _valid_response(tmp_path, manifest)

    validate_statement_candidate_batch_v3_output(
        response, manifest=manifest, package_dir=tmp_path / "batch_001"
    )


def test_shortened_v2_failure_format_is_rejected(tmp_path: Path) -> None:
    result = write_statement_candidate_batch_v3_package(
        _document_export(1), output_dir=tmp_path, model_name="GPT 5.6 Sol"
    )
    manifest = _read_json(result.manifest_path)
    binding = _read_json(tmp_path / "batch_001" / "request_payload.json")["metadata"][
        "response_binding"
    ]
    target_id = manifest["batches"][0]["target_node_ids"][0]
    shortened = {
        "package_version": CONTRACT_VERSION,
        "contract_version": CONTRACT_VERSION,
        "batch_binding": binding,
        "node_processing": [
            {
                "document_node_id": target_id,
                "processing_status": "processed_with_candidate",
            }
        ],
        "candidates": [
            {
                "candidate_id": "batch_001:candidate_001",
                "source_node_id": target_id,
                "statement_type": "finding",
                "statement_text": "Short form.",
                "evidence_quote": "A-1",
                "review_status": "needs_review",
            }
        ],
        "relations": [],
    }

    with pytest.raises(ValidationError):
        validate_statement_candidate_batch_v3_output(
            shortened, manifest=manifest, package_dir=tmp_path / "batch_001"
        )


def test_missing_required_field_is_rejected(tmp_path: Path) -> None:
    result = write_statement_candidate_batch_v3_package(
        _document_export(1), output_dir=tmp_path, model_name="GPT 5.6 Sol"
    )
    manifest = _read_json(result.manifest_path)
    response = _valid_response(tmp_path, manifest)
    del response["candidates"][0]["statement_subtype"]

    with pytest.raises(ValidationError, match="statement_subtype"):
        validate_statement_candidate_batch_v3_output(
            response, manifest=manifest, package_dir=tmp_path / "batch_001"
        )


@pytest.mark.parametrize(
    ("location", "field", "value"),
    [
        ("processing", "processing_status", "processed_with_candidate"),
        ("candidate", "source_node_id", "node-001"),
        ("candidate", "evidence_quote", "A-1"),
        ("candidate", "statement_text", "Short form."),
    ],
)
def test_forbidden_short_field_is_rejected(
    tmp_path: Path, location: str, field: str, value: str
) -> None:
    result = write_statement_candidate_batch_v3_package(
        _document_export(1), output_dir=tmp_path, model_name="GPT 5.6 Sol"
    )
    manifest = _read_json(result.manifest_path)
    response = _valid_response(tmp_path, manifest)
    target = response["node_processing"][0] if location == "processing" else response["candidates"][0]
    target[field] = value

    with pytest.raises(ValidationError, match="Additional properties"):
        validate_statement_candidate_batch_v3_output(
            response, manifest=manifest, package_dir=tmp_path / "batch_001"
        )


def test_repeated_v3_package_generation_is_byte_identical(tmp_path: Path) -> None:
    document = _document_export(9)
    first = tmp_path / "first"
    second = tmp_path / "second"
    first_result = write_statement_candidate_batch_v3_package(
        document, output_dir=first, model_name="GPT 5.6 Sol", batch_number=1
    )
    second_result = write_statement_candidate_batch_v3_package(
        copy.deepcopy(document),
        output_dir=second,
        model_name="GPT 5.6 Sol",
        batch_number=1,
    )

    first_files = {
        path.relative_to(first): path.read_bytes()
        for path in first.rglob("*")
        if path.is_file()
    }
    second_files = {
        path.relative_to(second): path.read_bytes()
        for path in second.rglob("*")
        if path.is_file()
    }
    assert first_files == second_files
    assert first_result.manifest_sha256 == second_result.manifest_sha256
    assert first_result.input_sha256 == second_result.input_sha256
    assert first_result.prompt_sha256 == second_result.prompt_sha256
    assert first_result.request_payload_sha256 == second_result.request_payload_sha256


def _valid_response(package_root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    batch = manifest["batches"][0]
    package_dir = package_root / "batch_001"
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
    candidate_id = "batch_001:candidate_001"
    return {
        "package_version": CONTRACT_VERSION,
        "contract_version": CONTRACT_VERSION,
        "batch_binding": binding,
        "node_processing": [
            {
                "document_node_id": node_id,
                "status": (
                    "processed_with_candidates"
                    if node_id == target_id
                    else "processed_without_candidate"
                ),
                "candidate_ids": [candidate_id] if node_id == target_id else [],
            }
            for node_id in batch["target_node_ids"]
        ],
        "candidates": [
            {
                "candidate_id": candidate_id,
                "document_node_id": target_id,
                "evidence": {
                    "source_text": source_text,
                    "char_start": 0,
                    "char_end": len(source_text),
                    "source_role": "primary",
                },
                "statement_type": "finding",
                "statement_subtype": "technical_value",
                "normalized_text": "Synthetic finding.",
                "binding_status": "unknown",
                "lifecycle_status": "active",
                "review_status": "needs_review",
                "action_state": "documented",
                "output_constraint": None,
                "objects": [],
                "values": [],
                "conditions": [],
                "classifications": [],
                "groups": [],
                "relations": [],
                "document_references": [],
                "review_notes": [],
                "validation_warnings": [],
            }
        ],
        "relations": [],
    }


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


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _find_keys(value: Any, key: str) -> list[Any]:
    found = []
    if isinstance(value, dict):
        for current_key, child in value.items():
            if current_key == key:
                found.append(child)
            found.extend(_find_keys(child, key))
    elif isinstance(value, list):
        for child in value:
            found.extend(_find_keys(child, key))
    return found


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha_json(value: Any) -> str:
    return _sha_text(_canonical_json(value))


def _sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
