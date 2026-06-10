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
    prune_free_ends: bool = True   # drop members hanging off the structure (free, unsupported end)
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
class FrameSlot:
    """One reusable demand slot derived from the solve: one physical member, or one inter-column span of
    a continuous beam. Carries the worst-case action-effect envelope over the segments it was built from
    (a split column's storey lifts, or a girder's segments between secondary-beam crossings)."""

    slot_id: str
    member_id: str
    role: str
    required_length_mm: float
    demands: list[tuple[str, MemberDemand]]


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
    # EN 1993-1-1 5.2.1(4)B sway-stiffness factor per lateral direction (computed when the sway
    # imperfection runs): alpha_cr >= 10 -> non-sway, the k = 1.0 system-length route is justified.
    alpha_cr: dict[str, float] = field(default_factory=dict)
    # Reuse slots grouped per ORIGINAL member id (one physical member or one inter-column span). The
    # pipeline consumes this; ``demands_by_member`` above keeps the raw per-solved-segment physics view.
    slots_by_member: dict[str, list[FrameSlot]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Continuous-member splitting (pure Python)
# ---------------------------------------------------------------------------

def _expand_spans_tracked(members) -> tuple[list, set[tuple[str, str]]]:
    """Split continuous (multi-span) beams into one sub-member per span, tracking the interior joins.

    A beam that spans several bays is extracted as a single member carrying ``spans_mm = [s1, s2, ...]``
    with its endpoints at the far ends. Modelled whole, the frame would dump its entire load at those two
    ends and leave the **interior supports under it unloaded** (they sit on the member but aren't connected
    to it). Splitting it at the cumulative span positions — interpolated along the member axis so the
    interior nodes land on whatever is below — gives each span its own element and creates the shared nodes
    the snapper needs. Single-span members, columns and braces (and any multi-span member lacking geometry)
    pass through unchanged. Sub-members are id'd ``f"{id}#k"``.

    Returns ``(members, interior_ends)`` where ``interior_ends`` is the set of ``(submember_id, "i"|"j")``
    that are **interior joins** — the same physical beam continues past that end. The solver uses this to
    keep a girder moment-continuous at a secondary-beam crossing (no real support) while still pinning it
    at a genuine support; see :func:`analyze_frame`.
    """
    out: list = []
    interior_ends: set[tuple[str, str]] = set()
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
        n = len(spans)
        for k, span in enumerate(spans):
            f0, f1 = cum / total, (cum + span) / total
            p0 = [s[a] + (e[a] - s[a]) * f0 for a in range(3)]
            p1 = [s[a] + (e[a] - s[a]) * f1 for a in range(3)]
            sid = f"{m.id}#{k}"
            out.append(ExtractedMember(
                id=sid, role="beam", section=m.section, material_grade=m.material_grade,
                raw_section=m.raw_section, start_xyz=p0, end_xyz=p1,
                spans_mm=[span], length_mm=span, ky=m.ky, kz=m.kz))
            if k > 0:
                interior_ends.add((sid, "i"))   # start continues the previous span
            if k < n - 1:
                interior_ends.add((sid, "j"))   # end continues into the next span
            cum += span
    return out, interior_ends


def expand_spans(members) -> list:
    """Split continuous multi-span beams at their interior supports (see :func:`_expand_spans_tracked`)."""
    return _expand_spans_tracked(members)[0]


def split_columns_at_framing(members, snap_tol_mm: float = 50.0) -> list:
    """Split a multi-storey column wherever another member frames into its interior.

    A column extracted from Revit as a single full-height element carries nodes only at its two ends, so
    beams that frame in at an intermediate floor land on the bare shaft and never connect — the floor
    floats off as a disconnected component (the dominant cause of a fractured demand model). For each
    (near-)vertical column this inserts a node at every interior elevation where another member's endpoint
    meets its axis (same plan position within ``snap_tol_mm``), splitting it into storey segments. The
    segments keep role ``"column"`` and are never released, so the split is structurally identical to the
    continuous member (the solver enforces full continuity at the inserted node) while restoring the load
    path: the floor's reactions now flow into the column and it accumulates the storeys above. Sub-ids are
    ``f"{id}@{k}"`` (separator distinct from the beam span ``#k``) so the per-segment forces fold back into
    the single physical column after the solve. Non-columns, sloped columns, and columns with no interior
    framing pass through unchanged.
    """
    tol = snap_tol_mm
    pts: list[tuple[float, float, float]] = []
    for m in members:
        if m.start_xyz:
            pts.append((m.start_xyz[0], m.start_xyz[1], m.start_xyz[2]))
        if m.end_xyz:
            pts.append((m.end_xyz[0], m.end_xyz[1], m.end_xyz[2]))
    out: list = []
    for m in members:
        if m.role != "column" or not m.start_xyz or not m.end_xyz:
            out.append(m)
            continue
        s, e = m.start_xyz, m.end_xyz
        if math.hypot(e[0] - s[0], e[1] - s[1]) > tol:   # not (near-)vertical -> leave alone
            out.append(m)
            continue
        lo, hi = min(s[2], e[2]), max(s[2], e[2])
        cx, cy = s[0], s[1]
        cuts_at: set[float] = set()
        for px, py, pz in pts:
            if math.hypot(px - cx, py - cy) <= tol and lo + tol < pz < hi - tol:
                cuts_at.add(round(pz, 3))
        if not cuts_at:
            out.append(m)
            continue
        clustered: list[float] = []
        for z in sorted(cuts_at):
            if not clustered or z - clustered[-1] > tol:
                clustered.append(z)
        cuts = [lo, *clustered, hi]
        for k in range(len(cuts) - 1):
            za, zb = cuts[k], cuts[k + 1]
            out.append(ExtractedMember(
                id=f"{m.id}@{k}", role="column", section=m.section,
                material_grade=m.material_grade, raw_section=m.raw_section,
                start_xyz=[cx, cy, za], end_xyz=[cx, cy, zb],
                spans_mm=[zb - za], length_mm=zb - za, ky=m.ky, kz=m.kz))
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
    # Support each connected component at ITS OWN lowest level: a real model can contain several
    # disconnected structures sitting at different elevations, so a single global-minimum base would
    # leave the higher pieces floating (a global instability). Within each component the column feet
    # at the lowest level are the supports (falling back to every lowest node if none are columns).
    base_ids: list[str] = []
    adj: dict[str, set[str]] = {}
    for i, j in member_nodes.values():
        adj.setdefault(i, set()).add(j)
        adj.setdefault(j, set()).add(i)
    seen: set[str] = set()
    for start in adj:
        if start in seen:
            continue
        comp: list[str] = []
        stack = [start]
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x)
            comp.append(x)
            stack.extend(adj[x] - seen)
        cmin = min(node_map[c].z for c in comp)
        at_base = [c for c in comp if node_map[c].z - cmin <= base_tol_mm]
        col_base = [c for c in at_base if c in col_endpoint_ids]
        base_ids.extend(col_base or at_base)
    return Topology(node_map, member_nodes, base_ids, skipped)


