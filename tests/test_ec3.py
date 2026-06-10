"""Phase 2 tests: EN 1993-1-1 checks validated against hand calculations / section tables.

Reference (IPE300):
  S235  M_pl,Rd = W_pl * f_y = 628e3 * 235 = 147.6 kNm; V_pl,Rd = 348 kN  (matches tables)
  S275  M_pl,Rd = 628e3 * 275 = 172.7 kNm;             N_t,Rd = 1479.5 kN
"""

import math

import pytest

from steelreuse.core.ec3_checks import (
    GAMMA_M0,
    M_c_Rd,
    MemberDemand,
    N_b_Rd,
    N_t_Rd,
    V_c_Rd,
    check_member,
    classify,
    epsilon,
)
from steelreuse.core.sections import FY_BY_GRADE, load_catalog


@pytest.fixture(scope="module")
def cat():
    return load_catalog()


def test_epsilon():
    assert epsilon(235) == pytest.approx(1.0)
    assert epsilon(355) == pytest.approx(0.814, abs=1e-3)


def test_classification_ipe300_s275_is_class1(cat):
    assert classify(cat["IPE300"], 275, N_Ed=0, My_Ed=1e6) == 1


def test_tension_resistance_ipe300_s275(cat):
    assert N_t_Rd(cat["IPE300"], 275) == pytest.approx(1_479_500.0, rel=1e-4)


def test_bending_resistance_ipe300(cat):
    assert M_c_Rd(cat["IPE300"], 235, 1) == pytest.approx(147.58e6, rel=1e-3)
    assert M_c_Rd(cat["IPE300"], 275, 1) == pytest.approx(172.7e6, rel=1e-3)


def test_shear_resistance_ipe300_s235(cat):
    # Av = A - 2*b*tf + (tw+2r)*tf = 2567 mm^2 -> V = Av*fy/sqrt(3)
    assert V_c_Rd(cat["IPE300"], 235) == pytest.approx(348.3e3, rel=5e-3)


def test_buckling_chi_weak_axis_ipe300_s275(cat):
    # Hand calc: L=4000 mm, k=1, z-z curve b -> chi_z ~ 0.392
    nb_z, chi_z = N_b_Rd(cat["IPE300"], 275, L=4000, k=1.0, axis="z")
    assert chi_z == pytest.approx(0.392, abs=0.01)
    assert nb_z == pytest.approx(chi_z * cat["IPE300"].A * 275 / GAMMA_M0, rel=1e-6)


def test_compression_member_buckling_governs_weak_axis(cat):
    d = MemberDemand(N_Ed=300e3, L=4000, ky=1.0, kz=1.0)
    res = check_member(cat["IPE300"], "S275", d)
    g = next(c for c in res.checks if c.name == "compression_buckling")
    assert g.detail["axis"] == "z"  # weak axis governs
    assert res.governing == "compression_buckling"


def test_restrained_beam_passes_ok(cat):
    # My well below M_c,Rd, flange restrained -> clean OK
    d = MemberDemand(My_Ed=100e6, L=6000, compression_flange_restrained=True)
    res = check_member(cat["IPE300"], "S275", d)
    assert res.status == "OK"
    assert res.utilization == pytest.approx(100e6 / 172.7e6, rel=1e-3)


def test_unrestrained_beam_has_ltb_reduction(cat):
    # Same moment: restrained passes, unrestrained loses capacity to LTB (chi_LT < 1).
    restrained = check_member(cat["IPE300"], "S275",
                              MemberDemand(My_Ed=80e6, L=6000, compression_flange_restrained=True))
    unrestrained = check_member(cat["IPE300"], "S275",
                                MemberDemand(My_Ed=80e6, L=6000, compression_flange_restrained=False))
    assert unrestrained.utilization > restrained.utilization
    bend = next(c for c in unrestrained.checks if c.name == "bending_y")
    assert 0.3 < bend.detail["chi_LT"] < 0.7   # hand calc ~0.45 for IPE300 L=6 m
    assert any("LTB" in w for w in unrestrained.warnings)


