"""Spec §4 / §8.1: deterministic tool order, output schemas, honest annotations."""

from __future__ import annotations

import pytest

from tests.conftest import SPEC_TOOL_ORDER

READ_TOOLS = SPEC_TOOL_ORDER[:8]


@pytest.mark.anyio
async def test_tools_are_listed_in_spec_order(client):
    result = await client.list_tools()
    names = [t.name for t in result.tools]
    assert names == [n for n in SPEC_TOOL_ORDER if n in names], names
    assert len(names) >= 8


@pytest.mark.xfail(strict=True, reason="write/destructive tools arrive in Tasks 6-9")
@pytest.mark.anyio
async def test_every_spec_tool_is_registered(client):
    names = [t.name for t in (await client.list_tools()).tools]
    assert names == SPEC_TOOL_ORDER


@pytest.mark.anyio
async def test_every_tool_has_an_output_schema_and_a_title(client):
    for t in (await client.list_tools()).tools:
        assert t.output_schema is not None, t.name
        assert t.output_schema.get("type") == "object", t.name
        assert t.description, t.name
        assert t.annotations is not None and t.annotations.title, t.name


@pytest.mark.anyio
async def test_read_tools_are_annotated_read_only_and_closed_world(client):
    tools = {t.name: t for t in (await client.list_tools()).tools}
    for name in READ_TOOLS:
        a = tools[name].annotations
        assert a.read_only_hint is True and a.open_world_hint is False, name
    for name in set(tools) - set(READ_TOOLS):
        assert tools[name].annotations.read_only_hint is False, name


@pytest.mark.anyio
async def test_tools_list_carries_the_2026_cache_directives(client):
    result = await client.list_tools()
    assert result.ttl_ms >= 0 and result.cache_scope in ("public", "private")
