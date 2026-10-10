"""Task 11 / spec §5: policy and record resources, cache directives, prompts."""

from __future__ import annotations

import json

import pytest

from hostel_ops_mcp import guards
from tests.conftest import ALL_SCOPES


@pytest.mark.anyio
async def test_resources_and_templates_are_listed(client):
    static = {r.uri.__str__().rstrip("/") for r in (await client.list_resources()).resources}
    templates = {t.uri_template for t in (await client.list_resource_templates()).resource_templates}
    assert static == {"hostel://policy/roles", "hostel://policy/retention"}
    assert templates == {"hostel://registration/{token}", "hostel://group/{slug}",
                         "export://xlsx/{export_id}", "export://pdf/{export_id}"}
    assert set(guards.RESOURCE_SCOPES) == {"hostel://policy/roles", "hostel://policy/retention",
                                           "hostel://registration/{token}", "hostel://group/{slug}", "export://{export_id}"}


@pytest.mark.anyio
async def test_roles_policy_names_every_scope_and_tool_and_is_public_for_an_hour(client):
    res = await client.read_resource("hostel://policy/roles")
    text = res.contents[0].text
    for scope in ALL_SCOPES:
        assert f"`{scope}`" in text, scope
    for tool in guards.TOOL_SCOPES:
        assert tool in text, tool
    assert "passport_no" in text and "age_band" in text   # the field policy rides along
    assert res.ttl_ms == 3_600_000 and res.cache_scope == "public"
    assert res.contents[0].mime_type == "text/markdown"


@pytest.mark.anyio
async def test_retention_policy_quotes_the_apps_privacy_notice(client):
    res = await client.read_resource("hostel://policy/retention")
    text = res.contents[0].text
    assert "326/1999" in text and "purge" in text.lower() and "legal representative" not in text[:0]
    assert "<p>" not in text and "<li>" not in text          # HTML from the i18n catalogue is stripped
    assert "verified" in text and "backup" in text.lower()
    assert res.ttl_ms == 3_600_000 and res.cache_scope == "public"


@pytest.mark.anyio
async def test_registration_resource_matches_the_tool_and_is_private(client, planted):
    token = planted["tokens"]["APPROVED"][2]
    via_tool = (await client.call_tool("get_registration", {"token": token})).structured_content
    res = await client.read_resource(f"hostel://registration/{token}")
    assert json.loads(res.contents[0].text) == via_tool
    assert res.ttl_ms == 0 and res.cache_scope == "private"
    assert res.contents[0].mime_type == "application/json"
    with pytest.raises(Exception) as exc:
        await client.read_resource("hostel://registration/ghost")
    assert "ghost" in str(exc.value)


@pytest.mark.anyio
async def test_group_resource_and_scope_checks(client_with_scopes, planted):
    slug = planted["groups"]["submitted_visible"]
    async with client_with_scopes({"groups:read"}) as c:
        res = await c.read_resource(f"hostel://group/{slug}")
        doc = json.loads(res.contents[0].text)
        assert doc["group"]["slug"] == slug and doc["group"]["leader_email"] is None
        assert res.cache_scope == "private"
        with pytest.raises(Exception) as exc:
            await c.read_resource(f"hostel://registration/{planted['tokens']['APPROVED'][0]}")
        assert "desk:read" in str(exc.value)
    async with client_with_scopes(set()) as c:
        text = (await c.read_resource("hostel://policy/roles")).contents[0].text   # policy needs no scope
        assert "desk:read" in text
        with pytest.raises(Exception) as exc:
            await c.read_resource(f"hostel://group/{slug}")
        assert "groups:read" in str(exc.value)


@pytest.mark.anyio
async def test_prompts(client):
    names = {p.name for p in (await client.list_prompts()).prompts}
    assert names == {"morning_desk_briefing", "authority_export_checklist"}
    p = await client.get_prompt("morning_desk_briefing", {})
    text = p.messages[0].content.text
    for tool in ("list_pending_registrations", "desk_minor_alerts", "list_groups"):
        assert tool in text
    assert "RETURNED" in text and "24" in text
    q = await client.get_prompt("authority_export_checklist", {"period": "2026-09"})
    text = q.messages[0].content.text
    assert "2026-09" in text and "export_authority_report" in text and "month_summary" in text
