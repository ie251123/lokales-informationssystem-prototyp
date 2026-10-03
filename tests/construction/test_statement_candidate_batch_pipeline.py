from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from info_system.construction.statement_candidate_batch_pipeline import (
    BatchPipelineError,
    build_batch_manifest,
    build_batch_output_receipt,
    merge_statement_candidate_batch_outputs,
    validate_statement_candidate_batch_output,
    write_statement_candidate_batch_packages,
)


def test_batch_manifest_is_deterministic_complete_and_section_aware() -> None:
    document_export = _document_export(
        [("A", 3, 10), ("B", 9, 10), ("C", 2, 12_000)]
    )

    first = build_batch_manifest(document_export)
    second = build_batch_manifest(copy.deepcopy(document_export))

    assert first == second
    assert first["manifest_sha256"] == second["manifest_sha256"]
    assert [len(batch["target_node_ids"]) for batch in first["batches"]] == [3, 8, 1, 2]
    assert [batch["section_numbers"] for batch in first["batches"]] == [
        ["A"],
        ["B"],
        ["B"],
        ["C"],
    ]
    target_ids = [
        node_id
        for batch in first["batches"]
        for node_id in batch["target_node_ids"]
    ]
    expected_ids = [
        node["node_id"]
        for node in sorted(document_export["nodes"], key=lambda item: item["rank"])
        if node["is_citable"] and node["element_type"] == "page_text"
    ]
    assert target_ids == expected_ids
    assert len(target_ids) == len(set(target_ids))
    assert first["batches"][-1]["source_text_character_count"] == 24_000


def test_context_only_is_adjacent_and_never_a_target_in_same_batch() -> None:
    manifest = build_batch_manifest(_document_export([("A", 3, 10), ("B", 9, 10)]))

    assert manifest["batches"][0]["context_node_ids"] == ["node-004"]
    assert manifest["batches"][1]["context_node_ids"] == ["node-003", "node-012"]
    assert manifest["batches"][2]["context_node_ids"] == ["node-011"]
    for batch in manifest["batches"]:
        assert set(batch["target_node_ids"]).isdisjoint(batch["context_node_ids"])


@pytest.mark.parametrize(
    "model_name",
    ["", "   ", "frontier-visible-model-to-be-filled", "MODEL_TO_BE_FILLED"],
)
def test_package_writer_rejects_missing_or_placeholder_model(
    tmp_path: Path, model_name: str
) -> None:
    with pytest.raises(BatchPipelineError, match="Modellbezeichnung"):
        write_statement_candidate_batch_packages(
            _document_export([("A", 1, 10)]),
            output_dir=tmp_path,
            model_name=model_name,
        )


def test_package_writer_binds_target_and_context_nodes(tmp_path: Path) -> None:
    document_export = _document_export([("A", 3, 10), ("B", 1, 10)])
    result = write_statement_candidate_batch_packages(
        document_export,
        output_dir=tmp_path,
        model_name="frontier-visible-model-2026-08",
        max_target_nodes=3,
    )

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    first_input = json.loads(
        (tmp_path / "batch_001" / "input_package.json").read_text(encoding="utf-8")
    )
    first_request = json.loads(
        (tmp_path / "batch_001" / "request_payload.json").read_text(encoding="utf-8")
    )
    assert first_input["prompt_version"] == "statement_candidates_frontier_v2"
    assert [node["node_role"] for node in first_input["source_nodes"]] == [
        "target",
        "target",
        "target",
        "context_only",
    ]
    assert first_request["model"] == "frontier-visible-model-2026-08"
    assert first_request["metadata"]["batch_sha256"] == manifest["batches"][0][
        "batch_sha256"
    ]
    assert first_request["metadata"]["input_sha256"] == hashlib.sha256(
        (tmp_path / "batch_001" / "input_package.json").read_bytes()
    ).hexdigest()
    assert first_request["metadata"]["prompt_sha256"] == hashlib.sha256(
        (tmp_path / "batch_001" / "prompt.md").read_bytes()
    ).hexdigest()
    assert result.manifest_sha256 == manifest["manifest_sha256"]


