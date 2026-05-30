"""Steel section catalog + robust Revit-name mapping.

Two jobs:
  1. Load the standard-section catalog (``data/sections/eu_sections.csv``) into
     :class:`SectionProps`, converting catalogue units (mm, cm^2/3/4) into internal units (N, mm).
  2. Map messy Revit type names onto canonical catalog names *without ever silently guessing*:
     exact -> user override -> normalized -> fuzzy (reported) -> ``unknown`` bucket.

Standard library only (imported on both sides of the Revit boundary).
"""

from __future__ import annotations

import csv
import difflib
import re
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

DEFAULT_CATALOG = Path(__file__).resolve().parents[3] / "data" / "sections" / "eu_sections.csv"

# Nominal yield strength f_y (N/mm^2) by grade, EN 1993-1-1 Table 3.1 (t <= 40 mm).
FY_BY_GRADE = {"S235": 235.0, "S275": 275.0, "S355": 355.0, "S420": 420.0, "S460": 460.0}


@dataclass(frozen=True)
class SectionProps:
    """Section properties in internal units: lengths in mm, areas mm^2, I in mm^4, W in mm^3."""

    name: str
    shape: str          # "I" (IPE) or "H" (HE...)
    h: float            # mm
    b: float
    tw: float
    tf: float
    r: float
    A: float            # mm^2
    mass_kgm: float     # kg/m (unchanged)
    Iy: float           # mm^4
    Wel_y: float        # mm^3
    Wpl_y: float
    iy: float           # mm
    Iz: float
    Wel_z: float
    Wpl_z: float
    iz: float

    @property
    def Av_z(self) -> float:
        """Shear area A_v for load along the web, EN 1993-1-1 eq. (6.18), rolled I/H (mm^2)."""
        return max(self.A - 2 * self.b * self.tf + (self.tw + 2 * self.r) * self.tf,
                   1.0 * (self.h - 2 * self.tf) * self.tw)


def load_catalog(path: str | Path = DEFAULT_CATALOG) -> dict[str, SectionProps]:
    """Read the catalog CSV, converting cm-based columns into internal mm units."""
    out: dict[str, SectionProps] = {}
    with Path(path).open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            name = row["name"].strip().upper()
            out[name] = SectionProps(
                name=name,
                shape=row["shape"].strip(),
                h=float(row["h_mm"]),
                b=float(row["b_mm"]),
                tw=float(row["tw_mm"]),
                tf=float(row["tf_mm"]),
                r=float(row["r_mm"]),
                A=float(row["A_cm2"]) * 1e2,        # cm^2 -> mm^2
                mass_kgm=float(row["mass_kgm"]),
                Iy=float(row["Iy_cm4"]) * 1e4,       # cm^4 -> mm^4
                Wel_y=float(row["Wel_y_cm3"]) * 1e3,  # cm^3 -> mm^3
                Wpl_y=float(row["Wpl_y_cm3"]) * 1e3,
                iy=float(row["iy_cm"]) * 10,         # cm -> mm
                Iz=float(row["Iz_cm4"]) * 1e4,
                Wel_z=float(row["Wel_z_cm3"]) * 1e3,
                Wpl_z=float(row["Wpl_z_cm3"]) * 1e3,
                iz=float(row["iz_cm"]) * 10,
            )
    return out


# ---------------------------------------------------------------------------
# Name mapping
# ---------------------------------------------------------------------------

# "HE 300 A" / "HE300B" / "HEM 300" -> "HEA300" / "HEB300" / "HEM300".
_HE_SUFFIX = re.compile(r"^HE0*(\d+)([ABM])$")
_HE_PREFIX = re.compile(r"^HE([ABM])0*(\d+)$")
# leading zeros in plain profiles, e.g. "IPE0300" -> "IPE300".
_PROFILE = re.compile(r"^([A-Z]+)0*(\d+)$")


