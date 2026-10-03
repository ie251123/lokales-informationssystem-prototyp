from __future__ import annotations

from info_system.construction.segmentation import SourceSegment
from info_system.construction.text_artifacts import write_text_artifact


def test_write_text_artifact_contains_segment_markers(tmp_path) -> None:
    artifact_path = write_text_artifact(
        [
            SourceSegment(
                segment_id="doc:p1:s1",
                document_id="doc",
                page_number=1,
                section_label=None,
                segment_order=1,
                text="Erster Rettungsweg.",
            ),
            SourceSegment(
                segment_id="doc:p2:s2",
                document_id="doc",
                page_number=2,
                section_label="Abschnitt 2",
                segment_order=2,
                text="Zweiter Absatz.",
            ),
        ],
        document_id="doc",
        output_dir=tmp_path,
    )

    assert artifact_path == tmp_path / "doc.txt"
    content = artifact_path.read_text(encoding="utf-8")
    assert "# document_id: doc" in content
    assert "[segment_id=doc:p1:s1 page=1 order=1]" in content
    assert "[segment_id=doc:p2:s2 page=2 section=Abschnitt 2 order=2]" in content
    assert "Erster Rettungsweg." in content


def test_write_text_artifact_replaces_existing_file(tmp_path) -> None:
    artifact_path = write_text_artifact(
        [
            SourceSegment(
                segment_id="doc:p1:s1",
                document_id="doc",
                page_number=1,
                section_label=None,
                segment_order=1,
                text="Alt.",
            ),
        ],
        document_id="doc",
        output_dir=tmp_path,
    )

    write_text_artifact(
        [
            SourceSegment(
                segment_id="doc:p1:s1",
                document_id="doc",
                page_number=1,
                section_label=None,
                segment_order=1,
                text="Neu.",
            ),
        ],
        document_id="doc",
        output_dir=tmp_path,
    )

    assert artifact_path.read_text(encoding="utf-8").count("[segment_id=") == 1
    assert "Neu." in artifact_path.read_text(encoding="utf-8")
