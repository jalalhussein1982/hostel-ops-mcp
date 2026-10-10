"""Shared fixtures: every test runs against a private copy of the synthetic database."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"
SYNTHETIC_DB = FIXTURES / "synthetic.db"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """A private copy of the committed synthetic DB (built by `make fixtures` if missing)."""
    target = tmp_path / "hostel.db"
    if not SYNTHETIC_DB.exists():
        from hostel_ops_mcp.synth import build_synthetic_db

        build_synthetic_db(SYNTHETIC_DB)
    shutil.copy(SYNTHETIC_DB, target)
    return target


@pytest.fixture
def app(db_path: Path, monkeypatch):
    """The vendored app package bound to the private DB copy."""
    from hostel_ops_mcp import _app

    monkeypatch.setenv("HOSTEL_DATABASE_PATH", str(db_path))
    return _app.load(db_path)
