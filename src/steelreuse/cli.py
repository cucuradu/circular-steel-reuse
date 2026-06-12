"""Command-line entry point: donor.json + demand.json -> matching report (HTML + console summary).

    # try it instantly on the bundled sample models:
    steelreuse --demo

    # run on your own extracted models:
    steelreuse --donor donor.json --demand demand.json --out reports/report.html
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__
from .core.loads import AreaLoadModel
from .llm.providers import select_provider
from .llm.report import build_report_context, generate_narrative, render_html
from .pipeline import LoadModel, run_pipeline
from .resources import sample_path
from .schema import ExtractionError
from .writeback import build_writeback


def load_dotenv(path: str = ".env") -> None:
    """Minimal .env loader (stdlib): sets vars like GEMINI_API_KEY without overriding the shell."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Circular structural steel-reuse matcher")
    ap.add_argument("--version", action="version", version=f"steelreuse {__version__}")
    ap.add_argument("--demo", action="store_true",
                    help="run on the bundled sample donor/demand models (no --donor/--demand needed)")
    ap.add_argument("--debug", action="store_true",
                    help="show the full Python traceback on error (default: a short message)")
    ap.add_argument("--donor", help="donor (supply) extraction JSON")
    ap.add_argument("--demand", help="new-design (demand) extraction JSON")
    ap.add_argument("--out", default="reports/report.html", help="output HTML path")
    ap.add_argument("--apply-matches-out",
                    help="write a per-element status JSON (donor: reused/available/quarantined/"
                         "unmapped; demand: filled/partially_filled/unfilled/non_steel) for the "
                         "pyRevit 'Apply Matches' button to colour the source models")
    ap.add_argument("--knockdown", type=float, default=1.0,
                    help="default reclaimed f_y knockdown (<=1.0) for donor members with no audit data")
    # Pre-demolition audit (PDA): per-member condition / verification provenance.
    ap.add_argument("--pda", help="pre-demolition-audit CSV (id,condition_grade,verification_status,"
                                  "knockdown,recoverable_length_mm,defects) merged onto donor members")
    ap.add_argument("--include-unverified", action="store_true",
                    help="admit donor members that the audit could not verify (at a conservative "
                         "knockdown) instead of quarantining them; off by default")
    # Area-based load model (default). Floor pressures + tributary geometry + EN 1990 ULS factors.
    ap.add_argument("--dead", type=float, default=3.5, help="permanent area load g_k (kN/m^2)")
    ap.add_argument("--live", type=float, default=3.0, help="imposed area load q_k (kN/m^2)")
    ap.add_argument("--gamma-g", type=float, default=1.35, help="permanent partial factor (EN 1990)")
    ap.add_argument("--gamma-q", type=float, default=1.5, help="variable partial factor (EN 1990)")
    ap.add_argument("--trib-width", type=float, default=3.0, help="default beam tributary width (m)")
    ap.add_argument("--col-trib-area", type=float, default=9.0, help="column tributary area / floor (m^2)")
    ap.add_argument("--col-floors", type=float, default=1.0, help="floors a column accumulates")
    ap.add_argument("--col-ecc", type=float, default=0.0,
                    help="notional column moment eccentricity (mm); 0 = pure axial")
    ap.add_argument("--phi", type=float, default=0.0,
                    help="EN 1993-1-1 5.3.2 global sway imperfection for the load-combination "
                         "envelope (e.g. 0.005 = 1/200); 0 = gravity only")
    ap.add_argument("--construction", action="store_true",
                    help="add the bare-steel erection-stage case for beams: full dead + construction "
                         "live, compression flange UNRESTRAINED (chi_LT applies)")
    ap.add_argument("--construction-live", type=float, default=0.75,
                    help="construction live load q_ca for --construction (kN/m^2, EN 1991-1-6)")
    ap.add_argument("--wind-uplift", type=float, default=0.0,
                    help="net upward wind pressure on the roof (kN/m^2, EN 1991-1-4 input): adds a "
                         "load-reversal case for roof beams (gamma_Q*W with favourable permanent, "
                         "bottom flange in compression, UNRESTRAINED); needs beam coordinates")
    ap.add_argument("--trib-from-geometry", action="store_true",
                    help="estimate per-beam width AND per-column tributary area/floors from geometry")
    ap.add_argument("--all-demand", action="store_true",
                    help="also slot non-steel demand (concrete, joists); default is steel members only")
    ap.add_argument("--cut", action="store_true",
                    help="cutting-stock: allow one donor to be cut into several pieces for several "
                         "slots (default: one piece per donor)")
    ap.add_argument("--objective", choices=("co2", "members", "mass"), default="co2",
                    help="what the matcher maximizes: net CO2 saved (default), the number of "
                         "members reused, or the reclaimed steel mass put back to work (the latter "
                         "two break ties toward CO2 and may select carbon-negative reuses when "
                         "that serves the goal)")
    ap.add_argument("--verify-match", action="store_true",
                    help="independently audit the matching result after the solve: re-derive every "
                         "feasible (donor, slot) pair, re-check constraints and assignment "
                         "feasibility, and confirm no improving single move exists")
    ap.add_argument("--connections", action="store_true",
                    help="enable the connection feasibility screen: exclude donors geometrically "
                         "incompatible with the slot's design section (wrong shape family, too deep "
                         "for the detailed zone); milder mismatches are flagged 'review' either way")
    ap.add_argument("--frame-analysis", action="store_true",
                    help="derive member forces from a global frame solve (PyNite) instead of "
                         "per-member closed forms; column axials then come from the real load path. "
                         "With --phi, the sway imperfection is applied as frame equivalent horizontal "
                         "forces (EN 5.3.2) + a 2nd-order P-Delta solve")
    ap.add_argument("--pdelta", action="store_true",
                    help="force a 2nd-order (P-Delta) frame solve even without --phi "
                         "(only with --frame-analysis)")
    ap.add_argument("--wind", type=float, default=0.0,
                    help="net horizontal wind pressure (kN/m^2, EN 1991-1-4 input) applied as frame "
                         "storey forces (only with --frame-analysis; needs a 3-D model)")
    ap.add_argument("--seismic", type=float, default=0.0,
                    help="EN 1998-1 base-shear coefficient Cs = Sd(T1)*lambda/g; applies the lateral "
                         "force method as frame storey forces (only with --frame-analysis)")
    # Legacy flat model: if either is given, override the area model with one UDL / one axial.
    ap.add_argument("--beam-udl", type=float, default=None, help="[legacy] flat beam UDL (kN/m == N/mm)")
    ap.add_argument("--column-axial", type=float, default=None, help="[legacy] flat column axial (kN)")
    args = ap.parse_args(argv)

    # Resolve the input models: --demo uses the bundled samples, otherwise both paths are required.
    if args.demo:
        donor, demand = str(sample_path("donor.json")), str(sample_path("demand.json"))
        if args.out == "reports/report.html":
            args.out = "reports/demo_report.html"
    elif args.donor and args.demand:
        donor, demand = args.donor, args.demand
    else:
        ap.error("provide --donor and --demand (or use --demo to run the bundled sample models)")

    try:
        return _execute(args, donor, demand)
    except ExtractionError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001 — top-level guard: a friendly message, not a traceback
        if args.debug:
            raise
        print(f"error: {type(e).__name__}: {e}\n(run with --debug for the full traceback)",
              file=sys.stderr)
        return 1


