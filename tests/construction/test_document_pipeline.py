from __future__ import annotations

import copy
import json
import sqlite3
from pathlib import Path

import pytest
from jsonschema import ValidationError

from info_system.cli import main
from info_system.construction.document_pipeline import (
    DocumentPipelineError,
    export_document_nodes_package,
    import_document_nodes_from_pdf,
    load_manifest,
    validate_export_package,
)
from info_system.construction.text_extraction import ExtractedPage
from info_system.db.schema import initialize_database


def test_import_creates_stable_document_version_and_nodes(tmp_path) -> None:
    pdf_path = _write_pdf(tmp_path, b"%PDF deterministic")
    db_path = tmp_path / "prototype.sqlite"
    manifest = _manifest(expected_page_count=3)
    pages = [
        ExtractedPage(1, "Deckblatt\r\nBrandschutzkonzept"),
        ExtractedPage(2, "Abschnitt 1\n\nRettungsweg"),
        ExtractedPage(3, "Abschnitt 2 mit Maßnahme"),
    ]

    first = import_document_nodes_from_pdf(
        pdf_path,
        db_path=db_path,
        manifest=manifest,
        pages=pages,
    )
    second = import_document_nodes_from_pdf(
        pdf_path,
        db_path=db_path,
        manifest=manifest,
        pages=pages,
    )

    with sqlite3.connect(db_path) as connection:
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("documents", "document_versions", "document_nodes")
        }
        current_versions = connection.execute(
            "SELECT COUNT(*) FROM document_versions WHERE is_current = 1"
        ).fetchone()[0]
        node_ids = [
            row[0]
            for row in connection.execute(
                "SELECT document_node_id FROM document_nodes ORDER BY node_rank"
            )
        ]

    assert first.document_sha256 == second.document_sha256
    assert first.document_version_id == second.document_version_id
    assert first.node_ids == second.node_ids == node_ids
    assert counts == {"documents": 1, "document_versions": 1, "document_nodes": 5}
    assert current_versions == 1


def test_changed_file_creates_new_current_version_for_same_document(tmp_path) -> None:
    pdf_path = _write_pdf(tmp_path, b"%PDF version one")
    db_path = tmp_path / "prototype.sqlite"
    manifest = _manifest(expected_page_count=1, allow_new_version=True)
    pages = [ExtractedPage(1, "Erste Fassung")]

    first = import_document_nodes_from_pdf(
        pdf_path,
        db_path=db_path,
        manifest=manifest,
        pages=pages,
    )
    pdf_path.write_bytes(b"%PDF version two")
    second = import_document_nodes_from_pdf(
        pdf_path,
        db_path=db_path,
        manifest=manifest,
        pages=[ExtractedPage(1, "Zweite Fassung")],
    )

    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT document_version_id, is_current
            FROM document_versions
            ORDER BY document_version_id
            """
        ).fetchall()

    assert first.document_version_id != second.document_version_id
    assert sum(row[1] for row in rows) == 1
    assert dict(rows)[second.document_version_id] == 1


def test_changed_file_requires_explicit_new_version_permission(tmp_path) -> None:
    pdf_path = _write_pdf(tmp_path, b"%PDF version one")
    db_path = tmp_path / "prototype.sqlite"
    manifest = _manifest(expected_page_count=1, allow_new_version=False)
    pages = [ExtractedPage(1, "Erste Fassung")]

    import_document_nodes_from_pdf(
        pdf_path,
        db_path=db_path,
        manifest=manifest,
        pages=pages,
    )
    pdf_path.write_bytes(b"%PDF version two")

    with pytest.raises(DocumentPipelineError, match="new version is not allowed"):
        import_document_nodes_from_pdf(
            pdf_path,
            db_path=db_path,
            manifest=manifest,
            pages=[ExtractedPage(1, "Zweite Fassung")],
        )


def test_page_coverage_hierarchy_locators_and_empty_page(tmp_path) -> None:
    pdf_path = _write_pdf(tmp_path, b"%PDF pages")
    db_path = tmp_path / "prototype.sqlite"
    manifest = _manifest(
        expected_page_count=3,
        sections=[
            _section("1", "Grundlagen", 1, 2),
            _section("2", "Massnahmen", 3, 3),
        ],
        empty_pages=[2],
    )

    result = import_document_nodes_from_pdf(
        pdf_path,
        db_path=db_path,
        manifest=manifest,
        pages=[
            ExtractedPage(1, "Brandschutz"),
            ExtractedPage(2, ""),
            ExtractedPage(3, "Tuer dicht- und selbstschliessend"),
        ],
    )

    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT node_path, parent_document_node_pk, locator, page_start, page_end,
                   is_citable, source_text, element_type
            FROM document_nodes
            ORDER BY node_rank
            """
        ).fetchall()

    assert result.page_states == [
        {"page": 1, "state": "text"},
        {"page": 2, "state": "empty"},
        {"page": 3, "state": "text"},
    ]
    assert [row[0] for row in rows] == [
        "/",
        "/1",
        "/1/p1",
        "/1/p2",
        "/2",
        "/2/p3",
    ]
    assert rows[0][1] is None
    assert all(row[1] is not None for row in rows[1:])
    assert rows[3][5] == 0
    assert rows[3][6] == ""
    assert rows[5][2] == "page=3;section=2"
    assert [row[7] for row in rows] == [
        "document_root",
        "section",
        "page_text",
        "empty_page",
        "section",
        "page_text",
    ]


