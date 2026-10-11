# Implementation notes: where the build deviates from the plan

Companion to `2026-10-11-hostel-ops-mcp.md`. Each entry: what the plan said, what was done, why.
SDK installed: `mcp 2.3.0` (plan: `>=2.2`), protocol 2026-07-28.

## SDK-driven

1. **Union return types are wrapped.** Plan: `erase_registration` returns `EraseResult` or
   `Cancelled`. Done: the function is annotated `EraseResult | Cancelled | InputRequiredResult`
   (the SDK requires the `InputRequiredResult` arm declared for the manual MRTR form). The SDK
   strips that arm for schema derivation and, for a multi-arm union, emits
   `structured_content = {"result": {...}}` with a `oneOf` schema. Tests and docs use that shape.
   Same for `purge_month`.
2. **`export://{export_id}` became two templates.** Plan/spec: one template serving XLSX or PDF
   bytes "with the right MIME type". In the SDK a resource template's `mime_type` is fixed at
   registration, so there are `export://xlsx/{export_id}` and `export://pdf/{export_id}`; the
   `ExportResult.resource_uri` tells the host which to read.
3. **Token without `mcp` → 403, not 401.** Plan: 401. The SDK's `RequireAuthMiddleware` answers a
   valid token lacking a required scope with `403 insufficient_scope` (RFC 6750 §3.1) and keeps
   401 for missing/invalid tokens. The test asserts what the SDK emits.
4. **`scopes_supported` lists only `mcp`.** The SDK fills RFC 9728 metadata from
   `AuthSettings.required_scopes`. The full scope list is in `hostel://policy/roles`, the README
   and the Keycloak realm. Documented as a limitation.
5. **Per-resource `ttlMs`/`cacheScope` via middleware.** The SDK offers `cache_hints` per method
   only; a small `cache_directives` middleware marks `hostel://policy/*` reads public for an hour,
   everything else keeps the SDK default (0, private). `tests/test_resources.py` asserts both.
6. **Client capabilities.** `ctx.client_capabilities.elicitation.form` is the check for "host can
   confirm"; the SDK `Client` declares the capability only when an `elicitation_callback` is
   given. MRTR tests drive the loop manually with `client.session.call_tool(...,
   allow_input_required=True)` and `input_responses=` / `request_state=`, as the SDK docs show.
7. **In-process tests go through the real middleware.** The sealed `requestState` and its
   `-32602` rejection are exercised in-memory (`Client(mcp)`), which was not certain from the docs
   ("dispatches directly without JSON-RPC framing"). Confirmed by `tests/test_mrtr.py`.

## Design choices that differ from the plan's wording

8. **`guards.require(ctx, tool, *scopes)`** takes the tool name first and defaults the scopes to
   `TOOL_SCOPES[tool]`, so the scope table is the single source for the handler, the matrix test,
   the policy resource, the README and the Keycloak realm (`scripts/make_realm.py`).
9. **`create_group_link` has no `expected_members`.** The app's `validate_create` forces it to 1
   (the leader builds the list on the portal). `purpose_of_stay` is applied with
   `groups.set_purpose` after `groups.create`, as the app's own form flow does.
10. **Synthetic data shape.** Plan: "60 registrations … 30 APPROVED across 2026-08, 2026-09".
    Done: exactly that for individual rows (10/15/5/30), plus 11 APPROVED group-member rows whose
    groups arrive in 2026-10 and 2026-11 so the per-month counts hold and the desk's
    future-group rule has something to hide. The approved under-15 with a legal representative is
    an August guest, so `desk_minor_alerts` (pending + approved today) lists the two pending
    minors only, as the plan's test expects. The school choir's members are minors by design.
11. **Duplicates on both group paths.** Plan: "two SUBMITTED (one with 2 duplicates in
    `panel_state`)". After submit the app deletes the staging rows, so `panel_state` cannot show
    duplicates for a SUBMITTED group. Done: the PENDING group has a duplicate pair of staging rows
    (`panel_state.duplicates`, ids) and a SUBMITTED group has the same person injected twice
    (`ops.duplicate_tokens`, same (first, last, dob) rule, keyed by token).
12. **A DRAFT with partial wizard fields.** The real app saves a draft only in the browser, so
    DRAFT rows are empty. The fixture plants partial fields on one DRAFT row by direct SQL so
    Review Focus 2 (no wizard PII beyond the policy) is actually exercised.
13. **`age_band` is measured against today** (as the plan says); the `under18` flag is measured
    against the arrival date (the desk's convention). Both are documented on the model.
14. **Protocol test.** Rather than `xfail` marks removed task by task, the order test asserts the
    registered tools appear in spec relative order, and a second test asserts the full list; the
    second carried a strict `xfail` from Task 4 to Task 9 and was then un-marked.
15. **`tests/` is a package** (`tests/__init__.py`) so `from tests.conftest import …` works under
    `uv run pytest` regardless of rootdir handling. `client_with_scopes` is a factory fixture
    (an async context manager) because scopes are read from the process environment per call;
    two clients with different scopes cannot be open at the same time in one test.
16. **`skipped_detail`** was added to `ExportResult` (`token: STATE`) next to the plan's `skipped[]`.
17. **Realm generated, not hand-written.** `scripts/make_realm.py` builds
    `keycloak/realm-hostel.json` from `guards.py`. Role → scope mapping uses Keycloak role
    scope-mappings on client scopes with *Full scope allowed* off. Keycloak does not support
    Client ID Metadata Documents, so the Inspector uses the pre-registered public PKCE client
    `hostel-ops-inspector`; dynamic registration is off, as the spec requires.
18. **`scripts/capture_use_cases.py`** reproduces `docs/use-cases.md` from a scratch copy of the
    fixture, so the documented outputs are real and regenerable.
19. **`--http` runs stateless by default** (`--stateful` to keep sessions). MRTR state lives in the
    sealed `requestState`, so stateless is enough and matches spec §1.
20. **`dev_token.py`** writes the JWKS to `fixtures/dev-jwks.json` (git-ignored, regenerated per
    key) and the private key to `.dev/` (git-ignored).

## Not done / not verifiable here

- Docker and Keycloak were not run in this environment. `docker-compose.yml` and the realm are
  written and documented; the realm import and the Inspector OAuth flow against a live Keycloak
  are untested.
- The spec's Fly.io demo deployment (§10) is not part of the plan's tasks and was not done;
  `docs/live-attach.md` covers the owner's path.
- Live mode (`HOSTEL_MODE=live`, SMTP through the app's `group_email`) is wired but exercised only
  through the synthetic no-op recorder in tests.
