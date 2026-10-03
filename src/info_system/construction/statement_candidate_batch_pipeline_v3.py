from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from info_system.construction.statement_candidate_batch_pipeline import (
    MAX_SOURCE_TEXT_CHARACTERS,
    MAX_TARGET_NODES,
    BatchPipelineError,
    build_batch_manifest,
)

BATCH_MANIFEST_VERSION = "statement_candidate_batch_manifest_v3"
BATCH_INPUT_VERSION = "statement_candidate_batch_input_v3"
BATCH_OUTPUT_VERSION = "statement_candidates_batch_v3"
CONTRACT_VERSION = "statement_candidates_batch_v3"
PROMPT_VERSION = "statement_candidates_frontier_v3"

_FORBIDDEN_SHORT_FIELDS = (
    "processing_status",
    "source_node_id",
    "evidence_quote",
    "statement_text",
)
_PLACEHOLDER_PARTS = (
    "to-be-filled",
    "to_be_filled",
    "model-to-be-filled",
    "model_to_be_filled",
    "placeholder",
)
_OBSOLETE_PAGE_23_CAPTIONS = (
    "0,30 m über dach geführt",
    "0,30 m ueber dach gefuehrt",
    "0,30 m uber dach gefuhrt",
)


@dataclass(frozen=True)
class BatchPackageV3Result:
    output_dir: Path
    manifest_path: Path
    manifest_sha256: str
    input_path: Path
    input_sha256: str
    prompt_path: Path
    prompt_sha256: str
    request_payload_path: Path
    request_payload_sha256: str
    raw_output_path: Path


def build_statement_candidate_batch_v3_manifest(
    document_nodes_export: dict[str, Any],
    *,
    max_target_nodes: int = MAX_TARGET_NODES,
    max_source_text_characters: int = MAX_SOURCE_TEXT_CHARACTERS,
) -> dict[str, Any]:
    """Build a v3 manifest while retaining the deterministic v2 batch partition."""
    base = build_batch_manifest(
        document_nodes_export,
        max_target_nodes=max_target_nodes,
        max_source_text_characters=max_source_text_characters,
    )
    manifest = dict(base)
    manifest.pop("manifest_sha256")
    manifest["manifest_version"] = BATCH_MANIFEST_VERSION
    manifest["contract_version"] = CONTRACT_VERSION
    manifest["prompt_version"] = PROMPT_VERSION
    manifest["manifest_sha256"] = _sha256_json(manifest)
    _validate_v3_manifest(manifest)
    return manifest


def write_statement_candidate_batch_v3_package(
    document_nodes_export: dict[str, Any],
    *,
    output_dir: str | Path,
    model_name: str,
    batch_number: int = 1,
    max_target_nodes: int = MAX_TARGET_NODES,
    max_source_text_characters: int = MAX_SOURCE_TEXT_CHARACTERS,
) -> BatchPackageV3Result:
    """Materialize exactly one self-contained v3 Frontier batch package."""
    checked_model_name = _validated_model_name(model_name)
    manifest = build_statement_candidate_batch_v3_manifest(
        document_nodes_export,
        max_target_nodes=max_target_nodes,
        max_source_text_characters=max_source_text_characters,
    )
    if batch_number < 1 or batch_number > manifest["batch_count"]:
        raise BatchPipelineError("unbekannte Batchnummer für v3-Paket")
    batch = manifest["batches"][batch_number - 1]

    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "batch_manifest.json"
    _write_canonical_json(manifest_path, manifest)

    batch_dir = root / batch["batch_id"]
    batch_dir.mkdir(parents=True, exist_ok=True)
    nodes_by_id = {node["node_id"]: node for node in document_nodes_export["nodes"]}
    input_package = _build_input_package(
        document_nodes_export=document_nodes_export,
        manifest=manifest,
        batch=batch,
        nodes_by_id=nodes_by_id,
    )
    input_path = batch_dir / "input_package.json"
    _write_canonical_json(input_path, input_package)
    input_sha256 = _sha256_bytes(input_path.read_bytes())

    prompt_text = _render_prompt(input_package)
    prompt_path = batch_dir / "prompt.md"
    prompt_path.write_text(prompt_text, encoding="utf-8", newline="")
    prompt_sha256 = _sha256_bytes(prompt_path.read_bytes())

    response_binding = _response_binding(
        document_nodes_export=document_nodes_export,
        manifest=manifest,
        batch=batch,
        model_name=checked_model_name,
        input_sha256=input_sha256,
        prompt_sha256=prompt_sha256,
    )
    request_payload = _request_payload(
        model_name=checked_model_name,
        prompt_text=prompt_text,
        response_binding=response_binding,
    )
    request_payload_path = batch_dir / "request_payload.json"
    _write_canonical_json(request_payload_path, request_payload)
    request_payload_sha256 = _sha256_bytes(request_payload_path.read_bytes())

    return BatchPackageV3Result(
        output_dir=root,
        manifest_path=manifest_path,
        manifest_sha256=manifest["manifest_sha256"],
        input_path=input_path,
        input_sha256=input_sha256,
        prompt_path=prompt_path,
        prompt_sha256=prompt_sha256,
        request_payload_path=request_payload_path,
        request_payload_sha256=request_payload_sha256,
        raw_output_path=(
            root.parent
            / "frontier_batch_outputs_v3"
            / f"frontier_raw_output_{batch['batch_id']}.json"
        ),
    )


