"""Steel section catalog + robust Revit-name mapping.

Two jobs:
  1. Load the standard-section catalogs into :class:`SectionProps`, converting catalogue units into
     internal units (N, mm). Two source schemas are supported:
       * European (``steelreuse/data/sections/eu_sections.csv``) in mm / cm^2 / cm^3 / cm^4 / kg/m;
       * US AISC (``steelreuse/data/sections/us_sections.csv``) in inch / in^2 / in^3 / in^4 / lb/ft, stored
         verbatim from the AISC Shapes Database v15.0 (traceable to source) and converted on load.
     Both land in the same internal representation, so everything downstream stays in N, mm.
  2. Map messy Revit type names (EN ``IPE300``/``HE 300 A`` *and* AISC ``W18x55``/``HSS6x6x5/8``)
     onto canonical catalog names *without ever silently guessing*:
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

# Catalogs ship *inside* the package (``src/steelreuse/data``) so an installed wheel can find them;
# ``parent.parent`` is the ``steelreuse`` package dir whether running from a source checkout or a wheel.
_DATA = Path(__file__).resolve().parent.parent / "data" / "sections"
DEFAULT_CATALOG = _DATA / "eu_sections.csv"        # European IPE/HE (metric source units)
DEFAULT_US_CATALOG = _DATA / "us_sections.csv"     # US AISC W-shapes (imperial source units)

# Nominal yield strength f_y (N/mm^2) by grade.
#   * EN 1993-1-1 Table 3.1 (t <= 40 mm) for European grades;
#   * ASTM specified minimum F_y for US grades (1 ksi = 6.894757 N/mm^2), e.g. 50 ksi -> 344.7.
# A992 (wide-flange), A500 Gr.C (HSS), and A36 (plate/angle/channel) are the common US construction
# defaults; see :func:`default_grade_for_section` for the shape -> grade policy.
FY_BY_GRADE = {
    # European (EN 1993-1-1)
    "S235": 235.0, "S275": 275.0, "S355": 355.0, "S420": 420.0, "S460": 460.0,
    # US (ASTM specified minimum yield)
    "A36": 248.0,        # 36 ksi  -- plates, angles, channels
    "A992": 345.0,       # 50 ksi  -- the standard for hot-rolled W-shapes
    "A572-50": 345.0,    # 50 ksi  -- HP and general structural
    "A529-50": 345.0,    # 50 ksi
    "A913-50": 345.0,    # 50 ksi
    "A500": 345.0,       # Gr.C rectangular HSS (round HSS is 46 ksi; rectangular governs here)
    "A1085": 345.0,      # 50 ksi  -- HSS
    "A53": 240.0,        # 35 ksi  -- pipe
}


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


# Imperial -> internal (N, mm) conversion factors.
_IN_MM = 25.4                    # inch -> mm
_IN2_MM2 = 645.16                # in^2 -> mm^2  (25.4^2)
_IN3_MM3 = 16_387.064            # in^3 -> mm^3  (25.4^3)
_IN4_MM4 = 416_231.4256          # in^4 -> mm^4  (25.4^4)
_LBFT_KGM = 1.488_163_94         # lb/ft -> kg/m


def load_catalog_imperial(path: str | Path = DEFAULT_US_CATALOG) -> dict[str, SectionProps]:
    """Read the US AISC catalog CSV (imperial source units), converting to internal mm units.

    Columns are stored verbatim from the AISC Shapes Database v15.0 (inch, in^2/3/4, lb/ft) so the
    numbers stay auditable against the published tables; the conversion happens here. The fillet
    radius the EN classification needs is recovered as ``r = kdes - tf`` (AISC ``kdes`` is the design
    distance from the outer flange face to the web toe of the fillet, i.e. ``tf + r``).
    """
    out: dict[str, SectionProps] = {}
    with Path(path).open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            name = row["name"].strip().upper()
            tf_in = float(row["tf_in"])
            r_in = max(float(row["kdes_in"]) - tf_in, 0.0)
            out[name] = SectionProps(
                name=name,
                shape=row["shape"].strip(),
                h=float(row["d_in"]) * _IN_MM,
                b=float(row["bf_in"]) * _IN_MM,
                tw=float(row["tw_in"]) * _IN_MM,
                tf=tf_in * _IN_MM,
                r=r_in * _IN_MM,
                A=float(row["A_in2"]) * _IN2_MM2,
                mass_kgm=float(row["mass_lbft"]) * _LBFT_KGM,
                Iy=float(row["Ix_in4"]) * _IN4_MM4,        # AISC strong axis (x) -> EN major (y)
                Wel_y=float(row["Sx_in3"]) * _IN3_MM3,
                Wpl_y=float(row["Zx_in3"]) * _IN3_MM3,
                iy=float(row["rx_in"]) * _IN_MM,
                Iz=float(row["Iy_in4"]) * _IN4_MM4,         # AISC weak axis (y) -> EN minor (z)
                Wel_z=float(row["Sy_in3"]) * _IN3_MM3,
                Wpl_z=float(row["Zy_in3"]) * _IN3_MM3,
                iz=float(row["ry_in"]) * _IN_MM,
            )
    return out


def load_default_catalog(
    eu_path: str | Path = DEFAULT_CATALOG, us_path: str | Path = DEFAULT_US_CATALOG
) -> dict[str, SectionProps]:
    """Combined catalog: European IPE/HE + US AISC W-shapes (disjoint name spaces, safe to merge).

    This is what the CLI/pipeline use by default so a single run can read either a metric/EU model or
    an imperial/US model. Tests that need a fixed standard still call :func:`load_catalog` directly.
    """
    catalog = load_catalog(eu_path)
    if Path(us_path).exists():
        catalog.update(load_catalog_imperial(us_path))
    return catalog


# ---------------------------------------------------------------------------
# Default material grade for ungraded US members (conservative, by shape)
# ---------------------------------------------------------------------------

# Revit/IFC US models routinely carry no material grade. Rather than fall back to the EN default
# (235 N/mm^2, which would understate a 50-ksi W-shape), assign the common US construction grade for
# the shape family. Always flagged in the report (see pipeline ``_fill_default_grades``), never
# silently favourable. Checked in order; first matching prefix wins (so WT/HP/HSS beat W/H).
_US_DEFAULT_GRADE: tuple[tuple[str, str], ...] = (
    ("HSS", "A500"), ("HP", "A572-50"), ("PIPE", "A53"),
    ("WT", "A992"), ("MT", "A992"), ("ST", "A992"),
    ("MC", "A36"),
    ("W", "A992"), ("M", "A992"), ("S", "A992"),  # rolled I-shapes
    ("C", "A36"), ("L", "A36"), ("PL", "A36"),
)


def default_grade_for_section(name: str | None) -> str | None:
    """Default ASTM grade for an AISC designation (e.g. ``W18X55`` -> ``A992``), else ``None``.

    Returns ``None`` for European sections and anything that is not a recognizable AISC designation,
    so the caller leaves the existing (EN) behaviour untouched and only fills US members.
    """
    if not name:
        return None
    n = name.strip().upper()
    if not any(ch.isdigit() for ch in n):  # designations always carry a size
        return None
    for prefix, grade in _US_DEFAULT_GRADE:
        if n.startswith(prefix):
            return grade
    return None


# ---------------------------------------------------------------------------
# Name mapping
# ---------------------------------------------------------------------------

# "HE 300 A" / "HE300B" / "HEM 300" -> "HEA300" / "HEB300" / "HEM300".
_HE_SUFFIX = re.compile(r"^HE0*(\d+)([ABM])$")
_HE_PREFIX = re.compile(r"^HE([ABM])0*(\d+)$")
# leading zeros in plain profiles, e.g. "IPE0300" -> "IPE300".
_PROFILE = re.compile(r"^([A-Z]+)0*(\d+)$")

# AISC (US) designations embedded in a Revit family-type name, e.g. "W Shapes W18x55",
# "W Shapes-Column W14x109", "C Shapes C8X11.5", "HSS-...-Column HSS6x6x5/8". The actual section is
# the designation token (letters + size, joined by 'x'); the family words around it carry no size and
# never match. Order matters: multi-letter prefixes (HSS/WT/MC/...) are tried before single letters.
_AISC_DESIG = re.compile(
    r"HSS\d+(?:\.\d+)?X\d+(?:\.\d+)?X[\d./]+"          # tube: HSS6X6X5/8
    r"|(?:WT|MT|ST|MC|HP)\d+(?:\.\d+)?X[\d.]+"          # tees, misc. channel, bearing pile
    r"|L\d+(?:\.\d+)?X\d+(?:\.\d+)?X[\d./]+"            # angle: L4X4X1/4
    r"|PIPE\d+(?:STD|XS|XXS|X[\d.]+)?"                  # pipe: PIPE4STD
    r"|[CWMS]\d+(?:\.\d+)?X[\d.]+",                      # I-shapes & channels: W18X55, C8X11.5
)


def _aisc_designation(raw_upper: str) -> str | None:
    """Extract the AISC designation from a raw (upper-cased) name, or ``None`` if it has none.

    Returns the *last* match so a coincidental family-word hit can't shadow the real type that
    trails it (e.g. the ``C`` in "Concrete... CC24x24" yields the column, never the family word).
    """
    matches = _AISC_DESIG.findall(raw_upper)
    return matches[-1].replace(" ", "") if matches else None


def normalize_name(raw: str) -> str:
    """Best-effort normalization of a raw Revit type name to a token (no catalog lookup).

    AISC designations (``W18x55``, ``HSS6x6x5/8``, ...) are detected first and returned canonicalized
    (upper-case, 'X' separator); otherwise the European IPE/HE normalization applies.
    """
    upper = (raw or "").upper().replace("×", "X")  # normalize the unicode multiplication sign
    aisc = _aisc_designation(upper)
    if aisc:
        return aisc
    s = re.sub(r"[\s_]+", "", upper)
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
    members, catalog, overrides=None, fuzzy_cutoff: float = 0.82, include_fuzzy: bool = False
) -> ValidationReport:
    """Map every member's ``raw_section`` -> ``section`` in place and return a validation report.

    ``members`` is any iterable of objects with ``raw_section`` and a writable ``section`` attribute
    (e.g. :class:`steelreuse.schema.ExtractedMember`). Unknown members keep ``section=None`` and are
    reported, never guessed.

    Fuzzy matches are **quarantined by default** (``include_fuzzy=False``): a near-miss like
    ``IPE300`` vs ``IPE330`` scores ~0.83 and would otherwise enter the structural analysis with the
    wrong section properties. Quarantined fuzzy members keep ``section=None`` (excluded from supply,
    passport, and matching) but are still listed in the report so the user can confirm them via an
    override CSV. Set ``include_fuzzy=True`` only when the caller has accepted that risk.
    """
    mapped: list[MappingResult] = []
    fuzzy: list[MappingResult] = []
    unknown: list[MappingResult] = []
    for m in members:
        res = map_section(m.raw_section, catalog, overrides, fuzzy_cutoff)
        if res.method == "fuzzy":
            fuzzy.append(res)
            m.section = res.canonical if include_fuzzy else None  # quarantined unless confirmed
        elif res.method == "unknown":
            unknown.append(res)
            m.section = None
        else:
            mapped.append(res)
            m.section = res.canonical
    return ValidationReport(mapped=mapped, fuzzy=fuzzy, unknown=unknown)
