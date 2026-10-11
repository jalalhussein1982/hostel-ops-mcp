"""Finding 3: no token must never mean 'use STDIO_SCOPES' once the server is in OAuth mode,
and unauthenticated HTTP must be a deliberate, loopback-only choice."""

from __future__ import annotations

import pytest
from mcp import Client

from hostel_ops_mcp import guards, server
from tests.conftest import ALL_SCOPES


@pytest.mark.anyio
async def test_auth_required_server_ignores_stdio_scopes(app, monkeypatch):
    monkeypatch.setenv("STDIO_SCOPES", ",".join(sorted(ALL_SCOPES)))
    srv = server.create_server(auth_required=True)
    async with Client(srv, raise_exceptions=True) as c:
        r = await c.call_tool("list_pending_registrations", {})
        assert r.is_error and "bearer token" in r.content[0].text and "STDIO_SCOPES" not in r.content[0].text
        with pytest.raises(Exception) as exc:
            await c.read_resource(f"hostel://registration/{app.repo.list_pending()[0]['token']}")
        assert "bearer token" in str(exc.value)


def test_create_server_defaults_auth_required_when_a_verifier_is_given():
    class V:
        async def verify_token(self, token):  # pragma: no cover
            return None

    from mcp.server.auth.settings import AuthSettings

    srv = server.create_server(token_verifier=V(), auth=AuthSettings(
        issuer_url="http://as.test", resource_server_url="http://127.0.0.1:8000/mcp",
        required_scopes=["mcp"], validate_token_resource=True))
    assert srv._hostel_auth_required is True
    assert server.create_server()._hostel_auth_required is False


def test_require_fails_closed_on_an_unknown_tool_name(app):
    with pytest.raises(ValueError):
        guards.require(None, "tool_nobody_registered")


@pytest.mark.parametrize("argv,env,ok", [
    (["--http"], {}, True),                                                     # loopback, read scopes
    (["--http", "--host", "0.0.0.0"], {}, False),                               # non-loopback, no auth
    (["--http", "--host", "0.0.0.0", "--insecure-no-auth"], {}, True),
    (["--http"], {"STDIO_SCOPES": "desk:read,desk:write"}, False),              # write scope over no-auth HTTP
    (["--http"], {"STDIO_SCOPES": "desk:read,pii:read"}, False),
    (["--http", "--insecure-no-auth"], {"STDIO_SCOPES": "desk:read,admin:erase"}, True),
    (["--http", "--host", "0.0.0.0"], {"OAUTH_ISSUER_URL": "http://as.test"}, True),   # OAuth mode: fine anywhere
])
def test_http_without_auth_is_loopback_and_read_only_unless_insecure(argv, env, ok, monkeypatch):
    for k in ("OAUTH_ISSUER_URL", "STDIO_SCOPES"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    started = {}
    monkeypatch.setattr(server.MCPServer, "run", lambda self, **kw: started.update(kw))
    if ok:
        server.main(argv)
        assert started["transport"] == "streamable-http"
    else:
        with pytest.raises(SystemExit):
            server.main(argv)
        assert not started
