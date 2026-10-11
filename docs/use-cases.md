# Use cases

Eight scenarios, narrated. Every tool call and every output below was captured by running
`scripts/capture_use_cases.py` against a scratch copy of `fixtures/synthetic.db` (synthetic
people, fixed seed) through the SDK's in-memory client. Nothing is edited except the sealed
`request_state` (truncated) and a signature PNG (truncated). Every registration carries a `ref`;
the raw `token` is present only because scenarios 1–7 run with `pii:read`. Timestamps such as `approved_at`
are the wall clock at capture time; the fixture's own timestamps come from the synthetic clock.

Scopes in scenarios 1–7: an admin token (every scope, including `pii:read`), so document
numbers and addresses are in clear. Scenario 8 shows the same record through a receptionist
token without `pii:read`.

Regenerate: `uv run python scripts/capture_use_cases.py /tmp/capture > transcript.md`.

---

## 1. Morning desk briefing

The host loads the `morning_desk_briefing` prompt. It names the three read tools and the
handover format, and it tells the model not to approve or return anything while briefing. The
model walks the queue (here only the first page of three is shown; `next_cursor` continues it),
asks for minor alerts, and lists the groups whose portal is still open.

Every card carries a `ref`, the handle a receptionist uses to address a record; the raw `token`
appears here only because this run holds `pii:read` (scenario 8 shows the same card without it).

What to read in the output: the two alerts. Lopa Nupali is 16 and signed personally, which the
law allows; Zodo Detiha is under 15 and also signed personally, so
`needs_legal_representative` is `true` and the desk must collect a parent's signature before
anyone approves that card. The pending group shows `member_count: 4` staging rows for an
`expected_members: 1` request: the leader has added three member rows on the portal since the
invite went out (`get_group` shows two of them are the same person, entered twice).


Prompt text:

> You are preparing the morning handover for the hostel reception desk. Call list_pending_registrations (walk every page with next_cursor), desk_minor_alerts, list_groups with status=pending and then get_group for each pending group, and list_pending_registrations with state=RETURNED. Then write the handover in this format:
> 
> 1. Arrivals today: pending cards whose arrival is today, oldest first, with last_name, first_name, nationality, nights and the 4-digit code.
> 2. Minors: every alert, with age_band and whether a legal representative's signature is still needed before approval (needs_legal_representative).
> 3. Groups awaiting members: each pending group's group_name, arrival, filled of total rows, attention_ids and duplicates from get_group, and whether invite_sent_at is set.
> 4. Returned and not resubmitted: RETURNED cards whose created_at is older than 24 hours, with return_step (1 dates, 2 personal, 3 address); quote return_reason only if it is present.
> 
> Use the masked document numbers and the ref values exactly as returned; never guess a full number or a token. Do not approve or return anything in this briefing.

### pending queue, first page

```
list_pending_registrations({"limit": 3})
```

```json
{
  "items": [
    {
      "ref": "b24e527a78982b40",
      "token": "lNft8MglJKdrN61RsjARlHn09BY41lgv",
      "serial": null,
      "state": "PENDING_REVIEW",
      "arrival": "2026-10-11",
      "departure": "2026-10-16",
      "nights": 5,
      "purpose_of_stay": "leisure",
      "last_name": "Hazoka",
      "first_name": "Bosu",
      "nationality": "AUS",
      "age_band": "18+",
      "dob": "1996-09-16",
      "passport_no": "X4374498",
      "visa_no": null,
      "car_reg": null,
      "addr_country": "Australia",
      "addr_display": "72 Harbor Road, 98012 Southbrook, Australia",
      "lang": "fr",
      "created_at": "2026-10-11T06:31:00Z",
      "group_id": null,
      "is_group_leader": false,
      "under18": false,
      "return_reason": null,
      "return_step": null,
      "code": "3104"
    },
    {
      "ref": "8fef6b503815da22",
      "token": "HRDr7b2F06_kT2J_n4pDYMzf8ZCr_Z3a",
      "serial": null,
      "state": "PENDING_REVIEW",
      "arrival": "2026-10-11",
      "departure": "2026-10-12",
      "nights": 1,
      "purpose_of_stay": "business",
      "last_name": "Fayeve",
      "first_name": "Vema",
      "nationality": "USA",
      "age_band": "18+",
      "dob": "1985-11-16",
      "passport_no": "X5980315",
      "visa_no": null,
      "car_reg": null,
      "addr_country": "United States",
      "addr_display": "32 Bridge Street, 49870 Southbrook, United States",
      "lang": "cs",
      "created_at": "2026-10-11T06:33:00Z",
      "group_id": null,
      "is_group_leader": false,
      "under18": false,
      "return_reason": null,
      "return_step": null,
      "code": "6489"
    },
    {
      "ref": "bf11cb99f6f09864",
      "token": "8kMhP3ejpf3tP68ZfG7Rv0eZpf1plcCu",
      "serial": null,
      "state": "PENDING_REVIEW",
      "arrival": "2026-10-11",
      "departure": "2026-10-12",
      "nights": 1,
      "purpose_of_stay": "business",
      "last_name": "Totana",
      "first_name": "Lifa",
      "nationality": "KOR",
      "age_band": "18+",
      "dob": "1977-07-06",
      "passport_no": "X6852830",
      "visa_no": null,
      "car_reg": "C217LS",
      "addr_country": "South Korea",
      "addr_display": "90 Orchard Way, 13840 Northvale, South Korea",
      "lang": "uk",
      "created_at": "2026-10-11T06:35:00Z",
      "group_id": null,
      "is_group_leader": false,
      "under18": false,
      "return_reason": null,
      "return_step": null,
      "code": "0771"
    }
  ],
  "next_cursor": "OGtNaFAzZWpwZjN0UDY4WmZHN1J2MGVacGYxcGxjQ3U",
  "total": 15
}
```

