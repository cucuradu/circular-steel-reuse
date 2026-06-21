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
Conservative by default (docs/DESIGN_PRINCIPLES.md rule 4): an edge beam with a neighbour on one side
only is given the full bay width, and anything the estimator is unsure about falls back to the
configured default.

Units: pressures in kN/m^2 (== kPa); lengths/areas in m at this boundary; the resulting
:class:`~steelreuse.core.forces.Load` is in internal N, mm (1 kN/m == 1 N/mm).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .forces import Load

# EN 1991-1-1 6.3.1.2 reference area for the αA imposed-load reduction.
A0_M2 = 10.0


@dataclass(frozen=True)
class ZoneSpec:
    """Characteristic loads + reduction parameters for one load zone.

    ``g_k`` is a *typical buildup assumption* (slab + finishes), NOT an EN
    occupancy value — EN 1991-1-1 tabulates only the imposed ``q_k``. ``psi0`` is
    the EN 1990 Table A1.1 combination factor used by the αA/αn reductions.
    ``reducible`` is True only for EN categories A–D (the scope of §6.3.1.2);
    roofs, storage, traffic and placeholders set it False so no reduction applies.
    """

    g_k: float
    q_k: float
    psi0: float = 0.7
    reducible: bool = False


# q_k is a Nationally Determined Parameter: EN 1991-1-1:2002 Table 6.2 (floors A–E),
# Table 6.4 (storage E1), Table 6.8 (traffic F,G) and Table 6.10 (roofs H,I,K) give
# RANGES; the National Annex picks the value within each (the recommended values are
# underlined in the standard). The trailing comment on each row records the EN range;
# the encoded number is a defensible value within it. VERIFIED 2026-06-20 against the
# EN 1991-1-1:2002 base standard (text extracted from the JRC/published English PDF):
# all ranges match; Table 6.10 roof-H qk=0.4, Table 6.4 E1 qk=7.5 and Table 6.8 G qk=5.0
# are exact. The underlined recommended values inside ranges are not machine-extractable,
# so conservative within-range picks are used. Notes:
#   * office-B q_k kept at upper bound 3.0 as the tool's historical default (EN range
#     2.0–3.0); set --occupancy/--live or a National Annex for another value.
#   * For certified use, confirm every q_k against the governing National Annex.
# g_k is NOT an EN occupancy value — a typical slab/finishes buildup, overridable --dead.
OCCUPANCY_PRESETS: dict[str, ZoneSpec] = {
    "residential-A": ZoneSpec(3.5, 2.0, 0.7, True),   # T6.2 cat A floors, range 1.5–2.0 (UK NA 1.5)
    "stairs-A":      ZoneSpec(3.5, 2.0, 0.7, True),   # T6.2 cat A stairs, range 2.0–4.0
    "balcony-A":     ZoneSpec(2.0, 2.5, 0.7, True),   # T6.2 cat A balconies, range 2.5–4.0
    "office-B":      ZoneSpec(3.5, 3.0, 0.7, True),   # T6.2 cat B offices, range 2.0–3.0 (rec 2.5) — default
    "congress-C1":   ZoneSpec(3.5, 3.0, 0.7, True),   # T6.2 tables (cafés/restaurants), range 2.0–3.0
    "congress-C2":   ZoneSpec(3.5, 4.0, 0.7, True),   # T6.2 fixed seats, range 3.0–4.0
    "congress-C3":   ZoneSpec(3.5, 5.0, 0.7, True),   # T6.2 open circulation, range 3.0–5.0
    "congress-C4":   ZoneSpec(3.5, 5.0, 0.7, True),   # T6.2 physical activity, range 4.5–5.0
    "congress-C5":   ZoneSpec(3.5, 5.0, 0.7, True),   # T6.2 crowds, range 5.0–7.5 (raise for stadia)
    "retail-D1":     ZoneSpec(3.5, 4.0, 0.7, True),   # T6.2 general retail, range 4.0–5.0
    "retail-D2":     ZoneSpec(3.5, 5.0, 0.7, True),   # T6.2 department stores, range 4.0–5.0
    "storage-E1":    ZoneSpec(5.0, 7.5, 1.0, False),  # T6.2 cat E1 storage, qk 7.5; outside A–D reduction
    "industrial-E2": ZoneSpec(5.0, 7.5, 1.0, False),  # T6.2 cat E2 industrial — use-specific PLACEHOLDER
    "traffic-F":     ZoneSpec(3.5, 2.0, 0.7, False),  # T6.8 vehicles ≤30 kN, range 1.5–2.5
    "traffic-G":     ZoneSpec(3.5, 5.0, 0.7, False),  # T6.8 vehicles 30–160 kN, qk 5.0
    "roof-H":        ZoneSpec(1.0, 0.4, 0.0, False),  # T6.10 not accessible, range 0.0–1.0 (rec 0.4)
    "roof-I":        ZoneSpec(3.5, 3.0, 0.7, False),  # T6.10 accessible — takes the matching A–D qk
    "roof-K":        ZoneSpec(1.0, 0.0, 0.0, False),  # T6.10 helicopter — class-specific PLACEHOLDER
}


