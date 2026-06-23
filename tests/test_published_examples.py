"""Cross-check the EN 1993-1-1 engine against INDEPENDENTLY PUBLISHED worked examples.

The rest of the validation suite (``test_ec3.py``, ``test_validation.py``) checks the engine against
our *own* hand algebra. This file closes that gap: every number below is taken from a published design
example by an external authority, so the engine is corroborated against third-party calculations.

Sources (both free):
  * **SCI P387** "Steel Building Design: Worked examples for students" (Steel Construction Institute,
    Eurocodes + UK National Annex). Examples 01, 02, 05.
  * **ArcelorMittal / SECEU "Multi-Storey Steel Buildings, Part 4: Detailed Design" (MSB04)**, Appendix A
    worked examples (base EN 1993-1-1, recommended values). Examples A.1, A.5.

Section properties are entered exactly as tabulated in each source, so the test does not depend on our
own catalog. gamma_M0 = gamma_M1 = 1.0 in both sources, matching the engine.

Two documented engine simplifications surface here and are guarded explicitly rather than hidden:
  1. f_y banding — the engine uses EN 1993-1-1 Table 3.1 coarse bands (t <= 40 mm -> full f_y), not the
     finer EN 10025-2 product banding P387 uses (which drops S275 to 265 for 16 < t <= 40 mm). So for
     P387 Ex02 (t_f = 19.6 mm) we cross-check the f_y-INDEPENDENT elastic critical moment M_cr instead.
  2. load height z_g — the engine's M_cr assumes the load acts at the shear centre (z_g = 0). MSB04 A.1
     loads the top flange (z_g = +165 mm, destabilising), which lowers the real M_cr. test A.1 guards
     this as a known NON-conservative gap (the engine over-predicts M_cr for top-flange loading).
"""

import math

from steelreuse.core.ec3_checks import (
    M_c_Rd,
    M_cr,
    MemberDemand,
    N_b_Rd,
    V_c_Rd,
    check_member,
    chi_LT,
    chi_LT_member,
    classify,
)
from steelreuse.core.sections import SectionProps


def _i(name, h, b, tw, tf, r, A, Iy, Iz, Wel_y, Wpl_y, *, shape="I",
       Wel_z=0.0, Wpl_z=0.0):
    """Build an I/H SectionProps from the tabulated values of a worked example (mm/mm^2/mm^4/mm^3).

    iy/iz/Wel_z/Wpl_z do not affect any check exercised here (buckling uses I and A directly; no minor
    bending) — they are filled from the geometry only so the dataclass is well-formed."""
    return SectionProps(
        name=name, shape=shape, h=h, b=b, tw=tw, tf=tf, r=r, A=A, mass_kgm=A * 7.85e-3,
        Iy=Iy, Wel_y=Wel_y, Wpl_y=Wpl_y, iy=math.sqrt(Iy / A),
        Iz=Iz, Wel_z=Wel_z or 2 * Iz / b, Wpl_z=Wpl_z or 3 * Iz / b, iz=math.sqrt(Iz / A),
        standard="EU",
    )


# ---------------------------------------------------------------------------
# SCI P387 Example 01 — Simply supported FULLY RESTRAINED beam
# 457x191x82 UKB, S275 (t_f = 16 mm -> f_y = 275). Class 1.
# Published results: M_c,Rd = 503 kNm, V_c,Rd = 756 kN, delta = 13.6 mm (q_k only).
# ---------------------------------------------------------------------------

_UKB_457x191x82 = _i("457x191x82 UKB", h=460.0, b=191.3, tw=9.9, tf=16.0, r=10.2,
                     A=10400.0, Iy=37100e4, Iz=1870e4, Wel_y=1610e3, Wpl_y=1830e3)


def test_p387_ex01_restrained_beam():
    sec, fy = _UKB_457x191x82, 275.0
    assert classify(sec, fy, My_Ed=1e6) == 1                       # Class 1 in bending
    assert M_c_Rd(sec, fy, 1) == 503.25e6                          # 1830e3 * 275 (P387: "503 kNm")
    # P387 Av = A - 2 b t_f + (t_w + 2 r) t_f = 4763 mm^2 -> V_c,Rd = 756 kN
    assert abs(sec.Av_z - 4763.0) < 1.0
    assert abs(V_c_Rd(sec, fy) - 756.3e3) < 1e3
    # SLS deflection under q_k = 19.8 N/mm over L = 8.0 m -> 13.6 mm (P387 sheet 5)
    res = check_member(sec, "S275", MemberDemand(My_Ed=1e6, L=8000.0, w_service=19.8,
                                                 compression_flange_restrained=True))
    defl = next(c for c in res.checks if c.name == "deflection")
    assert abs(defl.detail["delta"] - 13.6) < 0.1
    assert res.fy == 275.0                                          # t_f = 16 mm: both use 275


