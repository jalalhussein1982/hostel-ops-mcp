"""MCP server over the hostel check-in system.

    uv run python -m hostel_ops_mcp.server --stdio     # local, no auth, STDIO_SCOPES apply
    uv run python -m hostel_ops_mcp.server --http      # Streamable HTTP on 127.0.0.1:8000/mcp
    OAUTH_ISSUER_URL=... RESOURCE_SERVER_URL=... uv run python -m hostel_ops_mcp.server --http

What this server is, in one paragraph: the hostel app's service layer (``app.repo``,
``app.groups``, ``app.state``, ``app.export``) imported from a pinned submodule and put
behind sixteen tools in a fixed order. Reads redact by scope (``redact.py``); writes
run the app's own state machine and write audit rows; the two destructive tools act
only after a human confirms through a Multi Round-Trip Request (``confirm.py``). Over
HTTP the server is an OAuth 2.1 resource server (``auth.py``); over stdio it runs with
the read scopes in ``STDIO_SCOPES``.

Design choices, stated once here and once in the README:

* ``ops.py`` has no MCP in it. This file is a thin adapter.
* ``ToolError`` for anything the model can fix (unknown token, wrong state, missing
  scope); its message names the tool to call next. Anything else is a crash.
* Every tool returns a Pydantic model, so ``structured_content`` matches a declared
  output schema. Tools register in spec order; a test pins it.
* Nothing here changes the app: what the HTTP layer did (state checks, APPROVED-only
  edit, the purge guard) is re-implemented explicitly and tested.
"""

from __future__ import annotations

import base64
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from . import guards, ops, redact
from .models import (
    AuditRow, GroupCard, GroupDetail, GroupMemberRow, MinorAlert, MonthRow,
    Registration, RegistrationCard, RegistrationPage, SignatureInfo,
)

mcp = MCPServer(
    "hostel-ops",
    instructions=(
        "Operations view of a hostel guest-registration system. Registrations move "
        "DRAFT -> PENDING_REVIEW -> APPROVED (or RETURNED with a reason). Start with "
        "list_pending_registrations and desk_minor_alerts; approve_registration and "
        "return_registration act on the queue. Document numbers, dates of birth, "
        "addresses and signatures are returned only to callers holding pii:read. "
        "erase_registration and purge_month ask the human to confirm before acting. "
        "Read hostel://policy/roles before telling a user what a token can do."
    ),
)

READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)


def _read(title: str) -> ToolAnnotations:
    return ToolAnnotations(title=title, read_only_hint=True, destructive_hint=False,
                           idempotent_hint=True, open_world_hint=False)


def _card(rec: dict[str, Any], scopes: set[str]) -> RegistrationCard:
    out = redact.apply(rec, "guest_registration", scopes)
    out["under18"] = ops.is_under_18(rec)
    out["is_group_leader"] = bool(rec.get("is_group_leader"))
    return RegistrationCard.model_validate(out)


def _not_found(e: ops.NotFound) -> ToolError:
    return ToolError(str(e))


# ── read tools (spec §4.1) ──────────────────────────────────────────────────────────


@mcp.tool(annotations=_read("Pending queue"))
async def list_pending_registrations(
    ctx: Context,
    limit: Annotated[int, Field(ge=1, le=100, description="Rows per page.")] = 25,
    cursor: Annotated[str | None, Field(description="next_cursor from the previous page.")] = None,
) -> RegistrationPage:
    """The PENDING_REVIEW queue, oldest first, as the receptionist sees it.

    Each card carries `under18` (under 18 on arrival) and `is_group_leader`. Document
    numbers are masked unless the token holds pii:read."""
    caller = guards.require(ctx, "list_pending_registrations")
    try:
        rows, nxt, total = ops.list_pending(limit, cursor)
    except ops.BadCursor as e:
        raise ToolError(str(e))
    return RegistrationPage(items=[_card(r, set(caller.scopes)) for r in rows], next_cursor=nxt, total=total)


@mcp.tool(annotations=_read("Approved registrations"))
async def list_approved_registrations(
    ctx: Context,
    arrival_from: Annotated[str | None, Field(description="ISO date, inclusive.")] = None,
    arrival_to: Annotated[str | None, Field(description="ISO date, inclusive.")] = None,
    group_id: Annotated[str | None, Field(description="Group slug; only that group's rows.")] = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 25,
    cursor: Annotated[str | None, Field(description="next_cursor from the previous page.")] = None,
) -> RegistrationPage:
    """APPROVED registrations, mirroring the desk's completed list.

    Group rows with a future arrival are hidden until their arrival day, exactly as on
    the desk. Filter by an arrival window or a group."""
    caller = guards.require(ctx, "list_approved_registrations")
    try:
        rows, nxt, total = ops.list_approved(arrival_from, arrival_to, group_id, limit, cursor)
    except ops.BadCursor as e:
        raise ToolError(str(e))
    return RegistrationPage(items=[_card(r, set(caller.scopes)) for r in rows], next_cursor=nxt, total=total)


