"""Plain-Python operations over the hostel app's service layer. No MCP in this module.

Every function takes plain arguments and returns dicts shaped like the app's rows (or
raises one of the small exception classes below). ``server.py`` maps those onto scope
checks, redaction and Pydantic models. Keeping MCP out of here means the operations are
testable alone and the adapter stays thin.
"""

from __future__ import annotations

import base64
import binascii
import os
from datetime import date
from pathlib import Path
from typing import Any, Literal

from . import _app

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "fixtures" / "synthetic.db"

GroupStatus = Literal["pending", "submitted", "all"]


class NotFound(Exception):
    """Unknown token or slug. ``hint`` names the list tool the model should call."""

    def __init__(self, what: str, key: str, hint: str) -> None:
        super().__init__(f"no {what} {key!r}; call {hint} to find a valid one")
        self.hint = hint


class BadCursor(Exception):
    pass


def app():
    """The vendored app bound to HOSTEL_DATABASE_PATH (default: the synthetic fixture)."""
    try:
        return _app.current()
    except RuntimeError:
        path = os.environ.get("HOSTEL_DATABASE_PATH") or str(DEFAULT_DB)
        if not Path(path).exists() and Path(path) == DEFAULT_DB:
            from .synth import build_synthetic_db

            build_synthetic_db(DEFAULT_DB, summary_path=DEFAULT_DB.with_suffix(".json"))
        return _app.load(path)


def today() -> date:
    return app().dates.prague_now().date()


# ---------------------------------------------------------------- shaping helpers

def is_under_18(rec: dict[str, Any]) -> bool:
    """Under 18 on the arrival date (the desk's convention); False when either date is missing."""
    dob, arrival = rec.get("dob"), rec.get("arrival")
    if not dob or not arrival:
        return False
    try:
        return app().dates.is_under_18(date.fromisoformat(dob), date.fromisoformat(arrival))
    except (ValueError, TypeError):
        return False


def is_under_15(rec: dict[str, Any]) -> bool:
    dob, arrival = rec.get("dob"), rec.get("arrival")
    if not dob or not arrival:
        return False
    try:
        return app().dates.is_under_15(date.fromisoformat(dob), date.fromisoformat(arrival))
    except (ValueError, TypeError):
        return False


def encode_cursor(token: str) -> str:
    return base64.urlsafe_b64encode(token.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> str:
    try:
        return base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
    except (binascii.Error, UnicodeDecodeError) as e:
        raise BadCursor(f"invalid cursor {cursor!r}; use the next_cursor value from the previous page") from e


def _paginate(rows: list[dict[str, Any]], limit: int, cursor: str | None) -> tuple[list[dict[str, Any]], str | None, int]:
    """Stable order by (created_at, token); the cursor is the last token of the previous page."""
    rows = sorted(rows, key=lambda r: (r.get("created_at") or "", r["token"]))
    start = 0
    if cursor:
        last = decode_cursor(cursor)
        idx = next((i for i, r in enumerate(rows) if r["token"] == last), None)
        if idx is None:
            raise BadCursor("stale cursor: the row it pointed at is gone; restart from the first page")
        start = idx + 1
    page = rows[start:start + limit]
    nxt = encode_cursor(page[-1]["token"]) if page and start + limit < len(rows) else None
    return page, nxt, len(rows)


# ---------------------------------------------------------------- reads

def list_pending(limit: int = 25, cursor: str | None = None) -> tuple[list[dict[str, Any]], str | None, int]:
    return _paginate(app().repo.list_pending(), limit, cursor)


def list_approved(arrival_from: str | None = None, arrival_to: str | None = None, group_id: str | None = None,
                  limit: int = 25, cursor: str | None = None) -> tuple[list[dict[str, Any]], str | None, int]:
    """APPROVED rows as the desk's /completed shows them: group rows hidden until their arrival day."""
    rows = app().repo.list_all_approved(visible_groups_until=today().isoformat())
    if arrival_from:
        rows = [r for r in rows if (r.get("arrival") or "") >= arrival_from]
    if arrival_to:
        rows = [r for r in rows if (r.get("arrival") or "") <= arrival_to]
    if group_id:
        rows = [r for r in rows if r.get("group_id") == group_id]
    return _paginate(rows, limit, cursor)


def get_registration(token: str) -> dict[str, Any]:
    rec = app().repo.get_registration(token)
    if rec is None:
        raise NotFound("registration", token, "list_pending_registrations or list_approved_registrations")
    return rec


def get_signature(token: str) -> dict[str, Any] | None:
    return app().repo.get_signature(token)


def minor_alerts() -> list[dict[str, Any]]:
    """Under-18 guests in the pending queue or approved for today, with the under-15 signature rule."""
    a = app()
    t = today().isoformat()
    rows = list(a.repo.list_pending())
    rows += [r for r in a.repo.list_all_approved(visible_groups_until=t) if r.get("arrival") == t]
    out = []
    for rec in rows:
        if not is_under_18(rec):
            continue
        sig = a.repo.get_signature(rec["token"])
        role = sig["signer_role"] if sig else None
        out.append({**rec, "signer_role": role,
                    "needs_legal_representative": is_under_15(rec) and role != "legal_representative"})
    return out


def list_groups(status: GroupStatus = "all") -> list[dict[str, Any]]:
    g = app().groups
    rows: list[dict[str, Any]] = []
    if status in ("pending", "all"):
        rows += g.list_pending()
    if status in ("submitted", "all"):
        rows += g.list_submitted()
    for r in rows:
        r["member_count"] = (len(g.members(r["slug"])) if r["status"] == g.SUBMITTED
                             else len(g.member_rows(r["slug"])))
    return rows


def _dupe_key(row: dict[str, Any]) -> tuple[str, str, str] | None:
    first = str(row.get("first_name") or "").strip().casefold()
    last = str(row.get("last_name") or "").strip().casefold()
    dob = str(row.get("dob") or "").strip()
    return (first, last, dob) if first and last and dob else None


def duplicate_tokens(members: list[dict[str, Any]]) -> list[list[str]]:
    """Injected members sharing (first, last, dob) — same rule as the app's panel check, keyed by token."""
    buckets: dict[tuple[str, str, str], list[str]] = {}
    for m in members:
        key = _dupe_key(m)
        if key is not None:
            buckets.setdefault(key, []).append(m["token"])
    return sorted([sorted(v) for v in buckets.values() if len(v) >= 2])


def get_group(slug: str) -> dict[str, Any]:
    """Group header plus either the injected members (SUBMITTED) or the live panel (PENDING)."""
    g = app().groups
    group = g.get(slug)
    if group is None:
        raise NotFound("group", slug, "list_groups")
    if group["status"] == g.SUBMITTED:
        members = g.members(slug)
        return {"group": {**group, "member_count": len(members)}, "members": members, "panel_rows": [],
                "total": len(members), "filled": len(members), "attention_ids": [],
                "duplicates": duplicate_tokens(members)}
    panel = g.panel_state(slug)
    return {"group": {**group, "member_count": panel["total"]}, "members": [], "panel_rows": panel["rows"],
            "total": panel["total"], "filled": panel["filled"], "attention_ids": panel["attention_ids"],
            "duplicates": [[str(i) for i in grp] for grp in panel["duplicates"]]}


def month_summary() -> list[dict[str, Any]]:
    return app().repo.month_summary()


def audit_recent(limit: int = 50) -> list[dict[str, Any]]:
    return app().repo.audit_recent(limit)
