"""Run the eight documented scenarios against a scratch copy of the synthetic DB and print
the real tool calls and outputs (docs/use-cases.md is written from this transcript).

    uv run python scripts/capture_use_cases.py /tmp/scratch-dir
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def show(title: str, call: str, result) -> None:
    print(f"\n### {title}\n\n```\n{call}\n```\n")
    if hasattr(result, "is_error") and result.is_error:
        print(f"ToolError: {result.content[0].text}\n")
        return
    data = result.structured_content if hasattr(result, "structured_content") else result
    print("```json\n" + json.dumps(data, indent=2, ensure_ascii=False) + "\n```")


async def main(scratch: Path) -> None:
    from mcp import Client
    from mcp.types import ElicitResult, InputRequiredResult

    from hostel_ops_mcp import _app

    db = scratch / "use-cases.db"
    shutil.copy(ROOT / "fixtures" / "synthetic.db", db)
    os.environ["EXPORT_DIR"] = str(scratch / "exports")
    _app.load(db)
    planted = json.loads((ROOT / "fixtures" / "synthetic.json").read_text())
    from hostel_ops_mcp.server import mcp

    os.environ["STDIO_SCOPES"] = "desk:read,desk:write,groups:read,groups:write,export,pii:read,admin:read,admin:write,admin:erase"

    async def yes(ctx, params):
        print(f"\n> The host shows the human: **{params.message}**\n> Human ticks *Confirm* and accepts.\n")
        return ElicitResult(action="accept", content={"confirm": True})

    async with Client(mcp, raise_exceptions=True, elicitation_callback=yes) as c:
        print("## 1. Morning desk briefing")
        p = await c.get_prompt("morning_desk_briefing", {})
        print("\nPrompt text:\n\n> " + p.messages[0].content.text.replace("\n", "\n> "))
        show("pending queue, first page", 'list_pending_registrations({"limit": 3})',
             await c.call_tool("list_pending_registrations", {"limit": 3}))
        show("minor alerts", "desk_minor_alerts({})", await c.call_tool("desk_minor_alerts", {}))
        show("groups awaiting members", 'list_groups({"status": "pending"})',
             await c.call_tool("list_groups", {"status": "pending"}))

        print("\n## 2. Approve the queue after a check")
        tok = planted["tokens"]["PENDING_REVIEW"][0]
        show("approve", f'approve_registration({{"token": "{tok}"}})', await c.call_tool("approve_registration", {"token": tok}))
        show("approve again (idempotent)", f'approve_registration({{"token": "{tok}"}})', await c.call_tool("approve_registration", {"token": tok}))
        d = planted["tokens"]["DRAFT"][1]
        show("approve a DRAFT (refused)", f'approve_registration({{"token": "{d}"}})', await c.call_tool("approve_registration", {"token": d}))

        print("\n## 3. Return a registration with a reason")
        tok = planted["tokens"]["PENDING_REVIEW"][1]
        show("return", f'return_registration({{"token": "{tok}", "reason": "Passport number does not match the document shown", "step": 2}})',
             await c.call_tool("return_registration", {"token": tok, "reason": "Passport number does not match the document shown", "step": 2}))
        show("the guest's record afterwards", f'get_registration({{"token": "{tok}"}})', await c.call_tool("get_registration", {"token": tok}))

        print("\n## 4. Create a group link for a school trip")
        args = {"group_name": "Southbrook Primary — Year 6 trip", "leader_first_name": "Maru", "leader_last_name": "Tovane",
                "arrival": "2026-11-16", "departure": "2026-11-18", "purpose_of_stay": "leisure", "receiver_email": "m.tovane@example.invalid"}
        show("create_group_link", "create_group_link(" + json.dumps(args, ensure_ascii=False) + ")", await c.call_tool("create_group_link", args))

        print("\n## 5. Monthly authority export")
        show("month_summary", "month_summary({})", await c.call_tool("month_summary", {}))
        r = await c.call_tool("export_authority_report", {"arrival_from": "2026-09-01", "arrival_to": "2026-09-30", "format": "xlsx"})
        show("export", 'export_authority_report({"arrival_from": "2026-09-01", "arrival_to": "2026-09-30", "format": "xlsx"})', r)
        res = await c.read_resource(r.structured_content["resource_uri"])
        import base64, hashlib
        data = base64.b64decode(res.contents[0].blob)
        print(f"\nresources/read {r.structured_content['resource_uri']} -> {len(data)} bytes, mime {res.contents[0].mime_type}, "
              f"sha256 {hashlib.sha256(data).hexdigest()[:16]}…, ttlMs={res.ttl_ms}, cacheScope={res.cache_scope}")
        mixed = planted["tokens"]["APPROVED"][:2] + [planted["tokens"]["PENDING_REVIEW"][2]]
        show("export with a pending token in the list", "export_authority_report(" + json.dumps({"tokens": mixed, "format": "pdf"}) + ")",
             await c.call_tool("export_authority_report", {"tokens": mixed, "format": "pdf"}))

        print("\n## 6. GDPR erasure request with confirmation")
        tok = planted["tokens"]["APPROVED"][20]   # a September guest, so the August backup stays intact for scenario 7
        first = await c.session.call_tool("erase_registration", {"token": tok}, allow_input_required=True)
        assert isinstance(first, InputRequiredResult)
        print(f"\n```\nerase_registration({{\"token\": \"{tok}\"}})\n```\n\nFirst round: `InputRequiredResult`\n")
        print("```json\n" + json.dumps({"input_requests": {"confirm": first.input_requests["confirm"].params.model_dump(by_alias=True, exclude_none=True)},
                                        "request_state": first.request_state[:24] + "… (sealed, " + str(len(first.request_state)) + " chars)"}, indent=2, ensure_ascii=False) + "\n```")
        done = await c.session.call_tool("erase_registration", {"token": tok},
                                         input_responses={"confirm": ElicitResult(action="accept", content={"confirm": True})},
                                         request_state=first.request_state, allow_input_required=True)
        show("second round: the human accepted with confirm=true", 'erase_registration({"token": ...}, input_responses={"confirm": accept/confirm=true}, request_state=<echoed>)', done)
        tok2 = planted["tokens"]["APPROVED"][21]
        first2 = await c.session.call_tool("erase_registration", {"token": tok2}, allow_input_required=True)
        declined = await c.session.call_tool("erase_registration", {"token": tok2},
                                             input_responses={"confirm": ElicitResult(action="decline")},
                                             request_state=first2.request_state, allow_input_required=True)
        show("the human declines", 'erase_registration({"token": ...}, input_responses={"confirm": decline}, request_state=<echoed>)', declined)
        show("audit trail", 'audit_log({"limit": 3})', await c.call_tool("audit_log", {"limit": 3}))

        print("\n## 7. The refused purge")
        os.environ.pop("BACKUP_ENCRYPTION_KEY", None)
        show("no backup key on this machine", 'purge_month({"period": "2026-08"})', await c.call_tool("purge_month", {"period": "2026-08"}))
        from cryptography.fernet import Fernet
        os.environ["BACKUP_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
        show("key set, month never backed up", 'purge_month({"period": "2026-09"})', await c.call_tool("purge_month", {"period": "2026-09"}))
        show("key set, verified row, but the snapshot file is not here", 'purge_month({"period": "2026-08"})', await c.call_tool("purge_month", {"period": "2026-08"}))
        # July was archived and purged last season (see the audit row); its verified backup row still exists.
        _app.current().repo.backup_log_upsert("2026-07", 12, "2026-07.db.enc", "3b1f" + "0" * 60, "2026-08-01T06:00:00Z", verified=1)
        show("a month with a verified backup row but nothing left to purge", 'purge_month({"period": "2026-07"})',
             await c.call_tool("purge_month", {"period": "2026-07"}))

    print("\n## 8. The receptionist token that cannot see passport numbers")
    tok = planted["minor_tokens"]["approved_under15_legal_rep"]
    os.environ["STDIO_SCOPES"] = "desk:read"
    async with Client(mcp, raise_exceptions=True) as c:
        show("token scopes: desk:read", f'get_registration({{"token": "{tok}", "include_signature": true}})',
             await c.call_tool("get_registration", {"token": tok, "include_signature": True}))
        show("same token, month_summary", "month_summary({})", await c.call_tool("month_summary", {}))
    os.environ["STDIO_SCOPES"] = "desk:read,pii:read"
    async with Client(mcp, raise_exceptions=True) as c:
        r = await c.call_tool("get_registration", {"token": tok, "include_signature": True})
        sc = dict(r.structured_content)
        sc["signature"] = dict(sc["signature"]); sc["signature"]["signature_png_base64"] = sc["signature"]["signature_png_base64"][:20] + "…"
        show("token scopes: desk:read pii:read", f'get_registration({{"token": "{tok}", "include_signature": true}})', sc)

    # ---- eval answers -------------------------------------------------------------------
    print("\n## EVAL ANSWERS (read-only, computed from the pristine fixture)")
    shutil.copy(ROOT / "fixtures" / "synthetic.db", db)
    _app.load(db)
    os.environ["STDIO_SCOPES"] = "desk:read,groups:read,admin:read,pii:read"
    async with Client(mcp, raise_exceptions=True) as c:
        page = (await c.call_tool("list_pending_registrations", {"limit": 100})).structured_content
        print("pending total:", page["total"], "| under18:", sum(i["under18"] for i in page["items"]),
              "| arriving 2026-10-11:", sum(i["arrival"] == "2026-10-11" for i in page["items"]),
              "| business:", sum(i["purpose_of_stay"] == "business" for i in page["items"]))
        alerts = (await c.call_tool("desk_minor_alerts", {})).structured_content["result"]
        print("alerts:", [(a["first_name"], a["last_name"], a["age_band"], a["needs_legal_representative"]) for a in alerts])
        months = (await c.call_tool("month_summary", {})).structured_content["result"]
        print("months:", [(m["period"], m["live_count"], m["entry_count"], m["verified"]) for m in months])
        groups = (await c.call_tool("list_groups", {"status": "all"})).structured_content["result"]
        print("groups:", [(g["group_name"], g["status"], g["arrival"], g["member_count"]) for g in groups])
        for g in groups:
            d = (await c.call_tool("get_group", {"slug": g["slug"]})).structured_content
            print("  ", g["group_name"], "total", d["total"], "filled", d["filled"], "dups", d["duplicates"], "attention", d["attention_ids"])
        aug = (await c.call_tool("list_approved_registrations", {"arrival_from": "2026-08-01", "arrival_to": "2026-08-31", "limit": 100})).structured_content
        nat = {}
        for i in aug["items"]:
            nat[i["nationality"]] = nat.get(i["nationality"], 0) + 1
        print("aug approved:", aug["total"], "nationalities:", sorted(nat.items(), key=lambda kv: -kv[1])[:4])
        allap = (await c.call_tool("list_approved_registrations", {"limit": 100})).structured_content
        print("approved visible total:", allap["total"], "| of which group rows:", sum(1 for i in allap["items"] if i["group_id"]))
        audit = (await c.call_tool("audit_log", {"limit": 5})).structured_content["result"]
        print("audit:", [(a["actor"], a["action"], a["period"]) for a in audit])
        serials = sorted(i["serial"] for i in aug["items"])
        print("aug serial range:", serials[0], "…", serials[-1])


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/hostel-ops-capture")
    out.mkdir(parents=True, exist_ok=True)
    asyncio.run(main(out))
