"""Pydantic output models. Every tool returns one of these; they ARE the output schemas.

Fields that the redaction policy may remove are optional and default to ``None``, so a
caller without ``pii:read`` sees the key with ``null`` (or a masked value) rather than
a schema violation.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class RegistrationCard(_Model):
    """What the desk list shows for one registration. Document numbers are masked without pii:read."""

    token: str = Field(description="Opaque registration id; the argument every other tool takes.")
    serial: str | None = Field(default=None, description="YYYY-MM-DD-NNNN, assigned at approval.")
    state: Literal["DRAFT", "PENDING_REVIEW", "RETURNED", "APPROVED"]
    arrival: str | None = None
    departure: str | None = None
    nights: int | None = None
    purpose_of_stay: str | None = None
    last_name: str | None = None
    first_name: str | None = None
    nationality: str | None = Field(default=None, description="ISO 3166-1 alpha-3.")
    age_band: str | None = Field(default=None, description="<15 | 15-17 | 18+ (always); dob needs pii:read.")
    dob: str | None = Field(default=None, description="Date of birth; pii:read only.")
    passport_no: str | None = Field(default=None, description="Masked to ••• + last 2 without pii:read.")
    visa_no: str | None = None
    car_reg: str | None = None
    addr_country: str | None = None
    addr_display: str | None = Field(default=None, description="Full address; pii:read only.")
    lang: str | None = None
    created_at: str | None = None
    group_id: str | None = None
    is_group_leader: bool = False
    under18: bool = Field(default=False, description="Under 18 on the arrival date (desk awareness flag).")
    return_reason: str | None = None
    return_step: int | None = None


class SignatureInfo(_Model):
    signer_role: Literal["self", "legal_representative"]
    signed_at: str
    declaration_lang: str | None = None
    signer_name: str | None = Field(default=None, description="Representative's name; pii:read only.")
    signature_png_base64: str | None = Field(
        default=None, description="Flat PNG; only with pii:read AND include_signature=True."
    )


class Registration(RegistrationCard):
    """Full record as the scope allows, plus signature provenance when one exists."""

    code: str | None = None
    approved_at: str | None = None
    updated_at: str | None = None
    addr_structured: int | None = None
    addr_street: str | None = None
    addr_postcode: str | None = None
    addr_city: str | None = None
    addr_freetext: str | None = None
    signature: SignatureInfo | None = None


class RegistrationPage(_Model):
    items: list[RegistrationCard]
    next_cursor: str | None = Field(default=None, description="Pass back as `cursor` to continue; null at the end.")
    total: int = Field(description="Rows matching the query across all pages.")


class MinorAlert(_Model):
    token: str
    serial: str | None = None
    state: str
    first_name: str | None = None
    last_name: str | None = None
    age_band: str | None = None
    arrival: str | None = None
    group_id: str | None = None
    signer_role: str | None = None
    needs_legal_representative: bool = Field(
        description="Under 15 and the signature is not by a legal representative (Act 326/1999 §103)."
    )


class GroupCard(_Model):
    slug: str
    status: Literal["PENDING", "SUBMITTED"]
    group_name: str
    leader_first_name: str
    leader_last_name: str
    arrival: str
    departure: str
    nights: int
    purpose_of_stay: str | None = None
    expected_members: int | None = None
    created_at: str | None = None
    invite_sent_at: str | None = None
    submitted_at: str | None = None
    member_count: int = Field(description="Injected registrations (SUBMITTED) or staging rows (PENDING).")
    leader_email: str | None = Field(default=None, description="pii:read only.")
    leader_phone_home: str | None = None
    leader_phone_stay: str | None = None
    receiver_email: str | None = None


class GroupMemberRow(_Model):
    """A staging row on an open group portal (PENDING groups only)."""

    id: int
    is_leader: bool
    position: int
    status: str
    source: str
    last_name: str | None = None
    first_name: str | None = None
    age_band: str | None = None
    dob: str | None = None
    nationality: str | None = None
    passport_no: str | None = None
    addr_country: str | None = None
    email: str | None = None


class GroupDetail(_Model):
    group: GroupCard
    total: int
    filled: int
    attention_ids: list[int] = Field(description="PENDING: staging rows with data that fail validation.")
    duplicates: list[list[str]] = Field(
        description="Rows sharing (first, last, dob): staging row ids (PENDING) or tokens (SUBMITTED)."
    )
    members: list[RegistrationCard] = Field(description="Injected registrations; empty while PENDING.")
    panel_rows: list[GroupMemberRow] = Field(description="Staging rows; empty once SUBMITTED.")


class MonthRow(_Model):
    period: str
    live_count: int
    entry_count: int | None = None
    snapshot_name: str | None = None
    backed_up_at: str | None = None
    deleted_at: str | None = None
    verified: bool = False


class AuditRow(_Model):
    id: int
    at: str
    actor: str
    action: str
    period: str | None = None
    detail: str | None = None


class ApprovalResult(_Model):
    token: str
    serial: str | None
    approved_at: str | None
    already_approved: bool = Field(description="True when the row was APPROVED before this call; no second serial, no second audit row.")


class ReturnResult(_Model):
    token: str
    state: Literal["RETURNED"]
    return_step: int
    return_reason: str


class GroupLink(_Model):
    slug: str
    portal_url: str
    member_url: str
    portal_password: str = Field(description="Shown once. The server stores only its argon2 hash.")
    email_sent: bool
    receiver_email: str


class ExportResult(_Model):
    export_id: str
    resource_uri: str = Field(description="Read this resource to get the bytes.")
    format: Literal["xlsx", "pdf"]
    rows: int
    skipped: list[str] = Field(description="Tokens that were not APPROVED and were left out.")
    sha256: str
    generated_at: str
    expires_at: str


class EditResult(_Model):
    token: str
    changed: list[str]
    old_serial: str | None
    serial: str | None


class EraseResult(_Model):
    token: str
    deleted: int
    removed_group: str | None = Field(default=None, description="Slug of a group removed because this was its last member.")


class PurgeResult(_Model):
    period: str
    deleted: int
    snapshot_name: str
    snapshot_sha256: str


class Cancelled(_Model):
    cancelled: Literal[True] = True
    reason: Literal["declined", "cancelled"]
    message: str
