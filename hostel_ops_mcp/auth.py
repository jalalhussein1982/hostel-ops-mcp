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

import anyio
import httpx
import jwt
from mcp.server.auth.provider import AccessToken

log = logging.getLogger("hostel_ops_mcp.auth")

DEFAULT_ALGORITHMS = ("RS256", "RS384", "RS512", "PS256", "ES256", "ES384")
REFETCH_INTERVAL = 30.0          # seconds between JWKS fetches triggered by an unknown kid
MAX_JWKS_BYTES = 64 * 1024       # a JWKS is a few KiB; anything larger is not one


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
        self._fetched_at = 0.0        # last successful fetch
        self._attempted_at = 0.0      # last fetch attempt, success or not (throttles unknown-kid refetches)
        self._lock = anyio.Lock()

    async def _fetch(self) -> None:
        """One JWKS fetch. Never raises: on any failure the cached keys stay as they were."""
        self._attempted_at = time.monotonic()
        try:
            resp = await self.http.get(self.jwks_url)
            resp.raise_for_status()
            if len(resp.content) > MAX_JWKS_BYTES:
                raise ValueError(f"JWKS body is {len(resp.content)} bytes; limit is {MAX_JWKS_BYTES}")
            data = resp.json()
            if not isinstance(data, dict) or not isinstance(data.get("keys"), list):
                raise ValueError("JWKS is not an object with a `keys` array")
            keys: dict[str, Any] = {}
            for jwk in data["keys"]:
                if not isinstance(jwk, dict):
                    continue
                kid = jwk.get("kid")
                if isinstance(kid, str) and kid and jwk.get("use", "sig") == "sig":
                    try:
                        keys[kid] = jwt.PyJWK(jwk).key
                    except Exception:  # noqa: BLE001 - one bad key must not take the ring down
                        log.warning("skipping unusable JWK kid=%s", kid)
            self._keys, self._fetched_at = keys, time.monotonic()
        except Exception as e:  # noqa: BLE001 - a JWKS problem is a 401 for the caller, never a 500
            log.warning("JWKS fetch from %s failed: %s: %s", self.jwks_url, type(e).__name__, e)

    async def _key_for(self, kid: str) -> Any | None:
        """The verification key for `kid`, fetching the JWKS when stale or when the kid is unknown,
        at most once per REFETCH_INTERVAL for unknown kids. Serialised so concurrent cold starts
        share one fetch."""
        async with self._lock:
            now = time.monotonic()
            fresh = bool(self._keys) and now - self._fetched_at < self.cache_ttl
            if fresh and kid in self._keys:
                return self._keys[kid]
            if now - self._attempted_at >= REFETCH_INTERVAL or not fresh and not self._keys:
                if not fresh or kid not in self._keys:
                    await self._fetch()
            return self._keys.get(kid)

    async def verify_token(self, token: str) -> AccessToken | None:
        if not token:
            return None
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            return None
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            return None
        key = await self._key_for(kid)
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