def test_validation_allows_multiple_candidates_but_rejects_context_source(
    tmp_path: Path,
) -> None:
    document_export = _document_export([("A", 1, 20), ("B", 1, 20)])
    result = write_statement_candidate_batch_packages(
        document_export,
        output_dir=tmp_path,
        model_name="frontier-visible-model-2026-08",
        max_target_nodes=1,
    )
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    package = _batch_output(tmp_path, manifest, batch_number=1, candidate_count=2)

    validate_statement_candidate_batch_output(
        package,
        manifest=manifest,
        package_dir=tmp_path / "batch_001",
    )
    package["candidates"][0]["document_node_id"] = "node-002"
    with pytest.raises(BatchPipelineError, match="Kontextknoten"):
        validate_statement_candidate_batch_output(
            package,
            manifest=manifest,
            package_dir=tmp_path / "batch_001",
        )


def test_validation_rejects_non_citable_source_and_cross_batch_candidate_id(
    tmp_path: Path,
) -> None:
    document_export = _document_export([("A", 2, 20)], include_non_citable=True)
    result = write_statement_candidate_batch_packages(
        document_export,
        output_dir=tmp_path,
        model_name="frontier-visible-model-2026-08",
        max_target_nodes=1,
    )
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    first = _batch_output(tmp_path, manifest, batch_number=1)
    second = _batch_output(tmp_path, manifest, batch_number=2)

    first["candidates"][0]["document_node_id"] = "node-non-citable"
    with pytest.raises(BatchPipelineError, match="Zielknoten"):
        validate_statement_candidate_batch_output(
            first, manifest=manifest, package_dir=tmp_path / "batch_001"
        )
    second["candidates"][0]["candidate_id"] = "batch_001:candidate_001"
    second["node_processing"][0]["candidate_ids"] = ["batch_001:candidate_001"]
    with pytest.raises(BatchPipelineError, match="Batchpräfix"):
        validate_statement_candidate_batch_output(
            second, manifest=manifest, package_dir=tmp_path / "batch_002"
        )


def test_validation_rejects_obsolete_graphic_page_23_caption(tmp_path: Path) -> None:
    document_export = _document_export([("5.2.5", 1, 30)])
    node = document_export["nodes"][0]
    node["page_start"] = 23
    node["page_end"] = 23
    node["locator"] = "page=23;section=5.2.5"
    node["source_text"] = "0,30 m ueber Dach gefuehrt"
    node_without_hash = dict(node)
    node_without_hash.pop("content_sha256")
    node["content_sha256"] = _sha(node_without_hash)
    package_without_hash = dict(document_export)
    package_without_hash.pop("package_sha256")
    document_export["package_sha256"] = _sha(package_without_hash)
    result = write_statement_candidate_batch_packages(
        document_export,
        output_dir=tmp_path,
        model_name="frontier-visible-model-2026-08",
    )
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    package = _batch_output(tmp_path, manifest, batch_number=1)

    with pytest.raises(BatchPipelineError, match="grafische Beschriftung"):
        validate_statement_candidate_batch_output(
            package, manifest=manifest, package_dir=tmp_path / "batch_001"
        )


@pytest.mark.parametrize("field", ["input_sha256", "prompt_sha256", "batch_sha256"])
def test_validation_rejects_wrong_bindings(tmp_path: Path, field: str) -> None:
    result = write_statement_candidate_batch_packages(
        _document_export([("A", 1, 20)]),
        output_dir=tmp_path,
        model_name="frontier-visible-model-2026-08",
    )
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    package = _batch_output(tmp_path, manifest, batch_number=1)
    package["batch_binding"][field] = "0" * 64

    with pytest.raises(BatchPipelineError, match=field):
        validate_statement_candidate_batch_output(
            package, manifest=manifest, package_dir=tmp_path / "batch_001"
        )


def test_merge_is_deterministic_and_preserves_raw_outputs_exactly(tmp_path: Path) -> None:
    result = write_statement_candidate_batch_packages(
        _document_export([("A", 2, 20)]),
        output_dir=tmp_path / "packages",
        model_name="frontier-visible-model-2026-08",
        max_target_nodes=1,
    )
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    records = []
    original_raw = []
    for number in (1, 2):
        package = _batch_output(tmp_path / "packages", manifest, batch_number=number)
        raw = json.dumps(package, ensure_ascii=False, indent=2) + "\n"
        raw_path = tmp_path / f"frontier_raw_output_batch_{number:03d}.json"
        raw_path.write_text(raw, encoding="utf-8", newline="")
        records.append(build_batch_output_receipt(raw_path))
        original_raw.append(raw)

    first = merge_statement_candidate_batch_outputs(
        manifest=manifest,
        package_root=tmp_path / "packages",
        output_receipts=records,
    )
    second = merge_statement_candidate_batch_outputs(
        manifest=manifest,
        package_root=tmp_path / "packages",
        output_receipts=copy.deepcopy(records),
    )

    assert first == second
    assert [item["raw_output_json"] for item in first["raw_outputs"]] == original_raw
    assert len(first["node_processing"]) == 2
    assert len(first["candidates"]) == 2


