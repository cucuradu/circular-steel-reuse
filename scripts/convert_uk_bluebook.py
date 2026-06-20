"""Convert the SCI/Tata "Blue Book" UB/UC exports into the catalog CSV schema.

Source: steelforlifebluebook.co.uk -> "Section properties - Dimensions & properties" (EC3 UK NA,
BS EN 10365), "Export whole table" for Universal Columns (UC) and Universal Beams (UB). Drop the two
`.xlsx` files in ``data/uk_sections_raw/`` (any filenames starting ``UC``/``UB``) and run:

    python scripts/convert_uk_bluebook.py            # dry run: parse + validate + report
    python scripts/convert_uk_bluebook.py --write     # also (re)write the catalog CSV

It maps the Blue Book columns to ``eu_sections.csv``'s schema (units already match: mm / cm² / cm³ /
cm⁴ / kg/m), forms each designation as ``<prefix><serial>x<nominal-mass>`` (e.g. ``UC254x254x73``), and
**validates every row** against the catalog property-consistency invariants before writing, so a bad
export fails loudly here rather than silently entering the catalog. Needs ``pandas`` + ``openpyxl``
(the ``[analysis]`` extra plus ``openpyxl``); it is a one-off data tool, not part of the runtime.

Provenance is the point: re-run this whenever the Blue Book is re-exported, and the catalog is
regenerated from the authoritative source verbatim.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "uk_sections_raw"
DEST = ROOT / "src" / "steelreuse" / "data" / "sections" / "uk_sections.csv"

# (filename prefix, catalog name prefix, SectionProps shape): UC ~ H (h/b≈1), UB ~ I (h/b>1.2).
FAMILIES = [("UC", "UC", "H"), ("UB", "UB", "I")]

# Column indices in the raw "Dimensions & properties" sheet (stable across the UB/UC exports).
COL = dict(serial=0, mass=3, h=4, b=5, tw=6, tf=7, r=8,
           Iy=17, Iz=18, iy=19, iz=20, Wely=21, Welz=22, Wply=23, Wplz=24, A=29)

HEADER = ("name,shape,h_mm,b_mm,tw_mm,tf_mm,r_mm,A_cm2,mass_kgm,"
          "Iy_cm4,Wel_y_cm3,Wpl_y_cm3,iy_cm,Iz_cm4,Wel_z_cm3,Wpl_z_cm3,iz_cm")


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _find(prefix: str) -> Path:
    hits = sorted(RAW_DIR.glob(f"{prefix}*.xlsx"))
    if not hits:
        raise FileNotFoundError(f"no {prefix}*.xlsx in {RAW_DIR} — export it from the Blue Book first")
    return hits[0]


def parse(path: Path, name_prefix: str, shape: str) -> list[dict]:
    raw = pd.read_excel(path, sheet_name=0, header=None)
    serial = None
    rows: list[dict] = []
    for r in range(raw.shape[0]):
        s = raw.iloc[r, COL["serial"]]
        if isinstance(s, str) and "x" in s.lower() and any(ch.isdigit() for ch in s):
            serial = s.replace(" ", "")                  # serial size appears once per size group
        mass, h, A = (_num(raw.iloc[r, COL[k]]) for k in ("mass", "h", "A"))
        if serial is None or mass is None or h is None or A is None:
            continue                                     # header / blank / footnote row
        rec = {k: _num(raw.iloc[r, COL[k]]) for k in
               ("h", "b", "tw", "tf", "r", "A", "mass", "Iy", "Iz", "iy", "iz",
                "Wely", "Welz", "Wply", "Wplz")}
        rec["name"] = f"{name_prefix}{serial}x{round(mass)}"
        rec["shape"] = shape
        rows.append(rec)
    return rows


def validate(rows: list[dict]) -> None:
    """Raise AssertionError on any row violating the catalog property-consistency invariants."""
    for d in rows:
        assert abs(d["mass"] - 0.785 * d["A"]) / d["mass"] <= 0.05, f"{d['name']}: mass vs 0.785·A"
        for second_moment, area, radius in ((d["Iy"], d["A"], d["iy"]), (d["Iz"], d["A"], d["iz"])):
            assert abs(math.sqrt(second_moment / area) - radius) / radius <= 0.03, \
                f"{d['name']}: i vs sqrt(I/A)"
        assert d["Wply"] >= d["Wely"] and d["Wplz"] >= d["Welz"], f"{d['name']}: Wpl < Wel"


def to_csv(rows: list[dict]) -> str:
    out = [HEADER]
    for d in rows:
        out.append(",".join(str(x) for x in [
            d["name"], d["shape"], d["h"], d["b"], d["tw"], d["tf"], d["r"], d["A"], d["mass"],
            d["Iy"], d["Wely"], d["Wply"], d["iy"], d["Iz"], d["Welz"], d["Wplz"], d["iz"]]))
    return "\n".join(out) + "\n"


def main(argv: list[str]) -> int:
    all_rows: list[dict] = []
    for prefix, name_prefix, shape in FAMILIES:
        rows = parse(_find(prefix), name_prefix, shape)
        validate(rows)
        print(f"{prefix}: {len(rows)} sections ({rows[0]['name']} … {rows[-1]['name']}) — validated")
        all_rows += rows
    print(f"TOTAL: {len(all_rows)} UK sections")
    if "--write" in argv:
        DEST.write_text(to_csv(all_rows), encoding="utf-8")
        print(f"WROTE {DEST.relative_to(ROOT)}")
    else:
        print("(dry run — pass --write to regenerate the catalog CSV)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
