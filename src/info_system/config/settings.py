from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass


def _parse_bool(value: str | None, *, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_int(value: str | None, *, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    database_path: str = "data/processed/prototype.sqlite"
    text_artifacts_path: str = "data/processed/text"
    llm_provider: str = "none"
    answer_mode: str = "deterministic_extractive"
    retrieval_top_k: int = 5
    run_logging_enabled: bool = True
    llm_model_path: str | None = None

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Settings":
        source = os.environ if environ is None else environ
        model_path = source.get("LLM_MODEL_PATH") or None
        return cls(
            database_path=source.get(
                "DATABASE_PATH",
                "data/processed/prototype.sqlite",
            ),
            text_artifacts_path=source.get(
                "TEXT_ARTIFACTS_PATH",
                "data/processed/text",
            ),
            llm_provider=source.get("LLM_PROVIDER", "none"),
            answer_mode=source.get("ANSWER_MODE", "deterministic_extractive"),
            retrieval_top_k=_parse_int(source.get("RETRIEVAL_TOP_K"), default=5),
            run_logging_enabled=_parse_bool(
                source.get("RUN_LOGGING_ENABLED"),
                default=True,
            ),
            llm_model_path=model_path,
        )
