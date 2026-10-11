"""Task 4: read tools over the synthetic database."""

from __future__ import annotations

import pytest


@pytest.mark.anyio
async def test_pending_queue_has_fifteen_rows_oldest_first(client):
    r = await client.call_tool("list_pending_registrations", {"limit": 100})
    page = r.structured_content
    assert len(page["items"]) == 15 and page["next_cursor"] is None
    created = [c["created_at"] for c in page["items"]]
    assert created == sorted(created)
    assert all(c["state"] == "PENDING_REVIEW" for c in page["items"])


@pytest.mark.anyio
async def test_cursor_pagination_walks_the_queue_exactly_once(client):
    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        args = {"limit": 4}
        if cursor:
            args["cursor"] = cursor
        page = (await client.call_tool("list_pending_registrations", args)).structured_content
        seen += [c["token"] for c in page["items"]]
        pages += 1
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert pages == 4 and len(seen) == 15 and len(set(seen)) == 15


@pytest.mark.anyio
async def test_bad_cursor_is_a_tool_error(client):
    r = await client.call_tool("list_pending_registrations", {"cursor": "not-a-cursor"})
    assert r.is_error and "cursor" in r.content[0].text


@pytest.mark.anyio
async def test_minor_alerts_flag_the_two_pending_minors(client, planted):
    alerts = (await client.call_tool("desk_minor_alerts", {})).structured_content["result"]
    by_token = {a["token"]: a for a in alerts}
    assert set(by_token) == {planted["minor_tokens"]["pending_under18"], planted["minor_tokens"]["pending_under15_self"]}
    teen = by_token[planted["minor_tokens"]["pending_under18"]]
    child = by_token[planted["minor_tokens"]["pending_under15_self"]]
    assert teen["age_band"] == "15-17" and teen["needs_legal_representative"] is False
    assert child["age_band"] == "<15" and child["needs_legal_representative"] is True
    assert "dob" not in teen or teen["dob"] is None


@pytest.mark.anyio
async def test_get_registration_on_a_draft_returns_the_card_not_an_error(reader, planted):
    token = planted["draft_with_partial_fields"]
    r = await reader.call_tool("get_registration", {"token": token})
    assert r.is_error is False
    reg = r.structured_content
    assert reg["state"] == "DRAFT" and reg.get("token") is None and reg["ref"]
    # the half-filled wizard pages leak nothing beyond the policy
    assert reg["passport_no"].startswith("•••") and reg.get("dob") is None and reg["age_band"]
    assert reg["signature"] is None


@pytest.mark.anyio
async def test_get_registration_with_pii_and_signature(client, planted):
    token = planted["minor_tokens"]["approved_under15_legal_rep"]
    reg = (await client.call_tool("get_registration", {"token": token, "include_signature": True})).structured_content
    assert reg["dob"] == "2014-02-11" and reg["passport_no"].startswith("X")
    assert reg["signature"]["signer_role"] == "legal_representative"
    assert reg["signature"]["signer_name"] and reg["signature"]["signature_png_base64"]
    without = (await client.call_tool("get_registration", {"token": token})).structured_content
    assert without["signature"]["signature_png_base64"] is None


@pytest.mark.anyio
async def test_unknown_token_names_the_list_tool(client):
    r = await client.call_tool("get_registration", {"token": "nope"})
    assert r.is_error and "list_pending_registrations" in r.content[0].text


@pytest.mark.anyio
async def test_approved_list_hides_future_group_rows(client, planted):
    from datetime import date

    page = (await client.call_tool("list_approved_registrations", {"limit": 100})).structured_content
    tokens = {c["token"] for c in page["items"]}
    assert all(c["state"] == "APPROVED" for c in page["items"])
    assert set(planted["tokens"]["APPROVED"]) <= tokens
    choir = planted["groups"]["submitted_with_duplicates"]
    for c in page["items"]:
        if c["group_id"] == choir:
            assert c["arrival"] <= date.today().isoformat()
    window = (await client.call_tool(
        "list_approved_registrations", {"arrival_from": "2026-08-01", "arrival_to": "2026-08-31", "limit": 100}
    )).structured_content
    assert len(window["items"]) == 15
    by_group = (await client.call_tool(
        "list_approved_registrations", {"group_id": planted["groups"]["submitted_visible"], "limit": 100}
    )).structured_content
    assert len(by_group["items"]) == 5 and sum(c["is_group_leader"] for c in by_group["items"]) == 1


@pytest.mark.anyio
async def test_groups_and_duplicates(client, planted):
    pending = (await client.call_tool("list_groups", {"status": "pending"})).structured_content["result"]
    assert [g["slug"] for g in pending] == [planted["groups"]["pending"]]
    everything = (await client.call_tool("list_groups", {"status": "all"})).structured_content["result"]
    assert len(everything) == 3
    choir = (await client.call_tool("get_group", {"slug": planted["groups"]["submitted_with_duplicates"]})).structured_content
    assert choir["group"]["status"] == "SUBMITTED" and choir["total"] == 6 == len(choir["members"])
    assert len(choir["duplicates"]) == 1 and len(choir["duplicates"][0]) == 2
    panel = (await client.call_tool("get_group", {"slug": planted["groups"]["pending"]})).structured_content
    assert panel["group"]["status"] == "PENDING" and panel["total"] == 4 and panel["members"] == []
    assert len(panel["panel_rows"]) == 4 and len(panel["duplicates"]) == 1
    assert panel["filled"] >= 0 and isinstance(panel["attention_ids"], list)


@pytest.mark.anyio
async def test_group_leader_contact_needs_pii(client_with_scopes, planted):
    slug = planted["groups"]["submitted_visible"]
    async with client_with_scopes({"groups:read"}) as reader:
        masked = (await reader.call_tool("get_group", {"slug": slug})).structured_content["group"]
        assert masked["leader_email"] is None and masked["receiver_email"] is None
    async with client_with_scopes({"groups:read", "pii:read"}) as full_client:
        full = (await full_client.call_tool("get_group", {"slug": slug})).structured_content["group"]
        assert full["leader_email"].endswith("@example.invalid")
        r = await full_client.call_tool("get_group", {"slug": "missing"})
        assert r.is_error and "list_groups" in r.content[0].text


@pytest.mark.anyio
async def test_month_summary_and_audit(client):
    months = (await client.call_tool("month_summary", {})).structured_content["result"]
    by_period = {m["period"]: m for m in months}
    assert by_period["2026-08"]["live_count"] == 15 and by_period["2026-08"]["verified"] is True
    assert by_period["2026-09"]["live_count"] == 15 and by_period["2026-09"]["backed_up_at"] is None
    assert "2026-10" in by_period and "2026-11" in by_period
    audit = (await client.call_tool("audit_log", {"limit": 3})).structured_content["result"]
    assert len(audit) == 3 and audit[0]["id"] > audit[1]["id"]
