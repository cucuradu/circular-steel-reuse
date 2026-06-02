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
    column_floors: float = 1.0             # number of floors a column accumulates
    flange_restrained: bool = True         # a floor slab restrains the beam's compression flange
    tributary_overrides: dict[str, float] = field(default_factory=dict)  # member id -> width (m)

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
        """Per-member :class:`Load`, using a geometry-estimated tributary width when available."""
        if member.role == "column":
            return Load(axial_N=self.column_axial_N())
        trib = self.tributary_overrides.get(member.id) if self.tributary_overrides else None
        width = self.beam_tributary_width_m if trib is None else trib
        return Load(
            udl_Npmm=self.beam_udl_Npmm(trib),
            w_service_Npmm=self.characteristic_area_kpa() * width,
        )


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
