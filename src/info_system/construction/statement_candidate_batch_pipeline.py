from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from info_system.construction.document_pipeline import validate_export_package

BATCH_MANIFEST_VERSION = "statement_candidate_batch_manifest_v1"
BATCH_INPUT_VERSION = "statement_candidate_batch_input_v2"
BATCH_OUTPUT_VERSION = "statement_candidates_batch_v2"
MERGED_OUTPUT_VERSION = "statement_candidates_merged_v2"
CONTRACT_VERSION = "statement_candidates_batch_v2"
PROMPT_VERSION = "statement_candidates_frontier_v2"
MAX_TARGET_NODES = 8
MAX_SOURCE_TEXT_CHARACTERS = 24_000

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


class BatchPipelineError(RuntimeError):
    pass


@dataclass(frozen=True)
class BatchPackageResult:
    output_dir: Path
    manifest_path: Path
    manifest_sha256: str
    request_payload_paths: tuple[Path, ...]
    raw_output_paths: tuple[Path, ...]


def build_batch_manifest(
    document_nodes_export: dict[str, Any],
    *,
    max_target_nodes: int = MAX_TARGET_NODES,
    max_source_text_characters: int = MAX_SOURCE_TEXT_CHARACTERS,
) -> dict[str, Any]:
    """Build a deterministic, section-aware manifest over citable page nodes."""
    validate_export_package(document_nodes_export)
    if max_target_nodes < 1 or max_target_nodes > MAX_TARGET_NODES:
        raise BatchPipelineError("max_target_nodes muss zwischen 1 und 8 liegen")
    if (
        max_source_text_characters < 1
        or max_source_text_characters > MAX_SOURCE_TEXT_CHARACTERS
    ):
        raise BatchPipelineError(
            "max_source_text_characters muss zwischen 1 und 24000 liegen"
        )

    citable = [
        node
        for node in sorted(document_nodes_export["nodes"], key=lambda item: item["rank"])
        if node["is_citable"] and node["element_type"] == "page_text"
    ]
    if not citable:
        raise BatchPipelineError("keine zitierfähigen Seitenknoten vorhanden")
    for node in citable:
        if not node["source_text"]:
            raise BatchPipelineError(
                f"zitierfähiger Zielknoten ohne source_text: {node['node_id']}"
            )
        if len(node["source_text"]) > max_source_text_characters:
            raise BatchPipelineError(
                f"einzelner Zielknoten überschreitet Zeichenlimit: {node['node_id']}"
            )

    sections = _consecutive_sections(citable)
    target_groups: list[list[dict[str, Any]]] = []
    pending: list[dict[str, Any]] = []
    for section_nodes in sections:
        oversized = not _fits(
            section_nodes,
            max_target_nodes=max_target_nodes,
            max_characters=max_source_text_characters,
        )
        if oversized:
            if pending:
                target_groups.append(pending)
                pending = []
            target_groups.extend(
                _split_pages(
                    section_nodes,
                    max_target_nodes=max_target_nodes,
                    max_characters=max_source_text_characters,
                )
            )
        elif _fits(
            pending + section_nodes,
            max_target_nodes=max_target_nodes,
            max_characters=max_source_text_characters,
        ):
            pending.extend(section_nodes)
        else:
            if pending:
                target_groups.append(pending)
            pending = list(section_nodes)
    if pending:
        target_groups.append(pending)

    index_by_id = {node["node_id"]: index for index, node in enumerate(citable)}
    batches = []
    total = len(target_groups)
    for number, targets in enumerate(target_groups, start=1):
        first_index = index_by_id[targets[0]["node_id"]]
        last_index = index_by_id[targets[-1]["node_id"]]
        context = []
        if first_index > 0:
            context.append(citable[first_index - 1])
        if last_index + 1 < len(citable):
            context.append(citable[last_index + 1])
        batch: dict[str, Any] = {
            "batch_id": f"batch_{number:03d}",
            "batch_number": number,
            "total_batches": total,
            "target_node_ids": [node["node_id"] for node in targets],
            "context_node_ids": [node["node_id"] for node in context],
            "section_numbers": _ordered_unique(
                [node["section_number"] for node in targets]
            ),
            "section_headings": _ordered_unique([node["heading"] for node in targets]),
            "page_start": min(node["page_start"] for node in targets),
            "page_end": max(node["page_end"] for node in targets),
            "target_node_count": len(targets),
            "source_text_character_count": sum(
                len(node["source_text"]) for node in targets
            ),
            "target_content_sha256": {
                node["node_id"]: node["content_sha256"] for node in targets
            },
            "context_content_sha256": {
                node["node_id"]: node["content_sha256"] for node in context
            },
        }
        batch["batch_sha256"] = _sha256_json(batch)
        batches.append(batch)

    manifest: dict[str, Any] = {
        "manifest_version": BATCH_MANIFEST_VERSION,
        "project_id": document_nodes_export["project_id"],
        "document_id": document_nodes_export["document_id"],
        "document_version_id": document_nodes_export["document_version_id"],
        "document_sha256": document_nodes_export["document_sha256"],
        "document_nodes_package_sha256": document_nodes_export["package_sha256"],
        "ordering": "document_node.rank",
        "max_target_nodes": max_target_nodes,
        "max_source_text_characters": max_source_text_characters,
        "citable_target_node_count": len(citable),
        "batch_count": len(batches),
        "batches": batches,
    }
    manifest["manifest_sha256"] = _sha256_json(manifest)
    return manifest


