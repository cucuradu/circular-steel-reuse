"""Tests for the area-based load model and the geometry tributary-width estimator (WS2)."""

import pytest

from steelreuse.core.forces import member_demands
from steelreuse.core.loads import (
    OCCUPANCY_PRESETS,
    AreaLoadModel,
    ZoneSpec,
    alpha_A,
    alpha_n,
    assign_zones,
    estimate_column_loads,
    estimate_tributary_widths,
)
from steelreuse.schema import ExtractedMember


# ---------------------------------------------------------------------------
# Zone-based loads: presets + αA/αn reduction helpers
# ---------------------------------------------------------------------------

def test_occupancy_presets_cover_every_en_category():
    # one key per EN 1991-1-1 use category (A..K), not just roof/floor/balcony
    expected = {
        "residential-A", "stairs-A", "balcony-A", "office-B",
        "congress-C1", "congress-C2", "congress-C3", "congress-C4", "congress-C5",
        "retail-D1", "retail-D2", "storage-E1", "industrial-E2",
        "traffic-F", "traffic-G", "roof-H", "roof-I", "roof-K",
    }
    assert set(OCCUPANCY_PRESETS) == expected
    # office-B reproduces today's default exactly, and is reducible (cat B)
    assert OCCUPANCY_PRESETS["office-B"] == ZoneSpec(3.5, 3.0, 0.7, True)
    # light roof: not reducible
    assert OCCUPANCY_PRESETS["roof-H"].reducible is False
    assert OCCUPANCY_PRESETS["roof-H"].q_k == pytest.approx(0.4)
    # storage is not in the A-D reduction scope -> reducible False
    assert OCCUPANCY_PRESETS["storage-E1"].reducible is False


def test_alpha_A_area_reduction():
    # EN eq. 6.1: (5/7)*psi0 + A0/A, capped at 1.0, A0 = 10 m^2
    assert alpha_A(40.0, 0.7) == pytest.approx(0.5 + 10.0 / 40.0)   # 0.75
    assert alpha_A(10.0, 0.7) == pytest.approx(1.0)                 # 0.5+1.0=1.5 -> capped
    assert alpha_A(0.0, 0.7) == pytest.approx(1.0)                  # no area info -> no reduction


def test_alpha_n_storey_reduction():
    # EN eq. 6.2: (2 + (n-2)*psi0)/n for n>2, else 1.0
    assert alpha_n(8, 0.7) == pytest.approx((2 + 6 * 0.7) / 8)      # 0.775
    assert alpha_n(2, 0.7) == pytest.approx(1.0)
    assert alpha_n(1, 0.7) == pytest.approx(1.0)


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
    # no member_zone -> "floor"; 3 floors, all floor, αn now applies by default
    col = ExtractedMember(id="c", role="column", length_mm=4000)
    a_n = alpha_n(3, 0.7)
    perm = 1.35 * 3.5 * 3
    imp = 1.5 * a_n * 3.0 * 3
    n_expected = (perm + imp) * 18.0 * 1e3
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


# ---------------------------------------------------------------------------
# Construction-stage (bare-steel erection) case
# ---------------------------------------------------------------------------

def test_construction_udl_arithmetic():
    # w_c = (gamma_G*g_k + gamma_Q*q_ca) * width = (1.35*3.5 + 1.5*0.75) * 3 = 17.55 N/mm
    m = AreaLoadModel(construction_stage=True)
    assert m.construction_udl_Npmm() == pytest.approx((1.35 * 3.5 + 1.5 * 0.75) * 3.0)
    # per-beam tributary override is honoured
    m2 = AreaLoadModel(construction_stage=True, tributary_overrides={"b1": 2.0})
    assert m2.construction_udl_Npmm("b1") == pytest.approx((1.35 * 3.5 + 1.5 * 0.75) * 2.0)


