"""Read-only comparison of workbook settings against the configured SageOps database.

Run with:
    python -m scripts.reconcile_sageops_settings /path/to/workbook.xlsx
"""
from __future__ import annotations

import argparse
from pathlib import Path

from sqlalchemy import select

from app.operations_db import SessionLocal
from app.operations_models import Setting
from scripts.import_sageops_workbook import expected_settings, load


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook", type=Path)
    args = parser.parse_args()

    if SessionLocal is None:
        parser.error("DATABASE_URL is not configured")

    data, _ = load(args.workbook)
    expected = expected_settings(data)

    # Session is used only for SELECTs. This command never inserts, updates, or deletes.
    with SessionLocal() as db:
        existing = {
            item.key: item.value
            for item in db.scalars(select(Setting)).all()
        }

    print("\nSAGEOPS SETTINGS RECONCILIATION (READ ONLY)")
    print("-------------------------------------------")
    print(f"Workbook configuration rows: {len(data['Config'])}")
    print(f"Settings that import would write: {len(expected)}")
    print()

    counts = {"NEW": 0, "MATCH": 0, "DIFFERS": 0}
    for key, value in sorted(expected.items()):
        if key not in existing:
            status = "NEW"
        elif existing[key] == value:
            status = "MATCH"
        else:
            status = "DIFFERS"
        counts[status] += 1
        print(f"{key:42} {status}")

    untouched = sorted(set(existing) - set(expected))
    print("\nExisting settings not touched by this workbook:")
    if untouched:
        for key in untouched:
            print(f"- {key}")
    else:
        print("- None")

    print("\nSUMMARY")
    print(f"New:      {counts['NEW']}")
    print(f"Matching: {counts['MATCH']}")
    print(f"Different:{counts['DIFFERS']}")
    print("\nNo database records were changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
