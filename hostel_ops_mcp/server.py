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

import argparse
import base64
import os
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.server.auth.provider import TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import Context, RequestStateSecurity
from mcp.server.mcpserver.exceptions import ResourceError, ResourceNotFoundError, ToolError
from mcp.types import InputRequiredResult, ToolAnnotations
from pydantic import Field

from . import confirm, guards, ops, redact
from .models import (
    ApprovalResult, AuditRow, Cancelled, EditResult, EraseResult, ExportResult, GroupCard, GroupDetail, GroupLink, GroupMemberRow,
    MinorAlert, MonthRow, PurgeResult, Registration, RegistrationCard, RegistrationPage, ReturnResult, SignatureInfo,
)

INSTRUCTIONS = (
    "Operations view of a hostel guest-registration system. Registrations move "
    "DRAFT -> PENDING_REVIEW -> APPROVED (or RETURNED with a reason). Start with "
    "list_pending_registrations and desk_minor_alerts; approve_registration and "
    "return_registration act on the queue. Document numbers, dates of birth, "
    "addresses and signatures are returned only to callers holding pii:read. "
    "erase_registration and purge_month ask the human to confirm before acting. "
    "Read hostel://policy/roles before telling a user what a token can do."
)


def create_server(*, token_verifier: TokenVerifier | None = None, auth: AuthSettings | None = None,
                  request_state_security: RequestStateSecurity | None = None) -> MCPServer:
    """Build a server with every tool, resource and prompt registered in spec order.

    Without ``token_verifier``/``auth`` the instance serves stdio and in-memory clients
    with ``STDIO_SCOPES``; with them its Streamable HTTP app is an OAuth 2.1 resource server.
    """
    mcp = MCPServer("hostel-ops", instructions=INSTRUCTIONS, token_verifier=token_verifier, auth=auth,
                    request_state_security=request_state_security)
    _register(mcp)
    return mcp