def _stabilize_topology(topo: Topology) -> list[str]:
    """Remove members that hang off the structure and would make the stiffness matrix singular.

    A member is pruned if it has a **free end** (a degree-1 node that is not a support) **and is not
    anchored to a support** at either end — i.e. a column hanging from above, or a member floating with
    both ends loose. A genuine fixed-base cantilever (free top, supported foot) is *kept* because it is
    stable. Pruning is iterative (removing one member can free another). Pruned members are appended to
    ``skipped_member_ids`` so the caller analyses them per-member, and any orphaned nodes are dropped.
    Returns the list of pruned member ids.
    """
    base = set(topo.base_node_ids)
    pruned: list[str] = []
    changed = True
    while changed:
        changed = False
        deg: dict[str, int] = {}
        for i, j in topo.member_nodes.values():
            deg[i] = deg.get(i, 0) + 1
            deg[j] = deg.get(j, 0) + 1
        for mid, (i, j) in list(topo.member_nodes.items()):
            anchored = i in base or j in base
            free_end = (deg[i] == 1 and i not in base) or (deg[j] == 1 and j not in base)
            if free_end and not anchored:
                del topo.member_nodes[mid]
                pruned.append(mid)
                changed = True
    if pruned:
        topo.skipped_member_ids = list(topo.skipped_member_ids) + pruned
        referenced = {n for ends in topo.member_nodes.values() for n in ends}
        topo.nodes = {k: v for k, v in topo.nodes.items() if k in referenced}
        topo.base_node_ids = [b for b in topo.base_node_ids if b in referenced]
    return pruned


# ---------------------------------------------------------------------------
# Wind storey forces
# ---------------------------------------------------------------------------

