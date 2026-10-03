from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol


class Fts5HitLike(Protocol):
    segment_id: str
    document_id: str
    rank: int
    score: float
    snippet: str


class ToolHitLike(Protocol):
    tool_name: str
    search_pattern: str
    document_id: str | None
    segment_id: str | None
    source_path: str
    page_number: int | None
    line_number: int
    context_before: str | None
    matched_text: str
    context_after: str | None


class RunLoggingError(RuntimeError):
    pass


def persist_retrieval_run(
    db_path: str | Path,
    *,
    query_text: str,
    retrieval_mode: str,
    top_k: int,
    prompt_version: str | None,
    llm_provider: str,
    answer_mode: str,
    answer_text: str,
    status: str,
    fts5_hits: list[Fts5HitLike] | None = None,
    tool_hits: list[ToolHitLike] | None = None,
) -> str:
    retrieval_run_id = str(uuid.uuid4())
    timestamp = datetime.now(UTC).isoformat()
    try:
        with sqlite3.connect(Path(db_path)) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(
                """
                INSERT INTO retrieval_runs (
                    retrieval_run_id,
                    query_text,
                    retrieval_mode,
                    top_k,
                    prompt_version,
                    llm_provider,
                    answer_mode,
                    answer_text,
                    started_at,
                    completed_at,
                    status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    retrieval_run_id,
                    query_text,
                    retrieval_mode,
                    top_k,
                    prompt_version,
                    llm_provider,
                    answer_mode,
                    answer_text,
                    timestamp,
                    timestamp,
                    status,
                ),
            )
            _insert_retrieval_hits(
                connection,
                retrieval_run_id=retrieval_run_id,
                hits=fts5_hits or [],
                timestamp=timestamp,
            )
            _insert_tool_hits(
                connection,
                retrieval_run_id=retrieval_run_id,
                hits=tool_hits or [],
            )
    except sqlite3.Error as error:
        raise RunLoggingError(f"could not persist retrieval run: {error}") from error
    return retrieval_run_id


def _insert_retrieval_hits(
    connection: sqlite3.Connection,
    *,
    retrieval_run_id: str,
    hits: list[Fts5HitLike],
    timestamp: str,
) -> None:
    connection.executemany(
        """
        INSERT INTO retrieval_hits (
            hit_id,
            retrieval_run_id,
            retrieval_method,
            document_id,
            chunk_id,
            rank,
            score,
            snippet,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                str(uuid.uuid4()),
                retrieval_run_id,
                "fts5",
                hit.document_id,
                hit.segment_id,
                hit.rank,
                hit.score,
                hit.snippet,
                timestamp,
            )
            for hit in hits
        ],
    )


def _insert_tool_hits(
    connection: sqlite3.Connection,
    *,
    retrieval_run_id: str,
    hits: list[ToolHitLike],
) -> None:
    connection.executemany(
        """
        INSERT INTO tool_search_hits (
            tool_hit_id,
            retrieval_run_id,
            tool_name,
            search_pattern,
            document_id,
            segment_id,
            source_path,
            page_number,
            line_number,
            context_before,
            matched_text,
            context_after
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                str(uuid.uuid4()),
                retrieval_run_id,
                hit.tool_name,
                hit.search_pattern,
                hit.document_id,
                hit.segment_id,
                hit.source_path,
                hit.page_number,
                hit.line_number,
                hit.context_before,
                hit.matched_text,
                hit.context_after,
            )
            for hit in hits
        ],
    )
