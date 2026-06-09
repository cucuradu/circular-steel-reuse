"""Real frame analysis — derive member design forces from a global solve of the demand structure.

The rest of the pipeline consumes one thing per member: a :class:`~steelreuse.core.ec3_checks.MemberDemand`
(``N_Ed``, ``My_Ed``, ``Vz_Ed`` + buckling/serviceability context). Until now those came from
:mod:`steelreuse.core.forces`, which treats every beam as an isolated simply-supported span
(``M = wL^2/8``) and every column as bare axial. This module computes the *same* ``MemberDemand``
objects from an actual structural analysis instead:

  1. **Topology** — member endpoints (``start_xyz``/``end_xyz``) are snapped into shared nodes so beams
     and columns actually connect (:func:`snap_nodes`, pure Python, unit-testable without a solver).
  2. **Model** — the connected frame is built in PyNiteFEA (optional ``[fea]`` extra, imported lazily).
     For the **simple braced** idealisation (the project default): beam-to-column connections are
     *pinned* (both bending rotations released at each end → beams stay simply-supported, recovering
     ``wL^2/8`` as a check), columns are *continuous*, and column bases are *fixed* for gravity
     stability. The floor pressure is applied as a UDL on the **beams only**; each column then picks up
     its axial from the real load path — so a multi-storey stack accumulates the floors above it
     automatically, with no tributary-area/floor-count estimate needed.
  3. **Combinations** — dead and live are separate PyNite *load cases*; the ULS/SLS *combinations* apply
     the EN 1990 partial factors. When ``notional_phi > 0`` the **EN 1993-1-1 §5.3.2 global sway
     imperfection** is added as *equivalent horizontal forces* ``H_i = phi*N_Ed`` at each column top (a
     real frame-level lateral case, not the member-level notional moment), in each lateral direction.
     When ``wind_kpa > 0`` a **wind** case is added as horizontal storey forces from a net façade pressure
     (wind-leading combination ``gamma_G·G + gamma_Q·W + gamma_Q·psi0·Q``, carrying the imperfection too).
     When ``seismic_cs > 0`` an **EN 1998-1 lateral force** case is added (storey forces from the seismic
     weight, as a ``G + psi2·Q + E`` situation with unit factors). Any lateral case triggers a **2nd-order
     P-Delta** solve.
  4. **Extraction** — per member and per combination, the governing axial (compression-positive, EN
     sign convention), major-axis moment and shear become a ``MemberDemand``.

Members without usable geometry are reported in :attr:`FrameResult.skipped_member_ids` so the caller can
fall back to the per-member analytic load for those (a robust hybrid for messy real models). If the
solve itself fails (e.g. a residual instability), the result carries a warning and no demands, and the
caller again falls back — the analytic path remains the always-available default.

Sign/axis conventions (verified against PyNite 2.4.1): a global ``-Z`` (downward) load on a horizontal
beam produces bending reported as local **My** and shear as local **Fz**; PyNite member axial is
**compression-positive**, matching ``MemberDemand.N_Ed``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..schema import ExtractedMember
from .ec3_checks import MemberDemand
from .sections import SectionProps

# Steel elastic constants (N/mm^2). G = E / (2(1+nu)) with nu = 0.3.
_E_STEEL = 210_000.0
_G_STEEL = 80_769.2
_RHO_STEEL = 7.85e-9  # tonne/mm^3 (unused unless self-weight is added)


@dataclass
class FrameOptions:
    """Knobs for building and solving the frame (see module docstring)."""

    snap_tol_mm: float = 50.0      # endpoints within this distance share a node
    base_tol_mm: float = 500.0     # nodes within this of the lowest level become supports
    pin_beams: bool = True         # simple braced: release beam end moments (simply-supported beams)
    fixed_base: bool = True        # fix column bases (gravity stability without a lateral system yet)
    second_order: bool = False     # force a P-Delta solve (auto-on with notional_phi/wind)
    notional_phi: float = 0.0      # EN 1993-1-1 5.3.2 global sway imperfection (0 = off; EN value 1/200)
    lateral_dirs: tuple[str, ...] = ("FX", "FY")   # directions sway/wind are applied in
    wind_kpa: float = 0.0          # net horizontal wind pressure (kN/m^2; 0 = off) — EN 1991-1-4 input
    psi0_imposed: float = 0.7      # EN 1990 combination factor for imposed load when wind leads
    level_tol_mm: float = 500.0    # node elevations within this are one storey level (for wind/seismic)
    seismic_cs: float = 0.0        # EN 1998-1 base-shear coefficient Sd(T1)*lambda/g (0 = off)
    psi2_imposed: float = 0.3      # EN 1990 quasi-permanent factor (seismic mass + seismic combination)


@dataclass
class _Node:
    name: str
    x: float
    y: float
    z: float


@dataclass
class Topology:
    """Result of snapping member endpoints into shared nodes (no solver involved)."""

    nodes: dict[str, _Node]
    member_nodes: dict[str, tuple[str, str]]   # member id -> (i_node, j_node)
    base_node_ids: list[str]
    skipped_member_ids: list[str]              # members without usable geometry


@dataclass
class FrameResult:
    """Per-member design-force envelope from the frame solve, ready for the matcher/checker."""

    demands_by_member: dict[str, list[tuple[str, MemberDemand]]]
    node_count: int
    member_count: int                 # members actually placed in the frame
    base_node_ids: list[str]
    skipped_member_ids: list[str]     # fell back to the analytic path
    warnings: list[str] = field(default_factory=list)
    ok: bool = True                   # False if the solve failed and everything fell back


# ---------------------------------------------------------------------------
# Continuous-member splitting (pure Python)
# ---------------------------------------------------------------------------

def expand_spans(members) -> list:
    """Split continuous (multi-span) beams into one sub-member per span at the interior supports.

    A beam that spans several bays is extracted as a single member carrying ``spans_mm = [s1, s2, ...]``
    with its endpoints at the far ends. Modelled whole, the frame would dump its entire load at those two
    ends and leave the **interior columns under it unloaded** (they sit on the member but aren't connected
    to it). Splitting it at the cumulative span positions — placed by interpolating along the member axis
    so the interior nodes land on the columns below — gives each span its own simply-supported element and
    routes each bay's reaction into the correct column. Single-span members, columns and braces (and any
    multi-span member lacking geometry) pass through unchanged. Sub-members are id'd ``f"{id}#k"`` to align
    with the per-span slot ids the pipeline already uses.
    """
    out: list = []
    for m in members:
        spans = m.spans_mm or []
        if m.role != "beam" or len(spans) <= 1 or not m.start_xyz or not m.end_xyz:
            out.append(m)
            continue
        s, e = m.start_xyz, m.end_xyz
        total = float(sum(spans))
        if total <= 0:
            out.append(m)
            continue
        cum = 0.0
        for k, span in enumerate(spans):
            f0, f1 = cum / total, (cum + span) / total
            p0 = [s[a] + (e[a] - s[a]) * f0 for a in range(3)]
            p1 = [s[a] + (e[a] - s[a]) * f1 for a in range(3)]
            out.append(ExtractedMember(
                id=f"{m.id}#{k}", role="beam", section=m.section, material_grade=m.material_grade,
                raw_section=m.raw_section, start_xyz=p0, end_xyz=p1,
                spans_mm=[span], length_mm=span))
            cum += span
    return out


# ---------------------------------------------------------------------------
# Topology (pure Python)
# ---------------------------------------------------------------------------

def snap_nodes(members, snap_tol_mm: float = 50.0, base_tol_mm: float = 500.0) -> Topology:
    """Snap member endpoints into shared nodes so the structure is connected.

    Only members carrying both ``start_xyz`` and ``end_xyz`` participate; the rest are returned in
    :attr:`Topology.skipped_member_ids`. A degenerate (zero-length in 3-D) member is also skipped.
    Base nodes are those within ``base_tol_mm`` of the global minimum elevation that are an endpoint of
    a *column* (so a frame on sloping ground still supports at the column feet); if no column reaches
    the lowest level, every node at that level becomes a support instead.
    """
    nodes: list[_Node] = []
    member_nodes: dict[str, tuple[str, str]] = {}
    skipped: list[str] = []
    col_endpoint_ids: set[str] = set()
    tol2 = snap_tol_mm * snap_tol_mm

    def _find_or_add(p) -> str:
        for n in nodes:
            if (n.x - p[0]) ** 2 + (n.y - p[1]) ** 2 + (n.z - p[2]) ** 2 <= tol2:
                return n.name
        name = f"N{len(nodes)}"
        nodes.append(_Node(name, float(p[0]), float(p[1]), float(p[2])))
        return name

    for m in members:
        if not m.start_xyz or not m.end_xyz:
            skipped.append(m.id)
            continue
        s, e = m.start_xyz, m.end_xyz
        if (s[0] - e[0]) ** 2 + (s[1] - e[1]) ** 2 + (s[2] - e[2]) ** 2 < 1.0:
            skipped.append(m.id)          # zero-length -> not a usable element
            continue
        i, j = _find_or_add(s), _find_or_add(e)
        if i == j:                        # both ends snapped to the same node -> degenerate
            skipped.append(m.id)
            continue
        member_nodes[m.id] = (i, j)
        if m.role == "column":
            col_endpoint_ids.add(i)
            col_endpoint_ids.add(j)

    node_map = {n.name: n for n in nodes}
    base_ids: list[str] = []
    if node_map:
        min_z = min(n.z for n in node_map.values())
        at_base = [n.name for n in node_map.values() if n.z - min_z <= base_tol_mm]
        col_base = [nid for nid in at_base if nid in col_endpoint_ids]
        base_ids = col_base or at_base
    return Topology(node_map, member_nodes, base_ids, skipped)


# ---------------------------------------------------------------------------
# Wind storey forces
# ---------------------------------------------------------------------------

def _cluster_levels(z_values, tol: float) -> list[float]:
    """Cluster elevations into storey levels; returns a representative z per level (sorted)."""
    levels: list[list[float]] = []
    for z in sorted(z_values):
        if not levels or z - levels[-1][-1] > tol:
            levels.append([z])
        else:
            levels[-1].append(z)
    return [sum(g) / len(g) for g in levels]


def wind_node_forces(topo: Topology, members_by_id, wind_kpa: float, direction: str,
                     level_tol: float = 500.0) -> dict[str, float]:
    """Horizontal wind force (N) per node for one direction, from a net façade pressure.

    Storey force at a level = ``q · width_perp · h_trib`` (q the net pressure, ``width_perp`` the building
    plan extent perpendicular to the wind, ``h_trib`` half the storey above + half below), lumped equally
    onto that level's column-top nodes (rigid-diaphragm assumption). Returns ``{}`` for a **planar** frame
    (no perpendicular extent — wind needs a 3-D model), so the caller skips wind in that direction.
    """
    nodes = topo.nodes
    if not nodes:
        return {}
    perp = 1 if direction == "FX" else 0          # FX → width measured along Y; FY → along X
    coords = [(n.x, n.y, n.z) for n in nodes.values()]
    width = max(c[perp] for c in coords) - min(c[perp] for c in coords)
    if width < 1.0:                               # planar frame → can't derive a façade
        return {}

    levels = _cluster_levels([c[2] for c in coords], level_tol)
    if len(levels) < 2:
        return {}
    # column-top node ids grouped by level
    tops_by_level: dict[int, list[str]] = {}
    for mid, (i, j) in topo.member_nodes.items():
        if members_by_id[mid].role != "column":
            continue
        top = i if nodes[i].z >= nodes[j].z else j
        k = min(range(len(levels)), key=lambda idx: abs(levels[idx] - nodes[top].z))
        tops_by_level.setdefault(k, []).append(top)

    q = wind_kpa * 1.0e-3                          # kN/m^2 → N/mm^2
    forces: dict[str, float] = {}
    for k in range(1, len(levels)):               # level 0 is the base (reacted at supports)
        below = levels[k] - levels[k - 1]
        above = levels[k + 1] - levels[k] if k + 1 < len(levels) else 0.0
        h_trib = below / 2.0 + above / 2.0
        targets = tops_by_level.get(k) or [n for n in nodes if abs(nodes[n].z - levels[k]) <= level_tol]
        if not targets:
            continue
        f = q * width * h_trib / len(targets)
        for nid in targets:
            forces[nid] = forces.get(nid, 0.0) + f
    return forces


def seismic_node_forces(topo: Topology, members_by_id, loads, cs: float, psi2: float,
                        level_tol: float = 500.0) -> dict[str, float]:
    """Storey seismic forces (N) per node via the EN 1998-1 **lateral force method** (§4.3.3.2).

    Seismic weight of a level ``W_i = Σ (g_k + ψ₂·q_k)·trib_width·beam_length`` over its beams; the base
    shear ``F_b = c_s · ΣW_i`` (``c_s`` = the user's base-shear coefficient ``Sd(T₁)·λ/g``) is distributed
    up the height as ``F_i = F_b · (W_i·z_i)/Σ(W_j·z_j)`` (the inverted-triangular first-mode pattern, with
    ``z`` measured from the base), and ``F_i`` is lumped onto level ``i``'s column tops. Direction-agnostic
    in magnitude (the caller applies it in each lateral direction). Returns ``{}`` if there is too little
    structure (one level, or no seismic weight) to derive a distribution.
    """
    nodes = topo.nodes
    if not nodes or cs <= 0.0:
        return {}
    levels = _cluster_levels([n.z for n in nodes.values()], level_tol)
    if len(levels) < 2:
        return {}

    def _level_of(z: float) -> int:
        return min(range(len(levels)), key=lambda k: abs(levels[k] - z))

    weight = dict.fromkeys(range(len(levels)), 0.0)
    tops_by_level: dict[int, list[str]] = {}
    for mid, (i, j) in topo.member_nodes.items():
        m = members_by_id[mid]
        ni, nj = nodes[i], nodes[j]
        if m.role == "beam":
            trib = (loads.tributary_overrides or {}).get(mid) if getattr(
                loads, "tributary_overrides", None) else None
            width = loads.beam_tributary_width_m if trib is None else trib
            w = (loads.dead_kpa + psi2 * loads.live_kpa) * width      # N/mm seismic line weight
            weight[_level_of((ni.z + nj.z) / 2.0)] += w * math.dist(
                (ni.x, ni.y, ni.z), (nj.x, nj.y, nj.z))
        elif m.role == "column":
            top = i if ni.z >= nj.z else j
            tops_by_level.setdefault(_level_of(nodes[top].z), []).append(top)

    base_z = levels[0]
    z = {k: levels[k] - base_z for k in range(len(levels))}
    denom = sum(weight[k] * z[k] for k in range(len(levels)))
    if denom <= 0.0:
        return {}
    base_shear = cs * sum(weight.values())
    forces: dict[str, float] = {}
    for k in range(1, len(levels)):
        f_k = base_shear * (weight[k] * z[k]) / denom
        targets = tops_by_level.get(k) or [n for n in nodes if abs(nodes[n].z - levels[k]) <= level_tol]
        if not targets or f_k == 0.0:
            continue
        for nid in targets:
            forces[nid] = forces.get(nid, 0.0) + f_k / len(targets)
    return forces


# ---------------------------------------------------------------------------
# Section stiffness helpers
# ---------------------------------------------------------------------------

def torsion_constant(sec: SectionProps) -> float:
    """Open-section St-Venant torsion constant ``J ~= (1/3) sum(b_i t_i^3)`` (mm^4).

    An approximation for rolled I/H shapes (two flanges + web). Torsion is a minor effect in the
    pinned-beam / continuous-column model here, so the geometric estimate is adequate and documented.
    """
    hw = max(sec.h - 2.0 * sec.tf, 0.0)
    return (2.0 * sec.b * sec.tf ** 3 + hw * sec.tw ** 3) / 3.0


def _generic_section_args() -> tuple[float, float, float, float]:
    """Stiff generic (A, Iy, Iz, J) for members without a mapped catalog section.

    Forces in the determinate parts of a simple braced frame (simply-supported beams, gravity column
    axials) don't depend on section stiffness, so a generic section keeps those members in the load
    path without distorting the result; it is only an approximation where the structure is statically
    indeterminate (documented in METHODOLOGY).
    """
    return (1.0e4, 1.0e8, 1.0e8, 1.0e6)


def _section_args(sec: SectionProps | None) -> tuple[float, float, float, float]:
    if sec is None:
        return _generic_section_args()
    return (sec.A, sec.Iy, sec.Iz, torsion_constant(sec))


# ---------------------------------------------------------------------------
# Solve
# ---------------------------------------------------------------------------

def _governing_axial(member, combo: str) -> float:
    """Worst (largest-magnitude) axial in a member for a combo; compression-positive (EN sign)."""
    hi = member.max_axial(combo)
    lo = member.min_axial(combo)
    return hi if abs(hi) >= abs(lo) else lo


def _governing_moment(member, combo: str) -> float:
    """Worst single-axis bending moment magnitude (max of |My|, |Mz|) for a combo.

    Gravity loads a beam about one axis only (the other is ~0, so this equals ``My``); a lateral/sway
    case can add the orthogonal component, so the worst of the two is taken and checked against the
    member's bending resistance (the EN check is uniaxial — biaxial N+My+Mz is a documented limitation).
    """
    return max(abs(member.max_moment("My", combo)), abs(member.min_moment("My", combo)),
               abs(member.max_moment("Mz", combo)), abs(member.min_moment("Mz", combo)))


def _governing_shear(member, combo: str) -> float:
    """Worst transverse shear magnitude (max of |Fy|, |Fz|) for a combo."""
    return max(abs(member.max_shear("Fy", combo)), abs(member.min_shear("Fy", combo)),
               abs(member.max_shear("Fz", combo)), abs(member.min_shear("Fz", combo)))


def analyze_frame(
    demand_members,
    loads,
    catalog: dict[str, SectionProps] | None = None,
    combos: list[tuple[str, dict[str, float]]] | None = None,
    options: FrameOptions | None = None,
) -> FrameResult:
    """Build and solve the demand frame; return a per-member design-force envelope.

    ``loads`` is an :class:`~steelreuse.core.loads.AreaLoadModel` (it supplies the floor pressures, the
    EN 1990 factors and the per-beam tributary widths). ``combos`` is a list of
    ``(name, {case: factor})`` over the load cases ``"DL"`` (permanent) and ``"LL"`` (imposed); the
    default is the single ULS gravity combination ``gamma_G*DL + gamma_Q*LL`` plus an ``"SLS"`` service
    combination used only for the deflection check. Returns analytic-path fallbacks (empty demands) for
    members the frame can't use; see :class:`FrameResult`.
    """
    options = options or FrameOptions()
    catalog = catalog or {}
    # Split continuous multi-span beams at their interior supports first, so the load path is correct.
    expanded = expand_spans(demand_members)
    members_by_id = {m.id: m for m in expanded}
    topo = snap_nodes(expanded, options.snap_tol_mm, options.base_tol_mm)

    if not topo.member_nodes or not topo.base_node_ids:
        return FrameResult({}, len(topo.nodes), 0, topo.base_node_ids,
                           [m.id for m in demand_members],
                           ["no connectable geometry — using per-member analytic loads"], ok=False)

    try:
        from Pynite import FEModel3D
    except ImportError:
        return FrameResult({}, len(topo.nodes), 0, topo.base_node_ids,
                           [m.id for m in demand_members],
                           ["PyNiteFEA not installed ([fea] extra) — using analytic loads"], ok=False)

    fm = FEModel3D()
    fm.add_material("steel", _E_STEEL, _G_STEEL, 0.3, _RHO_STEEL)
    for n in topo.nodes.values():
        fm.add_node(n.name, n.x, n.y, n.z)
    for nid in topo.base_node_ids:
        if options.fixed_base:
            fm.def_support(nid, True, True, True, True, True, True)
        else:                                   # pinned base (needs a lateral system — Stage 2)
            fm.def_support(nid, True, True, True, True, False, False)

    # Place members; remember each beam's tributary width so we can load it.
    beam_ids: list[str] = []
    for mid, (i, j) in topo.member_nodes.items():
        m = members_by_id[mid]
        sec = catalog.get(m.section) if m.section else None
        a, iy, iz, jt = _section_args(sec)
        sname = f"S_{mid}"
        fm.add_section(sname, a, iy, iz, jt)
        fm.add_member(mid, i, j, "steel", sname)
        if options.pin_beams and m.role in ("beam", "brace"):
            # Pin the connection: release both bending rotations at each end (torsion stays connected
            # for stability). The beam is then simply-supported between columns.
            fm.def_releases(mid, Ryi=True, Rzi=True, Ryj=True, Rzj=True)
        if m.role == "beam":
            beam_ids.append(mid)

    # Floor load on beams: characteristic dead/live UDL (N/mm) via the area model + tributary width.
    # Columns are deliberately unloaded — their axial comes from the solved load path.
    for mid in beam_ids:
        trib = None
        if getattr(loads, "tributary_overrides", None):
            trib = loads.tributary_overrides.get(mid)
        width = loads.beam_tributary_width_m if trib is None else trib
        w_dead = loads.dead_kpa * width      # kN/m == N/mm
        w_live = loads.live_kpa * width
        fm.add_member_dist_load(mid, "FZ", -w_dead, -w_dead, case="DL")
        fm.add_member_dist_load(mid, "FZ", -w_live, -w_live, case="LL")

    def _fallback(msg: str) -> FrameResult:
        return FrameResult({}, len(topo.nodes), len(topo.member_nodes), topo.base_node_ids,
                           [m.id for m in demand_members], [msg], ok=False)

    gamma_g, gamma_q = loads.gamma_g, loads.gamma_q
    auto_combos = combos is None
    uls_combos: list[tuple[str, dict[str, float]]] = list(
        combos if combos is not None else [("ULS gravity", {"DL": gamma_g, "LL": gamma_q})])
    for name, factors in uls_combos + [("SLS", {"DL": 1.0, "LL": 1.0})]:
        fm.add_load_combo(name, dict(factors))

    second_order = (options.second_order or options.notional_phi > 0.0
                    or options.wind_kpa > 0.0 or options.seismic_cs > 0.0)
    warnings: list[str] = []
    nhf_cases: dict[str, str] = {}     # lateral dir -> sway load-case name (for the wind combos)

    # EN 1993-1-1 5.3.2 global sway imperfection as **equivalent horizontal forces** (the frame-level
    # treatment, replacing the member-level notional moment): H_i = phi * N_Ed,col applied at each column
    # top, in each lateral direction. This needs the gravity column axials, so we pre-solve gravity, add
    # the sway load cases + combinations, and re-solve (below) — with P-Delta, so 2nd-order sway is
    # captured. The lateral load is carried by whatever lateral system the model has (vertical bracing,
    # or the fixed column bases).
    if auto_combos and options.notional_phi > 0.0:
        grav_name = uls_combos[0][0]
        columns = [(mid, topo.member_nodes[mid]) for mid in topo.member_nodes
                   if members_by_id[mid].role == "column"]
        if columns:
            try:
                fm.analyze_linear(check_stability=True)
            except Exception as exc:  # noqa: BLE001 - pre-solve failed -> fall back to analytic
                return _fallback(f"frame pre-solve failed ({exc}) — using analytic loads")
            for d in options.lateral_dirs:
                case = f"NHF_{d}"
                applied = False
                for mid, (i, j) in columns:
                    n = _governing_axial(fm.members[mid], grav_name)
                    if n > 0.0:                       # compression columns shed a notional sway force
                        top = i if topo.nodes[i].z >= topo.nodes[j].z else j
                        fm.add_node_load(top, d, options.notional_phi * n, case=case)
                        applied = True
                if applied:
                    nhf_cases[d] = case
                    sway = (f"ULS gravity + sway {d[-1]}",
                            {"DL": gamma_g, "LL": gamma_q, case: 1.0})
                    fm.add_load_combo(sway[0], dict(sway[1]))
                    uls_combos.append(sway)
            if nhf_cases:
                warnings.append(f"applied EN 5.3.2 sway imperfection (EHF) in {len(nhf_cases)} direction(s)")

    # Wind: net façade pressure -> horizontal storey forces. The combination is wind-leading
    # (EN 1990 6.10: gamma_G·G + gamma_Q·W + gamma_Q·psi0·Q), and carries the sway imperfection too
    # where it exists (the imperfection is present in every lateral situation).
    if auto_combos and options.wind_kpa > 0.0:
        n_wind = 0
        for d in options.lateral_dirs:
            wf = wind_node_forces(topo, members_by_id, options.wind_kpa, d, options.level_tol_mm)
            if not wf:
                continue
            case = f"WIND_{d}"
            for nid, force in wf.items():
                fm.add_node_load(nid, d, force, case=case)
            factors = {"DL": gamma_g, "LL": gamma_q * options.psi0_imposed, case: gamma_q}
            if d in nhf_cases:
                factors[nhf_cases[d]] = 1.0
            name = f"ULS gravity + wind {d[-1]}"
            fm.add_load_combo(name, dict(factors))
            uls_combos.append((name, factors))
            n_wind += 1
        if n_wind:
            warnings.append(f"applied wind {options.wind_kpa:g} kN/m^2 in {n_wind} direction(s)")

    # Seismic: EN 1998-1 lateral force method. Storey forces (from the seismic weight + base-shear
    # coefficient) become a seismic design situation `G + psi2*Q + E` with unit factors (EN 1990 6.4.3.4).
    if auto_combos and options.seismic_cs > 0.0:
        sf = seismic_node_forces(topo, members_by_id, loads, options.seismic_cs,
                                 options.psi2_imposed, options.level_tol_mm)
        n_seis = 0
        if sf:
            for d in options.lateral_dirs:
                case = f"SEIS_{d}"
                for nid, force in sf.items():
                    fm.add_node_load(nid, d, force, case=case)
                factors = {"DL": 1.0, "LL": options.psi2_imposed, case: 1.0}
                name = f"seismic {d[-1]}"
                fm.add_load_combo(name, dict(factors))
                uls_combos.append((name, factors))
                n_seis += 1
        if n_seis:
            warnings.append(f"applied EN 1998 seismic (Cs={options.seismic_cs:g}) "
                            f"in {n_seis} direction(s)")

    try:
        if second_order:
            fm.analyze_PDelta(check_stability=True)
        else:
            fm.analyze_linear(check_stability=True)
    except Exception as exc:  # noqa: BLE001 - any solver failure -> fall back to analytic
        return _fallback(f"frame solve failed ({exc}) — using analytic loads")
    if second_order:
        warnings.append("2nd-order (P-Delta) solve")

    flange_restrained = bool(getattr(loads, "beam_flange_restrained", True))
    demands_by_member: dict[str, list[tuple[str, MemberDemand]]] = {}
    for mid, (i, j) in topo.member_nodes.items():
        m = members_by_id[mid]
        mem = fm.members[mid]
        ni, nj = topo.nodes[i], topo.nodes[j]
        length = math.dist((ni.x, ni.y, ni.z), (nj.x, nj.y, nj.z))
        restrained = flange_restrained if m.role == "beam" else False
        # Equivalent service UDL for the EC3 deflection check (only beams carry one).
        w_serv = None
        if m.role == "beam":
            trib = (loads.tributary_overrides or {}).get(mid) if getattr(
                loads, "tributary_overrides", None) else None
            width = loads.beam_tributary_width_m if trib is None else trib
            w_serv = loads.characteristic_area_kpa() * width or None
        per_combo: list[tuple[str, MemberDemand]] = []
        for name, _ in uls_combos:                 # SLS drives only the deflection check, not slots
            per_combo.append((name, MemberDemand(
                N_Ed=_governing_axial(mem, name),
                My_Ed=_governing_moment(mem, name),
                Vz_Ed=_governing_shear(mem, name),
                L=length, compression_flange_restrained=restrained, w_service=w_serv,
            )))
        demands_by_member[mid] = per_combo

    return FrameResult(
        demands_by_member=demands_by_member,
        node_count=len(topo.nodes),
        member_count=len(topo.member_nodes),
        base_node_ids=topo.base_node_ids,
        skipped_member_ids=topo.skipped_member_ids,
        warnings=warnings,
        ok=True,
    )