# National Annex q_k overrides (kN/m^2), layered over OCCUPANCY_PRESETS. q_k is a
# Nationally Determined Parameter, so each country's NA sets its own value; only the
# categories that DIFFER from the EN base are listed (the rest inherit EN). Adding or
# correcting a value is a single dict entry.
#   dk, fi, cy, es  — read from the official, free national documents (parsed 2026-06-20).
#   it, uk          — PARTIAL: a few values from secondary/known sources; verify.
#   de, fr, nl, ie  — NA is paywalled; not entered, inherit EN until verified values added.
# For certified use, confirm against the governing National Annex.
NATIONAL_ANNEXES: dict[str, dict[str, float]] = {
    "en": {},  # EN 1991-1-1 base recommended values (default)
    "dk": {    # Denmark — DS/EN 1991-1-1 DK NA:2013 (official, free; Table 6.2/6.8/6.10)
        "residential-A": 1.5,   # A1 floors
        "stairs-A": 3.0,        # A4 stairs
        "office-B": 2.5,        # cat B
        "congress-C1": 2.5,     # C1
        "traffic-F": 2.5,       # garages <=30 kN
        "roof-H": 0.0,          # roof cat H (DK takes qk = 0)
    },
    "fi": {    # Finland — SFS-EN 1991-1-1 NA, Min. Env. Decree 4/16 (official, free)
        "office-B": 2.5,        # cat B
        "congress-C1": 2.5,     # C1
        "congress-C2": 3.0,     # C2
        "congress-C3": 4.0,     # C3
        "congress-C5": 6.0,     # C5
    },
    "cy": {    # Cyprus — CYS EN 1991-1-1 NA (official, free; Table 6.2/6.8)
        "stairs-A": 3.0,        # A stairs
        "balcony-A": 4.0,       # A balconies
        "retail-D1": 5.0,       # D1
        "traffic-F": 2.5,       # cat F
    },
    "es": {    # Spain — CTE DB-SE-AE Tabla 3.1 (official, free)
        "office-B": 2.0,        # B zonas administrativas
        "retail-D1": 5.0,       # D1 locales comerciales
    },
    "it": {    # Italy — NTC 2018 Tab. 3.1.II  (PARTIAL)
        "storage-E1": 6.0,      # cat E magazzini/depositi (ground)
        "roof-H": 0.5,          # coperture cat H1
    },
    "uk": {    # United Kingdom — BS EN 1991-1-1 NA  (PARTIAL; verify)
        "residential-A": 1.5,   # NA A1 self-contained dwellings
        "office-B": 2.5,        # NA offices
    },
    # NA paywalled — inherit EN base until verified q_k are entered:
    "de": {},  # Germany     — DIN EN 1991-1-1/NA
    "fr": {},  # France      — NF EN 1991-1-1/NA
    "nl": {},  # Netherlands — NEN-EN 1991-1-1/NB
    "ie": {},  # Ireland     — I.S. EN 1991-1-1/NA
}


def presets_for_na(na: str) -> dict[str, ZoneSpec]:
    """OCCUPANCY_PRESETS with a National Annex's q_k overrides applied (q_k only)."""
    overrides = NATIONAL_ANNEXES.get(na, {})
    if not overrides:
        return dict(OCCUPANCY_PRESETS)
    return {
        key: (ZoneSpec(spec.g_k, overrides[key], spec.psi0, spec.reducible)
              if key in overrides else spec)
        for key, spec in OCCUPANCY_PRESETS.items()
    }


def alpha_A(area_m2: float, psi0: float) -> float:
    """EN 1991-1-1 eq. 6.1 area reduction factor, in [0.6, 1.0].

    ``αA = (5/7)·ψ0 + A0/A``, capped at 1.0. EN restricts ``αA ≥ 0.6`` for
    categories C and D; applied here for all categories (conservative for A/B,
    exact for C/D — it only bites for tributary areas above ~100 m²).
    ``area_m2 <= 0`` (no geometry) → 1.0.
    """
    if area_m2 <= 0:
        return 1.0
    return max(0.6, min(1.0, (5.0 / 7.0) * psi0 + A0_M2 / area_m2))


