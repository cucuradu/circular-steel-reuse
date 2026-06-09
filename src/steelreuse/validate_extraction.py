"""Validate an extraction JSON and (optionally) check its member count against a Revit schedule.

Use this for the Phase-1 sanity check: confirm the extractor wrote what the model contains.

    python -m steelreuse.validate_extraction donor.json
    python -m steelreuse.validate_extraction donor.json --expect 1016
    python -m steelreuse.validate_extraction donor.json --schedule revit_framing.csv

Without an expectation it just prints a summary (counts by role, mapped vs unknown, how many carry
coordinates). With --expect N or --schedule CSV it compares the total member count and exits non-zero
on a mismatch, so it can gate a CI/manual check.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

from .core.sections import load_default_catalog, resolve_members
from .schema import ExtractedModel, ExtractionError


def summarize(model: ExtractedModel) -> dict:
    """Counts useful for validating an extraction (resolves sections against the default catalog)."""
    roles = Counter(m.role for m in model.members)
    n_cols = roles.get("column", 0)
    cols_with_xyz = sum(1 for m in model.members if m.role == "column" and m.start_xyz)
    with_xyz = sum(1 for m in model.members if m.start_xyz and m.end_xyz)
    resolve_members(model.members, load_default_catalog())  # sets m.section where it maps
    mapped = sum(1 for m in model.members if m.section)
    return {
        "total": len(model.members),
        "roles": dict(roles),
        "mapped": mapped,
        "unknown": len(model.members) - mapped,
        "with_coords": with_xyz,
        "columns": n_cols,
        "columns_with_coords": cols_with_xyz,
    }


def _schedule_row_count(path: str | Path) -> int:
    """Count non-empty data rows in a CSV schedule (assumes a single header row)."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        next(reader, None)  # header
        return sum(1 for row in reader if any(cell.strip() for cell in row))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Validate a steelreuse extraction JSON")
    ap.add_argument("json", help="extraction JSON written by the pyRevit/IFC extractor")
    ap.add_argument("--expect", type=int, default=None, help="expected total member count")
    ap.add_argument("--schedule", default=None,
                    help="Revit schedule CSV; its data-row count is compared to the member count")
    ap.add_argument("--debug", action="store_true", help="show the full traceback on error")
    args = ap.parse_args(argv)

    try:
        model = ExtractedModel.load(args.json)
        s = summarize(model)
    except ExtractionError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001 — friendly message, not a traceback
        if args.debug:
            raise
        print(f"error: {type(e).__name__}: {e}\n(run with --debug for the full traceback)",
              file=sys.stderr)
        return 1

    print(f"{args.json}: {s['total']} members  {s['roles']}")
    print(f"  sections: {s['mapped']} mapped, {s['unknown']} unknown")
    print(f"  coordinates: {s['with_coords']}/{s['total']} members "
          f"({s['columns_with_coords']}/{s['columns']} columns) "
          f"— frame analysis needs column coordinates")

    expected = args.expect
    if expected is None and args.schedule:
        expected = _schedule_row_count(args.schedule)
        print(f"  schedule {args.schedule}: {expected} data rows")
    if expected is None:
        return 0

    if s["total"] == expected:
        print(f"OK: member count matches expected {expected}")
        return 0
    print(f"MISMATCH: extracted {s['total']} != expected {expected} "
          f"(difference {s['total'] - expected:+d})", file=sys.stderr)
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
