"""EN 1993-1-1 member checks for hot-rolled I/H and rect/square hollow sections — the ground truth.

Everything is in internal units: forces in N, moments in N*mm, lengths in mm, stresses in N/mm^2.
Sign convention: ``N_Ed`` is **compression-positive**; a negative value is treated as tension.

Scope & honesty (see docs/DESIGN_PRINCIPLES.md):
  * Implemented: classification, tension, compression+flexural buckling, biaxial bending (major axis
    with chi_LT, minor axis plain), shear, the full EN 1993-1-1 **6.3.3 beam-column interaction**
    (eq. 6.61/6.62 with Annex B Method 2 factors, C_m = 1.0 -> conservative for any moment shape),
    and an optional deflection (SLS) check.
  * The reclaimed-steel **knockdown** multiplies f_y (condition/uncertainty proxy), never silently 1.0
    when the caller asks for a reduction.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .sections import FY_BY_GRADE, SectionProps, nominal_fy

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


def _internal_part_class(ratio: float, eps: float, mode: str) -> int:
    """Class of an internal compression part (Table 5.2 sheet 1) from its c/t ratio."""
    limits = (33 * eps, 38 * eps, 42 * eps) if mode == "compression" else (72 * eps, 83 * eps, 124 * eps)
    for cls, lim in zip((1, 2, 3), limits, strict=True):
        if ratio <= lim:
            return cls
    return 4


def tube_class(sec: SectionProps, eps: float, mode: str) -> int:
    """Class of a rect/square hollow section (all walls are internal parts, Table 5.2 sheet 1).

    Flat width taken as ``c = h - 3t`` / ``b - 3t`` (the Table 5.2 convention for RHS). The flange
    (width-side wall) is in uniform compression under major-axis bending as well as under axial load,
    so it always uses the compression limits; only the webs get the bending limits in ``mode``
    'bending'.
    """
    t = sec.tf  # uniform wall
    flange = _internal_part_class(max(sec.b - 3 * t, 0.0) / t, eps, "compression")
    web = _internal_part_class(max(sec.h - 3 * t, 0.0) / t, eps, mode)
    return max(flange, web)


def chs_class(sec: SectionProps, eps: float) -> int:
    """Class of a round hollow section (CHS/pipe) from ``D/t``, EN 1993-1-1 Table 5.2 sheet 3.

    Tubular limits are on ``d/t`` against ``50 eps^2 / 70 eps^2 / 90 eps^2`` and apply to bending
    and/or compression alike (axisymmetric), so no ``mode`` is needed.
    """
    dt = sec.h / sec.tf                      # outer diameter / wall
    e2 = eps * eps
    for cls, lim in zip((1, 2, 3), (50 * e2, 70 * e2, 90 * e2), strict=True):
        if dt <= lim:
            return cls
    return 4


def channel_class(sec: SectionProps, eps: float, mode: str) -> int:
    """Class of a rolled channel (U/PFC/C): single-outstand flange + internal web (Table 5.2).

    The channel flange is a one-sided outstand from the web (``c = b - tw - r``), unlike the I-section
    flange whose outstand is measured each side of the web. The web is an internal part like the I web.
    """
    cf = max(sec.b - sec.tw - sec.r, 0.0)
    ratio_f = cf / sec.tf
    if ratio_f <= 9 * eps:
        flange = 1
    elif ratio_f <= 10 * eps:
        flange = 2
    elif ratio_f <= 14 * eps:
        flange = 3
    else:
        flange = 4
    web = _internal_part_class(max(sec.h - 2 * sec.tf - 2 * sec.r, 0.0) / sec.tw, eps, mode)
    return max(flange, web)


def angle_class(sec: SectionProps, eps: float) -> int:
    """Class of an equal/unequal-leg angle, EN 1993-1-1 Table 5.2 sheet 3.

    Angles have no plastic (class 1/2) range for the leg: a leg is **class 3** when both
    ``h/t <= 15 eps`` and ``(b + h)/(2t) <= 11.5 eps``, otherwise **class 4** (slender). The tool
    only checks angles in axial action; bending is flagged REVIEW (principal-axis biaxial),
    so this class drives the compression-resistance/effective-area path only.
    """
    t = sec.tf  # uniform leg thickness
    if sec.h / t <= 15 * eps and (sec.b + sec.h) / (2 * t) <= 11.5 * eps:
        return 3
    return 4


def classify(sec: SectionProps, fy: float, N_Ed: float = 0.0, My_Ed: float = 0.0) -> int:
    """Overall section class = worst of flange and web.

    Conservative web mode selection: pure compression if there is no bending, pure bending if there
    is no axial; otherwise the stricter 'compression' limits are used for combined N+M.
    """
    eps = epsilon(fy)
    if abs(My_Ed) < 1e-9 and N_Ed > 0:
        mode = "compression"
    elif N_Ed > 0:
        mode = "compression"   # combined N+M -> conservative
    else:
        mode = "bending"
    shape = sec.shape.upper()
    if sec.is_round:
        return chs_class(sec, eps)
    if sec.is_hollow:
        return tube_class(sec, eps, mode)
    if shape == "C":
        return channel_class(sec, eps, mode)
    if shape == "L":
        return angle_class(sec, eps)
    return max(flange_class(sec, eps), web_class(sec, eps, mode))


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


def M_z_Rd(sec: SectionProps, fy: float, section_class: int) -> float:
    """Minor-axis bending resistance, eq. (6.13)/(6.14). LTB does not apply about the minor axis."""
    W = sec.Wpl_z if section_class <= 2 else sec.Wel_z
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


def c1_moment_gradient(m_max: float, m_quarter: float, m_mid: float, m_three_quarter: float) -> float:
    """LTB moment-gradient factor ``C1`` (≈ AISC ``C_b``) from four moment samples along the segment.

    General 4-moment formula (NCCI SN003 / AISC F1):

        C1 = 12.5·|M_max| / (2.5·|M_max| + 3·|M_¼| + 4·|M_½| + 3·|M_¾|)

    It is valid for *any* moment diagram and uses magnitudes (sign-safe). It reduces to **1.0** for a
    uniform moment and **≈1.136** for a simply-supported beam under uniform load. Capped at the
    EN-conventional **2.70** and floored at **1.0** (uniform moment is the conservative baseline, and
    C1 > 1 must never be over-claimed from noisy sampling); a zero diagram returns 1.0.
    """
    mmax = abs(m_max)
    denom = 2.5 * mmax + 3 * abs(m_quarter) + 4 * abs(m_mid) + 3 * abs(m_three_quarter)
    if mmax <= 0.0 or denom <= 0.0:
        return 1.0
    return max(1.0, min(12.5 * mmax / denom, 2.70))


def _buckling_alpha(sec: SectionProps, axis: str) -> float:
    """Imperfection factor alpha from the buckling curve, EN 1993-1-1 Tables 6.1/6.2 (rolled I/H).

    Table 6.2 selects the curve from h/b *and the flange thickness* t_f — thicker flanges shift to a
    less favourable (higher-alpha) curve. The earlier version assumed t_f <= 40 mm and was therefore
    slightly non-conservative for jumbo sections (some heavy AISC W-shapes have t_f > 40 mm):
      * t_f > 100 mm                  -> curve d on both axes;
      * h/b > 1.2, t_f <= 40 mm       -> y: a, z: b;
      * h/b > 1.2, 40 < t_f <= 100 mm -> y: b, z: c;
      * h/b <= 1.2, t_f <= 100 mm     -> y: b, z: c.
    """
    a = {"a": 0.21, "b": 0.34, "c": 0.49, "d": 0.76}
    shape = sec.shape.upper()
    if sec.is_hollow:
        # Cold-formed hollow sections -> curve c, both axes (Table 6.2). AISC HSS (A500) are
        # cold-formed; hot-finished tube (curve a) would need a fabrication flag we don't have,
        # so the conservative curve is used for all hollow stock.
        return a["c"]
    if shape == "L":
        return a["b"]   # angles -> curve b (Table 6.2)
    if shape == "C":
        return a["c"]   # channels ("other sections") -> curve c, both axes (Table 6.2)
    if sec.tf > 100.0:
        curve = "d"
    elif sec.h / sec.b > 1.2:
        if sec.tf <= 40.0:
            curve = "a" if axis == "y" else "b"
        else:  # 40 < t_f <= 100
            curve = "b" if axis == "y" else "c"
    else:  # h/b <= 1.2, t_f <= 100
        curve = "b" if axis == "y" else "c"
    return a[curve]


def _chi(lambda_bar: float, alpha: float) -> float:
    """Reduction factor for flexural buckling, eq. (6.49)."""
    if lambda_bar <= 0.2:
        return 1.0
    phi = 0.5 * (1 + alpha * (lambda_bar - 0.2) + lambda_bar**2)
    chi = 1.0 / (phi + math.sqrt(phi**2 - lambda_bar**2))
    return min(chi, 1.0)


def _flexural_params(sec: SectionProps, fy: float, L: float, k: float, axis: str) -> tuple[float, float]:
    """Relative slenderness and reduction factor about ``axis`` ('y'|'z'): (lambda_bar, chi)."""
    I = sec.Iy if axis == "y" else sec.Iz  # noqa: E741
    Lcr = k * L
    Ncr = math.pi**2 * E_STEEL * I / Lcr**2
    lambda_bar = math.sqrt(sec.A * fy / Ncr)
    return lambda_bar, _chi(lambda_bar, _buckling_alpha(sec, axis))


def N_b_Rd(sec: SectionProps, fy: float, L: float, k: float, axis: str) -> tuple[float, float]:
    """Flexural-buckling resistance about ``axis`` ('y'|'z'), eq. (6.47). Returns (N_b_Rd, chi)."""
    _, chi = _flexural_params(sec, fy, L, k, axis)
    return chi * sec.A * fy / GAMMA_M1, chi


def N_b_Rd_minor(sec: SectionProps, fy: float, L: float, k: float) -> tuple[float, float]:
    """Flexural buckling about the **principal minor (v) axis** via ``i_min``, eq. (6.47).

    For angles the weak axis is rotated off the geometric axes, so the governing slenderness uses
    the principal minimum radius of gyration ``i_min`` (``i_v``), not ``iy``/``iz``. Curve from
    :func:`_buckling_alpha` (curve b for angles). Returns (N_b_Rd, chi).
    """
    Lcr = k * L
    Ncr = math.pi**2 * E_STEEL * (sec.A * sec.i_min**2) / Lcr**2
    lambda_bar = math.sqrt(sec.A * fy / Ncr)
    chi = _chi(lambda_bar, _buckling_alpha(sec, "z"))
    return chi * sec.A * fy / GAMMA_M1, chi


# ---------------------------------------------------------------------------
# EN 1993-1-1 6.3.3 beam-column interaction, Annex B (Method 2)
# ---------------------------------------------------------------------------

def cm_from_psi(psi: float) -> float:
    """Equivalent-uniform-moment factor ``Cm`` for linear end moments, EN 1993-1-1 Annex B Table B.3:
    ``Cm = 0.6 + 0.4·ψ ≥ 0.4`` with the end-moment ratio ``ψ`` clamped to ``[-1, 1]``."""
    psi = max(-1.0, min(psi, 1.0))
    return max(0.6 + 0.4 * psi, 0.4)


def end_moment_ratio(m_i: float, m_j: float) -> float:
    """Signed end-moment ratio ``ψ ∈ [-1, 1]`` from a member's two end moments.

    ``ψ`` = (smaller-magnitude end moment) / (larger-magnitude end moment), keeping signs — so equal
    same-sign ends give ``+1`` (single curvature) and equal opposite-sign ends give ``-1`` (double
    curvature). A zero diagram returns ``+1`` (uniform → ``Cm = 1.0``, the conservative default). The
    two moments must share a consistent convention (same sign ⇒ single curvature)."""
    a, b = (m_i, m_j) if abs(m_i) >= abs(m_j) else (m_j, m_i)
    if a == 0.0:
        return 1.0
    return max(-1.0, min(b / a, 1.0))


def annex_b_k_factors(
    section_class: int,
    lam_y: float,
    lam_z: float,
    n_y: float,
    n_z: float,
    hollow: bool,
    susceptible: bool,
    Cmy: float = 1.0,
    Cmz: float = 1.0,
    CmLT: float = 1.0,
) -> dict[str, float]:
    """Interaction factors k_yy, k_yz, k_zy, k_zz per EN 1993-1-1 Annex B (Method 2).

    ``n_y``/``n_z`` are N_Ed/(chi*N_Rk/gamma_M1); ``susceptible`` means susceptible to torsional
    deformation (an open section without restraint -> Table B.2/B.1 'members susceptible' column).
    All C_m factors default to 1.0 (uniform equivalent moment) — the upper bound of Table B.3, so
    conservative for any real moment shape.
    """
    if section_class <= 2:  # Table B.1
        kyy = min(Cmy * (1 + (min(lam_y, 1.0) - 0.2) * n_y), Cmy * (1 + 0.8 * n_y))
        if hollow:
            kzz = min(Cmz * (1 + (min(lam_z, 1.0) - 0.2) * n_z), Cmz * (1 + 0.8 * n_z))
        else:
            kzz = min(Cmz * (1 + (2 * min(lam_z, 1.0) - 0.6) * n_z), Cmz * (1 + 1.4 * n_z))
        kyz = 0.6 * kzz
        if not susceptible:
            kzy = 0.6 * kyy
        elif lam_z < 0.4:
            kzy = min(0.6 + lam_z, 1 - 0.1 * lam_z * n_z / (CmLT - 0.25))
        else:
            kzy = max(1 - 0.1 * min(lam_z, 1.0) * n_z / (CmLT - 0.25),
                      1 - 0.1 * n_z / (CmLT - 0.25))
    else:  # Table B.2 (class 3; class 4 is approximated with elastic moduli and flagged upstream)
        kyy = min(Cmy * (1 + 0.6 * min(lam_y, 1.0) * n_y), Cmy * (1 + 0.6 * n_y))
        kzz = min(Cmz * (1 + 0.6 * min(lam_z, 1.0) * n_z), Cmz * (1 + 0.6 * n_z))
        kyz = kzz
        if not susceptible:
            kzy = 0.8 * kyy
        else:
            kzy = max(1 - 0.05 * min(lam_z, 1.0) * n_z / (CmLT - 0.25),
                      1 - 0.05 * n_z / (CmLT - 0.25))
    return {"kyy": kyy, "kyz": kyz, "kzy": kzy, "kzz": kzz}


# ---------------------------------------------------------------------------
# Demand + result containers
# ---------------------------------------------------------------------------

@dataclass
class MemberDemand:
    """Design action effects + buckling/serviceability context for one member or span."""

    N_Ed: float = 0.0          # N, compression-positive (negative = tension)
    My_Ed: float = 0.0         # N*mm, major-axis bending
    Mz_Ed: float = 0.0         # N*mm, minor-axis bending (from lateral/sway frame cases)
    Vz_Ed: float = 0.0         # N
    L: float = 0.0             # mm, system length for buckling/deflection
    ky: float = 1.0            # buckling length factor about y
    kz: float = 1.0            # about z
    compression_flange_restrained: bool = False
    C1: float = 1.0            # LTB moment-distribution factor (1.0 = uniform moment, conservative)
    Cmy: float = 1.0           # 6.3.3 equivalent-uniform-moment factor, major axis (1.0 = conservative)
    Cmz: float = 1.0           # 6.3.3 equivalent-uniform-moment factor, minor axis (1.0 = conservative)
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
    # Thickness-dependent nominal f_y (EN 1993-1-1 Table 3.1): the flange t_f governs a rolled I/H.
    fy_nom = nominal_fy(grade, sec.tf)
    fy = fy_nom * knockdown
    warnings: list[str] = []
    if knockdown < 1.0:
        warnings.append(f"reclaimed knockdown applied: f_y {fy_nom:.0f} -> {fy:.0f} N/mm^2")
    if sec.tf > 40.0:
        msg = f"heavy section (t_f={sec.tf:.0f} mm > 40 mm): EN Table 6.2 buckling curve shifted"
        if fy_nom < (FY_BY_GRADE.get(grade.upper(), 235.0) if grade else 235.0):
            msg += f"; f_y reduced to {fy_nom:.0f} N/mm^2 (Table 3.1)"
        warnings.append(msg)

    section_class = classify(sec, fy, demand.N_Ed, demand.My_Ed)
    if section_class == 4:
        warnings.append("Class 4 (slender): effective-section design required; using W_el (approximate)")

    # Angles are checked in axial action only: their bending response is biaxial about rotated
    # principal axes (no doubly/singly-symmetric simplification holds), so any bending demand is
    # flagged REVIEW rather than given a capacity number (see the bending blocks below).
    is_angle = sec.shape.upper() == "L"
    angle_bending = is_angle and (abs(demand.My_Ed) > 0 or abs(demand.Mz_Ed) > 0)
    if angle_bending:
        warnings.append(
            "angle under bending: biaxial about rotated principal axes — not auto-checked; "
            "status REVIEW (verify by hand / principal-axis analysis)"
        )
    if sec.shape.upper() == "C" and abs(demand.My_Ed) > 0:
        warnings.append(
            "channel: mono-symmetric — M_cr/LTB uses the doubly-symmetric I_t/I_w approximation "
            "(shear-centre offset and load position not modelled); verify restraints"
        )

    checks: list[CheckResult] = []

    # Axial
    if demand.N_Ed < 0:  # tension
        r = N_t_Rd(sec, fy)
        checks.append(CheckResult("tension", abs(demand.N_Ed) / r, {"N_Rd": r}))
    elif demand.N_Ed > 0:  # compression -> governed by buckling (weakest axis)
        if is_angle:
            # Angle: governing buckling is about the principal minor (v) axis via i_min.
            nb, chi_v = N_b_Rd_minor(sec, fy, demand.L, max(demand.ky, demand.kz))
            checks.append(CheckResult(
                "compression_buckling", demand.N_Ed / nb,
                {"N_b_Rd": nb, "chi_v": chi_v, "axis": "v (principal min, i_min)"},
            ))
        else:
            nb_y, chi_y = N_b_Rd(sec, fy, demand.L, demand.ky, "y")
            nb_z, chi_z = N_b_Rd(sec, fy, demand.L, demand.kz, "z")
            nb = min(nb_y, nb_z)
            checks.append(CheckResult(
                "compression_buckling", demand.N_Ed / nb,
                {"N_b_Rd": nb, "chi_y": chi_y, "chi_z": chi_z, "axis": "z" if nb_z < nb_y else "y"},
            ))

    # Bending (major axis), with LTB when the compression flange is unrestrained
    if abs(demand.My_Ed) > 0 and not is_angle:
        mc = M_c_Rd(sec, fy, section_class)
        if sec.is_hollow:
            # Closed sections are torsionally stiff: lambda_bar_LT stays far below the 0.4 plateau
            # for any practical span, so LTB is not a design case (EN 1993-1-1 cl. 6.3.2.1(2) scope;
            # the open-section I_t/I_w approximations below would also be meaningless for a tube).
            mrd = mc
            detail = {"M_c_Rd": mc, "chi_LT": 1.0, "hollow": True}
        elif demand.compression_flange_restrained:
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

    # Bending (minor axis) — no LTB about z; plastic/elastic modulus per class
    if abs(demand.Mz_Ed) > 0 and not is_angle:
        mz = M_z_Rd(sec, fy, section_class)
        checks.append(CheckResult("bending_z", abs(demand.Mz_Ed) / mz, {"M_z_Rd": mz}))

    # Cross-section biaxial bending without axial: conservative linear sum, cl. 6.2.1(7)
    # (the full 6.2.9 alpha/beta exponents would allow more; linear is always on the safe side)
    if demand.N_Ed <= 0 and abs(demand.My_Ed) > 0 and abs(demand.Mz_Ed) > 0 and not is_angle:
        u = (abs(demand.My_Ed) / M_c_Rd(sec, fy, section_class)
             + abs(demand.Mz_Ed) / M_z_Rd(sec, fy, section_class))
        checks.append(CheckResult("biaxial_M", u, {"method": "linear cross-section sum, cl. 6.2.1(7)"}))

    # Shear
    if abs(demand.Vz_Ed) > 0:
        vrd = V_c_Rd(sec, fy)
        checks.append(CheckResult("shear_z", abs(demand.Vz_Ed) / vrd, {"V_c_Rd": vrd}))

    # Shear-moment interaction, cl. 6.2.8: above half the plastic shear resistance, bending must use
    # a reduced yield (1 - rho) on the shear area, rho = (2 V_Ed/V_pl,Rd - 1)^2. Peaks of M and V are
    # coincident here (conservative for a UDL span, where they occur at different points). Rolled
    # I/H use eq. (6.30) (modulus reduced by rho*A_w^2/(4 t_w)); hollow sections take the plainly
    # conservative (1 - rho) on the whole bending resistance.
    if abs(demand.My_Ed) > 0 and not is_angle and abs(demand.Vz_Ed) > 0.5 * V_c_Rd(sec, fy):
        vrd = V_c_Rd(sec, fy)
        # rho is the shear-yield reduction (1 - rho) on the bending resistance; it is physically
        # bounded at 1.0 (a 100% reduction). Uncapped, V_Ed > V_pl,Rd gives rho > 1 -> a *negative*
        # reduced resistance and a negative utilisation. Cap it; for V_Ed >= V_pl,Rd the shear_z check
        # already reports utilisation >= 1 and governs.
        rho = min((2.0 * abs(demand.Vz_Ed) / vrd - 1.0) ** 2, 1.0)
        mc = M_c_Rd(sec, fy, section_class)
        if sec.is_hollow:
            m_v = (1.0 - rho) * mc
        else:
            Wy = sec.Wpl_y if section_class <= 2 else sec.Wel_y
            Aw = (sec.h - 2.0 * sec.tf) * sec.tw
            m_v = min(mc, (Wy - rho * Aw**2 / (4.0 * sec.tw)) * fy / GAMMA_M0)
        if m_v > 0.0:                       # m_v == 0 only in the degenerate V_Ed = V_pl,Rd case
            checks.append(CheckResult(
                "bending_shear_MV", abs(demand.My_Ed) / m_v,
                {"method": "cl. 6.2.8, eq. (6.30)", "rho": round(rho, 4), "M_y_V_Rd": m_v},
            ))
            warnings.append(
                f"high shear (V_Ed > 0.5 V_pl,Rd): bending resistance reduced per cl. 6.2.8 "
                f"(rho={rho:.2f})"
            )

    # Combined compression + bending: member buckling interaction, EN 1993-1-1 6.3.3 eq. (6.61)/(6.62)
    # with Annex B (Method 2) interaction factors. All C_m = 1.0 (uniform equivalent moment, the
    # Table B.3 upper bound -> conservative for any real moment shape). LTB-aware: chi_LT multiplies
    # M_y,Rk exactly as in eq. (6.61)/(6.62), so an unrestrained beam-column can never pass on a
    # moment that lateral-torsional buckling would govern.
    if demand.N_Ed > 0 and (abs(demand.My_Ed) > 0 or abs(demand.Mz_Ed) > 0) and not is_angle:
        lam_y, x_y = _flexural_params(sec, fy, demand.L, demand.ky, "y")
        lam_z, x_z = _flexural_params(sec, fy, demand.L, demand.kz, "z")
        N_Rk = sec.A * fy
        n_y = demand.N_Ed / (x_y * N_Rk / GAMMA_M1)
        n_z = demand.N_Ed / (x_z * N_Rk / GAMMA_M1)
        susceptible = not (sec.is_hollow or demand.compression_flange_restrained)
        ltb = (chi_LT(sec, fy, demand.L, section_class, demand.C1)
               if susceptible and abs(demand.My_Ed) > 0 and demand.L > 0 else 1.0)
        k = annex_b_k_factors(section_class, lam_y, lam_z, n_y, n_z,
                              hollow=sec.is_hollow, susceptible=susceptible,
                              Cmy=demand.Cmy, Cmz=demand.Cmz)
        My_Rk = (sec.Wpl_y if section_class <= 2 else sec.Wel_y) * fy
        Mz_Rk = (sec.Wpl_z if section_class <= 2 else sec.Wel_z) * fy
        my_term = abs(demand.My_Ed) / (ltb * My_Rk / GAMMA_M1)
        mz_term = abs(demand.Mz_Ed) / (Mz_Rk / GAMMA_M1)
        u_661 = n_y + k["kyy"] * my_term + k["kyz"] * mz_term
        u_662 = n_z + k["kzy"] * my_term + k["kzz"] * mz_term
        checks.append(CheckResult(
            "interaction_NM", max(u_661, u_662),
            {"method": "EN 1993-1-1 6.3.3, Annex B Method 2",
             "Cmy": round(demand.Cmy, 3), "Cmz": round(demand.Cmz, 3), "C1": round(demand.C1, 3),
             "eq_6_61": round(u_661, 4), "eq_6_62": round(u_662, 4),
             "chi_LT": round(ltb, 4), **{f: round(v, 4) for f, v in k.items()}},
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
    elif section_class == 4 or angle_bending:
        status = "REVIEW"  # slender section / angle under bending needs hand verification
    else:
        status = "OK"

    return MemberCheck(
        section=sec.name, grade=grade or "?", fy=fy, section_class=section_class,
        checks=checks, governing=governing_check.name, utilization=util,
        status=status, warnings=warnings,
    )
