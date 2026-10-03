from __future__ import annotations

from pathlib import Path

from info_system.cli import main
from info_system.construction.text_extraction import (
    ExtractedPage,
    PdfExtractionError,
    extract_text_from_pdf,
)


class FakePage:
    def __init__(self, text: str | None) -> None:
        self.text = text

    def extract_text(self) -> str | None:
        return self.text


class FakePdf:
    def __init__(self, texts: list[str | None]) -> None:
        self.pages = [FakePage(text) for text in texts]

    def __enter__(self) -> "FakePdf":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None


def test_extract_text_from_pdf_returns_page_text(tmp_path) -> None:
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF test placeholder")

    pages = extract_text_from_pdf(
        pdf_path,
        opener=lambda _: FakePdf(["First page", None, "Third page"]),
    )

    assert pages == [
        ExtractedPage(page_number=1, text="First page"),
        ExtractedPage(page_number=2, text=""),
        ExtractedPage(page_number=3, text="Third page"),
    ]


def test_extract_text_from_pdf_rejects_missing_file(tmp_path) -> None:
    missing_pdf = tmp_path / "missing.pdf"

    try:
        extract_text_from_pdf(missing_pdf, opener=lambda _: FakePdf([]))
    except PdfExtractionError as error:
        assert "PDF not found" in str(error)
    else:
        raise AssertionError("Expected PdfExtractionError")


def test_cli_extract_text_prints_pages(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "info_system.cli.extract_text_from_pdf",
        lambda _: [ExtractedPage(page_number=1, text="Brandschutz text")],
    )

    exit_code = main(["extract-text", "synthetic/input/sample.pdf"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "page 1" in output
    assert "Brandschutz text" in output


def test_cli_extract_text_reports_errors(monkeypatch, capsys) -> None:
    def fail(_: Path) -> list[ExtractedPage]:
        raise PdfExtractionError("controlled failure")

    monkeypatch.setattr("info_system.cli.extract_text_from_pdf", fail)

    exit_code = main(["extract-text", "synthetic/input/sample.pdf"])

    assert exit_code == 1
    assert "controlled failure" in capsys.readouterr().err
