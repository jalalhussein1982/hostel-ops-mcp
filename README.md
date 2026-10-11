# hostel-ops-mcp

An MCP server over a live guest-registration system with personal data in it, built so that an
agent can run the reception desk without leaking what it should not see or doing damage it
cannot undo. Remote Streamable HTTP, OAuth 2.1 resource server with per-tool scopes, write tools
guarded by the app's own state machine, PII minimised by scope, destructive actions confirmed
through Multi Round-Trip Requests. Protocol revision **2026-07-28**, MCP Python SDK **2.3**.

Built on [`jalalhussein1982/hostel-checkin-system`](https://github.com/jalalhussein1982/hostel-checkin-system)
(FastAPI + SQLite, in production at a Prague hostel), pinned as a git submodule at
`vendor/hostel-checkin-system` @ `13e2f7a`. Nothing in the app changes; this server imports its
service layer.

## What it proves

| Claim | Where it is tested |
|---|---|
| Sixteen tools in a fixed order, every one with an output schema and honest annotations | `tests/test_protocol.py` |
| A token without `pii:read` never receives a document number, a date of birth, an address, a signature or a leader's contact, from any tool or resource; every column of every table is classified | `tests/test_redact.py`, `test_read_tools.py`, `test_http.py` |
| Scopes are enforced per tool; write scopes do not imply read scopes; the error names the missing scope | `tests/test_scopes.py` (every tool × scope set) |
| Writes run the app's state machine and are idempotent where they say they are; every write audits | `tests/test_writes.py` |
| Erasure and month purge act only after a human confirms through the host; the sealed `requestState` rejects a retry with other arguments or a tampered token (`-32602`) | `tests/test_mrtr.py`, `test_purge_guard.py` |
| The purge guard (backup configured, verified, counts equal, snapshot decrypts and re-verifies) runs before anyone is asked | `tests/test_purge_guard.py` |
| Exports contain APPROVED rows only; bytes travel as a resource, not inline | `tests/test_export.py` |
| Bearer JWTs are verified against the issuer's JWKS; 401 with `resource_metadata`, 403 for a missing scope, RFC 9728 metadata; the real HTTP app is tested in-process | `tests/test_auth.py`, `test_http.py` |
| Synthetic data by default, no real person in the repo | `tests/test_synth.py` |

`uv run pytest -q` → **130 passed** in about 4 s. No test needs the network, Docker or Keycloak.

## Run it in sixty seconds

```sh
git clone --recurse-submodules <this repo> && cd hostel-ops-mcp
uv sync
uv run pytest -q                                              # 130 passed
uv run python -m hostel_ops_mcp.server --stdio                # stdio, read scopes only
npx @modelcontextprotocol/inspector uv run python -m hostel_ops_mcp.server --stdio
```

Over stdio there is no authentication, so the server runs with `STDIO_SCOPES`
(default `desk:read,groups:read,admin:read`). Give it more to try the write tools locally:

```sh
STDIO_SCOPES=desk:read,desk:write,groups:read,groups:write,export,pii:read,admin:read,admin:write,admin:erase \
  uv run python -m hostel_ops_mcp.server --stdio
```

Streamable HTTP as an OAuth 2.1 resource server, with Keycloak from the compose file:

```sh
docker compose up -d                                          # Keycloak, realm `hostel`, users admin/group_manager/receptionist (changeme)
OAUTH_ISSUER_URL=http://127.0.0.1:8080/realms/hostel \
RESOURCE_SERVER_URL=http://127.0.0.1:8000/mcp \
  uv run python -m hostel_ops_mcp.server --http               # 127.0.0.1:8000/mcp, stateless
```

Without Keycloak, `scripts/dev_token.py` mints an RS256 token with a local key pair and writes the
JWKS to serve with `python -m http.server`; see [docs/auth.md](docs/auth.md). `--http` without
`OAUTH_ISSUER_URL` serves unauthenticated HTTP with `STDIO_SCOPES` (local development only).

### Host configuration

Claude Code: the repo ships `.mcp.json`:

```json
{"mcpServers": {"hostel-ops": {"command": "uv",
  "args": ["run", "python", "-m", "hostel_ops_mcp.server", "--stdio"],
  "env": {"STDIO_SCOPES": "desk:read,groups:read,admin:read"}}}}
```

Claude Desktop (`claude_desktop_config.json`), with an absolute path:

```json
{"mcpServers": {"hostel-ops": {"command": "uv",
  "args": ["run", "--directory", "/path/to/hostel-ops-mcp", "python", "-m", "hostel_ops_mcp.server", "--stdio"],
  "env": {"STDIO_SCOPES": "desk:read,groups:read,admin:read"}}}}
```

Remote hosts: Streamable HTTP at `https://<host>/mcp` with the OAuth flow the host discovers from
`/.well-known/oauth-protected-resource/mcp`.

## Tools

Registration order is the listing order. Annotations are the real ones.

| Tool | Scope | Annotations | What it does |
|---|---|---|---|
| `list_pending_registrations(limit, cursor)` | `desk:read` | read-only | PENDING_REVIEW queue, oldest first, `under18` flag, cursor pagination |
| `list_approved_registrations(arrival_from, arrival_to, group_id, limit, cursor)` | `desk:read` | read-only | APPROVED rows as the desk sees them (future group rows hidden) |
| `get_registration(token, include_signature)` | `desk:read` | read-only | One record in any state (a DRAFT is a card, not an error); fields depend on scope |
| `desk_minor_alerts()` | `desk:read` | read-only | Under-18 in the queue or approved today; `needs_legal_representative` for under-15 |
| `list_groups(status)` | `groups:read` | read-only | Group requests; leader contact needs `pii:read` |
| `get_group(slug)` | `groups:read` | read-only | Panel totals, rows needing attention, duplicate people, members |
| `month_summary()` | `admin:read` | read-only | Live count per arrival month joined with the backup log |
| `audit_log(limit)` | `admin:read` | read-only | `admin_audit`, newest first |
| `approve_registration(token)` | `desk:write` | idempotent, non-destructive | PENDING_REVIEW → APPROVED through the state machine; `already_approved` on retry |
| `return_registration(token, reason, step)` | `desk:write` | non-destructive | PENDING_REVIEW → RETURNED with reason and wizard page |
| `create_group_link(...)` | `groups:write` | open-world (email) | Creates the group request; returns portal links and the password once |
| `resend_group_invite(slug, receiver_email?)` | `groups:write` | open-world | New password, optional corrected email |
| `export_authority_report(tokens \| window, format)` | `export` + `pii:read` | writes a file | APPROVED-only XLSX/PDF; bytes via `export://xlsx/{id}` or `export://pdf/{id}` |
| `edit_approved_registration(token, fields)` | `admin:write` + `pii:read` | idempotent | Wizard-validated edit of an APPROVED record; audit names fields only |
| `erase_registration(token)` | `admin:erase` | **destructive**, MRTR-confirmed | One record and its signature, after the human says yes |
| `purge_month(period)` | `admin:erase` | **destructive**, MRTR-confirmed | One arrival month, after the app's delete guard passes and the human says yes |

Resources: `hostel://policy/roles` (scope matrix + field policy, public, 1 h), `hostel://policy/retention`
(the app's privacy notice + the purge guard, public, 1 h), `hostel://registration/{token}`,
`hostel://group/{slug}` (private, `ttlMs=0`), `export://xlsx/{id}`, `export://pdf/{id}`.
Prompts: `morning_desk_briefing`, `authority_export_checklist(period)`.

## Scopes

| Scope | Roles | Grants |
|---|---|---|
| `mcp` | all | may call the server at all (`required_scopes`) |
| `desk:read` | receptionist, group_manager, admin | registration reads, minor alerts, `hostel://registration/*` |
| `desk:write` | receptionist, group_manager, admin | approve, return |
| `groups:read` / `groups:write` | group_manager, admin | group reads / create and resend invites |
| `export` | receptionist, group_manager, admin | authority export (+ `pii:read`) |
| `pii:read` | receptionist, group_manager, admin by default; revocable per client | unmasked document numbers, dates of birth, addresses, signatures, leader contacts |
| `admin:read` | admin | month summary, audit log |
| `admin:write` | admin | edit approved |
| `admin:erase` | admin | erase, purge |

Without `pii:read`: `passport_no`/`visa_no`/`car_reg` become `•••` + last two characters, `dob`
becomes `age_band` (`<15`, `15-17`, `18+`), the address shrinks to `addr_country`, signatures,
signer names, leader contacts and drafts are absent. The full table is `redact.FIELD_POLICY` and
the resource `hostel://policy/roles`.

## Design decisions

- **Import the app, do not drive it over HTTP.** The app has no token-authenticated API; its
  service layer is clean. The server imports `app.repo/groups/state/export` from a pinned
  submodule and shares `DATABASE_PATH`. What the HTTP layer did (state checks, APPROVED-only edit,
  the purge guard, the future-group rule) is re-implemented explicitly in `ops.py` and tested.
- **`ops.py` has no MCP in it.** `server.py` is a thin adapter: scope check, call, redact, model.
- **`ToolError` for what the model can fix**, and its message names the tool to call next
  (unknown token → the list tool; wrong state → the allowed actions; missing scope → the scope).
  Anything else stays a crash.
- **Every tool returns a Pydantic model.** `structured_content` matches a declared output schema;
  the two MRTR tools return `Result | Cancelled | InputRequiredResult`, which the SDK wraps as
  `{"result": …}`.
- **One redaction table, fail-closed.** Unknown columns are dropped; a test walks `sqlite_master`
  so the vendored app cannot add a column that ships unclassified.
- **Return, do not call back.** Protocol 2026-07-28 retired server-initiated elicitation; the
  destructive tools return `InputRequiredResult` with a sealed `requestState` (AES-GCM, bound to
  tool, arguments, principal, TTL by the SDK) and act only on `accept` + `confirm: true`. Hosts
  without form elicitation are told to use the admin panel.
- **The purge guard runs before the question.** A human only ever confirms a purge that is
  allowed, and the question quotes the entry count and the snapshot checksum.
- **Verify tokens, never mint them.** `JwtVerifier` + `AuthSettings(required_scopes=["mcp"],
  validate_token_resource=True)`; Keycloak is the demo AS, its realm generated from `guards.py`.
- **Synthetic by default.** `fixtures/synthetic.db` is built through the app's own data layer with
  a seeded RNG and a synthetic clock; names are syllable combinations, documents `X` + 7 digits,
  emails `@example.invalid`. `make fixtures` rebuilds it; `fixtures/synthetic.json` lists the
  planted tokens.

## Repository

```
hostel_ops_mcp/   server.py guards.py redact.py confirm.py ops.py auth.py policy.py models.py synth.py _app.py
vendor/           hostel-checkin-system (submodule, never modified)
fixtures/         synthetic.db synthetic.json
scripts/          make_fixtures.py make_realm.py dev_token.py capture_use_cases.py
keycloak/         realm-hostel.json      docker-compose.yml
tests/            13 files, 130 tests
docs/             architecture.md use-cases.md auth.md live-attach.md superpowers/{specs,plans}
evals/            hostel-ops.xml (10 read-only QA pairs, answers computed from the fixture)
```

- [docs/use-cases.md](docs/use-cases.md): eight narrated scenarios with the real calls and outputs.
- [docs/architecture.md](docs/architecture.md): module map, one request end to end, the test map.
- [docs/auth.md](docs/auth.md): resource server vs authorization server, the Keycloak realm, the Inspector flow.
- [docs/live-attach.md](docs/live-attach.md): what attaching to the real hostel involves (the owner's decision).
- [docs/superpowers/plans/2026-10-11-hostel-ops-mcp-notes.md](docs/superpowers/plans/2026-10-11-hostel-ops-mcp-notes.md): where the implementation deviates from the plan, and why.

## Limitations

- **SSE wake.** `app.events.notify()` is in-process; an approval made through MCP does not wake a
  guest's waiting screen. The guest page falls back to `/status/{token}` polling, which the app
  already does.
- **One machine.** SQLite on one volume means the MCP process must run next to the app
  (`docs/live-attach.md`). Horizontal scaling of the server is not a goal.
- **Not deployed.** The spec's Fly.io demo deployment is not part of this repo; the compose file and
  the in-process HTTP tests stand in for it. Keycloak and Docker were not run while building
  this; the realm JSON is generated and documented but untested against a live Keycloak.
- **`scopes_supported`** in the RFC 9728 metadata lists `["mcp"]` (the SDK derives it from
  `required_scopes`); the full scope list lives in `hostel://policy/roles` and the realm.
- **Email** is recorded, not sent, in synthetic mode; live mode uses the app's Gmail settings.

## Related servers in this portfolio

- [`ihkam-eval-mcp`](https://github.com/jalalhussein1982/ihkam-eval-mcp): read-only evaluation harness over a legal RAG (stdio)
- `phase1/siem-mcp` in [`conflict-trends-dashboard`](https://github.com/jalalhussein1982/conflict-trends-dashboard): conflict analytics over PostGIS (stdio)
- `energy_platform/mcp` in [`energy-platform`](https://github.com/jalalhussein1982/energy-platform): the contributor path of an ingestion platform (stdio)
- `hostel-ops-mcp` (this), `geoecon-pipeline-mcp`, `coin-wargame-mcp`: remote transport and OAuth, long-running jobs, MCP as an agent harness
