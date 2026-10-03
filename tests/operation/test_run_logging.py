from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from info_system.db import initialize_database
from info_system.operation.run_logging import persist_retrieval_run
from info_system.operation.tool_retrieval import ToolSearchHit


@dataclass(frozen=True)
class SyntheticFts5Hit:
    segment_id: str
    document_id: str
    rank: int
    score: float
    snippet: str


def test_persist_retrieval_run_stores_run_and_ranked_hits(tmp_path) -> None:
    db_path = tmp_path / "prototype.sqlite"
    initialize_database(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO documents (
                document_id, filename, file_type, source_path, imported_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "brandschutz",
                "brandschutz.pdf",
                "pdf",
                "brandschutz.pdf",
                "2026-01-01T00:00:00+00:00",
            ),
        )
        connection.execute(
            """
            INSERT INTO source_segments (
                segment_id, document_id, page_number, section_label,
                segment_order, text, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "brandschutz:p1:s1",
                "brandschutz",
                1,
                None,
                1,
                "Rettungsweg im Erdgeschoss.",
                "2026-01-01T00:00:00+00:00",
            ),
        )
    fts5_hit = SyntheticFts5Hit(
        segment_id="brandschutz:p1:s1",
        document_id="brandschutz",
        rank=1,
        score=-0.5,
        snippet="Rettungsweg im Erdgeschoss.",
    )
    tool_hit = ToolSearchHit(
        tool_name="python_regex",
        search_pattern="Rettungsweg",
        document_id="brandschutz",
        segment_id="brandschutz:p1:s1",
        source_path="text/brandschutz.txt",
        page_number=1,
        line_number=4,
        context_before="[segment_id=brandschutz:p1:s1 page=1 order=1]",
        matched_text="Rettungsweg im Erdgeschoss.",
        context_after=None,
    )

    run_id = persist_retrieval_run(
        db_path,
        query_text="Rettungsweg",
        retrieval_mode="combined",
        top_k=5,
        prompt_version="synthetic_prompt_v1",
        llm_provider="none",
        answer_mode="deterministic_extractive",
        answer_text="Antwort:\nRettungsweg im Erdgeschoss.",
        status="completed",
        fts5_hits=[fts5_hit],
        tool_hits=[tool_hit],
    )

    with sqlite3.connect(db_path) as connection:
        run = connection.execute(
            """
            SELECT retrieval_run_id, query_text, retrieval_mode, top_k,
                   prompt_version, llm_provider, answer_mode, answer_text, status
            FROM retrieval_runs
            """
        ).fetchone()
        retrieval_hit = connection.execute(
            """
            SELECT retrieval_method, document_id, chunk_id, rank, score, snippet
            FROM retrieval_hits
            """
        ).fetchone()
        tool_search_hit = connection.execute(
            """
            SELECT tool_name, search_pattern, document_id, segment_id, source_path
            FROM tool_search_hits
            """
        ).fetchone()

    assert run == (
        run_id,
        "Rettungsweg",
        "combined",
        5,
        "synthetic_prompt_v1",
        "none",
        "deterministic_extractive",
        "Antwort:\nRettungsweg im Erdgeschoss.",
        "completed",
    )
    assert retrieval_hit == (
        "fts5",
        "brandschutz",
        "brandschutz:p1:s1",
        1,
        -0.5,
        "Rettungsweg im Erdgeschoss.",
    )
    assert tool_search_hit == (
        "python_regex",
        "Rettungsweg",
        "brandschutz",
        "brandschutz:p1:s1",
        "text/brandschutz.txt",
    )