### minor alerts

```
desk_minor_alerts({})
```

```json
{
  "result": [
    {
      "ref": "ca236c89c77f6467",
      "token": "eL7Z95VzQ88-n8yfmCXAyrduRPHe-cuC",
      "serial": null,
      "state": "PENDING_REVIEW",
      "first_name": "Lopa",
      "last_name": "Nupali",
      "age_band": "15-17",
      "arrival": "2026-10-11",
      "group_id": null,
      "signer_role": "self",
      "needs_legal_representative": false
    },
    {
      "ref": "a1d891a399ebd3ee",
      "token": "O_cuX7OfIsTdofo7GbngvgJRtJ4rYFKU",
      "serial": null,
      "state": "PENDING_REVIEW",
      "first_name": "Zodo",
      "last_name": "Detiha",
      "age_band": "<15",
      "arrival": "2026-10-11",
      "group_id": null,
      "signer_role": "self",
      "needs_legal_representative": true
    }
  ]
}
```

### groups awaiting members

```
list_groups({"status": "pending"})
```

```json
{
  "result": [
    {
      "slug": "ZduoklDgDrLXR7UQwAjIcpUgiGh1uVd8",
      "status": "PENDING",
      "group_name": "Westfold Hiking Society",
      "leader_first_name": "Rato",
      "leader_last_name": "Tohaul",
      "arrival": "2026-10-24",
      "departure": "2026-10-28",
      "nights": 4,
      "purpose_of_stay": "leisure",
      "expected_members": 1,
      "created_at": "2026-10-11T07:15:00Z",
      "invite_sent_at": "2026-10-11T07:16:00Z",
      "submitted_at": null,
      "member_count": 4,
      "leader_email": null,
      "leader_phone_home": null,
      "leader_phone_stay": null,
      "receiver_email": "rato.tohaul@example.invalid"
    }
  ]
}
```

## 2. Approve the queue after a check

The receptionist has compared the first card with the passport and approves it. The app
assigns the serial `2026-10-11-0001` (first approval for that arrival date) and stores the
address in capitals; the server writes an `admin_audit` row with actor `mcp:stdio` (over HTTP
it would be `mcp:<token subject>`).

The second call is the retry case: same token, `already_approved: true`, same serial, no second
serial minted and no second audit row. The third call shows the state machine refusing: a DRAFT
cannot be approved, and the error names the state and the only action it allows (`SUBMIT`),
which is the guest's, not the desk's.


### approve

```
approve_registration({"token": "lNft8MglJKdrN61RsjARlHn09BY41lgv"})
```

```json
{
  "ref": "b24e527a78982b40",
  "token": "lNft8MglJKdrN61RsjARlHn09BY41lgv",
  "serial": "2026-10-11-0001",
  "approved_at": "2026-10-11T00:24:12Z",
  "already_approved": false
}
```

### approve again (idempotent)

```
approve_registration({"token": "lNft8MglJKdrN61RsjARlHn09BY41lgv"})
```

