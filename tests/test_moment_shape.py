"""Moment-shape factors: the LTB moment-gradient C1 (general 4-moment / "Cb" formula) and the
Annex B Table B.3 equivalent-uniform-moment Cm. These let the checker drop the conservative
C1 = Cm = 1.0 (uniform moment) when the real moment diagram is known (analytic span or frame solve).

Opt-in (`--moment-shape`); default off so existing results stay byte-identical. All values here are
hand-verified against EN 1993-1-1 Annex B / NCCI SN003 and the AISC C_b equivalent.
"""

import pytest

from steelreuse.core.ec3_checks import (
    M_cr,
    c1_moment_gradient,
    chi_LT,
    cm_from_psi,
    end_moment_ratio,
)
from steelreuse.core.sections import load_catalog

# ---------------------------------------------------------------------------
# C1 — the general 4-moment formula  C1 = 12.5 Mmax / (2.5 Mmax + 3 M¼ + 4 M½ + 3 M¾)
# ---------------------------------------------------------------------------

def test_c1_uniform_moment_is_one():
    # All four sample moments equal -> uniform moment -> C1 = 1.0 (the conservative baseline).
    assert c1_moment_gradient(100.0, 100.0, 100.0, 100.0) == pytest.approx(1.0)


def test_c1_simply_supported_udl_is_1_136():
    # Parabolic moment: mid = Mmax, quarter points = 0.75 Mmax. 12.5/11 = 1.1364 (the textbook
    # C1 ~ 1.13 for a simply-supported beam under uniform load).
    assert c1_moment_gradient(100.0, 75.0, 100.0, 75.0) == pytest.approx(1.1364, abs=1e-3)


def test_c1_central_point_load():
    # Triangular moment: quarter points = 0.5 Mmax. 1250/950 = 1.3158.
    assert c1_moment_gradient(100.0, 50.0, 100.0, 50.0) == pytest.approx(1.3158, abs=1e-3)


def test_c1_double_curvature_is_large_but_capped():
    # Reversed linear moment (psi = -1): |M| at quarter pts = 50, mid = 0 -> 1250/550 = 2.27.
    assert c1_moment_gradient(100.0, 50.0, 0.0, 50.0) == pytest.approx(2.2727, abs=1e-3)
    # A near-concentrated moment would blow up; C1 is capped at the EN-conventional 2.70.
    assert c1_moment_gradient(100.0, 0.0, 0.0, 0.0) == pytest.approx(2.70)


def test_c1_no_bending_returns_conservative_one():
    # No moment anywhere -> fall back to 1.0 (never divide by zero, never go below 1.0).
    assert c1_moment_gradient(0.0, 0.0, 0.0, 0.0) == 1.0
    # C1 is never less than 1.0 (the formula can dip below 1 only with bad sampling; we floor it).
    assert c1_moment_gradient(100.0, 100.0, 120.0, 100.0) >= 1.0


def test_c1_uses_magnitudes():
    # Sign of the sampled moments must not matter (we feed signed values from the solver).
    assert c1_moment_gradient(-100.0, -75.0, -100.0, -75.0) == pytest.approx(1.1364, abs=1e-3)


# ---------------------------------------------------------------------------
# C1 actually sharpens LTB:  M_cr scales linearly with C1, chi_LT rises
# ---------------------------------------------------------------------------

def test_c1_raises_mcr_and_chi_lt():
    cat = load_catalog()
    sec = cat["IPE300"]
    L = 6000.0
    # M_cr is exactly linear in C1.
    assert M_cr(sec, L, 1.136) == pytest.approx(1.136 * M_cr(sec, L, 1.0))
    # and a higher C1 gives a higher (less conservative) chi_LT for an unrestrained beam.
    base = chi_LT(sec, 275.0, L, section_class=1, C1=1.0)
    sharp = chi_LT(sec, 275.0, L, section_class=1, C1=1.136)
    assert sharp > base
    assert base < 1.0  # the section is LTB-governed at this length, so there is room to sharpen


# ---------------------------------------------------------------------------
# Cm — Annex B Table B.3, linear end moments:  Cm = max(0.6 + 0.4 psi, 0.4)
# ---------------------------------------------------------------------------

def test_cm_from_psi_table_b3():
    assert cm_from_psi(1.0) == pytest.approx(1.0)    # uniform single curvature
    assert cm_from_psi(0.0) == pytest.approx(0.6)    # triangular
    assert cm_from_psi(0.5) == pytest.approx(0.8)
    assert cm_from_psi(-1.0) == pytest.approx(0.4)   # full reversal, floored at 0.4
    assert cm_from_psi(-0.5) == pytest.approx(0.4)   # 0.6-0.2 = 0.4, exactly at the floor


