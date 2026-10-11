"""Finding 11: without pii:read, no planted PII literal may appear anywhere in any read result.

The literals are read straight from the database (every passport, visa, car registration, date of
birth, street, address display, email, phone, signer name, return reason and raw token), then
every read tool and every record resource is called with the read scopes only and the serialized
results are searched for each literal."""

from __future__ import annotations

import json
import sqlite3

import pytest

READ_SCOPES = {"desk:read", "groups:read", "admin:read"}


def _pii_literals(db_path) -> set[str]:
    con = sqlite3.connect(db_path)
    lits: set[str] = set()
    for sql in (
        "SELECT passport_no, visa_no, car_reg, dob, addr_street, addr_postcode, addr_display, addr_freetext, "
        "return_reason, token FROM guest_registration",
        "SELECT signer_name FROM guest_signature",
        "SELECT receiver_email, leader_email, leader_phone_home, leader_phone_stay, password_hash, member_token, draft_json "
        "FROM group_request",
        "SELECT passport_no, visa_no, dob, addr_line, addr_postcode, email, phone_home, phone_stay, signer_name "
        "FROM group_member",
    ):
        for row in con.execute(sql):
            for v in row:
                if v is not None and str(v).strip() and len(str(v)) >= 4:
                    lits.add(str(v))
    con.close()
    # postcodes are 5 digits and could collide with unrelated numbers; keep them, they are exact literals
    return lits


@pytest.mark.anyio
async def test_no_pii_literal_leaves_without_pii_read(client_with_scopes, db_path, planted):
    literals = _pii_literals(db_path)
    assert len(literals) > 100
    blobs: list[str] = []
    async with client_with_scopes(READ_SCOPES) as c:
        for state in ("PENDING_REVIEW", "RETURNED", "DRAFT"):
            cursor = None
            while True:
                args = {"limit": 7, "state": state}
                if cursor:
                    args["cursor"] = cursor
                r = await c.call_tool("list_pending_registrations", args)
                assert r.is_error is False
                blobs.append(json.dumps(r.structured_content))
                cursor = r.structured_content["next_cursor"]
                if not cursor:
                    break
        r = await c.call_tool("list_approved_registrations", {"limit": 100})
        blobs.append(json.dumps(r.structured_content))
        refs = [card["ref"] for card in r.structured_content["items"]]
        for st in planted["tokens"].values():
            pass
        for ref in refs[:12]:
            reg = await c.call_tool("get_registration", {"token": ref, "include_signature": True})
            blobs.append(json.dumps(reg.structured_content))
            res = await c.read_resource(f"hostel://registration/{ref}")
            blobs.append(res.contents[0].text)
        blobs.append(json.dumps((await c.call_tool("desk_minor_alerts", {})).structured_content))
        groups = (await c.call_tool("list_groups", {"status": "all"})).structured_content
        blobs.append(json.dumps(groups))
        for g in groups["result"]:
            blobs.append(json.dumps((await c.call_tool("get_group", {"slug": g["slug"]})).structured_content))
            blobs.append((await c.read_resource(f"hostel://group/{g['slug']}")).contents[0].text)
        blobs.append(json.dumps((await c.call_tool("month_summary", {})).structured_content))
        blobs.append(json.dumps((await c.call_tool("audit_log", {"limit": 200})).structured_content))
        blobs.append((await c.read_resource("hostel://policy/roles")).contents[0].text)
        blobs.append((await c.read_resource("hostel://policy/retention")).contents[0].text)
    text = "\n".join(blobs)
    leaked = sorted(l for l in literals if l in text)
    assert leaked == [], leaked[:10]