def test_chi_lt_handcalc_ipe300(cat):
    from steelreuse.core.ec3_checks import chi_LT
    # IPE300 S275, L=6 m, uniform moment -> chi_LT ~ 0.45 (see project notes)
    assert chi_LT(cat["IPE300"], 275, L=6000, section_class=1) == pytest.approx(0.45, abs=0.05)


def test_longer_span_lowers_chi_lt(cat):
    from steelreuse.core.ec3_checks import chi_LT
    short = chi_LT(cat["IPE300"], 275, L=3000, section_class=1)
    long = chi_LT(cat["IPE300"], 275, L=8000, section_class=1)
    assert short > long  # longer unrestrained span -> more LTB-prone


def test_overload_fails(cat):
    d = MemberDemand(My_Ed=300e6, L=6000, compression_flange_restrained=True)
    res = check_member(cat["IPE300"], "S275", d)
    assert res.status == "FAIL" and res.utilization > 1.0


def test_reclaimed_knockdown_scales_utilization(cat):
    d = MemberDemand(My_Ed=100e6, L=6000, compression_flange_restrained=True)
    full = check_member(cat["IPE300"], "S275", d, knockdown=1.0)
    knocked = check_member(cat["IPE300"], "S275", d, knockdown=0.8)
    # lower f_y -> lower resistance -> utilization scales by 1/0.8
    assert knocked.utilization == pytest.approx(full.utilization / 0.8, rel=1e-3)
    assert any("knockdown" in w for w in knocked.warnings)


def test_deflection_check_simply_supported(cat):
    # delta = 5 w L^4 / (384 E I); w=10 N/mm, L=6000 -> ~9.62 mm, limit L/250=24 mm
    d = MemberDemand(My_Ed=1e6, L=6000, compression_flange_restrained=True, w_service=10.0)
    res = check_member(cat["IPE300"], "S275", d)
    defl = next(c for c in res.checks if c.name == "deflection")
    assert defl.detail["delta"] == pytest.approx(9.62, abs=0.1)
    assert defl.utilization == pytest.approx(9.62 / 24.0, abs=0.02)


def test_grades_table():
    assert FY_BY_GRADE["S355"] == 355 and math.isclose(FY_BY_GRADE["S235"], 235)


def test_restrained_beam_surfaces_unrestrained_chi_lt(cat):
    # #5: even when the slab restrains the flange (chi_LT used = 1.0), the "if unrestrained" chi_LT is
    # computed for the report and a restraint-reliance warning is raised when it would be low.
    d = MemberDemand(My_Ed=80e6, L=6000, compression_flange_restrained=True)
    res = check_member(cat["IPE300"], "S275", d)
    bend = next(c for c in res.checks if c.name == "bending_y")
    assert bend.detail["chi_LT"] == 1.0
    assert 0.3 < bend.detail["chi_LT_if_unrestrained"] < 0.7        # ~0.45 for IPE300, L=6 m
    assert any("relies on compression-flange restraint" in w for w in res.warnings)


def test_interaction_nm_is_ltb_aware(cat):
    # A beam-column under combined N+M: the interaction must use M_b_Rd (chi_LT-reduced) when the
    # compression flange is unrestrained, so LTB cannot be silently ignored in the combined check.
    sec = cat["IPE300"]
    N, M, L = 200e3, 60e6, 6000
    restrained = check_member(
        sec, "S275", MemberDemand(N_Ed=N, My_Ed=M, L=L, compression_flange_restrained=True)
    )
    unrestrained = check_member(
        sec, "S275", MemberDemand(N_Ed=N, My_Ed=M, L=L, compression_flange_restrained=False)
    )
    ir = next(c for c in restrained.checks if c.name == "interaction_NM")
    iu = next(c for c in unrestrained.checks if c.name == "interaction_NM")
    assert ir.detail["chi_LT"] == 1.0
    assert iu.detail["chi_LT"] < 1.0          # ~0.45 for IPE300, L=6 m
    assert iu.utilization > ir.utilization    # LTB lowers M_b_Rd -> higher combined utilization


# ---------------------------------------------------------------------------
# #6 heavy sections (t_f > 40 mm): EN Table 3.1 reduced f_y + Table 6.2 curve shift
# ---------------------------------------------------------------------------

