"""Task 10 / spec §8.7: JwtVerifier against a local JWKS. No network, no Keycloak."""

from __future__ import annotations

import json
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from hostel_ops_mcp.auth import JwtVerifier, parse_scope

ISS = "http://as.test/realms/hostel"
AUD = "http://127.0.0.1:8000/mcp"
JWKS_URL = "http://as.test/realms/hostel/protocol/openid-connect/certs"


class Keys:
    def __init__(self) -> None:
        self.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = "dev-key-1"
        self.requests = 0

    def jwks(self) -> dict:
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.private.public_key()))
        return {"keys": [{**jwk, "kid": self.kid, "alg": "RS256", "use": "sig"}]}

    def token(self, **over) -> str:
        now = int(time.time())
        claims = {"iss": ISS, "aud": AUD, "sub": "alice", "azp": "hostel-ops-inspector",
                  "scope": "mcp desk:read desk:write", "iat": now, "exp": now + 300}
        claims.update(over)
        claims = {k: v for k, v in claims.items() if v is not None}
        kid = over.pop("_kid", self.kid)
        return jwt.encode(claims, self.private, algorithm="RS256", headers={"kid": kid})

    def http(self) -> httpx.AsyncClient:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests += 1
            assert str(request.url) == JWKS_URL
            return httpx.Response(200, json=self.jwks())

        return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture
def keys():
    return Keys()


@pytest.fixture
def verifier(keys):
    return JwtVerifier(jwks_url=JWKS_URL, issuer=ISS, audience=AUD, http=keys.http())


@pytest.mark.anyio
async def test_valid_token_yields_scopes_subject_and_client(verifier, keys):
    at = await verifier.verify_token(keys.token())
    assert at is not None
    assert at.scopes == ["mcp", "desk:read", "desk:write"] and at.subject == "alice"
    assert at.client_id == "hostel-ops-inspector" and at.resource == AUD and at.claims["iss"] == ISS
    assert at.expires_at and at.expires_at > time.time()


@pytest.mark.anyio
@pytest.mark.parametrize("over", [
    {"exp": int(time.time()) - 10},
    {"aud": "http://other.example/mcp"},
    {"iss": "http://evil.test/realms/hostel"},
    {"sub": None},
])
async def test_bad_claims_return_none(verifier, keys, over):
    assert await verifier.verify_token(keys.token(**over)) is None


@pytest.mark.anyio
async def test_unknown_kid_and_garbage_return_none(verifier, keys):
    assert await verifier.verify_token(keys.token(_kid="rotated-away")) is None
    assert await verifier.verify_token("not.a.jwt") is None
    assert await verifier.verify_token("") is None


@pytest.mark.anyio
async def test_wrong_signing_key_returns_none(verifier, keys):
    other = Keys()
    other.kid = keys.kid   # same kid, different key material
    assert await verifier.verify_token(other.token()) is None


@pytest.mark.anyio
async def test_scope_claim_as_list_is_accepted(verifier, keys):
    """Review Focus 5: some IdPs emit `scope` as an array."""
    at = await verifier.verify_token(keys.token(scope=["mcp", "admin:read"]))
    assert at is not None and at.scopes == ["mcp", "admin:read"]
    at = await verifier.verify_token(keys.token(scope=None, scp="mcp groups:read"))
    assert at is not None and at.scopes == ["mcp", "groups:read"]


def test_parse_scope_forms():
    assert parse_scope("a b  c") == ["a", "b", "c"]
    assert parse_scope(["a", "b"]) == ["a", "b"]
    assert parse_scope(None) == [] and parse_scope("") == []


@pytest.mark.anyio
async def test_jwks_is_cached_and_refetched_once_on_unknown_kid(verifier, keys, monkeypatch):
    now = [100.0]
    monkeypatch.setattr("hostel_ops_mcp.auth.time.monotonic", lambda: now[0])
    await verifier.verify_token(keys.token())
    await verifier.verify_token(keys.token())
    assert keys.requests == 1
    now[0] += 31                                # past the unknown-kid throttle window
    assert await verifier.verify_token(keys.token(_kid="new")) is None
    assert keys.requests == 2


# ---- finding 7: JWKS fetching is bounded and never raises into the request -----------------

@pytest.mark.anyio
async def test_cold_cache_fetches_once_even_for_an_unknown_kid(keys):
    v = JwtVerifier(jwks_url=JWKS_URL, issuer=ISS, audience=AUD, http=keys.http())
    assert await v.verify_token(keys.token(_kid="unknown")) is None
    assert keys.requests == 1          # the fetch that just happened is fresh; no second fetch


@pytest.mark.anyio
async def test_unknown_kid_refetch_is_throttled(keys, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("hostel_ops_mcp.auth.time.monotonic", lambda: now[0])
    v = JwtVerifier(jwks_url=JWKS_URL, issuer=ISS, audience=AUD, http=keys.http())
    await v.verify_token(keys.token())                          # warm: 1 fetch
    now[0] += 60
    for _ in range(5):
        assert await v.verify_token(keys.token(_kid="rotated")) is None
    assert keys.requests == 2                                   # one refetch for the burst, not five
    now[0] += 31
    assert await v.verify_token(keys.token(_kid="rotated")) is None
    assert keys.requests == 3                                   # allowed again after the 30 s window


@pytest.mark.anyio
async def test_non_object_jwks_and_oversized_body_yield_none(keys):
    bodies = iter([httpx.Response(200, json=["not", "an", "object"]),
                   httpx.Response(200, content=b"{" + b" " * (70 * 1024) + b"}")])
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: next(bodies)))
    v = JwtVerifier(jwks_url=JWKS_URL, issuer=ISS, audience=AUD, http=http)
    assert await v.verify_token(keys.token()) is None
    v2 = JwtVerifier(jwks_url=JWKS_URL, issuer=ISS, audience=AUD, http=http)
    assert await v2.verify_token(keys.token()) is None


@pytest.mark.anyio
async def test_non_string_kid_header_is_rejected(verifier, keys):
    import base64

    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    def b64(b: bytes) -> str:
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

    def handmade(header: dict) -> str:   # PyJWT refuses to encode a non-string kid, so sign by hand
        payload = {"iss": ISS, "aud": AUD, "sub": "x", "scope": "mcp", "exp": int(time.time()) + 60}
        signing = b64(json.dumps(header).encode()) + "." + b64(json.dumps(payload).encode())
        sig = keys.private.sign(signing.encode(), padding.PKCS1v15(), hashes.SHA256())
        return signing + "." + b64(sig)

    assert await verifier.verify_token(handmade({"alg": "RS256", "typ": "JWT", "kid": 42})) is None
    assert await verifier.verify_token(handmade({"alg": "RS256", "typ": "JWT"})) is None   # no kid at all


@pytest.mark.anyio
async def test_concurrent_cold_starts_share_one_fetch(keys):
    import anyio

    v = JwtVerifier(jwks_url=JWKS_URL, issuer=ISS, audience=AUD, http=keys.http())
    results = []

    async def one():
        results.append(await v.verify_token(keys.token()))

    async with anyio.create_task_group() as tg:
        for _ in range(8):
            tg.start_soon(one)
    assert all(r is not None for r in results) and keys.requests == 1