```json
{
  "ref": "b24e527a78982b40",
  "token": "lNft8MglJKdrN61RsjARlHn09BY41lgv",
  "serial": "2026-10-11-0001",
  "approved_at": "2026-10-11T00:24:12Z",
  "already_approved": true
}
```

### approve a DRAFT (refused)

```
approve_registration({"token": "9-JtWBL_mqLTzl0pnQp0hXI5KgDDOJsS"})
```

ToolError: Error executing tool approve_registration: registration 868c662b4098a92c is DRAFT; allowed actions from DRAFT: SUBMIT


## 3. Return a registration with a reason

A passport number does not match the document. The receptionist returns the card to the
guest with a reason and the wizard page to reopen (`step: 2`, personal details). The record
flips to `RETURNED`; the guest's phone falls back to polling `/status/{token}` and sees the
reason (the SSE wake-up is in-process in the app and cannot be triggered from here; see the
README's limitations). `get_registration` afterwards shows the reason and step on the record,
and the signature provenance is still there: the guest will re-sign on resubmit.


### return

```
return_registration({"token": "HRDr7b2F06_kT2J_n4pDYMzf8ZCr_Z3a", "reason": "Passport number does not match the document shown", "step": 2})
```

```json
{
  "ref": "8fef6b503815da22",
  "token": "HRDr7b2F06_kT2J_n4pDYMzf8ZCr_Z3a",
  "state": "RETURNED",
  "return_step": 2,
  "return_reason": "Passport number does not match the document shown"
}
```

### the guest's record afterwards

```
get_registration({"token": "HRDr7b2F06_kT2J_n4pDYMzf8ZCr_Z3a"})
```

```json
{
  "ref": "8fef6b503815da22",
  "token": "HRDr7b2F06_kT2J_n4pDYMzf8ZCr_Z3a",
  "serial": null,
  "state": "RETURNED",
  "arrival": "2026-10-11",
  "departure": "2026-10-12",
  "nights": 1,
  "purpose_of_stay": "business",
  "last_name": "Fayeve",
  "first_name": "Vema",
  "nationality": "USA",
  "age_band": "18+",
  "dob": "1985-11-16",
  "passport_no": "X5980315",
  "visa_no": null,
  "car_reg": null,
  "addr_country": "United States",
  "addr_display": "32 Bridge Street, 49870 Southbrook, United States",
  "lang": "cs",
  "created_at": "2026-10-11T06:33:00Z",
  "group_id": null,
  "is_group_leader": false,
  "under18": false,
  "return_reason": "Passport number does not match the document shown",
  "return_step": 2,
  "code": "6489",
  "approved_at": null,
  "updated_at": "2026-10-11T00:24:12Z",
  "addr_structured": 1,
  "addr_street": "32 Bridge Street",
  "addr_postcode": "49870",
  "addr_city": "Southbrook",
  "addr_freetext": null,
  "signature": {
    "signer_role": "self",
    "signed_at": "2026-10-11T06:34:00Z",
    "declaration_lang": "en",
    "signer_name": null,
    "signature_png_base64": null
  }
}
```

## 4. Create a group link for a school trip

A primary school is bringing a class in November. The group manager creates the request:
the app validates the dates and the email with its own `group_validation`, the server generates
a portal password, hashes it with the app's argon2 helper and stores only the hash.

The password appears in this result and nowhere else; the server cannot show it again
(`resend_group_invite` rotates it). `email_sent: false` because this run is in synthetic mode,
where the invite is recorded rather than sent; in live mode the app's `group_email.try_send`
runs with the hostel's Gmail settings. The two links are what the leader receives: the portal
(`/g/…`) for the leader, the member self-registration link (`/gm/…`) to forward to parents.


### create_group_link

```
create_group_link({"group_name": "Southbrook Primary — Year 6 trip", "leader_first_name": "Maru", "leader_last_name": "Tovane", "arrival": "2026-11-16", "departure": "2026-11-18", "purpose_of_stay": "leisure", "receiver_email": "m.tovane@example.invalid"})
```

```json
{
  "slug": "_26DQUHgZC7tXNa9GuBQ3cl8k1n14h4H",
  "portal_url": "http://127.0.0.1:8080/g/_26DQUHgZC7tXNa9GuBQ3cl8k1n14h4H",
  "member_url": "http://127.0.0.1:8080/gm/0D0St0Dc9o1cod49IPKhoVSav8tR5P1_",
  "portal_password": "twnsfuwPxuOm",
  "email_sent": false,
  "receiver_email": "m.tovane@example.invalid"
}
```

## 5. Monthly authority export

Start of October: the September registrations go to the foreign police. `month_summary`
shows the state of every arrival month: August is backed up and verified (15 entries), September
has 15 live rows and no backup yet, October and November hold group rows that are already
approved for future arrivals.

`export_authority_report` with the September window builds the app's own XLSX (same columns
as the desk's export: serial, surname, first name, date of birth, nationality, passport,
visa, arrival, departure, nights, purpose, car, address). The bytes are not inlined: the result
carries a `resource_uri`, a `sha256` and an `expires_at`, and the host reads the file through
`resources/read`. The read is private and immediately stale (`ttlMs=0`), as personal data
should be.

