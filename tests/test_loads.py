"""Tests for the area-based load model and the geometry tributary-width estimator (WS2)."""

import pytest

from steelreuse.core.forces import member_demands
from steelreuse.core.loads import (
    AreaLoadModel,
    estimate_column_loads,
    estimate_tributary_widths,
)
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


def _col(i, x, y, z0=0.0, z1=3000.0):
    return ExtractedMember(id=str(i), role="column", start_xyz=[x, y, z0], end_xyz=[x, y, z1])


def test_estimate_column_floor_counts_from_a_stack():
    # two columns stacked at one plan location = a 2-storey column: the lower carries 2 floors, the
    # upper 1. (One grid point has no neighbours, so no area is estimated -> caller uses the default.)
    members = [_col("lo", 0.0, 0.0, 0.0, 3000.0), _col("hi", 0.0, 0.0, 3000.0, 6000.0)]
    areas, floors = estimate_column_loads(members)
    assert floors == {"lo": 2.0, "hi": 1.0}
    assert areas == {}                                   # single stack -> no bay to size


def test_estimate_column_tributary_area_on_a_3x3_grid():
    # 3x3 grid at 6 m spacing: interior tributary = 36 m^2, edge-mid = 18 m^2, corner = 9 m^2
    # (edge = half the present bay, i.e. slab edge at the column, no overhang).
    members = [_col(f"{ix}_{iy}", ix * 6000.0, iy * 6000.0) for ix in range(3) for iy in range(3)]
    areas, floors = estimate_column_loads(members)
    assert areas["1_1"] == pytest.approx(36.0)           # centre
    assert areas["1_0"] == pytest.approx(18.0)           # edge midside
    assert areas["0_0"] == pytest.approx(9.0)            # corner
    assert set(floors.values()) == {1.0}                 # single-storey grid


def test_loads_for_column_uses_overrides_and_eccentricity():
    m = AreaLoadModel(column_area_overrides={"c": 18.0}, column_floor_overrides={"c": 3.0},
                      column_eccentricity_mm=50.0)
    col = ExtractedMember(id="c", role="column", length_mm=4000)
    n_expected = 9.225 * 18.0 * 3.0 * 1e3                # pressure * area * floors, kN -> N
    load = m.loads_for(col)
    assert load.axial_N == pytest.approx(n_expected)
    assert load.axial_moment_Nmm == pytest.approx(n_expected * 50.0)
    # the moment flows into the column's MemberDemand so the N+M interaction can engage
    d = member_demands(col, load)[0]
    assert d.N_Ed == pytest.approx(n_expected) and d.My_Ed == pytest.approx(n_expected * 50.0)


def test_default_column_load_is_pure_axial_no_moment():
    # without overrides / eccentricity, behaviour is unchanged: one axial, no moment.
    col = ExtractedMember(id="c", role="column", length_mm=4000)
    load = AreaLoadModel().loads_for(col)
    assert load.axial_N == pytest.approx(83025.0) and load.axial_moment_Nmm == 0.0


def test_combination_loads_gravity_only_by_default():
    # phi = 0 (default): the envelope is a single gravity combination for both beams and columns,
    # so the matcher behaves exactly as before (no numeric change).
    m = AreaLoadModel()
    col = ExtractedMember(id="c", role="column", length_mm=4000)
    beam = ExtractedMember(id="b", role="beam", spans_mm=[6000])
    assert [name for name, _ in m.combination_loads(col)] == ["ULS gravity"]
    assert [name for name, _ in m.combination_loads(beam)] == ["ULS gravity"]


def test_combination_loads_adds_sway_imperfection_for_columns():
    # phi > 0 adds an EN 5.3.2 sway-imperfection combination for columns: same axial, plus a notional
    # moment M = N * phi * L. Beams are unaffected at member level (still one combination).
    m = AreaLoadModel(notional_phi=0.005)  # 1/200
    col = ExtractedMember(id="c", role="column", length_mm=4000)
    combos = m.combination_loads(col)
    assert [name for name, _ in combos] == ["ULS gravity", "ULS gravity + sway imperfection"]
    grav, sway = combos[0][1], combos[1][1]
    assert sway.axial_N == pytest.approx(grav.axial_N)
    assert sway.axial_moment_Nmm == pytest.approx(grav.axial_N * 0.005 * 4000)

    beam = ExtractedMember(id="b", role="beam", spans_mm=[6000])
    assert len(m.combination_loads(beam)) == 1
