from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class PdfExtractionError(RuntimeError):
    """Raised when PDF text extraction cannot be completed."""


@dataclass(frozen=True)
class ExtractedPage:
    page_number: int
    text: str


PdfOpener = Callable[[Path], AbstractContextManager[Any]]


def _open_with_pdfplumber(pdf_path: Path) -> AbstractContextManager[Any]:
    try:
        import pdfplumber
    except ImportError as exc:
        raise PdfExtractionError(
            "pdfplumber is required for PDF text extraction. "
            "Install the project dependencies before running extract-text."
        ) from exc
    return pdfplumber.open(str(pdf_path))


def extract_text_from_pdf(
    pdf_path: str | Path,
    *,
    opener: PdfOpener | None = None,
) -> list[ExtractedPage]:
    path = Path(pdf_path)
    if not path.exists():
        raise PdfExtractionError(f"PDF not found: {path}")
    if path.suffix.lower() != ".pdf":
        raise PdfExtractionError(f"Expected a .pdf file: {path}")

    open_pdf = opener or _open_with_pdfplumber
    try:
        with open_pdf(path) as pdf:
            return [
                ExtractedPage(
                    page_number=page_number,
                    text=page.extract_text() or "",
                )
                for page_number, page in enumerate(pdf.pages, start=1)
            ]
    except PdfExtractionError:
        raise
    except Exception as exc:
        raise PdfExtractionError(f"Failed to extract PDF text from {path}: {exc}") from exc