The second export passes an explicit token list that includes a pending registration. It is
listed under `skipped` with its state and never reaches the file: only APPROVED rows are
exported, whatever the caller asks for.


### month_summary

```
month_summary({})
```

```json
{
  "result": [
    {
      "period": "2026-11",
      "live_count": 6,
      "entry_count": null,
      "snapshot_name": null,
      "backed_up_at": null,
      "deleted_at": null,
      "verified": false
    },
    {
      "period": "2026-10",
      "live_count": 6,
      "entry_count": null,
      "snapshot_name": null,
      "backed_up_at": null,
      "deleted_at": null,
      "verified": false
    },
    {
      "period": "2026-09",
      "live_count": 15,
      "entry_count": null,
      "snapshot_name": null,
      "backed_up_at": null,
      "deleted_at": null,
      "verified": false
    },
    {
      "period": "2026-08",
      "live_count": 15,
      "entry_count": 15,
      "snapshot_name": "2026-08.db.enc",
      "backed_up_at": "2026-09-01T06:05:00Z",
      "deleted_at": null,
      "verified": true
    }
  ]
}
```

### export

```
export_authority_report({"arrival_from": "2026-09-01", "arrival_to": "2026-09-30", "format": "xlsx"})
```

```json
{
  "export_id": "5ed58a71fcb24364adf3805af44d5ade",
  "resource_uri": "export://xlsx/5ed58a71fcb24364adf3805af44d5ade",
  "format": "xlsx",
  "rows": 15,
  "skipped": [],
  "skipped_detail": [],
  "sha256": "2f210ee291b0f7a94ffa7011696a6428172baba83bc000a4c95aa375cd68df64",
  "generated_at": "2026-10-11T00:24:13Z",
  "expires_at": "2026-10-11T01:24:13Z"
}
```

resources/read export://xlsx/5ed58a71fcb24364adf3805af44d5ade -> 6807 bytes, mime application/vnd.openxmlformats-officedocument.spreadsheetml.sheet, sha256 2f210ee291b0f7a9…, ttlMs=0, cacheScope=private

### export with a pending token in the list

```
export_authority_report({"tokens": ["A5rF-mEntPjKpbu-pCwhHTwSasCx8z72", "V7KXK6yRbckNXouqIgmGdte1XqYIQ7ZW", "8kMhP3ejpf3tP68ZfG7Rv0eZpf1plcCu"], "format": "pdf"})
```

```json
{
  "export_id": "447538d4df3f48d4ac1b3c6de252cb54",
  "resource_uri": "export://pdf/447538d4df3f48d4ac1b3c6de252cb54",
  "format": "pdf",
  "rows": 2,
  "skipped": [
    "8kMhP3ejpf3tP68ZfG7Rv0eZpf1plcCu"
  ],
  "skipped_detail": [
    "8kMhP3ejpf3tP68ZfG7Rv0eZpf1plcCu: PENDING_REVIEW"
  ],
  "sha256": "97ba89da72db3b26a81d235d451575b678c917ec01b2bea0509a0a8e05088148",
  "generated_at": "2026-10-11T00:24:13Z",
  "expires_at": "2026-10-11T01:24:13Z"
}
```

## 6. GDPR erasure request with confirmation

A former guest asks for erasure. The admin's agent calls `erase_registration`. The server
does not delete: it returns an `InputRequiredResult` (protocol 2026-07-28) with a form
elicitation for the host to show, and a sealed `request_state`. The message names the serial,
the arrival date, the legal position (Act 326/1999 requires the hostel to keep registrations;
erasure is limited to records no longer required) and says it cannot be undone.

