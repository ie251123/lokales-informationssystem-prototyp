from __future__ import annotations

import hashlib
import json
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from info_system.construction.text_extraction import ExtractedPage, extract_text_from_pdf
from info_system.db.schema import initialize_database

IMPORT_MANIFEST_VERSION = "document_import_manifest_v1"
EXPORT_PACKAGE_VERSION = "document_nodes_export_v1"
TEXT_REPAIR_RULES_VERSION = "source_text_repair_v1"
SEARCH_NORMALIZATION_RULES_VERSION = "search_text_normalization_v1"


class DocumentPipelineError(RuntimeError):
    pass


@dataclass(frozen=True)
class ImportResult:
    project_id: str
    document_id: str
    document_version_id: str
    document_sha256: str
    page_count: int
    node_count: int
    citable_node_count: int
    fts5_row_count: int
    node_ids: list[str]
    page_states: list[dict[str, object]]


@dataclass(frozen=True)
class NodeDraft:
    node_id: str
    node_path: str
    parent_node_id: str | None
    node_rank: int
    element_type: str
    heading: str | None
    locator: str | None
    page_start: int | None
    page_end: int | None
    raw_extracted_text: str
    source_text: str
    search_text: str
    is_citable: bool
    section_number: str | None


@dataclass(frozen=True)
class SourceExcerpt:
    source_text: str
    locator: str
    page_start: int
    page_end: int


