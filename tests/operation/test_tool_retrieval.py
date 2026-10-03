from __future__ import annotations

import pytest

from info_system.operation.tool_retrieval import ToolSearchError, ToolSearchRunner


def test_python_tool_search_finds_segment_from_text_artifact(tmp_path) -> None:
    text_dir = tmp_path / "text"
    artifact = text_dir / "brandschutz.txt"
    text_dir.mkdir()
    artifact.write_text(
        "\ufeff# document_id: brandschutz\n\n"
        "[segment_id=brandschutz:p1:s1 page=1 order=1]\n"
        "Der Rettungsweg ist freizuhalten.\n\n",
        encoding="utf-8",
    )
    runner = ToolSearchRunner(
        allowed_paths=[text_dir],
        project_root=tmp_path,
        prefer_rg=False,
    )

    hits = runner.search("rettungsweg", search_path=text_dir, max_results=5)

    assert len(hits) == 1
    assert hits[0].tool_name == "python_regex"
    assert hits[0].document_id == "brandschutz"
    assert hits[0].segment_id == "brandschutz:p1:s1"
    assert hits[0].source_path == "text/brandschutz.txt"
    assert hits[0].page_number == 1
    assert hits[0].line_number == 4
    assert hits[0].matched_text == "Der Rettungsweg ist freizuhalten."


def test_tool_search_rejects_paths_outside_allowlist(tmp_path) -> None:
    allowed_dir = tmp_path / "text"
    blocked_dir = tmp_path / "blocked"
    allowed_dir.mkdir()
    blocked_dir.mkdir()
    runner = ToolSearchRunner(
        allowed_paths=[allowed_dir],
        project_root=tmp_path,
        prefer_rg=False,
    )

    with pytest.raises(ToolSearchError, match="not in allowed search paths"):
        runner.search("Rettungsweg", search_path=blocked_dir, max_results=5)


def test_python_tool_search_skips_undecodable_files(tmp_path) -> None:
    text_dir = tmp_path / "text"
    text_dir.mkdir()
    (text_dir / "broken.txt").write_bytes(b"\xff\xfe\x00\x00")
    runner = ToolSearchRunner(
        allowed_paths=[text_dir],
        project_root=tmp_path,
        prefer_rg=False,
    )

    assert runner.search("Rettungsweg", search_path=text_dir, max_results=5) == []
