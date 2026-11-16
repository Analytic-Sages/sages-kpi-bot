"""Offline KPI setup diagnostic for the one-time Excel migration source."""
from __future__ import annotations

import argparse
from pathlib import Path

from scripts.import_sageops_workbook import load

SETUP_FIELDS = (
    "Team", "Role", "Definition / Measurement", "Direction", "Frequency",
    "Target", "Unit", "Data Source", "Active?",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("workbook", type=Path)
    path = parser.parse_args().workbook
    data, _ = load(path)
    rows = data["KPI Register"]
    incomplete = []
    unassigned = 0
    for row in rows:
        missing = [name for name in SETUP_FIELDS if not row.get(name)]
        unassigned += not bool(row.get("Owner Person ID"))
        if missing:
            incomplete.append((row["_row"], missing))
    print("Offline workbook KPI diagnostic (read-only)")
    print(f"Configured KPI rows: {len(rows)}")
    print(f"Incomplete setup rows: {len(incomplete)}")
    print(f"Unassigned team KPI rows: {unassigned}")
    for row_number, fields in incomplete:
        print(f"Row {row_number}: missing {', '.join(fields)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