def load_manifest(path: str | Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as error:
        raise DocumentPipelineError(f"could not read manifest: {error}") from error
    except json.JSONDecodeError as error:
        raise DocumentPipelineError(f"invalid manifest JSON: {error}") from error
    validate_import_manifest(data)
    return data


def validate_import_manifest(manifest: dict[str, Any]) -> None:
    _validator("document_import_manifest_v1.schema.json").validate(manifest)
    _validate_profile(manifest)


def import_document_nodes_from_pdf(
    pdf_path: str | Path,
    *,
    db_path: str | Path,
    manifest: dict[str, Any],
    pages: list[ExtractedPage] | None = None,
    source_excerpt: SourceExcerpt | None = None,
) -> ImportResult:
    validate_import_manifest(manifest)
    path = Path(pdf_path)
    if not path.exists():
        raise DocumentPipelineError(f"PDF not found: {path}")
    if path.suffix.lower() != ".pdf":
        raise DocumentPipelineError(f"Expected a .pdf file: {path}")

    extracted_pages = pages if pages is not None else extract_text_from_pdf(path)
    page_count = int(manifest["profile"]["expected_page_count"])
    _validate_pages(extracted_pages, expected_page_count=page_count)

    document_sha256 = _sha256_file(path)
    profile_hash = _sha256_json(_version_parameters(manifest))
    document_id = str(manifest["document"]["document_id"])
    document_version_id = (
        f"{document_id}:sha256:{document_sha256[:16]}:profile:{profile_hash[:16]}"
    )
    timestamp = _utc_now()
    if source_excerpt is None:
        if _uncovered_profile_pages(manifest["profile"]):
            raise DocumentPipelineError(
                "sparse document profile requires a controlled source excerpt"
            )
        nodes, page_states = _build_nodes(
            manifest=manifest,
            document_version_id=document_version_id,
            pages=extracted_pages,
        )
    else:
        nodes, page_states = _build_excerpt_nodes(
            manifest=manifest,
            document_version_id=document_version_id,
            excerpt=source_excerpt,
        )

    database_path = initialize_database(db_path)
    try:
        with sqlite3.connect(database_path) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("BEGIN IMMEDIATE")
            try:
                project_pk = _upsert_project(connection, manifest, timestamp=timestamp)
                document_pk = _upsert_document(
                    connection,
                    manifest,
                    pdf_path=path,
                    project_pk=project_pk,
                    document_sha256=document_sha256,
                    timestamp=timestamp,
                )
                document_version_pk = _upsert_document_version(
                    connection,
                    manifest,
                    document_pk=document_pk,
                    document_version_id=document_version_id,
                    document_sha256=document_sha256,
                    timestamp=timestamp,
                )
                _replace_document_nodes(
                    connection,
                    document_version_pk=document_version_pk,
                    nodes=nodes,
                    timestamp=timestamp,
                )
                connection.execute(
                    "INSERT INTO document_nodes_fts(document_nodes_fts) VALUES ('rebuild')"
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
    except sqlite3.Error as error:
        raise DocumentPipelineError(f"database import failed: {error}") from error

    with sqlite3.connect(database_path) as connection:
        node_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM document_nodes
            JOIN document_versions USING (document_version_pk)
            WHERE document_version_id = ?
            """,
            (document_version_id,),
        ).fetchone()[0]
        citable_node_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM document_nodes
            JOIN document_versions USING (document_version_pk)
            WHERE document_version_id = ?
            AND is_citable = 1
            """,
            (document_version_id,),
        ).fetchone()[0]
        fts5_row_count = connection.execute(
            "SELECT COUNT(*) FROM document_nodes_fts"
        ).fetchone()[0]

    return ImportResult(
        project_id=str(manifest["project"]["project_id"]),
        document_id=document_id,
        document_version_id=document_version_id,
        document_sha256=document_sha256,
        page_count=page_count,
        node_count=node_count,
        citable_node_count=citable_node_count,
        fts5_row_count=fts5_row_count,
        node_ids=[node.node_id for node in nodes],
        page_states=page_states,
    )


def export_document_nodes_package(
    db_path: str | Path,
    *,
    document_version_id: str,
) -> dict[str, Any]:
    with sqlite3.connect(Path(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT
                projects.project_id,
                documents.document_id,
                documents.document_hash,
                document_versions.document_version_id,
                document_versions.version_label AS profile_id,
                document_nodes.document_node_id,
                document_nodes.node_path,
                parent.document_node_id AS parent_node_id,
                document_nodes.node_rank,
                document_nodes.element_type,
                document_nodes.heading,
                document_nodes.locator,
                document_nodes.page_start,
                document_nodes.page_end,
                document_nodes.source_text,
                document_nodes.is_citable
            FROM document_nodes
            JOIN document_versions USING (document_version_pk)
            JOIN documents USING (document_pk)
            LEFT JOIN projects USING (project_pk)
            LEFT JOIN document_nodes AS parent
                ON parent.document_node_pk = document_nodes.parent_document_node_pk
            WHERE document_versions.document_version_id = ?
            ORDER BY document_nodes.node_rank
            """,
            (document_version_id,),
        ).fetchall()
    if not rows:
        raise DocumentPipelineError(
            f"document version not found or has no nodes: {document_version_id}"
        )

    first = rows[0]
    nodes = [_export_node(row) for row in rows]
    package: dict[str, Any] = {
        "package_version": EXPORT_PACKAGE_VERSION,
        "project_id": first["project_id"],
        "document_id": first["document_id"],
        "document_version_id": first["document_version_id"],
        "document_sha256": first["document_hash"],
        "profile_id": first["profile_id"],
        "text_repair_rules_version": TEXT_REPAIR_RULES_VERSION,
        "search_normalization_rules_version": SEARCH_NORMALIZATION_RULES_VERSION,
        "nodes": nodes,
        "page_states": _page_states_from_export_nodes(nodes),
    }
    package["package_sha256"] = _sha256_json(package)
    validate_export_package(package)
    return package


def write_export_package(
    db_path: str | Path,
    *,
    document_version_id: str,
    output_path: str | Path,
) -> dict[str, Any]:
    package = export_document_nodes_package(
        db_path,
        document_version_id=document_version_id,
    )
    serialized = _canonical_json(package)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialized + "\n", encoding="utf-8")
    return package


def validate_export_package(package: dict[str, Any]) -> None:
    _validator("document_nodes_export_v1.schema.json").validate(package)
    expected = dict(package)
    package_sha256 = expected.pop("package_sha256")
    actual = _sha256_json(expected)
    if package_sha256 != actual:
        raise DocumentPipelineError("export package checksum mismatch")


def repair_source_text(raw_text: str) -> str:
    text = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00ad", "")
    text = text.replace("\u2010", "-").replace("\u2011", "-")
    text = text.replace("\u2013", "-").replace("\u2014", "-")
    text = text.replace("\u201e", '"').replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("ß", "ss")
    text = _replace_german_umlauts(text)
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()


def _page_source_text(
    raw_text: str,
    *,
    manifest: dict[str, Any],
    page_number: int,
) -> str:
    source_text = repair_source_text(raw_text)
    source_text = _apply_profile_glyph_repairs(
        source_text,
        _glyph_repairs_for_page(manifest["profile"], page_number),
    )
    title = repair_source_text(str(manifest["document"]["title"]))
    lines = source_text.split("\n")
    if lines and lines[0].strip() == title:
        lines = lines[1:]
    if lines and lines[-1].strip() == str(page_number):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def normalize_search_text(source_text: str) -> str:
    normalized = unicodedata.normalize("NFKD", source_text)
    without_marks = "".join(
        character
        for character in normalized
        if not unicodedata.combining(character)
    )
    return " ".join(without_marks.casefold().split())


def _validator(schema_name: str) -> Draft202012Validator:
    schema_path = Path(__file__).parents[3] / "schemas" / "construction" / schema_name
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


def _validate_profile(manifest: dict[str, Any]) -> None:
    profile = manifest["profile"]
    expected_page_count = int(profile["expected_page_count"])
    covered_pages: dict[int, str] = {}
    previous_start = 0
    for section in profile["sections"]:
        page_start = int(section["page_start"])
        page_end = int(section["page_end"])
        if page_start > page_end:
            raise DocumentPipelineError("section page_start must be <= page_end")
        if page_start <= previous_start:
            raise DocumentPipelineError("sections must be ordered by page_start")
        previous_start = page_start
        for page in range(page_start, page_end + 1):
            if page > expected_page_count:
                raise DocumentPipelineError("section exceeds expected page count")
            if page in covered_pages:
                raise DocumentPipelineError("section page ranges must not overlap")
            covered_pages[page] = str(section["section_number"])
    missing = [
        page
        for page in range(1, expected_page_count + 1)
        if page not in covered_pages
    ]
    uncovered_non_evidence = set(missing) - {
        int(page) for page in profile["non_evidence_pages"]
    }
    if uncovered_non_evidence:
        raise DocumentPipelineError(f"profile does not cover pages: {missing}")
    for page in [*profile["empty_pages"], *profile["non_evidence_pages"]]:
        if int(page) > expected_page_count:
            raise DocumentPipelineError("profile page marker exceeds expected page count")
    for excluded_block in profile.get("excluded_text_blocks", []):
        if int(excluded_block["page"]) > expected_page_count:
            raise DocumentPipelineError("excluded text block exceeds expected page count")
    for glyph_repair in profile.get("glyph_repairs", []):
        if int(glyph_repair["page"]) > expected_page_count:
            raise DocumentPipelineError("glyph repair exceeds expected page count")


def _validate_pages(pages: list[ExtractedPage], *, expected_page_count: int) -> None:
    page_numbers = [page.page_number for page in pages]
    if page_numbers != list(range(1, expected_page_count + 1)):
        raise DocumentPipelineError("page numbers must be contiguous and complete")


def _build_nodes(
    *,
    manifest: dict[str, Any],
    document_version_id: str,
    pages: list[ExtractedPage],
) -> tuple[list[NodeDraft], list[dict[str, object]]]:
    profile = manifest["profile"]
    document_title = str(manifest["document"]["title"])
    nodes: list[NodeDraft] = []
    rank = 1
    root_id = _node_id(document_version_id, "/")
    nodes.append(
        NodeDraft(
            node_id=root_id,
            node_path="/",
            parent_node_id=None,
            node_rank=rank,
            element_type="document_root",
            heading=document_title,
            locator=None,
            page_start=None,
            page_end=None,
            raw_extracted_text=document_title,
            source_text=repair_source_text(document_title),
            search_text=normalize_search_text(repair_source_text(document_title)),
            is_citable=False,
            section_number=None,
        )
    )
    rank += 1

    page_by_number = {page.page_number: page for page in pages}
    section_by_page = _section_by_page(profile)
    page_states: list[dict[str, object]] = []

    for section_index, section in enumerate(profile["sections"], start=1):
        section_number = str(section["section_number"])
        heading = str(section["heading"])
        section_path = f"/{section_number}"
        section_id = _node_id(document_version_id, section_path)
        nodes.append(
            NodeDraft(
                node_id=section_id,
                node_path=section_path,
                parent_node_id=root_id,
                node_rank=rank,
                element_type="section",
                heading=heading,
                locator=f"section={section_number}",
                page_start=int(section["page_start"]),
                page_end=int(section["page_end"]),
                raw_extracted_text=heading,
                source_text=repair_source_text(heading),
                search_text=normalize_search_text(repair_source_text(heading)),
                is_citable=False,
                section_number=section_number,
            )
        )
        rank += 1
        del section_index

        for page_number in range(int(section["page_start"]), int(section["page_end"]) + 1):
            page = page_by_number[page_number]
            raw_text = page.text
            excluded_blocks = _excluded_blocks_for_page(profile, page_number)
            citable_raw_text = _remove_excluded_raw_blocks(raw_text, excluded_blocks)
            source_text = _page_source_text(
                citable_raw_text,
                manifest=manifest,
                page_number=page_number,
            )
            search_text = normalize_search_text(source_text)
            is_empty = source_text == "" or page_number in profile["empty_pages"]
            is_non_evidence = page_number in profile["non_evidence_pages"]
            state = "empty" if is_empty else "non_evidence" if is_non_evidence else "text"
            page_states.append({"page": page_number, "state": state})
            node_path = f"{section_path}/p{page_number}"
            element_type = "empty_page" if is_empty else "page_text"
            nodes.append(
                NodeDraft(
                    node_id=_node_id(document_version_id, node_path),
                    node_path=node_path,
                    parent_node_id=section_id,
                    node_rank=rank,
                    element_type=element_type,
                    heading=heading,
                    locator=f"page={page_number};section={section_number}",
                    page_start=page_number,
                    page_end=page_number,
                    raw_extracted_text=raw_text,
                    source_text="" if is_empty else source_text,
                    search_text="" if is_empty else search_text,
                    is_citable=not is_empty and not is_non_evidence,
                    section_number=section_number,
                )
            )
            rank += 1
            for excluded_index, excluded_block in enumerate(excluded_blocks, start=1):
                excluded_source_text = repair_source_text(str(excluded_block["raw_text"]))
                excluded_node_path = f"{section_path}/p{page_number}/excluded{excluded_index}"
                nodes.append(
                    NodeDraft(
                        node_id=_node_id(document_version_id, excluded_node_path),
                        node_path=excluded_node_path,
                        parent_node_id=section_id,
                        node_rank=rank,
                        element_type="non_citable_text",
                        heading=str(excluded_block["label"]),
                        locator=(
                            f"page={page_number};section={section_number};"
                            f"excluded={excluded_index}"
                        ),
                        page_start=page_number,
                        page_end=page_number,
                        raw_extracted_text=str(excluded_block["raw_text"]),
                        source_text=excluded_source_text,
                        search_text=normalize_search_text(excluded_source_text),
                        is_citable=False,
                        section_number=section_number,
                    )
                )
                rank += 1

    if sorted(state["page"] for state in page_states) != sorted(section_by_page):
        raise DocumentPipelineError("incomplete page coverage")
    return nodes, sorted(page_states, key=lambda item: int(item["page"]))


def _build_excerpt_nodes(
    *,
    manifest: dict[str, Any],
    document_version_id: str,
    excerpt: SourceExcerpt,
) -> tuple[list[NodeDraft], list[dict[str, object]]]:
    profile = manifest["profile"]
    sections = profile["sections"]
    if len(sections) != 1:
        raise DocumentPipelineError("controlled source excerpt requires one section")
    section = sections[0]
    if (
        excerpt.page_start != int(section["page_start"])
        or excerpt.page_end != int(section["page_end"])
    ):
        raise DocumentPipelineError("source excerpt page span conflicts with profile")
    if excerpt.page_start > excerpt.page_end or not excerpt.source_text:
        raise DocumentPipelineError("source excerpt is empty or has an invalid page span")

    document_title = str(manifest["document"]["title"])
    section_number = str(section["section_number"])
    heading = str(section["heading"])
    root_id = _node_id(document_version_id, "/")
    section_path = f"/{section_number}"
    section_id = _node_id(document_version_id, section_path)
    excerpt_path = (
        f"{section_path}/p{excerpt.page_start}"
        if excerpt.page_start == excerpt.page_end
        else f"{section_path}/p{excerpt.page_start}-p{excerpt.page_end}"
    )
    nodes = [
        NodeDraft(
            node_id=root_id,
            node_path="/",
            parent_node_id=None,
            node_rank=1,
            element_type="document_root",
            heading=document_title,
            locator=None,
            page_start=None,
            page_end=None,
            raw_extracted_text=document_title,
            source_text=repair_source_text(document_title),
            search_text=normalize_search_text(repair_source_text(document_title)),
            is_citable=False,
            section_number=None,
        ),
        NodeDraft(
            node_id=section_id,
            node_path=section_path,
            parent_node_id=root_id,
            node_rank=2,
            element_type="section",
            heading=heading,
            locator=f"section={section_number}",
            page_start=excerpt.page_start,
            page_end=excerpt.page_end,
            raw_extracted_text=heading,
            source_text=repair_source_text(heading),
            search_text=normalize_search_text(repair_source_text(heading)),
            is_citable=False,
            section_number=section_number,
        ),
        NodeDraft(
            node_id=_node_id(document_version_id, excerpt_path),
            node_path=excerpt_path,
            parent_node_id=section_id,
            node_rank=3,
            element_type="page_text",
            heading=heading,
            locator=excerpt.locator,
            page_start=excerpt.page_start,
            page_end=excerpt.page_end,
            raw_extracted_text=excerpt.source_text,
            source_text=excerpt.source_text,
            search_text=normalize_search_text(excerpt.source_text),
            is_citable=True,
            section_number=section_number,
        ),
    ]
    selected_pages = set(range(excerpt.page_start, excerpt.page_end + 1))
    page_states = [
        {
            "page": page,
            "state": "text" if page in selected_pages else "non_evidence",
        }
        for page in range(1, int(profile["expected_page_count"]) + 1)
    ]
    return nodes, page_states


def _section_by_page(profile: dict[str, Any]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for section in profile["sections"]:
        for page in range(int(section["page_start"]), int(section["page_end"]) + 1):
            result[page] = section
    return result


def _uncovered_profile_pages(profile: dict[str, Any]) -> list[int]:
    covered = set(_section_by_page(profile))
    return [
        page
        for page in range(1, int(profile["expected_page_count"]) + 1)
        if page not in covered
    ]


def _upsert_project(
    connection: sqlite3.Connection,
    manifest: dict[str, Any],
    *,
    timestamp: str,
) -> int:
    project = manifest["project"]
    connection.execute(
        """
        INSERT INTO projects (project_id, name, description, created_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(project_id) DO UPDATE SET
            name = excluded.name,
            description = excluded.description
        """,
        (
            project["project_id"],
            project["name"],
            project.get("description"),
            timestamp,
        ),
    )
    return connection.execute(
        "SELECT project_pk FROM projects WHERE project_id = ?",
        (project["project_id"],),
    ).fetchone()[0]


def _upsert_document(
    connection: sqlite3.Connection,
    manifest: dict[str, Any],
    *,
    pdf_path: Path,
    project_pk: int,
    document_sha256: str,
    timestamp: str,
) -> int:
    document = manifest["document"]
    source_path = document.get("source_path") or pdf_path.name
    connection.execute(
        """
        INSERT INTO documents (
            document_id,
            project_pk,
            filename,
            file_type,
            source_path,
            imported_at,
            title,
            document_hash
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(document_id) DO UPDATE SET
            project_pk = excluded.project_pk,
            filename = excluded.filename,
            file_type = excluded.file_type,
            source_path = excluded.source_path,
            imported_at = excluded.imported_at,
            title = excluded.title,
            document_hash = excluded.document_hash
        """,
        (
            document["document_id"],
            project_pk,
            pdf_path.name,
            "pdf",
            source_path,
            timestamp,
            document["title"],
            document_sha256,
        ),
    )
    return connection.execute(
        "SELECT document_pk FROM documents WHERE document_id = ?",
        (document["document_id"],),
    ).fetchone()[0]


def _upsert_document_version(
    connection: sqlite3.Connection,
    manifest: dict[str, Any],
    *,
    document_pk: int,
    document_version_id: str,
    document_sha256: str,
    timestamp: str,
) -> int:
    import_options = manifest["import"]
    existing_versions = connection.execute(
        """
        SELECT document_version_id
        FROM document_versions
        WHERE document_pk = ?
        """,
        (document_pk,),
    ).fetchall()
    if (
        existing_versions
        and all(row[0] != document_version_id for row in existing_versions)
        and not import_options["allow_new_version_for_same_document"]
    ):
        raise DocumentPipelineError("new version is not allowed for this document")

    is_current = 1 if import_options["set_current"] else 0
    if is_current:
        connection.execute(
            "UPDATE document_versions SET is_current = 0 WHERE document_pk = ?",
            (document_pk,),
        )
    connection.execute(
        """
        INSERT INTO document_versions (
            document_version_id,
            document_pk,
            version_label,
            content_hash,
            is_current,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(document_version_id) DO UPDATE SET
            is_current = excluded.is_current,
            content_hash = excluded.content_hash
        """,
        (
            document_version_id,
            document_pk,
            manifest["profile"]["profile_id"],
            document_sha256,
            is_current,
            timestamp,
        ),
    )
    return connection.execute(
        """
        SELECT document_version_pk
        FROM document_versions
        WHERE document_version_id = ?
        """,
        (document_version_id,),
    ).fetchone()[0]


def _replace_document_nodes(
    connection: sqlite3.Connection,
    *,
    document_version_pk: int,
    nodes: list[NodeDraft],
    timestamp: str,
) -> None:
    existing = connection.execute(
        """
        SELECT
            child.document_node_id,
            child.node_path,
            parent.document_node_id,
            child.node_rank,
            child.element_type,
            child.heading,
            child.locator,
            child.page_start,
            child.page_end,
            child.raw_extracted_text,
            child.source_text,
            child.search_text,
            child.is_citable
        FROM document_nodes AS child
        LEFT JOIN document_nodes AS parent
          ON parent.document_node_pk = child.parent_document_node_pk
        WHERE child.document_version_pk = ?
        ORDER BY child.node_rank
        """,
        (document_version_pk,),
    ).fetchall()
    expected = [
        (
            node.node_id,
            node.node_path,
            node.parent_node_id,
            node.node_rank,
            node.element_type,
            node.heading,
            node.locator,
            node.page_start,
            node.page_end,
            node.raw_extracted_text,
            node.source_text,
            node.search_text,
            1 if node.is_citable else 0,
        )
        for node in nodes
    ]
    if [tuple(row) for row in existing] == expected:
        return
    connection.execute(
        "DELETE FROM document_nodes WHERE document_version_pk = ?",
        (document_version_pk,),
    )
    pk_by_node_id: dict[str, int] = {}
    for node in nodes:
        parent_pk = (
            pk_by_node_id[node.parent_node_id]
            if node.parent_node_id is not None
            else None
        )
        cursor = connection.execute(
            """
            INSERT INTO document_nodes (
                document_node_id,
                document_version_pk,
                parent_document_node_pk,
                node_path,
                node_rank,
                element_type,
                heading,
                locator,
                page_start,
                page_end,
                raw_extracted_text,
                source_text,
                search_text,
                is_citable,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING document_node_pk
            """,
            (
                node.node_id,
                document_version_pk,
                parent_pk,
                node.node_path,
                node.node_rank,
                node.element_type,
                node.heading,
                node.locator,
                node.page_start,
                node.page_end,
                node.raw_extracted_text,
                node.source_text,
                node.search_text,
                1 if node.is_citable else 0,
                timestamp,
            ),
        )
        pk_by_node_id[node.node_id] = cursor.fetchone()[0]


def _export_node(row: sqlite3.Row) -> dict[str, Any]:
    section_number = _locator_part(row["locator"], "section")
    node = {
        "node_id": row["document_node_id"],
        "node_path": row["node_path"],
        "parent_node_id": row["parent_node_id"],
        "rank": row["node_rank"],
        "element_type": row["element_type"],
        "page_start": row["page_start"],
        "page_end": row["page_end"],
        "section_number": section_number,
        "heading": row["heading"],
        "locator": row["locator"],
        "is_citable": bool(row["is_citable"]),
        "source_text": row["source_text"],
    }
    node["content_sha256"] = _sha256_json(node)
    return node


def _page_states_from_export_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, object]]:
    states = []
    for node in nodes:
        if node["page_start"] is None or node["page_start"] != node["page_end"]:
            continue
        if node["element_type"] not in ("page_text", "empty_page"):
            continue
        if node["element_type"] == "empty_page":
            state = "empty"
        elif node["is_citable"]:
            state = "text"
        else:
            state = "non_evidence"
        states.append({"page": node["page_start"], "state": state})
    return sorted(states, key=lambda item: int(item["page"]))


def _locator_part(locator: str | None, key: str) -> str | None:
    if locator is None:
        return None
    for part in locator.split(";"):
        name, _, value = part.partition("=")
        if name == key:
            return value
    return None


def _excluded_blocks_for_page(
    profile: dict[str, Any],
    page_number: int,
) -> list[dict[str, Any]]:
    excluded_blocks = []
    for block in profile.get("excluded_text_blocks", []):
        if int(block["page"]) == page_number:
            excluded_blocks.append(block)
    return excluded_blocks


def _glyph_repairs_for_page(
    profile: dict[str, Any],
    page_number: int,
) -> list[dict[str, Any]]:
    repairs = []
    for repair in profile.get("glyph_repairs", []):
        if int(repair["page"]) == page_number:
            repairs.append(repair)
    return repairs


def _apply_profile_glyph_repairs(
    source_text: str,
    glyph_repairs: list[dict[str, Any]],
) -> str:
    result = source_text
    for repair in glyph_repairs:
        raw_text = str(repair["raw_text"])
        replacement_text = str(repair["replacement_text"])
        if raw_text not in result:
            raise DocumentPipelineError(
                f"glyph repair text not found on page {repair['page']}: {raw_text}"
            )
        result = result.replace(raw_text, replacement_text, 1)
    return result


def _remove_excluded_raw_blocks(
    raw_text: str,
    excluded_blocks: list[dict[str, Any]],
) -> str:
    result = raw_text
    for block in excluded_blocks:
        raw_block_text = str(block["raw_text"])
        if raw_block_text not in result:
            raise DocumentPipelineError(
                f"excluded text block not found on page {block['page']}: {block['label']}"
            )
        result = result.replace(raw_block_text, "", 1)
    return result


def _node_id(document_version_id: str, node_path: str) -> str:
    return "node:" + _sha256_text(f"{document_version_id}|{node_path}")[:32]


def _version_parameters(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "manifest_version": manifest["manifest_version"],
        "document": manifest["document"],
        "profile": manifest["profile"],
        "rules": {
            "text_repair": TEXT_REPAIR_RULES_VERSION,
            "search_normalization": SEARCH_NORMALIZATION_RULES_VERSION,
        },
    }


def _replace_german_umlauts(text: str) -> str:
    replacements = {
        "ä": "ae",
        "ö": "oe",
        "ü": "ue",
        "Ä": "Ae",
        "Ö": "Oe",
        "Ü": "Ue",
    }
    return "".join(replacements.get(character, character) for character in text)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_json(data: dict[str, Any]) -> str:
    return _sha256_text(_canonical_json(data))


def _canonical_json(data: dict[str, Any]) -> str:
    return json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
