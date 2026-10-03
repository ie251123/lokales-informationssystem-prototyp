from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from info_system.construction.segmentation import SourceSegment


def _segment_marker(segment: SourceSegment) -> str:
    parts = [
        f"segment_id={segment.segment_id}",
        f"page={segment.page_number}",
    ]
    if segment.section_label:
        parts.append(f"section={segment.section_label}")
    parts.append(f"order={segment.segment_order}")
    return "[" + " ".join(parts) + "]"


def write_text_artifact(
    segments: Iterable[SourceSegment],
    *,
    document_id: str,
    output_dir: str | Path,
) -> Path:
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = target_dir / f"{document_id}.txt"

    lines = [f"# document_id: {document_id}", ""]
    for segment in segments:
        lines.append(_segment_marker(segment))
        lines.append(segment.text)
        lines.append("")

    artifact_path.write_text("\n".join(lines), encoding="utf-8")
    return artifact_path
