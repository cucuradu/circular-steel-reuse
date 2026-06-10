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

from dataclasses import dataclass, field

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


def _passes_all(sec: SectionProps, grade: str, slot: DemandSlot, knockdown: float = 1.0) -> bool:
    """Feasibility bar across the whole load-combination envelope: the section must pass *every*
    combination. Reused by both the supply check and the avoided-new baseline."""
    for _name, demand in slot.combinations:
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


def _slot_wants_hollow(slot: DemandSlot, catalog: dict[str, SectionProps]) -> bool:
    """Whether the slot's new-build baseline should be a hollow section.

    Only when the design explicitly specifies one: the baseline is "the new member you would otherwise
    buy", and that is an open I/H section unless the design says tube. Keeps results for all existing
    (open-section) models unchanged by the presence of HSS rows in the catalog.
    """
    sec = catalog.get(slot.design_section) if slot.design_section else None
    return bool(sec is not None and sec.is_hollow)


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
    if _degenerate(slot):
        return None
    grade = slot.grade or new_build_grade
    target_std = _slot_standard(slot, catalog)
    want_hollow = _slot_wants_hollow(slot, catalog)
    best: float | None = None
    for sec in catalog.values():
        if target_std is not None and sec.standard != target_std:
            continue
        if sec.is_hollow != want_hollow:
            continue
        if _passes_all(sec, grade, slot):
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
    allow_cutting: bool = False,
    connection_policy: ConnectionPolicy | None = None,
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
    # Geometric connection-compatibility vs the slot's design section. Always *annotated* on the
    # assignment; an "incompatible" pair is *gated* only when the screen is enabled (the policy is
    # set). Cheap, so it runs before the EN checks.
    design_sec = catalog.get(slot.design_section) if slot.design_section else None
    conn = screen_pair(sec, design_sec, connection_policy)
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
    if allow_cutting:
        # Cutting-stock: one donor can serve several slots, so the remainder is genuinely reusable
        # (tracked per donor after the solve, not per piece). Don't penalise off-cut here — that bias
        # against long stock is exactly what cutting-stock removes (FUTURE_IMPROVEMENTS #9).
        cell_offcut, score = 0.0, co2_saved
    else:
        # One-piece-per-donor: the remainder is cut off and returns to stock (not emitted), so the
        # off-cut is a *soft preference* that steers away from wasting long stock — not booked CO2.
        cell_offcut = offcut_mm
        score = co2_saved - w_offcut * offcut_mass * factor.saved_per_kg
    # Surface the LTB factor for the report: chi_LT used, and what it would be if unrestrained.
    bending = next((c for c in res.checks if c.name == "bending_y"), None)
    chi_lt = bending.detail.get("chi_LT") if bending else None
    chi_lt_if_free = bending.detail.get("chi_LT_if_unrestrained", chi_lt) if bending else None
    return _Cell(si, sj, res.utilization, res.status, cell_offcut, co2_saved, score,
                 chi_lt, chi_lt_if_free, governing_name or "ULS gravity", used_len_mm=used_len,
                 connection_status=conn.status, connection_note=conn.note)


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
) -> MatchResult:
    """Optimal supply->slot assignment maximizing net CO2 saved (with greedy fallback).

    ``allow_cutting`` switches on the **cutting-stock** model: one donor may be cut into several pieces
    to fill several slots, bounded by its length (``sum(required_len + cut tolerance) <= donor length``)
    instead of the default one-piece-per-donor rule. This removes the bias against long stock and books
    each filled slot's avoided-new saving; the leftover of each cut donor is reported as reusable
    remainder (``MatchResult.donor_leftover_mm``).

    ``connection_policy`` enables the **connection feasibility screen** (`core/connections.py`):
    geometrically incompatible (donor, slot) pairs — wrong shape family, donor too deep for the
    detailed zone — are excluded; milder mismatches surface as ``connection_status = "review"``.
    With the default ``None`` nothing is gated, but every assignment is still annotated.
    """
    factor = (factors or load_factors())["steel"]
    weights = {"w_offcut": w_offcut, "connection_penalty_kg": connection_penalty_kg,
               "allow_cutting": allow_cutting,
               "connection_screen": connection_policy is not None}

    # Avoided-new baseline per slot (lightest adequate section), computed once — see A1.
    baselines = [baseline_new_mass_kg(slot, catalog, new_build_grade) for slot in slots]

    cells: list[_Cell] = []
    for i, sup in enumerate(supply):
        for j, slot in enumerate(slots):
            cell = _feasible_cell(sup, slot, i, j, catalog, factor, w_offcut,
                                  connection_penalty_kg, baselines[j], allow_cutting,
                                  connection_policy)
            if cell is not None:
                cells.append(cell)

    if not cells:
        return MatchResult([], [s.id for s in slots], [s.id for s in supply], "no_feasible_pairs",
                           weights)

    caps = [s.length_mm for s in supply] if allow_cutting else None
    try:
        chosen, status = _solve_milp(cells, len(supply), len(slots), time_limit_s, caps)
        if not _is_optimal(status):  # timeout / "Not Solved" -> don't trust a partial MILP result
            chosen, status = _solve_greedy(cells, len(supply), len(slots), caps), \
                f"greedy_fallback ({status})"
    except Exception:  # pragma: no cover - solver edge cases -> graceful fallback
        chosen, status = _solve_greedy(cells, len(supply), len(slots), caps), \
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
                caps: list[float] | None = None) -> tuple[list[_Cell], str]:
    prob = pulp.LpProblem("reuse_matching", pulp.LpMaximize)
    x = {(c.si, c.sj): pulp.LpVariable(f"x_{c.si}_{c.sj}", cat="Binary") for c in cells}
    prob += pulp.lpSum(c.score * x[(c.si, c.sj)] for c in cells)

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

    prob.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=time_limit_s))
    status = pulp.LpStatus[prob.status]
    chosen = [c for c in cells if x[(c.si, c.sj)].value() and x[(c.si, c.sj)].value() > 0.5]
    return chosen, status


def _solve_greedy(cells, n_supply, n_slots, caps: list[float] | None = None) -> list[_Cell]:
    """Take highest-score feasible pairs first, respecting the use constraints.

    Only net-positive pairs are taken: the MILP leaves a negative-score x_ij at 0, so the greedy
    fallback must match that — never book a reuse whose net benefit is negative just to fill a slot.
    Cells are sorted by descending score, so the first non-positive one ends the scan. Each slot is
    filled once; a donor is either used once (default) or packed up to its length (cutting-stock).
    """
    used_j, chosen = set(), []
    remaining = list(caps) if caps is not None else None  # per-donor remaining length (cutting mode)
    used_s: set[int] = set()
    for c in sorted(cells, key=lambda c: c.score, reverse=True):
        if c.score <= 0:
            break
        if c.sj in used_j:
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
    return chosen
