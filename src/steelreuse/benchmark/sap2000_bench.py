"""Cross-software force benchmark — analytic vs PyNite vs SAP2000 on one validated frame.

The headline thesis-validation artifact (FUTURE_IMPROVEMENTS I-9, §11): solve the *same* small,
hand-validated frame three ways and tabulate the agreement, so the project's own hand-calcs gain an
independent commercial-solver cross-check. The force-comparison assembly here is pure Python (unit
tested in CI); only the SAP2000 column needs the OAPI, so a run without SAP2000 reports that solver
as unavailable rather than producing the table.

Run once on the SAP2000 trial machine:  ``steelreuse-bench-sap2000 --out docs/benchmark``
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MemberForces:
    """Governing (max-magnitude) design forces for one member: axial N, major moment M, shear V."""

    N: float
    M: float
    V: float


@dataclass
class ComparisonRow:
    """One member's forces across solvers, plus % difference of each vs the reference solver."""

    member_id: str
    by_solver: dict[str, MemberForces]
    pct_diff: dict[str, dict[str, float]]


def member_force_summary(result) -> dict[str, MemberForces]:
    """Reduce a :class:`~steelreuse.core.frame.FrameResult` to one governing N/M/V per member.

    Governing = the largest magnitude of each component across the member's load combinations
    (the value the matcher's worst-case envelope would see)."""
    out: dict[str, MemberForces] = {}
    for mid, combos in result.demands_by_member.items():
        n = max((abs(d.N_Ed) for _, d in combos), default=0.0)
        m = max((abs(d.My_Ed) for _, d in combos), default=0.0)
        v = max((abs(d.Vz_Ed) for _, d in combos), default=0.0)
        out[mid] = MemberForces(N=n, M=m, V=v)
    return out


def _pct(value: float, reference: float) -> float:
    if reference == 0.0:
        return 0.0 if value == 0.0 else float("inf")
    return (value - reference) / reference * 100.0


def build_comparison(
    results_by_solver: dict[str, object], reference: str = "pynite"
) -> list[ComparisonRow]:
    """Per-member comparison rows across solvers, with % diff vs the ``reference`` solver.

    Member order follows the reference solver's; members the reference lacks are appended."""
    summaries = {s: member_force_summary(r) for s, r in results_by_solver.items()}
    ref_summary = summaries.get(reference, {})
    ordered = list(ref_summary)
    for s in summaries.values():
        for mid in s:
            if mid not in ordered:
                ordered.append(mid)

    rows: list[ComparisonRow] = []
    for mid in ordered:
        by_solver = {s: summ[mid] for s, summ in summaries.items() if mid in summ}
        pct: dict[str, dict[str, float]] = {}
        ref = by_solver.get(reference)
        if ref is not None:
            for s, mf in by_solver.items():
                if s == reference:
                    continue
                pct[s] = {"N": _pct(mf.N, ref.N), "M": _pct(mf.M, ref.M), "V": _pct(mf.V, ref.V)}
        rows.append(ComparisonRow(member_id=mid, by_solver=by_solver, pct_diff=pct))
    return rows


def _fmt(x: float) -> str:
    return f"{x:.0f}" if abs(x) >= 1e3 else f"{x:.3g}"


def _solver_order(rows: list[ComparisonRow]) -> list[str]:
    """Stable union of every solver appearing in any row (a member absent from one solver — e.g. an
    unavailable SAP2000 — still gets its column, rendered as '—')."""
    order: list[str] = []
    for row in rows:
        for s in row.by_solver:
            if s not in order:
                order.append(s)
    return order


