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
DEFAULT_FACTORS = Path(__file__).resolve().parent.parent / "data" / "carbon" / "factors.csv"


@dataclass(frozen=True)
class CarbonFactor:
    a1a3: float            # kgCO2e/kg to produce new (A1-A3)
    reuse_process: float   # kgCO2e/kg to recover/refabricate for reuse
    source: str = ""

    @property
    def saved_per_kg(self) -> float:
        return self.a1a3 - self.reuse_process


def load_factors(path: str | Path = DEFAULT_FACTORS) -> dict[str, CarbonFactor]:
    out: dict[str, CarbonFactor] = {}
    with Path(path).open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            out[row["material"].strip().lower()] = CarbonFactor(
                a1a3=float(row["a1a3_kgco2e_per_kg"]),
                reuse_process=float(row["reuse_process_kgco2e_per_kg"]),
                source=row.get("source", ""),
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
        entries.append(PassportEntry(
            id=m.id, section=m.section, grade=m.material_grade,
            length_mm=m.length_mm, mass_kg=mass, volume_m3=member_volume_m3(sec, m.length_mm),
            ec_new_kgco2e=mass * f.a1a3,
            ec_reuse_kgco2e=mass * f.reuse_process,
            ec_saved_kgco2e=mass * f.saved_per_kg,
            verification_status=getattr(m, "verification_status", None),
            condition_grade=getattr(m, "condition_grade", None),
        ))
    return Passport(entries=entries)
