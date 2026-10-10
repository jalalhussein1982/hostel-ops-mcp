# hostel-ops-mcp Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An MCP server (protocol 2026-07-28, Python SDK 2.x) over the hostel check-in system: remote Streamable HTTP with OAuth 2.1 scopes, safe write tools, MRTR-confirmed erasure, PII redaction by scope, synthetic data by default.

**Architecture:** `ops.py` holds plain-Python operations over the hostel app's service layer (`app.repo`, `app.groups`, `app.state`, `app.export`) imported from a pinned git submodule. `server.py` is a thin `MCPServer` adapter: tools in fixed order, Pydantic output models, `ToolError` for model-fixable errors. `guards.py` + `redact.py` enforce scopes and the field policy; `confirm.py` implements the MRTR confirmation; `auth.py` verifies JWTs.

**Tech Stack:** Python 3.12, `uv`, `mcp[cli]>=2.2`, `pydantic`, `PyJWT[crypto]`, `httpx`, `pytest`, `anyio`. Hostel app pinned as a git submodule at `vendor/hostel-checkin-system` (it has no `pyproject.toml`, so it cannot be a pip git dependency).

**Spec:** `docs/superpowers/specs/2026-10-11-hostel-ops-mcp-design.md`

## Global Constraints

- Protocol revision 2026-07-28; `mcp>=2.2.0`; Python `>=3.12`.
- Never modify files under `vendor/hostel-checkin-system`. The server adapts; it does not patch.
- Tools register in the exact order of spec §4; a test pins the order.
- Every tool returns a Pydantic model (output schema). Primitive returns are not allowed.
- `ToolError` only for errors the model can fix; its message names the tool to call next.
- Without `pii:read`, no tool or resource ever returns `passport_no`, `visa_no`, `car_reg` unmasked, `dob`, street address, signature bytes, `signer_name`, leader contact fields, or `draft_json`.
- Destructive tools (`erase_registration`, `purge_month`) act only after a validated `accept` with `confirm=True` carried in `ctx.input_responses`.
- The synthetic DB contains no real person; names come from a fixed-seed generator.
- Commit after every task with a conventional message and the Co-Authored-By trailer from the session.

## Review Focus

1. A token with `desk:write` but without `desk:read` calls `approve_registration`: it must work (write does not imply read) and the result must not leak PII beyond the card fields. Test in Task 6.
2. `get_registration` on a DRAFT token (guest still in the wizard): return the card with `state=DRAFT`, never an error, and never partial wizard fields beyond the policy. Test in Task 4.
3. `purge_month` for a period with zero approved rows but a backup row: guard must refuse with "nothing to purge" before any elicitation. Test in Task 8.
4. `export_authority_report` with a mix of APPROVED and PENDING tokens: PENDING are skipped and listed in `skipped[]`, never exported. Test in Task 9.
5. A JWT whose `scope` claim is an array rather than a space-separated string (some IdPs do that): the verifier must accept both forms. Test in Task 10.

---

## File structure

```
hostel-ops-mcp/
  pyproject.toml, .python-version, Makefile, .mcp.json, docker-compose.yml, keycloak/realm-hostel.json
  vendor/hostel-checkin-system/      git submodule, pinned
  hostel_ops_mcp/
    __init__.py
    _app.py        sys.path shim: makes `app` importable from the submodule; sets DATABASE_PATH env before import
    models.py      Pydantic output models (RegistrationCard, Registration, Page, GroupCard, GroupDetail, MinorAlert, MonthRow, AuditRow, ApprovalResult, GroupLink, ExportResult, EditResult, EraseResult, PurgeResult, Cancelled)
    redact.py      FIELD_POLICY table + apply(record, scopes) + classify_all_columns()
    ops.py         plain functions over app.repo/app.groups/app.export; no MCP imports
    guards.py      Scopes dataclass, require(ctx, *scopes), current_scopes(ctx), STDIO_SCOPES
    confirm.py     confirm_or_request(ctx, key, message, state) -> ConfirmOutcome
    auth.py        JwtVerifier(TokenVerifier) with JWKS cache
    synth.py       build_synthetic_db(path, seed=1982)
    server.py      MCPServer wiring: tools (fixed order), resources, prompts, main()
  fixtures/synthetic.db
  scripts/make_fixtures.py, scripts/dev_token.py
  tests/conftest.py, test_protocol.py, test_redact.py, test_read_tools.py, test_scopes.py,
        test_writes.py, test_mrtr.py, test_purge_guard.py, test_export.py, test_auth.py, test_http.py
  evals/hostel-ops.xml
  docs/architecture.md, docs/use-cases.md, docs/live-attach.md, docs/auth.md
```