def comparison_to_markdown(
    rows: list[ComparisonRow], reference: str = "pynite", solvers: list[str] | None = None
) -> str:
    """Render the comparison as a thesis-ready markdown table (N in kN, M in kNm, V in kN)."""
    solvers = solvers if solvers is not None else _solver_order(rows)
    head = ["Member"]
    for s in solvers:
        head += [f"{s} N(kN)", f"{s} M(kNm)", f"{s} V(kN)"]
    for s in solvers:
        if s != reference:
            head += [f"{s} %ΔN", f"{s} %ΔM", f"{s} %ΔV"]
    lines = ["| " + " | ".join(head) + " |",
             "| " + " | ".join("---" for _ in head) + " |"]
    for row in rows:
        cells = [row.member_id]
        for s in solvers:
            mf = row.by_solver.get(s)
            if mf is None:
                cells += ["—", "—", "—"]
            else:
                cells += [_fmt(mf.N / 1e3), _fmt(mf.M / 1e6), _fmt(mf.V / 1e3)]
        for s in solvers:
            if s == reference:
                continue
            d = row.pct_diff.get(s)
            if d is None:
                cells += ["—", "—", "—"]
            else:
                cells += [f"{d['N']:+.1f}", f"{d['M']:+.1f}", f"{d['V']:+.1f}"]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def comparison_to_csv(
    rows: list[ComparisonRow], reference: str = "pynite", solvers: list[str] | None = None
) -> str:
    """Render the comparison as CSV (SI units: N in N, M in N·mm, V in N) for archival/processing."""
    import csv
    import io

    solvers = solvers if solvers is not None else _solver_order(rows)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    head = ["member"]
    for s in solvers:
        head += [f"{s}_N", f"{s}_M", f"{s}_V"]
    for s in solvers:
        if s != reference:
            head += [f"{s}_pctN", f"{s}_pctM", f"{s}_pctV"]
    w.writerow(head)
    for row in rows:
        cells: list[object] = [row.member_id]
        for s in solvers:
            mf = row.by_solver.get(s)
            cells += [mf.N, mf.M, mf.V] if mf else ["", "", ""]
        for s in solvers:
            if s == reference:
                continue
            d = row.pct_diff.get(s)
            cells += [d["N"], d["M"], d["V"]] if d else ["", "", ""]
        w.writerow(cells)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# The canonical benchmark frame + the three solver runs
# ---------------------------------------------------------------------------

def canonical_two_bay_frame():
    """A clean, hand-checkable 2-bay single-storey portal (3 fixed-base columns, 2 simply-supported
    6 m beams at 3 m). Geometry matches the validated layouts in ``tests/test_frame.py`` so the
    benchmark is anchored to an already-verified case rather than a fresh one."""
    from ..schema import ExtractedMember

    def col(cid, x, z0, z1):
        return ExtractedMember(id=cid, role="column", section="IPE300", material_grade="S275",
                               start_xyz=[x, 0.0, z0], end_xyz=[x, 0.0, z1], length_mm=z1 - z0)

    def beam(bid, x0, x1, z):
        return ExtractedMember(id=bid, role="beam", section="IPE300", material_grade="S275",
                               start_xyz=[x0, 0.0, z], end_xyz=[x1, 0.0, z], spans_mm=[x1 - x0])

    return [col("C0", 0, 0, 3000), col("C1", 6000, 0, 3000), col("C2", 12000, 0, 3000),
            beam("B0", 0, 6000, 3000), beam("B1", 6000, 12000, 3000)]


def analytic_frame_result(members, loads):
    """Closed-form isolated-member result as a :class:`~steelreuse.core.frame.FrameResult`: each beam
    gets ``M = wL²/8`` / ``V = wL/2``; columns carry no axial (the analytic path has no global load
    path — that contrast vs the solvers is itself informative)."""
    from ..core.ec3_checks import MemberDemand
    from ..core.frame import FrameResult

    w = loads.factored_area_kpa() * loads.beam_tributary_width_m   # N/mm
    demands: dict[str, list[tuple[str, MemberDemand]]] = {}
    for m in members:
        if m.role == "beam":
            length = m.spans_mm[0]
            demands[m.id] = [("ULS gravity",
                              MemberDemand(My_Ed=w * length * length / 8.0,
                                           Vz_Ed=w * length / 2.0, L=length))]
        else:
            demands[m.id] = [("ULS gravity", MemberDemand(N_Ed=0.0, L=m.length_mm or 0.0))]
    return FrameResult(
        demands_by_member=demands, node_count=0, member_count=len(members),
        base_node_ids=[], skipped_member_ids=[],
        warnings=["isolated per-member analytic (columns carry no solved load path)"], ok=True)


@dataclass
class WorstEntry:
    """One out-of-tolerance force component for the 'worst offenders' report."""

    member_id: str
    component: str          # "N" | "M" | "V"
    reference: float
    value: float
    pct: float


