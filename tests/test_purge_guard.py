"""Task 8 / spec §4.4, §8.6: purge_month's guard runs before any question is asked."""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from mcp.types import ElicitResult, InputRequiredResult

from hostel_ops_mcp import ops

ADMIN = {"admin:erase", "admin:read"}
PERIOD = "2026-08"


async def _never(*a, **k):
    raise AssertionError("auto-driver must not run")


def _out(result) -> dict:
    sc = result.structured_content
    return sc["result"] if set(sc) == {"result"} else sc


@pytest.fixture
def backup(app, monkeypatch, tmp_path: Path):
    """A real encrypted, verified snapshot of 2026-08 under the app's backup dir."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", key)
    backup_dir = Path(app.config.settings.backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    snap = tmp_path / f"{PERIOD}.db"
    count, sha = app.backup.build_snapshot(app.config.settings.database_path, PERIOD, str(snap))
    app.backup.encrypt_file(str(snap), str(backup_dir / f"{PERIOD}.db.enc"), key)
    app.repo.backup_log_upsert(PERIOD, count, f"{PERIOD}.db.enc", sha, "2026-09-01T06:05:00Z", verified=1)
    return {"key": key, "dir": backup_dir, "count": count, "sha": sha}


async def _first_leg(client_with_scopes, period=PERIOD):
    async with client_with_scopes(ADMIN, elicitation_callback=_never) as c:
        return await c.session.call_tool("purge_month", {"period": period}, allow_input_required=True)


def _refused(res, reason: str):
    assert not isinstance(res, InputRequiredResult), "the guard must refuse before any elicitation"
    assert res.is_error and reason in res.content[0].text, res.content[0].text


@pytest.mark.anyio
async def test_bad_period_format(client_with_scopes):
    _refused(await _first_leg(client_with_scopes, "August 2026"), "YYYY-MM")


@pytest.mark.anyio
async def test_backup_not_configured(client_with_scopes, app, monkeypatch):
    monkeypatch.delenv("BACKUP_ENCRYPTION_KEY", raising=False)
    monkeypatch.setattr(app.config.settings, "backup_encryption_key", "")
    _refused(await _first_leg(client_with_scopes), "backup is not configured")


@pytest.mark.anyio
async def test_no_verified_backup(client_with_scopes, backup):
    _refused(await _first_leg(client_with_scopes, "2026-09"), "no verified backup")


@pytest.mark.anyio
async def test_nothing_to_purge_even_with_a_backup_row(client_with_scopes, app, backup):
    """Review Focus 3: a period with a backup row but zero approved rows is refused with 'nothing to purge'."""
    app.repo.backup_log_upsert("2026-07", 12, "2026-07.db.enc", "0" * 64, "2026-08-01T06:00:00Z", verified=1)
    _refused(await _first_leg(client_with_scopes, "2026-07"), "nothing to purge")


@pytest.mark.anyio
async def test_count_mismatch(client_with_scopes, app, backup):
    app.repo.backup_log_upsert(PERIOD, backup["count"] - 1, f"{PERIOD}.db.enc", backup["sha"], "2026-09-01T06:05:00Z", verified=1)
    _refused(await _first_leg(client_with_scopes), "count")


@pytest.mark.anyio
async def test_missing_snapshot(client_with_scopes, backup):
    (backup["dir"] / f"{PERIOD}.db.enc").unlink()
    _refused(await _first_leg(client_with_scopes), "snapshot file")


@pytest.mark.anyio
async def test_decrypt_failed_with_another_key(client_with_scopes, backup, monkeypatch):
    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", Fernet.generate_key().decode())
    _refused(await _first_leg(client_with_scopes), "decrypt")


@pytest.mark.anyio
async def test_verify_failed_on_a_tampered_checksum(client_with_scopes, app, backup):
    app.repo.backup_log_upsert(PERIOD, backup["count"], f"{PERIOD}.db.enc", "f" * 64, "2026-09-01T06:05:00Z", verified=1)
    _refused(await _first_leg(client_with_scopes), "verif")


@pytest.mark.anyio
async def test_passing_guard_asks_with_count_and_sha(client_with_scopes, app, backup):
    res = await _first_leg(client_with_scopes)
    assert isinstance(res, InputRequiredResult)
    msg = res.input_requests["confirm"].params.message
    assert str(backup["count"]) in msg and backup["sha"][:12] in msg and PERIOD in msg
    assert app.repo.count_approved_for_period(PERIOD) == backup["count"]


@pytest.mark.anyio
async def test_accept_purges_the_month_atomically(client_with_scopes, app, backup):
    async with client_with_scopes(ADMIN, elicitation_callback=_never) as c:
        first = await c.session.call_tool("purge_month", {"period": PERIOD}, allow_input_required=True)
        done = await c.session.call_tool(
            "purge_month", {"period": PERIOD},
            input_responses={"confirm": ElicitResult(action="accept", content={"confirm": True})},
            request_state=first.request_state, allow_input_required=True,
        )
    out = _out(done)
    assert out["deleted"] == backup["count"] == 15 and out["snapshot_sha256"] == backup["sha"]
    assert app.repo.count_approved_for_period(PERIOD) == 0
    assert app.repo.backup_log_get(PERIOD)["deleted_at"] is not None
    # other months untouched
    assert app.repo.count_approved_for_period("2026-09") == 15
    rows = [a for a in app.repo.audit_recent(50) if a["action"] == "delete" and a["actor"] == "mcp:stdio"]
    assert len(rows) == 1 and rows[0]["period"] == PERIOD and "15" in rows[0]["detail"]


@pytest.mark.anyio
async def test_decline_purges_nothing(client_with_scopes, app, backup):
    async with client_with_scopes(ADMIN, elicitation_callback=_never) as c:
        first = await c.session.call_tool("purge_month", {"period": PERIOD}, allow_input_required=True)
        res = await c.session.call_tool(
            "purge_month", {"period": PERIOD},
            input_responses={"confirm": ElicitResult(action="cancel")},
            request_state=first.request_state, allow_input_required=True,
        )
    assert _out(res)["cancelled"] is True and _out(res)["reason"] == "cancelled"
    assert app.repo.count_approved_for_period(PERIOD) == 15


@pytest.mark.anyio
async def test_count_changed_inside_the_transaction_is_a_tool_error(client_with_scopes, app, backup, monkeypatch):
    def boom(period, expected_count):
        raise app.repo.CountChanged(f"live=14 expected={expected_count}")

    monkeypatch.setattr(app.repo, "delete_period", boom)
    async with client_with_scopes(ADMIN, elicitation_callback=_never) as c:
        first = await c.session.call_tool("purge_month", {"period": PERIOD}, allow_input_required=True)
        res = await c.session.call_tool(
            "purge_month", {"period": PERIOD},
            input_responses={"confirm": ElicitResult(action="accept", content={"confirm": True})},
            request_state=first.request_state, allow_input_required=True,
        )
    assert res.is_error and "count" in res.content[0].text.lower()
    assert app.repo.count_approved_for_period(PERIOD) == 15


def test_guard_report_order_is_the_apps_delete_guard(app, backup):
    rep = ops.purge_guard(PERIOD)
    assert rep.ok and rep.reason is None and rep.entry_count == 15 and rep.snapshot_sha256 == backup["sha"]
    assert ops.purge_guard("2026-09").reason == "no_verified_backup"
