"""Rect/square HSS support: catalog import, shape-aware EN 1993-1-1 checks, baseline restriction.

Anchor values are verbatim from the AISC Shapes Database v15.0 (HSS6X6X1/2: A = 9.74 in^2,
W = 35.24 lb/ft, Ix = 48.3 in^4, Sx = 16.1 in^3, Zx = 19.8 in^3, tdes = 0.465 in).
"""

import math

import pytest

from steelreuse.core.ec3_checks import (
    MemberDemand,
    _buckling_alpha,
    check_member,
    classify,
)
from steelreuse.core.sections import (
    default_grade_for_section,
    load_catalog_hss,
    load_default_catalog,
    map_section,
)
from steelreuse.match.optimize import DemandSlot, baseline_new_mass_kg

IN_MM = 25.4


@pytest.fixture(scope="module")
def catalog():
    return load_default_catalog()


@pytest.fixture(scope="module")
def hss(catalog):
    return catalog["HSS6X6X1/2"]


# --- catalog import ---------------------------------------------------------

def test_hss_catalog_loads_and_converts_units(hss):
    assert hss.shape == "HSS" and hss.is_hollow and hss.standard == "US"
    assert hss.h == pytest.approx(6 * IN_MM)
    assert hss.b == pytest.approx(6 * IN_MM)
    assert hss.tf == pytest.approx(0.465 * IN_MM)       # design wall, uniform
    assert hss.tw == hss.tf
    assert hss.A == pytest.approx(9.74 * 645.16)
    assert hss.mass_kgm == pytest.approx(35.24 * 1.488_163_94)
    assert hss.Iy == pytest.approx(48.3 * 416_231.4256)
    assert hss.Wpl_y == pytest.approx(19.8 * 16_387.064)


def test_hss_merged_into_default_catalog(catalog):
    # 388 US rectangular/square HSS + 29 round CHS (15 US round HSS/Pipe + 14 EN 10210 CHS).
    n_hss = sum(1 for s in catalog.values() if s.is_hollow)
    assert n_hss == 417
    assert sum(1 for s in catalog.values() if s.is_round) == 29
    assert len(load_catalog_hss()) == 388
    # W-shapes and EU sections are untouched by the merge
    assert not catalog["IPE300"].is_hollow and not catalog["W14X30"].is_hollow


def test_hss_maps_and_gets_a500_default(catalog):
    r = map_section("HSS-Square Column HSS6x6x1/2", catalog)
    assert r.method in ("exact", "normalized") and r.canonical == "HSS6X6X1/2"
    assert default_grade_for_section("HSS6X6X1/2") == "A500"


# --- shape-aware checks -----------------------------------------------------

def test_hss_shear_area_is_the_web_share(hss):
    # Uniform-wall RHS, EN 1993-1-1 6.2.6(3): A_v = A*h/(b+h); square -> half the area.
    assert hss.Av_z == pytest.approx(hss.A / 2.0)


def test_hss_classification_internal_parts(catalog, hss):
    # c/t = (152.4 - 3*11.811)/11.811 = 9.90 <= 33*eps (27.2 at fy=345) -> class 1.
    fy = 345.0
    assert classify(hss, fy, N_Ed=1.0) == 1
    # Thin-wall tube: c/t = (304.8 - 3*4.42)/4.42 = 66 > 42*eps (34.7) -> class 4 (slender).
    assert classify(catalog["HSS12X12X3/16"], fy, N_Ed=1.0) == 4


def test_hss_uses_buckling_curve_c(hss, catalog):
    assert _buckling_alpha(hss, "y") == 0.49
    assert _buckling_alpha(hss, "z") == 0.49
    # ...while a typical rolled W keeps its h/b-based curve
    assert _buckling_alpha(catalog["W14X30"], "y") == 0.21


def test_hss_bending_has_no_ltb_even_unrestrained(hss, catalog):
    demand = MemberDemand(My_Ed=30e6, L=8000.0, compression_flange_restrained=False)
    res = check_member(hss, "A500", demand)
    bend = next(c for c in res.checks if c.name == "bending_y")
    assert bend.detail["chi_LT"] == 1.0 and bend.detail.get("hollow") is True
    assert not any("LTB governs" in w for w in res.warnings)
    # sanity: an open section of similar depth at the same span *is* LTB-reduced
    res_w = check_member(catalog["IPE300"], "S355", demand)
    bend_w = next(c for c in res_w.checks if c.name == "bending_y")
    assert bend_w.detail["chi_LT"] < 1.0


def test_hss_member_check_passes_hand_calc(hss):
    # M_c,Rd = Wpl*fy = 19.8*16387.064 mm^3 * 345 N/mm^2 = 111.9 kNm (class 1, gammaM0 = 1)
    demand = MemberDemand(My_Ed=50e6, Vz_Ed=50e3, L=6000.0)
    res = check_member(hss, "A500", demand)
    mc_rd = 19.8 * 16_387.064 * 345.0
    bend = next(c for c in res.checks if c.name == "bending_y")
    assert bend.utilization == pytest.approx(50e6 / mc_rd, rel=1e-6)
    assert res.status == "OK"


def test_hss_compression_buckling_curve_c_hand_calc(hss):
    # chi from curve c at the slenderness this geometry gives; cross-check N_b,Rd end to end.
    fy, L = 345.0, 4000.0
    demand = MemberDemand(N_Ed=500e3, L=L)
    res = check_member(hss, "A500", demand)
    comp = next(c for c in res.checks if c.name == "compression_buckling")
    Ncr = math.pi**2 * 210_000.0 * hss.Iz / L**2
    lam = math.sqrt(hss.A * fy / Ncr)
    phi = 0.5 * (1 + 0.49 * (lam - 0.2) + lam**2)
    chi = 1.0 / (phi + math.sqrt(phi**2 - lam**2))
    assert comp.detail["chi_z"] == pytest.approx(chi, rel=1e-6)
    assert comp.detail["N_b_Rd"] == pytest.approx(chi * hss.A * fy, rel=1e-6)


# --- avoided-new baseline stays within the shape family ----------------------

def _slot(design_section, grade="A992"):
    demand = MemberDemand(My_Ed=40e6, Vz_Ed=40e3, L=5000.0, compression_flange_restrained=True)
    return DemandSlot(id="s1", member_id="m1", role="beam", required_length_mm=5000.0,
                      demand=demand, grade=grade, design_section=design_section)


def test_baseline_stays_within_the_shape_family(catalog):
    # An absurdly light tube must not become the avoided-new baseline of an open-section slot:
    # the baseline is "the new member you would otherwise buy", and that is an I/H unless the
    # design says tube. Both candidates pass the slot's check; only the family filter separates them.
    from dataclasses import replace

    w = catalog["W12X26"]
    light_tube = replace(catalog["HSS6X6X1/2"], name="HSSLIGHT", mass_kgm=1.0)
    minicat = {"W12X26": w, "HSSLIGHT": light_tube}

    assert baseline_new_mass_kg(_slot("W12X26"), minicat) == pytest.approx(w.mass_kgm * 5.0)
    # no design section -> open family by default (legacy behaviour, tubes never sneak in)
    assert baseline_new_mass_kg(_slot(None), minicat) == pytest.approx(w.mass_kgm * 5.0)
    # the design explicitly specifies a tube -> the baseline is the tube
    assert baseline_new_mass_kg(_slot("HSSLIGHT", grade="A500"), minicat) == pytest.approx(5.0)