@dataclass
class SolverSummary:
    """How well one solver agrees with the reference: counts + the biggest disagreements."""

    n_components: int       # force components compared (above the near-zero floor)
    n_within: int           # how many agree within the tolerance
    worst: list[WorstEntry]


def comparison_summary(
    rows: list[ComparisonRow], reference: str = "pynite", tol: float = 2.0,
    abs_floor: float = 1000.0, top: int = 20,
) -> dict[str, SolverSummary]:
    """For each non-reference solver, count how many force components agree with the reference within
    ``tol`` %% and list the worst disagreements. Components where both values are below ``abs_floor``
    (N or N·mm — numerical noise near zero) are ignored. Lets a 500-member diff become one line plus a
    short worst-offenders list."""
    out: dict[str, SolverSummary] = {}
    for s in (x for x in _solver_order(rows) if x != reference):
        worst: list[WorstEntry] = []
        n_comp = n_within = 0
        for row in rows:
            ref = row.by_solver.get(reference)
            val = row.by_solver.get(s)
            if ref is None or val is None:
                continue
            for comp in ("N", "M", "V"):
                a, b = getattr(ref, comp), getattr(val, comp)
                scale = max(abs(a), abs(b))
                if scale < abs_floor:        # both near zero — numerical noise, skip
                    continue
                # % relative to the reference (consistent with build_comparison's %Δ); if the
                # reference itself is ~0 but the other solver isn't, that's a real disagreement —
                # scale by the larger value so it shows up as a big (finite) %.
                denom = abs(a) if abs(a) >= abs_floor else scale
                pct = abs(b - a) / denom * 100.0
                n_comp += 1
                if pct <= tol:
                    n_within += 1
                else:
                    worst.append(WorstEntry(row.member_id, comp, a, b, pct))
        worst.sort(key=lambda e: e.pct, reverse=True)
        out[s] = SolverSummary(n_components=n_comp, n_within=n_within, worst=worst[:top])
    return out


@dataclass
class BenchmarkRun:
    """Result of a benchmark run: the comparison rows, the solver order, and any unavailable solver
    (name -> reason) so the caller can report which columns are real."""

    rows: list[ComparisonRow]
    solvers: list[str]
    unavailable: dict[str, str]


def run_benchmark(
    out_dir, demand_path: str | None = None, reference: str = "pynite",
    include_analytic: bool | None = None,
) -> BenchmarkRun:
    """Solve a frame with analytic / PyNite / SAP2000, write the comparison table
    (``forces_compare.csv`` + ``forces_compare.md``) under ``out_dir``, and return the run.

    With no ``demand_path`` the small validated **2-bay frame** is used (the calibration benchmark).
    With ``demand_path`` an **extracted demand model** is loaded and its members solved — the real
    PyNite-vs-SAP2000 diff on an actual building. Analytic is omitted by default for a loaded model
    (PyNite/SAP2000 split continuous members identically so their keys align, but the un-split analytic
    keys would not). Unavailable solvers (no ``[fea]`` extra, or SAP2000 absent) simply yield empty
    columns and are listed in :attr:`BenchmarkRun.unavailable`."""
    from pathlib import Path

    from ..core.frame import analyze_frame
    from ..core.frame_sap2000 import analyze_frame_sap2000
    from ..core.loads import AreaLoadModel
    from ..core.sections import load_default_catalog
    from ..schema import ExtractedModel

    if demand_path:
        members = ExtractedModel.load(demand_path).members
        include_analytic = False if include_analytic is None else include_analytic
        title = f"real demand model ({Path(demand_path).name}, {len(members)} members)"
    else:
        members = canonical_two_bay_frame()
        include_analytic = True if include_analytic is None else include_analytic
        title = "canonical 2-bay frame"

    loads = AreaLoadModel()
    catalog = load_default_catalog()
    results: dict[str, object] = {}
    if include_analytic:
        results["analytic"] = analytic_frame_result(members, loads)
    results["pynite"] = analyze_frame(members, loads, catalog)
    results["sap2000"] = analyze_frame_sap2000(members, loads, catalog)
    solvers = [s for s in ("analytic", "pynite", "sap2000") if s in results]
    unavailable = {s: (r.warnings[0] if r.warnings else "unavailable")
                   for s, r in results.items() if not r.demands_by_member}

    sap = results.get("sap2000")
    provenance = (sap.warnings[0] if sap and sap.demands_by_member and sap.warnings else None)

    rows = build_comparison(results, reference=reference)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "forces_compare.csv").write_text(
        comparison_to_csv(rows, reference, solvers), encoding="utf-8")
    (out / "forces_compare.md").write_text(
        _markdown_report(rows, reference, solvers, unavailable, title, provenance), encoding="utf-8")
    return BenchmarkRun(rows=rows, solvers=solvers, unavailable=unavailable)