def test_construction_stage_adds_unrestrained_beam_entry_to_slots():
    from steelreuse.pipeline import build_slots
    from steelreuse.schema import ExtractedModel

    demand = ExtractedModel(kind="demand", members=[
        ExtractedMember(id="b1", role="beam", section="IPE300", raw_section="IPE300",
                        material_grade="S275", length_mm=6000, spans_mm=[6000]),
        ExtractedMember(id="c1", role="column", section="HEB200", raw_section="HEB200",
                        material_grade="S275", length_mm=3000),
    ])
    slots = build_slots(demand, AreaLoadModel(construction_stage=True))
    beam = next(s for s in slots if s.member_id == "b1")
    names = [n for n, _ in beam.combinations]
    assert names == ["ULS gravity", "ULS construction stage"]
    cons = dict(beam.combinations)["ULS construction stage"]
    assert not cons.compression_flange_restrained                  # the defining feature: no slab
    assert cons.My_Ed == pytest.approx(17.55 * 6000**2 / 8)        # 78.975 kNm
    assert cons.Vz_Ed == pytest.approx(17.55 * 6000 / 2)
    # columns are untouched (reduced load is never their governing erection case here)
    col = next(s for s in slots if s.member_id == "c1")
    assert [n for n, _ in col.combinations] == ["ULS gravity"]
    # and default-off keeps the envelope exactly as before
    slots_off = build_slots(demand, AreaLoadModel())
    assert [n for n, _ in next(
        s for s in slots_off if s.member_id == "b1").combinations] == ["ULS gravity"]


# ---------------------------------------------------------------------------
# Wind-uplift (load reversal) case
# ---------------------------------------------------------------------------

def test_uplift_udl_arithmetic():
    # net upward w = (gamma_Q*W_up - 1.0*g_k) * width = (1.5*2.0 - 0.5) * 3 = 7.5 N/mm
    m = AreaLoadModel(dead_kpa=0.5, uplift_kpa=2.0)
    assert m.uplift_udl_Npmm() == pytest.approx((1.5 * 2.0 - 0.5) * 3.0)
    # a heavy floor-type permanent load wins -> negative (no reversal)
    assert AreaLoadModel(dead_kpa=3.5, uplift_kpa=1.0).uplift_udl_Npmm() < 0
    # per-beam tributary override is honoured
    m2 = AreaLoadModel(dead_kpa=0.5, uplift_kpa=2.0, tributary_overrides={"b1": 2.0})
    assert m2.uplift_udl_Npmm("b1") == pytest.approx((1.5 * 2.0 - 0.5) * 2.0)


def test_wind_uplift_adds_reversal_entry_for_roof_beams_only():
    from steelreuse.pipeline import build_slots
    from steelreuse.schema import ExtractedModel

    def _demand():
        return ExtractedModel(kind="demand", members=[
            ExtractedMember(id="roof", role="beam", section="IPE300", raw_section="IPE300",
                            material_grade="S275", length_mm=6000, spans_mm=[6000],
                            start_xyz=[0, 0, 6000], end_xyz=[6000, 0, 6000]),
            ExtractedMember(id="floor", role="beam", section="IPE300", raw_section="IPE300",
                            material_grade="S275", length_mm=6000, spans_mm=[6000],
                            start_xyz=[0, 0, 3000], end_xyz=[6000, 0, 3000]),
        ])

    slots = build_slots(_demand(), AreaLoadModel(dead_kpa=0.5, uplift_kpa=2.0))
    roof = next(s for s in slots if s.member_id == "roof")
    assert [n for n, _ in roof.combinations] == ["ULS gravity", "ULS wind uplift"]
    up = dict(roof.combinations)["ULS wind uplift"]
    assert not up.compression_flange_restrained    # bottom flange in compression: no slab there
    w = (1.5 * 2.0 - 0.5) * 3.0
    assert up.My_Ed == pytest.approx(w * 6000**2 / 8)
    assert up.Vz_Ed == pytest.approx(w * 6000 / 2)
    # only the top framing level sees roof suction
    floor = next(s for s in slots if s.member_id == "floor")
    assert [n for n, _ in floor.combinations] == ["ULS gravity"]
    # heavy permanent load -> net downward -> no reversal entry
    heavy = build_slots(_demand(), AreaLoadModel(dead_kpa=3.5, uplift_kpa=1.0))
    assert [n for n, _ in next(
        s for s in heavy if s.member_id == "roof").combinations] == ["ULS gravity"]
    # default off -> envelope exactly as before
    off = build_slots(_demand(), AreaLoadModel())
    assert [n for n, _ in next(
        s for s in off if s.member_id == "roof").combinations] == ["ULS gravity"]