def test_merge_rejects_missing_duplicate_swapped_and_bad_raw_checksum(
    tmp_path: Path,
) -> None:
    result = write_statement_candidate_batch_packages(
        _document_export([("A", 2, 20)]),
        output_dir=tmp_path / "packages",
        model_name="frontier-visible-model-2026-08",
        max_target_nodes=1,
    )
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    records = []
    for number in (1, 2):
        package = _batch_output(tmp_path / "packages", manifest, batch_number=number)
        raw_path = tmp_path / f"raw-{number}.json"
        raw_path.write_text(json.dumps(package), encoding="utf-8")
        records.append(build_batch_output_receipt(raw_path))

    for invalid in (
        records[:1],
        [records[0], records[0]],
        list(reversed(records)),
    ):
        with pytest.raises(BatchPipelineError):
            merge_statement_candidate_batch_outputs(
                manifest=manifest,
                package_root=tmp_path / "packages",
                output_receipts=invalid,
            )
    bad_checksum = copy.deepcopy(records)
    bad_checksum[0]["raw_output_sha256"] = "0" * 64
    with pytest.raises(BatchPipelineError, match="Rohoutputprüfsumme"):
        merge_statement_candidate_batch_outputs(
            manifest=manifest,
            package_root=tmp_path / "packages",
            output_receipts=bad_checksum,
        )


def _document_export(
    sections: list[tuple[str, int, int]], *, include_non_citable: bool = False
) -> dict[str, object]:
    nodes: list[dict[str, object]] = []
    rank = 1
    page = 1
    for section, count, chars in sections:
        for _ in range(count):
            text = (f"{section}-{page} ".ljust(chars, "x"))[:chars]
            node = {
                "node_id": f"node-{page:03d}",
                "node_path": f"/sections/{section}/pages/{page}",
                "parent_node_id": f"section-{section}",
                "rank": rank,
                "element_type": "page_text",
                "page_start": page,
                "page_end": page,
                "section_number": section,
                "heading": f"Section {section}",
                "locator": f"page={page};section={section}",
                "is_citable": True,
                "source_text": text,
            }
            node["content_sha256"] = _sha(node)
            nodes.append(node)
            rank += 1
            page += 1
    if include_non_citable:
        node = {
            "node_id": "node-non-citable",
            "node_path": "/excluded",
            "parent_node_id": None,
            "rank": rank,
            "element_type": "non_citable_text",
            "page_start": page,
            "page_end": page,
            "section_number": None,
            "heading": None,
            "locator": f"page={page}",
            "is_citable": False,
            "source_text": "excluded",
        }
        node["content_sha256"] = _sha(node)
        nodes.append(node)
    package: dict[str, object] = {
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
    package["package_sha256"] = _sha(package)
    return package


def _batch_output(
    package_root: Path,
    manifest: dict[str, object],
    *,
    batch_number: int,
    candidate_count: int = 1,
) -> dict[str, object]:
    batch = manifest["batches"][batch_number - 1]
    package_dir = package_root / f"batch_{batch_number:03d}"
    request = json.loads((package_dir / "request_payload.json").read_text(encoding="utf-8"))
    binding = request["metadata"]["response_binding"]
    target_id = batch["target_node_ids"][0]
    input_package = json.loads((package_dir / "input_package.json").read_text(encoding="utf-8"))
    source_text = next(
        node["source_text"]
        for node in input_package["source_nodes"]
        if node["document_node_id"] == target_id
    )
    candidate_ids = [
        f"batch_{batch_number:03d}:candidate_{index:03d}"
        for index in range(1, candidate_count + 1)
    ]
    candidates = []
    for candidate_id in candidate_ids:
        candidates.append(
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
        )
    return {
        "package_version": "statement_candidates_batch_v2",
        "contract_version": "statement_candidates_batch_v2",
        "batch_binding": binding,
        "node_processing": [
            {
                "document_node_id": node_id,
                "status": (
                    "processed_with_candidates"
                    if node_id == target_id
                    else "processed_without_candidate"
                ),
                "candidate_ids": candidate_ids if node_id == target_id else [],
            }
            for node_id in batch["target_node_ids"]
        ],
        "candidates": candidates,
        "relations": [],
    }


def _sha(value: object) -> str:
    serialized = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