def alpha_n(n_floors: float, psi0: float) -> float:
    """EN 1991-1-1 eq. 6.2 storey reduction factor for columns carrying n>2 floors.

    ``αn = (2 + (n − 2)·ψ0) / n``; ``n ≤ 2`` → 1.0 (no reduction).
    """
    n = int(n_floors)
    if n <= 2:
        return 1.0
    return (2.0 + (n - 2) * psi0) / n


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
    construction_stage: bool = False       # add the bare-steel erection-stage case for beams (opt-in)
    construction_live_kpa: float = 0.75    # EN 1991-1-6 q_ca, working personnel (kN/m^2)
    uplift_kpa: float = 0.0                # net upward roof wind pressure (kN/m^2, EN 1991-1-4; 0 = off)
    tributary_overrides: dict[str, float] = field(default_factory=dict)         # beam id -> width (m)
    column_area_overrides: dict[str, float] = field(default_factory=dict)       # col id -> area (m^2)
    column_floor_overrides: dict[str, float] = field(default_factory=dict)      # col id -> floor count

    # --- Zone-based loads (WS "real loads" step 2) -------------------------------
    # dead_kpa/live_kpa above ARE the default "floor" zone (back-compat). The roof
    # zone defaults to a light, not-accessible roof (EN cat H). Custom named zones
    # (balcony, etc.) live in custom_zones. member_zone is auto-filled by
    # assign_zones(); zone_overrides is the user's per-member tag (wins over auto).
    roof_dead_kpa: float = 1.0     # roof zone g_k (typical light roof buildup)
    roof_live_kpa: float = 0.4     # roof zone q_k (EN cat H recommended)
    roof_psi0: float = 0.0         # roof not reducible
    floor_psi0: float = 0.7        # default floor zone ψ0 (EN cat A–D)
    floor_reducible: bool = True   # default floor zone is a reducible category
    load_reduction: bool = True    # master switch for αA/αn (EN 6.3.1.2)
    custom_zones: dict[str, ZoneSpec] = field(default_factory=dict)   # name -> spec
    member_zone: dict[str, str] = field(default_factory=dict)         # id -> zone (auto)
    zone_overrides: dict[str, str] = field(default_factory=dict)      # id -> zone (user)

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

    def _zone_name(self, member_id: str) -> str:
        """Resolve a member's zone: override → auto (elevation) → default 'floor'."""
        if member_id in self.zone_overrides:
            return self.zone_overrides[member_id]
        return self.member_zone.get(member_id, "floor")

    def _zone_spec(self, name: str) -> ZoneSpec:
        if name == "floor":
            return ZoneSpec(self.dead_kpa, self.live_kpa, self.floor_psi0, self.floor_reducible)
        if name == "roof":
            return ZoneSpec(self.roof_dead_kpa, self.roof_live_kpa, self.roof_psi0, False)
        if name in self.custom_zones:
            return self.custom_zones[name]
        if name in OCCUPANCY_PRESETS:          # allow tagging straight to a preset key
            return OCCUPANCY_PRESETS[name]
        return self._zone_spec("floor")        # unknown zone -> safe floor default

    def _spec_by_id(self, member_id: str) -> ZoneSpec:
        return self._zone_spec(self._zone_name(member_id))

    def _beam_udl_for(self, member, width_m: float) -> tuple[float, float]:
        """(ULS udl, SLS service udl) in N/mm for a beam, zone- and αA-aware.

        Permanent term is never reduced; the imposed term is multiplied by αA
        (EN eq. 6.1) over the beam's loaded area = width × span, but only for a
        reducible zone with load_reduction on. SLS stays unreduced.
        """
        spec = self._spec_by_id(member.id)
        span_mm = member.length_mm or (sum(member.spans_mm) if member.spans_mm else 0.0)
        area_m2 = width_m * (span_mm / 1000.0)
        a = alpha_A(area_m2, spec.psi0) if (self.load_reduction and spec.reducible) else 1.0
        uls = self.gamma_g * spec.g_k * width_m + self.gamma_q * a * spec.q_k * width_m
        sls = (spec.g_k + spec.q_k) * width_m
        return uls, sls

    def _column_axial_for(self, member, area_m2: float, floors: float) -> float:
        """Zone- and αn-aware column axial (N).

        A column whose auto zone is 'roof' carries one roof level (light, never
        reduced) plus (floors − 1) floor levels; otherwise all floor levels. αn
        (EN eq. 6.2) reduces the floor-level imposed only, for a reducible zone
        with load_reduction on. Permanent is never reduced. Columns use roof/floor
        zones only in v1 (a custom override on a column falls back to floor).
        """
        floor_spec = self._zone_spec("floor")
        roof_spec = self._zone_spec("roof")
        roof_levels = 1.0 if self.member_zone.get(member.id) == "roof" else 0.0
        floor_levels = max(0.0, floors - roof_levels)
        a_n = (alpha_n(floor_levels, floor_spec.psi0)
               if (self.load_reduction and floor_spec.reducible) else 1.0)
        perm = self.gamma_g * (roof_spec.g_k * roof_levels + floor_spec.g_k * floor_levels)
        imp = self.gamma_q * (roof_spec.q_k * roof_levels + a_n * floor_spec.q_k * floor_levels)
        return (perm + imp) * area_m2 * 1.0e3   # kN -> N

    def beam_udl_Npmm(self, tributary_width_m: float | None = None) -> float:
        w = self.beam_tributary_width_m if tributary_width_m is None else tributary_width_m
        return self.factored_area_kpa() * w        # kN/m^2 * m = kN/m = N/mm

    def construction_udl_Npmm(self, member_id: str | None = None) -> float:
        """Erection-stage ULS line load: full permanent + EN 1991-1-6 construction live (q_ca).

        Keeping the full ``dead_kpa`` is deliberately conservative for the casting situation (the wet
        slab is on the beam but finishes/services are not yet) and saves a second dead-load input; the
        defining difference of the stage is the **missing flange restraint**, applied by the caller.
        """
        trib = self.tributary_overrides.get(member_id) if (
            member_id and self.tributary_overrides) else None
        width = self.beam_tributary_width_m if trib is None else trib
        g_k = self._spec_by_id(member_id).g_k if member_id else self.dead_kpa
        return (self.gamma_g * g_k + self.gamma_q * self.construction_live_kpa) * width

    def uplift_udl_Npmm(self, member_id: str | None = None) -> float:
        """Net UPWARD line load for the wind-uplift reversal case (N/mm); <= 0 means no reversal.

        EN 1990 6.10 with the permanent action FAVOURABLE: ``gamma_Q * W_up - 1.0 * g_k`` (Table
        A1.2(B) gives gamma_G,fav = 1.0; imposed load is favourable too, so it is absent). The same
        ``dead_kpa`` as the gravity case is used — set ``--dead`` to the roof's actual permanent
        load when checking a light roof, since a heavy floor pressure hides a real reversal.
        """
        trib = self.tributary_overrides.get(member_id) if (
            member_id and self.tributary_overrides) else None
        width = self.beam_tributary_width_m if trib is None else trib
        g_k = self._spec_by_id(member_id).g_k if member_id else self.dead_kpa
        return (self.gamma_q * self.uplift_kpa - 1.0 * g_k) * width

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
            n = self._column_axial_for(member, area, floors)
            return Load(axial_N=n, axial_moment_Nmm=n * self.column_eccentricity_mm)
        trib = self.tributary_overrides.get(member.id) if self.tributary_overrides else None
        width = self.beam_tributary_width_m if trib is None else trib
        uls, sls = self._beam_udl_for(member, width)
        return Load(udl_Npmm=uls, w_service_Npmm=sls)

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


