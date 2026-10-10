"""Task 6: approve / return / group link tools — state machine, idempotency, audit, no PII leak."""

from __future__ import annotations

import re

import pytest

SERIAL = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{4}$")


def _audit(app, action: str) -> list[dict]:
    return [a for a in app.repo.audit_recent(200) if a["action"] == action and a["actor"].startswith("mcp:")]


@pytest.mark.anyio
async def test_approve_pending_assigns_a_serial_and_audits(client, app, planted):
    token = planted["tokens"]["PENDING_REVIEW"][0]
    r = (await client.call_tool("approve_registration", {"token": token})).structured_content
    assert r["token"] == token and SERIAL.match(r["serial"]) and r["already_approved"] is False
    rec = app.repo.get_registration(token)
    assert rec["state"] == "APPROVED" and rec["serial"] == r["serial"] and rec["approved_at"] == r["approved_at"]
    rows = _audit(app, "approve")
    assert len(rows) == 1 and rows[0]["detail"] == token and rows[0]["actor"] == "mcp:stdio"


@pytest.mark.anyio
async def test_approve_twice_is_idempotent(client, app, planted):
    token = planted["tokens"]["PENDING_REVIEW"][1]
    first = (await client.call_tool("approve_registration", {"token": token})).structured_content
    second = (await client.call_tool("approve_registration", {"token": token})).structured_content
    assert second["already_approved"] is True and second["serial"] == first["serial"]
    assert len(_audit(app, "approve")) == 1


@pytest.mark.anyio
async def test_approve_draft_or_returned_is_a_tool_error_naming_the_state(client, planted):
    r = await client.call_tool("approve_registration", {"token": planted["tokens"]["DRAFT"][1]})
    assert r.is_error and "DRAFT" in r.content[0].text and "SUBMIT" in r.content[0].text
    r = await client.call_tool("approve_registration", {"token": planted["tokens"]["RETURNED"][0]})
    assert r.is_error and "RETURNED" in r.content[0].text


@pytest.mark.anyio
async def test_return_requires_a_step_between_one_and_three(client, app, planted):
    token = planted["tokens"]["PENDING_REVIEW"][2]
    bad = await client.call_tool("return_registration", {"token": token, "reason": "x", "step": 4})
    assert bad.is_error
    blank = await client.call_tool("return_registration", {"token": token, "reason": "   ", "step": 2})
    assert blank.is_error and "reason" in blank.content[0].text
    ok = (await client.call_tool("return_registration",
                                 {"token": token, "reason": "Passport number does not match", "step": 2})).structured_content
    assert ok["state"] == "RETURNED" and ok["return_step"] == 2
    rec = app.repo.get_registration(token)
    assert rec["state"] == "RETURNED" and rec["return_reason"] == "Passport number does not match"
    again = await client.call_tool("return_registration", {"token": token, "reason": "again", "step": 1})
    assert again.is_error and "RETURNED" in again.content[0].text
    assert len(_audit(app, "return")) == 1


@pytest.mark.anyio
async def test_create_group_link_returns_the_password_once(client, app):
    args = {"group_name": "Oakridge Chess Club", "leader_first_name": "Lio", "leader_last_name": "Varema",
            "arrival": "2026-12-04", "departure": "2026-12-06", "purpose_of_stay": "business",
            "receiver_email": "lio.varema@example.invalid"}
    r = (await client.call_tool("create_group_link", args)).structured_content
    assert r["portal_url"].endswith(f"/g/{r['slug']}") and "/gm/" in r["member_url"]
    assert len(r["portal_password"]) >= 8 and r["email_sent"] is False  # synthetic mode never sends
    g = app.groups.get(r["slug"])
    assert g["status"] == "PENDING" and g["purpose_of_stay"] == "business" and g["nights"] == 2
    assert g["password_hash"].startswith("$argon2") and app.auth.verify_password(r["portal_password"], g["password_hash"])
    assert r["portal_password"] not in str(g)
    bad = await client.call_tool("create_group_link", {**args, "departure": "2026-12-04"})
    assert bad.is_error and "departure" in bad.content[0].text
    assert len(_audit(app, "group_invite")) == 1


@pytest.mark.anyio
async def test_resend_rotates_the_password_and_fixes_the_email(client, app, planted):
    slug = planted["groups"]["pending"]
    before = app.groups.get(slug)["password_hash"]
    r = (await client.call_tool("resend_group_invite",
                                {"slug": slug, "receiver_email": "corrected@example.invalid"})).structured_content
    after = app.groups.get(slug)
    assert after["password_hash"] != before and after["receiver_email"] == "corrected@example.invalid"
    assert app.auth.verify_password(r["portal_password"], after["password_hash"])
    submitted = await client.call_tool("resend_group_invite", {"slug": planted["groups"]["submitted_visible"]})
    assert submitted.is_error and "SUBMITTED" in submitted.content[0].text
    missing = await client.call_tool("resend_group_invite", {"slug": "nope"})
    assert missing.is_error and "list_groups" in missing.content[0].text


@pytest.mark.anyio
async def test_desk_write_alone_can_approve_and_sees_no_pii(client_with_scopes, planted):
    """Review Focus 1: write does not imply read, and the write result leaks nothing."""
    token = planted["tokens"]["PENDING_REVIEW"][3]
    async with client_with_scopes({"desk:write"}) as c:
        r = await c.call_tool("approve_registration", {"token": token})
        assert r.is_error is False
        assert set(r.structured_content) == {"token", "serial", "approved_at", "already_approved"}
        denied = await c.call_tool("get_registration", {"token": token})
        assert denied.is_error and "desk:read" in denied.content[0].text


@pytest.mark.anyio
async def test_write_tool_annotations(client):
    tools = {t.name: t.annotations for t in (await client.list_tools()).tools}
    a = tools["approve_registration"]
    assert a.read_only_hint is False and a.destructive_hint is False and a.idempotent_hint is True
    assert tools["return_registration"].idempotent_hint is False
    for name in ("create_group_link", "resend_group_invite"):
        assert tools[name].open_world_hint is True and tools[name].destructive_hint is False
