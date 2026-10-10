"""Mint an RS256 development token with a local key pair; write the matching JWKS.

    uv run python scripts/dev_token.py --sub alice --scopes "mcp desk:read desk:write pii:read"
    python -m http.server 9001 --directory fixtures      # serves fixtures/dev-jwks.json
    OAUTH_ISSUER_URL=http://127.0.0.1:9001 OAUTH_JWKS_URL=http://127.0.0.1:9001/dev-jwks.json \\
      RESOURCE_SERVER_URL=http://127.0.0.1:8000/mcp uv run python -m hostel_ops_mcp.server --http

The private key lives in .dev/ (git-ignored); fixtures/dev-jwks.json is the public half.
For a real authorization server use the Keycloak realm in keycloak/ (docs/auth.md).
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

ROOT = Path(__file__).resolve().parent.parent
PRIVATE = ROOT / ".dev" / "dev-private.pem"
JWKS = ROOT / "fixtures" / "dev-jwks.json"
KID = "hostel-ops-dev"


def load_or_create_key():
    if PRIVATE.exists():
        return serialization.load_pem_private_key(PRIVATE.read_bytes(), password=None)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    PRIVATE.parent.mkdir(parents=True, exist_ok=True)
    PRIVATE.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
    PRIVATE.chmod(0o600)
    return key


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sub", default="dev-admin")
    ap.add_argument("--client-id", default="hostel-ops-inspector")
    ap.add_argument("--scopes", default="mcp desk:read desk:write groups:read groups:write export pii:read "
                                        "admin:read admin:write admin:erase")
    ap.add_argument("--iss", default="http://127.0.0.1:9001")
    ap.add_argument("--aud", default="http://127.0.0.1:8000/mcp")
    ap.add_argument("--ttl", type=int, default=3600, help="seconds")
    ap.add_argument("--scope-as-list", action="store_true", help="emit `scope` as a JSON array (some IdPs do)")
    a = ap.parse_args()

    key = load_or_create_key()
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    JWKS.write_text(json.dumps({"keys": [{**jwk, "kid": KID, "alg": "RS256", "use": "sig"}]}, indent=2) + "\n")
    now = int(time.time())
    scope = a.scopes.split() if a.scope_as_list else a.scopes
    token = jwt.encode(
        {"iss": a.iss, "aud": a.aud, "sub": a.sub, "azp": a.client_id, "scope": scope, "iat": now, "exp": now + a.ttl},
        key, algorithm="RS256", headers={"kid": KID},
    )
    print(token)


if __name__ == "__main__":
    main()