def sway_alpha_cr(storeys: list[tuple[float, float, float, float]]) -> float | None:
    """EN 1993-1-1 eq. (5.2): alpha_cr = (H_Ed/V_Ed)*(h/delta_H), minimum over storeys.

    ``storeys`` carries one tuple per storey: (h_mm, H_N, V_N, drift_mm) — storey height, the total
    horizontal force applied at/above the storey top, the vertical load carried through the storey,
    and the inter-storey drift under those horizontal forces. Storeys with no vertical load or no
    measurable drift (rigid, e.g. fully braced in that direction) are skipped; returns ``None`` when
    nothing is assessable.
    """
    values = [(H / V) * (h / d) for h, H, V, d in storeys if V > 0.0 and d > 1e-9 and H > 0.0]
    return min(values) if values else None


def _compute_alpha_cr(fm, topo: Topology, members_by_id, nhf_forces: dict[str, float],
                      grav_name: str, direction: str, acr_combo: str, level_tol: float) -> float | None:
    """Sway-stiffness alpha_cr for one lateral direction from the solved frame.

    Per storey (consecutive node levels): H = sum of the EHF applied at/above the storey top;
    V = sum of the gravity column axials passing through the storey; drift = difference of the
    mean level displacement (DX/DY) under the lateral-only combination ``acr_combo``.
    """
    nodes = list(topo.nodes.values())
    levels = _cluster_levels([n.z for n in nodes], level_tol)
    if len(levels) < 2:
        return None
    disp_attr = "DX" if direction == "FX" else "DY"

    def level_nodes(z):
        return [n for n in nodes if abs(n.z - z) <= level_tol]

    def mean_disp(z):
        ds = [getattr(fm.nodes[n.name], disp_attr).get(acr_combo, 0.0) for n in level_nodes(z)]
        return sum(ds) / len(ds) if ds else 0.0

    storeys: list[tuple[float, float, float, float]] = []
    for z_lo, z_hi in zip(levels, levels[1:], strict=False):
        h = z_hi - z_lo
        if h <= level_tol:
            continue
        H = sum(f for nid, f in nhf_forces.items() if topo.nodes[nid].z >= z_hi - level_tol)
        mid_z = (z_lo + z_hi) / 2.0
        V = 0.0
        for mid, (i, j) in topo.member_nodes.items():
            if members_by_id[mid].role != "column":
                continue
            lo = min(topo.nodes[i].z, topo.nodes[j].z)
            hi = max(topo.nodes[i].z, topo.nodes[j].z)
            if lo <= mid_z <= hi:
                V += max(_governing_axial(fm.members[mid], grav_name), 0.0)
        drift = abs(mean_disp(z_hi) - mean_disp(z_lo))
        storeys.append((h, H, V, drift))
    return sway_alpha_cr(storeys)


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


def _envelope_moment(member, axis: str, combo: str) -> float:
    """Peak bending-moment magnitude about one local axis ('My'|'Mz') for a combo.

    Local axes follow the section axes (each member is created with its own Iy/Iz), so local ``My``
    is the section's major axis and ``Mz`` its minor axis. Both are carried separately into
    :class:`MemberDemand`: gravity loads a beam about ``My`` only, while a lateral/sway case can bend
    a column about both — the checker's biaxial 6.3.3 interaction now sees the true pair instead of
    a single worst-axis magnitude checked against major-axis resistance."""
    return max(abs(member.max_moment(axis, combo)), abs(member.min_moment(axis, combo)))


def _governing_shear(member, combo: str) -> float:
    """Worst transverse shear magnitude (max of |Fy|, |Fz|) for a combo."""
    return max(abs(member.max_shear("Fy", combo)), abs(member.min_shear("Fy", combo)),
               abs(member.max_shear("Fz", combo)), abs(member.min_shear("Fz", combo)))


