"""Tests for the area-based load model and the geometry tributary-width estimator (WS2)."""

import pytest

from steelreuse.core.loads import AreaLoadModel, estimate_tributary_widths
from steelreuse.schema import ExtractedMember


def test_factored_and_characteristic_pressure():
    m = AreaLoadModel(dead_kpa=3.5, live_kpa=3.0, gamma_g=1.35, gamma_q=1.5)
    assert m.factored_area_kpa() == pytest.approx(1.35 * 3.5 + 1.5 * 3.0)   # 9.225
    assert m.characteristic_area_kpa() == pytest.approx(6.5)


def test_beam_udl_and_column_axial():
    m = AreaLoadModel()  # defaults: 9.225 kN/m^2 factored
    # UDL = factored pressure * tributary width; 1 kN/m == 1 N/mm
    assert m.beam_udl_Npmm(3.0) == pytest.approx(9.225 * 3.0)               # 27.675 N/mm
    assert m.beam_udl_Npmm() == pytest.approx(9.225 * 3.0)                  # default width 3 m
    # axial = factored pressure * tributary area * floors, kN -> N
    assert m.column_axial_N(9.0, 1.0) == pytest.approx(9.225 * 9.0 * 1e3)   # 83025 N
    assert m.column_axial_N(9.0, 4.0) == pytest.approx(4 * 83025.0)


def test_loads_for_beam_and_column():
    m = AreaLoadModel()
    beam = ExtractedMember(id="b", role="beam", spans_mm=[6000])
    col = ExtractedMember(id="c", role="column", length_mm=4000)
    lb = m.loads_for(beam)
    assert lb.udl_Npmm == pytest.approx(27.675) and lb.axial_N == 0.0
    assert lb.w_service_Npmm == pytest.approx(6.5 * 3.0)                    # SLS uses unfactored G+Q
    lc = m.loads_for(col)
    assert lc.axial_N == pytest.approx(83025.0) and lc.udl_Npmm == 0.0


def test_tributary_override_changes_udl():
    m = AreaLoadModel(tributary_overrides={"b": 2.0})
    beam = ExtractedMember(id="b", role="beam", spans_mm=[6000])
    assert m.loads_for(beam).udl_Npmm == pytest.approx(9.225 * 2.0)         # uses the per-member width


def _beam(i, y, x0=0.0, x1=6000.0, z=0.0):
    return ExtractedMember(id=str(i), role="beam", start_xyz=[x0, y, z], end_xyz=[x1, y, z])


def test_estimate_tributary_widths_on_a_regular_grid():
    # three parallel beams spaced 2.5 m apart -> every beam's tributary width is 2.5 m
    members = [_beam(0, 0.0), _beam(1, 2500.0), _beam(2, 5000.0)]
    widths = estimate_tributary_widths(members, default_m=3.0)
    assert widths["1"] == pytest.approx(2.5, abs=1e-6)       # interior: 1.25 + 1.25
    assert widths["0"] == pytest.approx(2.5, abs=1e-6)       # edge: whole bay (conservative)
    assert widths["2"] == pytest.approx(2.5, abs=1e-6)


def test_estimate_skips_isolated_and_nonbeam_members():
    # a lone beam (no parallel neighbour) and a column are not estimated -> caller uses the default
    members = [_beam(0, 0.0), ExtractedMember(id="col", role="column", start_xyz=[0, 0, 0],
                                              end_xyz=[0, 0, 3000])]
    widths = estimate_tributary_widths(members)
    assert widths == {}
