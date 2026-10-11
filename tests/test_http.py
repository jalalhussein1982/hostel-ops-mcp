"""Task 10 / spec §8.8: the real Streamable HTTP app in-process, as an OAuth 2.1 resource server."""

from __future__ import annotations

import json

import httpx
import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.auth.settings import AuthSettings

from hostel_ops_mcp.auth import JwtVerifier
from hostel_ops_mcp.server import create_server
from tests.test_auth import AUD, ISS, JWKS_URL, Keys

BASE = "http://127.0.0.1:8000"


@pytest.fixture
async def rs(app, monkeypatch):
    """An authenticated server instance plus its ASGI app with the lifespan running."""
    # Every stdio scope is set on purpose: in OAuth mode none of them may ever be used.
    monkeypatch.setenv("STDIO_SCOPES", "desk:read,desk:write,groups:read,groups:write,export,pii:read,admin:read,admin:write,admin:erase")
    keys = Keys()
    verifier = JwtVerifier(jwks_url=JWKS_URL, issuer=ISS, audience=AUD, http=keys.http())
    server = create_server(
        token_verifier=verifier,
        auth=AuthSettings(issuer_url=ISS, resource_server_url=AUD, required_scopes=["mcp"], validate_token_resource=True),
    )
    asgi = server.streamable_http_app(json_response=True, stateless_http=True, host="127.0.0.1")
    async with asgi.router.lifespan_context(asgi):
        yield keys, asgi


def _raw(asgi) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=asgi), base_url=BASE)


def _sdk(asgi, token: str):
    http = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=asgi), base_url=BASE,
                              headers={"Authorization": f"Bearer {token}"})
    return Client(streamable_http_client(f"{BASE}/mcp", http_client=http), raise_exceptions=True)


INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


@pytest.mark.anyio
async def test_no_token_is_401_with_resource_metadata(rs):
    _, asgi = rs
    async with _raw(asgi) as c:
        r = await c.post("/mcp", json=INIT, headers=HEADERS)
    assert r.status_code == 401
    www = r.headers["www-authenticate"]
    assert www.startswith("Bearer ") and 'error="invalid_token"' in www
    assert f'resource_metadata="{BASE}/.well-known/oauth-protected-resource/mcp"' in www


@pytest.mark.anyio
async def test_token_without_mcp_scope_is_refused(rs):
    keys, asgi = rs
    async with _raw(asgi) as c:
        r = await c.post("/mcp", json=INIT, headers={**HEADERS, "Authorization": f"Bearer {keys.token(scope='desk:read')}"})
    # The SDK answers a missing required scope with 403 insufficient_scope (RFC 6750 §3.1), not 401.
    assert r.status_code == 403 and 'error="insufficient_scope"' in r.headers["www-authenticate"]
    assert "mcp" in r.headers["www-authenticate"]


@pytest.mark.anyio
async def test_bad_token_is_401(rs):
    keys, asgi = rs
    async with _raw(asgi) as c:
        r = await c.post("/mcp", json=INIT, headers={**HEADERS, "Authorization": f"Bearer {keys.token(aud='x')}"})
    assert r.status_code == 401


@pytest.mark.anyio
async def test_protected_resource_metadata(rs):
    _, asgi = rs
    async with _raw(asgi) as c:
        r = await c.get("/.well-known/oauth-protected-resource/mcp")
    assert r.status_code == 200
    meta = r.json()
    assert meta["resource"].rstrip("/") == AUD and meta["authorization_servers"] == [ISS]
    assert "mcp" in meta["scopes_supported"] and meta["bearer_methods_supported"] == ["header"]


@pytest.mark.anyio
async def test_valid_token_lists_tools_and_scopes_come_from_the_token(rs, planted):
    keys, asgi = rs
    tok = keys.token(scope="mcp desk:read desk:write", sub="alice")
    async with _sdk(asgi, tok) as c:
        names = [t.name for t in (await c.list_tools()).tools]
        assert len(names) == 16 and names[0] == "list_pending_registrations"
        page = (await c.call_tool("list_pending_registrations", {"limit": 2})).structured_content
        assert len(page["items"]) == 2
        assert page["items"][0]["passport_no"].startswith("•••")       # no pii:read in the token
        denied = await c.call_tool("month_summary", {})
        assert denied.is_error and "admin:read" in denied.content[0].text
        ok = await c.call_tool("approve_registration", {"token": planted["tokens"]["PENDING_REVIEW"][6]})
        assert ok.is_error is False


@pytest.mark.anyio
async def test_audit_actor_is_the_token_subject(rs, app, planted):
    keys, asgi = rs
    async with _sdk(asgi, keys.token(scope="mcp desk:write", sub="bob")) as c:
        await c.call_tool("approve_registration", {"token": planted["tokens"]["PENDING_REVIEW"][7]})
    rows = [a for a in app.repo.audit_recent(10) if a["action"] == "approve"]
    assert rows and rows[0]["actor"] == "mcp:bob"


@pytest.mark.anyio
async def test_pii_scope_in_the_token_unmasks(rs, planted):
    keys, asgi = rs
    tok = keys.token(scope=["mcp", "desk:read", "pii:read"])
    async with _sdk(asgi, tok) as c:
        reg = (await c.call_tool("get_registration", {"token": planted["tokens"]["APPROVED"][0]})).structured_content
    assert reg["passport_no"].startswith("X") and reg["dob"]
