"""End-to-end orchestration: extraction JSON -> mapping -> forces -> matching -> passport.

This is the glue the CLI and the report layer call. It performs only deterministic computation;
the (optional) LLM narrative is added downstream from these results.
"""

from __future__ import annotations

from dataclasses import dataclass

from .core.carbon import Passport, build_passport
from .core.forces import AnalyticBackend, ForceBackend, Load, member_demands
from .core.frame import FrameOptions, FrameResult, analyze_frame
from .core.loads import AreaLoadModel, estimate_column_loads, estimate_tributary_widths
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

    def combination_loads(self, member) -> list[tuple[str, Load]]:
        """Single-combination envelope for the flat fallback model (no imperfection case)."""
        return [("ULS gravity", self.loads_for(member))]


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
    demands_by_member: dict[str, list[tuple[str, object]]] | None = None,
) -> list[DemandSlot]:
    """Turn demand members into force-based slots.

    ``steel_only`` keeps only members that mapped to a steel catalog section, so non-steel demand
    (concrete columns, bar joists, ...) does not become a slot we would try to fill with reclaimed
    steel — it would otherwise inflate the "needs new steel" count and distort the match rate.

    ``demands_by_member`` (optional) carries action-effect envelopes from a global **frame analysis**
    (:func:`steelreuse.core.frame.analyze_frame`): ``{member_id: [(combo_name, MemberDemand), ...]}``.
    When a member appears there, its forces come from the solved frame (one slot for the whole element);
    members absent from it fall back to the per-member analytic load path below — a robust hybrid for
    real models where some members lack usable geometry.
    """
    loads = loads or LoadModel()
    backend = backend or AnalyticBackend()
    slots: list[DemandSlot] = []
    for m in demand.members:
        if steel_only and not m.section:
            continue
        # Frame path: forces come from the global solve. A column or single-span member is one solved
        # element -> one slot; a continuous multi-span beam is split at its interior supports into one
        # solved element per span (id'd `{id}#k`), so it yields one slot per span (see core/frame.py).
        if demands_by_member:
            if m.role == "column" or len(m.spans_mm) <= 1:
                frame_combos = demands_by_member.get(m.id)
                if frame_combos:
                    req_len = m.length_mm if m.role == "column" else (
                        m.spans_mm[0] if m.spans_mm else (m.length_mm or 0.0))
                    slots.append(DemandSlot(
                        id=f"{m.id}#0", member_id=m.id, role=m.role,
                        required_length_mm=req_len, demand=frame_combos[0][1], demands=frame_combos,
                        grade=m.material_grade, design_section=m.section,
                    ))
                    continue
            else:
                per_span = [demands_by_member.get(f"{m.id}#{k}") for k in range(len(m.spans_mm))]
                if all(per_span):
                    for k, fc in enumerate(per_span):
                        slots.append(DemandSlot(
                            id=f"{m.id}#{k}", member_id=m.id, role=m.role,
                            required_length_mm=m.spans_mm[k], demand=fc[0][1], demands=fc,
                            grade=m.material_grade, design_section=m.section,
                        ))
                    continue
        # One demand list per load combination (aligned by span index — same member geometry), so
        # every slot carries the full envelope the matcher verifies it against.
        per_combo = [
            (name, member_demands(
                m, load, backend, compression_flange_restrained=loads.beam_flange_restrained))
            for name, load in loads.combination_loads(m)
        ]
        spans = m.spans_mm or ([m.length_mm] if m.length_mm else [0.0])
        n_demands = len(per_combo[0][1])
        for idx in range(n_demands):
            span = spans[idx] if idx < len(spans) else spans[-1]
            combo_demands = [(name, dl[idx]) for name, dl in per_combo]
            req_len = m.length_mm if m.role == "column" else span
            slots.append(DemandSlot(
                id=f"{m.id}#{idx}", member_id=m.id, role=m.role,
                required_length_mm=req_len, demand=combo_demands[0][1], demands=combo_demands,
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
    frame: FrameResult | None = None   # set when frame_analysis was used


def run_pipeline(
    donor_path: str,
    demand_path: str,
    loads: LoadModel | AreaLoadModel | None = None,
    knockdown: float = 1.0,
    catalog: dict[str, SectionProps] | None = None,
    steel_only_demand: bool = False,
    tributary_from_geometry: bool = False,
    allow_cutting: bool = False,
    frame_analysis: bool = False,
    second_order: bool = False,
    wind_kpa: float = 0.0,
    seismic_cs: float = 0.0,
) -> PipelineResult:
    catalog = catalog or load_default_catalog()
    donor = ExtractedModel.load(donor_path)
    demand = ExtractedModel.load(demand_path)

    supply, report = build_supply(donor, catalog, knockdown)
    # Map the new-design sections too, so each slot carries its design grade/section for the
    # avoided-new CO2 baseline (A1/A6). Matching itself stays force-based, not section-based.
    resolve_members(demand.members, catalog)
    _fill_default_grades(demand.members)

    # Optionally refine per-member loads from the model geometry: beam tributary widths, and column
    # tributary areas + floor counts (each falls back to the configured default where the geometry is
    # insufficient — an isolated beam/column, too little grid to size a bay, etc.).
    if tributary_from_geometry and isinstance(loads, AreaLoadModel):
        loads.tributary_overrides = estimate_tributary_widths(
            demand.members, default_m=loads.beam_tributary_width_m
        )
        loads.column_area_overrides, loads.column_floor_overrides = estimate_column_loads(
            demand.members, default_area_m2=loads.column_tributary_area_m2
        )

    # Optionally derive member forces from a global frame analysis instead of per-member closed forms.
    # Beam tributary widths (above) still set the floor load; column axials then come from the solved
    # load path. Falls back per member to the analytic path wherever the frame can't be built/solved.
    frame_result: FrameResult | None = None
    demands_by_member = None
    if frame_analysis and isinstance(loads, AreaLoadModel):
        # Route the sway imperfection (--phi) to the frame-level EHF treatment (not the member-level
        # notional moment); P-Delta is auto-enabled there whenever phi > 0.
        opts = FrameOptions(notional_phi=loads.notional_phi, second_order=second_order,
                            wind_kpa=wind_kpa, seismic_cs=seismic_cs)
        frame_result = analyze_frame(demand.members, loads, catalog, options=opts)
        demands_by_member = frame_result.demands_by_member or None

    slots = build_slots(demand, loads, steel_only=steel_only_demand,
                        demands_by_member=demands_by_member)
    passport = build_passport(donor.members, catalog)
    result = match(supply, slots, catalog, allow_cutting=allow_cutting)

    return PipelineResult(
        supply_count=len(supply), slot_count=len(slots),
        validation=report, passport=passport, match=result, frame=frame_result,
    )