def _register(mcp: MCPServer) -> None:
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


    # ── write tools, non-destructive (spec §4.2) ────────────────────────────────────────


    def _write(title: str, *, idempotent: bool, open_world: bool = False) -> ToolAnnotations:
        return ToolAnnotations(title=title, read_only_hint=False, destructive_hint=False,
                               idempotent_hint=idempotent, open_world_hint=open_world)


    def _as_tool_error(e: Exception) -> ToolError:
        if isinstance(e, ops.NotFound):
            return ToolError(str(e))
        if isinstance(e, ops.InvalidState):
            return ToolError(str(e))
        if isinstance(e, ops.Invalid):
            return ToolError("invalid input: " + str(e))
        raise e


    @mcp.tool(annotations=_write("Approve registration", idempotent=True))
    async def approve_registration(
        ctx: Context,
        token: Annotated[str, Field(description="A PENDING_REVIEW token from list_pending_registrations.")],
    ) -> ApprovalResult:
        """Approve a pending registration: the app assigns the per-arrival serial and stores
        the address in capitals. Only PENDING_REVIEW rows can be approved; the error names
        the current state and the actions it allows. Calling it again on an APPROVED row
        returns the same result with already_approved=true, so retries are safe. Audited."""
        caller = guards.require(ctx, "approve_registration")
        try:
            return ApprovalResult.model_validate(ops.approve(token, caller.actor))
        except (ops.NotFound, ops.InvalidState) as e:
            raise _as_tool_error(e)


    @mcp.tool(annotations=_write("Return registration", idempotent=False))
    async def return_registration(
        ctx: Context,
        token: Annotated[str, Field(description="A PENDING_REVIEW token.")],
        reason: Annotated[str, Field(min_length=1, max_length=500, description="Shown to the guest.")],
        step: Annotated[int, Field(ge=1, le=3, description="Wizard page to reopen: 1 dates, 2 personal, 3 address.")],
    ) -> ReturnResult:
        """Send a pending registration back to the guest with a reason, reopening one wizard
        page, as the desk's Return button does. PENDING_REVIEW -> RETURNED. Audited."""
        caller = guards.require(ctx, "return_registration")
        try:
            return ReturnResult.model_validate(ops.return_registration(token, reason, step, caller.actor))
        except (ops.NotFound, ops.InvalidState, ops.Invalid) as e:
            raise _as_tool_error(e)


    @mcp.tool(annotations=_write("Create group link", idempotent=False, open_world=True))
    async def create_group_link(
        ctx: Context,
        group_name: Annotated[str, Field(min_length=1, max_length=120)],
        leader_first_name: Annotated[str, Field(min_length=1)],
        leader_last_name: Annotated[str, Field(min_length=1)],
        arrival: Annotated[str, Field(description="ISO date, group-wide.")],
        departure: Annotated[str, Field(description="ISO date, after arrival.")],
        receiver_email: Annotated[str, Field(description="Where the invite goes.")],
        purpose_of_stay: Annotated[Literal["leisure", "business"], Field()] = "leisure",
    ) -> GroupLink:
        """Open a group pre-registration: creates the PENDING request, generates the portal
        password and emails the invite (in synthetic mode the email is recorded, not sent).

        The password appears in this result once. The server stores only its argon2 hash;
        use resend_group_invite to issue a new one."""
        caller = guards.require(ctx, "create_group_link")
        fields = {"group_name": group_name, "leader_first_name": leader_first_name,
                  "leader_last_name": leader_last_name, "arrival": arrival, "departure": departure,
                  "receiver_email": receiver_email, "purpose_of_stay": purpose_of_stay}
        try:
            return GroupLink.model_validate(ops.create_group_link(fields, caller.actor))
        except ops.Invalid as e:
            raise _as_tool_error(e)


    @mcp.tool(annotations=_write("Resend group invite", idempotent=False, open_world=True))
    async def resend_group_invite(
        ctx: Context,
        slug: Annotated[str, Field(description="A PENDING group's slug.")],
        receiver_email: Annotated[str | None, Field(description="Corrected address; default: the stored one.")] = None,
    ) -> GroupLink:
        """Re-issue a PENDING group's invite with a fresh portal password (the old one is
        unrecoverable from its hash), optionally to a corrected email address."""
        caller = guards.require(ctx, "resend_group_invite")
        try:
            return GroupLink.model_validate(ops.resend_group_invite(slug, receiver_email, caller.actor))
        except (ops.NotFound, ops.InvalidState, ops.Invalid) as e:
            raise _as_tool_error(e)


    # ── export and admin edit (spec §4.2 export row, §4.3) ──────────────────────────────


    @mcp.tool(annotations=ToolAnnotations(title="Authority export", read_only_hint=False, destructive_hint=False,
                                          idempotent_hint=False, open_world_hint=False))
    async def export_authority_report(
        ctx: Context,
        tokens: Annotated[list[str] | None, Field(description="Explicit registrations; non-APPROVED ones are skipped.")] = None,
        arrival_from: Annotated[str | None, Field(description="Or an arrival window: ISO date, inclusive.")] = None,
        arrival_to: Annotated[str | None, Field(description="ISO date, inclusive.")] = None,
        format: Annotated[Literal["xlsx", "pdf"], Field(description="File format.")] = "xlsx",
    ) -> ExportResult:
        """Build the registration export the hostel hands to the authority (the app's own
        XLSX/PDF layout) from APPROVED rows only. Needs `export` and `pii:read`.

        The file is written server-side and expires after EXPORT_TTL_MINUTES; read the bytes
        through the returned `resource_uri`. Tokens that are not APPROVED are listed in
        `skipped` and never exported. Audited with counts, never names."""
        caller = guards.require(ctx, "export_authority_report")
        try:
            return ExportResult.model_validate(ops.export(tokens, arrival_from, arrival_to, format, caller.actor))
        except ops.Invalid as e:
            raise _as_tool_error(e)


    @mcp.tool(annotations=ToolAnnotations(title="Edit approved registration", read_only_hint=False,
                                          destructive_hint=False, idempotent_hint=True, open_world_hint=False))
    async def edit_approved_registration(
        ctx: Context,
        token: Annotated[str, Field(description="An APPROVED token.")],
        fields: Annotated[dict[str, str | int | None], Field(
            description="Wizard fields to change: arrival, departure, purpose_of_stay, last_name, first_name, dob, "
                        "passport_no, nationality (alpha-3), visa_no, car_reg, addr_line, addr_postcode, addr_city, "
                        "addr_country, addr_structured.")],
    ) -> EditResult:
        """Correct an APPROVED registration with the same validators the guest wizard uses.
        Needs `admin:write` and `pii:read`. Only the named fields change; moving the arrival
        re-issues the serial from the new date's counter (old numbers are never reused).
        Audited with the changed field names, never the values."""
        caller = guards.require(ctx, "edit_approved_registration")
        try:
            return EditResult.model_validate(ops.edit_approved(token, fields, caller.actor))
        except (ops.NotFound, ops.InvalidState, ops.Invalid) as e:
            raise _as_tool_error(e)


    # ── destructive tools, confirmed by Multi Round-Trip Request (spec §4.4) ────────────


    def _destructive(title: str) -> ToolAnnotations:
        return ToolAnnotations(title=title, read_only_hint=False, destructive_hint=True,
                               idempotent_hint=False, open_world_hint=False)


    @mcp.tool(annotations=_destructive("Erase registration"))
    async def erase_registration(
        ctx: Context,
        token: Annotated[str, Field(description="Registration token to erase.")],
    ) -> EraseResult | Cancelled | InputRequiredResult:
        """Erase one registration after the human confirms.

        First call: returns a confirmation request naming the serial and arrival date and
        stating the legal position (Act 326/1999 requires the hostel to keep registrations;
        erasure is limited to records no longer required). The host shows it to the human
        and retries with the answer. Accept with confirm=true deletes the row and its
        signature and writes an audit row; decline returns a cancelled result. Hosts that
        cannot ask get an error instead and must use the admin panel."""
        caller = guards.require(ctx, "erase_registration")
        try:
            rec = ops.get_registration(token)
        except ops.NotFound as e:
            raise _not_found(e)
        who = rec.get("serial") or token
        message = (
            f"Erase registration {who} (arrival {rec.get('arrival') or 'not set'}, state {rec['state']})? "
            "Legal basis is Act 326/1999; erasure is limited to records no longer required. "
            "This cannot be undone."
        )
        outcome = confirm.confirm_or_request(ctx, "confirm", message, state=f"erase:{token}")
        if outcome.status == "ask":
            return outcome.result
        if outcome.status == "declined":
            return Cancelled(reason="declined" if outcome.action == "decline" else "cancelled",
                             message=f"Registration {who} was not erased.")
        return EraseResult.model_validate(ops.erase(token, caller.actor))


    @mcp.tool(annotations=_destructive("Purge month"))
    async def purge_month(
        ctx: Context,
        period: Annotated[str, Field(description="Arrival month, YYYY-MM.")],
    ) -> PurgeResult | Cancelled | InputRequiredResult:
        """Purge one arrival month's APPROVED registrations after the human confirms.

        The app's delete guard runs first, before any question: backup configured, a
        verified backup row for the month, live rows present, live count equal to the
        backed-up count, and the encrypted snapshot on disk, decrypting and re-verifying.
        A failing guard is an error naming the reason; only a purge that is actually allowed
        is ever put to the human, and the confirmation quotes the entry count and the
        snapshot checksum. Accept deletes atomically (the app re-checks the count inside
        the transaction); decline cancels."""
        caller = guards.require(ctx, "purge_month")
        rep = ops.purge_guard(period)
        if not rep.ok:
            raise ToolError(rep.message.format(period=period))
        message = (
            f"Purge {period}: delete {rep.entry_count} approved registrations whose verified encrypted "
            f"snapshot {rep.snapshot_name} (sha256 {rep.snapshot_sha256[:12]}…) is on file? "
            "The live rows and their signatures are removed; the snapshot remains. This cannot be undone."
        )
        outcome = confirm.confirm_or_request(ctx, "confirm", message, state=f"purge:{period}:{rep.entry_count}")
        if outcome.status == "ask":
            return outcome.result
        if outcome.status == "declined":
            return Cancelled(reason="declined" if outcome.action == "decline" else "cancelled",
                             message=f"{period} was not purged.")
        try:
            return PurgeResult.model_validate(ops.purge(period, rep.entry_count, caller.actor))
        except ops.PurgeAborted as e:
            raise ToolError(str(e))


    # ── resources ────────────────────────────────────────────────────────────────────────


    def _require_resource(uri: str, *scopes: str) -> None:
        caller = guards.current_caller()
        for sc in scopes:
            if sc not in caller.scopes:
                raise ResourceError(f"missing scope {sc}; this token cannot read {uri}")


    def _export_blob(fmt: str, export_id: str) -> bytes:
        uri = f"export://{fmt}/{export_id}"
        _require_resource(uri, "export", "pii:read")
        data = ops.read_export(fmt, export_id)
        if data is None:
            raise ResourceNotFoundError(f"{uri} (expired or never existed; exports live EXPORT_TTL_MINUTES)")
        return data


    @mcp.resource("export://xlsx/{export_id}", mime_type=ops.EXPORT_MIME["xlsx"], title="Authority export (XLSX)")
    def export_xlsx(export_id: str) -> bytes:
        """Bytes of an XLSX export produced by export_authority_report. `export` + `pii:read`."""
        return _export_blob("xlsx", export_id)


    @mcp.resource("export://pdf/{export_id}", mime_type=ops.EXPORT_MIME["pdf"], title="Authority export (PDF)")
    def export_pdf(export_id: str) -> bytes:
        """Bytes of a PDF export produced by export_authority_report. `export` + `pii:read`."""
        return _export_blob("pdf", export_id)


