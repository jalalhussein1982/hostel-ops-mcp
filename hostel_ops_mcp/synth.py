"""Synthetic hostel database: the app's own schema, filled with invented people.

Every row is produced through the app's service layer (``repo``, ``groups``) so the
data is shaped exactly like production, but nothing in it is real: names are
syllable combinations from a seeded RNG, documents are ``X`` + 7 digits, addresses
are numbered streets in invented towns, emails end in ``@example.invalid``.

The build is deterministic. The app mints tokens with ``secrets`` and timestamps with
the wall clock; both are replaced for the duration of the build by a seeded RNG and a
synthetic clock, then restored.
"""

from __future__ import annotations

import base64
import hashlib
import json
import random
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import _app

DEFAULT_SEED = 1982

# A 1x1 transparent PNG: a valid flat image, no biometrics, 68 bytes.
SIGNATURE_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)

_SYLLABLES = ("ka", "ro", "mi", "ta", "ve", "lo", "na", "si", "do", "ra", "pe", "li", "to", "ma",
              "ne", "su", "ri", "va", "bo", "de", "fa", "gu", "ha", "jo", "ke", "lu", "mo", "nu",
              "pa", "re", "sa", "ti", "ul", "wa", "ye", "zo")
_COUNTRIES = (("DEU", "Germany"), ("FRA", "France"), ("ESP", "Spain"), ("ITA", "Italy"),
              ("POL", "Poland"), ("NLD", "Netherlands"), ("GBR", "United Kingdom"), ("USA", "United States"),
              ("CAN", "Canada"), ("AUS", "Australia"), ("BRA", "Brazil"), ("JPN", "Japan"),
              ("SWE", "Sweden"), ("PRT", "Portugal"), ("AUT", "Austria"), ("KOR", "South Korea"))
_TOWNS = ("Northvale", "Eastmere", "Westfold", "Southbrook", "Greyharbor", "Fairmont", "Oakridge", "Ashford")
_STREETS = ("Mill Lane", "Harbor Road", "Station Street", "Orchard Way", "Linden Avenue", "Bridge Street")
_LANGS = ("en", "en", "en", "cs", "fr", "es", "it", "uk")
_RETURN_REASONS = (("Passport number does not match the document shown", 2),
                   ("Departure date is before arrival", 1),
                   ("Address is missing the street", 3),
                   ("Nationality field left blank", 2),
                   ("Please re-enter the visa number", 2))
_DECLARATION = "I confirm that the information above is true and complete."


@dataclass
class SynthSummary:
    """What the build planted, so tests and docs can point at exact rows."""

    seed: int
    counts: dict[str, int]
    group_members: int
    periods: list[str]
    minor_tokens: dict[str, str]
    groups: dict[str, str]
    tokens: dict[str, list[str]] = field(default_factory=dict)
    draft_with_partial_fields: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)


class _Clock:
    """Monotonic synthetic UTC clock, one minute per call, starting at a fixed instant."""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> str:
        self.now += timedelta(minutes=1)
        return self.now.strftime("%Y-%m-%dT%H:%M:%SZ")

    def jump_to(self, when: datetime) -> None:
        if when > self.now:
            self.now = when


class _Ids:
    def __init__(self, rng: random.Random) -> None:
        self.rng = rng

    def token(self) -> str:
        return base64.urlsafe_b64encode(self.rng.randbytes(24)).decode().rstrip("=")

    def code(self) -> str:
        return f"{self.rng.randrange(10000):04d}"


def _name(rng: random.Random, parts: int) -> str:
    return "".join(rng.choice(_SYLLABLES) for _ in range(parts)).capitalize()