# ---------------------------------------------------------------------------
# SCI P387 Example 02 — Simply supported UNRESTRAINED beam (LTB)
# 457x191x98 UKB, S275, L = 6.0 m, load NOT destabilising (z_g taken as 0).
# Published rigorous M_cr = 534.0 kNm (C1 = 1.127, k = k_w = 1, z_g = 0).
# This is the cleanest LTB cross-check: M_cr is purely elastic (E, G, I) -> f_y-independent, so it is
# immune to the Table 3.1 vs EN 10025-2 f_y-banding difference (P387 uses f_y = 265 for this section).
# ---------------------------------------------------------------------------

_UKB_457x191x98 = _i("457x191x98 UKB", h=467.2, b=192.8, tw=11.4, tf=19.6, r=10.2,
                     A=12500.0, Iy=45700e4, Iz=2350e4, Wel_y=1960e3, Wpl_y=2230e3)


def test_p387_ex02_elastic_critical_moment():
    sec = _UKB_457x191x98
    # Engine M_cr with the example's C1 must reproduce the published 534.0 kNm (within ~1%; the engine
    # uses G = 80769 vs the example's 81000, and a thin-wall I_t/I_w estimate vs tabulated values).
    mcr = M_cr(sec, L=6000.0, C1=1.127)
    assert abs(mcr - 534.0e6) / 534.0e6 < 0.01
    # LTB curve selection matches the example: h/b = 2.42 > 2 -> curve c (alpha_LT = 0.49).
    # Engine chi_LT (un-modified, f_y = 265 as P387) ~ 0.60, vs P387's 0.601 before its k_c/f boost.
    # The engine does NOT apply the EN 6.3.2.3(2) k_c/f modification, so it stays slightly conservative
    # (P387's modified value is 0.617 -> Mb,Rd 365 kNm; the engine yields ~359 kNm).
    x_lt = chi_LT(sec, 265.0, L=6000.0, section_class=1, C1=1.127)
    assert 0.59 <= x_lt <= 0.62


# ---------------------------------------------------------------------------
# MSB04 Example A.1 — Simply supported, laterally unrestrained beam
# IPE 330, S235, L = 5.70 m. Independent (non-UK) base-EC3 source.
# Published: M_c,Rd = 189.01 kNm, V_pl,Rd = 417.9 kN, Class 1.
#   M_cr = 113.9 kNm BUT computed with z_g = +165 mm (top-flange / destabilising load) + a C2 term.
# ---------------------------------------------------------------------------

_IPE330 = _i("IPE 330", h=330.0, b=160.0, tw=7.5, tf=11.5, r=18.0,
             A=6260.0, Iy=11770e4, Iz=788.1e4, Wel_y=713.1e3, Wpl_y=804.3e3)


def test_msb04_a1_ipe330_bending_and_shear():
    sec, fy = _IPE330, 235.0
    assert classify(sec, fy, My_Ed=1e6) == 1
    assert abs(M_c_Rd(sec, fy, 1) - 189.01e6) < 0.05e6        # 804.3e3 * 235 = 189.01 kNm
    assert abs(sec.Av_z - 3080.0) < 1.0                        # A - 2 b t_f + (t_w + 2 r) t_f
    assert abs(V_c_Rd(sec, fy) - 417.9e3) / 417.9e3 < 1e-3    # 3080 * 235 / sqrt(3)


def test_msb04_a1_load_height_zg_now_modelled():
    """Load height is now modelled. MSB04 A.1's top-flange load (z_g = +165 mm, C2 = 0.454) lowers the
    real M_cr to a published 113.9 kNm. The engine, fed the same z_g/C2, lands at ~101 kNm — below the
    published value because it uses a thin-wall I_t/I_w estimate that under-predicts on purpose (lower
    M_cr -> lower chi_LT -> conservative), as documented. The key correctness gains, both guarded here:
      * z_g lowers M_cr (the old shear-centre z_g = 0 form was the non-conservative upper bound), and
      * the member LTB check defaults to the destabilising z_g = +h/2 (chi_LT_member), so an unrestrained
        floor/roof beam can no longer pass on an over-stated, shear-centre M_cr."""
    mcr_zg = M_cr(_IPE330, L=5700.0, C1=1.127, C2=0.454, zg=165.0)
    mcr_sc = M_cr(_IPE330, L=5700.0, C1=1.127)                # shear centre (z_g = 0)
    assert mcr_zg < mcr_sc                                    # load height lowers M_cr
    assert mcr_zg <= 113.9e6                                  # engine stays at/below the published value
    assert mcr_zg / 113.9e6 > 0.85                            # but in the right ballpark (~0.89)
    # The member wrapper applies the conservative top-flange default automatically.
    assert chi_LT_member(_IPE330, 235.0, L=5700.0, section_class=1, C1=1.127) \
        < chi_LT(_IPE330, 235.0, L=5700.0, section_class=1, C1=1.127)