The host renders the form, the human ticks *Confirm*, and the host retries the same call
with the answer and the echoed state. The SDK unseals the state and checks that it was minted
for this tool, these arguments and this principal, then the handler acts: one row deleted
(the signature cascades), one audit row. Had the retry carried a different token, the round
would have failed with `-32602 Invalid or expired requestState` before the handler ran
(`tests/test_mrtr.py`). The second erasure shows a decline: a non-error `cancelled` result and
nothing deleted. The audit trail shows the delete and the two exports, with refs, counts and ids,
never names or raw tokens.


```
erase_registration({"token": "fuzv74fIwBao0uGnE6JvXvaZNw5tfmlN"})
```

First round: `InputRequiredResult`

```json
{
  "input_requests": {
    "confirm": {
      "mode": "form",
      "message": "Erase registration 2026-09-11-0001 (arrival 2026-09-11, state APPROVED)? Legal basis is Act 326/1999; erasure is limited to records no longer required. This cannot be undone.",
      "requestedSchema": {
        "properties": {
          "confirm": {
            "type": "boolean",
            "title": "Confirm",
            "description": "Tick to proceed. Leave unticked to cancel."
          }
        },
        "required": [
          "confirm"
        ],
        "type": "object"
      }
    }
  },
  "request_state": "v1.DMAjxd3DGzL7v2XSKYT3t… (sealed, 302 chars)"
}
```

### second round: the human accepted with confirm=true

```
erase_registration({"token": ...}, input_responses={"confirm": accept/confirm=true}, request_state=<echoed>)
```

```json
{
  "result": {
    "ref": "ca9323dddecef667",
    "token": "fuzv74fIwBao0uGnE6JvXvaZNw5tfmlN",
    "deleted": 1,
    "removed_group": null
  }
}
```

### the human declines

```
erase_registration({"token": ...}, input_responses={"confirm": decline}, request_state=<echoed>)
```

```json
{
  "result": {
    "cancelled": true,
    "reason": "declined",
    "message": "Registration 2026-09-13-0001 was not erased."
  }
}
```

### audit trail

```
audit_log({"limit": 3})
```

```json
{
  "result": [
    {
      "id": 11,
      "at": "2026-10-11T00:24:13Z",
      "actor": "mcp:stdio",
      "action": "delete",
      "period": "2026-09",
      "detail": "registration ca9323dddecef667 serial=2026-09-11-0001 erased"
    },
    {
      "id": 10,
      "at": "2026-10-11T00:24:13Z",
      "actor": "mcp:stdio",
      "action": "export",
      "period": "2026-08",
      "detail": "2 rows pdf export 447538d4df3f48d4ac1b3c6de252cb54 sha256 97ba89da72db; skipped 1"
    },
    {
      "id": 9,
      "at": "2026-10-11T00:24:13Z",
      "actor": "mcp:stdio",
      "action": "export",
      "period": "2026-09",
      "detail": "15 rows xlsx export 5ed58a71fcb24364adf3805af44d5ade sha256 2f210ee291b0; skipped 0"
    }
  ]
}
```

## 7. The refused purge

`purge_month` is the dangerous one, so its guard runs *before* anyone is asked. Four
refusals, each a `ToolError` the model can read and none producing a confirmation request:

1. No `BACKUP_ENCRYPTION_KEY` on this machine: nothing can be purged at all.
2. Key set, but September was never backed up.
3. Key set, August has a verified backup row (15 entries, count matches), but the encrypted
   snapshot `2026-08.db.enc` is not on this machine, so the guard cannot decrypt and re-verify it.
4. July: a verified backup row exists (12 entries archived last season) but no approved rows
   remain, so there is nothing to purge (Review Focus 3).

Only when every check passes does the human see a question, and that question quotes the entry
count, the number of group requests the app removes with the month, and the snapshot checksum (`tests/test_purge_guard.py::test_passing_guard_asks_with_count_and_sha`).


### no backup key on this machine

```
purge_month({"period": "2026-08"})
```

ToolError: Error executing tool purge_month: backup is not configured on this server (BACKUP_ENCRYPTION_KEY unset); nothing can be purged


### key set, month never backed up

```
purge_month({"period": "2026-09"})
```

ToolError: Error executing tool purge_month: no verified backup is logged for 2026-09; run the month backup in the admin panel first


### key set, verified row, but the snapshot file is not here

```
purge_month({"period": "2026-08"})
```

