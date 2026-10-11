"""Plain-Python operations over the hostel app's service layer. No MCP in this module.

Every function takes plain arguments and returns dicts shaped like the app's rows (or
raises one of the small exception classes below). ``server.py`` maps those onto scope
checks, redaction and Pydantic models. Keeping MCP out of here means the operations are
testable alone and the adapter stays thin.
"""

from __future__ import annotations

import base64
import binascii
import os
from datetime import date
from pathlib import Path
from typing import Any, Literal

from . import _app, handles

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "fixtures" / "synthetic.db"

GroupStatus = Literal["pending", "submitted", "all"]


class NotFound(Exception):
    """Unknown token or slug. ``hint`` names the list tool the model should call."""

    def __init__(self, what: str, key: str, hint: str) -> None:
        super().__init__(f"no {what} {key!r}; call {hint} to find a valid one")
        self.hint = hint


class BadCursor(Exception):
    pass


def app():
    """The vendored app bound to HOSTEL_DATABASE_PATH (default: the synthetic fixture)."""
    try:
        return _app.current()
    except RuntimeError:
        path = os.environ.get("HOSTEL_DATABASE_PATH") or str(DEFAULT_DB)
        if not Path(path).exists() and Path(path) == DEFAULT_DB:
            from .synth import build_synthetic_db

            build_synthetic_db(DEFAULT_DB, summary_path=DEFAULT_DB.with_suffix(".json"))
        return _app.load(path)


def today() -> date:
    return app().dates.prague_now().date()


# ---------------------------------------------------------------- shaping helpers

def is_under_18(rec: dict[str, Any]) -> bool:
    """Under 18 on the arrival date (the desk's convention); False when either date is missing."""
    dob, arrival = rec.get("dob"), rec.get("arrival")
    if not dob or not arrival:
        return False
    try:
        return app().dates.is_under_18(date.fromisoformat(dob), date.fromisoformat(arrival))
    except (ValueError, TypeError):
        return False


def is_under_15(rec: dict[str, Any]) -> bool:
    dob, arrival = rec.get("dob"), rec.get("arrival")
    if not dob or not arrival:
        return False
    try:
        return app().dates.is_under_15(date.fromisoformat(dob), date.fromisoformat(arrival))
    except (ValueError, TypeError):
        return False


