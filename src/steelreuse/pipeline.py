"""End-to-end orchestration: extraction JSON -> mapping -> forces -> matching -> passport.

This is the glue the CLI and the report layer call. It performs only deterministic computation;
the (optional) LLM narrative is added downstream from these results.
"""

from __future__ import annotations

from dataclasses import dataclass

from .core.carbon import Passport, build_passport
from .core.forces import AnalyticBackend, ForceBackend, Load, member_demands
from .core.loads import AreaLoadModel, estimate_tributary_widths
from .core.sections import (
    SectionProps,
    ValidationReport,
    default_grade_for_section,
    load_default_catalog,
    resolve_members,
)
from .match.optimize import DemandSlot, MatchResult, SupplyItem, match
from .schema import ExtractedModel


def _fill_default_grades(members) -> None:
    """Assign a conservative default grade to mapped members that arrive without one.

    US (AISC) models routinely carry no material grade; rather than let the EN 235 N/mm^2 fallback
    understate a 50-ksi W-shape, fill the standard ASTM grade for the shape (see
    :func:`steelreuse.core.sections.default_grade_for_section`) and record the assumption in
    ``notes``. European members and anything unmapped are left untouched.
    """
    for m in members:
        if m.section and not m.material_grade:
            grade = default_grade_for_section(m.section)
            if grade:
                m.material_grade = grade
                m.notes = (f"{m.notes}; " if m.notes else "") + f"assumed grade {grade} (US default)"


@dataclass
class LoadModel:
    """Flat fallback load model: one UDL for every beam, one axial for every column.

    Kept for back-compatibility and quick what-ifs. For loads derived from a floor area pressure with
    tributary widths and EN 1990 factors, use :class:`steelreuse.core.loads.AreaLoadModel` instead —
    both expose :meth:`loads_for`, so the pipeline treats them interchangeably.
    """

    beam_udl_Npmm: float = 15.0      # kN/m
    column_axial_N: float = 400e3    # kN
    beam_flange_restrained: bool = True

    def loads_for(self, member) -> Load:
        if member.role == "column":
            return Load(axial_N=self.column_axial_N)
        return Load(udl_Npmm=self.beam_udl_Npmm)


def build_supply(
    donor: ExtractedModel, catalog: dict[str, SectionProps], knockdown: float = 1.0
) -> tuple[list[SupplyItem], ValidationReport]:
    report = resolve_members(donor.members, catalog)
    _fill_default_grades(donor.members)
    supply = [
        SupplyItem(id=m.id, section=m.section, grade=m.material_grade,
                   length_mm=m.length_mm, knockdown=knockdown)
        for m in donor.members if m.section  # unmapped excluded (reported separately)
    ]
    return supply, report


def build_slots(
    demand: ExtractedModel,
    loads: LoadModel | AreaLoadModel | None = None,
    backend: ForceBackend | None = None,
    steel_only: bool = False,
) -> list[DemandSlot]:
    """Turn demand members into force-based slots.

    ``steel_only`` keeps only members that mapped to a steel catalog section, so non-steel demand
    (concrete columns, bar joists, ...) does not become a slot we would try to fill with reclaimed
    steel — it would otherwise inflate the "needs new steel" count and distort the match rate.
    """
    loads = loads or LoadModel()
    backend = backend or AnalyticBackend()
    slots: list[DemandSlot] = []
    for m in demand.members:
        if steel_only and not m.section:
            continue
        load = loads.loads_for(m)
        demands = member_demands(
            m, load, backend, compression_flange_restrained=loads.beam_flange_restrained
        )
        spans = m.spans_mm or ([m.length_mm] if m.length_mm else [0.0])
        for idx, (d, span) in enumerate(zip(demands, spans, strict=False)):
            req_len = m.length_mm if m.role == "column" else span
            slots.append(DemandSlot(
                id=f"{m.id}#{idx}", member_id=m.id, role=m.role,
                required_length_mm=req_len, demand=d,
                grade=m.material_grade, design_section=m.section,
            ))
    return slots


@dataclass
class PipelineResult:
    supply_count: int
    slot_count: int
    validation: ValidationReport
    passport: Passport
    match: MatchResult


def run_pipeline(
    donor_path: str,
    demand_path: str,
    loads: LoadModel | AreaLoadModel | None = None,
    knockdown: float = 1.0,
    catalog: dict[str, SectionProps] | None = None,
    steel_only_demand: bool = False,
    tributary_from_geometry: bool = False,
) -> PipelineResult:
    catalog = catalog or load_default_catalog()
    donor = ExtractedModel.load(donor_path)
    demand = ExtractedModel.load(demand_path)

    supply, report = build_supply(donor, catalog, knockdown)
    # Map the new-design sections too, so each slot carries its design grade/section for the
    # avoided-new CO2 baseline (A1/A6). Matching itself stays force-based, not section-based.
    resolve_members(demand.members, catalog)
    _fill_default_grades(demand.members)

    # Optionally refine beam tributary widths from the model geometry (falls back to the configured
    # default for any beam without a detectable parallel neighbour).
    if tributary_from_geometry and isinstance(loads, AreaLoadModel):
        loads.tributary_overrides = estimate_tributary_widths(
            demand.members, default_m=loads.beam_tributary_width_m
        )

    slots = build_slots(demand, loads, steel_only=steel_only_demand)
    passport = build_passport(donor.members, catalog)
    result = match(supply, slots, catalog)

    return PipelineResult(
        supply_count=len(supply), slot_count=len(slots),
        validation=report, passport=passport, match=result,
    )