---

### Task 1: Project skeleton, submodule, app shim

**Files:** Create `pyproject.toml`, `.python-version`, `Makefile`, `hostel_ops_mcp/__init__.py`, `hostel_ops_mcp/_app.py`, `tests/conftest.py`, `tests/test_shim.py`. Add submodule `vendor/hostel-checkin-system` at the current `main` commit of `https://github.com/jalalhussein1982/hostel-checkin-system`.

**Interfaces produced:** `hostel_ops_mcp._app.load(database_path: Path) -> types.ModuleType` returns the imported `app` package after setting `DATABASE_PATH`, `SESSION_SECRET`, `COOKIE_SECURE=false`, `BACKUP_DIR` env and inserting the submodule root on `sys.path`; calls `app.db.init_db()` once.

- [ ] `git submodule add https://github.com/jalalhussein1982/hostel-checkin-system vendor/hostel-checkin-system`; record the commit in README.
- [ ] `pyproject.toml` modelled on `ihkam-eval-mcp` (hatchling, `mcp[cli]>=2.2.0`, `pydantic>=2`, `PyJWT[crypto]>=2.9`, `httpx`; dev: `pytest`, `anyio`, `pytest-asyncio`), plus the hostel app's runtime deps copied from `vendor/hostel-checkin-system/requirements.txt` (fastapi, jinja2, passlib[argon2], itsdangerous, cryptography, openpyxl, fpdf2, segno, httpx) so `app.*` imports resolve.
- [ ] Failing test `tests/test_shim.py::test_app_imports_and_inits_db(tmp_path)`: `app = load(tmp_path/"t.db")`; assert `app.repo.list_pending() == []` and the file exists.
- [ ] Implement `_app.py`; run `uv run pytest -q`; commit `chore: skeleton, submodule, app shim`.

### Task 2: Synthetic database

**Files:** Create `hostel_ops_mcp/synth.py`, `scripts/make_fixtures.py`, `fixtures/synthetic.db`, `tests/test_synth.py`.

**Interfaces produced:** `synth.build_synthetic_db(path: Path, seed: int = 1982) -> SynthSummary` (dataclass: counts per state, group slugs, minor tokens, periods). Uses `app.repo.create_registration`, `save_submission_with_signature` (a tiny valid PNG), `approve`, `set_returned`, `app.groups.create/add_member/submit_panel`, `repo.backup_log_upsert`, `repo.audit`.

Content: 60 registrations (10 DRAFT, 15 PENDING_REVIEW, 5 RETURNED, 30 APPROVED across two months `2026-08`, `2026-09`), one under-18 pending, one under-15 approved with `signer_role=legal_representative`, one under-15 pending with `signer_role=self`; groups: one PENDING, two SUBMITTED (one with 2 duplicates in `panel_state`); `backup_log` row verified for `2026-08`, none for `2026-09`; 5 audit rows. Names from a fixed word list, documents as `X` + 7 digits from the seeded RNG.

- [ ] Failing tests: counts per state equal the spec; `is_under_18` true for exactly the planted tokens; determinism (two builds, same `SELECT token, last_name ORDER BY token` digest).
- [ ] Implement; `make fixtures` writes `fixtures/synthetic.db`; commit `feat: synthetic hostel database`.

### Task 3: Field policy and redaction

**Files:** Create `hostel_ops_mcp/redact.py`, `tests/test_redact.py`.

**Interfaces produced:**
```python
PII_SCOPE = "pii:read"
class Treatment(Enum): PASS, MASK_DOC, AGE_BAND, COUNTRY_ONLY, DROP, PII_ONLY
FIELD_POLICY: dict[str, Treatment]   # keyed "table.column" for every column in guest_registration, guest_signature, group_request, group_member
def apply(record: dict, table: str, scopes: set[str]) -> dict
def classify_all_columns(conn) -> list[str]   # columns present in sqlite_master but missing from FIELD_POLICY
```
Masking: `MASK_DOC` → `"•••" + value[-2:]`; `AGE_BAND` → `dob` replaced by `age_band` computed against today; `COUNTRY_ONLY` → only `addr_country`.

- [ ] Failing tests: every PII column from spec §6.3 is dropped/masked without the scope and present with it; `classify_all_columns` on the synthetic DB returns `[]` (fails if a column is unclassified); `apply` never raises on missing keys.
- [ ] Implement; commit `feat: field policy and redaction`.

