from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path

from info_system.construction.text_extraction import ExtractedPage


@dataclass(frozen=True)
class SourceSegment:
    segment_id: str
    document_id: str
    page_number: int
    section_label: str | None
    segment_order: int
    text: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def document_id_from_path(path: str | Path) -> str:
    stem = Path(path).stem.strip()
    document_id = re.sub(r"[^A-Za-z0-9_-]+", "_", stem)
    return document_id or "document"


def split_page_text(text: str) -> list[str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []
    return [
        " ".join(part.split())
        for part in re.split(r"\n\s*\n+", normalized)
        if part.strip()
    ]


def segment_pages(
    pages: list[ExtractedPage],
    *,
    document_id: str,
) -> list[SourceSegment]:
    segments: list[SourceSegment] = []
    next_order = 1
    for page in pages:
        for text in split_page_text(page.text):
            segments.append(
                SourceSegment(
                    segment_id=f"{document_id}:p{page.page_number}:s{next_order}",
                    document_id=document_id,
                    page_number=page.page_number,
                    section_label=None,
                    segment_order=next_order,
                    text=text,
                )
            )
            next_order += 1
    return segments
