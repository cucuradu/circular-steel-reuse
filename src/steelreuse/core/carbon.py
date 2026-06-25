"""Mass + embodied-carbon material passport (Phase 3).

For each member: mass, volume, the embodied carbon of buying it new (A1-A3), the small process
carbon of reusing it (clean/test/refabricate), and the net CO2 *saved* by reusing instead of new.

Factors are loaded from ``steelreuse/data/carbon/factors.csv`` (ICE values; swap for Okobaudat/Climatiq).
All numbers are plain arithmetic here so they can later be injected into reports verbatim — the LLM
never recomputes them.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from .sections import SectionProps

# Ships inside the package (see sections.py) so the factor table is found in an installed wheel too.
_CARBON_DIR = Path(__file__).resolve().parent.parent / "data" / "carbon"

# Selectable embodied-carbon datasets (Scenario Sweep §4 "carbon-factor dataset axis"). Each is a
# self-contained, provenance-stamped factors CSV; they differ in the A1-A3 *production* figure — the
# number the EPD databases actually disagree on — while the reuse-process and end-of-life credits are
# held at common reference values (those are process/credit estimates from SCI P427 / worldsteel
# module-D / Cambridge-Allwood, orthogonal to the production database). Swapping the active set answers
# the standard LCA critique "how much does my saving depend on which database I trust?".
CARBON_DATASETS: dict[str, Path] = {
    "ice_v3": _CARBON_DIR / "factors.csv",                 # Circular Ecology ICE v3 (2019) — default
    "ice_v4": _CARBON_DIR / "factors_ice_v4.csv",          # ICE v4 (2024) = Climatiq "Steel - Section"
    "oekobaudat": _CARBON_DIR / "factors_oekobaudat.csv",  # German EPD via Oekobaudat (bauforumstahl/IBU)
}
DEFAULT_CARBON_DATASET = "ice_v3"
# Back-compat alias: the historical default path. Existing callers (and the validated case study)
# keep loading the ICE v3 table byte-identically.
DEFAULT_FACTORS = CARBON_DATASETS[DEFAULT_CARBON_DATASET]


def factors_path(dataset: str | None = None) -> Path:
    """Path to a named carbon-factor dataset (``None`` -> the default ICE v3 set)."""
    if dataset is None:
        return DEFAULT_FACTORS
    try:
        return CARBON_DATASETS[dataset]
    except KeyError:
        raise ValueError(
            f"unknown carbon dataset {dataset!r}; expected one of {tuple(CARBON_DATASETS)}"
        ) from None


@dataclass(frozen=True)
class CarbonFactor:
    a1a3: float            # kgCO2e/kg to produce new (A1-A3)
    reuse_process: float   # kgCO2e/kg to recover/refabricate for reuse
    source: str = ""
    # --- End-of-life counterfactual credits (kgCO2e per kg of steel sent to that fate) -----------
    # What one kg of donor steel would have saved the wider system had it NOT been reused here.
    # These are parameters, not constants — the literature ranges are wide and the CSV is the place
    # to localize them. Representative defaults shipped in factors.csv:
    #
    #   recycle_credit — conventional EAF scrap recycling (~99 % recovery for structural steel).
    #     Net system benefit of recycling one kg = avoided primary production minus the EAF
    #     re-melting/re-rolling burden. Literature range ~ 0.4-0.7 kgCO2e/kg (worldsteel LCI
    #     methodology scrap/module-D credit; SCI P427 reuse-protocol discussion; EN 15804/15978
    #     module D avoided-burden convention). Shipped mid value: 0.55.
    #
    #   reroll_credit — direct re-rolling of reclaimed sections WITHOUT re-melting
    #     (Cambridge/Allwood line of work, "Sustainable Materials: with both eyes open" —
    #     reuse-without-melting chapter). PILOT-SCALE only: flagged research-grade, not an
    #     established industrial route. It avoids the melt but pays re-heating/rolling/logistics,
    #     so the credit sits between recycling and full reuse. Documented range ~ 0.9-1.4;
    #     shipped conservative value: 1.0 (vs this dataset's full-reuse saving of
    #     a1a3 - reuse_process = 1.45).
    #
    # Sanity ordering the defaults respect: 0 < recycle_credit < reroll_credit < saved_per_kg —
    # reuse beats re-rolling beats recycling, which is the premise of the tool.
    recycle_credit: float = 0.0
    reroll_credit: float = 0.0

    @property
    def saved_per_kg(self) -> float:
        return self.a1a3 - self.reuse_process


def _opt_float(row: dict, key: str) -> float:
    """Optional CSV column: a missing column or empty cell -> 0.0 (old factor files keep working)."""
    v = row.get(key)
    return float(v) if v not in (None, "") else 0.0


def load_factors(path: str | Path | None = None, *,
                 dataset: str | None = None) -> dict[str, CarbonFactor]:
    """Load the carbon-factor table.

    Pass ``dataset`` to pick a named bundled set (see :data:`CARBON_DATASETS`); pass ``path`` to load
    an arbitrary CSV. With neither, the default ICE v3 set is used (historical behaviour)."""
    if path is None:
        path = factors_path(dataset)
    out: dict[str, CarbonFactor] = {}
    # The factor file carries version/source provenance as leading ``#`` comment lines (Roadmap §1.2,
    # parsed for the evidence package by core.rules); skip them so the CSV reader sees only data.
    with Path(path).open(newline="", encoding="utf-8") as fh:
        data_lines = [ln for ln in fh if not ln.lstrip().startswith("#")]
        for row in csv.DictReader(data_lines):
            out[row["material"].strip().lower()] = CarbonFactor(
                a1a3=float(row["a1a3_kgco2e_per_kg"]),
                reuse_process=float(row["reuse_process_kgco2e_per_kg"]),
                source=row.get("source", ""),
                recycle_credit=_opt_float(row, "recycle_credit_kgco2e_per_kg"),
                reroll_credit=_opt_float(row, "reroll_credit_kgco2e_per_kg"),
            )
    return out


def member_mass_kg(sec: SectionProps, length_mm: float) -> float:
    """Mass from catalog mass/m (kg/m * m)."""
    return sec.mass_kgm * (length_mm / 1000.0)


def member_volume_m3(sec: SectionProps, length_mm: float) -> float:
    return (sec.A / 1e6) * (length_mm / 1000.0)  # A mm^2 -> m^2, length mm -> m


@dataclass
class PassportEntry:
    id: str
    section: str
    grade: str | None
    length_mm: float
    mass_kg: float
    volume_m3: float
    ec_new_kgco2e: float      # if procured new
    ec_reuse_kgco2e: float    # process carbon to reuse
    ec_saved_kgco2e: float    # new - reuse
    # Pre-demolition-audit provenance (None when the member was not audited) — the material passport
    # is the natural home for "where did this come from and how do we know its grade".
    verification_status: str | None = None
    condition_grade: str | None = None


@dataclass
class Passport:
    entries: list[PassportEntry]

    @property
    def total_mass_kg(self) -> float:
        return sum(e.mass_kg for e in self.entries)

    @property
    def total_saved_kgco2e(self) -> float:
        return sum(e.ec_saved_kgco2e for e in self.entries)

    @property
    def total_new_kgco2e(self) -> float:
        return sum(e.ec_new_kgco2e for e in self.entries)


def build_passport(
    members,
    catalog: dict[str, SectionProps],
    factors: dict[str, CarbonFactor] | None = None,
) -> Passport:
    """Material passport for all members whose section mapped (unknown sections are skipped)."""
    factors = factors or load_factors()
    f = factors["steel"]
    entries: list[PassportEntry] = []
    for m in members:
        if not m.section or m.section not in catalog:
            continue  # unmapped -> excluded from the passport (reported separately by the mapping layer)
        sec = catalog[m.section]
        mass = member_mass_kg(sec, m.length_mm)
        from .deconstruction import deconstruction_treatment
        mult = deconstruction_treatment(m).process_multiplier
        reuse_per_kg = f.reuse_process * mult
        entries.append(PassportEntry(
            id=m.id, section=m.section, grade=m.material_grade,
            length_mm=m.length_mm, mass_kg=mass, volume_m3=member_volume_m3(sec, m.length_mm),
            ec_new_kgco2e=mass * f.a1a3,
            ec_reuse_kgco2e=mass * reuse_per_kg,
            ec_saved_kgco2e=mass * (f.a1a3 - reuse_per_kg),
            verification_status=getattr(m, "verification_status", None),
            condition_grade=getattr(m, "condition_grade", None),
        ))
    return Passport(entries=entries)


def passport_rows(passport: Passport, assignments=()) -> list[dict]:
    """Flatten the material passport to per-member rows for CSV/JSON export.

    Each row carries the donor's identity, mass, audit provenance (condition/verification) and the
    avoided-new carbon; for the members the matcher actually reused, the EN 1993 reuse verdict (status,
    utilisation, slot) is joined in. ``assignments`` is any iterable of objects exposing
    ``supply_id``/``slot_id``/``status``/``utilization`` (duck-typed to avoid importing the matcher)."""
    by_supply = {a.supply_id: a for a in assignments}
    rows: list[dict] = []
    for e in passport.entries:
        a = by_supply.get(e.id)
        rows.append({
            "id": e.id,
            "section": e.section,
            "grade": e.grade or "",
            "length_mm": round(e.length_mm, 1),
            "mass_kg": round(e.mass_kg, 1),
            "condition_grade": e.condition_grade or "",
            "verification_status": e.verification_status or "",
            "ec_new_kgco2e": round(e.ec_new_kgco2e, 1),
            "ec_reuse_kgco2e": round(e.ec_reuse_kgco2e, 1),
            "ec_saved_kgco2e": round(e.ec_saved_kgco2e, 1),
            "reuse_verdict": a.status if a else "not reused",
            "reuse_slot": a.slot_id if a else "",
            "reuse_utilisation": round(a.utilization, 3) if a else "",
        })
    return rows