def _execute(args: argparse.Namespace, donor: str, demand: str) -> int:
    load_dotenv()  # pick up GEMINI_API_KEY etc. from a .env in the working directory

    if args.beam_udl is not None or args.column_axial is not None:
        loads: LoadModel | AreaLoadModel = LoadModel(
            beam_udl_Npmm=args.beam_udl if args.beam_udl is not None else 15.0,
            column_axial_N=(args.column_axial if args.column_axial is not None else 400.0) * 1e3,
        )
    else:
        loads = AreaLoadModel(
            dead_kpa=args.dead, live_kpa=args.live, gamma_g=args.gamma_g, gamma_q=args.gamma_q,
            beam_tributary_width_m=args.trib_width, column_tributary_area_m2=args.col_trib_area,
            column_floors=args.col_floors, column_eccentricity_mm=args.col_ecc,
            notional_phi=args.phi,
            construction_stage=args.construction, construction_live_kpa=args.construction_live,
            uplift_kpa=args.wind_uplift,
        )
    res = run_pipeline(
        donor, demand, loads=loads, knockdown=args.knockdown,
        include_unverified=args.include_unverified, pda_csv=args.pda,
        steel_only_demand=not args.all_demand, tributary_from_geometry=args.trib_from_geometry,
        allow_cutting=args.cut, connection_screen=args.connections,
        frame_analysis=args.frame_analysis, second_order=args.pdelta,
        wind_kpa=args.wind, seismic_cs=args.seismic, objective=args.objective,
    )

    ctx = build_report_context(res)
    narrative, source = generate_narrative(ctx, select_provider())
    html = render_html(ctx, narrative, source)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")

    if args.apply_matches_out:
        wb_path = Path(args.apply_matches_out)
        wb_path.parent.mkdir(parents=True, exist_ok=True)
        wb_path.write_text(json.dumps(build_writeback(res), indent=2), encoding="utf-8")

    if isinstance(loads, AreaLoadModel):
        trib = "geometry-estimated" if args.trib_from_geometry else f"{args.trib_width:g} m"
        parts = ["gravity"]
        if args.phi > 0:
            parts.append(f"sway imperfection (phi={args.phi:g})")
        if args.construction:
            parts.append(f"construction stage ({args.construction_live:g} kN/m^2, unrestrained)")
        if args.wind_uplift > 0:
            parts.append(f"wind uplift ({args.wind_uplift:g} kN/m^2, roof beams, unrestrained)")
        combos = " + ".join(parts) if len(parts) > 1 else "gravity only"
        print(f"Loads: area-based, {args.dead:g}+{args.live:g} kN/m^2 (G+Q), "
              f"ULS {args.gamma_g:g}G+{args.gamma_q:g}Q, tributary {trib}; "
              f"combinations: {combos}; "
              f"demand={'steel only' if not args.all_demand else 'all members'}")
    if res.frame is not None:
        if res.frame.ok:
            extra = (f", {len(res.frame.skipped_member_ids)} fell back to analytic"
                     if res.frame.skipped_member_ids else "")
            notes = f" [{'; '.join(res.frame.warnings)}]" if res.frame.warnings else ""
            print(f"Forces: frame analysis (PyNite) — {res.frame.node_count} nodes, "
                  f"{res.frame.member_count} members{extra}{notes}")
        else:
            why = res.frame.warnings[0] if res.frame.warnings else "unavailable"
            print(f"Forces: analytic (frame analysis not applied — {why})")
    if res.audit is not None and res.audit.present:
        print(f"Pre-demolition audit: {res.audit.n_audited} member(s) audited, "
              f"{res.audit.n_admitted} admitted, {res.audit.n_quarantined} quarantined, "
              f"avg knockdown {res.audit.avg_knockdown:g}"
              f"{' (--include-unverified)' if args.include_unverified else ''}")
    print(f"Mapping: {res.validation.summary()}")
    print(f"Supply {res.supply_count} | demand slots {res.slot_count} | reused {res.match.n_reused}"
          f"{' (cutting-stock)' if args.cut else ''}")
    goal = {"co2": "net-CO2", "members": "members-reused",
            "mass": "reclaimed-mass"}[args.objective]
    if res.match.proven_optimal:
        print(f"Matching: MILP proven optimal (CBC) — best possible {goal} assignment "
              f"under the use constraints")
    elif res.match.solver_status != "no_feasible_pairs":
        print(f"Matching: heuristic ({goal} objective) — {res.match.solver_status}; result is "
              f"feasible but NOT guaranteed optimal")
    if args.verify_match:
        from .core.sections import load_default_catalog
        from .match.optimize import verify_match
        issues = verify_match(res.supply, res.slots, load_default_catalog(), res.match)
        if issues:
            print(f"Match verification: {len(issues)} issue(s) found!")
            for issue in issues:
                print(f"  - {issue}")
        else:
            print("Match verification: constraints hold, every assignment re-validates, "
                  "and no improving single move exists")
    conn_review = sum(1 for a in res.match.assignments if a.connection_status == "review")
    if args.connections or conn_review:
        print(f"Connections: screen {'on' if args.connections else 'off (annotate only)'} | "
              f"{conn_review} assignment(s) flagged for connection review")
    print(f"CO2e saved by matches: {res.match.total_co2_saved_kg:.1f} kg "
          f"(full donor stock potential: {res.passport.total_saved_kgco2e:.1f} kg)")
    if args.cut and res.match.donor_leftover_mm:
        print(f"Cut donors: {len(res.match.donor_leftover_mm)} | reusable remainder "
              f"{res.match.total_donor_leftover_mm / 1000.0:.1f} m")
    if res.match.unmatched_slots:
        print(f"Slots needing new steel: {', '.join(res.match.unmatched_slots)}")
    print(f"Narrative source: {source}")
    print(f"Report written -> {out}")
    if args.apply_matches_out:
        print(f"Apply-matches data written -> {wb_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
