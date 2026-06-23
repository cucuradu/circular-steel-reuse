"""External validation: anchor the engine's member resistances to PUBLISHED Blue Book / SCI values.

Unlike tests/test_validation.py and tests/test_ec3.py (which guard the engine against *hand*
calculations derived from the same EN clauses the engine implements), this file pins two engine
results to numbers taken straight from a published, citable third-party source — the SCI/BCSA worked
examples that accompany the "Blue Book" (SCI P363). This is provenance-grade validation: if the
authors of the Eurocode design guides get a different number, we want to know.

SOURCE
  SCI P364, "Steel Building Design: Worked Examples – Open Sections" (The Steel Construction
  Institute, 2009; worked to BS EN 1993-1-1:2005 + UK National Annex). The companion volume to the
  Blue Book section tables (SCI P363). Public PDF:
      https://www.steelconstruction.info/images/5/50/Sci_p364.pdf
  Both example sections exist verbatim in the UK catalogue CSV
  (src/steelreuse/data/sections/uk_sections.csv); designations and section properties were
  cross-checked against the printed P364 calculation sheets before use.

TOLERANCE RATIONALE
  Published tables round to 3-4 significant figures and the guides may differ from this engine in
  second-order modelling choices (notably the source of M_cr — see Case 2). A 0.5% band is used where
  the engine reproduces the published clause exactly (Case 1); a 3% band is used where the engine's
  documented, deliberately-conservative I_t/I_w approximation for M_cr is involved (Case 2). Neither
  band is a fudge: each case states its assumptions and the observed difference sits well inside the
  band. The tolerances are NOT to be loosened to force a pass — a real divergence is a finding.

ASSUMPTIONS FIXED TO MATCH THE TABLE
  * gamma_M0 = gamma_M1 = 1.0 — the UK NA value, identical to the engine constants (ec3_checks.py).
  * Case 1 uses f_y = 345 N/mm^2 directly (P364's S355 value for 16 < t_f <= 40 mm, per BS EN 10025-2
    Table 7). The engine's grade table simplifies S355 to 355 for t_f <= 40 mm, so the low-level
    N_b_Rd(...) is called with f_y = 345 explicitly to align with the published assumption rather than
    routing through check_member's grade banding (that simplification is its own, separately-tested
    behaviour — see tests/test_ec3.py::test_nominal_fy_thickness_bands).
  * Buckling/effective length is taken exactly as the example states (Lcr = L, k = 1.0): both examples
    are pin-ended / simply-supported with no intermediate restraint.
"""

import math

import pytest

from steelreuse.core.ec3_checks import GAMMA_M1, M_cr, N_b_Rd
from steelreuse.core.sections import DEFAULT_UK_CATALOG, load_catalog


@pytest.fixture(scope="module")
def uk():
    # The UK UB/UC catalogue (BS EN 10365) — the same families the Blue Book / SCI P363 tabulate.
    return load_catalog(DEFAULT_UK_CATALOG, standard="GB")


# ---------------------------------------------------------------------------
# Case 1 — Flexural buckling resistance N_b,Rd  (SCI P364, Worked Example 9)
# ---------------------------------------------------------------------------
#
# SOURCE  : SCI P364 Example 9, "Pinned column using a Class 3 section", calculation sheets 1-7.
#           https://www.steelconstruction.info/images/5/50/Sci_p364.pdf
# SECTION : 356 x 368 x 129 UKC  (catalogue name UC356X368X129)
# GRADE   : S355, f_y = 345 N/mm^2  (16 < t_f = 17.5 mm <= 40 mm, BS EN 10025-2 Table 7)
# LENGTH  : pin-ended both axes, no intermediate restraint -> Lcr = L = 6000 mm; minor (z-z) axis
#           governs (sheet 4). gamma_M1 = 1.0 (sheet 3, UK NA NA.2.15).
# CURVE   : h/b = 355.6/368.6 = 0.96 < 1.2 and t_f < 100 mm -> buckling curve 'c', alpha = 0.49
#           (sheet 4). The engine selects the same curve in _buckling_alpha().
# PUBLISHED (sheets 4-5):
#           lambda_bar_z = 0.82 ;  chi_z = 0.65 ;  N_b,z,Rd = 0.65 * 164e2 * 345 / 1.0 = 3678 kN.

