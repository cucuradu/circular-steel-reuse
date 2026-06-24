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
import json
import sys
from pathlib import Path

from .core.sections import load_default_catalog
from .extraction_review import extraction_review
from .inventory_sheet import load_model_file
from .schema import ExtractedModel, ExtractionError


def summarize(model: ExtractedModel) -> dict:
    """Counts useful for validating an extraction (delegates to the review core)."""
    rv = extraction_review(model, load_default_catalog())
    return {
        "total": rv.total,
        "roles": rv.roles,
        "mapped": rv.mapped,
        "unknown": rv.total - rv.mapped,   # legacy semantics: fuzzy counts as not-mapped here
        "with_coords": rv.with_coords,
        "columns": rv.columns,
        "columns_with_coords": rv.columns_with_coords,
    }


def _schedule_row_count(path: str | Path) -> int:
    """Count non-empty data rows in a CSV schedule (assumes a single header row)."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        next(reader, None)  # header
        return sum(1 for row in reader if any(cell.strip() for cell in row))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Validate a steelreuse extraction JSON")
    ap.add_argument("json", help="extraction JSON written by the pyRevit/IFC extractor, or a "
                                  ".csv/.xlsx inventory spreadsheet (see steelreuse --inventory-template)")
    ap.add_argument("--expect", type=int, default=None, help="expected total member count")
    ap.add_argument("--schedule", default=None,
                    help="Revit schedule CSV; its data-row count is compared to the member count")
    ap.add_argument("--pda", default=None, help="audit CSV merged onto members before review")
    ap.add_argument("--report", default=None, help="write the problem-report HTML here")
    ap.add_argument("--pda-report", default=None, help="write the PDA QA-report HTML here")
    ap.add_argument("--review-json", default=None, help="write the ReviewModel JSON here")
    ap.add_argument("--pda-out", default=None,
                    help="write the audit CSV (--pda column order) here")
    ap.add_argument("--survey-template", default=None,
                    help="write a PDA survey template CSV (one row per member, audit columns blank)")
    ap.add_argument("--debug", action="store_true", help="show the full traceback on error")
    args = ap.parse_args(argv)

    try:
        model = load_model_file(args.json, "donor")
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

    if args.report or args.pda_report or args.review_json or args.pda_out:
        from .review_view import pda_report_csv, render_pda_report, render_problem_report

        review = extraction_review(model, load_default_catalog(), pda=args.pda).to_dict()
        if args.report:
            Path(args.report).write_text(render_problem_report(review), encoding="utf-8")
        if args.pda_report:
            Path(args.pda_report).write_text(render_pda_report(review), encoding="utf-8")
        if args.review_json:
            Path(args.review_json).write_text(json.dumps(review, indent=2), encoding="utf-8")
        if args.pda_out:
            Path(args.pda_out).write_text(pda_report_csv(review), encoding="utf-8")

    if args.survey_template:
        from .survey import survey_template_csv
        Path(args.survey_template).write_text(survey_template_csv(model), encoding="utf-8")

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