def _person(rng: random.Random, dob: date | None = None) -> dict:
    code, country = rng.choice(_COUNTRIES)
    if dob is None:
        dob = date(rng.randrange(1958, 2004), rng.randrange(1, 13), rng.randrange(1, 29))
    street = f"{rng.randrange(1, 180)} {rng.choice(_STREETS)}"
    town = rng.choice(_TOWNS)
    postcode = f"{rng.randrange(10000, 99999)}"
    display = f"{street}, {postcode} {town}, {country}"
    return {
        "last_name": _name(rng, 3), "first_name": _name(rng, 2), "dob": dob.isoformat(),
        "passport_no": f"X{rng.randrange(10**7):07d}", "nationality": code,
        "visa_no": f"V{rng.randrange(10**6):06d}" if rng.random() < 0.25 else None,
        "car_reg": f"{rng.choice('ABCDEFGH')}{rng.randrange(100, 999)}{rng.choice('KLMN')}{rng.choice('PRST')}"
        if rng.random() < 0.2 else None,
        "addr_structured": 1, "addr_street": street, "addr_postcode": postcode, "addr_city": town,
        "addr_country": country, "addr_freetext": None, "addr_display": display,
    }


def _stay(rng: random.Random, arrival: date) -> dict:
    nights = rng.randrange(1, 6)
    return {"arrival": arrival.isoformat(), "departure": (arrival + timedelta(days=nights)).isoformat(),
            "nights": nights, "purpose_of_stay": "business" if rng.random() < 0.3 else "leisure"}


def _submit(app, token: str, fields: dict, signer_role: str = "self", signer_name: str | None = None) -> None:
    sig = {
        "signature_png": SIGNATURE_PNG, "signer_role": signer_role, "signer_name": signer_name,
        "signed_content_hash": app.signature.signed_content_hash(fields),
        "declaration_text": _DECLARATION, "declaration_lang": "en",
    }
    app.repo.save_submission_with_signature(token, fields, sig)


def build_synthetic_db(path: Path | str, seed: int = DEFAULT_SEED, summary_path: Path | str | None = None) -> SynthSummary:
    """Create ``path`` from scratch and fill it. Returns what was planted.

    When ``summary_path`` is given the summary is also written there as JSON (the
    committed fixture ships ``fixtures/synthetic.json`` next to the database).
    """
    path = Path(path)
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(path) + suffix)
        if p.exists():
            p.unlink()
    app = _app.load(path)
    rng = random.Random(seed)
    ids = _Ids(random.Random(seed + 1))
    clock = _Clock(datetime(2026, 7, 28, 6, 0, tzinfo=timezone.utc))

    patched = {
        (app.repo, "new_token"): app.repo.new_token, (app.repo, "new_code"): app.repo.new_code,
        (app.repo, "_utcnow_iso"): app.repo._utcnow_iso,
        (app.groups, "new_token"): app.groups.new_token, (app.groups, "new_code"): app.groups.new_code,
        (app.groups, "_utcnow_iso"): app.groups._utcnow_iso,
    }
    app.repo.new_token = app.groups.new_token = ids.token
    app.repo.new_code = app.groups.new_code = ids.code
    app.repo._utcnow_iso = app.groups._utcnow_iso = clock
    try:
        summary = _fill(app, rng, clock, seed)
    finally:
        for (mod, name), original in patched.items():
            setattr(mod, name, original)
    if summary_path is not None:
        Path(summary_path).write_text(summary.to_json() + "\n", encoding="utf-8")
    return summary