### Task 4: Read operations and read tools

**Files:** Create `hostel_ops_mcp/models.py`, `hostel_ops_mcp/ops.py` (read half), `hostel_ops_mcp/guards.py`, `hostel_ops_mcp/server.py` (first version), `tests/test_protocol.py`, `tests/test_read_tools.py`.

**Interfaces produced (ops.py):**
```python
def list_pending(limit: int, cursor: str | None) -> tuple[list[dict], str | None]
def list_approved(arrival_from, arrival_to, group_id, limit, cursor) -> tuple[list[dict], str | None]
def get_registration(token: str, include_signature: bool) -> dict | None
def minor_alerts() -> list[dict]
def list_groups(status: Literal["pending","submitted","all"]) -> list[dict]
def get_group(slug: str) -> dict | None
def month_summary() -> list[dict]
def audit_recent(limit: int) -> list[dict]
```
Cursor = base64 of the last `token`; stable ordering by `created_at, token`.

**guards.py:** `current_scopes(ctx) -> set[str]` reads `get_access_token()`; when `None` (stdio/in-memory) returns `STDIO_SCOPES` from env (default `desk:read,groups:read,admin:read`). `require(ctx, *scopes)` raises `ToolError(f"missing scope {s}; this token cannot call {tool}")`.

**server.py:** `MCPServer("hostel-ops", instructions=...)`; tools registered in spec order with `ToolAnnotations(read_only_hint=True, open_world_hint=False)`; each tool: `require`, call ops, `redact.apply`, return model.

- [ ] Failing `test_protocol.py`: tool names in exact spec order (`list_pending_registrations, list_approved_registrations, get_registration, desk_minor_alerts, list_groups, get_group, month_summary, audit_log, approve_registration, return_registration, create_group_link, resend_group_invite, export_authority_report, edit_approved_registration, erase_registration, purge_month`); every tool has `outputSchema`; read tools have `readOnlyHint=True`. (Later tasks add tools; keep the list complete now and mark not-yet-registered names `xfail` until Task 6/7/8/9 remove the marks.)
- [ ] Failing `test_read_tools.py`: pending count 15; cursor pagination with `limit=4` walks all 15 once; `desk_minor_alerts` returns the two planted minors with `needs_legal_representative` on the right one; `get_registration` on a DRAFT token returns `state="DRAFT"` and no wizard PII (Review Focus 2); `get_group` on the duplicate group reports `duplicates`.
- [ ] Implement; commit `feat: read tools over the synthetic hostel database`.

### Task 5: Scope matrix

**Files:** Create `tests/test_scopes.py`; modify `tests/conftest.py` to provide `client_with_scopes(scopes: set[str])` by setting `STDIO_SCOPES`.

- [ ] Parametrised test over every (tool, scope-set) pair from spec §6.2 for the tools existing so far: allowed sets succeed, each missing scope raises `ToolError` whose message contains the scope name. Extend this file in Tasks 6–9 as tools appear.
- [ ] Commit `test: scope matrix`.

### Task 6: Non-destructive write tools

**Files:** Modify `ops.py` (write half), `server.py`; create `tests/test_writes.py`.

**Interfaces produced:**
```python
def approve(token, actor) -> dict        # {token, serial, approved_at, already_approved}
def return_registration(token, reason, step, actor) -> dict
def create_group_link(fields: dict, actor, base_url) -> dict   # {slug, portal_url, member_url, portal_password, email_sent}
def resend_group_invite(slug, receiver_email, actor, base_url) -> dict
```
`approve`: load record; if `state == APPROVED` return `already_approved=True`; else `app.state.next_state(State(rec["state"]), Action.APPROVE)` (maps `InvalidTransition` → `ToolError` naming current state and allowed actions) then `app.repo.approve(token)`; `app.repo.audit(actor, "approve", detail=token)`. Email sending is injected (`send_invite: Callable`) and defaults to a no-op recorder in synthetic mode so tests never touch SMTP.

- [ ] Failing tests: approve PENDING → serial `YYYY-MM-DD-NNNN` and audit row; approve twice → `already_approved` and no second audit row; approve DRAFT → `ToolError` mentioning `DRAFT` and `SUBMIT`; return with `step=4` → validation error; `create_group_link` returns password once and `groups.get(slug)` has a hash; token with only `desk:write` can approve and the result contains no PII fields (Review Focus 1).
- [ ] Implement; annotations `destructive_hint=False`, `idempotent_hint=True` for approve; `open_world_hint=True` for the two group tools; commit `feat: approve, return, group link tools`.

