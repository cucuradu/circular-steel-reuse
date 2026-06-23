"""End-to-end worked example — one complete bay through the WHOLE pipeline, every stage hand-checked.

This is the single start-to-finish validation case (docs/VALIDATION.md, "Worked example"): a 6 m x 3 m
office floor bay designed as an IPE300 beam on two HEB200 columns (S275), filled from a reclaimed
donor stock of one IPE330 (7 m) and two HEB220 (3.2 m). Unlike the per-check unit tests, *nothing* is
called directly: the extraction JSON goes in, `run_pipeline` runs mapping -> loads -> forces -> EN
checks -> matching -> carbon, and every published-table / closed-form hand value is asserted on the
way out.

Hand chain (full derivation with table citations in docs/VALIDATION.md):
  pressure   9.225 kPa = 1.35*3.5 + 1.5*3.0          (EN 1990 6.10)
  beam       w = 27.675 N/mm; M_Ed = wL^2/8 = 124.5375 kNm; V_Ed = wL/2 = 83.025 kN
  column     N_Ed = 9.225 * 9 m^2 = 83.025 kN
  IPE330     M_c,Rd = 804e3 * 275 = 221.1 kNm  -> bending utilization 0.5633 (governs)
  HEB220     Ncr_z = pi^2*E*Iz/L^2 = 6547 kN, lambda = 0.618, curve c -> chi_z = 0.7745
             N_b,Rd = 1938 kN -> utilization 0.0428
  baselines  beam: IPE300 (42.2 kg/m; IPE270 fails the L/250 deflection check: 27.1 > 24 mm)
             column: IPE160 (15.8 kg/m; chi_z = 0.235 -> N_b,Rd = 129.8 kN >= 83.0 kN)
  carbon     beam   253.2*1.55 - 294.6*0.10 - 5 = 358.00 kg CO2e
             column  47.4*1.55 - 214.5*0.10 - 5 =  47.02 kg CO2e  (each)
             total  452.04 kg CO2e
"""

import pytest

from steelreuse.llm.report import build_report_context
from steelreuse.pipeline import AreaLoadModel, run_pipeline
from steelreuse.schema import ExtractedMember, ExtractedModel


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("worked_example")
    donor = ExtractedModel(kind="donor", members=[
        ExtractedMember(id="D-BM", role="beam", raw_section="IPE330",
                        material_grade="S275", length_mm=7000.0),
        ExtractedMember(id="D-C1", role="column", raw_section="HEB220",
                        material_grade="S275", length_mm=3200.0),
        ExtractedMember(id="D-C2", role="column", raw_section="HEB220",
                        material_grade="S275", length_mm=3200.0),
    ])
    demand = ExtractedModel(kind="demand", members=[
        ExtractedMember(id="B1", role="beam", raw_section="IPE300",
                        material_grade="S275", length_mm=6000.0, spans_mm=[6000.0]),
        ExtractedMember(id="C1", role="column", raw_section="HEB200",
                        material_grade="S275", length_mm=3000.0),
        ExtractedMember(id="C2", role="column", raw_section="HEB200",
                        material_grade="S275", length_mm=3000.0),
    ])
    donor_p, demand_p = tmp / "donor.json", tmp / "demand.json"
    donor.save(donor_p)
    demand.save(demand_p)
    # Defaults spelled out so the doc's hand chain and this run can never drift apart silently.
    # Whole-member mode pinned: the documented narrative books each donor's off-cut per
    # assignment, which only exists when a donor is reused as one piece (cutting is the product
    # default but tracks leftover per donor instead).
    loads = AreaLoadModel(dead_kpa=3.5, live_kpa=3.0, gamma_g=1.35, gamma_q=1.5,
                          beam_tributary_width_m=3.0, column_tributary_area_m2=9.0,
                          column_floors=1.0, flange_restrained=True)  # 6 m floor beam under a slab
    return run_pipeline(str(donor_p), str(demand_p), loads=loads, steel_only_demand=True,
                        allow_cutting=False)


def _assignment(result, slot_id):
    return next(a for a in result.match.assignments if a.slot_id == slot_id)


def test_everything_maps_exactly(result):
    v = result.validation
    assert len(v.mapped) == 3 and not v.fuzzy and not v.unknown
    assert result.supply_count == 3 and result.slot_count == 3


def test_all_three_slots_are_filled(result):
    assert result.match.n_reused == 3
    assert not result.match.unmatched_slots
    # the long donor must land on the beam slot (it is the only one long enough for 6 m)
    assert _assignment(result, "B1#0").supply_id == "D-BM"


def test_beam_utilization_matches_hand_calc(result):
    # M_Ed / M_c,Rd = 124.5375 kNm / (804e3 mm^3 * 275 N/mm^2 = 221.1 kNm) = 0.5633 (bending governs;
    # deflection 13.3/24 = 0.555, shear 83.0/489.1 = 0.170)
    a = _assignment(result, "B1#0")
    assert a.section == "IPE330" and a.status == "OK"
    assert a.utilization == pytest.approx(124.5375 / 221.1, abs=1e-3)


def test_column_utilization_matches_hand_calc(result):
    # chi_z(HEB220, L=3 m, curve c) = 0.7745 -> N_b,Rd = 0.7745*9100*275 = 1938 kN
    # N_Ed / N_b,Rd = 83.025 / 1938.2 = 0.0428
    for slot in ("C1#0", "C2#0"):
        a = _assignment(result, slot)
        assert a.section == "HEB220" and a.status == "OK"
        assert a.utilization == pytest.approx(0.0428, abs=5e-4)


def test_offcuts_match_the_stock_lengths(result):
    assert _assignment(result, "B1#0").offcut_mm == pytest.approx(1000.0)
    assert _assignment(result, "C1#0").offcut_mm == pytest.approx(200.0)


def test_connection_screen_finds_all_compatible(result):
    # IPE330 in an IPE300 position (+30 mm <= 50) and HEB220 in a HEB200 position (+20 mm): all ok.
    assert all(a.connection_status == "ok" for a in result.match.assignments)


def test_carbon_matches_hand_calc(result):
    # Beam: avoided-new baseline = IPE300 (the design section itself: nothing lighter passes — IPE270
    # fails L/250 deflection at 27.1 mm > 24 mm). 253.2*1.55 - 294.6*0.10 - 5 = 358.00 kg.
    assert _assignment(result, "B1#0").co2_saved_kg == pytest.approx(358.00, abs=0.01)
    # Column: baseline = IPE160 (lightest EU passing: N_b,Rd = 129.8 kN >= 83.0 kN).
    # 47.4*1.55 - 214.5*0.10 - 5 = 47.02 kg, each.
    assert _assignment(result, "C1#0").co2_saved_kg == pytest.approx(47.02, abs=0.01)
    assert _assignment(result, "C2#0").co2_saved_kg == pytest.approx(47.02, abs=0.01)
    assert result.match.total_co2_saved_kg == pytest.approx(452.04, abs=0.05)


def test_report_context_carries_the_worked_example(result):
    ctx = build_report_context(result)
    assert ctx["n_reused"] == 3
    assert ctx["match_co2_saved_kg"] == pytest.approx(452.0, abs=0.1)
    assert ctx["connection_review"] == 0
