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


def _feasible_cell(
    supply: SupplyItem,
    slot: DemandSlot,
    si: int,
    sj: int,
    catalog: dict[str, SectionProps],
    factor: CarbonFactor,
    w_offcut: float,
    connection_penalty_kg: float,
) -> _Cell | None:
    """Return an economics cell if the pair is feasible, else ``None``."""
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
    co2_saved = mass_used * factor.saved_per_kg
    # objective contribution: benefit - wasted-material penalty - connection refabrication penalty
    score = co2_saved - w_offcut * offcut_mass * factor.saved_per_kg - connection_penalty_kg
    return _Cell(si, sj, res.utilization, res.status, offcut_mm, co2_saved, score)


def match(
    supply: list[SupplyItem],
    slots: list[DemandSlot],
    catalog: dict[str, SectionProps],
    factors: dict[str, CarbonFactor] | None = None,
    w_offcut: float = 0.3,
    connection_penalty_kg: float = 5.0,
    time_limit_s: float = 30.0,
) -> MatchResult:
    """Optimal supply->slot assignment maximizing net CO2 saved (with greedy fallback)."""
    factor = (factors or load_factors())["steel"]
    weights = {"w_offcut": w_offcut, "connection_penalty_kg": connection_penalty_kg}

    cells: list[_Cell] = []
    for i, sup in enumerate(supply):
        for j, slot in enumerate(slots):
            cell = _feasible_cell(sup, slot, i, j, catalog, factor, w_offcut, connection_penalty_kg)
            if cell is not None:
                cells.append(cell)

    if not cells:
        return MatchResult([], [s.id for s in slots], [s.id for s in supply], "no_feasible_pairs",
                           weights)

    try:
        chosen, status = _solve_milp(cells, len(supply), len(slots), time_limit_s)
    except Exception:  # pragma: no cover - solver edge cases -> graceful fallback
        chosen, status = _solve_greedy(cells, len(supply), len(slots)), "greedy_fallback"

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
    """Take highest-score feasible pairs first, respecting one-use-each constraints."""
    used_s, used_j, chosen = set(), set(), []
    for c in sorted(cells, key=lambda c: c.score, reverse=True):
        if c.si in used_s or c.sj in used_j:
            continue
        chosen.append(c)
        used_s.add(c.si)
        used_j.add(c.sj)
    return chosen
