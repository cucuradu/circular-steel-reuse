"""End-to-end orchestration: extraction JSON -> mapping -> forces -> matching -> passport.

This is the glue the CLI and the report layer call. It performs only deterministic computation;
the (optional) LLM narrative is added downstream from these results.
"""

from __future__ import annotations

from dataclasses import dataclass

from .core.carbon import Passport, build_passport
from .core.forces import AnalyticBackend, ForceBackend, Load, member_demands
from .core.sections import SectionProps, ValidationReport, load_catalog, resolve_members
from .match.optimize import DemandSlot, MatchResult, SupplyItem, match
from .schema import ExtractedModel


@dataclass
class LoadModel:
    """Default load assumptions used when no analysis model is supplied (see flaw #6 — explicit)."""

    beam_udl_Npmm: float = 15.0      # kN/m
    column_axial_N: float = 400e3    # kN
    beam_flange_restrained: bool = True


def build_supply(
    donor: ExtractedModel, catalog: dict[str, SectionProps], knockdown: float = 1.0
) -> tuple[list[SupplyItem], ValidationReport]:
    report = resolve_members(donor.members, catalog)
    supply = [
        SupplyItem(id=m.id, section=m.section, grade=m.material_grade,
                   length_mm=m.length_mm, knockdown=knockdown)
        for m in donor.members if m.section  # unmapped excluded (reported separately)
    ]
    return supply, report


def build_slots(
    demand: ExtractedModel,
    loads: LoadModel | None = None,
    backend: ForceBackend | None = None,
) -> list[DemandSlot]:
    loads = loads or LoadModel()
    backend = backend or AnalyticBackend()
    slots: list[DemandSlot] = []
    for m in demand.members:
        if m.role == "column":
            load = Load(axial_N=loads.column_axial_N)
        else:
            load = Load(udl_Npmm=loads.beam_udl_Npmm)
        demands = member_demands(
            m, load, backend, compression_flange_restrained=loads.beam_flange_restrained
        )
        spans = m.spans_mm or ([m.length_mm] if m.length_mm else [0.0])
        for idx, (d, span) in enumerate(zip(demands, spans, strict=False)):
            req_len = m.length_mm if m.role == "column" else span
            slots.append(DemandSlot(
                id=f"{m.id}#{idx}", member_id=m.id, role=m.role,
                required_length_mm=req_len, demand=d,
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
    loads: LoadModel | None = None,
    knockdown: float = 1.0,
    catalog: dict[str, SectionProps] | None = None,
) -> PipelineResult:
    catalog = catalog or load_catalog()
    donor = ExtractedModel.load(donor_path)
    demand = ExtractedModel.load(demand_path)

    supply, report = build_supply(donor, catalog, knockdown)
    slots = build_slots(demand, loads)
    passport = build_passport(donor.members, catalog)
    result = match(supply, slots, catalog)

    return PipelineResult(
        supply_count=len(supply), slot_count=len(slots),
        validation=report, passport=passport, match=result,
    )
