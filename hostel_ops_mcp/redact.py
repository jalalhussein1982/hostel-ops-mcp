"""Field policy: which columns leave the server, and in what form, per scope.

The policy is a table keyed ``"table.column"`` covering every column of the four
tables that hold guest or leader data. ``apply`` is fail-closed: a column that is not
in the table is dropped, and ``classify_all_columns`` lets a test fail the build when
the vendored app adds a column nobody has classified yet.

Without ``pii:read`` a caller sees names, nationality, stay dates, state, serial and an opaque
``ref`` in place of the registration token (a bearer credential for the guest wizard);
document numbers are masked to their last two characters, the date of birth becomes an
age band, the address shrinks to its country, and signatures, signer names, contact
details and drafts are absent.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from enum import Enum
from typing import Any

from .handles import ref_for  # noqa: F401  (re-exported for callers of the policy)

PII_SCOPE = "pii:read"
TABLES = ("guest_registration", "guest_signature", "group_request", "group_member")


class Treatment(Enum):
    PASS = "pass"                  # returned at every scope
    MASK_DOC = "mask_doc"          # "•••" + last two characters unless pii:read
    AGE_BAND = "age_band"          # replaced by `age_band` unless pii:read
    COUNTRY_ONLY = "country_only"  # the address field that survives without pii:read
    PII_ONLY = "pii_only"          # present only with pii:read
    DROP = "drop"                  # never returned (secrets, hashes, raw drafts)
    HANDLE = "handle"              # registration token: `ref` always, the token itself only with pii:read


P, M, A, C, PII, D, H = (Treatment.PASS, Treatment.MASK_DOC, Treatment.AGE_BAND,
                         Treatment.COUNTRY_ONLY, Treatment.PII_ONLY, Treatment.DROP, Treatment.HANDLE)

_REGISTRATION = {
    "token": H, "code": P, "state": P, "return_reason": PII, "return_step": P, "lang": P,
    "arrival": P, "departure": P, "nights": P, "purpose_of_stay": P,
    "last_name": P, "first_name": P, "dob": A, "passport_no": M, "nationality": P,
    "visa_no": M, "car_reg": M,
    "addr_structured": PII, "addr_street": PII, "addr_postcode": PII, "addr_city": PII,
    "addr_country": C, "addr_freetext": PII, "addr_display": PII,
    "created_at": P, "updated_at": P, "approved_at": P, "serial": P,
    "group_id": P, "is_group_leader": P,
}
_SIGNATURE = {
    "token": H, "signature_png": PII, "signer_role": P, "signer_name": PII,
    "signed_content_hash": D, "declaration_text": P, "declaration_lang": P, "signed_at": P,
}
_GROUP_REQUEST = {
    "slug": P, "status": P, "group_name": P, "leader_last_name": P, "leader_first_name": P,
    "arrival": P, "departure": P, "nights": P, "purpose_of_stay": P,
    "receiver_email": PII, "password_hash": D, "expected_members": P,
    "leader_email": PII, "leader_phone_home": PII, "leader_phone_stay": PII,
    "draft_json": D, "draft_saved_at": P, "created_at": P, "invite_sent_at": P,
    "submitted_at": P, "member_token": D,
}
_GROUP_MEMBER = {
    "id": P, "group_id": P, "is_leader": P, "position": P,
    "last_name": P, "first_name": P, "dob": A, "nationality": P, "passport_no": M, "visa_no": M,
    "addr_line": PII, "addr_postcode": PII, "addr_city": PII, "addr_country": C, "addr_structured": PII,
    "email": PII, "phone_home": PII, "phone_stay": PII,
    "status": P, "source": P, "seen": P,
    "signature_png": PII, "signer_role": P, "signer_name": PII,
    "created_at": P, "updated_at": P,
}

FIELD_POLICY: dict[str, Treatment] = {
    **{f"guest_registration.{k}": v for k, v in _REGISTRATION.items()},
    **{f"guest_signature.{k}": v for k, v in _SIGNATURE.items()},
    **{f"group_request.{k}": v for k, v in _GROUP_REQUEST.items()},
    **{f"group_member.{k}": v for k, v in _GROUP_MEMBER.items()},
}


def age_band(dob: str | None, on: date | None = None) -> str | None:
    """``<15`` / ``15-17`` / ``18+`` for an ISO date of birth, measured on ``on`` (default today)."""
    if not dob:
        return None
    try:
        born = date.fromisoformat(dob)
    except ValueError:
        return None
    on = on or date.today()
    age = on.year - born.year - ((on.month, on.day) < (born.month, born.day))
    if age < 15:
        return "<15"
    if age < 18:
        return "15-17"
    return "18+"


def mask_document(value: Any) -> str | None:
    if value is None or value == "":
        return None
    s = str(value)
    return "•••" + s[-2:]


def apply(record: dict[str, Any], table: str, scopes: set[str]) -> dict[str, Any]:
    """Return a copy of ``record`` with the policy for ``table`` applied for ``scopes``.

    Missing keys are simply absent; unknown keys are dropped (fail-closed).
    """
    if table not in TABLES:
        raise ValueError(f"unknown table {table!r}")
    full = PII_SCOPE in scopes
    out: dict[str, Any] = {}
    for key, value in record.items():
        treatment = FIELD_POLICY.get(f"{table}.{key}")
        if treatment is None or treatment is Treatment.DROP:
            continue
        if treatment is Treatment.PASS or treatment is Treatment.COUNTRY_ONLY:
            out[key] = value
        elif treatment is Treatment.MASK_DOC:
            out[key] = value if full else mask_document(value)
        elif treatment is Treatment.AGE_BAND:
            if full:
                out[key] = value
            out["age_band"] = age_band(value)
        elif treatment is Treatment.PII_ONLY:
            if full:
                out[key] = value
        elif treatment is Treatment.HANDLE:
            if value:
                out["ref"] = ref_for(str(value))
            if full:
                out[key] = value
    return out


def classify_all_columns(conn: sqlite3.Connection) -> list[str]:
    """``table.column`` entries present in the database but missing from FIELD_POLICY."""
    missing: list[str] = []
    for table in TABLES:
        for row in conn.execute(f"PRAGMA table_info({table})"):
            key = f"{table}.{row[1]}"
            if key not in FIELD_POLICY:
                missing.append(key)
    return missing


def policy_markdown() -> str:
    """The policy as a Markdown table, served by the ``hostel://policy/roles`` resource."""
    lines = ["| Column | Without pii:read | With pii:read |", "|---|---|---|"]
    words = {
        Treatment.PASS: ("returned", "returned"),
        Treatment.MASK_DOC: ("`•••` + last 2 characters", "returned"),
        Treatment.AGE_BAND: ("`age_band` only (`<15`, `15-17`, `18+`)", "returned (+ `age_band`)"),
        Treatment.COUNTRY_ONLY: ("returned (the only address field)", "returned"),
        Treatment.PII_ONLY: ("absent", "returned"),
        Treatment.DROP: ("absent", "absent"),
        Treatment.HANDLE: ("opaque `ref` only (the token opens the guest wizard)", "`ref` and the token"),
    }
    for key, t in FIELD_POLICY.items():
        if t is Treatment.PASS:
            continue
        a, b = words[t]
        lines.append(f"| `{key}` | {a} | {b} |")
    return "\n".join(lines)
