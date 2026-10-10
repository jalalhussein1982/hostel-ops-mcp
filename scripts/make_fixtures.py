"""Rebuild fixtures/synthetic.db and fixtures/synthetic.json from the fixed seed.

    uv run python scripts/make_fixtures.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hostel_ops_mcp.synth import build_synthetic_db, fixture_digest  # noqa: E402


def main() -> None:
    db = ROOT / "fixtures" / "synthetic.db"
    summary = build_synthetic_db(db, summary_path=ROOT / "fixtures" / "synthetic.json")
    print(f"wrote {db} ({db.stat().st_size} bytes)")
    print(f"  states: {summary.counts}, group members: {summary.group_members}")
    print(f"  groups: {summary.groups}")
    print(f"  digest: {fixture_digest(db)}")


if __name__ == "__main__":
    main()