_FULL_TABLE_MAX_ROWS = 30   # above this, the markdown shows a summary + worst offenders, not all rows


def _worst_table(worst: list[WorstEntry]) -> str:
    head = "| Member | Comp | reference | value | %Δ |\n| --- | --- | --- | --- | --- |"
    rows = [f"| {e.member_id} | {e.component} | {_fmt(e.reference)} | {_fmt(e.value)} | "
            f"{e.pct:+.1f} |" for e in worst]
    return "\n".join([head, *rows])


def _markdown_report(rows, reference, solvers, unavailable, title, provenance=None) -> str:
    """The committed artifact: title, an agreement summary, and either the full table (small frames)
    or a worst-offenders list pointing at the CSV (large real models)."""
    lines = [
        f"# Cross-software force benchmark — {title}",
        "",
        "Per-member governing design forces (ULS gravity) on the *same* snapped topology. "
        "`%Δ` is each solver vs the reference (**" + reference + "**). N in kN, M in kNm, V in kN.",
        "",
    ]

    summary = comparison_summary(rows, reference=reference)
    for s, summ in summary.items():
        if summ.n_components == 0:
            continue
        lines += [f"## {s} vs {reference}",
                  f"- {summ.n_within}/{summ.n_components} force components agree within 2 %.",
                  f"- {len(summ.worst)} component(s) outside 2 %"
                  + (":" if summ.worst else "."), ""]
        if summ.worst:
            lines += [_worst_table(summ.worst), ""]

    if len(rows) <= _FULL_TABLE_MAX_ROWS:
        lines += ["## Per-member forces", "", comparison_to_markdown(rows, reference, solvers)]
    else:
        lines += [f"_Full per-member table ({len(rows)} members) in `forces_compare.csv`._"]

    if unavailable:
        lines += ["", "## Unavailable solvers", ""]
        lines += [f"- **{s}**: {why}" for s, why in unavailable.items()]
    footer = provenance or ("SAP2000 not run (its columns are empty) — run on a machine with the "
                            "OAPI to fill them")
    lines += ["", f"_{footer}._", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Console entry (``steelreuse-bench-sap2000``)."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="steelreuse-bench-sap2000",
        description="Cross-software force benchmark (analytic vs PyNite vs SAP2000) on the canonical "
                    "2-bay frame. Run once on a machine with the SAP2000 OAPI available.")
    parser.add_argument("--out", default="docs/benchmark",
                        help="output directory for forces_compare.{csv,md} (default: docs/benchmark)")
    parser.add_argument("--demand", default=None,
                        help="extracted demand model JSON to compare on the REAL building (e.g. "
                             "pyrevit_extension/demand_test_4.json); default is the 2-bay frame")
    parser.add_argument("--reference", default="pynite", choices=["analytic", "pynite", "sap2000"],
                        help="reference solver for %% difference (default: pynite)")
    args = parser.parse_args(argv)

    run = run_benchmark(args.out, demand_path=args.demand, reference=args.reference)
    print(f"Wrote {args.out}/forces_compare.csv and forces_compare.md "
          f"({len(run.rows)} members).")
    for s, summ in comparison_summary(run.rows, reference=args.reference).items():
        if summ.n_components:
            print(f"  {s} vs {args.reference}: {summ.n_within}/{summ.n_components} within 2 %, "
                  f"{len(summ.worst)} worse")
    for solver, why in run.unavailable.items():
        print(f"  [!] {solver} unavailable: {why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