def load_statement_candidate_batch_v3_schema() -> dict[str, Any]:
    schema = _read_json_object(
        _repo_root()
        / "docs"
        / "extraction_contracts"
        / "schemas"
        / "statement_candidates_batch_v3.schema.json"
    )
    if _find_key(schema, "$ref"):
        raise BatchPipelineError("v3-Ausgabeschema darf keine Referenzen enthalten")
    return schema


def statement_candidate_batch_v3_output_validator() -> Draft202012Validator:
    schema = load_statement_candidate_batch_v3_schema()
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def build_statement_candidate_batch_v3_output_skeleton() -> dict[str, Any]:
    return {
        "package_version": BATCH_OUTPUT_VERSION,
        "contract_version": CONTRACT_VERSION,
        "batch_binding": {
            "project_id": "<copy_from_binding>",
            "document_id": "<copy_from_binding>",
            "document_version_id": "<copy_from_binding>",
            "document_sha256": "0" * 64,
            "batch_manifest_sha256": "0" * 64,
            "batch_id": "batch_000",
            "batch_number": 1,
            "total_batches": 1,
            "target_node_ids": [],
            "context_node_ids": [],
            "model_name": "<copy_from_binding>",
            "prompt_version": PROMPT_VERSION,
            "input_sha256": "0" * 64,
            "prompt_sha256": "0" * 64,
            "batch_sha256": "0" * 64,
        },
        "node_processing": [
            {
                "document_node_id": "<target_document_node_id>",
                "status": "processed_without_candidate",
                "candidate_ids": [],
            }
        ],
        "candidates": [
            {
                "candidate_id": "<batch_id>:candidate_<nnn>",
                "document_node_id": "<target_document_node_id>",
                "evidence": {
                    "source_text": "<exact_source_text_excerpt>",
                    "char_start": None,
                    "char_end": None,
                    "source_role": "primary",
                },
                "statement_type": "<allowed_statement_type>",
                "statement_subtype": "<allowed_statement_subtype>",
                "normalized_text": "<statement_supported_by_evidence>",
                "binding_status": "unknown",
                "lifecycle_status": "active",
                "review_status": "needs_review",
                "action_state": None,
                "output_constraint": None,
                "objects": [
                    {
                        "object_id": "<candidate_local_object_id>",
                        "entity_type": "<allowed_entity_type>",
                        "canonical_label": "<source_supported_object_label>",
                        "aliases": [],
                        "role": "<allowed_object_role>",
                    }
                ],
                "values": [
                    {
                        "value_name": "<allowed_value_name>",
                        "raw_value": "<exact_source_value>",
                        "text_value": "<source_supported_text_value>",
                        "numeric_value": None,
                        "unit": None,
                        "comparison_operator": "<allowed_comparison_operator>",
                        "is_approximate": False,
                        "spatial_reference": None,
                        "value_order": 1,
                    }
                ],
                "conditions": [
                    {
                        "condition_text": "<source_supported_condition>",
                        "parameters": {},
                    }
                ],
                "classifications": [
                    {
                        "scheme": "<classification_scheme>",
                        "code": "<classification_code>",
                        "label": None,
                        "role": "<allowed_classification_role>",
                    }
                ],
                "groups": [
                    {
                        "group_id": "<candidate_local_group_id>",
                        "group_type": "<allowed_group_type>",
                        "label": "<source_supported_group_label>",
                        "member_role": "<allowed_member_role>",
                        "member_rank": 1,
                    }
                ],
                "relations": [{"note": "<source_supported_local_relation_note>"}],
                "document_references": [
                    {
                        "reference_text": "<exact_source_reference>",
                        "reference_type": "<allowed_reference_type>",
                    }
                ],
                "review_notes": [],
                "validation_warnings": [],
            }
        ],
        "relations": [
            {
                "relation_id": "<batch_local_relation_id>",
                "source_candidate_id": "<existing_source_candidate_id>",
                "target_candidate_id": "<existing_target_candidate_id>",
                "relation_type": "<allowed_relation_type>",
            }
        ],
    }