@mcp.tool(annotations=_read("One registration"))
async def get_registration(
    ctx: Context,
    token: Annotated[str, Field(description="Registration token from a list tool.")],
    include_signature: Annotated[bool, Field(description="Also return the signature PNG (pii:read only).")] = False,
) -> Registration:
    """One registration in any state, with the fields the caller's scopes allow.

    A DRAFT (guest still in the wizard) is returned as a card with state=DRAFT, never an
    error. Signature provenance (signer_role, signed_at) is always included when a
    signature exists; the image and the signer's name need pii:read."""
    caller = guards.require(ctx, "get_registration")
    scopes = set(caller.scopes)
    try:
        rec = ops.get_registration(token)
    except ops.NotFound as e:
        raise _not_found(e)
    out = redact.apply(rec, "guest_registration", scopes)
    out["under18"] = ops.is_under_18(rec)
    out["is_group_leader"] = bool(rec.get("is_group_leader"))
    sig = ops.get_signature(token)
    if sig is not None:
        s = redact.apply(sig, "guest_signature", scopes)
        png = s.pop("signature_png", None)
        if include_signature and png is not None:
            s["signature_png_base64"] = base64.b64encode(png).decode("ascii")
        out["signature"] = SignatureInfo.model_validate(s)
    return Registration.model_validate(out)


@mcp.tool(annotations=_read("Minor alerts"))
async def desk_minor_alerts(ctx: Context) -> list[MinorAlert]:
    """Under-18 guests in the pending queue or approved for today.

    `needs_legal_representative` is set for an under-15 whose signature is not by a
    legal representative; the desk must collect one before approving."""
    caller = guards.require(ctx, "desk_minor_alerts")
    out = []
    for rec in ops.minor_alerts():
        r = redact.apply(rec, "guest_registration", set(caller.scopes))
        r["signer_role"] = rec["signer_role"]
        r["needs_legal_representative"] = rec["needs_legal_representative"]
        out.append(MinorAlert.model_validate(r))
    return out


def _group_card(g: dict[str, Any], scopes: set[str]) -> GroupCard:
    out = redact.apply(g, "group_request", scopes)
    out["member_count"] = g.get("member_count", 0)
    return GroupCard.model_validate(out)


@mcp.tool(annotations=_read("Groups"))
async def list_groups(
    ctx: Context,
    status: Annotated[ops.GroupStatus, Field(description="pending = portal open; submitted = members injected.")] = "all",
) -> list[GroupCard]:
    """Group pre-registrations. Leader contact details need pii:read."""
    caller = guards.require(ctx, "list_groups")
    return [_group_card(g, set(caller.scopes)) for g in ops.list_groups(status)]


@mcp.tool(annotations=_read("One group"))
async def get_group(
    ctx: Context,
    slug: Annotated[str, Field(description="Group slug from list_groups.")],
) -> GroupDetail:
    """One group with its panel state: totals, rows needing attention, duplicate people.

    A SUBMITTED group lists its injected registrations as cards; a PENDING group lists
    the live staging rows of the open portal."""
    caller = guards.require(ctx, "get_group")
    scopes = set(caller.scopes)
    try:
        d = ops.get_group(slug)
    except ops.NotFound as e:
        raise _not_found(e)
    rows = []
    for r in d["panel_rows"]:
        row = redact.apply(r, "group_member", scopes)
        row["is_leader"] = bool(r.get("is_leader"))
        rows.append(GroupMemberRow.model_validate(row))
    return GroupDetail(
        group=_group_card(d["group"], scopes), total=d["total"], filled=d["filled"],
        attention_ids=d["attention_ids"], duplicates=d["duplicates"],
        members=[_card(m, scopes) for m in d["members"]], panel_rows=rows,
    )


@mcp.tool(annotations=_read("Months"))
async def month_summary(ctx: Context) -> list[MonthRow]:
    """One row per arrival month: live approved count joined with the backup log
    (entries backed up, verified, purged). Admin only."""
    guards.require(ctx, "month_summary")
    return [MonthRow.model_validate(m) for m in ops.month_summary()]


@mcp.tool(annotations=_read("Audit log"))
async def audit_log(
    ctx: Context,
    limit: Annotated[int, Field(ge=1, le=200)] = 50,
) -> list[AuditRow]:
    """Most recent admin_audit rows, newest first. Rows this server writes carry the
    actor `mcp:<subject>`."""
    guards.require(ctx, "audit_log")
    return [AuditRow.model_validate(a) for a in ops.audit_recent(limit)]


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