ToolError: Error executing tool purge_month: the snapshot file 2026-08.db.enc is not on this machine; the purge needs the encrypted snapshot to re-verify


### a month with a verified backup row but nothing left to purge

```
purge_month({"period": "2026-07"})
```

ToolError: Error executing tool purge_month: nothing to purge: 2026-07 has no approved registrations


## 8. The receptionist token that cannot see passport numbers

The same approved record, read with two tokens. With `desk:read` alone the caller does not even
hold the registration token (in the app that token opens the guest wizard without a login); it
addresses the record by its `ref`. The result: `token` is `null`, the passport is `•••61`, `dob` is
`null` and only `age_band: "<15"` remains, the address shrinks to its country, the signer's name
is absent and the signature image is not returned even though `include_signature=true` was asked
for. The provenance (`signer_role`, `signed_at`) is kept, because the desk needs to know a legal
representative signed. The same token cannot call `month_summary`: the error names the missing
scope.

With `desk:read pii:read`: the raw token, date of birth, full passport number, the address in
capitals as the app stores it after approval, the representative's name and the PNG. The
difference is one scope on the token, and the server enforces it on every field through one
table (`redact.py`).


### token scopes: desk:read (the caller only ever holds the ref)

```
get_registration({"token": "e604828acd5596fc", "include_signature": true})
```

```json
{
  "ref": "e604828acd5596fc",
  "token": null,
  "serial": "2026-08-15-0001",
  "state": "APPROVED",
  "arrival": "2026-08-15",
  "departure": "2026-08-18",
  "nights": 3,
  "purpose_of_stay": "leisure",
  "last_name": "Peyede",
  "first_name": "Rere",
  "nationality": "CAN",
  "age_band": "<15",
  "dob": null,
  "passport_no": "•••61",
  "visa_no": null,
  "car_reg": null,
  "addr_country": "CANADA",
  "addr_display": null,
  "lang": "es",
  "created_at": "2026-08-15T09:01:00Z",
  "group_id": null,
  "is_group_leader": false,
  "under18": true,
  "return_reason": null,
  "return_step": null,
  "code": "6414",
  "approved_at": "2026-08-15T09:03:00Z",
  "updated_at": "2026-08-15T09:03:00Z",
  "addr_structured": null,
  "addr_street": null,
  "addr_postcode": null,
  "addr_city": null,
  "addr_freetext": null,
  "signature": {
    "signer_role": "legal_representative",
    "signed_at": "2026-08-15T09:02:00Z",
    "declaration_lang": "en",
    "signer_name": null,
    "signature_png_base64": null
  }
}
```

### same token, month_summary

```
month_summary({})
```

ToolError: Error executing tool month_summary: missing scope admin:read; this token cannot call month_summary


### token scopes: desk:read pii:read

```
get_registration({"token": "B5GCPYu9X0n3rtxFHHfoErXztwHIW47p", "include_signature": true})
```

```json
{
  "ref": "e604828acd5596fc",
  "token": "B5GCPYu9X0n3rtxFHHfoErXztwHIW47p",
  "serial": "2026-08-15-0001",
  "state": "APPROVED",
  "arrival": "2026-08-15",
  "departure": "2026-08-18",
  "nights": 3,
  "purpose_of_stay": "leisure",
  "last_name": "Peyede",
  "first_name": "Rere",
  "nationality": "CAN",
  "age_band": "<15",
  "dob": "2014-02-11",
  "passport_no": "X6184261",
  "visa_no": null,
  "car_reg": null,
  "addr_country": "CANADA",
  "addr_display": "8 LINDEN AVENUE, 37760 ASHFORD, CANADA",
  "lang": "es",
  "created_at": "2026-08-15T09:01:00Z",
  "group_id": null,
  "is_group_leader": false,
  "under18": true,
  "return_reason": null,
  "return_step": null,
  "code": "6414",
  "approved_at": "2026-08-15T09:03:00Z",
  "updated_at": "2026-08-15T09:03:00Z",
  "addr_structured": 1,
  "addr_street": "8 LINDEN AVENUE",
  "addr_postcode": "37760",
  "addr_city": "ASHFORD",
  "addr_freetext": null,
  "signature": {
    "signer_role": "legal_representative",
    "signed_at": "2026-08-15T09:02:00Z",
    "declaration_lang": "en",
    "signer_name": "Fawa Peyede",
    "signature_png_base64": "iVBORw0KGgoAAAANSUhE…"
  }
}
```