def encode_cursor(token: str) -> str:
    return base64.urlsafe_b64encode(token.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> str:
    try:
        return base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
    except (binascii.Error, UnicodeDecodeError) as e:
        raise BadCursor(f"invalid cursor {cursor!r}; use the next_cursor value from the previous page") from e


def _paginate(rows: list[dict[str, Any]], limit: int, cursor: str | None) -> tuple[list[dict[str, Any]], str | None, int]:
    """Stable order by (created_at, token); the cursor is the last token of the previous page."""
    rows = sorted(rows, key=lambda r: (r.get("created_at") or "", r["token"]))
    start = 0
    if cursor:
        last = decode_cursor(cursor)
        idx = next((i for i, r in enumerate(rows) if r["token"] == last), None)
        if idx is None:
            raise BadCursor("stale cursor: the row it pointed at is gone; restart from the first page")
        start = idx + 1
    page = rows[start:start + limit]
    nxt = encode_cursor(page[-1]["token"]) if page and start + limit < len(rows) else None
    return page, nxt, len(rows)


# ---------------------------------------------------------------- reads

QueueState = Literal["PENDING_REVIEW", "RETURNED", "DRAFT"]


def list_pending(limit: int = 25, cursor: str | None = None,
                 state: QueueState = "PENDING_REVIEW") -> tuple[list[dict[str, Any]], str | None, int]:
    """The desk queue (PENDING_REVIEW, via repo.list_pending) or the other non-approved states,
    oldest first. RETURNED rows are what the briefing checks for guests who never resubmitted."""
    a = app()
    if state == "PENDING_REVIEW":
        rows = a.repo.list_pending()
    else:
        if state not in ("RETURNED", "DRAFT"):
            raise Invalid({"state": "PENDING_REVIEW, RETURNED or DRAFT (use list_approved_registrations for APPROVED)"})
        with a.db.get_conn() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM guest_registration WHERE state=? ORDER BY created_at ASC", (state,))]
    return _paginate(rows, limit, cursor)


def list_approved(arrival_from: str | None = None, arrival_to: str | None = None, group_id: str | None = None,
                  limit: int = 25, cursor: str | None = None) -> tuple[list[dict[str, Any]], str | None, int]:
    """APPROVED rows as the desk's /completed shows them: group rows hidden until their arrival day."""
    rows = app().repo.list_all_approved(visible_groups_until=today().isoformat())
    if arrival_from:
        rows = [r for r in rows if (r.get("arrival") or "") >= arrival_from]
    if arrival_to:
        rows = [r for r in rows if (r.get("arrival") or "") <= arrival_to]
    if group_id:
        rows = [r for r in rows if r.get("group_id") == group_id]
    return _paginate(rows, limit, cursor)


def resolve_token(value: str) -> str:
    """A tool's `token` argument may be a raw token or a `ref`; return the raw token."""
    try:
        return handles.resolve(value)
    except handles.Unknown:
        raise NotFound("registration", value, "list_pending_registrations or list_approved_registrations")


def get_registration(token: str) -> dict[str, Any]:
    token = resolve_token(token)
    rec = app().repo.get_registration(token)
    if rec is None:  # pragma: no cover - resolve_token has just seen it
        raise NotFound("registration", token, "list_pending_registrations or list_approved_registrations")
    return rec


def get_signature(token: str) -> dict[str, Any] | None:
    return app().repo.get_signature(resolve_token(token))


def minor_alerts() -> list[dict[str, Any]]:
    """Under-18 guests in the pending queue or approved for today, with the under-15 signature rule."""
    a = app()
    t = today().isoformat()
    rows = list(a.repo.list_pending())
    rows += [r for r in a.repo.list_all_approved(visible_groups_until=t) if r.get("arrival") == t]
    out = []
    for rec in rows:
        if not is_under_18(rec):
            continue
        sig = a.repo.get_signature(rec["token"])
        role = sig["signer_role"] if sig else None
        out.append({**rec, "signer_role": role,
                    "needs_legal_representative": is_under_15(rec) and role != "legal_representative"})
    return out


def list_groups(status: GroupStatus = "all") -> list[dict[str, Any]]:
    g = app().groups
    rows: list[dict[str, Any]] = []
    if status in ("pending", "all"):
        rows += g.list_pending()
    if status in ("submitted", "all"):
        rows += g.list_submitted()
    for r in rows:
        r["member_count"] = (len(g.members(r["slug"])) if r["status"] == g.SUBMITTED
                             else len(g.member_rows(r["slug"])))
    return rows


def _dupe_key(row: dict[str, Any]) -> tuple[str, str, str] | None:
    first = str(row.get("first_name") or "").strip().casefold()
    last = str(row.get("last_name") or "").strip().casefold()
    dob = str(row.get("dob") or "").strip()
    return (first, last, dob) if first and last and dob else None


def duplicate_tokens(members: list[dict[str, Any]]) -> list[list[str]]:
    """Injected members sharing (first, last, dob) — same rule as the app's panel check, keyed by `ref`."""
    buckets: dict[tuple[str, str, str], list[str]] = {}
    for m in members:
        key = _dupe_key(m)
        if key is not None:
            buckets.setdefault(key, []).append(handles.ref_for(m["token"]))
    return sorted([sorted(v) for v in buckets.values() if len(v) >= 2])


def get_group(slug: str) -> dict[str, Any]:
    """Group header plus either the injected members (SUBMITTED) or the live panel (PENDING)."""
    g = app().groups
    group = g.get(slug)
    if group is None:
        raise NotFound("group", slug, "list_groups")
    if group["status"] == g.SUBMITTED:
        members = g.members(slug)
        return {"group": {**group, "member_count": len(members)}, "members": members, "panel_rows": [],
                "total": len(members), "filled": len(members), "attention_ids": [],
                "duplicates": duplicate_tokens(members)}
    panel = g.panel_state(slug)
    return {"group": {**group, "member_count": panel["total"]}, "members": [], "panel_rows": panel["rows"],
            "total": panel["total"], "filled": panel["filled"], "attention_ids": panel["attention_ids"],
            "duplicates": [[str(i) for i in grp] for grp in panel["duplicates"]]}


def month_summary() -> list[dict[str, Any]]:
    return app().repo.month_summary()


def audit_recent(limit: int = 50) -> list[dict[str, Any]]:
    return app().repo.audit_recent(limit)


# ---------------------------------------------------------------- writes (non-destructive)

class InvalidState(Exception):
    """The registration/group is not in a state that allows this action."""

    def __init__(self, what: str, current: str, allowed: list[str]) -> None:
        allowed_txt = ", ".join(allowed) if allowed else "none"
        super().__init__(f"{what} is {current}; allowed actions from {current}: {allowed_txt}")
        self.current, self.allowed = current, allowed


class Invalid(Exception):
    """Field validation failed. ``errors`` is {field: message}."""

    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors


def _allowed_actions(state_value: str) -> list[str]:
    st = app().state
    try:
        current = st.State(state_value)
    except ValueError:
        return []
    out = []
    for action in st.Action:
        try:
            st.next_state(current, action)
            out.append(action.value)
        except st.InvalidTransition:
            pass
    return out


def _check_transition(rec: dict[str, Any], action_name: str) -> None:
    st = app().state
    try:
        st.next_state(st.State(rec["state"]), st.Action[action_name])
    except st.InvalidTransition:
        raise InvalidState(f"registration {handles.ref_for(rec['token'])}", rec["state"], _allowed_actions(rec["state"]))


def approve(token: str, actor: str) -> dict[str, Any]:
    """PENDING_REVIEW -> APPROVED through the app's state machine; idempotent on APPROVED."""
    a = app()
    rec = get_registration(token)
    token, ref = rec["token"], handles.ref_for(rec["token"])
    if rec["state"] == a.state.State.APPROVED.value:
        return {"ref": ref, "token": token, "serial": rec["serial"], "approved_at": rec["approved_at"], "already_approved": True}
    _check_transition(rec, "APPROVE")
    a.repo.approve(token)
    rec = a.repo.get_registration(token)
    a.repo.audit(actor, "approve", period=(rec.get("arrival") or "")[:7] or None, detail=ref)
    return {"ref": ref, "token": token, "serial": rec["serial"], "approved_at": rec["approved_at"], "already_approved": False}


def return_registration(token: str, reason: str, step: int, actor: str) -> dict[str, Any]:
    a = app()
    reason = reason.strip()
    if not reason:
        raise Invalid({"reason": "a reason note is required; the guest sees it"})
    if step not in (1, 2, 3):
        raise Invalid({"step": "must be 1 (dates), 2 (personal) or 3 (address)"})
    rec = get_registration(token)
    token, ref = rec["token"], handles.ref_for(rec["token"])
    _check_transition(rec, "RETURN")
    a.repo.set_returned(token, reason=reason, step=step)
    a.repo.audit(actor, "return", period=(rec.get("arrival") or "")[:7] or None, detail=f"{ref} step={step}")
    return {"ref": ref, "token": token, "state": "RETURNED", "return_step": step, "return_reason": reason}


# --- group invites -------------------------------------------------------------------
# Email is injected so the synthetic default never touches SMTP. In live mode the app's own
# group_email.try_send runs (it returns False, never raises, when GMAIL_* is unset).

SENT_INVITES: list[dict[str, Any]] = []   # synthetic-mode recorder (tests and demos read it)


def record_invite(group: dict[str, Any], link: str, password: str) -> bool:
    SENT_INVITES.append({"slug": group["slug"], "to": group["receiver_email"], "link": link})
    return False


def smtp_invite(group: dict[str, Any], link: str, password: str) -> bool:
    ge = app().group_email if hasattr(app(), "group_email") else __import__("app.group_email", fromlist=["x"])
    return bool(ge.try_send(ge.build_invite(group, link, password)))


def default_send_invite():
    return smtp_invite if os.environ.get("HOSTEL_MODE", "synthetic") == "live" else record_invite


def base_url() -> str:
    return (os.environ.get("PUBLIC_BASE_URL") or app().config.settings.public_base_url
            or "http://127.0.0.1:8080").rstrip("/")


def _links(group: dict[str, Any]) -> tuple[str, str]:
    base = base_url()
    return f"{base}/g/{group['slug']}", f"{base}/gm/{group['member_token']}"


def _new_password() -> str:
    import secrets

    return secrets.token_urlsafe(9)


def create_group_link(fields: dict[str, Any], actor: str, send_invite=None) -> dict[str, Any]:
    """Validate with the app's own validator, create the PENDING request, send (or record) the invite."""
    a = app()
    send_invite = send_invite or default_send_invite()
    password = _new_password()
    cleaned, errors = a.group_validation.validate_create({**fields, "password": password})
    purpose = fields.get("purpose_of_stay") or "leisure"
    if purpose not in ("leisure", "business"):
        errors["purpose_of_stay"] = "must be leisure or business"
    if errors:
        raise Invalid(errors)
    cleaned.pop("password")
    slug = a.groups.create(cleaned, a.auth.hash_password(password))
    a.groups.set_purpose(slug, purpose)
    group = a.groups.get(slug)
    portal_url, member_url = _links(group)
    sent = bool(send_invite(group, portal_url, password))
    if sent:
        a.groups.mark_invite_sent(slug)
    a.repo.audit(actor, "group_invite", period=group["arrival"][:7], detail=f"{slug} created")
    return {"slug": slug, "portal_url": portal_url, "member_url": member_url, "portal_password": password,
            "email_sent": sent, "receiver_email": group["receiver_email"]}


def resend_group_invite(slug: str, receiver_email: str | None, actor: str, send_invite=None) -> dict[str, Any]:
    a = app()
    send_invite = send_invite or default_send_invite()
    group = a.groups.get(slug)
    if group is None:
        raise NotFound("group", slug, "list_groups")
    if group["status"] != a.groups.PENDING:
        raise InvalidState(f"group {slug}", group["status"], [])
    email = (receiver_email or group["receiver_email"]).strip()
    if not a.group_validation.email_ok(email):
        raise Invalid({"receiver_email": "enter a valid email address"})
    password = _new_password()
    a.groups.update_invite(slug, email, a.auth.hash_password(password))
    group = a.groups.get(slug)
    portal_url, member_url = _links(group)
    sent = bool(send_invite(group, portal_url, password))
    if sent:
        a.groups.mark_invite_sent(slug)
    a.repo.audit(actor, "group_invite", period=group["arrival"][:7], detail=f"{slug} resent")
    return {"slug": slug, "portal_url": portal_url, "member_url": member_url, "portal_password": password,
            "email_sent": sent, "receiver_email": email}


# ---------------------------------------------------------------- destructive (confirmed by the caller)

def erase(token: str, actor: str) -> dict[str, Any]:
    """Hard-delete one registration (signature cascades). If it was the last member of a
    group, the group request goes too, as the desk's delete does. Audited as `delete`."""
    a = app()
    rec = get_registration(token)
    token, ref = rec["token"], handles.ref_for(rec["token"])
    result = a.groups.delete_member(token)
    a.repo.audit(actor, "delete", period=(rec.get("arrival") or "")[:7] or None,
                 detail=f"registration {ref} serial={rec.get('serial')} erased"
                        + (f"; group {result['removed_group']} removed" if result["removed_group"] else ""))
    return {"ref": ref, "token": token, "deleted": result["deleted"], "removed_group": result["removed_group"]}


# ---------------------------------------------------------------- month purge guard

import re  # noqa: E402
import tempfile  # noqa: E402
from dataclasses import dataclass  # noqa: E402

PERIOD_RE = re.compile(r"^\d{4}-\d{2}$")

GUARD_MESSAGES = {
    "bad_period": "period must look like YYYY-MM",
    "backup_not_configured": "backup is not configured on this server (BACKUP_ENCRYPTION_KEY unset); nothing can be purged",
    "no_verified_backup": "no verified backup is logged for {period}; run the month backup in the admin panel first",
    "nothing_to_purge": "nothing to purge: {period} has no approved registrations",
    "count_mismatch": "count mismatch: {live} approved rows live but the verified backup holds {entry_count}; back the month up again",
    "missing_snapshot": "the snapshot file {snapshot} is not on this machine; the purge needs the encrypted snapshot to re-verify",
    "decrypt_failed": "the snapshot {snapshot} does not decrypt with the configured key",
    "verify_failed": "the snapshot {snapshot} failed verification ({check}: {detail}); do not purge",
}


@dataclass
class GuardReport:
    ok: bool
    reason: str | None
    entry_count: int
    snapshot_sha256: str | None = None
    snapshot_name: str | None = None
    live_count: int = 0
    detail: str = ""
    group_requests: int = 0        # group_request rows delete_period removes with the month (open portals included)
    period: str = ""

    def message(self) -> str:
        """The refusal, formatted exactly once (DB-supplied text such as a snapshot name is data, not a template)."""
        if self.ok:
            return "ok"
        check, _, detail = self.detail.partition(":")
        return GUARD_MESSAGES[self.reason].format(
            period=self.period, live=self.live_count, entry_count=self.entry_count,
            snapshot=self.snapshot_name, check=check, detail=detail.strip(),
        )


def count_group_requests(period: str) -> int:
    with app().db.get_conn() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM group_request WHERE substr(arrival,1,7)=?", (period,)).fetchone()[0])


def purge_state(period: str, rep: GuardReport) -> str:
    """Plaintext request_state for purge_month: binds the counts and the checksum the human was shown."""
    return f"purge:{period}:{rep.entry_count}:{rep.group_requests}:{(rep.snapshot_sha256 or '')[:12]}"


def backup_key() -> str:
    return (os.environ.get("BACKUP_ENCRYPTION_KEY") or app().config.settings.backup_encryption_key or "").strip()


def purge_guard(period: str) -> GuardReport:
    """The app's _delete_guard, re-implemented: every check must pass before anyone is asked.

    Order: period shape, backup configured, verified backup row, live rows exist (a month
    with nothing approved is refused as 'nothing to purge' even when a backup row exists),
    live count equals the backed-up count, snapshot file present, decrypts, re-verifies.
    """
    a = app()
    if not PERIOD_RE.match(period or ""):
        return GuardReport(False, "bad_period", 0, period=period)
    key = backup_key()
    if not key:
        return GuardReport(False, "backup_not_configured", 0, period=period)
    row = a.repo.backup_log_get(period)
    if row is None or not row["verified"]:
        return GuardReport(False, "no_verified_backup", 0, period=period)
    live = a.repo.count_approved_for_period(period)
    rep = GuardReport(True, None, row["entry_count"], row["snapshot_sha256"], row["snapshot_name"], live,
                      group_requests=count_group_requests(period), period=period)
    if live == 0:
        rep.ok, rep.reason = False, "nothing_to_purge"
        return rep
    if live != row["entry_count"]:
        rep.ok, rep.reason = False, "count_mismatch"
        return rep
    enc = Path(a.config.settings.backup_dir) / row["snapshot_name"]
    if not enc.is_file():
        rep.ok, rep.reason = False, "missing_snapshot"
        return rep
    from cryptography.fernet import InvalidToken

    with tempfile.TemporaryDirectory() as tmp:
        rt = Path(tmp) / f"{period}.rt.db"
        try:
            a.backup.decrypt_to_file(str(enc), str(rt), key)
        except (InvalidToken, ValueError):
            rep.ok, rep.reason = False, "decrypt_failed"
            return rep
        report = a.backup.verify_snapshot(str(rt), expected_count=row["entry_count"], expected_sha256=row["snapshot_sha256"])
        if not report.ok:
            check, msg = report.first_failure
            rep.ok, rep.reason, rep.detail = False, "verify_failed", f"{check}: {msg}"
            return rep
    return rep


class PurgeAborted(Exception):
    """delete_period found a different live count inside its write transaction."""


def purge(period: str, expected_count: int, actor: str) -> dict[str, Any]:
    a = app()
    row = a.repo.backup_log_get(period)
    groups_before = count_group_requests(period)
    try:
        n = a.repo.delete_period(period, expected_count)
    except a.repo.CountChanged as e:
        raise PurgeAborted(f"the approved count for {period} changed while confirming ({e}); nothing was deleted") from e
    a.repo.audit(actor, "delete", period, f"{n} entries purged, snapshot {row['snapshot_name']}")
    return {"period": period, "deleted": n, "group_requests_deleted": groups_before,
            "snapshot_name": row["snapshot_name"], "snapshot_sha256": row["snapshot_sha256"]}


# ---------------------------------------------------------------- admin edit (APPROVED only)

# Wizard input field names (the shape validation.validate_data_fields expects). Mirrors the
# app's routes/guest.py _record_to_form_data, re-stated here so no FastAPI module is imported.
EDIT_FIELDS = ("arrival", "departure", "purpose_of_stay", "last_name", "first_name", "dob",
               "passport_no", "nationality", "visa_no", "car_reg",
               "addr_line", "addr_postcode", "addr_city", "addr_country", "addr_structured")


def record_to_form(rec: dict[str, Any]) -> dict[str, str]:
    def s(v: Any) -> str:
        return "" if v is None else str(v)

    return {
        "arrival": s(rec.get("arrival")), "departure": s(rec.get("departure")),
        "purpose_of_stay": s(rec.get("purpose_of_stay")),
        "last_name": s(rec.get("last_name")), "first_name": s(rec.get("first_name")), "dob": s(rec.get("dob")),
        "passport_no": s(rec.get("passport_no")), "nationality": s(rec.get("nationality")),
        "visa_no": s(rec.get("visa_no")), "car_reg": s(rec.get("car_reg")),
        "addr_line": s(rec.get("addr_street")), "addr_postcode": s(rec.get("addr_postcode")),
        "addr_city": s(rec.get("addr_city")), "addr_country": s(rec.get("addr_country")),
        "addr_structured": "1" if rec.get("addr_structured") else "0",
    }


def edit_approved(token: str, fields: dict[str, Any], actor: str) -> dict[str, Any]:
    """Merge ``fields`` into the record's wizard form, validate with the app's own validator,
    persist through repo.update_registration (APPROVED only). Audit names the fields, never values."""
    a = app()
    unknown = sorted(set(fields) - set(EDIT_FIELDS))
    if unknown:
        raise Invalid({k: f"not editable; editable fields: {', '.join(EDIT_FIELDS)}" for k in unknown})
    if not fields:
        raise Invalid({"fields": "nothing to change"})
    rec = get_registration(token)
    token, ref = rec["token"], handles.ref_for(rec["token"])
    if rec["state"] != a.state.State.APPROVED.value:
        raise InvalidState(f"registration {ref}", rec["state"], ["only APPROVED records can be edited"])
    form = record_to_form(rec)
    form.update({k: ("" if v is None else str(v)) for k, v in fields.items()})
    res = a.validation.validate_data_fields(form)
    if not res.ok:
        cat = a.i18n.get_catalog("en")
        raise Invalid({f: cat.get(k, k) for f, k in res.errors.items()})
    try:
        info = a.repo.update_registration(token, res.cleaned)
    except a.repo.NotEditable as e:
        raise InvalidState(f"registration {token}", str(e), ["only APPROVED records can be edited"])
    except KeyError:
        raise NotFound("registration", token, "list_approved_registrations")
    detail = "fields: " + (", ".join(info["changed"]) or "none")
    if info["serial"] != info["old_serial"]:
        detail += f"; serial {info['old_serial']} -> {info['serial']}"
    a.repo.audit(actor, "edit", period=res.cleaned["arrival"][:7], detail=f"{ref} {detail}")
    return {"ref": ref, "token": token, **info}


# ---------------------------------------------------------------- authority export

import hashlib  # noqa: E402
import time  # noqa: E402
import uuid  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402

EXPORT_MIME = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
}
_EXPORT_ID = re.compile(r"^[0-9a-f]{32}$")