def write_statement_candidate_batch_packages(
    document_nodes_export: dict[str, Any],
    *,
    output_dir: str | Path,
    model_name: str,
    max_target_nodes: int = MAX_TARGET_NODES,
    max_source_text_characters: int = MAX_SOURCE_TEXT_CHARACTERS,
) -> BatchPackageResult:
    model_name = _validated_model_name(model_name)
    manifest = build_batch_manifest(
        document_nodes_export,
        max_target_nodes=max_target_nodes,
        max_source_text_characters=max_source_text_characters,
    )
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "batch_manifest.json"
    manifest_path.write_text(
        _canonical_json(manifest) + "\n", encoding="utf-8", newline=""
    )
    nodes_by_id = {node["node_id"]: node for node in document_nodes_export["nodes"]}
    request_paths = []
    raw_paths = []
    for batch in manifest["batches"]:
        batch_dir = root / batch["batch_id"]
        batch_dir.mkdir(parents=True, exist_ok=True)
        input_package = _build_input_package(
            document_nodes_export=document_nodes_export,
            manifest=manifest,
            batch=batch,
            nodes_by_id=nodes_by_id,
        )
        input_text = _canonical_json(input_package) + "\n"
        input_path = batch_dir / "input_package.json"
        input_path.write_text(input_text, encoding="utf-8", newline="")
        prompt_text = _render_prompt(input_package)
        prompt_path = batch_dir / "prompt.md"
        prompt_path.write_text(prompt_text, encoding="utf-8", newline="")
        response_binding = _response_binding(
            document_nodes_export=document_nodes_export,
            manifest=manifest,
            batch=batch,
            model_name=model_name,
            input_sha256=_sha256_text(input_text),
            prompt_sha256=_sha256_text(prompt_text),
        )
        request = _request_payload(
            model_name=model_name,
            prompt_text=prompt_text,
            response_binding=response_binding,
        )
        request_path = batch_dir / "request_payload.json"
        request_path.write_text(
            _canonical_json(request) + "\n", encoding="utf-8", newline=""
        )
        request_paths.append(request_path)
        raw_paths.append(
            root.parent
            / "frontier_batch_outputs"
            / f"frontier_raw_output_{batch['batch_id']}.json"
        )
    return BatchPackageResult(
        output_dir=root,
        manifest_path=manifest_path,
        manifest_sha256=manifest["manifest_sha256"],
        request_payload_paths=tuple(request_paths),
        raw_output_paths=tuple(raw_paths),
    )


