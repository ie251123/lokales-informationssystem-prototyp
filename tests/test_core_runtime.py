from __future__ import annotations

import sqlite3

from info_system.cli import main
from info_system.config import Settings


def test_package_exposes_version() -> None:
    import info_system

    assert info_system.__version__


def test_default_settings_are_local_and_llm_free() -> None:
    settings = Settings.from_env({})

    assert settings.llm_provider == "none"
    assert settings.answer_mode == "deterministic_extractive"
    assert settings.run_logging_enabled is True


def test_cli_init_db_creates_sqlite_database(tmp_path, capsys) -> None:
    db_path = tmp_path / "prototype.sqlite"

    exit_code = main(["init-db", "--db", str(db_path)])

    assert exit_code == 0
    assert db_path.exists()
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'documents'"
        ).fetchone()
    assert row == ("documents",)
    assert str(db_path) in capsys.readouterr().out
