"""Scopes: the single table of who may call what, and the check every tool runs.

The Python SDK has no scope decorator, so each handler calls ``require``. Over HTTP the
scopes come from the verified bearer token (``get_access_token``). Over stdio or
in-memory there is no token; the server then runs with ``STDIO_SCOPES`` from the
environment, read scopes only by default, so local use is possible but never silently
privileged.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.mcpserver.exceptions import ToolError

SCOPES = ("desk:read", "desk:write", "groups:read", "groups:write", "export",
          "pii:read", "admin:read", "admin:write", "admin:erase")

DEFAULT_STDIO_SCOPES = "desk:read,groups:read,admin:read"

# tool -> scopes it requires (all of them). Spec §6.2.
TOOL_SCOPES: dict[str, tuple[str, ...]] = {
    "list_pending_registrations": ("desk:read",),
    "list_approved_registrations": ("desk:read",),
    "get_registration": ("desk:read",),
    "desk_minor_alerts": ("desk:read",),
    "list_groups": ("groups:read",),
    "get_group": ("groups:read",),
    "month_summary": ("admin:read",),
    "audit_log": ("admin:read",),
    "approve_registration": ("desk:write",),
    "return_registration": ("desk:write",),
    "create_group_link": ("groups:write", "pii:read"),      # the result carries the portal password
    "resend_group_invite": ("groups:write", "pii:read"),
    "export_authority_report": ("export", "pii:read"),
    "edit_approved_registration": ("admin:write", "pii:read"),
    "erase_registration": ("admin:erase",),
    "purge_month": ("admin:erase",),
}

RESOURCE_SCOPES: dict[str, tuple[str, ...]] = {
    "hostel://policy/roles": (),
    "hostel://policy/retention": (),
    "hostel://registration/{token}": ("desk:read",),
    "hostel://group/{slug}": ("groups:read",),
    "export://{export_id}": ("export", "pii:read"),
}

# Which app role holds which scope (the Keycloak realm mirrors this).
ROLE_SCOPES: dict[str, tuple[str, ...]] = {
    "receptionist": ("desk:read", "desk:write", "export", "pii:read"),
    "group_manager": ("desk:read", "desk:write", "groups:read", "groups:write", "export", "pii:read"),
    "admin": SCOPES,
}


@dataclass(frozen=True)
class Caller:
    scopes: frozenset[str]
    actor: str                 # written to admin_audit
    authenticated: bool        # True when a bearer token was verified


def stdio_scopes() -> set[str]:
    raw = os.environ.get("STDIO_SCOPES", DEFAULT_STDIO_SCOPES)
    return {s.strip() for s in raw.split(",") if s.strip()}


def current_caller(ctx=None) -> Caller:
    token = get_access_token()
    if token is None:
        return Caller(scopes=frozenset(stdio_scopes()), actor="mcp:stdio", authenticated=False)
    who = token.subject or token.client_id
    return Caller(scopes=frozenset(token.scopes), actor=f"mcp:{who}", authenticated=True)


def current_scopes(ctx=None) -> set[str]:
    return set(current_caller(ctx).scopes)


def require(ctx, tool: str, *scopes: str) -> Caller:
    """Raise ``ToolError`` naming the first missing scope; return the caller otherwise."""
    caller = current_caller(ctx)
    for s in scopes or TOOL_SCOPES.get(tool, ()):
        if s not in caller.scopes:
            raise ToolError(f"missing scope {s}; this token cannot call {tool}")
    return caller


def has(ctx, scope: str) -> bool:
    return scope in current_caller(ctx).scopes


def roles_markdown() -> str:
    """Scope → tools matrix for the ``hostel://policy/roles`` resource."""
    lines = ["# Scopes and what they allow", "",
             "| Scope | Roles that hold it | Tools / resources |", "|---|---|---|"]
    for scope in SCOPES:
        roles = [r for r, ss in ROLE_SCOPES.items() if scope in ss]
        grants = [t for t, ss in TOOL_SCOPES.items() if scope in ss]
        grants += [u for u, ss in RESOURCE_SCOPES.items() if scope in ss]
        lines.append(f"| `{scope}` | {', '.join(roles)} | {', '.join(f'`{g}`' for g in grants) or '—'} |")
    lines += ["", "Every tool requires every scope listed for it; `export_authority_report`, "
              "`edit_approved_registration`, `create_group_link` and `resend_group_invite` need `pii:read` "
              "as well as their own scope (the last two return a portal password that unlocks member data). Write "
              "scopes do not imply read scopes.", "",
              f"Over stdio (no OAuth) the server runs with `STDIO_SCOPES` (default `{DEFAULT_STDIO_SCOPES}`)."]
    return "\n".join(lines)
