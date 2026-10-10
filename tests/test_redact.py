"""Task 3: every column of every table is classified; PII leaves only with pii:read."""

from __future__ import annotations

import json
import sqlite3

import pytest

from hostel_ops_mcp import redact
from tests.conftest import FIXTURES

PII = {"pii:read"}
NONE: set[str] = set()


@pytest.fixture
def approved_record(app):
    planted = json.loads((FIXTURES / "synthetic.json").read_text())
    token = planted["minor_tokens"]["approved_under15_legal_rep"]
    rec = app.repo.get_registration(token)
    sig = app.repo.get_signature(token)
    return rec, sig


def test_every_column_of_every_table_is_classified(db_path):
    con = sqlite3.connect(db_path)
    try:
        assert redact.classify_all_columns(con) == []
    finally:
        con.close()
    for key in redact.FIELD_POLICY:
        table, _, column = key.partition(".")
        assert table in redact.TABLES and column


def test_document_numbers_are_masked_without_pii_scope(approved_record):
    rec, _ = approved_record
    out = redact.apply(rec, "guest_registration", NONE)
    assert out["passport_no"] == "•••" + rec["passport_no"][-2:]
    assert out["last_name"] == rec["last_name"] and out["nationality"] == rec["nationality"]
    assert "dob" not in out and out["age_band"] == "<15"
    assert out["addr_country"] == rec["addr_country"]
    for f in ("addr_street", "addr_postcode", "addr_city", "addr_freetext", "addr_display"):
        assert f not in out
    assert "code" in out and "serial" in out and out["state"] == "APPROVED"


def test_full_record_with_pii_scope(approved_record):
    rec, _ = approved_record
    out = redact.apply(rec, "guest_registration", PII)
    for f in ("passport_no", "dob", "addr_street", "addr_display", "addr_city"):
        assert out[f] == rec[f]
    assert out["age_band"] == "<15"


def test_signature_table_policy(approved_record):
    _, sig = approved_record
    out = redact.apply(sig, "guest_signature", NONE)
    assert "signature_png" not in out and "signer_name" not in out
    assert out["signer_role"] == "legal_representative" and "signed_at" in out
    assert "signed_content_hash" not in out
    full = redact.apply(sig, "guest_signature", PII)
    assert full["signature_png"] == sig["signature_png"] and full["signer_name"] == sig["signer_name"]
    assert "signed_content_hash" not in full  # internal, never returned


def test_group_request_policy(app):
    planted = json.loads((FIXTURES / "synthetic.json").read_text())
    g = app.groups.get(planted["groups"]["submitted_visible"])
    out = redact.apply(g, "group_request", NONE)
    for f in ("leader_email", "leader_phone_home", "leader_phone_stay", "receiver_email"):
        assert f not in out
    assert out["group_name"] == g["group_name"] and out["slug"] == g["slug"]
    full = redact.apply(g, "group_request", PII)
    assert full["leader_email"] == g["leader_email"] and full["receiver_email"] == g["receiver_email"]
    for f in ("password_hash", "draft_json", "member_token"):
        assert f not in out and f not in full


def test_apply_never_raises_on_missing_or_unknown_keys():
    assert redact.apply({"token": "x"}, "guest_registration", NONE) == {"token": "x"}
    assert redact.apply({"passport_no": None, "dob": None}, "guest_registration", NONE) == {"passport_no": None, "age_band": None}
    # an unclassified key is dropped, never passed through by accident
    assert redact.apply({"brand_new_column": 1, "token": "t"}, "guest_registration", PII) == {"token": "t"}


@pytest.mark.parametrize("dob,band", [("2015-01-01", "<15"), ("2010-01-01", "15-17"), ("1990-05-05", "18+")])
def test_age_bands(dob, band):
    assert redact.age_band(dob) == band
