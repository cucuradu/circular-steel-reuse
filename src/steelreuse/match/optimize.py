"""The flagship: match reclaimed steel (supply) to new-design member slots (demand).

Pipeline:
  1. Build a **sparse feasibility mask** — a (supply, slot) pair is allowed only if the reclaimed
     member is long enough AND passes the exact EN 1993 check for that slot's forces. Most pairs are
     infeasible and never enter the model (this is what tames the MILP size, flaw #7).
  2. Solve a **MILP** (PuLP/CBC): binary x_ij, each slot <= 1 supply, maximizing CO2 saved minus an
     off-cut penalty minus a per-reuse connection-refabrication penalty (flaw #1 — connections are
     never treated as free). Each supply is used at most once by default; with ``allow_cutting`` the
     **cutting-stock** model instead bounds each donor by its length so it can be cut into several
     pieces for several slots.
  3. If the solver is unavailable/stalls, fall back to a greedy assignment.

All numbers are computed here so they can be injected verbatim into the report; the LLM never
recomputes them.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import pulp

from ..core.carbon import CarbonFactor, load_factors
from ..core.connections import ConnectionPolicy, screen_pair
from ..core.ec3_checks import MemberDemand, check_member
from ..core.sections import SectionProps

CUT_TOLERANCE_MM = 50.0   # extra length needed beyond the structural span for cutting/fit-up


@dataclass
class SupplyItem:
    id: str
    section: str            # canonical catalog name (must be mapped)
    grade: str | None
    length_mm: float
    knockdown: float = 1.0  # reclaimed f_y reduction


@dataclass
class DemandSlot:
    id: str                 # unique slot id, e.g. "N1#0"
    member_id: str
    role: str
    required_length_mm: float
    demand: MemberDemand               # primary (gravity) combination — kept for back-compat
    grade: str | None = None         # design grade of the new member (for the avoided-new baseline)
    design_section: str | None = None  # canonical section the new design specified, if known
    # The full ULS load-combination envelope: (name, demand) per design situation. When unset, the
    # slot behaves as a single gravity combination (``demand``). The matcher checks the member against
    # every combination and reports the governing (worst-utilisation) one; a reuse passes only if it
    # passes them all.
    demands: list[tuple[str, MemberDemand]] | None = None

    @property
    def combinations(self) -> list[tuple[str, MemberDemand]]:
        return self.demands if self.demands else [("ULS gravity", self.demand)]


@dataclass
class Assignment:
    supply_id: str
    slot_id: str
    section: str
    utilization: float
    status: str             # OK | REVIEW
    offcut_mm: float
    co2_saved_kg: float
    score: float
    chi_lt: float | None = None       # LTB reduction used (1.0 if restrained, None if no bending)
    chi_lt_if_free: float | None = None  # what chi_LT would be if the flange were unrestrained
    governing_combination: str = "ULS gravity"  # load combination that drove the utilisation
    # Geometric connection-compatibility vs the slot's design section (core/connections.py):
    # "ok" | "review" | "incompatible" | "unknown" (no design section). Purely informational unless
    # the screen is enabled, in which case "incompatible" pairs never get this far.
    connection_status: str = "unknown"
    connection_note: str = ""


@dataclass
class MatchResult:
    assignments: list[Assignment]
    unmatched_slots: list[str]
    unused_supply: list[str]
    solver_status: str
    weights: dict = field(default_factory=dict)
    # Cutting-stock mode only: leftover length (mm) of each donor that was cut, after all its pieces
    # (empty in the default one-piece-per-donor mode, where the off-cut is the per-assignment value).
    donor_leftover_mm: dict[str, float] = field(default_factory=dict)

    @property
    def total_co2_saved_kg(self) -> float:
        return sum(a.co2_saved_kg for a in self.assignments)

    @property
    def total_offcut_mm(self) -> float:
        return sum(a.offcut_mm for a in self.assignments)

    @property
    def total_donor_leftover_mm(self) -> float:
        """Total reusable remainder across cut donors (cutting-stock mode)."""
        return sum(self.donor_leftover_mm.values())

    @property
    def n_reused(self) -> int:
        return len(self.assignments)

    @property
    def proven_optimal(self) -> bool:
        """True when the MILP solver *proved* this assignment optimal for the stated objective
        (see :attr:`objective`) under the use constraints. False means the greedy heuristic
        produced it (solver timeout/failure) — feasible and verified, but not guaranteed best."""
        return self.solver_status == "Optimal"

    @property
    def objective(self) -> str:
        """The goal this result was optimized for: "co2" (net CO2 saved, default), "members"
        (slots filled) or "mass" (reclaimed steel reused) — recorded on the result so reports and
        the independent verifier judge it against the right yardstick."""
        return (self.weights or {}).get("objective", "co2")


@dataclass
class _Cell:
    """A feasible (supply, slot) pair with its precomputed economics."""

    si: int
    sj: int
    utilization: float
    status: str
    offcut_mm: float
    co2_saved_kg: float
    score: float
    chi_lt: float | None = None
    chi_lt_if_free: float | None = None
    governing_combination: str = "ULS gravity"
    used_len_mm: float = 0.0   # length this piece consumes from the donor (for the cutting-stock cap)
    connection_status: str = "unknown"
    connection_note: str = ""
    mass_used_kg: float = 0.0  # donor steel this piece actually consumes (the "mass" objective)
    # Solver coefficient for the chosen objective (set by _apply_objective). None = fall back to
    # the net-CO2 score, so directly-constructed cells (tests) behave as before.
    objective_coeff: float | None = None


OBJECTIVES = ("co2", "members", "mass")

# End-of-life counterfactual modes (A1ii): what the donor steel consumed by a reuse would otherwise
# have done. "none" books plain avoided-new (the historical behavior); the others subtract the
# foregone credit (see core/carbon.CarbonFactor) from every booked saving.
COUNTERFACTUALS = ("none", "recycling", "rerolling")


def _coeff(cell: _Cell) -> float:
    return cell.score if cell.objective_coeff is None else cell.objective_coeff


def _apply_objective(cells: list[_Cell], objective: str) -> None:
    """Set each cell's solver coefficient for the chosen goal.

    ``"co2"`` (default) keeps the net-CO2 score — carbon-negative pairs are then never selected.
    ``"members"`` (most slots filled) and ``"mass"`` (most reclaimed steel put back to work) are
    lexicographic: the primary value (1 per assignment / kg of donor steel used) dominates and the
    net-CO2 score only breaks ties, scaled so its total influence stays below one primary unit.
    Under these goals a carbon-negative pair CAN be selected when it serves the goal — the booked
    CO2 stays honest (and the headline can come out negative); the report states the objective.
    """
    if objective == "co2":
        for c in cells:
            c.objective_coeff = c.score
        return
    big = sum(abs(c.score) for c in cells) + 1.0
    for c in cells:
        primary = 1.0 if objective == "members" else c.mass_used_kg
        c.objective_coeff = primary + c.score / big


def _apply_reserve(cells: list[_Cell], supply: list[SupplyItem], reserve_w: float,
                   factor: CarbonFactor) -> None:
    """Scarcity / reserve soft penalty (C2, opt-in, EXPERIMENTAL) — applied to cell scores in place.

    A deliberately simple **single-project proxy for option value**: donors from SCARCE capacity
    classes should not be spent on slots that more-abundant donors could also serve, because unseen
    future demand may need exactly them. Per donor section family ``f`` (canonical section name):

        scarcity_f = (# slots for which f is feasible) / (# donors of family f)

    computed once from the feasibility cells, normalized by the maximum across families, and charged
    as ``reserve_w x scarcity_norm_f x mass_used x saved_per_kg`` — but ONLY on cells whose slot at
    least one OTHER family could also serve (a slot only f can serve carries no penalty: spending f
    there is the point of keeping it). Score-only, like the off-cut and over-spec terms: booked CO2
    is never touched.

    Honesty note: within a fully-specified single project the global MILP already allocates scarce
    donors correctly — this proxy only changes outcomes by deliberate conservatism (holding scarce
    stock back from shared slots), which is valuable exactly insofar as demand NOT in the model
    exists. The principled tool for that is portfolio matching (C1); a calibration path for this
    weight is designed (not built) in docs/OPTION_VALUE_ML.md.
    """
    if reserve_w <= 0 or not cells:
        return
    fams = [s.section for s in supply]
    donors_by_fam: dict[str, set[int]] = {}
    slots_by_fam: dict[str, set[int]] = {}
    fams_by_slot: dict[int, set[str]] = {}
    for c in cells:
        f = fams[c.si]
        donors_by_fam.setdefault(f, set()).add(c.si)
        slots_by_fam.setdefault(f, set()).add(c.sj)
        fams_by_slot.setdefault(c.sj, set()).add(f)
    scarcity = {f: len(slots_by_fam[f]) / len(donors_by_fam[f]) for f in donors_by_fam}
    mx = max(scarcity.values())
    if mx <= 0:
        return
    for c in cells:
        if len(fams_by_slot[c.sj]) > 1:
            c.score -= reserve_w * (scarcity[fams[c.si]] / mx) * c.mass_used_kg * factor.saved_per_kg


def _passes_all(sec: SectionProps, grade: str, slot: DemandSlot, knockdown: float = 1.0,
                restrained_baseline: bool = False) -> bool:
    """Feasibility bar across the whole load-combination envelope: the section must pass *every*
    combination. Reused by both the supply check and the avoided-new baseline.

    ``restrained_baseline`` (avoided-new baseline only): size the new section as if the compression
    flange were restrained. A competent new design specifies a slab-restrained floor beam, so the steel
    you would *otherwise buy* is the lighter restrained section. The reclaimed donor's own feasibility
    stays on the slot's actual (conservative, possibly unrestrained) assumption — only the baseline is
    decoupled this way, so a conservative feasibility check never over-credits CO2-saved (see
    docs/CASE_STUDY.md, "restraint")."""
    for _name, demand in slot.combinations:
        if restrained_baseline and not demand.compression_flange_restrained:
            demand = replace(demand, compression_flange_restrained=True)
        res = check_member(sec, grade, demand, knockdown)
        if res.status == "FAIL" or res.utilization > 1.0:
            return False
    return True


def _degenerate(slot: DemandSlot) -> bool:
    """Garbage geometry whose zero/negative length would divide by zero inside the EN buckling/LTB
    checks. Such slots are dropped (and surface as unmatched) rather than crashing the run."""
    if slot.required_length_mm <= 0:
        return True
    for _name, d in slot.combinations:
        needs_buckling_len = d.N_Ed > 0 or (abs(d.My_Ed) > 0 and not d.compression_flange_restrained)
        if needs_buckling_len and d.L <= 0:
            return True
    return False


def _slot_standard(slot: DemandSlot, catalog: dict[str, SectionProps]) -> str | None:
    """Design standard the new-build baseline should stay within ('EU'/'US'), or ``None`` if unknown.

    Prefer the standard of the demand's own mapped section; fall back to the grade prefix (EN grades
    start 'S', ASTM start 'A'). ``None`` means "can't tell" -> search the whole catalog (old behaviour).
    """
    if slot.design_section and slot.design_section in catalog:
        return catalog[slot.design_section].standard
    if slot.grade:
        g = slot.grade.upper()
        if g.startswith("S"):
            return "EU"
        if g.startswith("A"):
            return "US"
    return None


def _shape_family(sec: SectionProps) -> str:
    """Coarse shape family for the new-build baseline: 'open' (I/H), 'hollow' (any tube), 'channel',
    or 'angle'. You would replace a design section with new steel of the *same typology* — never an
    angle for an I-column or a tube for a channel — so the baseline search is confined to this family.
    I and H are one family (IPE/HE/W interchangeable); all hollow (rect + round) group together as
    before, so existing tube models are unaffected by the newer channel/angle catalogues.
    """
    if sec.is_hollow:
        return "hollow"
    s = sec.shape.upper()
    if s == "C":
        return "channel"
    if s == "L":
        return "angle"
    return "open"   # I, H, and any unrecognised open shape


def _slot_shape_family(slot: DemandSlot, catalog: dict[str, SectionProps]) -> str:
    """Shape family the slot's new-build baseline must stay within (see :func:`_shape_family`).

    Taken from the demand's mapped design section; defaults to 'open' (I/H) when it can't be
    determined, so existing open-section models are unchanged by the presence of channel/angle/tube
    rows in the catalogue.
    """
    sec = catalog.get(slot.design_section) if slot.design_section else None
    return _shape_family(sec) if sec is not None else "open"


def baseline_new_mass_kg(
    slot: DemandSlot, catalog: dict[str, SectionProps], new_build_grade: str = "S355"
) -> float | None:
    """Mass (kg) of the *new member you would otherwise buy* for this slot.

    The avoided-production baseline is the **lightest catalog section that passes the slot's exact EN
    check** at the design grade (the demand's own grade if known, else ``new_build_grade``), over the
    slot's required length. Using this instead of the donor's mass is what keeps CO2-saved honest:
    dropping a heavy IPE600 into a slot that only needs an IPE240 must not book IPE600's carbon as
    "saved", and the optimizer must not be rewarded for wasting heavy stock on light demands.

    The search is **restricted to the slot's own design standard** (a US slot's baseline is the lightest
    adequate W-shape, not a coincidentally-lighter IPE, and vice-versa) — you would buy new steel in the
    standard you are designing to. Reclaimed *supply* is deliberately not restricted (reusing a donor
    across standards is fine). Falls back to the whole catalog when the standard can't be determined.
    The search is likewise restricted to the slot's **shape family**: the baseline is a hollow section
    only when the design section is one, and an open (I/H) section otherwise — a W-shape slot must not
    book its avoided carbon against a coincidentally-lighter tube nobody would have bought.
    Returns ``None`` if nothing passes (then the slot is infeasible for reuse too).
    """
    sec = lightest_adequate_section(slot, catalog, new_build_grade)
    return None if sec is None else sec.mass_kgm * slot.required_length_mm / 1000.0


def lightest_adequate_section(
    slot: DemandSlot, catalog: dict[str, SectionProps], new_build_grade: str = "S355"
) -> SectionProps | None:
    """The lightest catalog section that passes the slot's exact EN check — the *new member you would
    otherwise buy* (see :func:`baseline_new_mass_kg`). Same standard/shape-family restrictions; returns
    the section itself so callers can both weigh it and name it (e.g. flag over-spec donor matches)."""
    if _degenerate(slot):
        return None
    grade = slot.grade or new_build_grade
    target_std = _slot_standard(slot, catalog)
    want_family = _slot_shape_family(slot, catalog)
    best: SectionProps | None = None
    for sec in catalog.values():
        if target_std is not None and sec.standard != target_std:
            continue
        if _shape_family(sec) != want_family:
            continue
        if (_passes_all(sec, grade, slot, restrained_baseline=True)
                and (best is None or sec.mass_kgm < best.mass_kgm)):
            best = sec
    return best


def _feasible_cell(
    supply: SupplyItem,
    slot: DemandSlot,
    si: int,
    sj: int,
    catalog: dict[str, SectionProps],
    factor: CarbonFactor,
    w_offcut: float,
    connection_penalty_kg: float,
    baseline_mass_kg: float | None,
    allow_cutting: bool = False,
    connection_policy: ConnectionPolicy | None = None,
    counterfactual_credit: float = 0.0,
    w_overspec: float = 0.0,
    min_util: float = 0.0,
) -> _Cell | None:
    """Return an economics cell if the pair is feasible, else ``None``."""
    # Skip degenerate geometry (garbage rows) before any EN check that could divide by zero; such
    # rows never become feasible pairs and instead surface in unused_supply / unmatched_slots.
    if supply.length_mm <= 0 or _degenerate(slot):
        return None
    if supply.length_mm < slot.required_length_mm + CUT_TOLERANCE_MM:
        return None
    sec = catalog.get(supply.section)
    if sec is None:
        return None
    # Connection screen vs the slot's design section + its worst shear demand (a standard fin plate
    # that can't carry V_Ed -> review). Always *annotated* on the assignment; an "incompatible" pair
    # is *gated* only when the screen is enabled (the policy is set). Cheap, so it runs before the
    # EN checks.
    design_sec = catalog.get(slot.design_section) if slot.design_section else None
    v_ed = max((abs(d.Vz_Ed) for _, d in slot.combinations), default=0.0)
    conn = screen_pair(sec, design_sec, connection_policy, v_ed_n=v_ed)
    if connection_policy is not None and conn.status == "incompatible":
        return None
    # Verify the reclaimed member against every load combination; the governing (worst-utilisation)
    # one is what we report. A single failing combination makes the pair infeasible.
    res = governing_name = None
    for name, demand in slot.combinations:
        r = check_member(sec, supply.grade or "S235", demand, supply.knockdown)
        if r.status == "FAIL" or r.utilization > 1.0:
            return None
        if res is None or r.utilization > res.utilization:
            res, governing_name = r, name
    # Utilization floor (B1, opt-in): refuse pairs whose GOVERNING utilization sits below the
    # floor, so grossly over-spec donors stay in stock for a slot that actually needs them
    # ("don't spend the solid column on a 0.1-util slot"). A hard gate, judged on the worst
    # combination like everything else; default 0.0 admits every passing pair (unchanged).
    if res.utilization < min_util:
        return None

    used_len = slot.required_length_mm
    offcut_mm = supply.length_mm - used_len
    mass_used = sec.mass_kgm * used_len / 1000.0
    offcut_mass = sec.mass_kgm * offcut_mm / 1000.0
    # Net CO2 saved (avoided-burden): avoid producing the right-sized new member (baseline_mass x
    # A1-A3), but still pay the carbon to recover/refabricate the donor we actually use — both the
    # process carbon and the extra connection refabrication a reused member needs. The donor always
    # passes the same check, so a baseline exists; fall back to the donor's own mass only if the
    # catalog lookup somehow yields nothing. This net figure is what gets booked AND reported, so the
    # headline "CO2 saved" matches the basis the optimiser actually used (no silent over-count).
    avoided_new = (baseline_mass_kg if baseline_mass_kg is not None else mass_used) * factor.a1a3
    co2_saved = avoided_new - mass_used * factor.reuse_process - connection_penalty_kg
    # Counterfactual booking (A1ii, opt-in): the donor steel consumed here can no longer take its
    # realistic end-of-life fate (EAF recycling / pilot-scale re-rolling), so the FOREGONE credit of
    # that fate is subtracted from the booked saving — answering the standard LCA critique that
    # avoided-new accounting implicitly assumes the unused donor would have evaporated. The credit
    # is 0.0 by default ("none"), keeping results byte-identical unless asked.
    co2_saved -= mass_used * counterfactual_credit
    if allow_cutting:
        # Cutting-stock: one donor can serve several slots, so the remainder is genuinely reusable
        # (tracked per donor after the solve, not per piece). Don't penalise off-cut here — that bias
        # against long stock is exactly what cutting-stock removes9)#9).
        cell_offcut, score = 0.0, co2_saved
    else:
        # One-piece-per-donor: the remainder is cut off and returns to stock (not emitted), so the
        # off-cut is a *soft preference* that steers away from wasting long stock — not booked CO2.
        cell_offcut = offcut_mm
        score = co2_saved - w_offcut * offcut_mass * factor.saved_per_kg
    # Over-spec soft penalty (B2, opt-in): the CAPACITY analogue of the off-cut preference. Charge
    # the score (never the booked CO2) for the donor's excess mass-per-metre over the slot's
    # avoided-new baseline — "don't waste the solid column on a slot a thin section could serve".
    # Same kg-CO2 currency as the score (via saved_per_kg), like the off-cut term; applies in both
    # length modes (capacity waste is orthogonal to length waste). Default 0 = off.
    if w_overspec > 0 and baseline_mass_kg is not None and slot.required_length_mm > 0:
        baseline_kgm = baseline_mass_kg / (slot.required_length_mm / 1000.0)
        over_kgm = max(0.0, sec.mass_kgm - baseline_kgm)
        score -= w_overspec * over_kgm * (used_len / 1000.0) * factor.saved_per_kg
    # Surface the LTB factor for the report: chi_LT used, and what it would be if unrestrained.
    bending = next((c for c in res.checks if c.name == "bending_y"), None)
    chi_lt = bending.detail.get("chi_LT") if bending else None
    chi_lt_if_free = bending.detail.get("chi_LT_if_unrestrained", chi_lt) if bending else None
    return _Cell(si, sj, res.utilization, res.status, cell_offcut, co2_saved, score,
                 chi_lt, chi_lt_if_free, governing_name or "ULS gravity", used_len_mm=used_len,
                 connection_status=conn.status, connection_note=conn.note,
                 mass_used_kg=mass_used)


def _build_cells(
    supply: list[SupplyItem],
    slots: list[DemandSlot],
    catalog: dict[str, SectionProps],
    factor: CarbonFactor,
    w_offcut: float,
    connection_penalty_kg: float,
    new_build_grade: str,
    allow_cutting: bool,
    connection_policy: ConnectionPolicy | None,
    counterfactual_credit: float = 0.0,
    w_overspec: float = 0.0,
    min_util: float = 0.0,
) -> list[_Cell]:
    """All feasible (supply, slot) cells with their economics — shared by :func:`match` (to solve)
    and :func:`verify_match` (to independently re-derive what the solver saw)."""
    # Avoided-new baseline per slot (lightest adequate section), computed once — see A1.
    baselines = [baseline_new_mass_kg(slot, catalog, new_build_grade) for slot in slots]
    cells: list[_Cell] = []
    for i, sup in enumerate(supply):
        for j, slot in enumerate(slots):
            cell = _feasible_cell(sup, slot, i, j, catalog, factor, w_offcut,
                                  connection_penalty_kg, baselines[j], allow_cutting,
                                  connection_policy,
                                  counterfactual_credit=counterfactual_credit,
                                  w_overspec=w_overspec, min_util=min_util)
            if cell is not None:
                cells.append(cell)
    return cells


def match(
    supply: list[SupplyItem],
    slots: list[DemandSlot],
    catalog: dict[str, SectionProps],
    factors: dict[str, CarbonFactor] | None = None,
    w_offcut: float = 0.3,
    connection_penalty_kg: float = 5.0,
    time_limit_s: float = 30.0,
    new_build_grade: str = "S355",
    allow_cutting: bool = False,
    connection_policy: ConnectionPolicy | None = None,
    objective: str = "co2",
    counterfactual: str = "none",
    w_overspec: float = 0.0,
    min_util: float = 0.0,
    max_distinct_sections: int | None = None,
    reserve_w: float = 0.0,
) -> MatchResult:
    """Optimal supply->slot assignment for the chosen ``objective`` (with greedy fallback).

    ``objective`` selects what "best" means (see :func:`_apply_objective`): ``"co2"`` (default)
    maximizes net CO2 saved; ``"members"`` maximizes the number of slots filled; ``"mass"``
    maximizes the reclaimed steel mass put back to work — the latter two break ties toward CO2.
    Feasibility (every EN check, lengths, use constraints) is identical for all objectives.

    ``allow_cutting`` switches on the **cutting-stock** model: one donor may be cut into several pieces
    to fill several slots, bounded by its length (``sum(required_len + cut tolerance) <= donor length``)
    instead of the default one-piece-per-donor rule. This removes the bias against long stock and books
    each filled slot's avoided-new saving; the leftover of each cut donor is reported as reusable
    remainder (``MatchResult.donor_leftover_mm``).

    ``connection_policy`` enables the **connection feasibility screen** (`core/connections.py`):
    geometrically incompatible (donor, slot) pairs — wrong shape family, donor too deep for the
    detailed zone — are excluded; milder mismatches surface as ``connection_status = "review"``.
    With the default ``None`` nothing is gated, but every assignment is still annotated.

    ``counterfactual`` selects the end-of-life fate the donor steel is assumed to forego by being
    reused (A1ii): ``"none"`` (default — byte-identical to before), ``"recycling"`` (EAF scrap
    credit) or ``"rerolling"`` (pilot-scale direct re-rolling credit). When set, every booked
    ``co2_saved`` (and hence score) is reduced by ``mass_used x credit`` — the saving is then *net
    of what the steel would have saved the wider system anyway*. The mode AND the credit value
    travel on ``MatchResult.weights`` so :func:`verify_match` and :func:`stock_disposition`
    regenerate identical economics.

    ``w_overspec`` (B2, default 0 = off) is the **capacity analogue of the off-cut preference**: a
    soft score penalty of ``w_overspec x (donor_kg/m − baseline_kg/m)+ x used_length_m x
    saved_per_kg`` steering the optimiser toward the lightest adequate donor. Booked CO2 is
    unchanged (like the off-cut term, it is stewardship, not emissions).

    ``min_util`` (B1, default 0 = off) is a **utilization floor**: pairs whose governing
    utilization falls below it are refused outright, keeping grossly over-spec donors in stock.
    A hard gate (unlike ``w_overspec``); the report's what-it-costs comparison is simply a run
    with and without the floor.

    ``max_distinct_sections`` (B3, default None = off) caps the number of **distinct donor section
    families** the result may use (anti-Frankenstein: section variety has real fabrication, QA,
    connection-detailing and procurement costs no carbon term sees). In the MILP this is one
    binary ``y_f`` per usable donor section with ``x_ij <= y_f(i)`` and ``sum(y_f) <= N``; the
    greedy fallback refuses to open an (N+1)-th family. The objective can only get worse under
    the cap — what it costs is exactly the point of comparing runs.

    ``reserve_w`` (C2, default 0 = off, EXPERIMENTAL) softly penalizes consuming donors from
    scarce capacity classes on slots that more-abundant donors could also serve — a single-project
    proxy for option value (see :func:`_apply_reserve`); the principled tool is portfolio
    matching (C1). Score-only; booked CO2 unchanged.
    """
    if objective not in OBJECTIVES:
        raise ValueError(f"unknown objective {objective!r}; expected one of {OBJECTIVES}")
    if counterfactual not in COUNTERFACTUALS:
        raise ValueError(
            f"unknown counterfactual {counterfactual!r}; expected one of {COUNTERFACTUALS}")
    if max_distinct_sections is not None and max_distinct_sections < 1:
        raise ValueError("max_distinct_sections must be a positive integer (or None = no cap)")
    factor = (factors or load_factors())["steel"]
    cf_credit = {"none": 0.0, "recycling": factor.recycle_credit,
                 "rerolling": factor.reroll_credit}[counterfactual]
    weights = {"w_offcut": w_offcut, "connection_penalty_kg": connection_penalty_kg,
               "allow_cutting": allow_cutting,
               "connection_screen": connection_policy is not None,
               "new_build_grade": new_build_grade, "objective": objective,
               "counterfactual": counterfactual, "counterfactual_credit": cf_credit,
               "w_overspec": w_overspec, "min_util": min_util,
               "max_distinct_sections": max_distinct_sections,
               "reserve_w": reserve_w}

    cells = _build_cells(supply, slots, catalog, factor, w_offcut, connection_penalty_kg,
                         new_build_grade, allow_cutting, connection_policy,
                         counterfactual_credit=cf_credit, w_overspec=w_overspec,
                         min_util=min_util)
    _apply_reserve(cells, supply, reserve_w, factor)
    _apply_objective(cells, objective)

    if not cells:
        return MatchResult([], [s.id for s in slots], [s.id for s in supply], "no_feasible_pairs",
                           weights)

    caps = [s.length_mm for s in supply] if allow_cutting else None
    # Donor section family per supply index, for the variety cap (only materialized when capping).
    fams = [s.section for s in supply] if max_distinct_sections is not None else None
    try:
        chosen, status = _solve_milp(cells, len(supply), len(slots), time_limit_s, caps,
                                     families=fams, max_families=max_distinct_sections)
        if not _is_optimal(status):  # timeout / "Not Solved" -> don't trust a partial MILP result
            chosen, status = _solve_greedy(cells, len(supply), len(slots), caps,
                                           families=fams,
                                           max_families=max_distinct_sections), \
                f"greedy_fallback ({status})"
    except Exception:  # pragma: no cover - solver edge cases -> graceful fallback
        chosen, status = _solve_greedy(cells, len(supply), len(slots), caps,
                                       families=fams, max_families=max_distinct_sections), \
            "greedy_fallback (solver error)"

    assignments = [
        Assignment(
            supply_id=supply[c.si].id, slot_id=slots[c.sj].id, section=supply[c.si].section,
            utilization=round(c.utilization, 4), status=c.status,
            offcut_mm=round(c.offcut_mm, 1), co2_saved_kg=round(c.co2_saved_kg, 2),
            score=round(c.score, 2),
            chi_lt=c.chi_lt, chi_lt_if_free=c.chi_lt_if_free,
            governing_combination=c.governing_combination,
            connection_status=c.connection_status, connection_note=c.connection_note,
        )
        for c in chosen
    ]
    used_supply = {a.supply_id for a in assignments}
    filled_slots = {a.slot_id for a in assignments}
    # Cutting-stock: report each cut donor's leftover length (its length minus the pieces taken, each
    # piece consuming required_len + the cut tolerance).
    leftover: dict[str, float] = {}
    if allow_cutting:
        consumed: dict[int, float] = {}
        for c in chosen:
            consumed[c.si] = consumed.get(c.si, 0.0) + c.used_len_mm + CUT_TOLERANCE_MM
        for i, used in consumed.items():
            leftover[supply[i].id] = round(max(supply[i].length_mm - used, 0.0), 1)
    return MatchResult(
        assignments=assignments,
        unmatched_slots=[s.id for s in slots if s.id not in filled_slots],
        unused_supply=[s.id for s in supply if s.id not in used_supply],
        solver_status=status,
        weights=weights,
        donor_leftover_mm=leftover,
    )


def _is_optimal(status: str) -> bool:
    """Only a proven-optimal CBC result is trustworthy; anything else (e.g. a timeout's
    'Not Solved' with a partial/empty assignment) is escalated to the greedy fallback."""
    return status == "Optimal"


def _solve_milp(cells, n_supply, n_slots, time_limit_s,
                caps: list[float] | None = None,
                families: list[str] | None = None,
                max_families: int | None = None) -> tuple[list[_Cell], str]:
    prob = pulp.LpProblem("reuse_matching", pulp.LpMaximize)
    x = {(c.si, c.sj): pulp.LpVariable(f"x_{c.si}_{c.sj}", cat="Binary") for c in cells}
    prob += pulp.lpSum(_coeff(c) * x[(c.si, c.sj)] for c in cells)

    for j in range(n_slots):  # each slot gets at most one supply
        terms = [x[(c.si, c.sj)] for c in cells if c.sj == j]
        if terms:
            prob += pulp.lpSum(terms) <= 1
    for i in range(n_supply):
        cells_i = [c for c in cells if c.si == i]
        if not cells_i:
            continue
        if caps is None:  # default: each supply used at most once
            prob += pulp.lpSum(x[(c.si, c.sj)] for c in cells_i) <= 1
        else:  # cutting-stock: total length cut from this donor must fit its length
            prob += pulp.lpSum(
                (c.used_len_mm + CUT_TOLERANCE_MM) * x[(c.si, c.sj)] for c in cells_i
            ) <= caps[i]

    # Section-variety cap (B3): one binary y_f per donor SECTION FAMILY actually usable, x_ij <= y_f
    # and sum(y_f) <= N — variety has fabrication/QA/detailing/procurement costs no carbon term sees.
    if max_families is not None and families is not None:
        usable = sorted({families[c.si] for c in cells})
        y = {f: pulp.LpVariable(f"y_{k}", cat="Binary") for k, f in enumerate(usable)}
        for c in cells:
            prob += x[(c.si, c.sj)] <= y[families[c.si]]
        prob += pulp.lpSum(y.values()) <= max_families

    prob.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=time_limit_s))
    status = pulp.LpStatus[prob.status]
    chosen = [c for c in cells if x[(c.si, c.sj)].value() and x[(c.si, c.sj)].value() > 0.5]
    return chosen, status


def _solve_greedy(cells, n_supply, n_slots, caps: list[float] | None = None,
                  families: list[str] | None = None,
                  max_families: int | None = None) -> list[_Cell]:
    """Take highest-coefficient feasible pairs first, respecting the use constraints.

    Only objective-positive pairs are taken: the MILP leaves a negative-coefficient x_ij at 0, so
    the greedy fallback must match that — under the net-CO2 objective that means never booking a
    reuse whose net benefit is negative just to fill a slot (under "members"/"mass" every feasible
    pair has a positive coefficient by construction). Cells are sorted by descending coefficient,
    so the first non-positive one ends the scan. Each slot is filled once; a donor is either used
    once (default) or packed up to its length (cutting-stock). With a section-variety cap (B3),
    a cell whose donor family would open an (N+1)-th family is skipped.
    """
    used_j, chosen = set(), []
    remaining = list(caps) if caps is not None else None  # per-donor remaining length (cutting mode)
    used_s: set[int] = set()
    open_families: set[str] = set()
    for c in sorted(cells, key=_coeff, reverse=True):
        if _coeff(c) <= 0:
            break
        if c.sj in used_j:
            continue
        if max_families is not None and families is not None:
            fam = families[c.si]
            if fam not in open_families and len(open_families) >= max_families:
                continue
        if remaining is None:
            if c.si in used_s:
                continue
        else:
            need = c.used_len_mm + CUT_TOLERANCE_MM
            if remaining[c.si] < need:
                continue
            remaining[c.si] -= need
        chosen.append(c)
        used_j.add(c.sj)
        used_s.add(c.si)
        if families is not None:
            open_families.add(families[c.si])
    return chosen


def _cells_from_weights(
    supply: list[SupplyItem],
    slots: list[DemandSlot],
    catalog: dict[str, SectionProps],
    factor: CarbonFactor,
    weights: dict,
) -> list[_Cell]:
    """Re-derive the feasibility/economics cells a solve saw from its recorded ``weights``.

    Every parameter that changes cell economics travels on :attr:`MatchResult.weights`, so any
    post-solve consumer (:func:`verify_match`, :func:`stock_disposition`) regenerates *identical*
    cells — same EN checks, same carbon arithmetic — instead of trusting the stored result.
    """
    policy = ConnectionPolicy() if weights.get("connection_screen") else None
    return _build_cells(supply, slots, catalog, factor,
                        weights.get("w_offcut", 0.3), weights.get("connection_penalty_kg", 5.0),
                        weights.get("new_build_grade", "S355"),
                        bool(weights.get("allow_cutting")), policy,
                        counterfactual_credit=weights.get("counterfactual_credit", 0.0),
                        w_overspec=weights.get("w_overspec", 0.0),
                        min_util=weights.get("min_util", 0.0))


# Minimum straight stock length for the direct re-rolling fate (A2): below this, handling and
# end-cropping losses make re-rolling impractical — the pilot literature works with member-scale
# feedstock. A parameter, not physics; override per call.
REROLL_MIN_LENGTH_MM = 3000.0


def stock_disposition(
    supply: list[SupplyItem],
    slots: list[DemandSlot],
    catalog: dict[str, SectionProps],
    result: MatchResult,
    factors: dict[str, CarbonFactor] | None = None,
    reroll_min_length_mm: float = REROLL_MIN_LENGTH_MM,
) -> list[dict]:
    """Per-unused-donor end-of-fate advisory: store, re-roll, or recycle — with numbers.

    For every donor in ``result.unused_supply`` this compares its three realistic fates:

    * **store** — could it still serve *this* project? The feasibility cells are re-derived for the
      (unused donor, unfilled slot) sub-matrix with the run's own economics (``result.weights``,
      counterfactual basis included), and the best score is reported. Advice is "store" when a
      feasible cell with a strictly positive score exists. Note that in an unconstrained
      proven-optimal run such a pair would have been an improving move (``verify_match`` clause 3)
      and cannot exist; positive store cases arise when stewardship knobs (utilization floor,
      section-variety cap) or a later stock review changed the picture — exactly when the advice
      is worth having.
    * **re-roll** — direct re-rolling without re-melting (pilot-scale; see
      :class:`~steelreuse.core.carbon.CarbonFactor`): credited at ``mass x reroll_credit`` when the
      donor maps to a catalog section and its stock length is at least ``reroll_min_length_mm``
      (straightness/prismatic condition is assumed for mapped catalog stock — surveyed damage is
      the pre-demolition audit's job and quarantined stock never reaches this list).
    * **recycle** — conventional EAF scrap route: ``mass x recycle_credit`` (always available).

    The advice is the argmax: "store" if feasible at positive score, else "re-roll" vs "recycle"
    by credit. Returns one dict per unused donor (id, section, length, mass, the three numbers,
    flags, and ``advice``); pure function — no behavior change to any solve. The C2 reserve term
    is deliberately NOT applied here: it shapes competition between live candidates during the
    solve, while the storage decision for an already-unused donor is judged on base economics
    (and the restricted sub-matrix would yield different scarcity statistics anyway).
    """
    factor = (factors or load_factors())["steel"]
    unused = set(result.unused_supply)
    unfilled = set(result.unmatched_slots)
    sub_supply = [s for s in supply if s.id in unused]
    sub_slots = [s for s in slots if s.id in unfilled]
    cells = _cells_from_weights(sub_supply, sub_slots, catalog, factor, result.weights or {})
    best: dict[str, _Cell] = {}
    for c in cells:
        sid = sub_supply[c.si].id
        if sid not in best or c.score > best[sid].score:
            best[sid] = c
    rows: list[dict] = []
    for s in sub_supply:
        sec = catalog.get(s.section)
        mass = sec.mass_kgm * s.length_mm / 1000.0 if sec else 0.0
        b = best.get(s.id)
        feasible = b is not None
        reroll_ok = sec is not None and s.length_mm >= reroll_min_length_mm
        reroll_credit_kg = mass * factor.reroll_credit if reroll_ok else 0.0
        recycle_credit_kg = mass * factor.recycle_credit
        if feasible and b.score > 0:
            advice = "store"
        elif reroll_credit_kg > recycle_credit_kg:
            advice = "re-roll"
        else:
            advice = "recycle"
        rows.append({
            "supply_id": s.id,
            "section": s.section,
            "length_mm": round(s.length_mm, 1),
            "mass_kg": round(mass, 1),
            "feasible_for_unfilled": feasible,
            "store_slot": sub_slots[b.sj].id if b is not None else None,
            "store_score_kg": round(b.score, 2) if b is not None else None,
            "reroll_eligible": reroll_ok,
            "reroll_credit_kg": round(reroll_credit_kg, 2),
            "recycle_credit_kg": round(recycle_credit_kg, 2),
            "advice": advice,
        })
    return rows


def verify_match(
    supply: list[SupplyItem],
    slots: list[DemandSlot],
    catalog: dict[str, SectionProps],
    result: MatchResult,
    factors: dict[str, CarbonFactor] | None = None,
) -> list[str]:
    """Independent post-solve audit of a :class:`MatchResult`; returns problems (empty = verified).

    Re-derives the same feasibility/economics cells the solver saw (same EN checks, same carbon
    arithmetic — the economic parameters travel on ``result.weights``) and checks:

    1. **Constraints** — every assignment refers to a known donor and slot; no slot is filled twice;
       a donor is used once (default) or within its length cap (cutting-stock).
    2. **Feasibility** — every assignment re-validates as a feasible pair with the same score.
    3. **No improving single move** (a *necessary* condition for optimality): no donor with capacity
       left could fill an unfilled slot at a positive score, and no such donor offers a strictly
       higher score on a filled slot than the donor chosen for it. A proven-optimal MILP result can
       never violate this, and the greedy fallback satisfies it by construction — so any violation
       indicates a real defect (stale result, mutated inputs, solver/economics drift).

    This is a certificate *check*, not a proof of global optimality by itself — global optimality is
    the MILP solver's job (``MatchResult.proven_optimal``); this guards the chain around it.
    """
    factor = (factors or load_factors())["steel"]
    w = result.weights or {}
    allow_cutting = bool(w.get("allow_cutting"))
    cells = _cells_from_weights(supply, slots, catalog, factor, w)
    # The reserve term (C2) is computed over the FULL cell matrix, exactly as the solve did, so the
    # re-derived scores match the stored ones (it is deliberately NOT part of _cells_from_weights:
    # stock_disposition's restricted sub-matrix would yield different scarcity statistics).
    _apply_reserve(cells, supply, w.get("reserve_w", 0.0), factor)
    # Judge "improving move" by the PRIMARY value of the objective the result was solved for (it
    # travels on weights): under "members"/"mass" an unfilled slot with any free feasible donor is
    # already a violation, CO2-negative or not. The CO2 tie-break is deliberately ignored here —
    # its epsilon-scale differences sit below the MILP solver's own tolerances, so flagging them
    # would produce false alarms, and a tie-break swap never changes the primary outcome.
    objective = result.objective

    def _primary(c: _Cell) -> float:
        if objective == "members":
            return 1.0
        if objective == "mass":
            return c.mass_used_kg
        return c.score

    by_pair = {(supply[c.si].id, slots[c.sj].id): c for c in cells}
    sup_by_id = {s.id: s for s in supply}
    slot_ids = {s.id for s in slots}
    issues: list[str] = []

    # 1+2: constraints and per-assignment feasibility (against the regenerated cells).
    chosen_by_slot: dict[str, _Cell] = {}
    uses_per_donor: dict[str, int] = {}
    consumed_mm: dict[str, float] = {}
    for a in result.assignments:
        pair = f"{a.supply_id} -> {a.slot_id}"
        if a.supply_id not in sup_by_id:
            issues.append(f"{pair}: unknown donor id")
            continue
        if a.slot_id not in slot_ids:
            issues.append(f"{pair}: unknown slot id")
            continue
        if a.slot_id in chosen_by_slot:
            issues.append(f"slot {a.slot_id} is filled more than once")
        c = by_pair.get((a.supply_id, a.slot_id))
        if c is None:
            issues.append(f"{pair}: not a feasible pair on independent re-check")
            continue
        if abs(c.score - a.score) > 0.06:   # stored scores are rounded to 2 dp
            issues.append(f"{pair}: score drift (stored {a.score}, recomputed {c.score:.2f})")
        chosen_by_slot[a.slot_id] = c
        uses_per_donor[a.supply_id] = uses_per_donor.get(a.supply_id, 0) + 1
        consumed_mm[a.supply_id] = (consumed_mm.get(a.supply_id, 0.0)
                                    + c.used_len_mm + CUT_TOLERANCE_MM)
    if allow_cutting:
        for sid, used in consumed_mm.items():
            if used > sup_by_id[sid].length_mm + 1e-6:
                issues.append(f"donor {sid}: cut pieces exceed its length "
                              f"({used:.0f} > {sup_by_id[sid].length_mm:.0f} mm)")
    else:
        for sid, n in uses_per_donor.items():
            if n > 1:
                issues.append(f"donor {sid} is used {n} times (one piece per donor)")

    # Section-variety cap (B3): the result may not use more distinct donor sections than allowed.
    max_fams = w.get("max_distinct_sections")
    used_families = {a.section for a in result.assignments}
    if max_fams is not None and len(used_families) > max_fams:
        issues.append(f"section-variety cap violated: {len(used_families)} distinct donor "
                      f"sections used, cap is {max_fams}")

    # 3: no improving single move among donors that still have capacity. Under a saturated
    # section-variety cap, a donor whose family is not already open is NOT free — using it would
    # open an (N+1)-th family, which is not a single-move improvement but a constraint violation.
    cap_saturated = max_fams is not None and len(used_families) >= max_fams
    used_ids = set(uses_per_donor)
    assigned_slots = {a.slot_id for a in result.assignments}
    remaining = {s.id: s.length_mm - consumed_mm.get(s.id, 0.0) for s in supply}
    for c in cells:
        sid, jid = supply[c.si].id, slots[c.sj].id
        donor_free = (remaining[sid] >= c.used_len_mm + CUT_TOLERANCE_MM) if allow_cutting \
            else (sid not in used_ids)
        if not donor_free:
            continue
        if cap_saturated and supply[c.si].section not in used_families:
            continue
        if jid not in assigned_slots:
            if _primary(c) > 1e-9:
                issues.append(f"improving move missed: free donor {sid} could fill unfilled "
                              f"slot {jid} ({objective} value {_primary(c):.2f} > 0)")
        else:
            cur = chosen_by_slot.get(jid)   # None = the slot's own cell failed re-check (reported)
            if cur is not None and _primary(c) > _primary(cur) + 1e-9:
                issues.append(f"improving replacement missed: free donor {sid} offers "
                              f"{objective} value {_primary(c):.2f} on slot {jid}, above the "
                              f"chosen {_primary(cur):.2f}")
    return issues


# Plain-language lever for each binding constraint the diagnosis can name (the narrative renders this;
# the LLM never derives it — docs/DESIGN_PRINCIPLES.md rule 1).
_LEVER = {
    "length": "the donors that are both strong enough and long enough are used up, and the free "
              "remainder is too short for these spans — splicing two short members into one full "
              "length (or sourcing longer stock) is the lever; cutting is already applied",
    "capacity": "the stock lacks sections strong or stiff enough for these slots, so heavier or "
                "different donor sections are what would lift reuse",
    "contention": "the adequate stock is simply outstripped by demand — more reclaimed members of "
                  "the sections that fit would lift reuse",
    "economics": "the only donors that fit are so over-spec for these light slots that reusing them "
                 "would book negative net CO2 — a lighter stock (or a members/mass objective) would "
                 "put them to work",
    "none": "every demand slot was filled",
}


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


# A donor at least this many times heavier per metre than the lightest section that would have passed
# the slot is flagged "over-spec" (an upgrade match) — the Frankenstein-receiver signal. Set at 2x so
# only egregious upgrades (e.g. a ~2x-heavier section) surface, not mild grade-driven margins.
_OVERSPEC_RATIO = 2.0


def diagnose_match(
    supply: list[SupplyItem],
    slots: list[DemandSlot],
    catalog: dict[str, SectionProps],
    result: MatchResult,
    factors: dict[str, CarbonFactor] | None = None,
) -> dict:
    """Diagnose **why** the match came out as it did — counts computed in Python for the narrative.

    Every UNFILLED slot is classified by the reason it stayed empty, so the report can state the
    *binding constraint* and the *lever* that would improve it instead of merely reciting how many
    slots went unfilled. The LLM only renders this conclusion; it never derives it
    (docs/DESIGN_PRINCIPLES.md rule 1).

    Categories (re-deriving the same feasibility cells the solver used, via ``result.weights``):

    * **length** — an adequate donor *section* is in stock but none long enough reaches the slot
      (cutting/splicing is the lever);
    * **capacity** — no donor section passes the slot's EN check at any length (need stronger stock);
    * **contention** — a usable, economic donor existed but went to a better slot (stock exhausted);
    * **economics** — the only feasible donors are so over-spec that reuse books negative net CO2
      under the carbon objective (so the slot is left empty).

    The dominant category is the ``binding_constraint``; ``lever`` is its plain-language fix.
    """
    factor = (factors or load_factors())["steel"]
    w = result.weights or {}
    cells = _cells_from_weights(supply, slots, catalog, factor, w)
    _apply_reserve(cells, supply, w.get("reserve_w", 0.0), factor)
    _apply_objective(cells, w.get("objective", "co2"))
    slots_with_cell = {c.sj for c in cells}              # length + EN feasible (any sign)
    slots_selectable = {c.sj for c in cells if _coeff(c) > 0}   # also economic under the objective
    slot_at = {s.id: i for i, s in enumerate(slots)}

    # Distinct donor "kinds" (section, grade, knockdown) -> longest available, for the EN-only re-check
    # of no-cell slots (splitting "too short" from "too weak" without re-touching every donor).
    kinds: dict[tuple, float] = {}
    for s in supply:
        key = (s.section, s.grade, round(s.knockdown, 4))
        kinds[key] = max(kinds.get(key, 0.0), s.length_mm)

    length = capacity = contention = economics = 0
    for sid in result.unmatched_slots:
        j = slot_at.get(sid)
        if j is None:
            continue
        if j in slots_selectable:
            contention += 1
        elif j in slots_with_cell:
            economics += 1
        else:
            slot = slots[j]
            cap_ok = False
            for (section, grade, _kd), _maxlen in kinds.items():
                sec = catalog.get(section)
                if sec is not None and _passes_all(sec, grade or "S235", slot, _kd):
                    cap_ok = True
                    break
            if cap_ok:
                length += 1
            else:
                capacity += 1

    buckets = {"length": length, "capacity": capacity,
               "contention": contention, "economics": economics}
    binding = max(buckets, key=lambda k: buckets[k]) if result.unmatched_slots else "none"

    # "Contention" can really be a SHORT-STOCK (length) story: the few donors long *and* strong
    # enough are used, and the free remainder is mostly too short for these spans. When that is the
    # case, report it as length (the actionable lever is splicing / longer stock, not "more stock").
    used_ids = {a.supply_id for a in result.assignments}
    free_lens = [s.length_mm for s in supply if s.id not in used_ids]
    unmatched_lens = [slots[slot_at[sid]].required_length_mm
                      for sid in result.unmatched_slots if slot_at.get(sid) is not None]
    if binding == "contention" and free_lens and unmatched_lens:
        typical_demand = _median(unmatched_lens)
        too_short = sum(1 for L in free_lens if typical_demand + CUT_TOLERANCE_MM > L)
        if too_short >= 0.6 * len(free_lens):
            binding = "length"

    # Over-spec ("upgrade") matches: a donor markedly heavier per metre than the lightest section that
    # would have passed the slot. Honest under avoided-new (booked at the lighter section's carbon),
    # but worth flagging — the "Frankenstein receiver" the --w-overspec / --reserve knobs target.
    slot_by_id = {s.id: s for s in slots}
    new_grade = w.get("new_build_grade", "S355")
    n_overspec = 0
    worst: tuple[float, str, str] | None = None      # (ratio, donor section, lighter section)
    for a in result.assignments:
        slot = slot_by_id.get(a.slot_id)
        donor = catalog.get(a.section)
        if slot is None or donor is None:
            continue
        lighter = lightest_adequate_section(slot, catalog, new_grade)
        if lighter is None or lighter.mass_kgm <= 0 or lighter.name == a.section:
            continue
        ratio = donor.mass_kgm / lighter.mass_kgm
        if ratio >= _OVERSPEC_RATIO:
            n_overspec += 1
            if worst is None or ratio > worst[0]:
                worst = (ratio, a.section, lighter.name)

    return {
        "n_unmatched": len(result.unmatched_slots),
        "length_limited": length,
        "capacity_limited": capacity,
        "contention": contention,
        "uneconomic": economics,
        "binding_constraint": binding,
        "lever": _LEVER[binding],
        "donors_eligible": len({c.si for c in cells}),   # donors with at least one feasible slot
        "donors_total": len(supply),
        "n_overspec": n_overspec,
        "overspec_example": ({"donor": worst[1], "lighter": worst[2]} if worst else None),
    }
