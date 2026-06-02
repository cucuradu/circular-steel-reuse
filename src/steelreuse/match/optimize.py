"""The flagship: match reclaimed steel (supply) to new-design member slots (demand).

Pipeline:
  1. Build a **sparse feasibility mask** — a (supply, slot) pair is allowed only if the reclaimed
     member is long enough AND passes the exact EN 1993 check for that slot's forces. Most pairs are
     infeasible and never enter the model (this is what tames the MILP size, flaw #7).
  2. Solve a **MILP** (PuLP/CBC): binary x_ij, each slot <= 1 supply, each supply used <= 1,
     maximizing CO2 saved minus an off-cut penalty minus a per-reuse connection-refabrication penalty
     (flaw #1 — connections are never treated as free).
  3. If the solver is unavailable/stalls, fall back to a greedy assignment.

All numbers are computed here so they can be injected verbatim into the report; the LLM never
recomputes them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pulp

from ..core.carbon import CarbonFactor, load_factors
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
    demand: MemberDemand
    grade: str | None = None         # design grade of the new member (for the avoided-new baseline)
    design_section: str | None = None  # canonical section the new design specified, if known


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


@dataclass
class MatchResult:
    assignments: list[Assignment]
    unmatched_slots: list[str]
    unused_supply: list[str]
    solver_status: str
    weights: dict = field(default_factory=dict)

    @property
    def total_co2_saved_kg(self) -> float:
        return sum(a.co2_saved_kg for a in self.assignments)

    @property
    def total_offcut_mm(self) -> float:
        return sum(a.offcut_mm for a in self.assignments)

    @property
    def n_reused(self) -> int:
        return len(self.assignments)


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


def _passes(sec: SectionProps, grade: str, demand: MemberDemand, knockdown: float = 1.0) -> bool:
    """The single feasibility bar reused by both supply and the avoided-new baseline."""
    res = check_member(sec, grade, demand, knockdown)
    return res.status != "FAIL" and res.utilization <= 1.0


def _degenerate(slot: DemandSlot) -> bool:
    """Garbage geometry whose zero/negative length would divide by zero inside the EN buckling/LTB
    checks. Such slots are dropped (and surface as unmatched) rather than crashing the run."""
    if slot.required_length_mm <= 0:
        return True
    d = slot.demand
    needs_buckling_len = d.N_Ed > 0 or (abs(d.My_Ed) > 0 and not d.compression_flange_restrained)
    return needs_buckling_len and d.L <= 0


def baseline_new_mass_kg(
    slot: DemandSlot, catalog: dict[str, SectionProps], new_build_grade: str = "S355"
) -> float | None:
    """Mass (kg) of the *new member you would otherwise buy* for this slot.

    The avoided-production baseline is the **lightest catalog section that passes the slot's exact EN
    check** at the design grade (the demand's own grade if known, else ``new_build_grade``), over the
    slot's required length. Using this instead of the donor's mass is what keeps CO2-saved honest:
    dropping a heavy IPE600 into a slot that only needs an IPE240 must not book IPE600's carbon as
    "saved", and the optimizer must not be rewarded for wasting heavy stock on light demands.
    Returns ``None`` if nothing in the catalog passes (then the slot is infeasible for reuse too).
    """
    if _degenerate(slot):
        return None
    grade = slot.grade or new_build_grade
    best: float | None = None
    for sec in catalog.values():
        if _passes(sec, grade, slot.demand):
            mass = sec.mass_kgm * slot.required_length_mm / 1000.0
            if best is None or mass < best:
                best = mass
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
    res = check_member(sec, supply.grade or "S235", slot.demand, supply.knockdown)
    if res.status == "FAIL" or res.utilization > 1.0:
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
    # The off-cut term is a *soft preference* only: the remainder is cut off and returns to stock, it
    # is not emitted, so it steers the optimiser away from wasting long stock but is deliberately not
    # booked into co2_saved.
    score = co2_saved - w_offcut * offcut_mass * factor.saved_per_kg
    return _Cell(si, sj, res.utilization, res.status, offcut_mm, co2_saved, score)


def match(
    supply: list[SupplyItem],
    slots: list[DemandSlot],
    catalog: dict[str, SectionProps],
    factors: dict[str, CarbonFactor] | None = None,
    w_offcut: float = 0.3,
    connection_penalty_kg: float = 5.0,
    time_limit_s: float = 30.0,
    new_build_grade: str = "S355",
) -> MatchResult:
    """Optimal supply->slot assignment maximizing net CO2 saved (with greedy fallback)."""
    factor = (factors or load_factors())["steel"]
    weights = {"w_offcut": w_offcut, "connection_penalty_kg": connection_penalty_kg}

    # Avoided-new baseline per slot (lightest adequate section), computed once — see A1.
    baselines = [baseline_new_mass_kg(slot, catalog, new_build_grade) for slot in slots]

    cells: list[_Cell] = []
    for i, sup in enumerate(supply):
        for j, slot in enumerate(slots):
            cell = _feasible_cell(sup, slot, i, j, catalog, factor, w_offcut,
                                  connection_penalty_kg, baselines[j])
            if cell is not None:
                cells.append(cell)

    if not cells:
        return MatchResult([], [s.id for s in slots], [s.id for s in supply], "no_feasible_pairs",
                           weights)

    try:
        chosen, status = _solve_milp(cells, len(supply), len(slots), time_limit_s)
        if not _is_optimal(status):  # timeout / "Not Solved" -> don't trust a partial MILP result
            chosen, status = _solve_greedy(cells, len(supply), len(slots)), f"greedy_fallback ({status})"
    except Exception:  # pragma: no cover - solver edge cases -> graceful fallback
        chosen, status = _solve_greedy(cells, len(supply), len(slots)), "greedy_fallback (solver error)"

    assignments = [
        Assignment(
            supply_id=supply[c.si].id, slot_id=slots[c.sj].id, section=supply[c.si].section,
            utilization=round(c.utilization, 4), status=c.status,
            offcut_mm=round(c.offcut_mm, 1), co2_saved_kg=round(c.co2_saved_kg, 2),
            score=round(c.score, 2),
        )
        for c in chosen
    ]
    used_supply = {a.supply_id for a in assignments}
    filled_slots = {a.slot_id for a in assignments}
    return MatchResult(
        assignments=assignments,
        unmatched_slots=[s.id for s in slots if s.id not in filled_slots],
        unused_supply=[s.id for s in supply if s.id not in used_supply],
        solver_status=status,
        weights=weights,
    )


def _is_optimal(status: str) -> bool:
    """Only a proven-optimal CBC result is trustworthy; anything else (e.g. a timeout's
    'Not Solved' with a partial/empty assignment) is escalated to the greedy fallback."""
    return status == "Optimal"


def _solve_milp(cells, n_supply, n_slots, time_limit_s) -> tuple[list[_Cell], str]:
    prob = pulp.LpProblem("reuse_matching", pulp.LpMaximize)
    x = {(c.si, c.sj): pulp.LpVariable(f"x_{c.si}_{c.sj}", cat="Binary") for c in cells}
    prob += pulp.lpSum(c.score * x[(c.si, c.sj)] for c in cells)

    for j in range(n_slots):  # each slot gets at most one supply
        terms = [x[(c.si, c.sj)] for c in cells if c.sj == j]
        if terms:
            prob += pulp.lpSum(terms) <= 1
    for i in range(n_supply):  # each supply used at most once
        terms = [x[(c.si, c.sj)] for c in cells if c.si == i]
        if terms:
            prob += pulp.lpSum(terms) <= 1

    prob.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=time_limit_s))
    status = pulp.LpStatus[prob.status]
    chosen = [c for c in cells if x[(c.si, c.sj)].value() and x[(c.si, c.sj)].value() > 0.5]
    return chosen, status


def _solve_greedy(cells, n_supply, n_slots) -> list[_Cell]:
    """Take highest-score feasible pairs first, respecting one-use-each constraints.

    Only net-positive pairs are taken: the MILP leaves a negative-score x_ij at 0, so the greedy
    fallback must match that — never book a reuse whose net benefit is negative just to fill a slot.
    Cells are sorted by descending score, so the first non-positive one ends the scan.
    """
    used_s, used_j, chosen = set(), set(), []
    for c in sorted(cells, key=lambda c: c.score, reverse=True):
        if c.score <= 0:
            break
        if c.si in used_s or c.sj in used_j:
            continue
        chosen.append(c)
        used_s.add(c.si)
        used_j.add(c.sj)
    return chosen
