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
async def test_jwks_is_cached_and_refetched_once_on_unknown_kid(verifier, keys):
    await verifier.verify_token(keys.token())
    await verifier.verify_token(keys.token())
    assert keys.requests == 1
    assert await verifier.verify_token(keys.token(_kid="new")) is None
    assert keys.requests == 2