def test_profile_excludes_non_citable_text_block_but_keeps_page_citable(tmp_path) -> None:
    pdf_path = _write_pdf(tmp_path, b"%PDF excluded")
    db_path = tmp_path / "prototype.sqlite"
    excluded_text = "0,30 m ueber Dach gefuehrt"
    result = import_document_nodes_from_pdf(
        pdf_path,
        db_path=db_path,
        manifest=_manifest(
            expected_page_count=1,
            excluded_text_blocks=[
                {
                    "page": 1,
                    "label": "ueberholte Bildbeschriftung",
                    "raw_text": excluded_text,
                }
            ],
        ),
        pages=[
            ExtractedPage(
                1,
                "Massgeblicher fortgeschriebener Fliesstext.\n"
                f"{excluded_text}\n"
                "Weitere massgebliche Passage.",
            )
        ],
    )

    package = export_document_nodes_package(
        db_path,
        document_version_id=result.document_version_id,
    )
    exported_nodes = {node["element_type"]: node for node in package["nodes"]}

    with sqlite3.connect(db_path) as connection:
        db_rows = connection.execute(
            """
            SELECT element_type, is_citable, source_text, raw_extracted_text
            FROM document_nodes
            ORDER BY node_rank
            """
        ).fetchall()
        excluded_fts_hits = connection.execute(
            """
            SELECT COUNT(*)
            FROM document_nodes_fts
            JOIN document_nodes ON document_nodes.document_node_pk = document_nodes_fts.rowid
            WHERE document_nodes_fts MATCH ?
            AND document_nodes.is_citable = 1
            """,
            ('"0" AND "30" AND "dach"',),
        ).fetchone()[0]

    page_node = exported_nodes["page_text"]
    excluded_node = exported_nodes["non_citable_text"]
    assert page_node["is_citable"] is True
    assert "Massgeblicher fortgeschriebener Fliesstext" in page_node["source_text"]
    assert excluded_text not in page_node["source_text"]
    assert excluded_node["is_citable"] is False
    assert excluded_node["source_text"] == excluded_text
    assert (excluded_node["node_id"], excluded_node["source_text"]) not in [
        (node["node_id"], node["source_text"])
        for node in package["nodes"]
        if node["is_citable"]
    ]
    assert excluded_fts_hits == 0
    assert ("non_citable_text", 0, excluded_text, excluded_text) in db_rows


def test_three_text_layers_and_document_nodes_fts_excludes_raw_text(tmp_path) -> None:
    pdf_path = _write_pdf(tmp_path, b"%PDF layers")
    db_path = tmp_path / "prototype.sqlite"

    import_document_nodes_from_pdf(
        pdf_path,
        db_path=db_path,
        manifest=_manifest(expected_page_count=1),
        pages=[ExtractedPage(1, "Roh\u00adGeheimnis\r\nT\u00fcr")],
    )

    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            """
            SELECT raw_extracted_text, source_text, search_text
            FROM document_nodes
            WHERE is_citable = 1
            """
        ).fetchone()
        raw_hits = connection.execute(
            "SELECT COUNT(*) FROM document_nodes_fts WHERE document_nodes_fts MATCH ?",
            ("RohGeheimnis",),
        ).fetchone()[0]
        source_hits = connection.execute(
            "SELECT COUNT(*) FROM document_nodes_fts WHERE document_nodes_fts MATCH ?",
            ("RohGeheimnis OR tuer",),
        ).fetchone()[0]

    assert row == ("Roh\u00adGeheimnis\r\nT\u00fcr", "RohGeheimnis\nTuer", "rohgeheimnis tuer")
    assert raw_hits == 1
    assert source_hits == 1


