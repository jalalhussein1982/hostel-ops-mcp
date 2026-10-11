"""Registration tokens are bearer credentials in the vendor app (GET /w/{token}/{step} needs no
login and rehydrates the form). Callers without pii:read get an opaque `ref` instead, and every
tool accepts either."""

from __future__ import annotations

import json
import re

import pytest

from hostel_ops_mcp import handles

REF = re.compile(r"^[0-9a-f]{16}$")


def test_ref_is_a_keyed_one_way_handle(monkeypatch):
    a = handles.ref_for("tokenA")
    assert REF.match(a) and a == handles.ref_for("tokenA") and a != handles.ref_for("tokenB")
    monkeypatch.setenv("HANDLE_KEY", "another-key-for-this-deployment")
    assert handles.ref_for("tokenA") != a


def test_resolve_accepts_token_or_ref_and_fails_closed(app, planted):
    tok = planted["tokens"]["APPROVED"][0]
    assert handles.resolve(tok) == tok
    assert handles.resolve(handles.ref_for(tok)) == tok
    with pytest.raises(handles.Unknown):
        handles.resolve("0123456789abcdef")
    with pytest.raises(handles.Unknown):
        handles.resolve("ghost")


@pytest.mark.anyio
async def test_reader_without_pii_sees_refs_and_never_tokens(client_with_scopes, planted):
    tokens = set(sum(planted["tokens"].values(), []))
    async with client_with_scopes({"desk:read", "groups:read", "admin:read"}) as c:
        page = (await c.call_tool("list_pending_registrations", {"limit": 100})).structured_content
        for card in page["items"]:
            assert REF.match(card["ref"]) and card.get("token") is None
        alerts = (await c.call_tool("desk_minor_alerts", {})).structured_content["result"]
        assert all(a.get("token") is None and REF.match(a["ref"]) for a in alerts)
        g = (await c.call_tool("get_group", {"slug": planted["groups"]["submitted_with_duplicates"]})).structured_content
        assert all(m.get("token") is None for m in g["members"])
        for pair in g["duplicates"]:
            assert all(REF.match(r) for r in pair)
        ref = page["items"][0]["ref"]
        reg = (await c.call_tool("get_registration", {"token": ref})).structured_content
        assert reg["ref"] == ref and reg.get("token") is None
        doc = json.loads((await c.read_resource(f"hostel://registration/{ref}")).contents[0].text)
        assert doc["ref"] == ref and doc.get("token") is None
        blob = json.dumps(page) + json.dumps(alerts) + json.dumps(g) + json.dumps(reg)
        assert not any(t in blob for t in tokens)


@pytest.mark.anyio
async def test_ref_round_trips_through_writes_and_audit_stores_the_ref(client_with_scopes, app, planted):
    tok = planted["tokens"]["PENDING_REVIEW"][8]
    ref = handles.ref_for(tok)
    async with client_with_scopes({"desk:write"}) as c:
        r = (await c.call_tool("approve_registration", {"token": ref})).structured_content
        assert r["ref"] == ref and r.get("token") is None and r["serial"]
    async with client_with_scopes({"desk:read", "pii:read"}) as c:
        reg = (await c.call_tool("get_registration", {"token": ref})).structured_content
        assert reg["token"] == tok and reg["ref"] == ref and reg["state"] == "APPROVED"
    rows = [a for a in app.repo.audit_recent(20) if a["action"] == "approve" and a["actor"] == "mcp:stdio"]
    assert len(rows) == 1 and ref in rows[0]["detail"] and tok not in rows[0]["detail"]


@pytest.mark.anyio
async def test_audit_log_without_pii_carries_no_tokens(client_with_scopes, client, app, planted):
    tokens = set(sum(planted["tokens"].values(), []))
    for t in planted["tokens"]["PENDING_REVIEW"][9:11]:
        await client.call_tool("return_registration", {"token": t, "reason": "check", "step": 1})
    blob = json.dumps([a for a in app.repo.audit_recent(50)])
    assert not any(t in blob for t in tokens)
