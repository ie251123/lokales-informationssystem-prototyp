from __future__ import annotations

import json

from info_system.cli import main
from info_system.construction.segmentation import SourceSegment, segment_pages
from info_system.construction.text_extraction import ExtractedPage


def test_segment_pages_creates_traceable_segments() -> None:
    pages = [
        ExtractedPage(page_number=1, text="First paragraph.\n\nSecond paragraph."),
        ExtractedPage(page_number=2, text="Third paragraph."),
    ]

    segments = segment_pages(pages, document_id="doc-1")

    assert segments == [
        SourceSegment(
            segment_id="doc-1:p1:s1",
            document_id="doc-1",
            page_number=1,
            section_label=None,
            segment_order=1,
            text="First paragraph.",
        ),
        SourceSegment(
            segment_id="doc-1:p1:s2",
            document_id="doc-1",
            page_number=1,
            section_label=None,
            segment_order=2,
            text="Second paragraph.",
        ),
        SourceSegment(
            segment_id="doc-1:p2:s3",
            document_id="doc-1",
            page_number=2,
            section_label=None,
            segment_order=3,
            text="Third paragraph.",
        ),
    ]


def test_segment_pages_skips_empty_pages() -> None:
    pages = [
        ExtractedPage(page_number=1, text=""),
        ExtractedPage(page_number=2, text="Only content."),
    ]

    segments = segment_pages(pages, document_id="doc-1")

    assert [segment.page_number for segment in segments] == [2]
    assert [segment.segment_order for segment in segments] == [1]


def test_cli_chunk_outputs_json_lines(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "info_system.cli.extract_text_from_pdf",
        lambda _: [ExtractedPage(page_number=1, text="A\n\nB")],
    )

    exit_code = main(["chunk", "synthetic/input/sample.pdf"])

    assert exit_code == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert [json.loads(line)["text"] for line in lines] == ["A", "B"]
    assert json.loads(lines[0])["document_id"] == "sample"