def validate_statement_candidate_batch_v3_output(
    candidate_package: dict[str, Any],
    *,
    manifest: dict[str, Any],
    package_dir: str | Path,
) -> None:
    _validate_v3_manifest(manifest)
    statement_candidate_batch_v3_output_validator().validate(candidate_package)
    binding = candidate_package["batch_binding"]
    number = binding["batch_number"]
    if number < 1 or number > manifest["batch_count"]:
        raise BatchPipelineError("unbekannte Batchnummer")
    batch = manifest["batches"][number - 1]
    directory = Path(package_dir)
    request = _read_json_object(directory / "request_payload.json")
    expected_binding = request.get("metadata", {}).get("response_binding")
    if not isinstance(expected_binding, dict):
        raise BatchPipelineError("request_payload enthält keine response_binding")
    if binding != expected_binding:
        for field, expected in expected_binding.items():
            if binding.get(field) != expected:
                raise BatchPipelineError(f"Batchausgabe passt nicht zu {field}")
        raise BatchPipelineError("Batchausgabe enthält widersprüchliche Metadaten")
    _validated_model_name(str(binding["model_name"]))
    if binding["prompt_version"] != PROMPT_VERSION:
        raise BatchPipelineError("falsche Promptversion")
    if binding["input_sha256"] != _sha256_bytes(
        (directory / "input_package.json").read_bytes()
    ):
        raise BatchPipelineError("falsche input_sha256")
    if binding["prompt_sha256"] != _sha256_bytes(
        (directory / "prompt.md").read_bytes()
    ):
        raise BatchPipelineError("falsche prompt_sha256")
    if binding["batch_sha256"] != batch["batch_sha256"]:
        raise BatchPipelineError("falsche batch_sha256")
    if binding["batch_manifest_sha256"] != manifest["manifest_sha256"]:
        raise BatchPipelineError("falsche batch_manifest_sha256")

    target_ids = list(batch["target_node_ids"])
    context_ids = set(batch["context_node_ids"])
    processing = candidate_package["node_processing"]
    if [item["document_node_id"] for item in processing] != target_ids:
        raise BatchPipelineError(
            "node_processing muss genau die Zielknoten in Manifestreihenfolge enthalten"
        )
    input_package = _read_json_object(directory / "input_package.json")
    source_nodes = {
        node["document_node_id"]: node for node in input_package["source_nodes"]
    }
    candidates_by_id: dict[str, dict[str, Any]] = {}
    prefix = f"{batch['batch_id']}:"
    for candidate in candidate_package["candidates"]:
        candidate_id = candidate["candidate_id"]
        if not candidate_id.startswith(prefix):
            raise BatchPipelineError("candidate_id verwendet nicht das erwartete Batchpräfix")
        if candidate_id in candidates_by_id:
            raise BatchPipelineError(f"doppelte candidate_id: {candidate_id}")
        candidates_by_id[candidate_id] = candidate
        node_id = candidate["document_node_id"]
        if node_id in context_ids:
            raise BatchPipelineError("Kandidat darf keinen Kontextknoten als Quelle verwenden")
        if node_id not in target_ids:
            raise BatchPipelineError("Kandidat darf nur einen Zielknoten als Quelle verwenden")
        evidence = candidate["evidence"]
        source_text = source_nodes[node_id]["source_text"]
        excerpt = evidence["source_text"]
        if excerpt not in source_text:
            raise BatchPipelineError("Evidenz ist keine exakte Passage aus source_text")
        if _is_obsolete_page_23_caption(source_nodes[node_id], excerpt):
            raise BatchPipelineError(
                "überholte grafische Beschriftung auf Seite 23 ist keine Quelle"
            )
        start = evidence["char_start"]
        end = evidence["char_end"]
        if (start is None) != (end is None):
            raise BatchPipelineError(
                "Evidenzposition muss vollständig oder vollständig null sein"
            )
        if start is not None and end is not None and source_text[start:end] != excerpt:
            raise BatchPipelineError("Evidenzposition passt nicht zur exakten source_text")

    referenced: list[str] = []
    for item in processing:
        ids = item["candidate_ids"]
        if item["status"] == "processed_with_candidates" and not ids:
            raise BatchPipelineError("processed_with_candidates erfordert candidate_ids")
        if item["status"] == "processed_without_candidate" and ids:
            raise BatchPipelineError(
                "processed_without_candidate darf keine candidate_ids enthalten"
            )
        for candidate_id in ids:
            candidate = candidates_by_id.get(candidate_id)
            if candidate is None:
                raise BatchPipelineError("node_processing referenziert unbekannte candidate_id")
            if candidate["document_node_id"] != item["document_node_id"]:
                raise BatchPipelineError("candidate_id ist dem falschen Zielknoten zugeordnet")
            referenced.append(candidate_id)
    if sorted(referenced) != sorted(candidates_by_id):
        raise BatchPipelineError("jeder Kandidat muss genau einem Zielknoten zugeordnet sein")
    if len(referenced) != len(set(referenced)):
        raise BatchPipelineError("Kandidat wird mehrfach in node_processing referenziert")

    for relation in candidate_package["relations"]:
        source_id = relation["source_candidate_id"]
        target_id = relation["target_candidate_id"]
        if source_id not in candidates_by_id or target_id not in candidates_by_id:
            raise BatchPipelineError("Relation darf nur Kandidaten desselben Batches referenzieren")
        if source_id == target_id:
            raise BatchPipelineError("Selbstrelation ist nicht zulässig")