# ── wiring ───────────────────────────────────────────────────────────────────────────

mcp = create_server()
"""The default instance: stdio, in-memory tests, `uv run mcp dev`."""


def request_state_security_from_env() -> RequestStateSecurity | None:
    """REQUEST_STATE_KEYS: comma-separated secrets (>= 32 bytes each) shared across replicas.
    Unset -> the SDK's per-process ephemeral key (fine for one replica)."""
    raw = os.environ.get("REQUEST_STATE_KEYS", "").strip()
    if not raw:
        return None
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    ttl = float(os.environ.get("REQUEST_STATE_TTL", "300"))
    return RequestStateSecurity(keys=keys, ttl=ttl)


def server_from_env() -> MCPServer:
    """Resource-server mode when OAUTH_ISSUER_URL is set; plain server otherwise."""
    from .auth import JwtVerifier

    issuer = os.environ.get("OAUTH_ISSUER_URL", "").strip().rstrip("/")
    rss = request_state_security_from_env()
    if not issuer:
        return create_server(request_state_security=rss)
    resource = os.environ.get("RESOURCE_SERVER_URL", "http://127.0.0.1:8000/mcp").strip()
    jwks = os.environ.get("OAUTH_JWKS_URL", "").strip() or f"{issuer}/protocol/openid-connect/certs"
    verifier = JwtVerifier(jwks_url=jwks, issuer=issuer, audience=resource)
    auth = AuthSettings(issuer_url=issuer, resource_server_url=resource, required_scopes=["mcp"],
                        validate_token_resource=True)
    return create_server(token_verifier=verifier, auth=auth, request_state_security=rss)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="hostel-ops-mcp", description="MCP server over the hostel check-in system")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--stdio", action="store_true", help="stdio transport (default)")
    mode.add_argument("--http", action="store_true", help="Streamable HTTP on --host:--port/mcp")
    ap.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    ap.add_argument("--stateful", action="store_true", help="HTTP: keep per-session state (default: stateless)")
    args = ap.parse_args(argv)
    ops.app()  # bind the database early so a bad HOSTEL_DATABASE_PATH fails at start-up
    if args.http:
        server_from_env().run(transport="streamable-http", host=args.host, port=args.port,
                              stateless_http=not args.stateful)
    else:
        server_from_env().run()


if __name__ == "__main__":
    main()
