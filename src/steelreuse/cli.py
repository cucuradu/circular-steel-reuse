"""Command-line entry point: donor.json + demand.json -> matching report (HTML + console summary).

    uv run steelreuse --donor data/samples/donor.json --demand data/samples/demand.json \
        --out reports/report.html
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from .core.loads import AreaLoadModel
from .llm.providers import select_provider
from .llm.report import build_report_context, generate_narrative, render_html
from .pipeline import LoadModel, run_pipeline


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
    ap.add_argument("--donor", required=True, help="donor (supply) extraction JSON")
    ap.add_argument("--demand", required=True, help="new-design (demand) extraction JSON")
    ap.add_argument("--out", default="reports/report.html", help="output HTML path")
    ap.add_argument("--knockdown", type=float, default=1.0, help="reclaimed f_y knockdown (<=1.0)")
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
    ap.add_argument("--trib-from-geometry", action="store_true",
                    help="estimate per-beam width AND per-column tributary area/floors from geometry")
    ap.add_argument("--all-demand", action="store_true",
                    help="also slot non-steel demand (concrete, joists); default is steel members only")
    # Legacy flat model: if either is given, override the area model with one UDL / one axial.
    ap.add_argument("--beam-udl", type=float, default=None, help="[legacy] flat beam UDL (kN/m == N/mm)")
    ap.add_argument("--column-axial", type=float, default=None, help="[legacy] flat column axial (kN)")
    args = ap.parse_args(argv)

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
        )
    res = run_pipeline(
        args.donor, args.demand, loads=loads, knockdown=args.knockdown,
        steel_only_demand=not args.all_demand, tributary_from_geometry=args.trib_from_geometry,
    )

    ctx = build_report_context(res)
    narrative, source = generate_narrative(ctx, select_provider())
    html = render_html(ctx, narrative, source)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")

    if isinstance(loads, AreaLoadModel):
        trib = "geometry-estimated" if args.trib_from_geometry else f"{args.trib_width:g} m"
        print(f"Loads: area-based, {args.dead:g}+{args.live:g} kN/m^2 (G+Q), "
              f"ULS {args.gamma_g:g}G+{args.gamma_q:g}Q, tributary {trib}; "
              f"demand={'steel only' if not args.all_demand else 'all members'}")
    print(f"Mapping: {res.validation.summary()}")
    print(f"Supply {res.supply_count} | demand slots {res.slot_count} | reused {res.match.n_reused}")
    print(f"CO2e saved by matches: {res.match.total_co2_saved_kg:.1f} kg "
          f"(full donor stock potential: {res.passport.total_saved_kgco2e:.1f} kg)")
    if res.match.unmatched_slots:
        print(f"Slots needing new steel: {', '.join(res.match.unmatched_slots)}")
    print(f"Narrative source: {source}")
    print(f"Report written -> {out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
