"""Opaque handles for registration tokens.

In the vendor app a registration token is a bearer credential: ``GET /w/{token}/{step}`` needs
no login and, for a RETURNED record, rehydrates the whole form. So a caller without ``pii:read``
must never see a token. Such callers get ``ref`` = HMAC-SHA256(token, HANDLE_KEY)[:16] hex
instead, and every tool accepts either a ``ref`` or a raw token as its ``token`` argument.

The HMAC is one-way: knowing the key and a ref does not yield the token (tokens are 192 random
bits). The key therefore only needs to be *stable* for refs to survive restarts; set
``HANDLE_KEY`` per deployment so refs are not comparable across deployments.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re

from . import _app

DEFAULT_KEY = "hostel-ops-mcp-handle-v1"
_REF = re.compile(r"^[0-9a-f]{16}$")


class Unknown(Exception):
    """Neither a known token nor the ref of one."""


def _key() -> bytes:
    return (os.environ.get("HANDLE_KEY") or DEFAULT_KEY).encode()


def ref_for(token: str) -> str:
    return hmac.new(_key(), token.encode(), hashlib.sha256).hexdigest()[:16]


def is_ref(value: str) -> bool:
    return bool(_REF.match(value or ""))


def resolve(value: str) -> str:
    """Return the raw token for ``value`` (a token or a ref); raise ``Unknown`` otherwise."""
    if not value:
        raise Unknown(value)
    app = _app.current()
    with app.db.get_conn() as conn:
        if conn.execute("SELECT 1 FROM guest_registration WHERE token=?", (value,)).fetchone():
            return value
        if is_ref(value):
            key = _key()
            for (token,) in conn.execute("SELECT token FROM guest_registration"):
                if hmac.new(key, token.encode(), hashlib.sha256).hexdigest()[:16] == value:
                    return token
    raise Unknown(value)
