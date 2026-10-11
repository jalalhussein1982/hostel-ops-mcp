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
import json
import os
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.server.auth.provider import TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import Context, RequestStateSecurity
from mcp.server.mcpserver.exceptions import ResourceError, ResourceNotFoundError, ToolError
from mcp.types import InputRequiredResult, ToolAnnotations
from pydantic import BaseModel, Field

from . import confirm, guards, handles, ops, policy, redact
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


POLICY_PREFIX = "hostel://policy/"
POLICY_TTL_MS = 3_600_000


async def cache_directives(ctx, call_next):
    """Spec §5: policy resources are public for an hour; records are private and immediately stale.

    The SDK stamps ttlMs=0 / cacheScope=private on every 2026-07-28 read by default; this
    middleware overrides the two policy URIs. Results may arrive as a model or a wire mapping."""
    result = await call_next(ctx)
    if ctx.method != "resources/read" or not (ctx.params or {}).get("uri", "").startswith(POLICY_PREFIX):
        return result
    if isinstance(result, BaseModel):
        return result.model_copy(update={"ttl_ms": POLICY_TTL_MS, "cache_scope": "public"})
    if isinstance(result, dict):
        return {**result, "ttlMs": POLICY_TTL_MS, "cacheScope": "public"}
    return result


def create_server(*, token_verifier: TokenVerifier | None = None, auth: AuthSettings | None = None,
                  request_state_security: RequestStateSecurity | None = None,
                  auth_required: bool | None = None) -> MCPServer:
    """Build a server with every tool, resource and prompt registered in spec order.

    Without ``token_verifier``/``auth`` the instance serves stdio and in-memory clients
    with ``STDIO_SCOPES``; with them its Streamable HTTP app is an OAuth 2.1 resource server
    and ``auth_required`` defaults to True: a request without a verified token is refused by
    the guards themselves, never mapped onto ``STDIO_SCOPES`` (defence in depth behind the
    SDK's bearer middleware).
    """
    mcp = MCPServer("hostel-ops", instructions=INSTRUCTIONS, token_verifier=token_verifier, auth=auth,
                    request_state_security=request_state_security, middleware=[cache_directives])
    mcp._hostel_auth_required = bool(token_verifier is not None if auth_required is None else auth_required)
    _register(mcp)
    return mcp