def validate_statement_candidate_batch_output(
    candidate_package: dict[str, Any],
    *,
    manifest: dict[str, Any],
    package_dir: str | Path,
) -> None:
    _validate_manifest(manifest)
    _batch_output_validator().validate(candidate_package)
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
    for field, expected in expected_binding.items():
        if binding.get(field) != expected:
            raise BatchPipelineError(f"Batchausgabe passt nicht zu {field}")
    if binding != expected_binding:
        raise BatchPipelineError("Batchausgabe enthält widersprüchliche Metadaten")
    _validated_model_name(str(binding["model_name"]))
    input_text = (directory / "input_package.json").read_text(encoding="utf-8")
    prompt_text = (directory / "prompt.md").read_text(encoding="utf-8")
    if binding["input_sha256"] != _sha256_text(input_text):
        raise BatchPipelineError("falsche input_sha256")
    if binding["prompt_sha256"] != _sha256_text(prompt_text):
        raise BatchPipelineError("falsche prompt_sha256")
    if binding["batch_sha256"] != batch["batch_sha256"]:
        raise BatchPipelineError("falsche batch_sha256")

    target_ids = list(batch["target_node_ids"])
    context_ids = set(batch["context_node_ids"])
    processing = candidate_package["node_processing"]
    processing_ids = [item["document_node_id"] for item in processing]
    if processing_ids != target_ids:
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
        start = evidence.get("char_start")
        end = evidence.get("char_end")
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


def build_batch_output_receipt(raw_output_path: str | Path) -> dict[str, Any]:
    path = Path(raw_output_path)
    raw_bytes = path.read_bytes()
    try:
        package = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BatchPipelineError("Rohoutput ist kein gültiges UTF-8-JSON") from error
    if not isinstance(package, dict) or not isinstance(package.get("batch_binding"), dict):
        raise BatchPipelineError("Rohoutput enthält keine Batchbindung")
    binding = package["batch_binding"]
    return {
        "batch_id": binding.get("batch_id"),
        "batch_number": binding.get("batch_number"),
        "raw_output_path": str(path.resolve()),
        "raw_output_sha256": hashlib.sha256(raw_bytes).hexdigest(),
    }


def merge_statement_candidate_batch_outputs(
    *,
    manifest: dict[str, Any],
    package_root: str | Path,
    output_receipts: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    _validate_manifest(manifest)
    if len(output_receipts) != manifest["batch_count"]:
        raise BatchPipelineError("fehlende oder doppelte Batches")
    root = Path(package_root)
    processing = []
    candidates = []
    relations = []
    raw_outputs = []
    all_candidate_ids: set[str] = set()
    model_name: str | None = None
    for index, receipt in enumerate(output_receipts, start=1):
        expected_batch = manifest["batches"][index - 1]
        if (
            receipt.get("batch_number") != index
            or receipt.get("batch_id") != expected_batch["batch_id"]
        ):
            raise BatchPipelineError("fehlender, doppelter oder vertauschter Batch")
        raw_path = Path(str(receipt["raw_output_path"]))
        raw_bytes = raw_path.read_bytes()
        actual_sha = hashlib.sha256(raw_bytes).hexdigest()
        if actual_sha != receipt.get("raw_output_sha256"):
            raise BatchPipelineError("falsche Rohoutputprüfsumme")
        try:
            raw_text = raw_bytes.decode("utf-8")
            package = json.loads(raw_text)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise BatchPipelineError("Rohoutput ist kein gültiges UTF-8-JSON") from error
        if not isinstance(package, dict):
            raise BatchPipelineError("Rohoutput muss ein JSON-Objekt sein")
        validate_statement_candidate_batch_output(
            package,
            manifest=manifest,
            package_dir=root / expected_batch["batch_id"],
        )
        current_model = package["batch_binding"]["model_name"]
        if model_name is None:
            model_name = current_model
        elif model_name != current_model:
            raise BatchPipelineError("widersprüchliche Modellmetadaten")
        for candidate in package["candidates"]:
            candidate_id = candidate["candidate_id"]
            if candidate_id in all_candidate_ids:
                raise BatchPipelineError("batchübergreifend doppelte candidate_id")
            all_candidate_ids.add(candidate_id)
        processing.extend(package["node_processing"])
        candidates.extend(package["candidates"])
        relations.extend(package["relations"])
        raw_outputs.append(
            {
                "batch_id": expected_batch["batch_id"],
                "batch_number": index,
                "raw_output_sha256": actual_sha,
                "raw_output_json": raw_text,
            }
        )

    merged: dict[str, Any] = {
        "package_version": MERGED_OUTPUT_VERSION,
        "contract_version": CONTRACT_VERSION,
        "project_id": manifest["project_id"],
        "document_id": manifest["document_id"],
        "document_version_id": manifest["document_version_id"],
        "document_sha256": manifest["document_sha256"],
        "model_name": model_name,
        "prompt_version": PROMPT_VERSION,
        "batch_manifest_sha256": manifest["manifest_sha256"],
        "batch_count": manifest["batch_count"],
        "node_processing": processing,
        "candidates": candidates,
        "relations": relations,
        "raw_outputs": raw_outputs,
    }
    merged["package_sha256"] = _sha256_json(merged)
    return merged


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
        },
    }
    package["package_sha256"] = _sha256_json(package)
    _simple_validator(
        "schemas/construction/statement_candidate_batch_input_v2.schema.json"
    ).validate(package)
    return package


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
    return {
        "model": model_name,
        "temperature": 0,
        "top_p": 1,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Gib exakt ein JSON-Objekt gemäß Schema aus. Verwende kein "
                    "Markdown und keine erläuternde Prosa. Kopiere batch_binding exakt "
                    "aus der abschließenden Bindungsnachricht."
                ),
            },
            {"role": "user", "content": prompt_text},
            {
                "role": "user",
                "content": (
                    "Verbindliche batch_binding für die Ausgabe; Werte nicht erraten "
                    "oder verändern:\n" + _canonical_json(response_binding)
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
            "response_binding": response_binding,
        },
    }