def test_per_member_k_override_in_the_analytic_path():
    from steelreuse.pipeline import build_slots
    from steelreuse.schema import ExtractedModel

    demand = ExtractedModel(kind="demand", members=[
        ExtractedMember(id="c1", role="column", section="HEB200", raw_section="HEB200",
                        material_grade="S275", length_mm=4000, kz=2.0),
    ])
    slot = build_slots(demand, AreaLoadModel())[0]
    assert slot.demand.kz == 2.0 and slot.demand.ky == 1.0


# ---------------------------------------------------------------------------
# Zone-based loads: resolution, member-aware loads, assign_zones
# ---------------------------------------------------------------------------

def test_zone_resolution_override_beats_auto_beats_floor():
    m = AreaLoadModel()
    m.member_zone = {"b1": "roof", "b2": "floor"}
    m.zone_overrides = {"b1": "balcony-A"}
    m.custom_zones = {"balcony-A": ZoneSpec(2.0, 2.5, 0.7, True)}
    assert m._zone_name("b1") == "balcony-A"   # override wins over auto "roof"
    assert m._zone_name("b2") == "floor"       # auto
    assert m._zone_name("b3") == "floor"       # default when unknown
    assert m._zone_spec("floor") == ZoneSpec(3.5, 3.0, 0.7, True)
    assert m._zone_spec("roof") == ZoneSpec(1.0, 0.4, 0.0, False)
    assert m._spec_by_id("b1") == ZoneSpec(2.0, 2.5, 0.7, True)


def test_roof_and_floor_beams_get_different_udls():
    m = AreaLoadModel()
    m.member_zone = {"r": "roof", "f": "floor"}
    roof = ExtractedMember(id="r", role="beam", length_mm=6000, spans_mm=[6000])
    floor = ExtractedMember(id="f", role="beam", length_mm=6000, spans_mm=[6000])
    w_roof = m.loads_for(roof).udl_Npmm
    w_floor = m.loads_for(floor).udl_Npmm
    assert w_floor == pytest.approx((1.35 * 3.5 + 1.5 * 3.0) * 3.0)   # area 18 -> αA capped
    assert w_roof == pytest.approx((1.35 * 1.0 + 1.5 * 0.4) * 3.0)
    assert w_roof < w_floor


def test_beam_alpha_A_reduces_imposed_only_on_large_area():
    m = AreaLoadModel(beam_tributary_width_m=5.0)   # area = 5 * 8 = 40 m^2
    m.member_zone = {"b": "floor"}
    beam = ExtractedMember(id="b", role="beam", length_mm=8000, spans_mm=[8000])
    a = alpha_A(40.0, 0.7)                          # 0.75
    perm = 1.35 * 3.5 * 5.0
    imp = 1.5 * (a * 3.0) * 5.0
    assert m.loads_for(beam).udl_Npmm == pytest.approx(perm + imp)
    assert m.loads_for(beam).w_service_Npmm == pytest.approx((3.5 + 3.0) * 5.0)


def test_no_load_reduction_disables_alpha_A():
    m = AreaLoadModel(beam_tributary_width_m=5.0, load_reduction=False)
    m.member_zone = {"b": "floor"}
    beam = ExtractedMember(id="b", role="beam", length_mm=8000, spans_mm=[8000])
    assert m.loads_for(beam).udl_Npmm == pytest.approx((1.35 * 3.5 + 1.5 * 3.0) * 5.0)


def test_single_storey_column_carries_roof_not_office():
    m = AreaLoadModel()
    m.member_zone = {"c": "roof"}
    m.column_area_overrides = {"c": 9.0}
    m.column_floor_overrides = {"c": 1.0}
    col = ExtractedMember(id="c", role="column", length_mm=4000)
    assert m.loads_for(col).axial_N == pytest.approx((1.35 * 1.0 + 1.5 * 0.4) * 9.0 * 1e3)