### Task 7: MRTR confirmation and `erase_registration`

**Files:** Create `hostel_ops_mcp/confirm.py`, `tests/test_mrtr.py`; modify `server.py`, `ops.py` (`erase(token, actor) -> {deleted, removed_group}`).

**Interfaces produced:**
```python
@dataclass
class ConfirmOutcome: status: Literal["confirmed","declined","ask"]; result: InputRequiredResult | None
def confirm_or_request(ctx: Context, key: str, message: str, state: str, supports_elicitation: bool) -> ConfirmOutcome
```
Behaviour per spec §4.4: reads `ctx.input_responses.get(key)`; `ElicitResult` with `action != "accept"` → `declined`; `accept` with `content["confirm"] is True` → `confirmed`; otherwise → `ask` with `InputRequiredResult(input_requests={key: ElicitRequest(params=ElicitRequestFormParams(message=..., requested_schema={"type":"object","properties":{"confirm":{"type":"boolean","title":"Confirm"}},"required":["confirm"]}))}, request_state=state)`. If the client capabilities carry no `elicitation`, raise `ToolError("this host cannot confirm destructive actions; use the admin panel")`. Read client capabilities from `ctx` (the SDK exposes the per-request client capabilities; find the attribute in `mcp.server.mcpserver.Context` and pin it with a test).

Tool `erase_registration(token, ctx)`: `require(ctx, "admin:erase")`; load record (unknown → `ToolError`); message = `f"Erase registration {serial or token} (arrival {arrival})? Legal basis is Act 326/1999; erasure is limited to records no longer required. This cannot be undone."`; `state=f"erase:{token}"`; on confirmed → `ops.erase`, audit `delete`, return `EraseResult`; on declined → `Cancelled(reason="declined")`.

