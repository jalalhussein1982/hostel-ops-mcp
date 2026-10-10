"""Import shim for the vendored hostel app.

The app (``vendor/hostel-checkin-system``) has no ``pyproject.toml`` and reads its
configuration from the environment at import time (``app.config.settings``). This module
puts the submodule root on ``sys.path``, sets the environment the app expects, imports
the ``app`` package once, and points it at the requested SQLite file. Nothing under
``vendor/`` is modified.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor" / "hostel-checkin-system"

_SUBMODULES = (
    "config", "db", "repo", "groups", "state", "export", "dates", "auth",
    "backup", "signature", "validation", "group_validation", "ids", "i18n",
)

_app: ModuleType | None = None


def _import_app() -> ModuleType:
    global _app
    if _app is not None:
        return _app
    if not (VENDOR / "app" / "__init__.py").is_file():
        raise RuntimeError(
            f"hostel app not found at {VENDOR}; run `git submodule update --init`"
        )
    if str(VENDOR) not in sys.path:
        sys.path.insert(0, str(VENDOR))
    os.environ.setdefault("SESSION_SECRET", "hostel-ops-mcp-local-session-secret-not-used")
    os.environ.setdefault("COOKIE_SECURE", "false")
    os.environ.setdefault("ADMIN_PASSWORD", "unused-by-mcp")
    app = importlib.import_module("app")
    for name in _SUBMODULES:
        importlib.import_module(f"app.{name}")
    _app = app
    return app


def load(database_path: Path | str, backup_dir: Path | str | None = None) -> ModuleType:
    """Return the imported ``app`` package bound to ``database_path``.

    Sets ``DATABASE_PATH`` / ``BACKUP_DIR`` before the first import and updates the live
    ``settings`` object afterwards, so tests can switch databases within one process.
    Runs ``app.db.init_db()`` so the schema exists.
    """
    database_path = Path(database_path).resolve()
    backup_dir = Path(backup_dir).resolve() if backup_dir else database_path.parent / "backups"
    os.environ["DATABASE_PATH"] = str(database_path)
    os.environ["BACKUP_DIR"] = str(backup_dir)
    app = _import_app()
    app.config.settings.database_path = str(database_path)
    app.config.settings.backup_dir = str(backup_dir)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    app.db.init_db()
    return app


def current() -> ModuleType:
    """The already-loaded ``app`` package; raises if ``load`` was never called."""
    if _app is None:
        raise RuntimeError("hostel app not loaded; call _app.load(database_path) first")
    return _app