def _register(mcp: MCPServer) -> None:
    def _read(title: str) -> ToolAnnotations:
        return ToolAnnotations(title=title, read_only_hint=True, destructive_hint=False,
                               idempotent_hint=True, open_world_hint=False)


    def _strip_token(d: dict[str, Any], scopes: set[str]) -> dict[str, Any]:
        """Write results: the raw token only for pii:read holders; `ref` always."""
        if redact.PII_SCOPE not in scopes:
            d = {**d, "token": None}
        return d

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
        state: Annotated[ops.QueueState, Field(
            description="PENDING_REVIEW (default, the desk queue), RETURNED (sent back, not yet resubmitted) or DRAFT.")] = "PENDING_REVIEW",
    ) -> RegistrationPage:
        """The desk queue, oldest first, as the receptionist sees it: PENDING_REVIEW by default,
        or the RETURNED / DRAFT rows with `state`.

        Each card carries `under18` (under 18 on arrival), `is_group_leader`, the 4-digit desk
        `code`, and `return_step`/`return_reason` for RETURNED rows (the reason needs pii:read).
        Document numbers are masked unless the token holds pii:read."""
        caller = guards.require(ctx, "list_pending_registrations")
        try:
            rows, nxt, total = ops.list_pending(limit, cursor, state)
        except (ops.BadCursor, ops.Invalid) as e:
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
        token: Annotated[str, Field(description="A registration `ref` (from any list) or a raw token.")],
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
        sig = ops.get_signature(rec["token"])
        if sig is not None:
            s = redact.apply(sig, "guest_signature", scopes)
            s.pop("ref", None)
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
        token: Annotated[str, Field(description="A PENDING_REVIEW registration: its `ref` from list_pending_registrations, or the raw token.")],
    ) -> ApprovalResult:
        """Approve a pending registration: the app assigns the per-arrival serial and stores
        the address in capitals. Only PENDING_REVIEW rows can be approved; the error names
        the current state and the actions it allows. Calling it again on an APPROVED row
        returns the same result with already_approved=true, so retries are safe. Audited."""
        caller = guards.require(ctx, "approve_registration")
        try:
            return ApprovalResult.model_validate(_strip_token(ops.approve(token, caller.actor), set(caller.scopes)))
        except (ops.NotFound, ops.InvalidState) as e:
            raise _as_tool_error(e)


    @mcp.tool(annotations=_write("Return registration", idempotent=False))
    async def return_registration(
        ctx: Context,
        token: Annotated[str, Field(description="A PENDING_REVIEW registration (`ref` or raw token).")],
        reason: Annotated[str, Field(min_length=1, max_length=500, description="Shown to the guest.")],
        step: Annotated[int, Field(ge=1, le=3, description="Wizard page to reopen: 1 dates, 2 personal, 3 address.")],
    ) -> ReturnResult:
        """Send a pending registration back to the guest with a reason, reopening one wizard
        page, as the desk's Return button does. PENDING_REVIEW -> RETURNED. Audited."""
        caller = guards.require(ctx, "return_registration")
        try:
            return ReturnResult.model_validate(_strip_token(ops.return_registration(token, reason, step, caller.actor), set(caller.scopes)))
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
        use resend_group_invite to issue a new one. Needs `groups:write` and `pii:read`: the
        password opens the leader portal, which shows every member's data."""
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
        tokens: Annotated[list[str] | None, Field(description="Explicit registrations (`ref`s or raw tokens); non-APPROVED ones are skipped.")] = None,
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
        token: Annotated[str, Field(description="An APPROVED registration (`ref` or raw token).")],
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
        token: Annotated[str, Field(description="The registration to erase (`ref` or raw token).")],
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
        who = rec.get("serial") or handles.ref_for(rec["token"])
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
        return EraseResult.model_validate(_strip_token(ops.erase(token, caller.actor), set(caller.scopes)))


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
        is ever put to the human, and the confirmation quotes the entry count, the number of
        group requests the app removes with the month (open portals included) and the
        snapshot checksum; all three are bound into the request state, so if any changes
        between the question and the answer the human is asked again. Accept deletes
        atomically (the app re-checks the count inside the transaction); decline cancels."""
        caller = guards.require(ctx, "purge_month")
        rep = ops.purge_guard(period)
        if not rep.ok:
            raise ToolError(rep.message())
        groups_txt = (f"{rep.group_requests} group request{'s' if rep.group_requests != 1 else ''} for the month "
                      f"(pending portals included)" if rep.group_requests else "no group requests")
        message = (
            f"Purge {period}: delete {rep.entry_count} approved registrations and {groups_txt}? "
            f"The verified encrypted snapshot {rep.snapshot_name} (sha256 {rep.snapshot_sha256[:12]}…) stays on file; "
            "the live rows, their signatures and the group requests are removed. This cannot be undone."
        )
        outcome = confirm.confirm_or_request(ctx, "confirm", message, state=ops.purge_state(period, rep))
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


    def _require_resource(uri: str, *scopes: str, ctx: Context | None = None) -> None:
        try:
            caller = guards.current_caller(ctx)
        except ToolError as e:
            raise ResourceError(str(e))
        for sc in scopes:
            if sc not in caller.scopes:
                raise ResourceError(f"missing scope {sc}; this token cannot read {uri}")


    def _export_blob(fmt: str, export_id: str, ctx: Context) -> bytes:
        uri = f"export://{fmt}/{export_id}"
        _require_resource(uri, "export", "pii:read", ctx=ctx)
        data = ops.read_export(fmt, export_id)
        if data is None:
            raise ResourceNotFoundError(f"{uri} (expired or never existed; exports live EXPORT_TTL_MINUTES)")
        return data


    @mcp.resource("export://xlsx/{export_id}", mime_type=ops.EXPORT_MIME["xlsx"], title="Authority export (XLSX)")
    def export_xlsx(export_id: str, ctx: Context) -> bytes:
        """Bytes of an XLSX export produced by export_authority_report. `export` + `pii:read`."""
        return _export_blob("xlsx", export_id, ctx)


    @mcp.resource("export://pdf/{export_id}", mime_type=ops.EXPORT_MIME["pdf"], title="Authority export (PDF)")
    def export_pdf(export_id: str, ctx: Context) -> bytes:
        """Bytes of a PDF export produced by export_authority_report. `export` + `pii:read`."""
        return _export_blob("pdf", export_id, ctx)


    @mcp.resource("hostel://policy/roles", mime_type="text/markdown", title="Scopes, tools and the field policy")
    def policy_roles() -> str:
        """The scope -> tool matrix and the per-column redaction policy. Quote it; do not guess."""
        return policy.roles_text()

    @mcp.resource("hostel://policy/retention", mime_type="text/markdown", title="Retention and erasure")
    def policy_retention() -> str:
        """Legal basis, retention, erasure position and the purge guard, in the words of the app's privacy notice."""
        return policy.retention_text()

    @mcp.resource("hostel://registration/{token}", mime_type="application/json", title="One registration")
    async def registration_resource(token: str, ctx: Context) -> str:
        """Same document get_registration returns, as a resource. `token` is a `ref` or a raw token. desk:read."""
        _require_resource(f"hostel://registration/{token}", "desk:read", ctx=ctx)
        try:
            reg = await get_registration(ctx, token)   # the tool function, with its own guard and redaction
        except ToolError as e:
            raise ResourceNotFoundError(str(e))
        return reg.model_dump_json()

    @mcp.resource("hostel://group/{slug}", mime_type="application/json", title="One group")
    async def group_resource(slug: str, ctx: Context) -> str:
        """Same document get_group returns, as a resource. groups:read."""
        _require_resource(f"hostel://group/{slug}", "groups:read", ctx=ctx)
        try:
            g = await get_group(ctx, slug)
        except ToolError as e:
            raise ResourceNotFoundError(str(e))
        return g.model_dump_json()

    # ── prompts ──────────────────────────────────────────────────────────────────────

    @mcp.prompt(title="Morning desk briefing")
    def morning_desk_briefing() -> str:
        """The receptionist's handover note, in the house format, from three read tools."""
        return (
            "You are preparing the morning handover for the hostel reception desk. Call "
            "list_pending_registrations (walk every page with next_cursor), desk_minor_alerts, "
            "list_groups with status=pending and then get_group for each pending group, and "
            "list_pending_registrations with state=RETURNED. Then write the handover in this format:\n\n"
            "1. Arrivals today: pending cards whose arrival is today, oldest first, with last_name, "
            "first_name, nationality, nights and the 4-digit code.\n"
            "2. Minors: every alert, with age_band and whether a legal representative's signature is "
            "still needed before approval (needs_legal_representative).\n"
            "3. Groups awaiting members: each pending group's group_name, arrival, filled of total rows, "
            "attention_ids and duplicates from get_group, and whether invite_sent_at is set.\n"
            "4. Returned and not resubmitted: RETURNED cards whose created_at is older than 24 hours, "
            "with return_step (1 dates, 2 personal, 3 address); quote return_reason only if it is present.\n\n"
            "Use the masked document numbers and the ref values exactly as returned; never guess a "
            "full number or a token. Do not approve or return anything in this briefing."
        )

    @mcp.prompt(title="Authority export checklist")
    def authority_export_checklist(
        period: Annotated[str, Field(description="Arrival month, YYYY-MM.")],
    ) -> str:
        """Steps and checks before a month's registration export leaves the building."""
        return (
            f"Prepare the registration export for {period} for the foreign police.\n\n"
            f"1. Call month_summary and confirm {period} appears with its live_count; note whether "
            "the month is backed up and verified.\n"
            f"2. Call list_approved_registrations with arrival_from={period}-01 and arrival_to the last "
            "day of the month, walking every page; confirm the count equals live_count.\n"
            "3. Call desk_minor_alerts and list any under-15 whose signature is not by a legal "
            "representative; those records must be fixed on the desk before export.\n"
            "4. Check that no row in the window is PENDING_REVIEW or RETURNED (they are not exported; "
            "say so explicitly if any exist).\n"
            f"5. Call export_authority_report with arrival_from/arrival_to for {period} and format=xlsx. "
            "Report rows, skipped, sha256 and the resource_uri. Read hostel://policy/retention and "
            "remind the operator that the export contains document numbers and must be handled as such."
        )


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


LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def check_http_mode(host: str, auth_configured: bool, insecure: bool) -> None:
    """Refuse the footguns of unauthenticated HTTP unless --insecure-no-auth says so explicitly.

    Without OAuth the HTTP transport authenticates nobody and every caller gets STDIO_SCOPES.
    That is acceptable on loopback with read scopes; anything else must be asked for by name."""
    if auth_configured or insecure:
        return
    problems = []
    if host.strip("[]") not in LOOPBACK_HOSTS:
        problems.append(f"--host {host} is not loopback")
    privileged = sorted(guards.stdio_scopes() & guards.WRITE_OR_PII_SCOPES)
    if privileged:
        problems.append(f"STDIO_SCOPES grants {', '.join(privileged)}")
    if problems:
        raise SystemExit(
            "refusing to serve unauthenticated HTTP: " + "; ".join(problems) + ". Set OAUTH_ISSUER_URL "
            "(resource-server mode) or pass --insecure-no-auth if you really mean it."
        )


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
    ap.add_argument("--insecure-no-auth", action="store_true",
                    help="HTTP without OAuth on a non-loopback host or with write/pii STDIO_SCOPES (never in production)")
    args = ap.parse_args(argv)
    ops.app()  # bind the database early so a bad HOSTEL_DATABASE_PATH fails at start-up
    ops.sweep_exports()  # drop exports a previous process left behind past their TTL
    if args.http:
        check_http_mode(args.host, bool(os.environ.get("OAUTH_ISSUER_URL", "").strip()), args.insecure_no_auth)
        server_from_env().run(transport="streamable-http", host=args.host, port=args.port,
                              stateless_http=not args.stateful)
    else:
        server_from_env().run()


if __name__ == "__main__":
    main()
