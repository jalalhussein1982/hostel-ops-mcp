"""Bearer-token verification for the OAuth 2.1 resource-server mode.

The server never mints tokens. ``JwtVerifier`` fetches the authorization server's JWKS
(cached, re-fetched once on an unknown ``kid``), verifies signature, ``exp``, ``iss`` and
``aud`` with PyJWT, and maps the claims onto the SDK's ``AccessToken``. Any failure is
``None``, which the SDK turns into ``401`` with ``WWW-Authenticate: Bearer error=
"invalid_token", resource_metadata=...``.

``parse_scope`` accepts the space-separated string RFC 8693 describes and the array some
identity providers emit instead.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx
import jwt
from mcp.server.auth.provider import AccessToken

log = logging.getLogger("hostel_ops_mcp.auth")

DEFAULT_ALGORITHMS = ("RS256", "RS384", "RS512", "PS256", "ES256", "ES384")


def parse_scope(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [s for s in value.split() if s]
    if isinstance(value, (list, tuple)):
        return [str(s) for s in value if str(s)]
    return []


class JwtVerifier:
    """``TokenVerifier`` implementation over a JWKS endpoint."""

    def __init__(self, jwks_url: str, issuer: str, audience: str, http: httpx.AsyncClient | None = None,
                 cache_ttl: float = 600.0, algorithms: tuple[str, ...] = DEFAULT_ALGORITHMS) -> None:
        self.jwks_url = jwks_url
        self.issuer = issuer.rstrip("/")
        self.audience = audience
        self.http = http or httpx.AsyncClient(timeout=10.0)
        self.cache_ttl = cache_ttl
        self.algorithms = list(algorithms)
        self._keys: dict[str, Any] = {}
        self._fetched_at = 0.0

    async def _jwks(self, force: bool = False) -> dict[str, Any]:
        if not force and self._keys and time.monotonic() - self._fetched_at < self.cache_ttl:
            return self._keys
        resp = await self.http.get(self.jwks_url)
        resp.raise_for_status()
        keys: dict[str, Any] = {}
        for jwk in resp.json().get("keys", []):
            kid = jwk.get("kid")
            if kid and jwk.get("use", "sig") == "sig":
                try:
                    keys[kid] = jwt.PyJWK(jwk).key
                except jwt.PyJWTError:
                    log.warning("skipping unusable JWK kid=%s", kid)
        self._keys, self._fetched_at = keys, time.monotonic()
        return keys

    async def verify_token(self, token: str) -> AccessToken | None:
        if not token:
            return None
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            return None
        kid = header.get("kid")
        try:
            keys = await self._jwks()
            key = keys.get(kid)
            if key is None:
                key = (await self._jwks(force=True)).get(kid)
        except (httpx.HTTPError, ValueError) as e:
            log.warning("JWKS fetch failed: %s", e)
            return None
        if key is None:
            return None
        try:
            claims = jwt.decode(
                token, key=key, algorithms=self.algorithms, audience=self.audience, issuer=self.issuer,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError as e:
            log.info("token rejected: %s", type(e).__name__)
            return None
        scopes = parse_scope(claims.get("scope")) or parse_scope(claims.get("scp"))
        client_id = claims.get("azp") or claims.get("client_id") or claims["sub"]
        return AccessToken(
            token=token, client_id=str(client_id), scopes=scopes, expires_at=int(claims["exp"]),
            resource=self.audience, subject=str(claims["sub"]), claims={"iss": claims["iss"]},
        )
