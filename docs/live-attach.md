# Attaching to the real hostel

This repo runs against a synthetic database by default. Pointing it at the production hostel is
the owner's decision; this page is what that decision involves. Nothing here is required for
the portfolio demo.

## The constraint

The app stores everything in one SQLite file on one volume (`DATABASE_PATH`, WAL mode). The MCP
server imports the app's data layer and opens the same file, so **it must run on the same
machine as the app**. On Fly that is a second process in the app's `fly.toml`, not a second app:

```toml
[processes]
  web = "uvicorn app.main:app --host 0.0.0.0 --port 8080"
  mcp = "python -m hostel_ops_mcp.server --http --host 0.0.0.0 --port 8000"

[[services]]            # existing web service, unchanged
  processes = ["web"]
  internal_port = 8080
  ...

[[services]]            # new: the MCP endpoint
  processes = ["mcp"]
  internal_port = 8000
  protocol = "tcp"
  [[services.ports]]
    port = 443
    handlers = ["tls", "http"]
```

Both processes mount the same volume at `/data`. SQLite's WAL handles the two writers (the app's
request threads and this server) the same way it handles the app's own concurrency; every call
opens a fresh connection and commits, as the app does. Keep exactly one machine for the app, as
the app itself requires (`events.py`).

The image needs this package and its dependencies on top of the app's: install from the repo
(`pip install .` with the submodule checked out), or vendor `hostel_ops_mcp/` into the app image
and add `mcp[cli]`, `pydantic`, `PyJWT[crypto]` to its requirements.

## Environment

| Variable | Live value |
|---|---|
| `HOSTEL_MODE` | `live` (emails go through the app's `group_email`; synthetic mode only records them) |
| `HOSTEL_DATABASE_PATH` | the app's `DATABASE_PATH`, e.g. `/data/demohostel.db` |
| `BACKUP_ENCRYPTION_KEY`, `BACKUP_DIR` | identical to the app's; `purge_month` decrypts and re-verifies the app's snapshots |
| `PUBLIC_BASE_URL` | the app's public origin; portal links in `create_group_link` are built from it |
| `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD` | the app's; read by `app.config` at import |
| `OAUTH_ISSUER_URL`, `OAUTH_JWKS_URL`, `RESOURCE_SERVER_URL` | the hostel's identity provider and this endpoint's public URL (`https://…/mcp`) |
| `REQUEST_STATE_KEYS` | one or more 32+ byte secrets; required if more than one replica or to survive restarts mid-confirmation |
| `EXPORT_DIR`, `EXPORT_TTL_MINUTES` | a directory on the volume; exports are swept after the TTL |
| `STDIO_SCOPES` | leave unset; irrelevant over HTTP |

Never set `STDIO_SCOPES` with write scopes on a live machine; stdio has no authentication.

## Before the first live request

1. **Schema check**: run `redact.classify_all_columns()` against the live file (one line in a
   Python shell). If it returns anything, the app has a column this server has not classified;
   classify it in `redact.py` before serving. The fail-closed default drops unknown columns, but
   decide deliberately.
2. **Audit actor**: rows this server writes carry `actor = mcp:<token subject>`. The admin panel's
   audit view shows them; tell the operator what the prefix means.
3. **Scopes**: give receptionist tokens `pii:read` only if the desk agent needs document numbers.
   An agent writing the morning briefing does not.
4. **Backups**: the purge guard requires the `.enc` snapshot on the same volume. If backups are
   downloaded and removed, `purge_month` will refuse with `missing_snapshot`, by design.

## What changes for the app

Nothing in its code. Two observable effects:

- `app.events.notify()` is in-process, so an approval made through MCP does not wake a guest's
  SSE "waiting" screen; the guest page falls back to `/status/{token}` polling, which the app
  already does (spec §2).
- New actors in `admin_audit`.

## Rolling back

Stop the `mcp` process. The database is the app's; nothing this server created outlives it except
audit rows and export files under `EXPORT_DIR`.