def test_versioned_profile_repairs_glyphs_without_changing_raw_text(
    tmp_path,
) -> None:
    pdf_path = _write_pdf(tmp_path, b"%PDF synthetic glyph fixture")
    profile_path = tmp_path / "synthetic_profile.json"
    profile_path.write_text(
        json.dumps(
            _manifest(
                expected_page_count=1,
                glyph_repairs=[
                    {
                        "page": 1,
                        "raw_text": "Prue(cid:101)klappe",
                        "replacement_text": "Pruefklappe",
                    },
                    {
                        "page": 1,
                        "raw_text": "Al(cid:202)a",
                        "replacement_text": "Alpha",
                    },
                ],
            ),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    manifest = load_manifest(profile_path)
    db_path = tmp_path / "prototype.sqlite"
    raw_passage = "Die Prue(cid:101)klappe Al(cid:202)a ist mit Code K-7 markiert."
    expected_passage = "Die Pruefklappe Alpha ist mit Code K-7 markiert."

    result = import_document_nodes_from_pdf(
        pdf_path,
        db_path=db_path,
        manifest=manifest,
        pages=[ExtractedPage(1, raw_passage)],
    )

    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            """
            SELECT nodes.raw_extracted_text, nodes.source_text, nodes.search_text,
                   nodes.is_citable, nodes.element_type, nodes.locator,
                   versions.version_label, documents.source_path
            FROM document_nodes AS nodes
            JOIN document_versions AS versions USING (document_version_pk)
            JOIN documents USING (document_pk)
            WHERE nodes.page_start = 1
            AND nodes.page_end = 1
            AND nodes.element_type = 'page_text'
            """
        ).fetchone()
        search_text_hits = connection.execute(
            """
            SELECT COUNT(*)
            FROM document_nodes
            WHERE page_start = 1
            AND page_end = 1
            AND is_citable = 1
            AND search_text LIKE ?
            AND search_text LIKE ?
            """,
            ("%pruefklappe%", "%alpha%"),
        ).fetchone()[0]
        fts_hits = connection.execute(
            """
            SELECT COUNT(*)
            FROM document_nodes_fts
            JOIN document_nodes
                ON document_nodes.document_node_pk = document_nodes_fts.rowid
            WHERE document_nodes_fts MATCH ?
            AND document_nodes.page_start = 1
            AND document_nodes.is_citable = 1
            """,
            ('"pruefklappe" AND "alpha"',),
        ).fetchone()[0]

    assert result.page_count == 1
    assert row is not None
    (
        raw_text,
        source_text,
        search_text,
        is_citable,
        element_type,
        locator,
        profile_version,
        source_path,
    ) = row
    assert raw_text == raw_passage
    assert source_text == expected_passage
    assert search_text == "die pruefklappe alpha ist mit code k-7 markiert."
    assert is_citable == 1
    assert element_type == "page_text"
    assert locator == "page=1;section=1"
    assert profile_version == "synthetic_plan_profile_v1"
    assert source_path == "synthetic_document.pdf"
    assert search_text_hits == 1
    assert fts_hits == 1


def test_glyph_repair_is_not_applied_without_profile_entry(tmp_path) -> None:
    pdf_path = _write_pdf(tmp_path, b"%PDF glyphs")
    db_path = tmp_path / "prototype.sqlite"

    import_document_nodes_from_pdf(
        pdf_path,
        db_path=db_path,
        manifest=_manifest(expected_page_count=1),
        pages=[
            ExtractedPage(
                1,
                "Sie werden als beidsei(cid:415)ge 0,5 m auskragende Pla(cid:425)e "
                "hergestellt.",
            )
        ],
    )

    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            """
            SELECT raw_extracted_text, source_text, search_text
            FROM document_nodes
            WHERE is_citable = 1
            """
        ).fetchone()

    assert row == (
        "Sie werden als beidsei(cid:415)ge 0,5 m auskragende Pla(cid:425)e hergestellt.",
        "Sie werden als beidsei(cid:415)ge 0,5 m auskragende Pla(cid:425)e hergestellt.",
        "sie werden als beidsei(cid:415)ge 0,5 m auskragende pla(cid:425)e hergestellt.",
    )


def test_invalid_segment_rolls_back_import(tmp_path) -> None:
    pdf_path = _write_pdf(tmp_path, b"%PDF invalid")
    db_path = initialize_database(tmp_path / "prototype.sqlite")

    with pytest.raises(DocumentPipelineError, match="page numbers must be contiguous"):
        import_document_nodes_from_pdf(
            pdf_path,
            db_path=db_path,
            manifest=_manifest(expected_page_count=2),
            pages=[ExtractedPage(1, "Seite 1"), ExtractedPage(3, "Seite 3")],
        )

    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM document_nodes").fetchone()[0] == 0


