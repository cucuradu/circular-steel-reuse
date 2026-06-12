"""End-to-end orchestration: extraction JSON -> mapping -> forces -> matching -> passport.

This is the glue the CLI and the report layer call. It performs only deterministic computation;
the (optional) LLM narrative is added downstream from these results.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from .core.audit import AuditSummary, apply_audit, assess_supply, load_audit_csv, recoverable_length
from .core.carbon import Passport, build_passport
from .core.connections import ConnectionPolicy
from .core.ec3_checks import MemberDemand
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
    donor: ExtractedModel,
    catalog: dict[str, SectionProps],
    knockdown: float = 1.0,
    include_unverified: bool = False,
) -> tuple[list[SupplyItem], ValidationReport, AuditSummary]:
    """Build the reclaimed-steel supply from the donor model.

    A donor member becomes supply only if it (a) maps to a catalog section (unmapped is reported
    separately) **and** (b) passes the pre-demolition audit (:mod:`steelreuse.core.audit`): unverified
    or unsuitable-condition members are quarantined, exactly like a fuzzy section match, so they cannot
    silently enter analysis. Each admitted member carries its **audit-derived knockdown** on f_y and its
    **recoverable length** (the usable stock after de-construction). Members with no audit data behave
    as before: admitted at the run's default ``knockdown``, full length.
    """
    report = resolve_members(donor.members, catalog)
    _fill_default_grades(donor.members)
    audit = assess_supply(donor.members, default_knockdown=knockdown,
                          include_unverified=include_unverified)
    supply: list[SupplyItem] = []
    for m in donor.members:
        if not m.section:  # unmapped -> excluded (reported separately)
            continue
        decision = audit.decisions.get(m.id)
        if decision is not None and not decision.admitted:
            continue  # quarantined by the audit (reported via the AuditSummary)
        kd = decision.knockdown if decision is not None else knockdown
        supply.append(SupplyItem(id=m.id, section=m.section, grade=m.material_grade,
                                 length_mm=recoverable_length(m), knockdown=kd))
    return supply, report, audit


def _construction_demand(loads, member_id: str, span_mm: float) -> tuple[str, MemberDemand] | None:
    """Bare-steel erection-stage envelope entry for a beam span (EN 1991-1-6), or ``None`` if off.

    The defining feature of the stage is the **missing slab**: the compression flange is unrestrained,
    so chi_LT applies in earnest, under full permanent load (wet slab) + the construction live load.
    Simply-supported statics are used in both the analytic and the frame path — during erection the
    diaphragm/continuity the frame model assumes is not yet present, so the isolated-span idealisation
    is the honest one for this stage. SLS deflection is not re-checked here (a temporary situation).
    """
    if not getattr(loads, "construction_stage", False) or span_mm <= 0:
        return None
    w = loads.construction_udl_Npmm(member_id)
    return ("ULS construction stage", MemberDemand(
        My_Ed=w * span_mm**2 / 8.0, Vz_Ed=w * span_mm / 2.0, L=span_mm,
        compression_flange_restrained=False,
    ))


def _uplift_demand(loads, member_id: str, span_mm: float) -> tuple[str, MemberDemand] | None:
    """Wind-uplift load-reversal envelope entry for a ROOF beam span, or ``None`` if off / no reversal.

    Net upward wind (suction) on a light roof reverses the bending: the BOTTOM flange goes into
    compression, where no slab restrains it — the one situation the restrained-flange default would
    otherwise miss. The entry uses isolated-span statics with the magnitude of the net upward load
    (``gamma_Q*W_up - 1.0*g_k``, permanent favourable per EN 1990) and ``chi_LT`` in earnest. When the
    permanent load wins (net <= 0) there is no reversal and no entry. SLS is not re-checked.
    """
    if getattr(loads, "uplift_kpa", 0.0) <= 0 or span_mm <= 0:
        return None
    w = loads.uplift_udl_Npmm(member_id)
    if w <= 0:
        return None
    return ("ULS wind uplift", MemberDemand(
        My_Ed=w * span_mm**2 / 8.0, Vz_Ed=w * span_mm / 2.0, L=span_mm,
        compression_flange_restrained=False,
    ))


# Beams whose mid-height is within this of the highest beam belong to the roof level (wind uplift).
_ROOF_LEVEL_TOL_MM = 500.0


def _roof_beam_ids(members) -> set[str]:
    """Ids of beams at the model's top framing level (the only ones wind uplift acts on).

    Needs geometry: members without coordinates can't be placed on a level and are left out —
    documented limitation (an all-roof single-storey model without coordinates sees no uplift case).
    """
    mid_z: dict[str, float] = {}
    for m in members:
        if m.role == "beam" and m.start_xyz and m.end_xyz:
            mid_z[m.id] = (m.start_xyz[2] + m.end_xyz[2]) / 2.0
    if not mid_z:
        return set()
    top = max(mid_z.values())
    return {i for i, z in mid_z.items() if z >= top - _ROOF_LEVEL_TOL_MM}


# How close a column endpoint must be (3-D) to a span joint for the joint to count as a real support.
# Generous vs the extractor's 50 mm curve-projection tolerance: the joint position is re-derived here by
# interpolating cumulative span fractions along the member axis, and a column top can sit half a beam
# depth below the beam centreline.
_SPAN_SUPPORT_TOL_MM = 300.0


def _verified_spans(member, column_pts: list[tuple[float, float, float]]) -> list[float]:
    """Keep an interior span split only where a column actually supports the joint.

    The extractor splits a demand beam at every member endpoint that lands on its curve — deliberately
    including *other beams'* endpoints, because the frame solver needs those crossing points as
    connection nodes (a joist framing into a girder transfers its reaction there). But on the analytic
    path each span is checked as an isolated simply-supported piece, and a joist *loads* the girder, it
    does not support it: treating the crossing as a support understates the girder moment (M ~ L^2) and
    produces short slots no single reusable member could fill. So here, with column geometry available,
    interior joints with no column endpoint nearby are merged back together. Members without geometry —
    or models whose columns carry no coordinates — keep their extracted spans unchanged (the frame path
    is unaffected either way: it verifies supports physically and splits slots only at column nodes).
    """
    spans = member.spans_mm or []
    s, e = member.start_xyz, member.end_xyz
    if len(spans) <= 1 or not s or not e or not column_pts:
        return spans
    total = float(sum(spans))
    if total <= 0:
        return spans
    tol2 = _SPAN_SUPPORT_TOL_MM**2
    merged = [spans[0]]
    cum = 0.0
    for i in range(len(spans) - 1):
        cum += spans[i]
        f = cum / total
        p = [s[a] + (e[a] - s[a]) * f for a in range(3)]
        supported = any(
            (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 + (p[2] - q[2]) ** 2 <= tol2
            for q in column_pts)
        if supported:
            merged.append(spans[i + 1])
        else:
            merged[-1] += spans[i + 1]
    return merged


def build_slots(
    demand: ExtractedModel,
    loads: LoadModel | AreaLoadModel | None = None,
    backend: ForceBackend | None = None,
    steel_only: bool = False,
    frame_slots: dict[str, list] | None = None,
) -> list[DemandSlot]:
    """Turn demand members into force-based slots.

    ``steel_only`` keeps only members that mapped to a steel catalog section, so non-steel demand
    (concrete columns, bar joists, ...) does not become a slot we would try to fill with reclaimed
    steel — it would otherwise inflate the "needs new steel" count and distort the match rate.

    ``frame_slots`` (optional) carries the reuse slots from a global **frame analysis**
    (:attr:`steelreuse.core.frame.FrameResult.slots_by_member`): ``{member_id: [FrameSlot, ...]}``, where
    each :class:`~steelreuse.core.frame.FrameSlot` is one physical member (a column folds its storey lifts
    into one slot) or one inter-column span of a continuous beam, carrying the solved action-effect
    envelope. When a member appears there its forces come from the solved frame; members absent from it
    fall back to the per-member analytic load path below — a robust hybrid for real models where some
    members lack usable geometry.
    """
    loads = loads or LoadModel()
    backend = backend or AnalyticBackend()
    column_pts = [tuple(p) for c in demand.members if c.role == "column"
                  for p in (c.start_xyz, c.end_xyz) if p]
    roof_ids = _roof_beam_ids(demand.members) if getattr(loads, "uplift_kpa", 0.0) > 0 else set()
    slots: list[DemandSlot] = []
    for m in demand.members:
        if steel_only and not m.section:
            continue
        # Frame path: forces and slot structure come from the global solve (see core/frame.py).
        if frame_slots is not None:
            member_slots = frame_slots.get(m.id)
            if member_slots:
                for s in member_slots:
                    envelope = list(s.demands)
                    if m.role == "beam":
                        extra = _construction_demand(loads, m.id, s.required_length_mm)
                        if extra:
                            envelope.append(extra)
                        if m.id in roof_ids:
                            extra = _uplift_demand(loads, m.id, s.required_length_mm)
                            if extra:
                                envelope.append(extra)
                    slots.append(DemandSlot(
                        id=s.slot_id, member_id=m.id, role=m.role,
                        required_length_mm=s.required_length_mm,
                        demand=envelope[0][1], demands=envelope,
                        grade=m.material_grade, design_section=m.section,
                    ))
                continue
        # Analytic path: drop span splits that have no column under the joint (a joist crossing is a
        # load on the girder, not a support) before checking each span as an isolated piece.
        if m.role == "beam":
            spans_v = _verified_spans(m, column_pts)
            if spans_v != (m.spans_mm or []):
                n_merged = len(m.spans_mm) - len(spans_v)
                m = replace(m, spans_mm=spans_v,
                            notes=((m.notes + "; ") if m.notes else "")
                            + f"merged {n_merged} unsupported span joint(s) — no column at the split")
        # One demand list per load combination (aligned by span index — same member geometry), so
        # every slot carries the full envelope the matcher verifies it against.
        per_combo = [
            (name, member_demands(
                m, load, backend, ky=m.ky or 1.0, kz=m.kz or 1.0,
                compression_flange_restrained=loads.beam_flange_restrained))
            for name, load in loads.combination_loads(m)
        ]
        spans = m.spans_mm or ([m.length_mm] if m.length_mm else [0.0])
        n_demands = len(per_combo[0][1])
        for idx in range(n_demands):
            span = spans[idx] if idx < len(spans) else spans[-1]
            combo_demands = [(name, dl[idx]) for name, dl in per_combo]
            if m.role == "beam":
                extra = _construction_demand(loads, m.id, span or 0.0)
                if extra:
                    combo_demands.append(extra)
                if m.id in roof_ids:
                    extra = _uplift_demand(loads, m.id, span or 0.0)
                    if extra:
                        combo_demands.append(extra)
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
    audit: AuditSummary | None = None  # pre-demolition-audit provenance (always set by run_pipeline)
    donor: ExtractedModel | None = None    # resolved donor model (m.section/audit fields populated)
    demand: ExtractedModel | None = None   # resolved demand model (m.section populated)
    slots: list[DemandSlot] = field(default_factory=list)
    supply: list[SupplyItem] = field(default_factory=list)  # admitted reclaimed stock (post-audit)


def run_pipeline(
    donor_path: str,
    demand_path: str,
    loads: LoadModel | AreaLoadModel | None = None,
    knockdown: float = 1.0,
    include_unverified: bool = False,
    pda_csv: str | None = None,
    catalog: dict[str, SectionProps] | None = None,
    steel_only_demand: bool = False,
    tributary_from_geometry: bool = False,
    allow_cutting: bool = False,
    connection_screen: bool = False,
    frame_analysis: bool = False,
    second_order: bool = False,
    wind_kpa: float = 0.0,
    seismic_cs: float = 0.0,
    objective: str = "co2",
) -> PipelineResult:
    catalog = catalog or load_default_catalog()
    # Frame analysis needs the area-based load model (the floor pressure on the beams is what the
    # solved load path distributes). Default it like the CLI does; an explicit legacy flat
    # LoadModel cannot drive a frame solve, and silently falling back to analytic forces gave a
    # different answer than the caller asked for — refuse instead.
    if frame_analysis:
        if loads is None:
            loads = AreaLoadModel()
        elif not isinstance(loads, AreaLoadModel):
            raise ValueError(
                "frame_analysis requires the area-based load model (AreaLoadModel); the legacy "
                "flat LoadModel has no floor pressure to distribute through the frame — pass an "
                "AreaLoadModel (or drop --beam-udl/--column-axial on the CLI)"
            )
    donor = ExtractedModel.load(donor_path)
    demand = ExtractedModel.load(demand_path)

    # Merge an external pre-demolition-audit CSV onto the donor members (condition / verification),
    # if one is supplied — the audit may live alongside the BIM export rather than in it.
    if pda_csv:
        apply_audit(donor.members, load_audit_csv(pda_csv))

    supply, report, audit = build_supply(donor, catalog, knockdown, include_unverified)
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
    frame_slots = None
    if frame_analysis:
        # Route the sway imperfection (--phi) to the frame-level EHF treatment (not the member-level
        # notional moment); P-Delta is auto-enabled there whenever phi > 0.
        opts = FrameOptions(notional_phi=loads.notional_phi, second_order=second_order,
                            wind_kpa=wind_kpa, seismic_cs=seismic_cs)
        frame_result = analyze_frame(demand.members, loads, catalog, options=opts)
        frame_slots = frame_result.slots_by_member if frame_result.ok else None

    slots = build_slots(demand, loads, steel_only=steel_only_demand,
                        frame_slots=frame_slots)
    passport = build_passport(donor.members, catalog)
    result = match(supply, slots, catalog, allow_cutting=allow_cutting,
                   connection_policy=ConnectionPolicy() if connection_screen else None,
                   objective=objective)

    return PipelineResult(
        supply_count=len(supply), slot_count=len(slots),
        validation=report, passport=passport, match=result, frame=frame_result, audit=audit,
        donor=donor, demand=demand, slots=slots, supply=supply,
    )
