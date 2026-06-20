"""Synthetic dataset generator (Phase 4 enabler).

Sweeps the section catalog x grades x spans x loads, runs each combo through the deterministic
EN 1993-1-1 checks, and labels it with utilization + pass/fail. This labelled table is what the ML
capacity surrogate trains on — so we get a real "trained on data" model without any scarce
real-world dataset. The deterministic check remains the source of truth; the surrogate is a
speed-only pre-screen.

Run as a script to write ``data/generated/beam_dataset.csv``.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from steelreuse.core.ec3_checks import MemberDemand, check_member
from steelreuse.core.forces import AnalyticBackend
from steelreuse.core.sections import FY_BY_GRADE, SectionProps, load_catalog

FEATURES = ["fy", "span_mm", "udl_Npmm", "A_mm2", "Wply_mm3", "Iy_mm4"]


def _row(sec: SectionProps, grade: str, span: float, udl: float) -> dict:
    M, V = AnalyticBackend().beam_span_forces(span, udl)
    demand = MemberDemand(
        My_Ed=M, Vz_Ed=V, L=span, compression_flange_restrained=True, w_service=udl,
    )
    res = check_member(sec, grade, demand)
    return {
        "section": sec.name,
        "fy": FY_BY_GRADE[grade],
        "span_mm": span,
        "udl_Npmm": udl,
        "A_mm2": sec.A,
        "Wply_mm3": sec.Wpl_y,
        "Iy_mm4": sec.Iy,
        "utilization": res.utilization,
        "passes": int(res.utilization <= 1.0),
    }


def generate_beam_dataset(
    catalog: dict[str, SectionProps] | None = None,
    grades=("S235", "S275", "S355"),
    spans=None,
    udls=None,
) -> pd.DataFrame:
    """One row per (section, grade, span, udl) labelled with the deterministic beam utilization."""
    catalog = catalog or load_catalog()
    spans = spans if spans is not None else range(3000, 12001, 1000)
    udls = udls if udls is not None else range(2, 41, 2)  # N/mm == kN/m
    rows = [
        _row(sec, grade, float(span), float(udl))
        for sec in catalog.values()
        for grade in grades
        for span in spans
        for udl in udls
    ]
    return pd.DataFrame(rows)


def main() -> None:  # pragma: no cover
    out = Path(__file__).resolve().parents[2] / "data" / "generated"
    out.mkdir(parents=True, exist_ok=True)
    df = generate_beam_dataset()
    path = out / "beam_dataset.csv"
    df.to_csv(path, index=False)
    print(f"wrote {len(df)} rows -> {path}")


if __name__ == "__main__":  # pragma: no cover
    main()