# ---------------------------------------------------------------------------
# SCI P387 Example 05 — Column in simple construction (flexural buckling)
# 254x254x73 UKC, S275 (t_f = 14.2 mm -> f_y = 275), L_cr = 5.0 m, weak axis governs.
# Published: chi_z = 0.61 (curve c), N_b,z,Rd = 1562 kN.
# ---------------------------------------------------------------------------

_UKC_254x254x73 = _i("254x254x73 UKC", h=254.1, b=254.6, tw=8.6, tf=14.2, r=12.7,
                     A=9310.0, Iy=11410e4, Iz=3910e4, Wel_y=898e3, Wpl_y=992e3, shape="H")


def test_p387_ex05_column_flexural_buckling():
    sec, fy = _UKC_254x254x73, 275.0
    nb_z, chi_z = N_b_Rd(sec, fy, L=5000.0, k=1.0, axis="z")
    assert abs(chi_z - 0.61) < 0.01                            # P387 reads 0.61 off the curve
    # N_b,z,Rd: P387 1562 kN uses chi rounded to 0.61; the engine's un-rounded chi gives ~1553 kN.
    assert abs(nb_z - 1562e3) / 1562e3 < 0.01
    # And the engine agrees the weak axis governs.
    res = check_member(sec, "S275", MemberDemand(N_Ed=1206e3, L=5000.0, ky=1.0, kz=1.0))
    comp = next(c for c in res.checks if c.name == "compression_buckling")
    assert comp.detail["axis"] == "z"


# ---------------------------------------------------------------------------
# MSB04 Example A.5 — Pinned column, non-slender H section (dual buckling lengths)
# HE 300 B, S235, L = 8.0 m, L_cr,y = 8.0 m (k_y = 1.0), L_cr,z = 5.6 m (k_z = 0.7).
# Published: chi_y = 0.808 (curve b), chi_z = 0.671 (curve c), N_b,Rd = min = 2349.5 kN.
# Exercises BOTH axes, BOTH curves, and a per-axis k factor in one member.
# ---------------------------------------------------------------------------

_HE300B = _i("HE 300 B", h=300.0, b=300.0, tw=11.0, tf=19.0, r=27.0,
             A=14900.0, Iy=25170e4, Iz=8560e4, Wel_y=1678e3, Wpl_y=1869e3, shape="H")


def test_msb04_a5_column_both_axes_and_k_factors():
    # MSB04 took f_y = 235 at t_f = 19 mm (EN 1993-1-1 Table 3.1, t <= 40 mm). The engine now uses the
    # finer EN 10025-2 product band -> f_y = 225 for 16 < t <= 40 mm. So the chi/N_b,Rd *physics* is
    # validated at the example's own f_y = 235 (passed explicitly), while check_member, on grade "S235",
    # is correctly a touch more conservative (225).
    sec, fy = _HE300B, 235.0
    _, chi_y = N_b_Rd(sec, fy, L=8000.0, k=1.0, axis="y")      # L_cr,y = 8.0 m, curve b
    nb_z, chi_z = N_b_Rd(sec, fy, L=5600.0, k=1.0, axis="z")   # L_cr,z = 5.6 m, curve c
    assert abs(chi_y - 0.808) < 0.005
    assert abs(chi_z - 0.671) < 0.005
    assert abs(nb_z - 2349.5e3) / 2349.5e3 < 0.005             # weak axis governs N_b,Rd (chi is f_y-free)
    # Through check_member with the published k factors (k_z = 0.7) the weak axis still governs, at the
    # product-band f_y = 225 (N_b,Rd = 225/235 * 2349.5 = 2249.5 kN).
    res = check_member(sec, "S235", MemberDemand(N_Ed=2000e3, L=8000.0, ky=1.0, kz=0.7))
    assert res.fy == 225.0
    comp = next(c for c in res.checks if c.name == "compression_buckling")
    assert comp.detail["axis"] == "z"                         # weak axis governs, as in MSB04
    assert comp.detail["N_b_Rd"] < nb_z                       # product-band f_y -> a touch below 2349 kN
    assert comp.utilization < 1.0                             # still adequate (N_Ed = 2000 kN)