- [ ] Failing tests using the SDK `Client`: first call returns `InputRequiredResult` with key `confirm` and a `request_state`; retry with `input_responses={"confirm": ElicitResult(action="accept", content={"confirm": True})}` and the echoed state deletes exactly one row and writes one audit row; `decline` deletes nothing; retry with a different `token` argument fails state verification (`-32602`); a client that declares no elicitation capability gets `ToolError`. (Use the client's API for passing `input_responses`/`request_state` on retry; the SDK docs page `handlers/multi-round-trip` shows the server side, `client/...` the client side.)
- [ ] Implement; commit `feat: MRTR-confirmed erase_registration`.

### Task 8: `purge_month` with guard

**Files:** Modify `ops.py` (`purge_guard(period) -> GuardReport`, `purge(period, expected_count, actor)`), `server.py`; create `tests/test_purge_guard.py`.

`GuardReport{ok: bool, reason: str | None, entry_count: int, snapshot_sha256: str | None}`. Checks in order: backup configured (`BACKUP_ENCRYPTION_KEY` set) → `backup_log_get(period)` exists and `verified` → `count_approved_for_period(period) == entry_count` → `entry_count > 0` (Review Focus 3) → `app.backup.verify_snapshot(path, expected_count=..., expected_sha256=...)` passes. The guard runs before `confirm_or_request`; a failing guard returns `ToolError(reason)` and never produces an elicitation.

- [ ] Failing tests: each guard condition failing in isolation produces the matching reason and no `InputRequiredResult`; passing guard → elicitation whose message contains `entry_count` and the sha prefix; accept → `repo.delete_period` called with `expected_count`; `CountChanged` → `ToolError`.
- [ ] Implement; commit `feat: purge_month with pre-elicitation guard`.

### Task 9: Edit, export, and `export://` resource

**Files:** Modify `ops.py`, `server.py`; create `tests/test_export.py`.

`edit_approved(token, fields, actor)`: whitelist fields to the app's wizard fields; `app.repo.update_registration` (`NotEditable` → `ToolError`); audit detail = sorted changed field names only.
`export(tokens | window, fmt, actor) -> ExportResult{resource_uri, rows, skipped, sha256, generated_at}`: select APPROVED rows, `app.export.normalize`, `build_xlsx`/`build_pdf`, write `EXPORT_DIR/<uuid>.<ext>`, sweep files older than `EXPORT_TTL_MINUTES`. Resource template `export://{export_id}` returns the bytes as a blob with the right MIME type; requires `export` and `pii:read`.

- [ ] Failing tests: mixed tokens → PENDING in `skipped` (Review Focus 4); xlsx opens with `openpyxl` and has `rows` data rows and the `export.FIELDS` header; resource read returns the same sha256; without `pii:read` the tool is refused.
- [ ] Implement; commit `feat: edit, authority export, export resource`.

### Task 10: JWT verifier and HTTP mode

**Files:** Create `hostel_ops_mcp/auth.py`, `scripts/dev_token.py`, `tests/test_auth.py`, `tests/test_http.py`; modify `server.py` `main()` to build `MCPServer(..., token_verifier=JwtVerifier(...), auth=AuthSettings(issuer_url=..., resource_server_url=..., required_scopes=["mcp"], validate_token_resource=True))` when `OAUTH_ISSUER_URL` is set, and to accept `--stdio` / `--http`.

`JwtVerifier(jwks_url, issuer, audience, http=httpx.AsyncClient)`: caches JWKS 10 min; `verify_token` returns `AccessToken(token, client_id=claims.get("azp") or claims["client_id"], scopes=parse_scope(claims["scope"]), expires_at=claims["exp"], resource=audience, subject=claims["sub"])` or `None`. `parse_scope` accepts string or list (Review Focus 5). `scripts/dev_token.py` mints an RS256 token with a local key pair and writes `fixtures/dev-jwks.json`.

- [ ] Failing `test_auth.py`: valid token → scopes; expired/wrong aud/wrong iss/unknown kid → `None`; list-form scope accepted.
- [ ] Failing `test_http.py` (ASGI in-process via `httpx.ASGITransport` against the server's Streamable HTTP app): no token → 401 with `WWW-Authenticate` containing `resource_metadata`; token without `mcp` → 401; valid token → `tools/list` succeeds; `/.well-known/oauth-protected-resource/mcp` lists the scopes.
- [ ] Implement; commit `feat: OAuth 2.1 resource server mode`.

### Task 11: Resources, prompts, Keycloak compose

**Files:** Modify `server.py`; create `docker-compose.yml`, `keycloak/realm-hostel.json`, `docs/auth.md`, `tests/test_resources.py`.

Resources per spec §5 (`hostel://policy/roles` generated from the scope table in `guards.py`; `hostel://policy/retention` text from `vendor/.../app/i18n/en.json` `privacy_*` keys; `hostel://registration/{token}`, `hostel://group/{slug}`). Prompts `morning_desk_briefing`, `authority_export_checklist(period)`. Keycloak realm: client `hostel-ops-inspector` (public, PKCE), client scopes for all spec scopes, roles `admin/group_manager/receptionist` with scope mappings, three users with `changeme` passwords.

- [ ] Failing tests: resources list contains the four URIs/templates; policy resource text contains every scope name; prompt `morning_desk_briefing` mentions the three tools it should call.
- [ ] Implement; `docs/auth.md` explains RS vs AS, the realm, and the Inspector flow; commit `feat: resources, prompts, Keycloak demo realm`.

### Task 12: Documentation, use cases, evals

**Files:** Rewrite `README.md`; create `docs/architecture.md`, `docs/use-cases.md`, `docs/live-attach.md`, `evals/hostel-ops.xml`, `.mcp.json`.

`docs/use-cases.md` — six narrated scenarios, each with the host prompt, the tool calls the model makes, the exact structured output, and what the human sees for MRTR: (1) morning desk briefing with minors; (2) approve the queue after a check; (3) return a registration with a reason; (4) create a group link for a school trip; (5) monthly authority export; (6) GDPR erasure request with confirmation; plus (7) the refused purge (guard) and (8) the receptionist token that cannot see passport numbers.
`README.md` — what it proves, 60-second run (stdio, Inspector, HTTP + Keycloak), host configs (Claude Code `.mcp.json`, Claude Desktop JSON), tool table, scope table, design decisions (the same voice as `ihkam-eval-mcp`), test counts, limitations (SSE wake, single machine).
`evals/hostel-ops.xml` — 10 read-only QA pairs with answers computed from the synthetic DB (e.g. "How many pending registrations involve a guest under 18?").

- [ ] Write; run `uv run pytest -q` and paste the real count into README; commit `docs: README, architecture, use cases, evals`.

---

## Self-review notes

Spec coverage: §4.1–4.5 → Tasks 4, 6–9; §5 → Tasks 9, 11; §6 → Tasks 3, 5, 10, 11; §7 → Task 7/10 env; §8 → every task; §9 → Task 2; §10 → Tasks 10–12; §11 → file structure. Type names used consistently: `ToolError`, `InputRequiredResult`, `ElicitRequest`, `ElicitResult`, `AccessToken`, `AuthSettings`, `ToolAnnotations`. Review Focus items pinned to Tasks 6, 4, 8, 9, 10.
