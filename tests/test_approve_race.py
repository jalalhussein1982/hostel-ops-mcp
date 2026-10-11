"""Finding 9: two concurrent approvals of the same registration must produce one approval,
one `already_approved`, and exactly one audit row."""

from __future__ import annotations

import threading

from hostel_ops_mcp import ops


def test_concurrent_approvals_audit_once(app, planted):
    token = planted["tokens"]["PENDING_REVIEW"][12]
    results: list[dict] = []
    gate = threading.Barrier(4)

    def go():
        gate.wait()
        results.append(ops.approve(token, "mcp:test"))

    threads = [threading.Thread(target=go) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 4
    assert sum(1 for r in results if not r["already_approved"]) == 1
    assert len({r["serial"] for r in results}) == 1
    rows = [a for a in app.repo.audit_recent(50) if a["action"] == "approve" and a["actor"] == "mcp:test"]
    assert len(rows) == 1


def test_approval_made_elsewhere_between_check_and_act_is_reported_as_already_approved(app, planted, monkeypatch):
    """The desk approves the same card in another process while our check has already passed.
    Detectable when its approved_at precedes ours; within the same second the app's whole-second
    timestamps make the two indistinguishable (documented limitation)."""
    token = planted["tokens"]["PENDING_REVIEW"][13]
    real_check = ops._check_transition

    def desk_wins(rec, action):
        real_check(rec, action)
        app.repo.approve(token)           # the other process got there first, a moment ago
        with app.db.get_conn() as conn:   # the app stamps whole seconds; make the desk's approval visibly earlier
            conn.execute("UPDATE guest_registration SET approved_at='2026-10-11T00:00:00Z' WHERE token=?", (token,))

    monkeypatch.setattr(ops, "_check_transition", desk_wins)
    r = ops.approve(token, "mcp:test")
    assert r["already_approved"] is True and r["serial"]
    assert not [a for a in app.repo.audit_recent(50) if a["action"] == "approve" and a["actor"] == "mcp:test"]