def _build_slots_by_member(
    demands_by_member: dict[str, list[tuple[str, MemberDemand]]],
    members_by_id: dict,
    topo: Topology,
    loads,
    column_nodes: set[str],
    flange_restrained: bool,
) -> dict[str, list[FrameSlot]]:
    """Group the per-segment solve results back into reusable slots — one per physical member or per
    inter-column span — keyed by ORIGINAL member id (the pipeline looks them up that way).

    A split column (``id@k``) folds into one slot over its full height (a column is one reused element).
    A continuous beam (``id#k``) is cut into a new slot only at an interior join that lands on a **column**
    (a real support, so each inter-column span is a separately reusable simply-supported member);
    secondary-beam crossings stay within one slot, so a girder maps to a single reused member. Each slot
    carries the worst-case (max-magnitude) action-effect envelope over its segments per load combination,
    with the slot's full length as the buckling/deflection length.
    """
    segs: dict[str, list[str]] = {}
    for sid in demands_by_member:
        orig = sid.split("@", 1)[0].split("#", 1)[0]
        segs.setdefault(orig, []).append(sid)

    def _seg_index(sid: str) -> int:
        for sep in ("@", "#"):
            if sep in sid:
                return int(sid.rsplit(sep, 1)[1])
        return 0

    slots_by_member: dict[str, list[FrameSlot]] = {}
    for orig, seg_ids in segs.items():
        seg_ids = sorted(seg_ids, key=_seg_index)
        role = members_by_id[seg_ids[0]].role
        # A new slot starts at an interior join that is a column node (real support); columns never split.
        groups: list[list[str]] = [[seg_ids[0]]]
        for prev, cur in zip(seg_ids, seg_ids[1:], strict=False):
            shared = set(topo.member_nodes[prev]) & set(topo.member_nodes[cur])
            boundary = role == "beam" and bool(shared) and next(iter(shared)) in column_nodes
            (groups.append([cur]) if boundary else groups[-1].append(cur))

        width = loads.beam_tributary_width_m
        if getattr(loads, "tributary_overrides", None):
            width = loads.tributary_overrides.get(orig, width)
        w_serv = (loads.characteristic_area_kpa() * width or None) if role == "beam" else None
        restrained = flange_restrained if role == "beam" else False

        slots: list[FrameSlot] = []
        for gi, group in enumerate(groups):
            length = 0.0
            for sid in group:
                ni, nj = (topo.nodes[x] for x in topo.member_nodes[sid])
                length += math.dist((ni.x, ni.y, ni.z), (nj.x, nj.y, nj.z))
            combo_names = [name for name, _ in demands_by_member[group[0]]]
            envelope: list[tuple[str, MemberDemand]] = []
            for ci, name in enumerate(combo_names):
                ds = [demands_by_member[sid][ci][1] for sid in group]
                envelope.append((name, MemberDemand(
                    N_Ed=max((d.N_Ed for d in ds), key=abs),
                    My_Ed=max(d.My_Ed for d in ds),
                    Mz_Ed=max(d.Mz_Ed for d in ds),
                    Vz_Ed=max(d.Vz_Ed for d in ds),
                    L=length, ky=ds[0].ky, kz=ds[0].kz,
                    compression_flange_restrained=restrained, w_service=w_serv,
                )))
            slots.append(FrameSlot(f"{orig}#{gi}", orig, role, length, envelope))
        slots_by_member[orig] = slots
    return slots_by_member


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
    # Connect the real load path of a messy BIM model: (1) split full-height columns at the floors that
    # frame into them, then (2) split continuous beams at their span points. Both create the shared nodes
    # the snapper needs; the release logic below keeps columns and beam-interior crossings continuous.
    columns_split = split_columns_at_framing(demand_members, options.snap_tol_mm)
    expanded, interior_ends = _expand_spans_tracked(columns_split)
    members_by_id = {m.id: m for m in expanded}
    topo = snap_nodes(expanded, options.snap_tol_mm, options.base_tol_mm)
    pruned_free = _stabilize_topology(topo) if options.prune_free_ends else []
    # Nodes that a column is incident to: a beam end here has a real vertical support (pin it), and an
    # interior beam join here is a genuine inter-column span boundary (a new reuse slot).
    column_nodes = {n for mid, ends in topo.member_nodes.items()
                    if members_by_id[mid].role == "column" for n in ends}

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
            # Pin the connection by releasing the MAJOR-axis bending moment (local My, the one that carries
            # gravity → recovers the simply-supported wL^2/8) — but only at a real support: the member's
            # true ends, and interior joins that sit on a column. At an interior beam-to-beam crossing with
            # no column (a secondary framing into a girder) the member stays moment-CONTINUOUS, so the
            # girder supports the secondary instead of forming a vertical mechanism. Minor-axis bending and
            # torsion always stay connected (gives beam-to-beam nodes vertical-axis rotational stiffness,
            # otherwise singular on real BIM models).
            rel_i = (mid, "i") not in interior_ends or i in column_nodes
            rel_j = (mid, "j") not in interior_ends or j in column_nodes
            if rel_i or rel_j:
                fm.def_releases(mid, Ryi=rel_i, Ryj=rel_j)
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
    if pruned_free:
        warnings.append(f"pruned {len(pruned_free)} member(s) with a free/unsupported end "
                        "(fell back to analytic)")
    nhf_cases: dict[str, str] = {}     # lateral dir -> sway load-case name (for the wind combos)
    nhf_forces: dict[str, dict[str, float]] = {}   # lateral dir -> {node: EHF} (for alpha_cr)

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
                applied: dict[str, float] = {}        # node -> EHF (for the alpha_cr storey shears)
                for mid, (i, j) in columns:
                    n = _governing_axial(fm.members[mid], grav_name)
                    if n > 0.0:                       # compression columns shed a notional sway force
                        top = i if topo.nodes[i].z >= topo.nodes[j].z else j
                        force = options.notional_phi * n
                        fm.add_node_load(top, d, force, case=case)
                        applied[top] = applied.get(top, 0.0) + force
                if applied:
                    nhf_cases[d] = case
                    nhf_forces[d] = applied
                    sway = (f"ULS gravity + sway {d[-1]}",
                            {"DL": gamma_g, "LL": gamma_q, case: 1.0})
                    fm.add_load_combo(sway[0], dict(sway[1]))
                    uls_combos.append(sway)
                    # Lateral-only combination, used solely to read the sway drifts for alpha_cr
                    # (EN 5.2.1(4)B). Not a design situation -> NOT appended to uls_combos.
                    fm.add_load_combo(f"_acr_{d}", {case: 1.0})
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

    # Sway-stiffness classification, EN 1993-1-1 5.2.1(4)B: alpha_cr = (H/V)*(h/delta) per storey,
    # from the EHF drifts. This *verifies* the k = 1.0 system-length route the checker uses
    # (5.2.2: 2nd-order analysis + global imperfections): alpha_cr >= 10 -> non-sway, k = 1.0 sound;
    # below 10 the P-Delta solve (already engaged whenever phi > 0) is doing real work; below 3 the
    # frame is so sway-sensitive that a dedicated global stability verification is warranted.
    alpha_cr: dict[str, float] = {}
    if nhf_forces:
        grav_name = uls_combos[0][0]
        for d, forces in nhf_forces.items():
            a = _compute_alpha_cr(fm, topo, members_by_id, forces, grav_name, d,
                                  f"_acr_{d}", options.level_tol_mm)
            if a is not None:
                alpha_cr[d] = a
        if alpha_cr:
            worst = min(alpha_cr.values())
            if worst >= 10.0:
                warnings.append(f"sway check: alpha_cr = {worst:.1f} >= 10 (non-sway; "
                                "k = 1.0 system lengths justified)")
            elif worst >= 3.0:
                warnings.append(f"sway-sensitive frame: alpha_cr = {worst:.1f} < 10 — "
                                "2nd-order effects significant (P-Delta solve engaged)")
            else:
                warnings.append(f"STRONGLY sway-sensitive frame: alpha_cr = {worst:.1f} < 3 — "
                                "verify global stability by a dedicated analysis")

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
        ky = getattr(m, "ky", None) or 1.0         # per-member buckling-length overrides (default 1.0)
        kz = getattr(m, "kz", None) or 1.0
        per_combo: list[tuple[str, MemberDemand]] = []
        for name, _ in uls_combos:                 # SLS drives only the deflection check, not slots
            per_combo.append((name, MemberDemand(
                N_Ed=_governing_axial(mem, name),
                My_Ed=_envelope_moment(mem, "My", name),
                Mz_Ed=_envelope_moment(mem, "Mz", name),
                Vz_Ed=_governing_shear(mem, name),
                L=length, ky=ky, kz=kz,
                compression_flange_restrained=restrained, w_service=w_serv,
            )))
        demands_by_member[mid] = per_combo

    # Guard against an ill-conditioned "success": an irregular/near-mechanism model can solve yet yield
    # non-physical forces. Rather than feed garbage to the checker, fall back to the analytic path.
    _CAP_N, _CAP_NMM = 1e9, 1e12   # 1e6 kN / 1e6 kNm — far above any real member force
    for combos in demands_by_member.values():
        for _, d in combos:
            if (abs(d.N_Ed) > _CAP_N or abs(d.Vz_Ed) > _CAP_N
                    or abs(d.My_Ed) > _CAP_NMM or abs(d.Mz_Ed) > _CAP_NMM):
                return _fallback(
                    "frame solve ill-conditioned (non-physical forces) — using analytic loads")

    slots_by_member = _build_slots_by_member(
        demands_by_member, members_by_id, topo, loads, column_nodes, flange_restrained)

    return FrameResult(
        demands_by_member=demands_by_member,
        node_count=len(topo.nodes),
        member_count=len(topo.member_nodes),
        base_node_ids=topo.base_node_ids,
        skipped_member_ids=topo.skipped_member_ids,
        warnings=warnings,
        ok=True,
        slots_by_member=slots_by_member,
        alpha_cr=alpha_cr,
    )