def _fill(app, rng: random.Random, clock: _Clock, seed: int) -> SynthSummary:
    repo, groups = app.repo, app.groups
    tokens: dict[str, list[str]] = {"DRAFT": [], "PENDING_REVIEW": [], "RETURNED": [], "APPROVED": []}
    minors: dict[str, str] = {}

    # --- APPROVED individuals: 15 arrivals in 2026-08, 15 in 2026-09 -----------------------
    for month in (8, 9):
        clock.jump_to(datetime(2026, month, 1, 7, 0, tzinfo=timezone.utc))
        for i in range(15):
            arrival = date(2026, month, 1 + (i * 2) % 28)
            clock.jump_to(datetime(2026, month, arrival.day, 9, 0, tzinfo=timezone.utc))
            token, _ = repo.create_registration(rng.choice(_LANGS))
            if month == 8 and i == 7:
                # under-15 guest, signed by a legal representative
                person = _person(rng, dob=date(2014, 2, 11))
                fields = {**_stay(rng, arrival), **person}
                _submit(app, token, fields, "legal_representative", signer_name=_name(rng, 2) + " " + person["last_name"])
                minors["approved_under15_legal_rep"] = token
            else:
                _submit(app, token, {**_stay(rng, arrival), **_person(rng)})
            repo.approve(token)
            tokens["APPROVED"].append(token)

    # --- the live desk: today is 2026-10-11 in the synthetic world -------------------------
    today = date(2026, 10, 11)
    clock.jump_to(datetime(2026, 10, 10, 15, 0, tzinfo=timezone.utc))

    for i in range(5):
        token, _ = repo.create_registration(rng.choice(_LANGS))
        _submit(app, token, {**_stay(rng, today - timedelta(days=rng.randrange(0, 2))), **_person(rng)})
        reason, step = _RETURN_REASONS[i]
        repo.set_returned(token, reason, step)
        tokens["RETURNED"].append(token)

    clock.jump_to(datetime(2026, 10, 11, 6, 30, tzinfo=timezone.utc))
    for i in range(15):
        token, _ = repo.create_registration(rng.choice(_LANGS))
        stay = _stay(rng, today if i < 12 else today + timedelta(days=1))
        if i == 4:
            fields = {**stay, **_person(rng, dob=date(2010, 3, 4))}       # 16 on arrival
            _submit(app, token, fields)
            minors["pending_under18"] = token
        elif i == 9:
            fields = {**stay, **_person(rng, dob=date(2013, 6, 15))}      # 13 on arrival, signed "self"
            _submit(app, token, fields)
            minors["pending_under15_self"] = token
        else:
            _submit(app, token, {**stay, **_person(rng)})
        tokens["PENDING_REVIEW"].append(token)

    for i in range(10):
        token, _ = repo.create_registration(rng.choice(_LANGS))
        tokens["DRAFT"].append(token)
    # Two guests are mid-wizard: the first pages are saved on the draft row (the real app
    # keeps a draft only in the browser, but a partially filled DRAFT is the worst case the
    # redaction policy must cover, so the fixture plants it).
    partial = tokens["DRAFT"][0]
    p = _person(rng)
    with app.db.get_conn() as conn:
        conn.execute(
            "UPDATE guest_registration SET arrival=?, departure=?, nights=?, last_name=?, first_name=?, "
            "dob=?, passport_no=?, nationality=? WHERE token=?",
            (today.isoformat(), (today + timedelta(days=2)).isoformat(), 2, p["last_name"], p["first_name"],
             p["dob"], p["passport_no"], p["nationality"], partial),
        )

    # --- groups ------------------------------------------------------------------------------
    pw_hash = app.auth.hash_password("synthetic-portal-password")
    group_slugs: dict[str, str] = {}

    def group_fields(name: str, leader: dict, arrival: date, nights: int, n: int) -> dict:
        return {"group_name": name, "leader_last_name": leader["last_name"],
                "leader_first_name": leader["first_name"], "arrival": arrival.isoformat(),
                "departure": (arrival + timedelta(days=nights)).isoformat(), "nights": nights,
                "receiver_email": f"{leader['first_name'].lower()}.{leader['last_name'].lower()}@example.invalid",
                "expected_members": n}

    # SUBMITTED group A: arrived 2026-10-05 (visible on the desk), 1 leader + 4 members
    clock.jump_to(datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc))
    leader = _person(rng)
    slug_a = groups.create(group_fields("Northvale Rowing Club", leader, date(2026, 10, 5), 3, 5), pw_hash)
    members_a = [_person(rng) for _ in range(4)]
    groups.submit(slug_a, {**leader, "purpose_of_stay": "leisure"}, members_a,
                  {"leader_email": f"{leader['first_name'].lower()}@example.invalid",
                   "leader_phone_home": "+420000000001", "leader_phone_stay": "+420000000001"})
    group_slugs["submitted_visible"] = slug_a

    # SUBMITTED group B: arrives 2026-11-02 (hidden from the completed list until then);
    # the same person was entered twice by the organiser -> a duplicate pair.
    clock.jump_to(datetime(2026, 9, 28, 11, 0, tzinfo=timezone.utc))
    leader_b = _person(rng)
    slug_b = groups.create(group_fields("Eastmere School Choir", leader_b, date(2026, 11, 2), 2, 6), pw_hash)
    members_b = [_person(rng, dob=date(2011, 5, 20)) for _ in range(3)] + [_person(rng)]
    members_b.append({**members_b[1], "passport_no": f"X{rng.randrange(10**7):07d}"})  # re-entered with ID number
    groups.submit(slug_b, {**leader_b, "purpose_of_stay": "leisure"}, members_b,
                  {"leader_email": f"{leader_b['first_name'].lower()}@example.invalid",
                   "leader_phone_home": "+420000000002", "leader_phone_stay": "+420000000003"})
    group_slugs["submitted_with_duplicates"] = slug_b

    # PENDING group: the portal is open, two member rows filled (one pair duplicated), one empty.
    clock.jump_to(datetime(2026, 10, 9, 9, 0, tzinfo=timezone.utc))
    leader_c = _person(rng)
    slug_c = groups.create(group_fields("Westfold Hiking Society", leader_c, date(2026, 10, 24), 4, 1), pw_hash)
    groups.mark_invite_sent(slug_c)
    dup = _person(rng)
    for person in (dup, {**dup, "passport_no": f"X{rng.randrange(10**7):07d}"}):
        rid = groups.add_empty_row(slug_c)
        groups.save_row(slug_c, rid, {k: person[k] for k in ("last_name", "first_name", "dob", "nationality", "passport_no")})
    groups.add_empty_row(slug_c)
    group_slugs["pending"] = slug_c

    # --- backup log and audit ---------------------------------------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        snap = Path(tmp) / "2026-08.db"
        count, sha = app.backup.build_snapshot(app.config.settings.database_path, "2026-08", str(snap))
    repo.backup_log_upsert("2026-08", count, "2026-08.db.enc", sha, "2026-09-01T06:05:00Z", verified=1)

    audit = [
        ("admin", "backup", "2026-08", f"{count} entries -> 2026-08.db.enc"),
        ("admin", "backup_failed", "2026-09", "row_count: expected 15, got 14"),
        ("admin", "edit", "2026-09", "fields: addr_display"),
        ("admin", "delete", "2026-07", "0 entries purged, snapshot 2026-07.db.enc"),
        ("desk", "approve", None, "via desk"),
    ]
    for actor, action, period, detail in audit:
        repo.audit(actor, action, period, detail)

    group_members = len(members_a) + 1 + len(members_b) + 1
    return SynthSummary(
        seed=seed,
        counts={k: len(v) for k, v in tokens.items()},
        group_members=group_members,
        periods=["2026-08", "2026-09"],
        minor_tokens=minors,
        groups=group_slugs,
        tokens=tokens,
        draft_with_partial_fields=partial,
    )


def fixture_digest(path: Path | str) -> str:
    """Stable digest of (token, last_name) pairs, used by the determinism test and docs."""
    import sqlite3

    con = sqlite3.connect(str(path))
    rows = con.execute("SELECT token, last_name FROM guest_registration ORDER BY token").fetchall()
    con.close()
    return hashlib.sha256(repr(rows).encode()).hexdigest()
