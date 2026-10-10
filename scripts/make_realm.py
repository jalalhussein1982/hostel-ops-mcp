"""Generate keycloak/realm-hostel.json from guards.py so the realm and the code never drift.

    uv run python scripts/make_realm.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hostel_ops_mcp import guards  # noqa: E402

RESOURCE = "http://127.0.0.1:8000/mcp"
SCOPE_DESC = {
    "mcp": "May call this MCP server at all (required_scopes).",
    "desk:read": "Registration reads: queue, approved list, one record, minor alerts.",
    "desk:write": "approve_registration, return_registration.",
    "groups:read": "Group reads.",
    "groups:write": "create_group_link, resend_group_invite.",
    "export": "export_authority_report and export:// resources (also needs pii:read).",
    "pii:read": "Unmasked document numbers, dates of birth, addresses, signatures, leader contacts.",
    "admin:read": "month_summary, audit_log.",
    "admin:write": "edit_approved_registration.",
    "admin:erase": "erase_registration, purge_month.",
}
AUDIENCE_MAPPER = {
    "name": "hostel-ops audience", "protocol": "openid-connect", "protocolMapper": "oidc-audience-mapper",
    "consentRequired": False,
    "config": {"included.custom.audience": RESOURCE, "id.token.claim": "false", "access.token.claim": "true",
               "lightweight.claim": "false", "introspection.token.claim": "true"},
}


def client_scope(name: str, mappers=()) -> dict:
    return {"name": name, "description": SCOPE_DESC[name], "protocol": "openid-connect",
            "attributes": {"include.in.token.scope": "true", "display.on.consent.screen": "true",
                           "consent.screen.text": SCOPE_DESC[name]},
            "protocolMappers": list(mappers)}


def user(username: str, first: str, last: str, role: str) -> dict:
    return {"username": username, "enabled": True, "firstName": first, "lastName": last,
            "email": f"{username}@example.invalid", "emailVerified": True, "realmRoles": [role],
            "credentials": [{"type": "password", "value": "changeme", "temporary": False}]}


def build() -> dict:
    client_scopes = [client_scope("mcp", [AUDIENCE_MAPPER])] + [client_scope(s) for s in guards.SCOPES]
    # A client scope with role scope-mappings is granted only to users holding one of those roles.
    scope_mappings = [{"clientScope": s, "roles": [r for r, ss in guards.ROLE_SCOPES.items() if s in ss]}
                      for s in guards.SCOPES]
    return {
        "realm": "hostel", "enabled": True, "displayName": "Demo Hostel (hostel-ops-mcp)",
        "sslRequired": "none", "registrationAllowed": False,
        "accessTokenLifespan": 900, "ssoSessionIdleTimeout": 1800,
        "roles": {"realm": [
            {"name": "admin", "description": "Every scope, incl. admin:read/write/erase."},
            {"name": "group_manager", "description": "Desk + groups + export + pii."},
            {"name": "receptionist", "description": "Desk + export + pii; no groups, no admin."},
        ]},
        "clientScopes": client_scopes,
        "defaultDefaultClientScopes": ["mcp"],
        "defaultOptionalClientScopes": list(guards.SCOPES),
        "scopeMappings": scope_mappings,
        "clients": [{
            "clientId": "hostel-ops-inspector", "name": "MCP Inspector (hostel-ops)", "enabled": True,
            "protocol": "openid-connect", "publicClient": True, "standardFlowEnabled": True,
            "directAccessGrantsEnabled": True, "implicitFlowEnabled": False, "serviceAccountsEnabled": False,
            "fullScopeAllowed": False,
            "redirectUris": ["http://localhost:6274/oauth/callback", "http://127.0.0.1:6274/oauth/callback",
                             "http://localhost:6274/oauth/callback/debug", "http://127.0.0.1:8000/*"],
            "webOrigins": ["http://localhost:6274", "http://127.0.0.1:6274"],
            "attributes": {"pkce.code.challenge.method": "S256", "post.logout.redirect.uris": "+"},
            "defaultClientScopes": ["mcp", "profile", "email"],
            "optionalClientScopes": list(guards.SCOPES),
        }],
        "users": [user("admin", "Ada", "Admin", "admin"), user("group_manager", "Gil", "Groups", "group_manager"),
                  user("receptionist", "Rea", "Desk", "receptionist")],
    }


def main() -> None:
    out = ROOT / "keycloak" / "realm-hostel.json"
    out.write_text(json.dumps(build(), indent=2) + "\n")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