def test_export_package_is_deterministic_schema_valid_and_rejects_mutation(tmp_path) -> None:
    pdf_path = _write_pdf(tmp_path, b"%PDF export")
    db_path = tmp_path / "prototype.sqlite"
    result = import_document_nodes_from_pdf(
        pdf_path,
        db_path=db_path,
        manifest=_manifest(expected_page_count=2, empty_pages=[2]),
        pages=[ExtractedPage(1, "Brandschutz"), ExtractedPage(2, "")],
    )

    first = export_document_nodes_package(
        db_path,
        document_version_id=result.document_version_id,
    )
    second = export_document_nodes_package(
        db_path,
        document_version_id=result.document_version_id,
    )
    mutated = copy.deepcopy(first)
    del mutated["nodes"][0]["source_text"]
    wrong_checksum = copy.deepcopy(first)
    wrong_checksum["package_sha256"] = "0" * 64

    validate_export_package(first)
    assert first == second
    assert first["package_sha256"] == second["package_sha256"]
    assert [node["node_id"] for node in first["nodes"]] == result.node_ids
    assert first["nodes"][-1]["is_citable"] is False
    assert "gold" not in json.dumps(first, ensure_ascii=False).lower()
    with pytest.raises(ValidationError):
        validate_export_package(mutated)
    with pytest.raises(DocumentPipelineError, match="checksum mismatch"):
        validate_export_package(wrong_checksum)


def test_cli_import_document_and_export_document_nodes(monkeypatch, tmp_path, capsys) -> None:
    pdf_path = _write_pdf(tmp_path, b"%PDF cli")
    db_path = tmp_path / "prototype.sqlite"
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(_manifest(expected_page_count=1), ensure_ascii=False),
        encoding="utf-8",
    )
    export_path = tmp_path / "export.json"
    monkeypatch.setattr(
        "info_system.construction.document_pipeline.extract_text_from_pdf",
        lambda _: [ExtractedPage(1, "Brandschutz")],
    )

    import_exit = main(
        [
            "import-document",
            str(pdf_path),
            "--db",
            str(db_path),
            "--manifest",
            str(manifest_path),
        ]
    )
    import_output = capsys.readouterr().out
    document_version_id = [
        line.split(": ", 1)[1]
        for line in import_output.splitlines()
        if line.startswith("document_version_id: ")
    ][0]
    export_exit = main(
        [
            "export-document-nodes",
            "--db",
            str(db_path),
            "--document-version-id",
            document_version_id,
            "--output",
            str(export_path),
        ]
    )
    export_output = capsys.readouterr().out

    assert import_exit == 0
    assert export_exit == 0
    assert "nodes: 3" in export_output
    assert export_path.exists()
    validate_export_package(json.loads(export_path.read_text(encoding="utf-8")))


def _write_pdf(tmp_path, content: bytes):
    pdf_path = tmp_path / "synthetic_document.pdf"
    pdf_path.write_bytes(content)
    return pdf_path


def _manifest(
    *,
    expected_page_count: int,
    sections: list[dict[str, object]] | None = None,
    empty_pages: list[int] | None = None,
    excluded_text_blocks: list[dict[str, object]] | None = None,
    glyph_repairs: list[dict[str, object]] | None = None,
    allow_new_version: bool = True,
) -> dict[str, object]:
    manifest: dict[str, object] = {
        "manifest_version": "document_import_manifest_v1",
        "project": {
            "project_id": "project_synthetic",
            "name": "Synthetisches Testprojekt",
        },
        "document": {
            "document_id": "document_plan_beta",
            "title": "Synthetische Planungsunterlage",
            "file_type": "pdf",
            "source_path": "synthetic_document.pdf",
        },
        "import": {
            "set_current": True,
            "allow_new_version_for_same_document": allow_new_version,
        },
        "profile": {
            "profile_id": "synthetic_plan_profile_v1",
            "expected_page_count": expected_page_count,
            "text_repair_rules_version": "source_text_repair_v1",
            "search_normalization_rules_version": "search_text_normalization_v1",
            "sections": sections or [_section("1", "Brandschutz", 1, expected_page_count)],
            "empty_pages": empty_pages or [],
            "non_evidence_pages": [23] if expected_page_count >= 23 else [],
            "excluded_text_blocks": excluded_text_blocks or [],
        },
    }
    if glyph_repairs is not None:
        profile = manifest["profile"]
        assert isinstance(profile, dict)
        profile["glyph_repairs"] = glyph_repairs
    return manifest


def _section(
    section_number: str,
    heading: str,
    page_start: int,
    page_end: int,
) -> dict[str, object]:
    return {
        "section_number": section_number,
        "heading": heading,
        "page_start": page_start,
        "page_end": page_end,
    }
