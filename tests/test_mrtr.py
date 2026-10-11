"""Task 7 / spec §4.4, §8.5: erase_registration acts only after a confirmed Multi Round-Trip Request."""

from __future__ import annotations

import pytest
from mcp.shared.exceptions import MCPError
from mcp.types import ElicitRequest, ElicitResult, InputRequiredResult

from hostel_ops_mcp.handles import ref_for

ADMIN = {"admin:erase", "admin:read", "desk:read"}


async def _never_called(*a, **k):  # declares the elicitation capability; the loop is driven by hand
    raise AssertionError("the auto-driver must not run in these tests")


def _out(result) -> dict:
    """A union return type (EraseResult | Cancelled) is wrapped by the SDK as {"result": ...}."""
    sc = result.structured_content
    return sc["result"] if set(sc) == {"result"} else sc


def _deletes(app) -> list[dict]:
    return [a for a in app.repo.audit_recent(200) if a["action"] == "delete" and a["actor"].startswith("mcp:")]


@pytest.mark.anyio
async def test_first_call_asks_for_confirmation_and_deletes_nothing(client_with_scopes, app, planted):
    token = planted["tokens"]["APPROVED"][5]
    async with client_with_scopes(ADMIN, elicitation_callback=_never_called) as c:
        res = await c.session.call_tool("erase_registration", {"token": token}, allow_input_required=True)
    assert isinstance(res, InputRequiredResult)
    assert set(res.input_requests) == {"confirm"} and res.request_state
    req = res.input_requests["confirm"]
    assert isinstance(req, ElicitRequest) and req.params.mode == "form"
    assert "326/1999" in req.params.message and "cannot be undone" in req.params.message
    assert req.params.requested_schema["properties"]["confirm"]["type"] == "boolean"
    assert app.repo.get_registration(token) is not None and _deletes(app) == []
    # the sealed state is opaque: no token, no tool name in clear
    assert token not in res.request_state and "erase" not in res.request_state


@pytest.mark.anyio
async def test_accept_with_confirm_true_deletes_exactly_once_and_audits(client_with_scopes, app, planted):
    token = planted["tokens"]["APPROVED"][6]
    async with client_with_scopes(ADMIN, elicitation_callback=_never_called) as c:
        first = await c.session.call_tool("erase_registration", {"token": token}, allow_input_required=True)
        done = await c.session.call_tool(
            "erase_registration", {"token": token},
            input_responses={"confirm": ElicitResult(action="accept", content={"confirm": True})},
            request_state=first.request_state, allow_input_required=True,
        )
    assert not isinstance(done, InputRequiredResult) and done.is_error is False
    # ADMIN has no pii:read, so the raw token is withheld even from the result of the erase itself
    assert _out(done) == {"ref": ref_for(token), "token": None, "deleted": 1, "removed_group": None}
    assert app.repo.get_registration(token) is None and app.repo.get_signature(token) is None
    rows = _deletes(app)
    assert len(rows) == 1 and ref_for(token) in rows[0]["detail"] and token not in rows[0]["detail"]


@pytest.mark.anyio
async def test_decline_and_accept_without_confirm(client_with_scopes, app, planted):
    token = planted["tokens"]["APPROVED"][7]
    async with client_with_scopes(ADMIN, elicitation_callback=_never_called) as c:
        first = await c.session.call_tool("erase_registration", {"token": token}, allow_input_required=True)
        declined = await c.session.call_tool(
            "erase_registration", {"token": token},
            input_responses={"confirm": ElicitResult(action="decline")},
            request_state=first.request_state, allow_input_required=True,
        )
        assert declined.is_error is False and _out(declined)["cancelled"] is True
        assert _out(declined)["reason"] == "declined"
        # accept but confirm=false (or missing) re-asks instead of acting
        again = await c.session.call_tool(
            "erase_registration", {"token": token},
            input_responses={"confirm": ElicitResult(action="accept", content={"confirm": False})},
            request_state=first.request_state, allow_input_required=True,
        )
        assert isinstance(again, InputRequiredResult)
    assert app.repo.get_registration(token) is not None and _deletes(app) == []


@pytest.mark.anyio
async def test_state_bound_to_arguments_rejects_another_token(client_with_scopes, app, planted):
    a, b = planted["tokens"]["APPROVED"][8], planted["tokens"]["APPROVED"][9]
    async with client_with_scopes(ADMIN, elicitation_callback=_never_called) as c:
        first = await c.session.call_tool("erase_registration", {"token": a}, allow_input_required=True)
        with pytest.raises(MCPError) as exc:
            await c.session.call_tool(
                "erase_registration", {"token": b},
                input_responses={"confirm": ElicitResult(action="accept", content={"confirm": True})},
                request_state=first.request_state, allow_input_required=True,
            )
        assert exc.value.code == -32602
        with pytest.raises(MCPError) as exc2:
            await c.session.call_tool(
                "erase_registration", {"token": a},
                input_responses={"confirm": ElicitResult(action="accept", content={"confirm": True})},
                request_state=first.request_state[:-4] + "AAAA", allow_input_required=True,
            )
        assert exc2.value.code == -32602
    assert app.repo.get_registration(a) and app.repo.get_registration(b) and _deletes(app) == []


@pytest.mark.anyio
async def test_host_without_elicitation_gets_a_tool_error(client_with_scopes, app, planted):
    token = planted["tokens"]["APPROVED"][10]
    async with client_with_scopes(ADMIN) as c:   # no elicitation_callback -> no capability declared
        r = await c.call_tool("erase_registration", {"token": token})
    assert r.is_error and "cannot confirm" in r.content[0].text and "admin panel" in r.content[0].text
    assert app.repo.get_registration(token) is not None


@pytest.mark.anyio
async def test_auto_driven_client_confirms_through_its_callback(client_with_scopes, app, planted):
    """The SDK Client's own loop: the callback plays the human and says yes."""
    token = planted["tokens"]["APPROVED"][11]
    seen: list[str] = []

    async def say_yes(context, params):
        seen.append(params.message)
        return ElicitResult(action="accept", content={"confirm": True})

    async with client_with_scopes(ADMIN, elicitation_callback=say_yes) as c:
        r = await c.call_tool("erase_registration", {"token": token})
    assert r.is_error is False and _out(r)["deleted"] == 1
    assert len(seen) == 1 and "Erase registration" in seen[0]


@pytest.mark.anyio
async def test_erasing_the_last_group_member_removes_the_group(client_with_scopes, app, planted):
    slug = planted["groups"]["submitted_visible"]
    members = app.groups.members(slug)

    async def say_yes(context, params):
        return ElicitResult(action="accept", content={"confirm": True})

    async with client_with_scopes(ADMIN, elicitation_callback=say_yes) as c:
        results = [_out(await c.call_tool("erase_registration", {"token": m["token"]})) for m in members]
    assert [r["removed_group"] for r in results] == [None] * (len(members) - 1) + [slug]
    assert app.groups.get(slug) is None


@pytest.mark.anyio
async def test_unknown_token_is_a_tool_error_before_any_question(client_with_scopes):
    async with client_with_scopes(ADMIN, elicitation_callback=_never_called) as c:
        res = await c.session.call_tool("erase_registration", {"token": "ghost"}, allow_input_required=True)
    assert not isinstance(res, InputRequiredResult) and res.is_error
    assert "list_approved_registrations" in res.content[0].text


@pytest.mark.anyio
async def test_destructive_annotations(client):
    tools = {t.name: t.annotations for t in (await client.list_tools()).tools}
    a = tools["erase_registration"]
    assert a.read_only_hint is False and a.destructive_hint is True and a.idempotent_hint is False