def _build_input_package(
    *,
    document_nodes_export: dict[str, Any],
    manifest: dict[str, Any],
    batch: dict[str, Any],
    nodes_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    target_ids = set(batch["target_node_ids"])
    selected_ids = target_ids | set(batch["context_node_ids"])
    source_nodes = []
    for node in sorted(
        (nodes_by_id[node_id] for node_id in selected_ids), key=lambda item: item["rank"]
    ):
        source_nodes.append(
            {
                "document_node_id": node["node_id"],
                "node_rank": node["rank"],
                "node_role": "target" if node["node_id"] in target_ids else "context_only",
                "node_path": node["node_path"],
                "page": node["page_start"],
                "section_number": node["section_number"],
                "heading": node["heading"],
                "locator": node["locator"],
                "source_text": node["source_text"],
                "content_sha256": node["content_sha256"],
            }
        )
    package: dict[str, Any] = {
        "package_version": BATCH_INPUT_VERSION,
        "contract_version": CONTRACT_VERSION,
        "prompt_version": PROMPT_VERSION,
        "project_id": document_nodes_export["project_id"],
        "document_id": document_nodes_export["document_id"],
        "document_version_id": document_nodes_export["document_version_id"],
        "document_sha256": document_nodes_export["document_sha256"],
        "document_nodes_package_sha256": document_nodes_export["package_sha256"],
        "batch_manifest_sha256": manifest["manifest_sha256"],
        "batch_id": batch["batch_id"],
        "batch_number": batch["batch_number"],
        "total_batches": batch["total_batches"],
        "batch_sha256": batch["batch_sha256"],
        "target_node_ids": batch["target_node_ids"],
        "context_node_ids": batch["context_node_ids"],
        "source_nodes": source_nodes,
        "instructions": {
            "review_status": "needs_review",
            "source_text_only": True,
            "context_only_is_source": False,
            "candidate_id_prefix": f"{batch['batch_id']}:",
            "all_required_fields_must_be_present": True,
            "short_fields_forbidden": list(_FORBIDDEN_SHORT_FIELDS),
        },
    }
    package["package_sha256"] = _sha256_json(package)
    input_schema = _read_json_object(
        _repo_root()
        / "schemas"
        / "construction"
        / "statement_candidate_batch_input_v3.schema.json"
    )
    Draft202012Validator.check_schema(input_schema)
    Draft202012Validator(input_schema).validate(package)
    return package


def _render_prompt(input_package: dict[str, Any]) -> str:
    root = _repo_root()
    template = (root / "prompts" / "statement_candidates_frontier_v3.md").read_text(
        encoding="utf-8"
    )
    contract = (
        root / "docs" / "extraction_contracts" / "statement_candidates_batch_v3.md"
    ).read_text(encoding="utf-8")
    schema = load_statement_candidate_batch_v3_schema()
    skeleton = build_statement_candidate_batch_v3_output_skeleton()
    rendered = (
        template.replace("{{CONTRACT_MARKDOWN}}", contract)
        .replace("{{JSON_SCHEMA}}", _canonical_json(schema))
        .replace("{{OUTPUT_SKELETON}}", _canonical_json(skeleton))
        .replace("{{INPUT_PACKAGE}}", _canonical_json(input_package))
    )
    unresolved = (
        "{{CONTRACT_MARKDOWN}}",
        "{{JSON_SCHEMA}}",
        "{{OUTPUT_SKELETON}}",
        "{{INPUT_PACKAGE}}",
    )
    if any(marker in rendered for marker in unresolved):
        raise BatchPipelineError("unaufgelöster Platzhalter im v3-Prompt")
    return rendered


def _response_binding(
    *,
    document_nodes_export: dict[str, Any],
    manifest: dict[str, Any],
    batch: dict[str, Any],
    model_name: str,
    input_sha256: str,
    prompt_sha256: str,
) -> dict[str, Any]:
    return {
        "project_id": document_nodes_export["project_id"],
        "document_id": document_nodes_export["document_id"],
        "document_version_id": document_nodes_export["document_version_id"],
        "document_sha256": document_nodes_export["document_sha256"],
        "batch_manifest_sha256": manifest["manifest_sha256"],
        "batch_id": batch["batch_id"],
        "batch_number": batch["batch_number"],
        "total_batches": batch["total_batches"],
        "target_node_ids": batch["target_node_ids"],
        "context_node_ids": batch["context_node_ids"],
        "model_name": model_name,
        "prompt_version": PROMPT_VERSION,
        "input_sha256": input_sha256,
        "prompt_sha256": prompt_sha256,
        "batch_sha256": batch["batch_sha256"],
    }


def _request_payload(
    *, model_name: str, prompt_text: str, response_binding: dict[str, Any]
) -> dict[str, Any]:
    schema_sha256 = _sha256_text(
        _canonical_json(load_statement_candidate_batch_v3_schema())
    )
    return {
        "model": model_name,
        "temperature": 0,
        "top_p": 1,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Gib exakt ein JSON-Objekt gemäß dem vollständig eingebetteten "
                    "Schema aus. Verwende kein Markdown und keine erläuternde Prosa. "
                    "Lasse kein Pflichtfeld aus und verwende keine Kurzfelder. Kopiere "
                    "batch_binding exakt aus der abschließenden Bindungsnachricht."
                ),
            },
            {"role": "user", "content": prompt_text},
            {
                "role": "user",
                "content": (
                    "Verbindliche batch_binding für die Ausgabe; vollständig und exakt "
                    "kopieren, Werte nicht erraten, umbenennen oder verändern:\n"
                    + _canonical_json(response_binding)
                ),
            },
        ],
        "metadata": {
            "contract_version": CONTRACT_VERSION,
            "prompt_version": PROMPT_VERSION,
            "batch_id": response_binding["batch_id"],
            "batch_sha256": response_binding["batch_sha256"],
            "input_sha256": response_binding["input_sha256"],
            "prompt_sha256": response_binding["prompt_sha256"],
            "output_schema_sha256": schema_sha256,
            "self_contained": True,
            "response_binding": response_binding,
        },
    }


