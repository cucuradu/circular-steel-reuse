"""CLI entry point for the standalone reuse value + suitability report (no demand model needed).

    steelreuse-value-case --donor donor.json
    steelreuse-value-case --demo
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .core.audit import apply_audit, load_audit_csv
from .core.value_case import MarketParams, value_case
from .resources import sample_path
from .schema import ExtractedModel
from .writeback import build_value_case_writeback


def _fmt(v: float, decimals: int = 2) -> str:
    return f"{v:,.{decimals}f}"


def _print_table(result) -> None:
    header = (
        f"{'ID':<20} {'Section':<12} {'Grade':<7} {'kg':>7} "
        f"{'Scrap':>8} {'Reclaim':>9} {'Premium':>9} {'CO2 kg':>8} "
        f"{'Verify':<13} {'Verdict':<7}"
    )
    sep = "-" * len(header)
    print(header)
    print(sep)
    for r in result.rows:
        print(
            f"{r.id:<20} {(r.section or '?'):<12} {(r.grade or '?'):<7} {_fmt(r.mass_kg, 1):>7} "
            f"{_fmt(r.scrap_value_gbp):>8} {_fmt(r.reclaimed_value_gbp):>9} "
            f"{_fmt(r.reuse_premium_gbp):>9} {_fmt(r.co2_saved_kg, 1):>8} "
            f"{(r.verification_status or '-'):<13} {r.verdict:<7}"
        )
    print(sep)
    reusable = result.reuse_count + result.review_count
    print(
        f"  REUSE {result.reuse_count}  REVIEW {result.review_count}  SCRAP {result.scrap_count}"
        f"  |  {reusable} reusable member(s), {_fmt(result.reusable_mass_kg / 1000.0, 2)} t"
    )
    print(
        f"  Reuse prize: GBP {_fmt(result.total_reclaimed_value_gbp)} reclaimed value "
        f"(GBP {_fmt(result.total_reuse_premium_gbp)} above scrap)  |  "
        f"CO2 avoided vs new: {_fmt(result.total_co2_saved_kg, 1)} kg"
    )
    if result.review_count:
        print(f"  {result.review_count} member(s) need grade/condition verification before reuse "
              f"(see REVIEW rows).")
    if result.skipped_total:
        detail = ", ".join(f"{k}: {v}" for k, v in sorted((result.skipped_breakdown or {}).items()))
        print(f"  Skipped {result.skipped_total} not-assessed member(s) ({detail}). "
              f"Use --include-unmapped to list unrecognised sections.")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="steelreuse-value-case",
        description="Per-member reuse value + suitability for a donor model (no demand model needed).",
    )
    ap.add_argument("--demo", action="store_true",
                    help="run on the bundled sample donor model")
    ap.add_argument("--donor", help="donor extraction JSON")
    ap.add_argument("--pda", help="pre-demolition-audit CSV "
                                  "(id,condition_grade,verification_status,...)")
    ap.add_argument("--include-unverified", action="store_true",
                    help="admit unverified donors at a conservative knockdown")
    ap.add_argument("--include-unmapped", action="store_true",
                    help="list members whose section was not recognised (open-web joists, plates, "
                         "etc.) instead of skipping them; they cannot be valued or checked")
    ap.add_argument("--knockdown", type=float, default=1.0,
                    help="default f_y knockdown for un-audited donors [1.0]")
    ap.add_argument("--scrap-price", type=float, default=240.0,
                    help="scrap steel price GBP/t [240]")
    ap.add_argument("--reclaimed-price", type=float, default=950.0,
                    help="reclaimed structural steel price GBP/t [950]")
    ap.add_argument("--co2-price", type=float, default=0.0,
                    help="carbon price GBP/tCO2e for the informational co2_value column [0]")
    ap.add_argument("--top", type=int, default=None,
                    help="limit console output to top N rows")
    ap.add_argument("--out-writeback", metavar="JSON",
                    help="write pyRevit-ready apply JSON (reuse/review/scrap colours + passport data)")
    ap.add_argument("--out-json", metavar="JSON",
                    help="write full ValueCaseResult as JSON")
    ap.add_argument("--out-csv", metavar="CSV",
                    help="write the ranked passport table as CSV")
    ap.add_argument("--debug", action="store_true",
                    help="show full traceback on error")
    return ap


def _write_csv(path: str, result) -> None:
    import csv

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "id", "section", "grade", "length_mm", "mass_kg",
        "scrap_value_gbp", "reclaimed_value_gbp", "reuse_premium_gbp",
        "co2_saved_kg", "co2_value_gbp", "reuse_score",
        "verification_status", "condition_grade", "verdict", "note",
        "audit_admitted", "audit_reason",
    ]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in result.rows:
            w.writerow({
                "id": r.id, "section": r.section or "", "grade": r.grade or "",
                "length_mm": r.length_mm, "mass_kg": r.mass_kg,
                "scrap_value_gbp": r.scrap_value_gbp,
                "reclaimed_value_gbp": r.reclaimed_value_gbp,
                "reuse_premium_gbp": r.reuse_premium_gbp,
                "co2_saved_kg": r.co2_saved_kg, "co2_value_gbp": r.co2_value_gbp,
                "reuse_score": r.reuse_score,
                "verification_status": r.verification_status,
                "condition_grade": r.condition_grade,
                "verdict": r.verdict, "note": r.note,
                "audit_admitted": r.audit_admitted, "audit_reason": r.audit_reason,
            })


def main() -> None:
    ap = build_parser()
    args = ap.parse_args()

    if not args.demo and not args.donor:
        ap.error("supply --donor <path> or --demo")

    try:
        donor_path = sample_path("donor.json") if args.demo else Path(args.donor)
        donor = ExtractedModel.load(donor_path)

        if args.pda:
            apply_audit(donor.members, load_audit_csv(args.pda))

        params = MarketParams(
            scrap_price_per_tonne=args.scrap_price,
            reclaimed_price_per_tonne=args.reclaimed_price,
            co2_price_per_tonne=args.co2_price,
        )

        result = value_case(
            donor,
            params=params,
            knockdown=args.knockdown,
            include_unverified=args.include_unverified,
            include_unmapped=args.include_unmapped,
        )

        # Console output.
        display = result
        if args.top:
            from dataclasses import replace
            display = replace(result, rows=result.rows[: args.top])
        _print_table(display)

        # Optional writeback JSON for pyRevit colouring + the passport schedule.
        if args.out_writeback:
            wb = build_value_case_writeback(result)
            Path(args.out_writeback).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out_writeback).write_text(json.dumps(wb, indent=2), encoding="utf-8")
            print(f"writeback -> {args.out_writeback}")

        # Optional full JSON (asdict recurses the nested dataclasses, so plain json.dumps suffices).
        if args.out_json:
            import dataclasses
            Path(args.out_json).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out_json).write_text(
                json.dumps(dataclasses.asdict(result), indent=2), encoding="utf-8")
            print(f"json -> {args.out_json}")

        # Optional CSV (the ranked passport).
        if args.out_csv:
            _write_csv(args.out_csv, result)
            print(f"csv -> {args.out_csv}")

    except Exception as exc:
        if args.debug:
            raise
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