# ---------------------------------------------------------------------------
# Load-zone assignment by elevation (roof vs floor)
# ---------------------------------------------------------------------------

# A beam/column top within this of the highest beam belongs to the roof level.
ROOF_LEVEL_TOL_MM = 500.0


def assign_zones(members, roof_tol_mm: float = ROOF_LEVEL_TOL_MM) -> dict[str, str]:
    """member_id -> 'roof' | 'floor', auto-assigned from elevation.

    The top band of beams (within ``roof_tol_mm`` of the highest beam mid-height)
    is the roof; all lower beams are floor. A column is 'roof' when its top reaches
    that band (it carries one roof level), else 'floor'. Members without coordinates
    are omitted, so the caller's default 'floor' zone applies to them.
    """
    beam_mid_z = {
        m.id: (m.start_xyz[2] + m.end_xyz[2]) / 2.0
        for m in members
        if m.role == "beam" and m.start_xyz and m.end_xyz
    }
    out: dict[str, str] = {}
    top = max(beam_mid_z.values()) if beam_mid_z else None
    if top is not None:
        for mid, z in beam_mid_z.items():
            out[mid] = "roof" if z >= top - roof_tol_mm else "floor"
        for m in members:
            if m.role == "column" and m.start_xyz and m.end_xyz:
                top_z = max(m.start_xyz[2], m.end_xyz[2])
                out[m.id] = "roof" if top_z >= top - roof_tol_mm else "floor"
    return out
