"""EN 1993-1-1 member checks for hot-rolled I/H steel sections — the deterministic ground truth.

Everything is in internal units: forces in N, moments in N*mm, lengths in mm, stresses in N/mm^2.
Sign convention: ``N_Ed`` is **compression-positive**; a negative value is treated as tension.

Scope & honesty (see CLAUDE.md):
  * Implemented: classification, tension, compression+flexural buckling, bending, shear,
    a *simplified linear* N+M interaction, and an optional deflection (SLS) check.
  * Lateral-torsional buckling is **flagged, not fully computed** here (needs I_t, I_w): an
    unrestrained beam in bending is marked ``status = "REVIEW"`` rather than silently passed.
    Full chi_LT is deferred to the "LTB refinement" phase.
  * The reclaimed-steel **knockdown** multiplies f_y (condition/uncertainty proxy), never silently 1.0
    when the caller asks for a reduction.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .sections import FY_BY_GRADE, SectionProps

E_STEEL = 210_000.0  # N/mm^2
G_STEEL = 80_769.0   # N/mm^2 (E / (2(1+nu)), nu=0.3)
GAMMA_M0 = 1.0
GAMMA_M1 = 1.0


def epsilon(fy: float) -> float:
    """Material factor epsilon = sqrt(235 / f_y), EN 1993-1-1 Table 5.2."""
    return math.sqrt(235.0 / fy)


# ---------------------------------------------------------------------------
# Cross-section classification (Table 5.2)
# ---------------------------------------------------------------------------

def flange_class(sec: SectionProps, eps: float) -> int:
    """Class of the compression flange outstand (Table 5.2 sheet 2)."""
    c = (sec.b - sec.tw - 2 * sec.r) / 2.0
    ratio = c / sec.tf
    if ratio <= 9 * eps:
        return 1
    if ratio <= 10 * eps:
        return 2
    if ratio <= 14 * eps:
        return 3
    return 4


def web_class(sec: SectionProps, eps: float, mode: str) -> int:
    """Class of the web (Table 5.2 sheet 1). ``mode`` is 'bending' or 'compression'."""
    c = sec.h - 2 * sec.tf - 2 * sec.r
    ratio = c / sec.tw
    if mode == "compression":
        limits = (33 * eps, 38 * eps, 42 * eps)
    else:  # pure bending
        limits = (72 * eps, 83 * eps, 124 * eps)
    for cls, lim in zip((1, 2, 3), limits, strict=True):
        if ratio <= lim:
            return cls
    return 4


def classify(sec: SectionProps, fy: float, N_Ed: float = 0.0, My_Ed: float = 0.0) -> int:
    """Overall section class = worst of flange and web.

    Conservative web mode selection: pure compression if there is no bending, pure bending if there
    is no axial; otherwise the stricter 'compression' limits are used for combined N+M.
    """
    eps = epsilon(fy)
    fcl = flange_class(sec, eps)
    if abs(My_Ed) < 1e-9 and N_Ed > 0:
        mode = "compression"
    elif N_Ed > 0:
        mode = "compression"   # combined N+M -> conservative
    else:
        mode = "bending"
    return max(fcl, web_class(sec, eps, mode))


# ---------------------------------------------------------------------------
# Resistances
# ---------------------------------------------------------------------------

def N_t_Rd(sec: SectionProps, fy: float) -> float:
    """Tension resistance, eq. (6.6)."""
    return sec.A * fy / GAMMA_M0


def N_c_Rd(sec: SectionProps, fy: float) -> float:
    """Cross-section compression resistance (class 1-3), eq. (6.10)."""
    return sec.A * fy / GAMMA_M0


def M_c_Rd(sec: SectionProps, fy: float, section_class: int) -> float:
    """Major-axis bending resistance, eq. (6.13)/(6.14). Class 4 falls back to W_el with a warning."""
    W = sec.Wpl_y if section_class <= 2 else sec.Wel_y
    return W * fy / GAMMA_M0


def V_c_Rd(sec: SectionProps, fy: float) -> float:
    """Plastic shear resistance (web), eq. (6.18)."""
    return sec.Av_z * (fy / math.sqrt(3.0)) / GAMMA_M0


# ---------------------------------------------------------------------------
# Lateral-torsional buckling (Phase 7 refinement)
# I_t and I_w are approximated from geometry (no catalog columns needed). Both approximations are
# on the safe side (they under-predict M_cr -> lower chi_LT -> conservative), and the assumption is
# flagged in the member warnings.
# ---------------------------------------------------------------------------

def torsion_constant(sec: SectionProps) -> float:
    """St-Venant torsion constant I_t for an I/H section, thin-wall approx (mm^4)."""
    return (2 * sec.b * sec.tf**3 + (sec.h - 2 * sec.tf) * sec.tw**3) / 3.0


def warping_constant(sec: SectionProps) -> float:
    """Warping constant I_w for a doubly-symmetric I/H section: I_z * h_s^2 / 4 (mm^6)."""
    hs = sec.h - sec.tf  # distance between flange centroids
    return sec.Iz * hs**2 / 4.0


def M_cr(sec: SectionProps, L: float, C1: float = 1.0) -> float:
    """Elastic critical moment for LTB, doubly-symmetric section, uniform moment (mm units)."""
    It, Iw = torsion_constant(sec), warping_constant(sec)
    base = math.pi**2 * E_STEEL * sec.Iz / L**2
    root = math.sqrt(Iw / sec.Iz + L**2 * G_STEEL * It / (math.pi**2 * E_STEEL * sec.Iz))
    return C1 * base * root


def chi_LT(sec: SectionProps, fy: float, L: float, section_class: int, C1: float = 1.0) -> float:
    """LTB reduction factor, EN 1993-1-1 cl. 6.3.2.3 (rolled sections), returns chi_LT in (0, 1]."""
    Wy = sec.Wpl_y if section_class <= 2 else sec.Wel_y
    Mcr = M_cr(sec, L, C1)
    lam = math.sqrt(Wy * fy / Mcr)
    lam0, beta = 0.4, 0.75
    if lam <= lam0:
        return 1.0
    alpha = 0.34 if sec.h / sec.b <= 2.0 else 0.49  # curve b / c, Table 6.5
    phi = 0.5 * (1 + alpha * (lam - lam0) + beta * lam**2)
    chi = 1.0 / (phi + math.sqrt(phi**2 - beta * lam**2))
    return min(chi, 1.0, 1.0 / lam**2)


def _buckling_alpha(sec: SectionProps, axis: str) -> float:
    """Imperfection factor alpha from the buckling curve, Tables 6.1/6.2 (rolled I/H, t_f <= 40 mm)."""
    # curve -> alpha
    a = {"a": 0.21, "b": 0.34, "c": 0.49, "d": 0.76}
    if sec.h / sec.b > 1.2:
        curve = "a" if axis == "y" else "b"
    else:
        curve = "b" if axis == "y" else "c"
    return a[curve]


def _chi(lambda_bar: float, alpha: float) -> float:
    """Reduction factor for flexural buckling, eq. (6.49)."""
    if lambda_bar <= 0.2:
        return 1.0
    phi = 0.5 * (1 + alpha * (lambda_bar - 0.2) + lambda_bar**2)
    chi = 1.0 / (phi + math.sqrt(phi**2 - lambda_bar**2))
    return min(chi, 1.0)


def N_b_Rd(sec: SectionProps, fy: float, L: float, k: float, axis: str) -> tuple[float, float]:
    """Flexural-buckling resistance about ``axis`` ('y'|'z'), eq. (6.47). Returns (N_b_Rd, chi)."""
    I = sec.Iy if axis == "y" else sec.Iz  # noqa: E741
    Lcr = k * L
    Ncr = math.pi**2 * E_STEEL * I / Lcr**2
    lambda_bar = math.sqrt(sec.A * fy / Ncr)
    chi = _chi(lambda_bar, _buckling_alpha(sec, axis))
    return chi * sec.A * fy / GAMMA_M1, chi


# ---------------------------------------------------------------------------
# Demand + result containers
# ---------------------------------------------------------------------------

@dataclass
class MemberDemand:
    """Design action effects + buckling/serviceability context for one member or span."""

    N_Ed: float = 0.0          # N, compression-positive (negative = tension)
    My_Ed: float = 0.0         # N*mm
    Vz_Ed: float = 0.0         # N
    L: float = 0.0             # mm, system length for buckling/deflection
    ky: float = 1.0            # buckling length factor about y
    kz: float = 1.0            # about z
    compression_flange_restrained: bool = False
    C1: float = 1.0            # LTB moment-distribution factor (1.0 = uniform moment, conservative)
    w_service: float | None = None   # service UDL (N/mm) for the optional deflection check
    defl_limit_ratio: float = 250.0  # delta <= L / this


@dataclass
class CheckResult:
    name: str
    utilization: float
    detail: dict = field(default_factory=dict)


@dataclass
class MemberCheck:
    section: str
    grade: str
    fy: float
    section_class: int
    checks: list[CheckResult]
    governing: str
    utilization: float
    status: str               # "OK" | "FAIL" | "REVIEW"
    warnings: list[str] = field(default_factory=list)

    @property
    def passes(self) -> bool:
        return self.status == "OK" and self.utilization <= 1.0


def check_member(
    sec: SectionProps,
    grade: str,
    demand: MemberDemand,
    knockdown: float = 1.0,
) -> MemberCheck:
    """Run all applicable EN 1993-1-1 checks. ``knockdown`` (<=1) reduces f_y for reclaimed steel."""
    fy_nom = FY_BY_GRADE.get(grade.upper(), 235.0) if grade else 235.0
    fy = fy_nom * knockdown
    warnings: list[str] = []
    if knockdown < 1.0:
        warnings.append(f"reclaimed knockdown applied: f_y {fy_nom:.0f} -> {fy:.0f} N/mm^2")

    section_class = classify(sec, fy, demand.N_Ed, demand.My_Ed)
    if section_class == 4:
        warnings.append("Class 4 (slender): effective-section design required; using W_el (approximate)")

    checks: list[CheckResult] = []

    # Axial
    if demand.N_Ed < 0:  # tension
        r = N_t_Rd(sec, fy)
        checks.append(CheckResult("tension", abs(demand.N_Ed) / r, {"N_Rd": r}))
    elif demand.N_Ed > 0:  # compression -> governed by buckling (weaker of two axes)
        nb_y, chi_y = N_b_Rd(sec, fy, demand.L, demand.ky, "y")
        nb_z, chi_z = N_b_Rd(sec, fy, demand.L, demand.kz, "z")
        nb = min(nb_y, nb_z)
        checks.append(CheckResult(
            "compression_buckling", demand.N_Ed / nb,
            {"N_b_Rd": nb, "chi_y": chi_y, "chi_z": chi_z, "axis": "z" if nb_z < nb_y else "y"},
        ))

    # Bending (major axis), with LTB when the compression flange is unrestrained
    if abs(demand.My_Ed) > 0:
        mc = M_c_Rd(sec, fy, section_class)
        if demand.compression_flange_restrained:
            mrd = mc
            # Surface what LTB *would* do without the slab restraint, so the chi_LT computation is
            # visible even on the (default) restrained path and restraint-critical beams are flagged.
            x_lt_free = chi_LT(sec, fy, demand.L, section_class, demand.C1) if demand.L > 0 else 1.0
            detail = {"M_c_Rd": mc, "chi_LT": 1.0, "restrained": True,
                      "chi_LT_if_unrestrained": round(x_lt_free, 4)}
            if x_lt_free < 0.85:
                warnings.append(
                    f"relies on compression-flange restraint: chi_LT would be {x_lt_free:.2f} if "
                    "unrestrained (verify the slab/bracing, especially at the construction stage)"
                )
        else:
            x_lt = chi_LT(sec, fy, demand.L, section_class, demand.C1)
            mrd = x_lt * mc
            detail = {"M_b_Rd": mrd, "chi_LT": round(x_lt, 4), "restrained": False}
            if x_lt < 1.0:
                warnings.append(
                    f"LTB governs: chi_LT={x_lt:.3f} (approx I_t/I_w, C1={demand.C1:g}); "
                    "verify restraints and moment shape"
                )
        checks.append(CheckResult("bending_y", abs(demand.My_Ed) / mrd, detail))

    # Shear
    if abs(demand.Vz_Ed) > 0:
        vrd = V_c_Rd(sec, fy)
        checks.append(CheckResult("shear_z", abs(demand.Vz_Ed) / vrd, {"V_c_Rd": vrd}))

    # Combined N + M (simplified conservative linear interaction, cl. 6.2.1(7) / member 6.3.3 approx).
    # LTB-aware: an unrestrained beam-column uses M_b_Rd (chi_LT-reduced), never the full M_c_Rd, so
    # the interaction cannot pass a member that lateral-torsional buckling would govern. This stays
    # conservative relative to the full 6.3.3 form (no favourable k_yy/k_zy factors are applied).
    if demand.N_Ed > 0 and abs(demand.My_Ed) > 0:
        nb_y, _ = N_b_Rd(sec, fy, demand.L, demand.ky, "y")
        nb_z, _ = N_b_Rd(sec, fy, demand.L, demand.kz, "z")
        mc = M_c_Rd(sec, fy, section_class)
        if demand.compression_flange_restrained:
            mrd, ltb = mc, 1.0
        else:
            ltb = chi_LT(sec, fy, demand.L, section_class, demand.C1)
            mrd = ltb * mc
        u = demand.N_Ed / min(nb_y, nb_z) + abs(demand.My_Ed) / mrd
        checks.append(CheckResult(
            "interaction_NM", u,
            {"method": "linear, LTB-aware (conservative)", "chi_LT": round(ltb, 4)},
        ))

    # Deflection (SLS), simply-supported UDL: delta = 5 w L^4 / (384 E I_y)
    if demand.w_service and demand.L > 0:
        delta = 5 * demand.w_service * demand.L**4 / (384 * E_STEEL * sec.Iy)
        limit = demand.L / demand.defl_limit_ratio
        checks.append(CheckResult("deflection", delta / limit,
                                  {"delta": delta, "limit": limit}))

    if not checks:
        checks.append(CheckResult("none", 0.0, {"note": "no actions supplied"}))

    governing_check = max(checks, key=lambda c: c.utilization)
    util = governing_check.utilization

    if util > 1.0:
        status = "FAIL"
    elif section_class == 4:
        status = "REVIEW"  # slender section needs effective-properties design
    else:
        status = "OK"

    return MemberCheck(
        section=sec.name, grade=grade or "?", fy=fy, section_class=section_class,
        checks=checks, governing=governing_check.name, utilization=util,
        status=status, warnings=warnings,
    )