def _render_prompt(input_package: dict[str, Any]) -> str:
    root = _repo_root()
    template = (root / "prompts" / "statement_candidates_frontier_v2.md").read_text(
        encoding="utf-8"
    )
    contract = (
        root / "docs" / "extraction_contracts" / "statement_candidates_batch_v2.md"
    ).read_text(encoding="utf-8")
    schema = _read_json_object(
        root
        / "docs"
        / "extraction_contracts"
        / "schemas"
        / "statement_candidates_batch_v2.schema.json"
    )
    return (
        template.replace("{{CONTRACT_MARKDOWN}}", contract)
        .replace("{{JSON_SCHEMA}}", _canonical_json(schema))
        .replace("{{INPUT_PACKAGE}}", _canonical_json(input_package))
    )


def _validate_manifest(manifest: dict[str, Any]) -> None:
    if manifest.get("manifest_version") != BATCH_MANIFEST_VERSION:
        raise BatchPipelineError("unbekannte Batchmanifest-Version")
    expected = dict(manifest)
    checksum = expected.pop("manifest_sha256", None)
    if checksum != _sha256_json(expected):
        raise BatchPipelineError("falsche Batchmanifest-Prüfsumme")
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


def _consecutive_sections(
    nodes: list[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    result: list[list[dict[str, Any]]] = []
    for node in nodes:
        key = (node["section_number"], node["heading"])
        previous_key = None
        if result:
            previous = result[-1][-1]
            previous_key = (previous["section_number"], previous["heading"])
        if not result or key != previous_key:
            result.append([node])
        else:
            result[-1].append(node)
    return result


def _split_pages(
    nodes: list[dict[str, Any]], *, max_target_nodes: int, max_characters: int
) -> list[list[dict[str, Any]]]:
    result: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for node in nodes:
        if current and not _fits(
            current + [node],
            max_target_nodes=max_target_nodes,
            max_characters=max_characters,
        ):
            result.append(current)
            current = []
        current.append(node)
    if current:
        result.append(current)
    return result


def _fits(
    nodes: list[dict[str, Any]], *, max_target_nodes: int, max_characters: int
) -> bool:
    return len(nodes) <= max_target_nodes and sum(
        len(node["source_text"]) for node in nodes
    ) <= max_characters


def _ordered_unique(values: list[Any]) -> list[Any]:
    result = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


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


def _batch_output_validator() -> Draft202012Validator:
    root = _repo_root()
    v1_path = (
        root
        / "docs"
        / "extraction_contracts"
        / "schemas"
        / "statement_candidates_v1.schema.json"
    )
    v2_path = (
        root
        / "docs"
        / "extraction_contracts"
        / "schemas"
        / "statement_candidates_batch_v2.schema.json"
    )
    v1 = _read_json_object(v1_path)
    v2 = _read_json_object(v2_path)
    registry = Registry().with_resource(v1["$id"], Resource.from_contents(v1))
    Draft202012Validator.check_schema(v2)
    return Draft202012Validator(v2, registry=registry)


def _simple_validator(relative_path: str) -> Draft202012Validator:
    schema = _read_json_object(_repo_root() / relative_path)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _read_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BatchPipelineError(f"JSON-Objekt erwartet: {path}")
    return value


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_json(value: dict[str, Any]) -> str:
    return _sha256_text(_canonical_json(value))


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
