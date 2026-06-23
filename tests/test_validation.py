"""End-to-end validation worked examples (see docs/VALIDATION.md).

Each assertion here corresponds to a hand calculation written out in docs/VALIDATION.md, so the
documented numbers stay guarded by CI. The per-clause EN 1993 checks are validated more finely in
tests/test_ec3.py; this file ties the chain together: loads -> frame -> resistance -> utilisation.
"""

import pytest

from steelreuse.core.ec3_checks import GAMMA_M0, M_c_Rd, MemberDemand, N_b_Rd, V_c_Rd, check_member
from steelreuse.core.loads import AreaLoadModel
from steelreuse.core.sections import load_catalog


@pytest.fixture(scope="module")
def cat():
    return load_catalog()


def test_worked_beam_resistances_match_section_tables(cat):
    # IPE300, Class 1. M_c,Rd = Wpl·fy/gM0; V_c,Rd = Av·fy/(sqrt(3)·gM0). See docs/VALIDATION.md §2.
    sec = cat["IPE300"]
    assert M_c_Rd(sec, 235, 1) == pytest.approx(147.58e6, rel=1e-3)   # 628e3·235 = 147.6 kNm
    assert M_c_Rd(sec, 275, 1) == pytest.approx(172.7e6, rel=1e-3)    # 628e3·275 = 172.7 kNm
    assert V_c_Rd(sec, 235) == pytest.approx(348.3e3, rel=5e-3)       # Av≈2567 mm^2 -> 348 kN


def test_worked_beam_bending_utilisation(cat):
    # M_Ed = 124.5 kNm (= w L^2/8 from the frame example, §1) on IPE300 S275, slab-restrained.
    # util = 124.5 / 172.7 = 0.721.
    res = check_member(
        cat["IPE300"], "S275",
        MemberDemand(My_Ed=124.5e6, L=6000, compression_flange_restrained=True),
    )
    assert res.status == "OK"
    assert res.utilization == pytest.approx(124.5e6 / 172.7e6, rel=2e-3)


def test_worked_column_flexural_buckling(cat):
    # IPE300 S275, L=4 m, k=1, weak (z) axis, buckling curve b -> chi_z ~ 0.392. See §3.
    nb_z, chi_z = N_b_Rd(cat["IPE300"], 275, L=4000, k=1.0, axis="z")
    assert chi_z == pytest.approx(0.392, abs=0.01)
    assert nb_z == pytest.approx(chi_z * cat["IPE300"].A * 275 / GAMMA_M0, rel=1e-6)


def test_worked_frame_load_path_to_check_end_to_end():
    # One 6 m bay, area load 9.225 kPa (factored) x 3 m tributary = 27.675 N/mm.
    # Frame solve must recover M = w L^2/8 = 124.5 kNm, and the EN check must then read util ~0.72.
    pytest.importorskip("Pynite")
    from steelreuse.core.frame import analyze_frame
    from steelreuse.core.sections import load_default_catalog
    from steelreuse.schema import ExtractedMember

    full = load_default_catalog()
    # slab-restrained floor beam (util ~0.72 from full M_c,Rd); restraint now asserted explicitly
    loads = AreaLoadModel(flange_restrained=True)  # defaults: 1.35·3.5 + 1.5·3.0 = 9.225 kPa, 3 m trib
    assert loads.factored_area_kpa() == pytest.approx(9.225, rel=1e-6)
    members = [
        ExtractedMember(id="c1", role="column", section="IPE300", material_grade="S275",
                        start_xyz=[0, 0, 0], end_xyz=[0, 0, 3000], length_mm=3000),
        ExtractedMember(id="c2", role="column", section="IPE300", material_grade="S275",
                        start_xyz=[6000, 0, 0], end_xyz=[6000, 0, 3000], length_mm=3000),
        ExtractedMember(id="b1", role="beam", section="IPE300", material_grade="S275",
                        start_xyz=[0, 0, 3000], end_xyz=[6000, 0, 3000], spans_mm=[6000]),
    ]
    res = analyze_frame(members, loads, full)
    dem = res.demands_by_member["b1"][0][1]
    assert dem.My_Ed == pytest.approx(124.5e6, rel=1e-2)            # w L^2 / 8
    # The EN check reads bending utilisation = M_Ed / M_c,Rd = 124.5 / 172.7 = 0.72 (SLS deflection
    # governs the overall result at ~0.78 — see docs/VALIDATION.md §1).
    check = check_member(full["IPE300"], "S275", dem)
    bend = next(c for c in check.checks if c.name == "bending_y")
    assert bend.utilization == pytest.approx(124.5e6 / 172.7e6, rel=3e-2)
