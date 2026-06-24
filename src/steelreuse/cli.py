"""Command-line entry point: donor.json + demand.json -> matching report (HTML + console summary).

    # try it instantly on the bundled sample models:
    steelreuse --demo

    # run on your own extracted models:
    steelreuse --donor donor.json --demand demand.json --out reports/report.html
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

from . import __version__
from .core import rules
from .core.carbon import passport_rows
from .core.loads import NATIONAL_ANNEXES, OCCUPANCY_PRESETS, AreaLoadModel, ZoneSpec, presets_for_na
from .core.mismatch import mismatch_summary
from .evidence import build_evidence_package, write_evidence_package
from .llm.providers import select_provider
from .llm.report import build_report_context, generate_narrative, render_html
from .pipeline import LoadModel, run_pipeline
from .resources import sample_path
from .schema import ExtractionError
from .writeback import build_results, build_writeback


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


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Circular structural steel-reuse matcher")
    ap.add_argument("--version", action="version", version=f"steelreuse {__version__}")
    ap.add_argument("--demo", action="store_true",
                    help="run on the bundled sample donor/demand models (no --donor/--demand needed)")
    ap.add_argument("--debug", action="store_true",
                    help="show the full Python traceback on error (default: a short message)")
    ap.add_argument("--donor", help="donor (supply) extraction JSON")
    ap.add_argument("--demand", nargs="+", metavar="JSON",
                    help="new-design (demand) extraction JSON; pass SEVERAL paths for portfolio "
                         "matching (one optimization allocates the donor stock across all the "
                         "projects at once, with a per-project breakdown)")
    ap.add_argument("--out", default="reports/report.html", help="output HTML path")
    ap.add_argument("--apply-matches-out",
                    help="write a per-element status JSON (donor: reused/available/quarantined/"
                         "unmapped; demand: filled/partially_filled/unfilled/non_steel) for the "
                         "pyRevit 'Apply Matches' button to colour the source models")
    ap.add_argument("--results-out",
                    help="write an assignment-keyed results JSON (the versioned contract the pyRevit "
                         "dockable results panel consumes: KPIs + per-match demand/donor/utilisation/"
                         "governing-case + unfilled slots + quarantined donors)")
    ap.add_argument("--knockdown", type=float, default=1.0,
                    help="default reclaimed f_y knockdown (<=1.0) for donor members with no audit data")
    # Pre-demolition audit (PDA): per-member condition / verification provenance.
    ap.add_argument("--pda", help="pre-demolition-audit CSV (id,condition_grade,verification_status,"
                                  "knockdown,recoverable_length_mm,defects) merged onto donor members")
    ap.add_argument("--include-unverified", action="store_true",
                    help="admit donor members that the audit could not verify (at a conservative "
                         "knockdown) instead of quarantining them; off by default")
    # Area-based load model (default). Floor pressures + tributary geometry + EN 1990 ULS factors.
    ap.add_argument("--dead", type=float, default=None,
                    help="permanent area load g_k (kN/m^2) for the FLOOR zone; overrides --occupancy")
    ap.add_argument("--live", type=float, default=None,
                    help="imposed area load q_k (kN/m^2) for the FLOOR zone; overrides --occupancy")
    ap.add_argument("--national-annex", choices=sorted(NATIONAL_ANNEXES), default="en",
                    help="National Annex q_k override set applied to the occupancy presets "
                         "(en = EN base, default; dk/fi/cy/es/be from official free sources; it/uk "
                         "partial; de/fr/nl/ie inherit EN until verified values are added). q_k is a "
                         "Nationally Determined Parameter — confirm against the official NA before "
                         "certified use")
    ap.add_argument("--occupancy",
                    help="EN 1991-1-1 occupancy preset for the FLOOR zone (e.g. residential-A, "
                         "office-B, retail-D1, storage-E1); sets g_k/q_k. --dead/--live override it. "
                         "Keys: " + ", ".join(sorted(OCCUPANCY_PRESETS)))
    ap.add_argument("--roof-occupancy", default="roof-H",
                    help="EN 1991-1-1 occupancy preset for the ROOF zone (default roof-H, light); "
                         "use 'office-B' to load the roof as a floor (pre-change behaviour)")
    ap.add_argument("--zone-override", action="append", default=[], metavar="ID=KEY",
                    help="tag a member into a zone/preset, e.g. --zone-override b7=balcony-A "
                         "(repeatable); wins over the auto roof/floor assignment")
    ap.add_argument("--no-load-reduction", dest="load_reduction", action="store_false",
                    help="disable EN 1991-1-1 6.3.1.2 alphaA/alphaN imposed-load reduction (fully "
                         "conservative run); reduction is ON by default")
    ap.set_defaults(load_reduction=True)
    ap.add_argument("--psi0", type=float, default=None,
                    help="override the psi0 used by the alphaA/alphaN reduction (default per category)")
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
    # Cutting-stock is the DEFAULT: reclamation practice cuts members to length as a matter of
    # course, and the one-piece rule artificially strands long donors. --cut kept as a no-op for
    # backward compatibility; --no-cut restores whole-member-only reuse.
    ap.add_argument("--cut", dest="cut", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--no-cut", dest="cut", action="store_false",
                    help="whole-member reuse only: a donor fills at most one slot, never cut "
                         "(by default one donor may be cut into several pieces for several slots)")
    ap.set_defaults(cut=True)
    ap.add_argument("--objective", choices=("co2", "members", "mass"), default="co2",
                    help="what the matcher maximizes: net CO2 saved (default), the number of "
                         "members reused, or the reclaimed steel mass put back to work (the latter "
                         "two break ties toward CO2 and may select carbon-negative reuses when "
                         "that serves the goal)")
    ap.add_argument("--pareto", action="store_true",
                    help="also solve the match under every objective (co2, members, mass) and "
                         "print/report the trade-off table; the shipped assignments still follow "
                         "--objective")
    ap.add_argument("--counterfactual", choices=("none", "recycling", "rerolling"), default="none",
                    help="book reuse savings NET of the donor steel's foregone end-of-life fate: "
                         "'recycling' subtracts the EAF scrap credit, 'rerolling' the pilot-scale "
                         "direct re-rolling credit (research-grade), per kg of donor steel "
                         "consumed. Default 'none' books plain avoided-new (results unchanged)")
    ap.add_argument("--w-overspec", type=float, default=0.0,
                    help="over-spec stewardship penalty weight (default 0 = off): softly penalize "
                         "a donor's excess mass-per-metre over the slot's avoided-new baseline so "
                         "the lightest adequate donor wins ties — the capacity analogue of the "
                         "off-cut preference; affects selection only, never the booked CO2")
    ap.add_argument("--min-util", type=float, default=0.0,
                    help="utilization floor (default 0 = off): refuse (donor, slot) pairs whose "
                         "governing utilization is below this, keeping grossly over-spec donors "
                         "in stock for slots that actually need them; a hard gate, so the floor "
                         "can leave slots unfilled")
    ap.add_argument("--max-distinct-sections", type=int, default=None, metavar="N",
                    help="cap the number of distinct donor sections the result may use "
                         "(default: no cap). Anti-Frankenstein: section variety has fabrication, "
                         "QA, connection-detailing and procurement costs no carbon term sees; "
                         "the MILP consolidates onto at most N section families")
    ap.add_argument("--lab", action="store_true",
                    help="unlock EXPERIMENTAL, non-certified features so the default CLI shows only the "
                         "validated core: --reserve (single-project scarcity weight) and --solver "
                         "sap2000 (OAPI backend). Without --lab these are rejected and stay at their "
                         "safe defaults (reserve 0, solver pynite)")
    # --reserve is EXPERIMENTAL: hidden from --help and gated behind --lab. A single-project scarcity
    # proxy for option value (selection only, booked CO2 unchanged); the principled tool is portfolio
    # matching (--demand with several models).
    ap.add_argument("--reserve", type=float, default=0.0, metavar="W", help=argparse.SUPPRESS)
    ap.add_argument("--moment-shape", action="store_true",
                    help="sharper (less conservative) checks from the real moment diagram: derive the "
                         "LTB moment-gradient C1 (4-moment / Cb formula) and the 6.3.3 Cm factors "
                         "(EN Annex B) instead of the conservative uniform-moment 1.0 — a "
                         "simply-supported beam under UDL gets C1=1.136; frame members use their "
                         "solved diagram. Default off (results byte-identical without it)")
    ap.add_argument("--verify-match", action="store_true",
                    help="independently audit the matching result after the solve: re-derive every "
                         "feasible (donor, slot) pair, re-check constraints and assignment "
                         "feasibility, and confirm no improving single move exists")
    ap.add_argument("--disposition", action="store_true",
                    help="stock disposition advisory: for every UNUSED donor compare its fates — "
                         "store (still feasible for an unfilled slot here?), re-roll (pilot-scale "
                         "direct re-rolling credit), recycle (EAF credit) — and advise one "
                         "(summary line + report section)")
    ap.add_argument("--disposition-csv",
                    help="also write the per-donor disposition advisory rows to this CSV "
                         "(implies --disposition)")
    ap.add_argument("--passport-out",
                    help="write the material passport to this path as CSV (every mapped donor member: "
                         "mass, audit provenance, avoided-new carbon, and the EN 1993 reuse verdict for "
                         "the matched ones), plus a JSON sibling with stock totals")
    ap.add_argument("--evidence-out",
                    help="write the per-run EVIDENCE PACKAGE (JSON): every input (with hashes), the "
                         "catalogue/carbon-factor versions and run weights, each assignment's EN 1993 "
                         "pass-evidence (governing clause, utilisation, chi_LT, governing combination) "
                         "and carbon breakdown, the rule-data versions + mismatch log, and the "
                         "verify_match certificate — one file a reviewer re-checks instead of "
                         "re-deriving (auto-written for --demo)")
    ap.add_argument("--mismatch-csv",
                    help="write the donor-row mismatch log to this CSV: every donor member classified "
                         "(mapped/fuzzy/unknown/quarantined) with a reason — 100%% of donor rows "
                         "accounted for (also embedded in the evidence package)")
    ap.add_argument("--uncertainty", type=int, default=0, metavar="N",
                    help="run N Monte Carlo samples to show a P5–P95 CO2-saved confidence band next to "
                         "the headline (default 0 = off): varies knockdown, loads and the EN 1990 "
                         "partial factors over their documented ranges — the carbon figure's sensitivity")
    ap.add_argument("--connections", action="store_true",
                    help="enable the connection feasibility screen: exclude donors geometrically "
                         "incompatible with the slot's design section (wrong shape family, too deep "
                         "for the detailed zone); milder mismatches are flagged 'review' either way")
    ap.add_argument("--frame-analysis", action="store_true",
                    help="derive member forces from a global frame solve (PyNite) instead of "
                         "per-member closed forms; column axials then come from the real load path. "
                         "With --phi, the sway imperfection is applied as frame equivalent horizontal "
                         "forces (EN 5.3.2) + a 2nd-order P-Delta solve")
    # --solver sap2000 is EXPERIMENTAL (gravity only; Windows + SAP2000 + the [sap2000] extra; falls
    # back to analytic when unavailable): hidden from --help and gated behind --lab. pynite is default.
    ap.add_argument("--solver", choices=["pynite", "sap2000"], default="pynite", help=argparse.SUPPRESS)
    ap.add_argument("--self-weight", action="store_true",
                    help="add each member's own (design-section) weight as a permanent load in the "
                         "frame solve, so it flows down the load path (only with --frame-analysis); "
                         "off by default to keep the simply-supported wL^2/8 idealisation")
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
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)

    # Experimental features are gated behind --lab so the default CLI exposes only the validated core.
    if not args.lab:
        if args.reserve:
            ap.error("--reserve is experimental; pass --lab to enable it")
        if args.solver != "pynite":
            ap.error(f"--solver {args.solver} is experimental; pass --lab to enable it")

    # Resolve the input models: --demo uses the bundled samples, otherwise both paths are required.
    # A single demand path is passed as a plain string (the historical case); several paths flow
    # through as a list and switch run_pipeline into portfolio mode.
    if args.demo:
        donor, demand = str(sample_path("donor.json")), str(sample_path("demand.json"))
        if args.out == "reports/report.html":
            args.out = "reports/demo_report.html"
        if not args.evidence_out:
            args.evidence_out = "reports/demo_evidence.json"
    elif args.donor and args.demand:
        donor = args.donor
        demand = args.demand[0] if len(args.demand) == 1 else args.demand
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


def _loads_from_args(args: argparse.Namespace) -> LoadModel | AreaLoadModel:
    """Build the load model from parsed CLI args (legacy flat model, or zone-based area model)."""
    if args.beam_udl is not None or args.column_axial is not None:
        return LoadModel(
            beam_udl_Npmm=args.beam_udl if args.beam_udl is not None else 15.0,
            column_axial_N=(args.column_axial if args.column_axial is not None else 400.0) * 1e3,
        )
    # Occupancy presets, with any National Annex q_k overrides applied first.
    presets = presets_for_na(getattr(args, "national_annex", "en"))
    # Floor zone: an occupancy preset seeds g_k/q_k/psi0; --dead/--live then override.
    floor = presets.get(args.occupancy) if args.occupancy else None
    dead = args.dead if args.dead is not None else (floor.g_k if floor else 3.5)
    live = args.live if args.live is not None else (floor.q_k if floor else 3.0)
    floor_psi0 = args.psi0 if args.psi0 is not None else (floor.psi0 if floor else 0.7)
    floor_reducible = floor.reducible if floor else True
    roof = presets.get(args.roof_occupancy, presets["roof-H"])
    overrides: dict[str, str] = {}
    custom: dict[str, ZoneSpec] = {}
    for item in args.zone_override:
        mid, _, key = item.partition("=")
        overrides[mid] = key
        if key in presets:
            custom[key] = presets[key]
    return AreaLoadModel(
        dead_kpa=dead, live_kpa=live, gamma_g=args.gamma_g, gamma_q=args.gamma_q,
        beam_tributary_width_m=args.trib_width, column_tributary_area_m2=args.col_trib_area,
        column_floors=args.col_floors, column_eccentricity_mm=args.col_ecc,
        notional_phi=args.phi,
        construction_stage=args.construction, construction_live_kpa=args.construction_live,
        uplift_kpa=args.wind_uplift,
        roof_dead_kpa=roof.g_k, roof_live_kpa=roof.q_k, roof_psi0=roof.psi0,
        floor_psi0=floor_psi0, floor_reducible=floor_reducible,
        load_reduction=args.load_reduction,
        custom_zones=custom, zone_overrides=overrides,
    )


def _execute(args: argparse.Namespace, donor: str, demand: str | list[str]) -> int:
    load_dotenv()  # pick up GEMINI_API_KEY etc. from a .env in the working directory

    loads: LoadModel | AreaLoadModel = _loads_from_args(args)
    res = run_pipeline(
        donor, demand, loads=loads, knockdown=args.knockdown,
        include_unverified=args.include_unverified, pda_csv=args.pda,
        steel_only_demand=not args.all_demand, tributary_from_geometry=args.trib_from_geometry,
        allow_cutting=args.cut, connection_screen=args.connections,
        frame_analysis=args.frame_analysis, second_order=args.pdelta,
        wind_kpa=args.wind, seismic_cs=args.seismic, objective=args.objective,
        pareto=args.pareto,
        disposition=args.disposition or bool(args.disposition_csv),
        counterfactual=args.counterfactual, w_overspec=args.w_overspec,
        min_util=args.min_util, max_distinct_sections=args.max_distinct_sections,
        reserve_w=args.reserve, moment_shape=args.moment_shape,
        self_weight=args.self_weight,
        solver=args.solver,
    )

    uncertainty = None
    if args.uncertainty > 0 and isinstance(demand, str):
        from .sensitivity import RunParams, run_monte_carlo
        base = (RunParams(dead_kpa=loads.dead_kpa, live_kpa=loads.live_kpa, gamma_g=loads.gamma_g,
                          gamma_q=loads.gamma_q, knockdown=args.knockdown,
                          counterfactual=args.counterfactual)
                if isinstance(loads, AreaLoadModel)
                else RunParams(knockdown=args.knockdown, counterfactual=args.counterfactual))
        band = run_monte_carlo(donor, demand, base, n=args.uncertainty)
        uncertainty = {"p5": round(band.p5, 1), "p50": round(band.p50, 1),
                       "p95": round(band.p95, 1), "n": band.n}

    ctx = build_report_context(res, uncertainty)
    narrative, source = generate_narrative(ctx, select_provider())
    html = render_html(ctx, narrative, source)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")

    # Write-back maps per-element statuses onto ONE demand model; emitting a file that silently
    # covers only the first project of a portfolio would be misleading — refuse instead.
    wb_path = None
    if args.apply_matches_out:
        if res.projects:
            print("Note: --apply-matches-out supports a single demand model only; "
                  "skipped for this portfolio run (no file written)")
        else:
            wb_path = Path(args.apply_matches_out)
            wb_path.parent.mkdir(parents=True, exist_ok=True)
            wb_path.write_text(json.dumps(build_writeback(res), indent=2), encoding="utf-8")

    if args.results_out:
        rp = Path(args.results_out)
        rp.parent.mkdir(parents=True, exist_ok=True)
        results_payload = build_results(res)
        # Stamp the sibling artifact paths so the Revit panel can open the report / folder directly.
        results_payload["paths"] = {
            "report": str(out),
            "status": str(wb_path) if wb_path is not None else "",
            "results": str(rp),
            "evidence": str(Path(args.evidence_out)) if args.evidence_out else "",
            "mismatch": str(Path(args.mismatch_csv)) if args.mismatch_csv else "",
        }
        rp.write_text(json.dumps(results_payload, indent=2), encoding="utf-8")

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
        red = "on" if args.load_reduction else "off"
        na = f"NA {args.national_annex}; " if args.national_annex != "en" else ""
        print(f"Loads: area-based, {na}floor {loads.dead_kpa:g}+{loads.live_kpa:g}, "
              f"roof {loads.roof_dead_kpa:g}+{loads.roof_live_kpa:g} kN/m^2 (G+Q), "
              f"ULS {args.gamma_g:g}G+{args.gamma_q:g}Q, tributary {trib}; "
              f"alphaA/alphaN reduction {red}; "
              f"combinations: {combos}; "
              f"demand={'steel only' if not args.all_demand else 'all members'}")
    if res.frame is not None:
        if res.frame.ok:
            extra = (f", {len(res.frame.skipped_member_ids)} fell back to analytic"
                     if res.frame.skipped_member_ids else "")
            notes = f" [{'; '.join(res.frame.warnings)}]" if res.frame.warnings else ""
            print(f"Forces: frame analysis ({args.solver}) — {res.frame.node_count} nodes, "
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
    print(f"Rule data: ruleset v{rules.RULESET_VERSION} "
          f"(grades, grade defaults, condition/verification knockdowns, carbon factors — "
          f"externalised + version-stamped in the evidence package)")
    if res.mismatch_log is not None:
        ms = mismatch_summary(res.mismatch_log)
        cover = "100%" if ms["accounts_for_all"] else "INCOMPLETE"
        print(f"Donor provenance: {ms['mapped']} mapped / {ms['fuzzy']} fuzzy / "
              f"{ms['unknown']} unknown / {ms['quarantined']} quarantined of "
              f"{ms['n_donor_rows']} donor row(s) ({cover} accounted)")
    print(f"Supply {res.supply_count} | demand slots {res.slot_count} | reused {res.match.n_reused}"
          f"{' (cutting-stock)' if args.cut else ' (whole-member only)'}")
    n_distinct = len({a.section for a in res.match.assignments})
    cap = res.match.weights.get("max_distinct_sections")
    print(f"Distinct donor sections used: {n_distinct}"
          + (f" (cap {cap})" if cap is not None else ""))
    if res.projects:
        print(f"Portfolio: one donor stock allocated across {len(res.projects)} demand models")
        for p in res.projects:
            frame_note = ""
            if p["frame_ok"] is not None:
                frame_note = " | frame solved" if p["frame_ok"] else " | frame fell back to analytic"
            print(f"  {p['tag']}: {p['slot_count']} slots | reused {p['n_reused']} | "
                  f"{p['co2_saved_kg']:.1f} kg CO2e | unfilled {p['n_unmatched']}{frame_note}")
    goal = {"co2": "net-CO2", "members": "members-reused",
            "mass": "reclaimed-mass"}[args.objective]
    if res.match.proven_optimal:
        print(f"Matching: MILP proven optimal (CBC) — best possible {goal} assignment "
              f"under the use constraints")
    elif res.match.solver_status != "no_feasible_pairs":
        print(f"Matching: heuristic ({goal} objective) — {res.match.solver_status}; result is "
              f"feasible but NOT guaranteed optimal")
    if res.pareto:
        print("Objective trade-off (same feasibility, different goals; * = shipped):")
        for p in res.pareto:
            mark = "*" if p["selected"] else " "
            note = "" if p["proven_optimal"] else "  (heuristic - not proven)"
            print(f"  {mark} {p['objective']:<8} {p['n_reused']:>4} reused | "
                  f"{p['co2_saved_kg']:>10.1f} kg CO2e | "
                  f"{p['mass_reused_kg']:>10.1f} kg steel reused{note}")
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
    if args.counterfactual != "none":
        credit = res.match.weights.get("counterfactual_credit", 0.0)
        print(f"Carbon basis: savings booked NET of the foregone {args.counterfactual} credit "
              f"({credit:g} kg CO2e per kg of donor steel consumed)"
              + (" [pilot-scale research-grade factor]" if args.counterfactual == "rerolling" else ""))
    print(f"CO2e saved by matches: {res.match.total_co2_saved_kg:.1f} kg "
          f"(full donor stock potential: {res.passport.total_saved_kgco2e:.1f} kg)")
    if uncertainty is not None:
        print(f"  uncertainty (n={uncertainty['n']}): P5 {uncertainty['p5']} | P50 "
              f"{uncertainty['p50']} | P95 {uncertainty['p95']} kg CO2e "
              f"(knockdown / load / EN 1990 factor ranges)")
    if args.cut and res.match.donor_leftover_mm:
        print(f"Cut donors: {len(res.match.donor_leftover_mm)} | reusable remainder "
              f"{res.match.total_donor_leftover_mm / 1000.0:.1f} m")
    if res.disposition is not None:
        n_store = sum(1 for r in res.disposition if r["advice"] == "store")
        n_reroll = sum(1 for r in res.disposition if r["advice"] == "re-roll")
        n_recycle = sum(1 for r in res.disposition if r["advice"] == "recycle")
        print(f"Stock disposition: {n_store} store / {n_reroll} re-roll / {n_recycle} recycle "
              f"of {len(res.disposition)} unused donor(s)")
        if args.disposition_csv:
            dpath = Path(args.disposition_csv)
            dpath.parent.mkdir(parents=True, exist_ok=True)
            with dpath.open("w", newline="", encoding="utf-8") as fh:
                if res.disposition:
                    w = csv.DictWriter(fh, fieldnames=list(res.disposition[0]))
                    w.writeheader()
                    w.writerows(res.disposition)
            print(f"Disposition advisory written -> {dpath}")
    if args.passport_out:
        rows = passport_rows(res.passport, res.match.assignments)
        ppath = Path(args.passport_out)
        ppath.parent.mkdir(parents=True, exist_ok=True)
        with ppath.open("w", newline="", encoding="utf-8") as fh:
            if rows:
                w = csv.DictWriter(fh, fieldnames=list(rows[0]))
                w.writeheader()
                w.writerows(rows)
        jpath = ppath.with_suffix(".json")
        payload = {
            "totals": {"mass_kg": round(res.passport.total_mass_kg, 1),
                       "new_kgco2e": round(res.passport.total_new_kgco2e, 1),
                       "saved_kgco2e": round(res.passport.total_saved_kgco2e, 1)},
            "entries": rows,
        }
        jpath.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"Material passport written -> {ppath} (+ {jpath.name})")
    if args.mismatch_csv and res.mismatch_log is not None:
        mpath = Path(args.mismatch_csv)
        mpath.parent.mkdir(parents=True, exist_ok=True)
        with mpath.open("w", newline="", encoding="utf-8") as fh:
            if res.mismatch_log:
                w = csv.DictWriter(fh, fieldnames=list(res.mismatch_log[0]))
                w.writeheader()
                w.writerows(res.mismatch_log)
        print(f"Mismatch log written -> {mpath}")
    if args.evidence_out:
        run_context = {
            "command": " ".join(sys.argv[1:]),
            "objective": args.objective,
            "cutting_stock": args.cut,
            "national_annex": args.national_annex,
            "connection_screen": args.connections,
            "counterfactual": args.counterfactual,
            "frame_analysis": args.frame_analysis,
        }
        package = build_evidence_package(
            res, donor_path=donor,
            demand_paths=demand if isinstance(demand, list) else [demand],
            run_context=run_context)
        epath = write_evidence_package(package, args.evidence_out)
        cert = package["certificate"]
        recon = package["carbon_reconciliation"]
        verdict = "verified" if cert["verified"] else f"{len(cert['verify_match_issues'])} issue(s)"
        recon_ok = "reconciles" if recon["reconciles"] else "MISMATCH"
        print(f"Evidence package written -> {epath} (certificate: {verdict}; "
              f"CO2 {recon_ok})")
    if res.match.unmatched_slots:
        print(f"Slots needing new steel: {', '.join(res.match.unmatched_slots)}")
    print(f"Narrative source: {source}")
    print(f"Report written -> {out}")
    if wb_path is not None:
        print(f"Apply-matches data written -> {wb_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
