"""Spec §6.2 / §8.3: every (tool, scope-set) pair. Allowed sets never fail on scope;
each missing scope produces a ToolError naming it. Write does not imply read."""

from __future__ import annotations

import pytest

from hostel_ops_mcp import guards
from tests.conftest import ALL_SCOPES, SPEC_TOOL_ORDER


def _args(name: str, planted: dict) -> dict:
    """Minimal valid arguments so an allowed call gets past input validation."""
    pending = planted["tokens"]["PENDING_REVIEW"]
    approved = planted["tokens"]["APPROVED"]
    return {
        "get_registration": {"token": pending[0]},
        "list_groups": {"status": "all"},
        "get_group": {"slug": planted["groups"]["submitted_visible"]},
        "approve_registration": {"token": pending[1]},
        "return_registration": {"token": pending[2], "reason": "Passport number unreadable", "step": 2},
        "create_group_link": {"group_name": "Scope Test Group", "leader_first_name": "Ana", "leader_last_name": "Test",
                              "arrival": "2026-12-01", "departure": "2026-12-03", "purpose_of_stay": "leisure",
                              "receiver_email": "lead@example.invalid"},
        "resend_group_invite": {"slug": planted["groups"]["pending"]},
        "export_authority_report": {"tokens": approved[:2], "format": "xlsx"},
        "edit_approved_registration": {"token": approved[0], "fields": {"car_reg": "TEST123"}},
        "erase_registration": {"token": approved[3]},
        "purge_month": {"period": "2026-08"},
    }.get(name, {})


def _cases():
    for tool in SPEC_TOOL_ORDER:
        required = set(guards.TOOL_SCOPES[tool])
        yield pytest.param(tool, required, None, id=f"{tool}-allowed")
        for s in sorted(required):
            yield pytest.param(tool, required - {s}, s, id=f"{tool}-missing-{s}")


@pytest.mark.anyio
@pytest.mark.parametrize("tool,scopes,missing", list(_cases()))
async def test_scope_matrix(tool, scopes, missing, client_with_scopes, planted):
    async with client_with_scopes(scopes) as c:
        registered = {t.name for t in (await c.list_tools()).tools}
        if tool not in registered:
            pytest.skip(f"{tool} not registered yet")
        r = await c.call_tool(tool, _args(tool, planted))
    if missing is None:
        text = r.content[0].text if r.content else ""
        assert "missing scope" not in text, text
    else:
        assert r.is_error, f"{tool} ran without {missing}"
        text = r.content[0].text
        assert missing in text and tool in text, text


@pytest.mark.anyio
async def test_write_scope_does_not_imply_read(client_with_scopes, planted):
    async with client_with_scopes({"desk:write", "groups:write", "admin:write", "admin:erase", "export"}) as c:
        for tool in ("list_pending_registrations", "get_registration", "desk_minor_alerts",
                     "list_groups", "month_summary", "audit_log"):
            r = await c.call_tool(tool, _args(tool, planted))
            assert r.is_error and "missing scope" in r.content[0].text, tool


def test_scope_table_covers_every_spec_tool_and_only_known_scopes():
    assert list(guards.TOOL_SCOPES) == SPEC_TOOL_ORDER
    assert {s for ss in guards.TOOL_SCOPES.values() for s in ss} <= ALL_SCOPES == set(guards.SCOPES)
    for role, ss in guards.ROLE_SCOPES.items():
        assert set(ss) <= ALL_SCOPES, role
    assert set(guards.ROLE_SCOPES["admin"]) == ALL_SCOPES


def test_tools_that_hand_out_a_portal_password_need_pii_read():
    """The portal password unlocks the vendor's leader portal, which serves member PII."""
    for tool in ("create_group_link", "resend_group_invite"):
        assert set(guards.TOOL_SCOPES[tool]) == {"groups:write", "pii:read"}, tool
