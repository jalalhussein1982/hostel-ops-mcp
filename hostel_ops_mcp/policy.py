"""Policy texts served as resources: the scope matrix and the retention position.

The retention text is assembled from the app's own privacy notice (``app/i18n/en.json``,
``privacy_*`` keys) so the server quotes the hostel's words rather than paraphrasing them,
plus a description of the purge guard this server enforces.
"""

from __future__ import annotations

import html
import json
import re

from . import _app, guards, redact

_TAG = re.compile(r"<[^>]+>")


def _strip(text: str) -> str:
    text = re.sub(r"</li>\s*", "\n", text)
    text = re.sub(r"<li>", "- ", text)
    text = re.sub(r"</p>\s*", "\n\n", text)
    text = _TAG.sub("", text)
    return html.unescape(re.sub(r"\n{3,}", "\n\n", text)).strip()


def privacy_catalog() -> dict[str, str]:
    path = _app.VENDOR / "app" / "i18n" / "en.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if k.startswith("privacy_")}


def roles_text() -> str:
    return (guards.roles_markdown() + "\n\n# Field policy (`redact.py`)\n\n"
            "Columns not listed are returned at every scope.\n\n" + redact.policy_markdown() + "\n")


def retention_text() -> str:
    cat = privacy_catalog()
    sections = [
        ("privacy_basis_title", "privacy_basis_body"),
        ("privacy_data_title", "privacy_data_body"),
        ("privacy_signature_title", "privacy_signature_body"),
        ("privacy_retention_title", "privacy_retention_body"),
        ("privacy_rights_title", "privacy_rights_body"),
    ]
    out = ["# Retention and erasure — in the hostel's own words", "",
           f"Source: the app's privacy notice ({cat.get('privacy_updated', '')}).", ""]
    for title_key, body_key in sections:
        if body_key in cat:
            out += [f"## {_strip(cat.get(title_key, title_key))}", "", _strip(cat[body_key]), ""]
    out += [
        "## What this server enforces on top",
        "",
        "- `erase_registration` erases one record only after a human confirms through the host "
        "(Multi Round-Trip Request). The confirmation text repeats the legal basis above: erasure "
        "is limited to records no longer required.",
        "- `purge_month` deletes one arrival month's APPROVED records only when the app's delete "
        "guard passes: backup configured, a verified backup row for the month, live rows present, "
        "live count equal to the backed-up count, and the encrypted snapshot on disk, decrypting "
        "and re-verifying. The guard runs before the human is asked, so the human only ever "
        "confirms a purge that is actually allowed; the question quotes the entry count and the "
        "snapshot checksum.",
        "- Every erase, purge, edit and export writes an `admin_audit` row with the actor "
        "`mcp:<subject>`; audit rows name fields and counts, never values.",
        "",
    ]
    return "\n".join(out)