def test_multistorey_column_roof_plus_floors_with_alpha_n():
    m = AreaLoadModel()
    m.member_zone = {"c": "roof"}
    m.column_area_overrides = {"c": 9.0}
    m.column_floor_overrides = {"c": 8.0}
    col = ExtractedMember(id="c", role="column", length_mm=4000)
    floor_levels = 7
    a_n = alpha_n(floor_levels, 0.7)
    perm = 1.35 * (1.0 * 1 + 3.5 * floor_levels)
    imp = 1.5 * (0.4 * 1 + a_n * 3.0 * floor_levels)
    assert m.loads_for(col).axial_N == pytest.approx((perm + imp) * 9.0 * 1e3)


def test_column_not_reaching_roof_is_all_floor():
    m = AreaLoadModel()
    m.member_zone = {"c": "floor"}
    m.column_area_overrides = {"c": 9.0}
    m.column_floor_overrides = {"c": 2.0}     # n=2 -> αn = 1.0
    col = ExtractedMember(id="c", role="column", length_mm=4000)
    assert m.loads_for(col).axial_N == pytest.approx((1.35 * 3.5 + 1.5 * 3.0) * 9.0 * 2.0 * 1e3)


def test_construction_and_uplift_use_zone_dead_load():
    m = AreaLoadModel(uplift_kpa=2.0)     # roof zone dead = roof_dead_kpa = 1.0 by default
    m.member_zone = {"r": "roof"}
    assert m.construction_udl_Npmm("r") == pytest.approx((1.35 * 1.0 + 1.5 * 0.75) * 3.0)
    assert m.uplift_udl_Npmm("r") == pytest.approx((1.5 * 2.0 - 1.0 * 1.0) * 3.0)
    assert m.construction_udl_Npmm("x") == pytest.approx((1.35 * 3.5 + 1.5 * 0.75) * 3.0)


def test_assign_zones_single_storey_all_roof():
    members = [_beam(0, 0.0, z=4000.0), _beam(1, 3000.0, z=4000.0)]
    assert assign_zones(members) == {"0": "roof", "1": "roof"}


def test_assign_zones_two_levels_split_roof_and_floor():
    members = [_beam("top", 0.0, z=7000.0), _beam("mid", 0.0, z=3500.0)]
    z = assign_zones(members)
    assert z["top"] == "roof" and z["mid"] == "floor"


def test_assign_zones_column_reaching_roof_band():
    members = [
        _beam("rb", 0.0, z=6000.0),
        _col("creach", 0.0, 0.0, 3000.0, 6000.0),
        _col("clow", 9000.0, 0.0, 0.0, 3000.0),
    ]
    z = assign_zones(members)
    assert z["creach"] == "roof"
    assert z["clow"] == "floor"


def test_assign_zones_without_geometry_is_empty():
    members = [ExtractedMember(id="b", role="beam", spans_mm=[6000])]
    assert assign_zones(members) == {}


def test_pipeline_assigns_zones_so_roof_beam_is_lighter():
    from steelreuse.pipeline import build_slots
    from steelreuse.schema import ExtractedModel

    demand = ExtractedModel(kind="demand", members=[
        ExtractedMember(id="roof", role="beam", section="IPE300", raw_section="IPE300",
                        material_grade="S275", length_mm=6000, spans_mm=[6000],
                        start_xyz=[0, 0, 6000], end_xyz=[6000, 0, 6000]),
        ExtractedMember(id="floor", role="beam", section="IPE300", raw_section="IPE300",
                        material_grade="S275", length_mm=6000, spans_mm=[6000],
                        start_xyz=[0, 0, 3000], end_xyz=[6000, 0, 3000]),
    ])
    loads = AreaLoadModel()
    loads.member_zone = assign_zones(demand.members)
    slots = {s.member_id: s for s in build_slots(demand, loads)}
    assert slots["roof"].demand.My_Ed < slots["floor"].demand.My_Ed
