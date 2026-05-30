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
