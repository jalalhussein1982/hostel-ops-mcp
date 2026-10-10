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


SPEC_TOOL_ORDER = [
    "list_pending_registrations", "list_approved_registrations", "get_registration",
    "desk_minor_alerts", "list_groups", "get_group", "month_summary", "audit_log",
    "approve_registration", "return_registration", "create_group_link", "resend_group_invite",
    "export_authority_report", "edit_approved_registration", "erase_registration", "purge_month",
]

ALL_SCOPES = {"desk:read", "desk:write", "groups:read", "groups:write", "export",
              "pii:read", "admin:read", "admin:write", "admin:erase"}


@pytest.fixture
def planted():
    import json

    return json.loads((FIXTURES / "synthetic.json").read_text())


@pytest.fixture
def scopes_env(app, monkeypatch):
    """Set the stdio scope set the server runs with (no OAuth in-memory)."""

    def _set(scopes):
        monkeypatch.setenv("STDIO_SCOPES", ",".join(sorted(scopes)))

    return _set


@pytest.fixture
def client_with_scopes(scopes_env):
    """Factory: an in-memory client whose server sees exactly the given scopes."""
    from contextlib import asynccontextmanager

    from mcp import Client

    @asynccontextmanager
    async def _make(scopes, **client_kwargs):
        scopes_env(scopes)
        from hostel_ops_mcp.server import mcp

        async with Client(mcp, raise_exceptions=True, **client_kwargs) as c:
            yield c

    return _make


@pytest.fixture
async def client(client_with_scopes):
    """An in-memory client holding every scope."""
    async with client_with_scopes(ALL_SCOPES) as c:
        yield c


@pytest.fixture
async def reader(client_with_scopes):
    """An in-memory client with the read scopes only, no pii:read."""
    async with client_with_scopes({"desk:read", "groups:read", "admin:read"}) as c:
        yield c