def test_p364_ex9_flexural_buckling_nb_rd(uk):
    sec = uk["UC356X368X129"]
    # Sanity-check the catalogue row matches the printed P364 section properties (sheet 2).
    assert sec.A == pytest.approx(164e2, rel=1e-3)        # A = 164 cm^2
    assert sec.iz == pytest.approx(94.3, rel=1e-3)        # i_z = 9.43 cm
    assert sec.tf == pytest.approx(17.5, rel=1e-3)        # t_f = 17.5 mm -> curve c, f_y = 345

    nb_z, chi_z = N_b_Rd(sec, fy=345.0, L=6000.0, k=1.0, axis="z")

    # Curve 'c' (alpha = 0.49) is what P364 derives from h/b < 1.2; assert the engine agrees.
    assert sec.h / sec.b == pytest.approx(0.96, abs=0.01)

    # chi_z: engine 0.649 vs published 0.65 (the table rounds chi to 2 d.p.).
    assert chi_z == pytest.approx(0.65, abs=0.005)

    # N_b,z,Rd: engine 3674 kN vs published 3678 kN -> 0.11% below.
    # The engine reproduces EN eq. (6.47)/(6.49) exactly here, so a tight 0.5% band is justified;
    # the tiny gap is published-table rounding of chi (0.65) and the resistance (3678 kN).
    assert nb_z == pytest.approx(3678e3, rel=5e-3)

    # Closure: the resistance is exactly chi * A * f_y / gamma_M1 (no hidden factors).
    assert nb_z == pytest.approx(chi_z * sec.A * 345.0 / GAMMA_M1, rel=1e-9)


# ---------------------------------------------------------------------------
# Case 2 — Elastic critical moment for LTB, uniform moment  (SCI P364, Worked Example 3)
# ---------------------------------------------------------------------------
#
# SOURCE  : SCI P364 Example 3, "Unrestrained beam with end bending moments", calculation sheets 6-9.
#           https://www.steelconstruction.info/images/5/50/Sci_p364.pdf
# SECTION : 457 x 191 x 67 UKB  (catalogue name UB457X191X67)
# GRADE   : S275, f_y = 275 N/mm^2 (t_f = 12.7 mm <= 16 mm, BS EN 10025-2 Table 7).
# LENGTH  : unrestrained over the full span -> Lcr = L = 9000 mm (sheet 6). gamma_M1 = 1.0.
#
# WHY THE UNIFORM-MOMENT M_cr (not the example's final M_b,Rd):
#   P364 Example 3 obtains M_cr from the external LTBeam program for the *actual* (non-uniform) moment
#   diagram (M_cr = 355.7 kNm) and then applies the EN 6.3.2.3(2) "f-factor" moment-shape modification
#   (k_c, lambda_LT,mod) to reach M_b,Rd = 291 kNm. This engine intentionally does NOT model either:
#   it computes M_cr for the *uniform* moment with C1 = 1.0 from thin-wall I_t/I_w approximations and
#   has no f-factor (a deliberately conservative scope choice, documented in ec3_checks.py). Anchoring
#   to the example's 291 kNm would therefore compare two different models. Instead we anchor to the
#   ONE published number that is on the engine's own basis: the uniform-moment M_cr that P364 also
#   reports from LTBeam on sheet 8 while back-calculating C1:
#       "Applying a uniform bending moment ... M_cr = 134.2 kNm"  (P364 Ex 3, sheet 8).
#   That 134.2 kNm is an independently-computed (LTBeam FE) uniform-moment elastic critical moment for
#   exactly this section/length, i.e. a clean external check of the engine's M_cr(C1=1.0).
#
# PUBLISHED (sheet 8):  M_cr (uniform moment, C1 = 1.0) = 134.2 kNm.

def test_p364_ex3_uniform_moment_mcr(uk):
    sec = uk["UB457X191X67"]
    # Catalogue row vs printed P364 section properties (sheet 3).
    assert sec.Wpl_y == pytest.approx(1470e3, rel=1e-3)   # W_pl,y = 1470 cm^3
    assert sec.A == pytest.approx(85.5e2, rel=1e-3)       # A = 85.5 cm^2
    assert sec.tf == pytest.approx(12.7, rel=1e-3)

    mcr = M_cr(sec, L=9000.0, C1=1.0)

    # Engine 130.7 kNm vs published (LTBeam, uniform) 134.2 kNm -> 2.6% below, i.e. CONSERVATIVE.
    # The 3% band reflects the engine's documented thin-wall I_t/I_w approximation, which under-
    # predicts M_cr on purpose (lower M_cr -> lower chi_LT -> safe). The engine must stay within the
    # band AND on the conservative (low) side; a value above the published M_cr would be a real flag.
    assert mcr == pytest.approx(134.2e6, rel=3e-2)
    assert mcr <= 134.2e6, "engine M_cr must not exceed the published value (must stay conservative)"

    # Consistency of the slenderness this M_cr feeds into 6.3.2.3:
    # lambda_LT = sqrt(W_pl,y * f_y / M_cr). From the published uniform M_cr this is 1.74; the engine's
    # slightly lower M_cr gives a slightly higher (more conservative) lambda_LT.
    lam_engine = math.sqrt(sec.Wpl_y * 275.0 / mcr)
    lam_published = math.sqrt(sec.Wpl_y * 275.0 / 134.2e6)
    assert lam_published == pytest.approx(1.74, abs=0.02)
    assert lam_engine >= lam_published                    # conservative direction
    assert lam_engine == pytest.approx(lam_published, rel=2e-2)
