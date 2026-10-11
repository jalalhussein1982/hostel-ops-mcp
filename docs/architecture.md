# Architecture

```
MCP host (Claude Code / Desktop / Inspector / agent)
        │  Streamable HTTP + Bearer JWT                 stdio (local, STDIO_SCOPES)
        ▼
hostel_ops_mcp/
  server.py    MCPServer wiring: 16 tools in spec order, 6 resources, 2 prompts, cache middleware,
               create_server() / server_from_env() / main(--stdio|--http)
  guards.py    SCOPES, TOOL_SCOPES, ROLE_SCOPES; require(ctx, tool) reads the verified token or STDIO_SCOPES
  redact.py    FIELD_POLICY for every column of 4 tables; apply(record, table, scopes)
  confirm.py   confirm_or_request(): the Multi Round-Trip confirmation (ask / declined / confirmed)
  ops.py       plain-Python operations over the app's service layer; no MCP imports
  auth.py      JwtVerifier (JWKS, PyJWT) -> AccessToken
  policy.py    hostel://policy/* texts (scope matrix + field policy; privacy notice + purge guard)
  models.py    Pydantic output models = the tools' output schemas
  synth.py     deterministic synthetic database through the app's own repo/groups
  _app.py      sys.path shim + env for the vendored app; load(database_path)
        │ imports
vendor/hostel-checkin-system/app   repo · groups · state · export · dates · auth · backup · validation · i18n
        │
SQLite (HOSTEL_DATABASE_PATH)      demo: fixtures/synthetic.db   live: the app's volume (docs/live-attach.md)
```

## One request, end to end

`approve_registration(token)` over HTTP:

1. **Bearer middleware (SDK)**: `JwtVerifier.verify_token` → `AccessToken` or 401/403. The token is
   placed in a context variable.
2. **RequestStateBoundary (SDK middleware)**: not involved for this tool (no `requestState`).
3. **`cache_directives` (ours)**: passes tools through untouched.
4. **Tool handler** (`server.py`): `guards.require(ctx, "approve_registration")` reads the token's
   scopes, raises `ToolError("missing scope desk:write; …")` if absent; `ops.approve(token, actor)`.
5. **`ops.approve`**: `repo.get_registration` → already APPROVED? return idempotent result;
   else `state.next_state(State(rec.state), Action.APPROVE)` (InvalidTransition → `InvalidState` →
   `ToolError` naming state and allowed actions) → `repo.approve` → `repo.audit(actor, "approve")`.
6. **Model**: `ApprovalResult.model_validate(...)`; the SDK validates against the output schema
   and returns `structured_content`.

`erase_registration(token)` adds the round trip: handler → `confirm.confirm_or_request` →
`InputRequiredResult(request_state="erase:<token>")`. The SDK seals the state (AES-GCM; bound to
method, tool name, argument digest, `(client_id, iss, sub)`, TTL). The host asks the human and
retries with `input_responses` + the sealed state; the SDK verifies and unseals before the handler
runs (`-32602` on any mismatch); the handler sees `ctx.input_responses["confirm"]` and acts.

## Read path and redaction

Every read tool: `require` → `ops.<read>()` returns raw app rows → `redact.apply(row, table,
scopes)` → flags computed from the raw row (`under18`, `needs_legal_representative`) →
`Model.model_validate`. The policy is one table (`FIELD_POLICY`) keyed `table.column`; unknown
columns are dropped (fail-closed) and `tests/test_redact.py::test_every_column_of_every_table_is_classified`
walks `sqlite_master` so a new app column cannot ship unclassified.

## Why the app is a submodule, not an HTTP dependency

The app has no token-authenticated JSON API (form bodies + signed session cookies). Its service
layer is clean and role-agnostic, so the server imports it and shares `DATABASE_PATH`. Nothing
under `vendor/` is modified; what the HTTP layer did (state checks, APPROVED-only edit, the purge
guard, the desk's future-group rule) is re-implemented in `ops.py` and tested. The app has no
`pyproject.toml`, so it cannot be a pip git dependency; `_app.py` puts the submodule on `sys.path`
and sets the environment the app reads at import (`DATABASE_PATH`, `SESSION_SECRET`,
`COOKIE_SECURE`, `BACKUP_DIR`). Its runtime dependencies are listed in `pyproject.toml`.

## Writes and audit

Every write goes through the app's own functions (`repo.approve`, `repo.set_returned`,
`groups.create/update_invite`, `repo.update_registration`, `groups.delete_member`,
`repo.delete_period`) and writes an `admin_audit` row with actor `mcp:<sub>` (`mcp:stdio` locally).
Audit details carry tokens, serials, field names and counts, never values.

## Determinism and tests

Tools are registered in spec order inside `_register()`; `tests/test_protocol.py` pins the list.
The synthetic database is built through the app's own `repo`/`groups` with a seeded RNG for
tokens and a synthetic clock for timestamps, so two builds are byte-stable on the fields that
matter and `fixtures/synthetic.json` records the planted tokens for tests and docs. All tests
run in-memory through `Client(mcp)`; the HTTP smoke test runs the real ASGI app in-process with
`httpx.ASGITransport`. No test needs the network, Keycloak or Docker.

| File | What it pins |
|---|---|
| test_shim, test_synth | app import, schema init, fixture shape, determinism |
| test_redact | field policy, column coverage |
| test_protocol | tool order, output schemas, annotations, cache directives |
| test_read_tools | queue, cursor, minors, DRAFT card, groups, duplicates, months, audit |
| test_scopes | every (tool, scope-set) pair; write ≠ read |
| test_writes | approve/return state machine, idempotency, group link password |
| test_mrtr | first round, accept, decline, argument binding, tamper, no-capability host |
| test_purge_guard | each guard condition alone, question text, atomic purge, CountChanged |
| test_export | APPROVED-only export, xlsx/pdf, resource bytes, TTL sweep, edit |
| test_auth, test_http | JWT verifier; 401/403/metadata/tools over the real HTTP app |
| test_resources | policy resources, record resources, cache scope, prompts |