def test_nominal_fy_thickness_bands():
    from steelreuse.core.sections import nominal_fy
    # EN 10025 grades band with thickness (Table 3.1): t<=40 nominal, 40<t<=80 reduced.
    assert nominal_fy("S355", 30) == 355.0
    assert nominal_fy("S355", 60) == 335.0
    assert nominal_fy("S275", 50) == 255.0
    assert nominal_fy("S235", 41) == 215.0
    # ASTM grades carry a single specified minimum F_y — no EN thickness banding.
    assert nominal_fy("A992", 10) == pytest.approx(345.0)
    assert nominal_fy("A992", 60) == pytest.approx(345.0)


def test_heavy_flange_shifts_buckling_curve(cat):
    import dataclasses

    from steelreuse.core.ec3_checks import _buckling_alpha
    base = cat["IPE300"]                         # h/b = 2.0 > 1.2
    thin = dataclasses.replace(base, tf=10.0)
    heavy = dataclasses.replace(base, tf=60.0)   # 40 < t_f <= 100
    jumbo = dataclasses.replace(base, tf=120.0)  # t_f > 100
    assert _buckling_alpha(thin, "y") == pytest.approx(0.21)    # curve a
    assert _buckling_alpha(thin, "z") == pytest.approx(0.34)    # curve b
    assert _buckling_alpha(heavy, "y") == pytest.approx(0.34)   # shifted a -> b
    assert _buckling_alpha(heavy, "z") == pytest.approx(0.49)   # shifted b -> c
    assert _buckling_alpha(jumbo, "y") == pytest.approx(0.76)   # curve d
    assert _buckling_alpha(jumbo, "z") == pytest.approx(0.76)


def test_heavy_flange_lowers_buckling_resistance(cat):
    import dataclasses
    base = cat["IPE300"]
    thin = dataclasses.replace(base, tf=10.0)
    heavy = dataclasses.replace(base, tf=60.0)   # only the curve changes (same I, A) -> lower chi
    _, chi_thin = N_b_Rd(thin, 355, L=4000, k=1.0, axis="y")
    _, chi_heavy = N_b_Rd(heavy, 355, L=4000, k=1.0, axis="y")
    assert chi_heavy < chi_thin                  # more conservative for the jumbo flange


def test_check_member_flags_heavy_section_and_reduces_en_fy(cat):
    import dataclasses
    heavy = dataclasses.replace(cat["IPE300"], tf=60.0)   # synthetic jumbo flange (>40 mm)
    res = check_member(heavy, "S355", MemberDemand(N_Ed=100e3, L=4000))
    assert res.fy == pytest.approx(335.0)                  # Table 3.1 reduced f_y for 40<t<=80
    assert any("heavy section" in w for w in res.warnings)
    assert any("f_y reduced" in w for w in res.warnings)
    # An ASTM W-shape with the same heavy flange gets the curve shift + warning but NO f_y reduction.
    us = check_member(dataclasses.replace(cat["IPE300"], tf=60.0, standard="US"), "A992",
                      MemberDemand(N_Ed=100e3, L=4000))
    assert us.fy == pytest.approx(345.0)
    assert any("heavy section" in w for w in us.warnings)
    assert not any("f_y reduced" in w for w in us.warnings)