def test_cm_clamps_out_of_range_psi():
    assert cm_from_psi(2.0) == pytest.approx(1.0)
    assert cm_from_psi(-5.0) == pytest.approx(0.4)


# ---------------------------------------------------------------------------
# end_moment_ratio — signed psi from two end moments (|psi| <= 1, sign = curvature)
# ---------------------------------------------------------------------------

def test_end_moment_ratio_single_and_double_curvature():
    # Single curvature (same sign), equal ends -> psi = +1.
    assert end_moment_ratio(80.0, 80.0) == pytest.approx(1.0)
    # Double curvature (opposite signs) -> negative psi.
    assert end_moment_ratio(80.0, -80.0) == pytest.approx(-1.0)
    # The smaller magnitude over the larger, sign from their relative sense.
    assert end_moment_ratio(40.0, 80.0) == pytest.approx(0.5)
    assert end_moment_ratio(-40.0, 80.0) == pytest.approx(-0.5)
    # A zero/near-zero diagram -> psi = 1.0 (Cm = 1.0, the conservative default).
    assert end_moment_ratio(0.0, 0.0) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# The factors actually sharpen the member checks (less conservative utilization)
# ---------------------------------------------------------------------------

def test_c1_sharpens_an_unrestrained_beam_check():
    from steelreuse.core.ec3_checks import MemberDemand, check_member
    cat = load_catalog()
    sec = cat["IPE300"]
    M = 70e6  # N·mm, a bending demand that pushes LTB
    base = check_member(sec, "S275", MemberDemand(My_Ed=M, L=6000, compression_flange_restrained=False))
    sharp = check_member(sec, "S275", MemberDemand(My_Ed=M, L=6000,
                                                   compression_flange_restrained=False, C1=1.136))
    assert sharp.utilization < base.utilization      # higher C1 -> higher M_b,Rd -> lower utilization
    # restrained members don't see C1 (no LTB), so it must NOT change anything there
    r0 = check_member(sec, "S275", MemberDemand(My_Ed=M, L=6000, compression_flange_restrained=True))
    r1 = check_member(sec, "S275", MemberDemand(My_Ed=M, L=6000,
                                                compression_flange_restrained=True, C1=1.136))
    assert r0.utilization == pytest.approx(r1.utilization)


def test_cm_sharpens_a_beam_column_interaction():
    from steelreuse.core.ec3_checks import MemberDemand, check_member
    cat = load_catalog()
    sec = cat["HEB200"]
    d_base = MemberDemand(N_Ed=200e3, My_Ed=40e6, L=4000, compression_flange_restrained=True)
    d_sharp = MemberDemand(N_Ed=200e3, My_Ed=40e6, L=4000, compression_flange_restrained=True, Cmy=0.6)
    base = check_member(sec, "S275", d_base)
    sharp = check_member(sec, "S275", d_sharp)
    nm_base = next(c for c in base.checks if c.name == "interaction_NM")
    nm_sharp = next(c for c in sharp.checks if c.name == "interaction_NM")
    assert nm_sharp.utilization < nm_base.utilization   # lower Cmy -> smaller kyy -> less conservative


# ---------------------------------------------------------------------------
# Pipeline wiring: --moment-shape threads C1 onto the analytic beam slots; off = 1.0
# ---------------------------------------------------------------------------

def test_pipeline_moment_shape_threads_c1_onto_beam_slots():
    from pathlib import Path

    from steelreuse.core.loads import AreaLoadModel
    from steelreuse.pipeline import run_pipeline

    data = Path(__file__).resolve().parents[1] / "src" / "steelreuse" / "data" / "samples"
    donor, demand = str(data / "donor.json"), str(data / "demand.json")

    off = run_pipeline(donor, demand, loads=AreaLoadModel())
    on = run_pipeline(donor, demand, loads=AreaLoadModel(), moment_shape=True)

    beams_off = [s.demand.C1 for s in off.slots if s.role == "beam"]
    beams_on = [s.demand.C1 for s in on.slots if s.role == "beam"]
    assert beams_off and all(c1 == 1.0 for c1 in beams_off)              # conservative default
    assert beams_on and all(c1 == pytest.approx(1.136, abs=1e-3) for c1 in beams_on)


def test_moment_shape_default_off_is_byte_identical():
    import dataclasses
    from pathlib import Path

    from steelreuse.pipeline import run_pipeline

    data = Path(__file__).resolve().parents[1] / "src" / "steelreuse" / "data" / "samples"
    donor, demand = str(data / "donor.json"), str(data / "demand.json")
    base = run_pipeline(donor, demand)
    explicit_off = run_pipeline(donor, demand, moment_shape=False)
    assert [dataclasses.asdict(a) for a in base.match.assignments] == \
        [dataclasses.asdict(a) for a in explicit_off.match.assignments]
