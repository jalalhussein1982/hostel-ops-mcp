# Authorization

hostel-ops-mcp is an **OAuth 2.1 resource server**. It verifies bearer tokens that an
authorization server it does not own has issued, and advertises that server through RFC 9728
protected-resource metadata. It never mints tokens (the only exception is the test suite and
`scripts/dev_token.py`, which sign with a throwaway local key).

## Resource server vs authorization server

| | Who | In this repo |
|---|---|---|
| Authorization server (AS) | Authenticates users, issues tokens with `scope`, `aud`, `sub` | Keycloak from `docker-compose.yml`, realm `keycloak/realm-hostel.json` |
| Resource server (RS) | Verifies tokens, enforces scopes per tool | `hostel_ops_mcp/auth.py` + `guards.py` |

SDK wiring (`server.server_from_env`):

```python
MCPServer("hostel-ops",
          token_verifier=JwtVerifier(jwks_url, issuer, audience=RESOURCE_SERVER_URL),
          auth=AuthSettings(issuer_url=OAUTH_ISSUER_URL, resource_server_url=RESOURCE_SERVER_URL,
                            required_scopes=["mcp"], validate_token_resource=True))
```

What the SDK does with that: the Streamable HTTP app gets a bearer middleware. A request with
no token or a token the verifier rejects is answered `401` with
`WWW-Authenticate: Bearer error="invalid_token", resource_metadata="<RS>/.well-known/oauth-protected-resource/mcp"`.
A valid token without the `mcp` scope is answered `403 insufficient_scope`. The metadata document
at `/.well-known/oauth-protected-resource/mcp` lists the issuer and `scopes_supported: ["mcp"]`.
The verified `AccessToken` is placed in a context variable; `guards.require` reads it per tool.

`JwtVerifier.verify_token` (PyJWT): fetch the JWKS (cached ten minutes, re-fetched once on an
unknown `kid`), verify signature, `exp`, `iss`, `aud == RESOURCE_SERVER_URL`, require `sub`;
read `scope` as a space-separated string or an array (some IdPs emit arrays). Returns
`AccessToken(client_id=azp|client_id|sub, scopes, expires_at, resource, subject, claims={iss})`,
or `None` on any failure. The audit actor is `mcp:<sub>`.

Auth applies to HTTP only. Over stdio (and in the in-memory tests) `get_access_token()` is `None`
and the server runs with `STDIO_SCOPES` (default `desk:read,groups:read,admin:read`).

## Scopes

See `hostel://policy/roles` (served by the server) or `guards.py`. Nine scopes plus `mcp`:
`desk:read desk:write groups:read groups:write export pii:read admin:read admin:write admin:erase`.
Roles: receptionist = desk + export + pii; group_manager = receptionist + groups; admin = everything.
`pii:read` is separate on purpose: an agent that only triages the queue can be given a token
without it, and then the server cannot leak what it never returned.

## The Keycloak realm

`keycloak/realm-hostel.json` is generated from `guards.py` by `scripts/make_realm.py` (re-run it
if the scope table changes). It defines:

- realm `hostel`, three realm roles `admin`, `group_manager`, `receptionist`;
- one client scope per MCP scope, plus `mcp` (default scope) carrying an **audience mapper** that
  adds `http://127.0.0.1:8000/mcp` to `aud`;
- **role scope-mappings**: each client scope is mapped to the roles that hold it, and the client has
  *Full scope allowed* off. Keycloak then grants a requested scope only to users holding one of
  those roles; a receptionist asking for `admin:erase` simply does not get it in the token;
- public client `hostel-ops-inspector` (PKCE S256, authorization code, redirect URIs for the MCP
  Inspector on port 6274). Dynamic client registration is **off**; the Inspector uses this
  pre-registered client id. (Client ID Metadata Documents are not supported by Keycloak; the spec's
  CIMD mention is satisfied by pre-registration here.)
- users `admin`, `group_manager`, `receptionist`, password `changeme`.

```sh
docker compose up -d                                  # Keycloak on 127.0.0.1:8080
OAUTH_ISSUER_URL=http://127.0.0.1:8080/realms/hostel \
RESOURCE_SERVER_URL=http://127.0.0.1:8000/mcp \
uv run python -m hostel_ops_mcp.server --http         # RS on 127.0.0.1:8000/mcp
```

`OAUTH_JWKS_URL` defaults to `<issuer>/protocol/openid-connect/certs` (Keycloak's path); set it
for another provider.

## Inspector flow

1. `npx @modelcontextprotocol/inspector`, transport Streamable HTTP, URL `http://127.0.0.1:8000/mcp`.
2. Authentication → OAuth. The Inspector reads `/.well-known/oauth-protected-resource/mcp`, finds
   the issuer, reads its OIDC metadata, and starts the authorization-code flow with PKCE.
   Client id `hostel-ops-inspector`; request scopes `mcp desk:read desk:write pii:read ...`
   (Keycloak drops the ones the user's role does not hold).
3. Log in as `receptionist` / `changeme`. `tools/list` works; `month_summary` fails with
   `missing scope admin:read`; `get_registration` shows unmasked passports because the role holds
   `pii:read`. Log in as `admin` to see `erase_registration` ask for confirmation.
4. To demonstrate PII minimisation, remove `pii:read` from the requested scopes (or from the
   receptionist role in the realm) and watch document numbers become `•••NN` and `dob` turn into
   `age_band`.

## Without Keycloak: a development token

```sh
uv run python scripts/dev_token.py --sub alice --scopes "mcp desk:read desk:write"   # prints a JWT
python -m http.server 9001 --directory fixtures &                                      # serves dev-jwks.json
OAUTH_ISSUER_URL=http://127.0.0.1:9001 OAUTH_JWKS_URL=http://127.0.0.1:9001/dev-jwks.json \
RESOURCE_SERVER_URL=http://127.0.0.1:8000/mcp uv run python -m hostel_ops_mcp.server --http
curl -H "Authorization: Bearer $TOKEN" -H 'Accept: application/json, text/event-stream' ... http://127.0.0.1:8000/mcp
```

The private key is written to `.dev/dev-private.pem` (git-ignored); only the JWKS is public.

## requestState keys

Destructive tools carry sealed `requestState` between rounds. The SDK seals it under a
per-process key by default, which is correct for one replica. For more than one replica (or to
survive restarts mid-confirmation) set `REQUEST_STATE_KEYS=<secret1>,<secret2>` (each ≥ 32
bytes; `keys[0]` seals, all verify) and optionally `REQUEST_STATE_TTL` (seconds, default 300).
The seal binds the state to the tool name, the argument digest and the token's
`(client_id, iss, sub)`, so a retry with another token's argument, another user, or an expired
round fails with `-32602 Invalid or expired requestState` before the tool runs.

## What the tests cover

`tests/test_auth.py` (verifier: good token, expired, wrong audience, wrong issuer, unknown kid,
wrong key, array-form scope, JWKS caching) and `tests/test_http.py` (the real ASGI app in
process: 401 + `resource_metadata`, 403 for a token without `mcp`, metadata document, `tools/list`
with a valid token, scopes and audit actor from the token). No test needs Keycloak or the network.
