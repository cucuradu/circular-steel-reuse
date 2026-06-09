"""Area-based load model — the WS2 "real loads" step.

Replaces the flat per-member default (one UDL for every beam, one axial for every column) with loads
derived from a **floor area pressure** the way an engineer would for a pre-sizing check:

    line load on a beam      w_Ed = (gamma_G * g_k + gamma_Q * q_k) * tributary_width
    axial on a column        N_Ed = (gamma_G * g_k + gamma_Q * q_k) * tributary_area * floors

with EN 1990 ULS (STR) partial factors (gamma_G = 1.35, gamma_Q = 1.5 by default). Every assumption
(area loads, factors, tributary width/area, floor count) is explicit and configurable rather than a
single magic number, and the characteristic (unfactored) pressure is kept for the SLS deflection check.

Tributary widths can either be a single configured default or **estimated per beam from the model
geometry** (:func:`estimate_tributary_widths`) using the spacing to parallel neighbouring beams.
Conservative by default (CLAUDE.md rule 4): an edge beam with a neighbour on one side only is given the
full bay width, and anything the estimator is unsure about falls back to the configured default.

Units: pressures in kN/m^2 (== kPa); lengths/areas in m at this boundary; the resulting
:class:`~steelreuse.core.forces.Load` is in internal N, mm (1 kN/m == 1 N/mm).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .forces import Load


@dataclass
class AreaLoadModel:
    """Floor pressures + tributary geometry -> per-member :class:`Load`.

    Defaults are a typical office floor (EN 1991-1-1 category B): permanent ~3.5 kN/m^2 (slab +
    finishes + services) and imposed 3.0 kN/m^2. Override per project via the CLI.
    """

    dead_kpa: float = 3.5          # g_k, permanent area load (kN/m^2)
    live_kpa: float = 3.0          # q_k, imposed area load (kN/m^2)
    gamma_g: float = 1.35          # EN 1990 Table A1.2(B), unfavourable permanent
    gamma_q: float = 1.5           # EN 1990, leading variable
    beam_tributary_width_m: float = 3.0    # default load width per beam
    column_tributary_area_m2: float = 9.0  # default floor area per column, per level
    column_floors: float = 1.0             # default floors a column accumulates
    column_eccentricity_mm: float = 0.0    # notional moment lever for columns (0 = pure axial)
    notional_phi: float = 0.0              # EN 5.3.2 global sway imperfection (0 = off; EN value 1/200)
    flange_restrained: bool = True         # a floor slab restrains the beam's compression flange
    tributary_overrides: dict[str, float] = field(default_factory=dict)         # beam id -> width (m)
    column_area_overrides: dict[str, float] = field(default_factory=dict)       # col id -> area (m^2)
    column_floor_overrides: dict[str, float] = field(default_factory=dict)      # col id -> floor count

    # Alias so the pipeline can treat this and the legacy flat model uniformly.
    @property
    def beam_flange_restrained(self) -> bool:
        return self.flange_restrained

    def factored_area_kpa(self) -> float:
        """ULS design pressure gamma_G*g_k + gamma_Q*q_k (kN/m^2)."""
        return self.gamma_g * self.dead_kpa + self.gamma_q * self.live_kpa

    def characteristic_area_kpa(self) -> float:
        """Unfactored g_k + q_k (kN/m^2), used for the SLS deflection check."""
        return self.dead_kpa + self.live_kpa

    def beam_udl_Npmm(self, tributary_width_m: float | None = None) -> float:
        w = self.beam_tributary_width_m if tributary_width_m is None else tributary_width_m
        return self.factored_area_kpa() * w        # kN/m^2 * m = kN/m = N/mm

    def column_axial_N(self, tributary_area_m2: float | None = None,
                       floors: float | None = None) -> float:
        a = self.column_tributary_area_m2 if tributary_area_m2 is None else tributary_area_m2
        n = self.column_floors if floors is None else floors
        return self.factored_area_kpa() * a * n * 1.0e3   # kN -> N

    def loads_for(self, member) -> Load:
        """Per-member :class:`Load`, using geometry-estimated tributary/floors when available."""
        if member.role == "column":
            area = self.column_area_overrides.get(member.id, self.column_tributary_area_m2)
            floors = self.column_floor_overrides.get(member.id, self.column_floors)
            n = self.column_axial_N(area, floors)
            return Load(axial_N=n, axial_moment_Nmm=n * self.column_eccentricity_mm)
        trib = self.tributary_overrides.get(member.id) if self.tributary_overrides else None
        width = self.beam_tributary_width_m if trib is None else trib
        return Load(
            udl_Npmm=self.beam_udl_Npmm(trib),
            w_service_Npmm=self.characteristic_area_kpa() * width,
        )

    def combination_loads(self, member) -> list[tuple[str, Load]]:
        """The ULS load-combination envelope for a member: a list of (name, :class:`Load`).

        Replaces the implicit single load case with an explicit envelope the matcher checks the
        member against, reporting the **governing** combination (worst utilisation). A member — and
        the avoided-new baseline — passes only if it passes *every* combination, the way an engineer
        verifies a member against all design situations.

        Combinations:
          * ``ULS gravity (EN 6.10)`` — ``gamma_G g_k + gamma_Q q_k``, always present (the workhorse).
          * ``ULS gravity + EN 5.3.2 imperfection`` — only for columns and only when
            ``notional_phi > 0``: a global (sway) imperfection ``phi`` (EN 1993-1-1 5.3.2, EN value
            ``phi_0 = 1/200``) is applied as an equivalent notional column moment over the column
            length, ``M_y,Ed = N_Ed (e_ecc + phi*L)``. This is a **member-level proxy** for the global
            imperfection (it engages the N+M interaction); it is *not* a frame analysis, so it is kept
            opt-in and documented as such (METHODOLOGY 4). Beams are unaffected at member level.

        Adding further design situations (uplift ``1.0 G + 1.5 Q``, wind, seismic) is a matter of
        appending more entries here.
        """
        base = self.loads_for(member)
        # Names are kept free of clause numbers so they never trip the report's invented-number guard;
        # the EN clause references live in this docstring and docs/METHODOLOGY.md.
        combos = [("ULS gravity", base)]
        if member.role == "column" and self.notional_phi > 0 and (member.length_mm or 0) > 0:
            e_phi = self.notional_phi * member.length_mm
            notional = Load(
                axial_N=base.axial_N,
                axial_moment_Nmm=base.axial_N * (self.column_eccentricity_mm + e_phi),
                w_service_Npmm=base.w_service_Npmm,
            )
            combos.append(("ULS gravity + sway imperfection", notional))
        return combos


# ---------------------------------------------------------------------------
# Geometry-based tributary width
# ---------------------------------------------------------------------------

def _plan(p: list[float]) -> tuple[float, float]:
    return (p[0], p[1])


def estimate_tributary_widths(
    members,
    default_m: float = 3.0,
    min_m: float = 1.0,
    max_m: float = 8.0,
    parallel_tol_deg: float = 15.0,
    elev_tol_mm: float = 400.0,
    overlap_frac: float = 0.25,
) -> dict[str, float]:
    """Estimate each beam's tributary width (m) from spacing to parallel neighbouring beams.

    Two beams are "framing neighbours" when they are roughly parallel in plan, at a similar elevation,
    and overlap along their length. A beam's tributary width is then half the gap to its nearest such
    neighbour on each side; an edge beam (neighbour on one side only) conservatively takes the whole
    gap. Beams with no detectable neighbour are omitted (the caller falls back to ``default_m``).

    Only ``role == "beam"`` members carrying ``start_xyz``/``end_xyz`` participate. Coordinates are in
    mm; the returned widths are in metres, clamped to ``[min_m, max_m]``.
    """
    cos_tol = math.cos(math.radians(parallel_tol_deg))
    beams = []
    for m in members:
        if m.role != "beam" or not m.start_xyz or not m.end_xyz:
            continue
        (x0, y0), (x1, y1) = _plan(m.start_xyz), _plan(m.end_xyz)
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy)
        if length < 1.0:  # zero-length in plan (vertical/garbage) -> skip
            continue
        ux, uy = dx / length, dy / length             # unit direction in plan
        cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0      # centre
        z = (m.start_xyz[2] + m.end_xyz[2]) / 2.0
        beams.append({"id": m.id, "u": (ux, uy), "c": (cx, cy), "z": z, "len": length,
                      "t0": _dot((x0 - cx, y0 - cy), (ux, uy)),
                      "t1": _dot((x1 - cx, y1 - cy), (ux, uy))})

    out: dict[str, float] = {}
    for bi in beams:
        ux, uy = bi["u"]
        nx, ny = -uy, ux                              # perpendicular in plan
        lo_i, hi_i = sorted((bi["t0"], bi["t1"]))
        gap_pos = math.inf   # nearest neighbour on +perp side
        gap_neg = math.inf   # nearest neighbour on -perp side
        for bj in beams:
            if bj is bi:
                continue
            if abs(bi["z"] - bj["z"]) > elev_tol_mm:
                continue
            if abs(_dot(bi["u"], bj["u"])) < cos_tol:  # not parallel
                continue
            # overlap along bi's axis
            dcx, dcy = bj["c"][0] - bi["c"][0], bj["c"][1] - bi["c"][1]
            along = _dot((dcx, dcy), (ux, uy))
            lo_j, hi_j = sorted((along + bj["t0"], along + bj["t1"]))
            overlap = min(hi_i, hi_j) - max(lo_i, lo_j)
            if overlap < overlap_frac * min(bi["len"], bj["len"]):
                continue
            s = _dot((dcx, dcy), (nx, ny))             # signed perpendicular offset
            if s > 1.0:
                gap_pos = min(gap_pos, s)
            elif s < -1.0:
                gap_neg = min(gap_neg, -s)

        half_pos = gap_pos / 2.0 if math.isfinite(gap_pos) else None
        half_neg = gap_neg / 2.0 if math.isfinite(gap_neg) else None
        if half_pos is None and half_neg is None:
            continue                                   # no neighbour -> use default
        if half_pos is None:                           # edge beam: take the whole bay (conservative)
            width_mm = gap_neg
        elif half_neg is None:
            width_mm = gap_pos
        else:
            width_mm = half_pos + half_neg
        out[bi["id"]] = max(min_m, min(max_m, width_mm / 1000.0))
    return out


def _dot(a: tuple[float, float], b: tuple[float, float]) -> float:
    return a[0] * b[0] + a[1] * b[1]


# ---------------------------------------------------------------------------
# Geometry-based column tributary area + floor count
# ---------------------------------------------------------------------------

def _half_bay(neg: float, pos: float) -> float | None:
    """Tributary half-width from the nearest neighbour gap on each side (mm), or ``None`` if isolated.

    Interior point (a neighbour both sides): half of each bay -> ``(neg + pos) / 2``. Edge point
    (one side only): half of the present bay, i.e. the slab edge is assumed at the column with no
    cantilever overhang. This is the exact tributary for a regular no-overhang grid (unlike the beam
    estimator's full-bay edge rule, full-bay here would 4x a corner column — too pessimistic in 2-D).
    """
    n = neg if math.isfinite(neg) else None
    p = pos if math.isfinite(pos) else None
    if n is None and p is None:
        return None
    if n is None:
        return p / 2.0
    if p is None:
        return n / 2.0
    return (n + p) / 2.0


def estimate_column_loads(
    members,
    default_area_m2: float = 9.0,
    min_area_m2: float = 2.0,
    max_area_m2: float = 100.0,
    plan_tol_mm: float = 300.0,
    align_tol_mm: float = 600.0,
) -> tuple[dict[str, float], dict[str, float]]:
    """Per-column tributary floor area (m^2) and floor count, estimated from the model geometry.

    Returns ``(area_overrides, floor_overrides)`` keyed by member id. Columns without usable geometry
    are omitted from a dict (the caller falls back to its configured default for those).

    Method (conservative, plan-grid based):
      * Columns are collapsed to **plan grid points** — a vertical stack at one (x, y) location (within
        ``plan_tol_mm``) shares one tributary area.
      * **Tributary area** = half-bay in x times half-bay in y, from the nearest grid neighbour on each
        of +x/-x/+y/-y (see :func:`_half_bay`); clamped to ``[min_area_m2, max_area_m2]``.
      * **Floor count** for a column = the number of columns in its own stack at or above its base
        elevation: the lowest column carries every floor above it, the top one carries one floor.

    ``default_area_m2`` is accepted for signature symmetry with the caller (unused: missing columns are
    simply omitted so the model's own default applies).
    """
    cols = []
    for m in members:
        if m.role != "column" or not m.start_xyz or not m.end_xyz:
            continue
        x = (m.start_xyz[0] + m.end_xyz[0]) / 2.0
        y = (m.start_xyz[1] + m.end_xyz[1]) / 2.0
        z_base = min(m.start_xyz[2], m.end_xyz[2])
        cols.append({"id": m.id, "x": x, "y": y, "z": z_base})

    # Greedy-cluster columns into vertical stacks by plan position.
    stacks: list[dict] = []
    for c in cols:
        for s in stacks:
            if abs(s["x"] - c["x"]) <= plan_tol_mm and abs(s["y"] - c["y"]) <= plan_tol_mm:
                s["cols"].append(c)
                break
        else:
            stacks.append({"x": c["x"], "y": c["y"], "cols": [c]})

    floor_overrides: dict[str, float] = {}
    for s in stacks:
        ordered = sorted(s["cols"], key=lambda c: c["z"])   # lowest first
        n = len(ordered)
        for rank, c in enumerate(ordered):
            floor_overrides[c["id"]] = float(n - rank)       # lowest carries n, top carries 1

    area_overrides: dict[str, float] = {}
    for i, s in enumerate(stacks):
        left = right = back = front = math.inf
        for j, t in enumerate(stacks):
            if j == i:
                continue
            dx, dy = t["x"] - s["x"], t["y"] - s["y"]
            if abs(dy) <= align_tol_mm:                      # same row -> x-spacing
                if dx > align_tol_mm:
                    right = min(right, dx)
                elif dx < -align_tol_mm:
                    left = min(left, -dx)
            if abs(dx) <= align_tol_mm:                      # same column line -> y-spacing
                if dy > align_tol_mm:
                    front = min(front, dy)
                elif dy < -align_tol_mm:
                    back = min(back, -dy)
        wx, wy = _half_bay(left, right), _half_bay(back, front)
        if wx is None or wy is None:
            continue                                         # too little grid -> use the default
        area_m2 = max(min_area_m2, min(max_area_m2, wx * wy / 1.0e6))
        for c in s["cols"]:
            area_overrides[c["id"]] = area_m2
    return area_overrides, floor_overrides
