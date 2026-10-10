"""Task 1: the vendored app imports and initialises a fresh database."""

from __future__ import annotations

from pathlib import Path


def test_app_imports_and_inits_db(tmp_path: Path):
    from hostel_ops_mcp._app import load

    db = tmp_path / "t.db"
    app = load(db)
    assert db.exists()
    assert app.repo.list_pending() == []
    assert app.config.settings.database_path == str(db.resolve())


def test_load_switches_database_within_one_process(tmp_path: Path):
    from hostel_ops_mcp._app import load

    a = load(tmp_path / "a.db")
    token, _ = a.repo.create_registration("en")
    b = load(tmp_path / "b.db")
    assert b.repo.get_registration(token) is None
    load(tmp_path / "a.db")
    assert a.repo.get_registration(token) is not None
