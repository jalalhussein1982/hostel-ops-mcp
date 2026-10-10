# hostel-ops-mcp — design

**Status:** draft for review, 2026-10-11.
**Source app:** [`jalalhussein1982/hostel-checkin-system`](https://github.com/jalalhussein1982/hostel-checkin-system) (FastAPI + Jinja2 + SQLite, production at a Prague hostel; this repo targets the anonymized portfolio copy).
**Protocol:** MCP revision 2026-07-28. **SDK:** MCP Python SDK 2.x (`mcp>=2.2`).

## 1. What this server proves

The three MCP servers already in the portfolio (`ihkam-eval-mcp`, `siem-mcp`, `energy_platform/mcp`) are local, stdio, Python, and read-only or nearly so. This one closes the gap an interviewer will probe first: **can you put a live business system with personal data behind MCP without leaking data or letting an agent do damage?**

Concretely it demonstrates:

| Capability | Where |
|---|---|
| Remote **Streamable HTTP**, stateless, deployable | §5, §10 |
| **OAuth 2.1 resource server**: bearer tokens, RFC 9728 protected-resource metadata, scope checks per tool | §6 |
| **Write tools made safe**: honest annotations, state-machine preconditions, idempotency, audit rows | §4 |
| **Multi Round-Trip Requests** (the 2026-07-28 replacement for elicitation) to confirm destructive actions, with sealed `requestState` | §4.4, §7 |
| **PII minimisation by scope**: document numbers and signatures only reach callers holding `pii:read` | §6.3 |
| Synthetic-data mode by default, live mode by flag (same pattern as `ihkam-eval-mcp`) | §9 |

Non-goals: no change to the hostel app's code; no guest-facing tools (the wizard stays a human flow); no OAuth authorization server of our own (we verify tokens, we never mint them, except in tests).

## 2. Relationship to the source app

The app has no JSON API with token auth: desk routes return JSON but take form bodies and a signed session cookie, and user management is HTML with redirects. Driving it over HTTP would mean a cookie jar and form encoding for every call. The service layer underneath is clean and role-agnostic, so the server **imports the app's Python modules** and reuses them against the same `DATABASE_PATH`:

| App module | Used for |
|---|---|
| `app.repo` | `list_pending`, `list_all_approved`, `get_registration`, `approve`, `set_returned`, `update_registration`, `delete_registration`, `month_summary`, `delete_period`, `audit`, `audit_recent`, `backup_log_*` |
| `app.groups` | `create`, `get`, `list_pending`, `list_submitted`, `members`, `panel_state`, `update_invite`, `delete`, `delete_group`, `add_member` |
| `app.state` | `State`, `Action`, `next_state`, `InvalidTransition` (the registration state machine) |
| `app.export` | `FIELDS`, `normalize`, `build_xlsx`, `build_pdf` |
| `app.dates` | `is_under_18` |
| `app.auth` | `hash_password` only (for group portal passwords); **not** for MCP auth |
| `app.backup` | `verify_snapshot` inside the purge guard |
| `app.db`, `app.config` | connection and `DATABASE_PATH` |

The app is installed as a pinned dependency (`hostel-checkin-system @ git+https://...@<commit>`). Nothing in the app repo changes. What the HTTP layer does that the service layer does not, the server re-implements explicitly and tests: state checks via `next_state`, APPROVED-only edit, the last-admin rule is out of scope (no user management tools), and the month-purge guard (§4.4).

Known limitation, stated in the README: `app.events.notify()` is in-process, so an approval made through MCP does not wake a guest's SSE "waiting" screen; the guest page falls back to `/status/{token}` polling, which the app already does.

## 3. Architecture

```
MCP host (Claude Code / Desktop / Inspector / agent)
        │  Streamable HTTP, Bearer token            stdio (local dev, no auth)
        ▼
┌─────────────────────────────────────────────────────────────┐
│ hostel_ops_mcp.server      MCPServer, tools/resources/prompts│
│   ├─ auth.py               TokenVerifier (JWT via JWKS), scopes│
│   ├─ guards.py             state-machine + scope preconditions│
│   ├─ redact.py             field policy per scope             │
│   ├─ confirm.py            MRTR confirmation helper           │
│   ├─ ops.py                plain-Python operations (no MCP)   │
│   └─ synth.py              synthetic DB generator             │
└───────────────┬─────────────────────────────────────────────┘
                │ imports
        hostel-checkin-system  app.repo / app.groups / app.state / app.export
                │
          SQLite (DATABASE_PATH)   demo: synthetic   live: the app's volume
```

Rules carried over from the earlier servers: `ops.py` has no MCP in it and is testable alone; `server.py` is a thin adapter; tools register in one fixed order so `tools/list` is deterministic; every tool returns a Pydantic model so `structured_content` matches a declared output schema.

## 4. Tools

Prefix: none inside the server (the host namespaces by server name). Order below is the registration order.

### 4.1 Read tools (`read_only_hint=True`, `open_world_hint=False`)

| Tool | Scope | Input | Output model | Notes |
|---|---|---|---|---|
| `list_pending_registrations` | `desk:read` | `limit≤100`, `cursor?` | `Page[RegistrationCard]` | PENDING_REVIEW rows with `under18` flag (`dates.is_under_18`), `is_group_leader`, `group_id`. No document numbers at this scope. |
| `list_approved_registrations` | `desk:read` | `arrival_from?`, `arrival_to?`, `group_id?`, `limit`, `cursor?` | `Page[RegistrationCard]` | Mirrors `/completed`: future group rows hidden until arrival. |
| `get_registration` | `desk:read` (+`pii:read` for full record) | `token` | `Registration` | Field set depends on scope (§6.3). |
| `desk_minor_alerts` | `desk:read` | none | `list[MinorAlert]` | Under-18 among pending and approved-today; under-15 rows missing `signer_role=legal_representative` flagged `needs_legal_representative`. |
| `list_groups` | `groups:read` | `status: pending\|submitted\|all` | `list[GroupCard]` | From `groups.list_pending` / `list_submitted`; leader contact only with `pii:read`. |
| `get_group` | `groups:read` | `slug` | `GroupDetail` | `panel_state` counts (`total`, `filled`, `attention_ids`, `duplicates`) plus members as cards. |
| `month_summary` | `admin:read` | none | `list[MonthRow]` | `repo.month_summary()` joined with `backup_log` so each month shows `entries`, `backed_up`, `verified`, `deleted`. |
| `audit_log` | `admin:read` | `limit≤200` | `list[AuditRow]` | `repo.audit_recent`. Includes rows this server wrote. |

### 4.2 Write tools, non-destructive

| Tool | Scope | Annotations | Behaviour |
|---|---|---|---|
| `approve_registration(token)` | `desk:write` | `idempotent_hint=True`, `destructive_hint=False` | Preconditions: state is PENDING_REVIEW (via `state.next_state(current, Action.APPROVE)`). Calls `repo.approve`; returns `ApprovalResult{token, serial, approved_at}`. Calling it again on an APPROVED row returns the same result with `already_approved=True` instead of erroring, so retries are safe. Writes `repo.audit(actor, "approve", detail=token)`. |
| `return_registration(token, reason, step∈{1,2,3})` | `desk:write` | `idempotent_hint=False` | PENDING_REVIEW → RETURNED with reason and wizard step, as the desk's `/return` does. Audited. |
| `create_group_link(group_name, leader_first_name, leader_last_name, arrival, departure, purpose_of_stay, receiver_email, expected_members?)` | `groups:write` | `idempotent_hint=False`, `open_world_hint=True` (sends email) | Generates a portal password, calls `groups.create` then the invite path; returns `GroupLink{slug, portal_url, member_url, email_sent}`. The password is returned **once** in this result and never stored by the server. |
| `resend_group_invite(slug, receiver_email?)` | `groups:write` | | `groups.update_invite` with a fresh password; same return shape. |
| `export_authority_report(tokens[] \| {arrival_from, arrival_to}, format: xlsx\|pdf)` | `export` + `pii:read` | `read_only_hint=False` (writes a file) | Builds through `export.normalize` + `build_xlsx`/`build_pdf` on APPROVED rows only, writes to `EXPORT_DIR`, returns `ExportResult{resource_uri, rows, sha256, generated_at}`. The bytes are read through the `export://{id}` resource (§5), not inlined. |

### 4.3 Admin edit

| Tool | Scope | Annotations | Behaviour |
|---|---|---|---|
| `edit_approved_registration(token, fields)` | `admin:write` + `pii:read` | `destructive_hint=False`, `idempotent_hint=True` | `repo.update_registration` (raises `NotEditable` unless APPROVED, mapped to `ToolError`). Fields validated with the app's `validation.validate_data_fields` where available. Returns `{changed, old_serial, serial}`. Audited with the changed field names, never the values. |

### 4.4 Destructive tools, confirmed by Multi Round-Trip Request

Both tools follow the same shape: the first call returns an `InputRequiredResult` carrying a form elicitation and a sealed `request_state`; the host asks the human; the retry carries `input_responses`; the handler acts only on a validated `accept` with `confirm=True`. Decline or cancel returns a non-error "cancelled" result. A missing or malformed answer re-asks.

| Tool | Scope | What is confirmed |
|---|---|---|
| `erase_registration(token)` | `admin:erase` | Message names the serial, arrival date and the retention position (the app's legal basis is Act 326/1999, erasure is limited; the elicitation text says so). On accept: `repo.delete_registration` (signature cascades), audit `delete`. Returns `{deleted: 1, removed_group: bool}`. |
| `purge_month(period "YYYY-MM")` | `admin:erase` | Re-implements the app's `_delete_guard`: backup configured, `backup_log` row verified for that period, live `count_approved_for_period` equals the logged `entry_count`, and the snapshot decrypts and re-verifies (`backup.verify_snapshot`). The guard runs **before** the elicitation so the human only ever confirms a purge that is actually allowed, and the message quotes `entry_count` and `snapshot_sha256`. On accept: `repo.delete_period(period, expected_count)`; `CountChanged` → `ToolError`. |

`request_state` carries `{tool, token-or-period, entry_count, issued_at}`. The SDK seals it; §7 covers keys. Because the state is bound to the tool name and arguments, a retry with different arguments fails verification instead of acting.

Hosts that do not declare elicitation support never receive the elicitation request: the tool returns `ToolError("this host cannot confirm destructive actions; use the admin panel")`.

### 4.5 Error policy

`ToolError` (message reaches the model) for everything the model can fix: unknown token or slug (names the list tool to call), `InvalidTransition` (states the current state and the allowed actions), missing scope (names the scope), `NotEditable`, `CountChanged`. Anything else is an unexpected exception: logged server-side with the request id, generic message to the caller.

## 5. Resources and prompts

| URI | Content | Scope |
|---|---|---|
| `hostel://policy/roles` | The scope → tool matrix (§6.2) as Markdown, so an agent quotes the policy instead of guessing | any |
| `hostel://policy/retention` | Legal basis, erasure position, purge guard, in the words of the app's privacy notice (`app/i18n/en.json` `privacy_*` keys) | any |
| `hostel://registration/{token}` | Same as `get_registration` | `desk:read` |
| `hostel://group/{slug}` | Same as `get_group` | `groups:read` |
| `export://{export_id}` | XLSX or PDF bytes as a blob resource, `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` or `application/pdf` | `export` + `pii:read` |

Resource reads carry `ttlMs` (policy: 1 h public; records: 0, private) and `cacheScope` as the 2026-07-28 revision requires.

Prompts:

- `morning_desk_briefing` — fixed instructions: call `list_pending_registrations`, `desk_minor_alerts`, `list_groups(status=pending)`, then write the receptionist's handover in the house format (arrivals today, minors, groups awaiting members, anything RETURNED older than 24 h).
- `authority_export_checklist(period)` — the steps and checks before an export leaves the building, ending with `export_authority_report`.

## 6. Authorization

### 6.1 Model

The server is an **OAuth 2.1 resource server**. It verifies bearer tokens issued by an authorization server it does not own and advertises that server through RFC 9728 metadata at `/.well-known/oauth-protected-resource/mcp`. SDK wiring: `MCPServer(name, token_verifier=JwtVerifier(...), auth=AuthSettings(issuer_url, resource_server_url, required_scopes=["mcp"], validate_token_resource=True))`.

`JwtVerifier.verify_token(token) -> AccessToken | None`: fetch and cache the issuer's JWKS, verify signature, `exp`, `iss`, and `aud == resource_server_url`, read `scope` (space-separated) and `sub`, return `AccessToken(token, client_id, scopes, expires_at, resource, subject)`. Any failure returns `None`, which the SDK turns into `401` with `WWW-Authenticate: Bearer error="invalid_token", resource_metadata=...`.

Per-tool scope checks are explicit in the handler (the Python SDK has no scope decorator): `guards.require(ctx, "desk:write")` reads `get_access_token()`, raises `ToolError` naming the missing scope. Over stdio, `get_access_token()` is `None`; the server then runs with a configured `STDIO_SCOPES` set (default: read scopes only) so local use is possible but never silently privileged.

The demo authorization server is **Keycloak** in `docker-compose.yml`, with a realm export that defines the scopes below and three test users (admin, group_manager, receptionist) whose roles map to scopes. A Client ID Metadata Document is served for the Inspector; dynamic registration stays off. Tests do not use Keycloak: `tests/conftest.py` mints tokens with a test key pair and points the verifier at a local JWKS.

### 6.2 Scopes

| Scope | App role that holds it | Grants |
|---|---|---|
| `desk:read` | receptionist, group_manager, admin | §4.1 registration reads, `desk_minor_alerts`, `hostel://registration/*` |
| `desk:write` | receptionist, group_manager, admin | `approve_registration`, `return_registration` |
| `groups:read` / `groups:write` | group_manager, admin | group reads / `create_group_link`, `resend_group_invite` |
| `export` | receptionist, group_manager, admin | `export_authority_report`, `export://*` (also needs `pii:read`) |
| `pii:read` | receptionist, group_manager, admin by default; **revocable per client** | unmasked document numbers, dates of birth, addresses, signatures, leader contact details |
| `admin:read` | admin | `month_summary`, `audit_log` |
| `admin:write` | admin | `edit_approved_registration` |
| `admin:erase` | admin | `erase_registration`, `purge_month` |

The point of `pii:read` being separate: an agent that triages the queue or writes the morning briefing can be given a token without it, and then the server cannot leak what it never returned.

### 6.3 Field policy (`redact.py`)

| Field group | Without `pii:read` | With `pii:read` |
|---|---|---|
| `last_name`, `first_name`, `nationality`, `arrival`, `departure`, `nights`, `purpose_of_stay`, `state`, `serial`, `lang`, `under18` | returned | returned |
| `dob` | `age_band` only (`<15`, `15-17`, `18+`) | returned |
| `passport_no`, `visa_no`, `car_reg` | `"•••" + last 2` | returned |
| `addr_*`, `addr_display` | `addr_country` only | returned |
| `signature_png`, `signer_name` | absent | returned via `get_registration(include_signature=True)` |
| group `leader_email`, `leader_phone_*`, `receiver_email`, `draft_json` | absent | returned (`draft_json` never) |

The policy is a table in code and a resource in text, and one test asserts every column of every table is classified.

## 7. State and secrets

The server is stateless across requests. What exists:

- **`request_state` sealing keys**: `REQUEST_STATE_KEYS` (≥32 bytes each, comma-separated) passed as `RequestStateSecurity(keys=[...], ttl=300)`. With one replica the SDK's per-process key is enough; the env var exists so a second replica can verify what the first minted.
- **Audit actor**: the token's `sub` (or `client_id`), written as `repo.audit(actor=f"mcp:{sub}", ...)`. The app's audit table gains rows from a new kind of actor and nothing else changes.
- **Exports**: files under `EXPORT_DIR` named by id, deleted after `EXPORT_TTL_MINUTES` (default 60) by the next request that runs the sweeper.

Env: `HOSTEL_DATABASE_PATH` (default: the synthetic DB), `HOSTEL_MODE=synthetic|live`, `OAUTH_ISSUER_URL`, `OAUTH_JWKS_URL`, `RESOURCE_SERVER_URL`, `REQUEST_STATE_KEYS`, `EXPORT_DIR`, `STDIO_SCOPES`, `PUBLIC_BASE_URL` (for portal links in `create_group_link`), SMTP settings passed through to the app's `group_email`.

## 8. Testing

All in-memory through `Client(mcp)`; no network, no Keycloak.

1. **Protocol**: tool order is fixed; every tool has an output schema; annotations match the table in §4; resources list carries `ttlMs`/`cacheScope`.
2. **State machine**: approve on PENDING succeeds; approve on APPROVED is idempotent; approve on DRAFT/RETURNED → `ToolError` naming the state; return requires step 1–3.
3. **Scopes**: a parametrised matrix (scope set × tool) asserting allow or `ToolError` for every pair in §6.2.
4. **Redaction**: every PII column is absent or masked without `pii:read`; present with it; a schema-walk test fails if a new column appears unclassified.
5. **MRTR**: `erase_registration` first call returns `InputRequiredResult` with one form request and a `request_state`; retry with `accept/confirm=true` deletes once; `decline` deletes nothing; a tampered or expired state is rejected (`-32602`); a retry with another token is rejected; a host without elicitation capability gets `ToolError`.
6. **Purge guard**: each of the four guard conditions failing blocks the purge **before** any elicitation is produced.
7. **Auth unit tests**: `JwtVerifier` with a test JWKS: good token, expired, wrong audience, wrong issuer, unknown kid.
8. **HTTP smoke** (one test, real ASGI transport in-process): no token → 401 with `resource_metadata`; token lacking `mcp` → 401; valid token → `tools/list`.

Target: 60–80 tests, under 10 s.

## 9. Synthetic data

`synth.py` builds a SQLite file with the app's own `db.init_db()` and then inserts ~60 registrations across the four states, three groups (one pending, two submitted), a minor, an under-15 with and without legal representative, two months with `backup_log` rows (one verified, one not), and a few audit rows. Names and documents come from a fixed seed so tests are deterministic. No real person appears. `make fixtures` regenerates it.

## 10. Deployment and demo

- **Local**: `uv run python -m hostel_ops_mcp.server --stdio` for Claude Code (`.mcp.json` provided); `make http` runs Streamable HTTP on `127.0.0.1:8000/mcp` with Keycloak from `docker compose up`; `make inspector` opens the MCP Inspector with the Client ID Metadata Document.
- **Demo deployment**: a Fly.io app in `fra` with its own volume holding the synthetic DB, Keycloak as a second Fly app (or Fly's Postgres-less Keycloak dev-file mode for the demo). Public URL in the README, read-only demo credentials for the three roles.
- **Attaching to the real hostel**: the MCP process must run on the same machine as the app because SQLite is a file on one volume. That is a `[processes]` entry in the app's `fly.toml` and a second service port, documented in `docs/live-attach.md`; it is the owner's decision and not part of this repo's deliverable.

## 11. Repository layout

```
hostel-ops-mcp/
  README.md                     what it proves, run it, host configs, design decisions
  pyproject.toml                mcp[cli], pydantic, PyJWT[crypto], httpx; dev: pytest, anyio
  .mcp.json                     stdio registration for Claude Code
  docker-compose.yml            Keycloak + realm export
  keycloak/realm-hostel.json
  hostel_ops_mcp/
    server.py  auth.py  guards.py  redact.py  confirm.py  ops.py  synth.py  models.py
  fixtures/synthetic.db         generated, committed (small)
  scripts/make_fixtures.py  scripts/dev_token.py
  tests/                        test_protocol.py test_state.py test_scopes.py test_redact.py
                                test_mrtr.py test_purge_guard.py test_auth.py test_http.py
  evals/hostel-ops.xml          10 read-only QA pairs over the synthetic DB (mcp-builder format)
  docs/superpowers/specs/       this document
  docs/live-attach.md
```

## 12. Build order

1. Skeleton, `ops.py` read paths over the synthetic DB, read tools, protocol tests.
2. Redaction policy and scope guards with stdio scopes; matrix tests.
3. Write tools with state machine and audit; idempotency tests.
4. MRTR confirmation for `erase_registration`, then `purge_month` with the guard.
5. `JwtVerifier`, `AuthSettings`, HTTP smoke test, Keycloak compose and realm.
6. Exports and the `export://` resource.
7. Prompts, resources, README, evals file, Fly deployment.

## 13. Open decisions for the owner

- Whether `pii:read` is granted to receptionists by default (this spec says yes, matching the desk) or only to admin.
- Whether the demo deployment runs at all, or the README's Inspector recording is enough.
- Repo visibility: public for the portfolio (this spec assumes public; the synthetic DB contains no real data).