def _validate_v3_manifest(manifest: dict[str, Any]) -> None:
    if manifest.get("manifest_version") != BATCH_MANIFEST_VERSION:
        raise BatchPipelineError("unbekannte v3-Batchmanifest-Version")
    if manifest.get("contract_version") != CONTRACT_VERSION:
        raise BatchPipelineError("falsche v3-Vertragsversion im Manifest")
    if manifest.get("prompt_version") != PROMPT_VERSION:
        raise BatchPipelineError("falsche v3-Promptversion im Manifest")
    expected = dict(manifest)
    checksum = expected.pop("manifest_sha256", None)
    if checksum != _sha256_json(expected):
        raise BatchPipelineError("falsche v3-Batchmanifest-Prüfsumme")
    if manifest.get("batch_count") != len(manifest.get("batches", [])):
        raise BatchPipelineError("widersprüchliche Batchanzahl")
    seen: set[str] = set()
    for number, batch in enumerate(manifest["batches"], start=1):
        if batch["batch_number"] != number or batch["batch_id"] != f"batch_{number:03d}":
            raise BatchPipelineError("vertauschte Batches im Manifest")
        expected_batch = dict(batch)
        batch_sha = expected_batch.pop("batch_sha256", None)
        if batch_sha != _sha256_json(expected_batch):
            raise BatchPipelineError("falsche Batchprüfsumme")
        targets = batch["target_node_ids"]
        if seen.intersection(targets):
            raise BatchPipelineError("doppelte Zielknoten im Manifest")
        seen.update(targets)
        if set(targets).intersection(batch["context_node_ids"]):
            raise BatchPipelineError("Ziel- und Kontextknoten überschneiden sich")
    if len(seen) != manifest["citable_target_node_count"]:
        raise BatchPipelineError("unvollständige Zielknotenabdeckung")


def _validated_model_name(model_name: str) -> str:
    stripped = model_name.strip()
    normalized = stripped.casefold().replace(" ", "-")
    if not stripped or any(part in normalized for part in _PLACEHOLDER_PARTS):
        raise BatchPipelineError(
            "tatsächlich sichtbare Modellbezeichnung ist zwingend erforderlich"
        )
    return stripped


def _is_obsolete_page_23_caption(node: dict[str, Any], excerpt: str) -> bool:
    if node["page"] != 23:
        return False
    normalized = " ".join(excerpt.casefold().split())
    return any(caption in normalized for caption in _OBSOLETE_PAGE_23_CAPTIONS)


def _find_key(value: Any, key: str) -> bool:
    if isinstance(value, dict):
        return key in value or any(_find_key(child, key) for child in value.values())
    if isinstance(value, list):
        return any(_find_key(child, key) for child in value)
    return False


def _read_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BatchPipelineError(f"JSON-Objekt erwartet: {path}")
    return value


def _write_canonical_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(_canonical_json(value) + "\n", encoding="utf-8", newline="")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _sha256_json(value: dict[str, Any]) -> str:
    return _sha256_text(_canonical_json(value))


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
