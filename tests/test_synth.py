"""Task 2: the synthetic database has the planned shape and is deterministic."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from hostel_ops_mcp.synth import build_synthetic_db


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    path = tmp_path_factory.mktemp("synth") / "synthetic.db"
    summary = build_synthetic_db(path)
    return path, summary


def _digest(path: Path) -> str:
    con = sqlite3.connect(path)
    rows = con.execute("SELECT token, last_name FROM guest_registration ORDER BY token").fetchall()
    con.close()
    return hashlib.sha256(repr(rows).encode()).hexdigest()


def test_counts_per_state_match_the_plan(built):
    path, summary = built
    assert summary.counts == {"DRAFT": 10, "PENDING_REVIEW": 15, "RETURNED": 5, "APPROVED": 30}
    con = sqlite3.connect(path)
    by_state = dict(con.execute(
        "SELECT state, COUNT(*) FROM guest_registration WHERE group_id IS NULL GROUP BY state"
    ).fetchall())
    assert by_state == summary.counts
    # group members are APPROVED rows tagged with a group_id, on top of the 30 individuals
    members = con.execute("SELECT COUNT(*) FROM guest_registration WHERE group_id IS NOT NULL").fetchone()[0]
    assert members == summary.group_members > 0
    approved_by_month = dict(con.execute(
        "SELECT substr(arrival,1,7), COUNT(*) FROM guest_registration "
        "WHERE state='APPROVED' AND group_id IS NULL GROUP BY 1"
    ).fetchall())
    assert approved_by_month == {"2026-08": 15, "2026-09": 15}
    con.close()


def test_minors_are_exactly_the_planted_tokens(built):
    from app.dates import is_under_18  # importable once the shim has loaded the app

    path, summary = built
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    minors = set()
    for r in con.execute("SELECT token, dob, arrival FROM guest_registration WHERE dob IS NOT NULL AND group_id IS NULL"):
        if is_under_18(date.fromisoformat(r["dob"]), date.fromisoformat(r["arrival"])):
            minors.add(r["token"])
    planted = set(summary.minor_tokens.values())
    assert minors == planted and len(planted) == 3
    # the school choir (a submitted group) is made of minors too; they are group rows, not desk minors
    choir = con.execute("SELECT COUNT(*) FROM guest_registration WHERE group_id=? AND dob >= '2008-01-01'",
                        (summary.groups["submitted_with_duplicates"],)).fetchone()[0]
    assert choir == 4
    sig = {r["token"]: r["signer_role"] for r in con.execute("SELECT token, signer_role FROM guest_signature")}
    assert sig[summary.minor_tokens["approved_under15_legal_rep"]] == "legal_representative"
    assert sig[summary.minor_tokens["pending_under15_self"]] == "self"
    assert sig[summary.minor_tokens["pending_under18"]] == "self"
    con.close()


def test_groups_backup_log_and_audit(built):
    path, summary = built
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    statuses = dict(con.execute("SELECT status, COUNT(*) FROM group_request GROUP BY status").fetchall())
    assert statuses == {"PENDING": 1, "SUBMITTED": 2}
    logs = {r["period"]: r for r in con.execute("SELECT * FROM backup_log")}
    assert set(logs) == {"2026-08"}
    assert logs["2026-08"]["verified"] == 1 and logs["2026-08"]["entry_count"] == 15
    assert con.execute("SELECT COUNT(*) FROM admin_audit").fetchone()[0] == 5
    assert summary.periods == ["2026-08", "2026-09"]
    con.close()


def test_no_real_person_strings_and_deterministic(tmp_path: Path, built):
    path, summary = built
    other = tmp_path / "again.db"
    build_synthetic_db(other)
    assert _digest(path) == _digest(other)
    con = sqlite3.connect(path)
    docs = [r[0] for r in con.execute("SELECT passport_no FROM guest_registration WHERE passport_no IS NOT NULL")]
    assert all(d.startswith("X") and d[1:].isdigit() and len(d) == 8 for d in docs)
    emails = [r[0] for r in con.execute("SELECT receiver_email FROM group_request")]
    assert all(e.endswith("@example.invalid") for e in emails)
    con.close()