def normalize_name(raw: str) -> str:
    """Best-effort normalization of a raw Revit type name to a token (no catalog lookup)."""
    s = re.sub(r"[\s_]+", "", (raw or "").upper())
    # take the first profile-looking chunk, dropping trailing grade/junk e.g. "IPE300-S275".
    # the optional trailing letter keeps the HE suffix in "HE300B"/"HEM300".
    m = re.match(r"[A-Z]+\d+[A-Z]?", s)
    if m:
        s = m.group(0)
    he = _HE_SUFFIX.match(s)
    if he:
        return f"HE{he.group(2)}{he.group(1)}"
    he2 = _HE_PREFIX.match(s)
    if he2:
        return f"HE{he2.group(1)}{he2.group(2)}"
    prof = _PROFILE.match(s)
    if prof:
        return f"{prof.group(1)}{prof.group(2)}"
    return s


@dataclass
class MappingResult:
    raw: str
    canonical: str | None
    method: str            # exact | override | normalized | fuzzy | unknown
    confidence: float      # 1.0 exact/override/normalized; <1 fuzzy; 0 unknown
    candidates: list[str]  # fuzzy alternatives, for the validation report


def map_section(
    raw: str,
    catalog: dict[str, SectionProps],
    overrides: dict[str, str] | None = None,
    fuzzy_cutoff: float = 0.82,
) -> MappingResult:
    """Map a raw name to a catalog name: exact -> override -> normalized -> fuzzy -> unknown."""
    overrides = overrides or {}
    raw_u = (raw or "").strip().upper()

    if raw_u in catalog:
        return MappingResult(raw, raw_u, "exact", 1.0, [])

    norm = normalize_name(raw)
    if norm in overrides:  # overrides are keyed by normalized name
        return MappingResult(raw, overrides[norm], "override", 1.0, [])
    if raw_u in overrides:
        return MappingResult(raw, overrides[raw_u], "override", 1.0, [])

    if norm in catalog:
        return MappingResult(raw, norm, "normalized", 1.0, [])

    close = difflib.get_close_matches(norm, list(catalog), n=3, cutoff=fuzzy_cutoff)
    if close:
        score = difflib.SequenceMatcher(None, norm, close[0]).ratio()
        return MappingResult(raw, close[0], "fuzzy", round(score, 3), close)

    return MappingResult(raw, None, "unknown", 0.0, [])


@dataclass
class ValidationReport:
    mapped: list[MappingResult]      # exact/override/normalized (confidence 1.0)
    fuzzy: list[MappingResult]       # matched but needs human confirmation
    unknown: list[MappingResult]     # no catalog match -> excluded from analysis

    @property
    def n_total(self) -> int:
        return len(self.mapped) + len(self.fuzzy) + len(self.unknown)

    def summary(self) -> str:
        return (
            f"{len(self.mapped)} mapped, {len(self.fuzzy)} fuzzy (confirm), "
            f"{len(self.unknown)} unknown of {self.n_total} members"
        )


def load_overrides(path: str | Path) -> dict[str, str]:
    """Optional user override CSV with columns ``raw,canonical`` (raw is normalized on load)."""
    overrides: dict[str, str] = {}
    p = Path(path)
    if not p.exists():
        return overrides
    with p.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            overrides[normalize_name(row["raw"])] = row["canonical"].strip().upper()
    return overrides


def resolve_members(
    members, catalog, overrides=None, fuzzy_cutoff: float = 0.82
) -> ValidationReport:
    """Map every member's ``raw_section`` -> ``section`` in place and return a validation report.

    ``members`` is any iterable of objects with ``raw_section`` and a writable ``section`` attribute
    (e.g. :class:`steelreuse.schema.ExtractedMember`). Unknown members keep ``section=None`` and are
    reported, never guessed.
    """
    mapped: list[MappingResult] = []
    fuzzy: list[MappingResult] = []
    unknown: list[MappingResult] = []
    for m in members:
        res = map_section(m.raw_section, catalog, overrides, fuzzy_cutoff)
        m.section = res.canonical  # None for unknown
        if res.method == "fuzzy":
            fuzzy.append(res)
        elif res.method == "unknown":
            unknown.append(res)
        else:
            mapped.append(res)
    return ValidationReport(mapped=mapped, fuzzy=fuzzy, unknown=unknown)