def export_dir() -> Path:
    """EXPORT_DIR, or a per-user directory under the system temp dir. Created 0700; never inside the repo."""
    d = Path(os.environ.get("EXPORT_DIR") or Path(tempfile.gettempdir()) / f"hostel-ops-exports-{os.getuid()}")
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        d.chmod(0o700)
    except OSError:  # pragma: no cover - foreign filesystem
        pass
    return d


def export_ttl() -> timedelta:
    return timedelta(minutes=int(os.environ.get("EXPORT_TTL_MINUTES", "60")))


def sweep_exports() -> int:
    """Delete export files older than EXPORT_TTL_MINUTES. Runs on every export and read."""
    cutoff = time.time() - export_ttl().total_seconds()
    n = 0
    for p in export_dir().iterdir():
        if p.suffix.lstrip(".") not in EXPORT_MIME:
            continue
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
                n += 1
        except FileNotFoundError:   # another worker swept it between listing and stat/unlink
            continue
    return n


def export(tokens: list[str] | None, arrival_from: str | None, arrival_to: str | None,
           fmt: str, actor: str) -> dict[str, Any]:
    """APPROVED rows only, through the app's export module; bytes go to EXPORT_DIR, not inline."""
    a = app()
    if fmt not in EXPORT_MIME:
        raise Invalid({"format": "xlsx or pdf"})
    if not tokens and not (arrival_from or arrival_to):
        raise Invalid({"tokens": "give tokens[] or an arrival window (arrival_from / arrival_to)"})
    selected: list[dict[str, Any]] = []
    skipped: list[str] = []
    skipped_detail: list[str] = []
    approved = a.state.State.APPROVED.value
    if tokens:
        for t in dict.fromkeys(tokens):
            try:
                rec = a.repo.get_registration(handles.resolve(t))
            except handles.Unknown:
                rec = None
            if rec is None:
                skipped.append(t)
                skipped_detail.append(f"{t}: unknown token")
            elif rec["state"] != approved:
                skipped.append(t)
                skipped_detail.append(f"{t}: {rec['state']}")
            else:
                selected.append(rec)
    else:
        for rec in a.repo.list_all_approved():
            arr = rec.get("arrival") or ""
            if arrival_from and arr < arrival_from:
                continue
            if arrival_to and arr > arrival_to:
                continue
            selected.append(rec)
    if not selected:
        raise Invalid({"selection": "no approved registrations in the selection; only APPROVED rows are exported"})
    data = a.export.build_xlsx(selected) if fmt == "xlsx" else a.export.build_pdf(selected)
    sweep_exports()
    export_id = uuid.uuid4().hex
    path = export_dir() / f"{export_id}.{fmt}"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    now = datetime.now(timezone.utc)
    sha = hashlib.sha256(data).hexdigest()
    periods = sorted({(r.get("arrival") or "")[:7] for r in selected})
    a.repo.audit(actor, "export", period=periods[0] if len(periods) == 1 else None,
                 detail=f"{len(selected)} rows {fmt} export {export_id} sha256 {sha[:12]}; skipped {len(skipped)}")
    return {
        "export_id": export_id, "resource_uri": f"export://{fmt}/{export_id}", "format": fmt,
        "rows": len(selected), "skipped": skipped, "skipped_detail": skipped_detail, "sha256": sha,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": (now + export_ttl()).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def read_export(fmt: str, export_id: str) -> bytes | None:
    if fmt not in EXPORT_MIME or not _EXPORT_ID.match(export_id or ""):
        return None
    sweep_exports()
    path = export_dir() / f"{export_id}.{fmt}"
    return path.read_bytes() if path.is_file() else None