# ---------------------------------------------------------------------------
# Full 6.3.3 beam-column interaction (Annex B Method 2) + biaxial bending
# ---------------------------------------------------------------------------
#
# Hand chain (IPE300 S275, L = 4 m, k = 1, restrained flange, N = 300 kN, My = 40 kNm):
#   N_Rk    = 5380 * 275 = 1479.5 kN
#   Ncr_y   = pi^2*210000*8356e4/4000^2 = 10824 kN -> lam_y = 0.3697, curve a -> chi_y = 0.9606
#   Ncr_z   = pi^2*210000*604e4/4000^2  =   782 kN -> lam_z = 1.3751, curve b -> chi_z = 0.3924
#   n_y     = 300/(0.9606*1479.5) = 0.2111;  n_z = 300/(0.3924*1479.5) = 0.5168
#   class 2 (web c/t = 35.01 <= 38*eps = 35.13 in compression) -> plastic moduli
#   My_Rk   = 628e3*275 = 172.7 kNm
#   kyy     = 1+(0.3697-0.2)*0.2111 = 1.0358 (cap 1.169 not hit);  kzy = 0.6*kyy = 0.6215
#   eq 6.61 = 0.2111 + 1.0358*40/172.7 = 0.4510
#   eq 6.62 = 0.5168 + 0.6215*40/172.7 = 0.6607   <- governs
# Adding Mz = 10 kNm (Mz_Rk = 125e3*275 = 34.375 kNm, mz = 0.2909):
#   kzz     = cap 1+1.4*n_z = 1.7235 (lam_z > 1);  kyz = 0.6*kzz = 1.0341
#   eq 6.61 = 0.4510 + 1.0341*0.2909 = 0.7518
#   eq 6.62 = 0.6607 + 1.7235*0.2909 = 1.1621     <- FAIL

def test_interaction_633_matches_hand_calc(cat):
    sec = cat["IPE300"]
    res = check_member(sec, "S275", MemberDemand(
        N_Ed=300e3, My_Ed=40e6, L=4000, compression_flange_restrained=True))
    inter = next(c for c in res.checks if c.name == "interaction_NM")
    assert inter.detail["eq_6_61"] == pytest.approx(0.4510, abs=2e-3)
    assert inter.detail["eq_6_62"] == pytest.approx(0.6607, abs=2e-3)
    assert inter.utilization == pytest.approx(0.6607, abs=2e-3)   # 6.62 governs
    assert inter.detail["kyy"] == pytest.approx(1.0358, abs=2e-3)
    assert inter.detail["kzy"] == pytest.approx(0.6215, abs=2e-3)  # 0.6*kyy (not susceptible)
    assert res.passes


def test_interaction_633_biaxial_mz_can_govern_and_fail(cat):
    sec = cat["IPE300"]
    res = check_member(sec, "S275", MemberDemand(
        N_Ed=300e3, My_Ed=40e6, Mz_Ed=10e6, L=4000, compression_flange_restrained=True))
    inter = next(c for c in res.checks if c.name == "interaction_NM")
    assert inter.detail["kzz"] == pytest.approx(1.7235, abs=2e-3)  # the 1+1.4*n_z cap (lam_z > 1)
    assert inter.detail["eq_6_62"] == pytest.approx(1.1621, abs=3e-3)
    assert not res.passes                                          # 10 kNm minor-axis flips it
    bz = next(c for c in res.checks if c.name == "bending_z")
    assert bz.utilization == pytest.approx(10e6 / (125e3 * 275), abs=1e-4)


def test_biaxial_bending_without_axial_uses_linear_cross_section_sum(cat):
    sec = cat["IPE300"]
    res = check_member(sec, "S275", MemberDemand(
        My_Ed=80e6, Mz_Ed=15e6, L=5000, compression_flange_restrained=True))
    bi = next(c for c in res.checks if c.name == "biaxial_M")
    assert bi.utilization == pytest.approx(80e6 / 172.7e6 + 15e6 / 34.375e6, rel=1e-3)


def test_interaction_633_less_conservative_than_old_linear_sum(cat):
    # The retired check was N/(chi_min*N_Rk) + My/M_Rd; 6.3.3 splits the axes (eq 6.61 uses chi_y,
    # and kzy < 1 softens the moment term in 6.62), so for a typical restrained beam-column the
    # governing 6.3.3 utilization must not exceed the old linear figure.
    sec = cat["IPE300"]
    d = MemberDemand(N_Ed=300e3, My_Ed=40e6, L=4000, compression_flange_restrained=True)
    res = check_member(sec, "S275", d)
    inter = next(c for c in res.checks if c.name == "interaction_NM")
    nb_z, _ = N_b_Rd(sec, 275, d.L, d.kz, "z")
    old_linear = d.N_Ed / nb_z + d.My_Ed / 172.7e6
    assert inter.utilization < old_linear
